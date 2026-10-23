// The Node program's own tests (AUTO-124). Run by tests/test_gateway_node.py
// with `node --test`, skipped there unless Node >=22 and gateway/node_modules
// exist. generateText, createGateway, fetch and sleep are injected, so no
// test reaches the gateway; the loopback test uses the real undici fetch and
// Agent against a server on 127.0.0.1.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { NoObjectGeneratedError, NoOutputGeneratedError } from 'ai';
import { EXIT, makeAgent, run } from '../gateway-engine.mjs';

const KEY = 'vck_node_test_key_0123456789';
const ENV = { AI_GATEWAY_API_KEY: KEY };
const MODEL = 'google/gemini-3.6-flash';
const SCHEMA = { type: 'object', properties: { answer: { type: 'string' } }, required: ['answer'] };
const USAGE = { inputTokens: 1200, outputTokens: 300,
  inputTokenDetails: { noCacheTokens: 1000, cacheReadTokens: 150, cacheWriteTokens: 50 } };

function request(extra = {}) {
  return { model: MODEL, instructions: 'the house', prompt: 'judge it', schema: SCHEMA,
    schemaName: 'answer', media: [], gateway: {}, timeoutMs: 5000, maxRetries: 0,
    costPoll: { tries: 0, intervalMs: 1 }, ...extra };
}

function result({ cost = '0.0012', generationId = 'gen_01', output = { answer: 'yes' } } = {}) {
  return { output, text: JSON.stringify(output), usage: USAGE, finishReason: 'stop',
    finalStep: { response: { modelId: MODEL },
      providerMetadata: { gateway: { generationId, cost,
        routing: { finalProvider: 'google', modelAttempts: [{ canonicalSlug: MODEL }] } } } } };
}

/** Deps that record the call and the provider settings, and answer `answer`. */
function fakes(answer = () => result(), more = {}) {
  const seen = { calls: [], settings: null };
  return {
    seen,
    deps: {
      createGateway: (settings) => { seen.settings = settings; return (id) => ({ id }); },
      generateText: async (call) => { seen.calls.push(call); return answer(call, seen); },
      makeAgent: () => ({ fake: true }),
      fetch: async () => { throw new Error('no network in tests'); },
      sleep: async () => {},
      ...more,
    },
  };
}

test('models is stripped and the rest of the gateway options pass', async () => {
  const { seen, deps } = fakes();
  const r = await run(request({ gateway: { only: ['google'], has: ['structured-output'],
    models: ['anthropic/claude-opus-5'] } }), deps, ENV);
  assert.equal(r.code, EXIT.ok);
  assert.deepEqual(seen.calls[0].providerOptions.gateway,
    { only: ['google'], has: ['structured-output'] });
  assert.equal(seen.calls[0].maxRetries, 0);
  assert.equal(seen.calls[0].instructions, 'the house');
  assert.equal(seen.settings.apiKey, KEY);
  assert.equal(seen.settings.baseURL, undefined);
});

test('a vmc/ model is refused before any call', async () => {
  const { seen, deps } = fakes();
  const r = await run(request({ model: 'vmc/house-judge' }), deps, ENV);
  assert.equal(r.code, EXIT.other);
  assert.equal(seen.calls.length, 0);
});

test('a named host becomes the base URL', async () => {
  const { seen, deps } = fakes();
  await run(request({ host: 'https://proxy.example/' }), deps, ENV);
  assert.match(seen.settings.baseURL, /^https:\/\/proxy\.example\//);
});

test('file parts carry the basename, the media type and the bytes', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'gw-'));
  const clip = join(dir, 'clip.mp4');
  await writeFile(clip, Buffer.from('frames'));
  const { seen, deps } = fakes();
  await run(request({ media: [{ path: clip, mediaType: 'video/mp4' }] }), deps, ENV);
  const [text, file] = seen.calls[0].messages[0].content;
  assert.equal(text.type, 'text');
  assert.equal(file.type, 'file');
  assert.equal(file.filename, 'clip.mp4');
  assert.equal(file.mediaType, 'video/mp4');
  assert.equal(Buffer.from(file.data).toString(), 'frames');
});

test('a success line carries the answer, usage, model, generation and cost', async () => {
  const { deps } = fakes();
  const r = await run(request(), deps, ENV);
  assert.deepEqual(r.out.output, { answer: 'yes' });
  assert.equal(r.out.modelId, MODEL);
  assert.equal(r.out.generationId, 'gen_01');
  assert.equal(r.out.cost, '0.0012');
  assert.equal(r.out.generationCost, null);
  assert.deepEqual(r.out.usage, USAGE);
});

test('classification, in order', async () => {
  const cases = [
    [Object.assign(new Error('rate limited'), { statusCode: 429, isRetryable: true }),
      EXIT.gateway, (o) => { assert.equal(o.error.kind, 'gateway'); assert.equal(o.error.retryable, true); assert.equal(o.error.status, 429); }],
    [Object.assign(new Error('bad request: no_providers_available'), { statusCode: 400 }),
      EXIT.providers, (o) => assert.equal(o.error.kind, 'providers_unavailable')],
    [new NoObjectGeneratedError({ message: 'No object generated', text: 'not json',
      response: { id: 'r', timestamp: new Date(), modelId: MODEL }, usage: USAGE, finishReason: 'stop' }),
      EXIT.schema, (o) => { assert.equal(o.error.kind, 'schema'); assert.deepEqual(o.usage, USAGE); }],
    [new NoOutputGeneratedError({ message: 'No output generated' }),
      EXIT.schema, (o) => assert.equal(o.error.kind, 'no_output')],
    [new Error('something else'), EXIT.other, (o) => { assert.equal(o.error.kind, 'other'); assert.equal(o.error.timeout, false); }],
  ];
  for (const [error, code, check] of cases) {
    const { deps } = fakes(() => { throw error; });
    const r = await run(request(), deps, ENV);
    assert.equal(r.code, code, error.message);
    check(r.out);
  }
});

test('the cost poll returns total_cost after two 404s', async () => {
  let polls = 0;
  const fetch = async () => {
    polls += 1;
    return polls < 3 ? { status: 404, json: async () => ({}) }
      : { status: 200, json: async () => ({ data: { total_cost: 0.0042 } }) };
  };
  const { deps } = fakes(() => result({ cost: null }), { fetch });
  const r = await run(request({ costPoll: { tries: 5, intervalMs: 1 } }), deps, ENV);
  assert.equal(polls, 3);
  assert.equal(r.out.generationCost, 0.0042);
});

test('the cost poll gives up after its tries, and never runs at 0', async () => {
  let polls = 0;
  const fetch = async () => { polls += 1; return { status: 404, json: async () => ({}) }; };
  const { deps } = fakes(() => result({ cost: null }), { fetch });
  let r = await run(request({ costPoll: { tries: 3, intervalMs: 1 } }), deps, ENV);
  assert.equal(polls, 3);
  assert.equal(r.out.generationCost, null);
  polls = 0;
  r = await run(request(), deps, ENV);
  assert.equal(polls, 0);
  assert.equal(r.code, EXIT.ok);
});

test('stdout never carries the key', async () => {
  for (const answer of [
    () => { throw Object.assign(new Error(`unauthorized key ${KEY}`), { statusCode: 401 }); },
    () => result({ output: { answer: KEY } }),
  ]) {
    const { deps } = fakes(answer);
    const r = await run(request(), deps, ENV);
    assert.ok(!JSON.stringify(r.out).includes(KEY));
    assert.ok(JSON.stringify(r.out).includes('***'));
  }
});

test('the Agent handed to undici fetch is the one whose timeouts apply', async () => {
  const server = createServer((req, res) => {
    setTimeout(() => { res.writeHead(200, { 'content-type': 'application/json' }); res.end('{}'); }, 2000);
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${server.address().port}/`;
  try {
    let dispatched = 0;
    const counted = (ms) => {
      const agent = makeAgent(ms);
      const dispatch = agent.dispatch.bind(agent);
      agent.dispatch = (...args) => { dispatched += 1; return dispatch(...args); };
      return agent;
    };
    // No abort signal on this fetch: only the Agent's header timeout can stop it.
    const answer = async (call, seen) => {
      const res = await seen.settings.fetch(url, { method: 'POST', body: '{}' });
      await res.text();
      return result();
    };
    // fetch left undefined: run() keeps the real undici fetch.
    const { deps } = fakes(answer, { makeAgent: counted, fetch: undefined });
    const fine = await run(request({ timeoutMs: 5000 }), deps, ENV);
    assert.equal(fine.code, EXIT.ok, JSON.stringify(fine.out));
    assert.ok(dispatched > 0, 'the request did not go through the Agent');
    const cut = await run(request({ timeoutMs: 1000 }), deps, ENV);
    assert.equal(cut.code, EXIT.other, JSON.stringify(cut.out));
    assert.equal(cut.out.error.timeout, true);
  } finally {
    server.closeAllConnections?.();
    server.close();
  }
});
