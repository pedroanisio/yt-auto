// gateway-engine.mjs: one structured decision through Vercel AI Gateway, with
// the AI SDK. gateway_engine.py runs it; nothing else should.
//
// Node >=22, ESM. package.json pins `ai` 7.0.150 exactly (it bundles
// @ai-sdk/gateway 4.0.123) and `undici`. The key is AI_GATEWAY_API_KEY in the
// environment and is never written to stdout or stderr.
//
// stdin, one JSON request:
//   {"model": "google/gemini-3.6-flash", "instructions": "...", "prompt": "...",
//    "schema": {...} | null, "schemaName": "answer",
//    "media": [{"path": "/abs/clip.mp4", "mediaType": "video/mp4",
//               "label": "Cut A:"}],            (label optional: a text part
//                                                immediately before its file)
//    "gateway": {"only": ["google"], "has": ["structured-output"]},
//    "timeoutMs": 900000, "maxRetries": 0,
//    "costPoll": {"tries": 0, "intervalMs": 2000},
//    "host": "https://proxy.example"}          (host only when YT_GATEWAY_URL is set)
//
// stdout, exactly one JSON line:
//   {"ok": true, "output": ..., "usage": {...}, "finishReason": "stop",
//    "modelId": "...", "generationId": "gen_...", "cost": "0.0012" | null,
//    "generationCost": 0.0013 | null, "routing": {...} | null}
//   {"ok": false, "error": {"kind": "schema|no_output|providers_unavailable|gateway|other",
//    "status": 429, "retryable": true, "message": "...", "timeout": false},
//    "usage": {...}}                             (usage only when the SDK had one)
//
// Exit codes: 0 an answer, 2 schema or no_output, 3 a gateway error, 4 none of
// the `only` providers available, 1 anything else.
//
// Why it is shaped this way:
//   - `maxRetries` defaults to 0: Python owns retries, so every paid attempt is
//     one it counted. Python also decides which failures are permanent; this
//     program only classifies.
//   - `gateway.models` (fallbacks, which may switch to another family) is
//     deleted, and a `vmc/` virtual model, which may route anywhere, refused.
//   - The SDK's HTTP client is the npm undici's own fetch with its own Agent,
//     whose header and body timeouts are timeoutMs: Node's built-in fetch
//     stops waiting for headers after 5 minutes, which a long video call can
//     pass (https://vercel.com/docs/ai-gateway/modalities/video-generation).
//     AbortSignal.timeout(timeoutMs) still bounds the whole call.
//   - The schema goes to the SDK as plain JSON Schema with no `validate`, so
//     the SDK only checks that the reply parses. Python validates it.
//   - `run(req, deps, env)` is exported with generateText, createGateway,
//     fetch, makeAgent, readFile and sleep injectable, so the tests need no
//     gateway. The entry point at the bottom wires the real ones.

import { readFile } from 'node:fs/promises';
import { realpathSync } from 'node:fs';
import { basename } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import {
  createGateway, generateText, jsonSchema, NoObjectGeneratedError,
  NoOutputGeneratedError, Output,
} from 'ai';
import { Agent, fetch as undiciFetch } from 'undici';

export const EXIT = { ok: 0, other: 1, schema: 2, gateway: 3, providers: 4 };
export const DEFAULT_HOST = 'https://ai-gateway.vercel.sh';
// The SDK endpoint's path under the gateway's host, used only when the request
// names a host of its own (YT_GATEWAY_URL); otherwise createGateway's default
// stands. TO VERIFY against @ai-sdk/gateway 4.0.123's default baseURL.
export const API_PATH = '/v3/ai';

/** The undici Agent the SDK's requests go through. */
export function makeAgent(timeoutMs) {
  return new Agent({ headersTimeout: timeoutMs, bodyTimeout: timeoutMs });
}

const REAL = { generateText, createGateway, fetch: undiciFetch, makeAgent, readFile, sleep };

/** Whether an error says none of the `only` providers can serve the model.
 *  The exact wording is TO VERIFY with a live call; both the code and the
 *  sentence the docs describe are matched, in the message and the cause. */
export function namesNoProviders(e) {
  const parts = [];
  for (let x = e, depth = 0; x && depth < 5; x = x.cause, depth++) {
    parts.push(String(x.message ?? ''), String(x.responseBody ?? ''));
    if (x.data) parts.push(JSON.stringify(x.data));
  }
  const text = parts.join(' ');
  return /no_providers_available/i.test(text)
    || /none of the (specified |requested |allowed )?providers/i.test(text);
}

/** Whether an error, or anything it was caused by, is a timeout or an abort. */
export function isTimeout(e) {
  const names = new Set(['TimeoutError', 'AbortError']);
  const codes = new Set(['UND_ERR_HEADERS_TIMEOUT', 'UND_ERR_BODY_TIMEOUT',
    'UND_ERR_CONNECT_TIMEOUT', 'ETIMEDOUT', 'ABORT_ERR']);
  for (let x = e, depth = 0; x && depth < 6; x = x.cause, depth++) {
    if (names.has(x.name) || codes.has(x.code)) return true;
  }
  return false;
}

/** The failure line for an error. First match wins. */
export function classify(e) {
  const base = { message: String(e?.message ?? e), name: e?.name ?? null };
  if (NoObjectGeneratedError.isInstance(e)) {
    return { code: EXIT.schema, out: { ok: false, error: { ...base, kind: 'schema',
      text: e.text ?? null, finishReason: e.finishReason ?? null }, usage: e.usage ?? null } };
  }
  if (NoOutputGeneratedError.isInstance(e)) {
    return { code: EXIT.schema, out: { ok: false, error: { ...base, kind: 'no_output' } } };
  }
  if (namesNoProviders(e)) {
    return { code: EXIT.providers, out: { ok: false, error: { ...base,
      kind: 'providers_unavailable', status: e?.statusCode ?? null, retryable: false } } };
  }
  if (typeof e?.statusCode === 'number') {
    return { code: EXIT.gateway, out: { ok: false, error: { ...base, kind: 'gateway',
      status: e.statusCode, retryable: !!e.isRetryable, generationId: e.generationId ?? null } } };
  }
  return { code: EXIT.other, out: { ok: false, error: { ...base, kind: 'other',
    timeout: isTimeout(e) } } };
}

/** total_cost from GET /v1/generation, polled while the usage is not yet
 *  ingested (404), or null. Off unless the request asks for tries. */
export async function pollCost({ fetch, sleep, host, id, key, tries, intervalMs }) {
  for (let i = 0; i < tries; i++) {
    const res = await fetch(`${host}/v1/generation?id=${encodeURIComponent(id)}`,
      { headers: { Authorization: `Bearer ${key}` } });
    if (res.status === 200) {
      const body = await res.json();
      const cost = body?.data?.total_cost ?? body?.total_cost;
      return cost == null ? null : Number(cost);
    }
    if (res.status !== 404) return null;
    if (i + 1 < tries) await sleep(intervalMs);
  }
  return null;
}

function scrubbed(out, key) {
  const text = JSON.stringify(out);
  return key ? JSON.parse(text.split(key).join('***')) : out;
}

/** One call. Returns {code, out}; `out` is the line to print, without the key. */
export async function run(req, deps = {}, env = process.env) {
  const d = { ...REAL };
  for (const [name, value] of Object.entries(deps)) if (value !== undefined) d[name] = value;
  const key = env.AI_GATEWAY_API_KEY ?? '';
  try {
    if (typeof req?.model !== 'string' || /^vmc\//i.test(req.model)) {
      return scrubbed({ code: EXIT.other, out: { ok: false, error: { kind: 'other',
        message: `refused model ${JSON.stringify(req?.model)}: a virtual model may route anywhere`,
        timeout: false } } }, key);
    }
    const timeoutMs = req.timeoutMs ?? 900000;
    const agent = d.makeAgent(timeoutMs);
    const httpFetch = (url, init) => d.fetch(url, { ...init, dispatcher: agent });
    const host = String(req.host ?? DEFAULT_HOST).replace(/\/+$/, '');
    const settings = { apiKey: key, fetch: httpFetch };
    if (req.host) settings.baseURL = host + API_PATH;
    const gateway = d.createGateway(settings);

    const content = [{ type: 'text', text: req.prompt ?? '' }];
    for (const m of req.media ?? []) {
      if (m.label) content.push({ type: 'text', text: m.label });
      content.push({ type: 'file', mediaType: m.mediaType, filename: basename(m.path),
        data: await d.readFile(m.path) });
    }
    const options = { ...(req.gateway ?? {}) };
    delete options.models;
    const call = {
      model: gateway(req.model),
      messages: [{ role: 'user', content }],
      maxRetries: req.maxRetries ?? 0,
      abortSignal: AbortSignal.timeout(timeoutMs),
      providerOptions: { gateway: options },
    };
    if (req.instructions) call.instructions = req.instructions;
    if (req.schema) {
      call.output = Output.object({ schema: jsonSchema(req.schema), name: req.schemaName });
    }
    const r = await d.generateText(call);
    const step = r.finalStep ?? r;
    const meta = step.providerMetadata?.gateway ?? {};
    let generationCost = null;
    const tries = req.costPoll?.tries ?? 0;
    if (meta.cost == null && tries > 0 && meta.generationId) {
      try {
        generationCost = await pollCost({ fetch: httpFetch, sleep: d.sleep, host,
          id: meta.generationId, key, tries, intervalMs: req.costPoll?.intervalMs ?? 2000 });
      } catch {
        generationCost = null;
      }
    }
    return scrubbed({ code: EXIT.ok, out: {
      ok: true, output: req.schema ? r.output : r.text, usage: r.usage ?? null,
      finishReason: r.finishReason ?? null, modelId: step.response?.modelId ?? null,
      generationId: meta.generationId ?? null, cost: meta.cost ?? null, generationCost,
      routing: meta.routing ?? null,
    } }, key);
  } catch (e) {
    return scrubbed(classify(e), key);
  }
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString('utf8');
}

function isMain() {
  try {
    return realpathSync(process.argv[1] ?? '') === realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return false;
  }
}

if (isMain()) {
  let result;
  try {
    result = await run(JSON.parse(await readStdin()));
  } catch (e) {
    result = scrubbed({ code: EXIT.other, out: { ok: false, error: { kind: 'other',
      message: `could not read the request: ${e?.message ?? e}`, timeout: false } } },
    process.env.AI_GATEWAY_API_KEY ?? '');
  }
  process.stdout.write(JSON.stringify(result.out) + '\n');
  process.exitCode = result.code;
}
