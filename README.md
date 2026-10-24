# automation

> **Why this exists:** [`PURPOSE.md`](PURPOSE.md) says what the pipeline is for,
> the quality bar it answers to and the principles every change must keep.
> This README says how the pieces work.

Two things that fit together:

- **`claude_engine.py`** — use **Claude Code as the AI engine of a Python
  program**: reasoning, schema-enforced structured output, and real tool-using
  agentic work, driven from your own code.
- **`yt`** — a gen-AI production pipeline built on it that **creates and
  composes top-tier YouTube videos** on scene-render and the sr-core SDK:
  visually intense explainers with voice-over and motion graphics, in motion
  graphics, 2D, 2.5D, 3D, voxel, pixel art, planetary and mathematical
  simulation, in whatever aesthetic the editorial decisions call for — research with sources, visual
  direction, screenplay, narration, authored motion graphics, sound, and the
  rendered, gated film.

No dependencies. The engine launches the `claude` CLI in non-interactive print
mode and parses its JSON / stream-JSON output. `codex_engine.py` offers the
same API on the Codex CLI; `YT_ENGINE=codex` runs the harness's stages on it
(its module docstring lists what maps, what is emulated and what Codex lacks).
A dollar budget needs a price for the model: when the model is unpriced (for
example under a ChatGPT sign-in), set `CODEX_PRICES` or a token cap with
`CODEX_BUDGET_TOKENS`, or the budgeted call refuses to start. The claude
models are priced in `ytstudio/spend.py` (`CLAUDE_PRICES` overrides a row),
so a call cut off by its timeout is billed from its stream, not booked at $0.
A Vercel AI Gateway id (`anthropic/claude-opus-5.5`, `openai/gpt-5`) is priced
and judged for family as its direct name (`ytstudio/models.py`); a 1M-token
context (`[1m]`) and another provider's Claude (`bedrock/claude-opus-5`) are
unpriced, and a virtual model (`vmc/<slug>`) has no price and is refused as
an author or a reviewer.

## Requirements

For a supported interactive alternative to CLI model access, see
[`docs/chat-bridge.md`](docs/chat-bridge.md): `YT_ENGINE=bridge` lets an active
chat session answer the pipeline's queued requests. It supports read-only
research and screenplay work; it is not an unattended model service.

- `claude` on PATH (`claude --version`)
- An authenticated session: `claude auth`, `claude setup-token`, or `ANTHROPIC_API_KEY`
- Or, opt-in, Vercel AI Gateway: `YT_GATEWAY=vercel` with the gateway key in
  `AI_GATEWAY_API_KEY` sends every `claude` call through
  `https://ai-gateway.vercel.sh/claude-code` (`ytstudio/gateway.py`). The key
  goes in `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_API_KEY` is emptied for the
  child, so a direct key left in the shell cannot win.
  `YT_GATEWAY_AUTH=subscription` keeps your Claude login and sends the key as
  a header instead; in that mode unset `ANTHROPIC_API_KEY`, because a key left
  set is sent to the gateway and used instead of the login. `YT_GATEWAY_URL`
  replaces the host. `bare` is refused under the gateway in either mode, and
  so is `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` or
  `CLAUDE_CODE_USE_FOUNDRY` left on, since the CLI would route there instead.
  A key alone turns nothing on, a set switch with no key fails before any
  call, and with `YT_GATEWAY` unset (or `YT_GATEWAY=off`, `0`, `false`, `no`,
  `none`) every call goes to Anthropic exactly as before. Nothing writes
  `~/.claude/settings.json`. Through the gateway a call is booked at its
  model's price row times the tokens the CLI reports in `modelUsage`, since
  the CLI's own `total_cost_usd` is its estimate at Anthropic's rates; the
  gateway charges list price with no markup, so the table is an estimate of
  the bill, short of a model priced below list and of surcharges such as
  zero data retention. The CLI's figure stays on the record's `by_model` row
  as `cli_cost_usd`, beside a count of the calls booked each way
  (`cost_table_calls`, `cost_cli_calls`). A web search the CLI ran
  (`webSearchRequests`) is added at $0.01 each, Anthropic's rate, since the
  CLI's figure holds it and the tokens do not. A model with no row, a result
  with no tokens, a prompt above a model's tier (Haiku 5.5 bills 5x above
  100K prompt tokens, which its row does not model) or another server
  tool's requests is booked at the CLI's figure instead and counted in
  `cost_cli_calls`, which `yt doctor` warns about. The CLI's own
  `--max-budget-usd` stays on as a backstop. A gateway budget's 402
  (`quota_for_entity_exceeded`, a team or project's "budget exceeded", an
  API key's "Quota limit exceeded") ends the call at the first one with no
  retry, whether the CLI prints it on stderr or in an `is_error` result,
  and a 429 is retried as any transient failure is. Under `YT_ENGINE=codex` the same switch makes
  the gateway Codex's model provider with `-c` pairs on the command line
  (`model_provider="vercel"`, base URL `https://ai-gateway.vercel.sh/codex/v1`,
  `env_key="AI_GATEWAY_API_KEY"`, `wire_api="responses"`), so codex reads the
  key from the environment and nothing is written to `$CODEX_HOME/config.toml`.
  A `codex_config` pair of your own wins over the gateway's, a bare model
  name goes to `-m` with `openai/` in front (Codex's own default too, passing
  over any Claude slug in its models cache), and a Claude id is never passed
  to codex. A `model_provider` of your own in `codex_config` gets the model
  as given. Codex reads the key as it is, so whitespace around it is refused.
  `yt doctor` checks the route when the switch is set (see `yt doctor`
  below), and `yt doctor --gateway-ping` shows the team's credit balance.
  [Vercel AI Gateway (optional)](#vercel-ai-gateway-optional) below puts
  it in one place.
- Optional, for a stage that opts into the gateway engine only: Node 22 or
  later and `cd gateway && npm ci --ignore-scripts` (until
  `gateway/package-lock.json` is committed, `npm ci` has no lockfile to read,
  so the first install runs `npm install --package-lock-only --ignore-scripts`
  before it). `gateway_engine.py`
  (stdlib Python, so the pipeline stays dependency-free) runs
  `gateway/gateway-engine.mjs`, a small program on the AI SDK (`ai` 7.0.150,
  pinned exactly), for models neither CLI reaches: one that listens to audio
  or watches video, or a reviewer of another family. A stage chooses it per
  harness, `Harness(engine="gateway")` behind its own flag, never through
  `YT_GATEWAY` or `YT_ENGINE`; it needs `AI_GATEWAY_API_KEY`, takes media
  files with `decide` and serves `decide` only, never `inspect`. `yt qa
  --against` is the first stage to opt in (AUTO-128).
  `YT_GATEWAY_NODE` names the node binary, and `GATEWAY_PRICES` adds price
  rows for gateway ids (USD per 1M tokens) for a call the gateway returns no
  cost for. With no stage asking for it nothing runs Node.
  The first stage to ask is the script's review: `--reviewer-model pool` (or
  `sdk:<provider/model>`) on `yt script --review` and on the calibration run
  takes a reviewer from another maker out of `reviewers.POOL`
  (`ytstudio/reviewers.py`): pinned rows of Z.ai, Moonshot, Google, DeepSeek,
  Alibaba and Mistral models, each with an upper-bound price at or below
  opus's and an allowlist of providers sent as the gateway's `only` with
  `has: ["structured-output"]`. A row with no pinned provider yet is passed
  over. It needs `YT_GATEWAY=vercel` and the prerequisites above, or the
  stage exits 2 before anything is built. Each run reads the catalogue and
  the model's providers (no key needed) and keeps only the pinned ones that
  are up, offer structured output, are priced within the row and are not of
  a quantization in `YT_REVIEWER_EXCLUDE_QUANT` (comma-separated, default
  `fp4`); when that read fails the pinned list stands only for a row whose
  providers were checked live (`verified` in the row), and an unchecked row
  is refused until the read succeeds. The script is sent to those
  third-party providers, and script.json says which ones.
  The second is `yt qa --listen` (AUTO-126): the film's own audio track,
  copied out of `video.mp4` with no re-encode, goes to a listener from
  `models.LISTEN_MODELS` (default `google/gemini-3.6-flash`, served by
  Google only). It also needs `ffmpeg` and `ffprobe` on PATH, which run
  here: a stream copy and a probe decode and encode nothing. Without
  `YT_GATEWAY=vercel` the flag is refused, never replaced.
  The third is `yt qa --watch` (AUTO-127): the cut, `video.mp4`, goes to a
  model that watches it (`--watch-model`, default `google/gemini-3.6-flash`,
  served by Google only). It needs the same Node 22 or later and
  `gateway/node_modules`, the catalogue reachable, and a render host
  (`RS_RENDER_HOSTS`): a cut too large to send is cut down there, never
  here. Without `YT_GATEWAY=vercel` the flag is refused, never replaced.
- Python 3.10+

## Vercel AI Gateway (optional)

The claude and codex CLIs are the default: with nothing set every stage
calls them directly, exactly as before, and they answer every call they can.
[Vercel AI Gateway](https://vercel.com/docs/ai-gateway) is an opt-in on top
of them, for two things they do not give. One is a single key and bill for
the CLIs' own calls (`YT_GATEWAY=vercel`), which changes their transport,
never which tool runs. The other is the models neither CLI reaches: one that
listens to the film's audio, one that watches it, a reviewer from another
maker. Each of those passes sits behind its own flag and runs on the gateway
engine (`gateway_engine.py`); without the gateway that flag is refused, or
the pass is skipped and recorded as not evidenced. The gateway never
replaces a CLI call.

To switch it on, make a key on the AI Gateway API Keys page of your Vercel
team, then:

```sh
export AI_GATEWAY_API_KEY=...         # the key; alone it turns nothing on
export YT_GATEWAY=vercel              # unset it (or set off) to go direct again
# export YT_GATEWAY_AUTH=subscription # keep your Claude login (see below)
./yt doctor --env --gateway-ping
```

Both can also go in an env file `yt` loads (`--env-file`, `$YT_ENV_FILE`,
`./.env`). With the switch set, doctor's "gateway" block names the
endpoints in use (never the key) and fails on anything the route would
refuse, and `yt doctor --gateway-ping` adds one `GET /v1/credits` showing
the team's balance; doctor makes no gateway call without it. No setup
command is needed: Vercel's `npx vercel ai-gateway setup` is described as
writing the gateway into the CLIs' own settings (`~/.claude/settings.json`,
`~/.codex/config.toml`), where it would stay on for every claude and codex
session outside `yt`. `yt` sets the variables for its own child processes
only and writes neither file.

| Variable | What it does |
|---|---|
| `YT_GATEWAY` | `vercel` sends the CLIs' calls through the gateway. Unset, or `off`, `0`, `false`, `no`, `none`: direct, with each call's argv, child environment, booking and decision cache as before. Any other value is refused, so a typo never quietly means direct. |
| `AI_GATEWAY_API_KEY` | The gateway key. A set switch without it fails before any call. |
| `YT_GATEWAY_AUTH` | How the claude CLI signs in. `key` (the default) sends the key as `ANTHROPIC_AUTH_TOKEN` and empties `ANTHROPIC_API_KEY` for the child, so a direct key left in the shell cannot win. `subscription` keeps your Claude login and sends the key in a header; unset `ANTHROPIC_API_KEY` yourself there, or the CLI sends it in place of the login. Codex always sends the key. |
| `YT_GATEWAY_URL` | Replaces the host `https://ai-gateway.vercel.sh`, for a proxy in front of it. The host only: the paths are added. |
| `YT_GATEWAY_NODE` | The node binary the gateway engine runs (22 or later; default `node`). |
| `GATEWAY_PRICES` | Price rows for gateway ids (JSON, USD per 1M tokens), for a gateway-engine call the gateway reports no cost for. |

**Where the calls go.** The claude CLI goes to
`https://ai-gateway.vercel.sh/claude-code`, set as its base URL, as
Vercel's [Claude Code page](https://vercel.com/docs/ai-gateway/coding-agents/claude-code)
describes. `bare` is refused under the gateway, and so is a
`CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` or
`CLAUDE_CODE_USE_FOUNDRY` left on, since the CLI would go there instead.
Under `YT_ENGINE=codex` the gateway becomes Codex's model provider through
`-c` pairs on its command line (base URL
`https://ai-gateway.vercel.sh/codex/v1`, `wire_api="responses"`, the key
read from `AI_GATEWAY_API_KEY`), as Vercel's
[Codex page](https://vercel.com/docs/ai-gateway/coding-agents/openai-codex)
describes; a bare model goes as `openai/<name>`, and a Claude id never goes
to codex. The gateway engine is a small Node program on the AI SDK, run only
for a stage that opts in (installed as Requirements above says).

**Spend.** Through the gateway a claude call is booked at its model's price
row times the tokens the CLI reports, not at the CLI's own
`total_cost_usd`, which is its estimate at Anthropic's rates; that figure
stays on the `by_model` row as `cli_cost_usd`. The gateway charges list
price with no markup ([Vercel's pricing page](https://vercel.com/docs/ai-gateway/pricing)),
so the booking is an estimate of the bill at list price: a model Vercel discounts, or a surcharge such as zero data
retention, is not in it. A call with no price row or no tokens is booked at
the CLI's figure instead and counted in `cost_cli_calls`, which `yt doctor`
warns about. A gateway-engine call books the cost the gateway reports for
it, else a price row (`GATEWAY_PRICES` first), and its row says which
(`cost_gateway_calls`, `cost_generation_calls`, `cost_table_calls`,
`cost_unpriced_calls`).

**402 and 429.** A gateway budget's 402 (`quota_for_entity_exceeded`, a
team or project's "budget exceeded", an API key's "Quota limit exceeded")
is permanent: the call stops at the first one, with no retry, as it does on
the engines' other permanent failures (a spent `--max-budget-usd`, a
`--max-turns` stop, a refused sign-in). A 429 is retried as any transient
failure is, within the engine's retries. Any other failure text, an
exhausted credit balance's included (Vercel does not document its wording),
is treated as transient and retried up to the engine's retries before the
call fails.

**Models.** A gateway id (`anthropic/claude-opus-5.5`, `openai/gpt-5`) is
priced and judged for family as its direct name, so the reviewer still
differs from the author by family (or by maker, for a reviewer from another
maker: `sdk:<id>` or `pool`). A virtual model (`vmc/<slug>`) may route
anywhere, so it has no family and no price, and is refused as an author or
a reviewer.

**Not wired.** BYOK (your own provider keys), zero data retention, and the
gateway's provider order and model fallbacks are not set for the CLIs'
calls. The gateway engine strips the gateway's model fallbacks, which may
cross families, and narrows providers only through a pass's own allowlist
(the reviewer pool's and the listener's `only`).

**Passes on the gateway engine.** `yt script --review --reviewer-model pool`
(or `sdk:<provider/model>`), `yt qa --listen`, `yt qa --watch` and
`yt qa --against FILE --compare-model MODEL`, each described in its stage's
section below.

Not yet checked against the live gateway: structured output through it
(the claude CLI's schema flag, which every decision relies on), whether
`bare` can sign in with a gateway token (so it is refused), a 1-hour prompt
cache (Vercel documents only a 5-minute one), the model ids the Claude Code
endpoint accepts for an alias such as `opus`, any cost or generation-id
field in the CLIs' own output, the wording of the errors for a budget or
exhausted credits as the CLIs print them, and which files
`npx vercel ai-gateway setup` writes. Until they are, treat each as
unknown.

---

# The `yt` pipeline

> **Operating it:** the operator's manual is [`ytstudio/SKILL.md`](ytstudio/SKILL.md),
> the source of truth for the invariants, the stage order, rendering on the
> remote hosts and what counts as done. `.claude/skills/yt/SKILL.md` links to
> it, so a Claude Code session loads it as the `yt` skill; `tests/test_skill.py`
> keeps the two in step. This README explains the pieces; that file says how
> to run them.

## The stack

The renderer is **scene-render** (the Rust engine) and the document model is
**sr-core** (the schema and its Python SDK). That is the whole animation stack:
`yt vfx` has the model author each section's effects as `<symbol>`s of a
scene-render document -- against the full schema, asking it with the SDK and
checking with the engine, not through a vocabulary of ours -- `yt build`
includes them shot by shot, and `yt render` hands the document to the engine.
No Manim, no After Effects, no other tool is in the loop. The intelligence is
injected through `claude_engine`; the tooling owns only the plumbing.

A document that validates can still render nothing -- a camera that frames
no object, a light the include does not carry, a solver that refuses its grid
-- so authoring has a frame gate: `sr.py frame` renders a proof frame at the
midpoint of every shot the symbol holds, the way the build includes it;
`ytstudio/framecheck.py` runs the checks that need no eyes (flat, black,
clipped, too dark, a `hero-` object the camera never frames, a set mostly out
of view) over each frame and over one short draft encode per shot, which is
the only thing that computes the engine's camera analysis. The author's own
`sr.py frame` renders stills only; the encodes run once a round at the
stage's gate, on the encode host, one per host at a time, cached by the
world's fingerprint, and an encode the host killed for memory is the
author's error (the world is too heavy for any host). Then the model
reviews the frames against the publication bar and scores them 0-10. Under
`--bar` (7) the findings go back to the author, up to `--rounds`
rounds (2). Each round of authoring lands in a new `vfx/vNN/`; nothing
authored before is overwritten. The reviewer also gets a copy of each frame
with a labelled 8x6 grid (`frames/grid/`, a ruler only, never kept with a
round) and may name the cells a finding is about; the author is then told
those cells' pixels and the object3D ids in them.

Rendering runs on other machines: `RS_RENDER_HOSTS=user@a,user@b` in `.env`.
`yt render` probes each host once (engine, RAM, GPU) and routes by
docs/compute.md's rule -- memory, not GPU: a full encode and the audio mix go
to the host with the most RAM, stills and frames to the one with the least,
which is the faster GPU. Before each job one short ssh per host reads its
one-minute load and GPU memory in use (cached 20 s); a host whose load is
over its cores, or whose GPU has under 1 GB free, is passed over for stills,
frames and validation while another is quiet, and a sync the host does not
take (a lost connection, not a partial transfer) sends the job to the other
host once and marks the first dead for the next moments. A slice that moves
waits for its turn on the other host (one slice per host at a time: a slice
was sized for the GPU alone) and the slices after it pass the marked host
over; the slices' audio mix stays on its host, since the join happens there.
Every job ssh and rsync gives up on a connection after 10 s (the short
pre-routing checks sooner). `--slices N` splits a long encode across every host
(one `--start/--end` range each, the audio mixed once, joined by stream copy; a job on a GPU host samples the engine's own processes every 0.2 s (`nvidia-smi pmon`, graphics included; accounting mode, then a memory.used delta, as fallbacks) into the report's `gpu_memory`; a still records a peak only, an encode a peak over the range it rendered, and only an encode's rate counts: the count rises, and an unsliced encode slices on its own, until a slice fits the smallest host's GPU memory; with no encode measured, the count stays and the output says so (a still's peak is named and ignored). The vfx gate warns of layers with no start/end).
A job pulls back only the files its own `-o` and `--report` name, with `--update` so an older remote copy never replaces a newer local file; sliced encodes keep their slices in `.slices/<key>/` (never mirrored, reused after a failed run, removed once the film is joined), and honour `--output` (NAME.mp4, render-NAME.json) and `--keep-remote`.
Each host keeps one mirror of the project, refreshed incrementally; every
job runs in a directory of its own hardlinked from it, the video, stills and
report come back, and the job directory is removed. `yt gate` runs the same
way, because the delivery gate needs ffprobe and the engine. Nothing is
built or installed here.

Each stage reads what the stages before it wrote, so you never pass files
between them by hand. Beside the chain sit the tools that keep it honest:
`yt retime` writes the measured narration timings back into the screenplay,
`yt pilot` cuts a short approval reel at full fidelity, `yt gate` runs the
delivery checks, `yt review` makes the PDF review sheet, and `yt doctor` says
what is missing and which command fixes it.

```bash
./yt ideas --niche "semiconductor manufacturing" --count 20
./yt research --title lithography
./yt styles   --title lithography --rich
./yt script   --title lithography --minutes 12 --polish --verify
./yt props    --title lithography                      # CC0 models, HDRIs, textures
./yt voice    --title lithography --engine espeak      # scratch narration, free
./yt retime   --title lithography                      # the screenplay takes the measured read
./yt keyframe --title lithography                      # lock the look: section 1, for approval
./yt keyframe --title lithography --approve            # after looking at keyframe.md
./yt vfx      --title lithography --budget-hours 40    # carries section 1, authors the rest
./yt sound    --title lithography --review --fetch     # the cue sheet (each cue a catalogue search or a synthesis spec), then its files
./yt build    --title lithography                      # the scene-render document
./yt render   --title lithography --quality draft
./yt pilot    --title lithography --seconds 30,60,180   # nested approval cuts
./yt gate     --title lithography --preflight           # what delivery needs
./yt doctor                                             # health, and next steps
```

Or all of it at once, for one idea:

```bash
./yt run --title lithography --minutes 12
./yt ls                      # what you have, and how far each video got
```

| stage | what it produces | typical cost |
|---|---|---|
| `yt ideas` | titles, genres, premises; deduplicated against a growing store | cents |
| `yt research` | claims with sources, a vetted source list, licence-checked footage, gaps, an outline | **$0.78** for a cut-down run; more at the defaults; `--paper` adds the paper read and the `06b-paper` digest (`spend.PAPER_USD`, assumed $1.00) |
| `yt styles` | visual treatments ranked against the video's message, with shots, palette, type, motion | $0.06–0.10 |
| `yt script` | the full AV screenplay: picture, narration verbatim, supers, assets, citations | **$0.51** for 3 minutes with polish and fact-check |
| `yt props` | CC0 glTF models, HDRIs and texture maps from Poly Haven under `props/`, listed in `props.json` for the vfx brief | two cached model decisions when no phrase is given, else none; no key |
| `yt keyframe` | the look-lock from the film itself: `yt vfx`'s plan for every section and the lead section authored once, judged by the mechanical gate, its frames awaiting the operator's approval; `yt vfx` then carries it and authors the rest against it (`--sheet`: the older separate style frame) | the vfx plan and one author session; no vision review |
| `yt voice` | one WAV per shot, measured drift against the script, captions | no model calls; ElevenLabs bills per character, local engines are free |
| `yt vfx` | every effect specified and costed, then one world `<symbol>` per section, written by the author as a Python program with the sr-core SDK that the stage builds itself (determinism and provenance checked), with a single camera, its shots as phases, proof frames checked (frame, camera and geometry: a hero that penetrates a set solid, or that reads merged into one on screen with no shadow, is an error with time, object and depth) and reviewed, versioned under `vfx/vNN/` | scales with `--per-section`, `--budget-usd` per section, `--rounds` |
| `yt sound` | effects, ambience, score, motifs, mix targets, cue sheet; with `--fetch`, a CC0/CC-BY file per cue from Freesound under `sound/` | **$1.07** for 3 minutes with listen-back |
| `yt build` | the scene-render document: continuous includes of the authored worlds, model-planned graphics drawn from the design, captions, markers, the mix with the fetched cues on buses per role; one film-wide colour pipeline (ACEScg, a tone mapper and one CDL look, chosen by the model from the treatment and palette, or none) so no section is graded apart | one planning call per section, one for the look |
| `yt render` | the encoded video, with the engine's report, from a job-scoped directory on a render host | no model calls |
| `yt retime` | the screenplay's shot lengths, replaced by the measured ones | no model calls |
| `yt pilot` | 30/60/180-second cuts of the finished film, nested, with what each proves | no model calls |
| `yt gate` | the delivery verdict: loudness, scopes, frames, caption limits, run on a render host and naming the requirement that stopped it; plus the geometry check (a hero that penetrates the set, or reads merged into it on screen, blocks whatever the tool and the triage said) | one triage call |
| `yt review` | the PDF review sheet: frames, captions, markers, sources, credits | no model calls |
| `yt qa` | the scorecard per pass: every shot sampled at its midpoint and each narrated line where it starts (`line: <first words>`, AUTO-104), up to 60 stills (`--frames N` above the shot count adds the line stills first, then evenly spaced stills; below it, or past 60 shots by default, names the shots left out; the lines left without a still are named in `lines_unsampled`), one frame window per sample (the still is its middle frame), a contact sheet, the mechanical findings (flat, clipped, frozen, jump, off-camera, `penetration`: the hero inside a set solid, and `reads-merged`: the hero over a surface on screen with no shadow, from the engine's evaluated geometry and camera; advisory and always minor, AUTO-145: `no-build`, a diagram's first shot whose ink does not grow from a still after its cut, through its line stills, to one before its end, and `text-dense`, too many words on screen counted from the rendered document), the model's review, scored as the median of three samples (an audio category no current listening review covers is null in every sample, never a median), and a side-by-side comparison with the previous kept cut at the same timestamps | `qa.QA_SAMPLES` (3) inspection calls per pass, at most one merge decision and one `<pass>-compare-pass` inspection when an earlier scored pass exists (`--recheck` adds one `<pass>-recheck` inspection, AUTO-106); one render job per sample (by default one per shot and per narrated line, at most 60; `--frames N` sets it), plus up to 12 single frames for the diagram build stills (AUTO-145), one eval job per pass; `yt improve` pays this every pass; `--listen` (gateway, opt-in, AUTO-126) adds one `listen-<audio sha>` decision on the film's audio and at most one `-again`, and on a narrated film one `transcript-<audio sha>` decision and at most one `-again` (AUTO-152), priced by its preflight and refused over `--listen-budget-usd` (0.50), none when the same mix was heard before; `--watch` (gateway, opt-in, AUTO-127) adds one `watch-sNN` decision per piece of the cut and at most one `-again` each, plus up to 12 render jobs for the moments it reports, none when the same cut was watched before |
| `yt improve` | routes each QA finding to the stage that owns it, briefs that stage through `feedback/`, re-runs it and everything downstream, keeps the pass only if it measurably improved | one routing decision per pass, plus the stages it runs |
| `yt doctor` | every video's state and the command that advances it | no model calls |
| `yt probe` | the realism probe suite: one neutral brief per class of material, light or motion, composed by an agent coding with the SDK, built and framed by the harness, scored by a different model with each defect's cause | **$1.06** for one probe and one round; up to three author sessions and three reviews per probe |

Those figures are measured on `sonnet`, which is the default; `--model opus`
costs more. `--effort {low..max}` and `--fallback-model` apply to every call a
stage makes (or set `CLAUDE_ENGINE_EFFORT` / `CLAUDE_ENGINE_FALLBACK_MODEL`
once); a decision asked at a different effort is asked again rather than
served from the project's cache. `yt research` and `yt sound` are the expensive stages — research
because it actually browses the web (and, with `--paper`, reads the paper first), sound because a cue sheet is long.

## Where things land

`work/` is **ephemeral by policy**: a project directory is an output of the
pipeline, never the only copy of anything, and the standard of done is a run
that regenerates it end to end from `work/ideas.json` forward. Delete freely.

Everything for one video lives in one directory:

```
work/
├── ideas.json                      every idea from the last `yt ideas`
├── ideas.store.json                every unique idea ever generated
├── channel.json                    each film's opening, closing, form, sections, treatment, palette
└── how-euv-light-is-made/          one video
    ├── idea.json
    ├── dossier.json  README.md  claims.csv  sources.csv  assets.csv  notes.md
    ├── raw/  downloads/            cached research stages, fetched assets
    ├── styles.json  styles.md
    ├── script.json  script.md  narration.txt  shotlist.csv
    ├── props/<kind>/<id>/  props.json   CC0 models, HDRIs, textures from Poly Haven
    ├── keyframe/    keyframe.png  frames.png                    the look: the lead section's frames
    ├── keyframe.json  keyframe.md       the section, its gate findings and approval
    ├── voice/       voice.json  narration.vtt  narration.srt  voice.wav
    ├── vfx/vNN/<section>.scene.xml     one authored world per section, per round
    │   ├── frames/                     the proof frames the gate and the review read
    │   │   └── grid/                   the review's grid copies, a ruler, not evidence
    │   └── <section>.include.scene.xml the cleaned copy the build's <include> points at
    ├── vfx.json     vfx.md     vfx.csv  the plan, the active version, the review rounds
    ├── sound/       index.json          the fetched cue files, and which Freesound id each is
    ├── sound.json   sound.md   cues.csv
    ├── scene.xml    build.json          the compiled document, and its report
    ├── build/proof/                    the film's frames the build compared with the proof frames
    ├── pilot.scene.xml  pilot.json      the approval cuts
    ├── retime.json  gate.json  review.json  review.pdf
    ├── video.mp4    render.json         the film's encode; other jobs write render-<job>.json
    ├── qa/pass-01/  frames/  samples.json  contact-sheet.png  previous/  side-by-side/  scorecard.json  findings.md
    ├── qa.json                         the latest verdict
    ├── retention/*.json  retention.json what Studio showed for the published film, read onto its shots
    └── decisions/<stage>/<name>.json   what the model decided, and from what
```

`--title` finds a video by matching a project already on disk, then an idea in
`work/ideas.json`, and failing both takes what you typed as the title itself.
`--project DIR` points at one directly, `--work DIR` keeps videos somewhere
other than `./work`, `--index N` picks the Nth idea, and `-` reads one idea as
JSON on stdin. Each stage writes its own files into the project *and* prints to
stdout, so `--json` / `--md` / `--csv` are only for sending a copy elsewhere.

`work/channel.json` is the channel record: every time `yt script`, `yt
styles` or `yt qa` writes, the film's row is refreshed with its opening (the plan's
`hook.trigger`), its closing (`ending.device`), its form (`form.name`), its section shape, its primary
treatment, the palette roles its own swatches fill and the type families it
names. A film counts once its `qa.json` says READY TO PUBLISH; drafts and
remakes are recorded but never compared. The next film's plan and its review
are shown how the last twelve films to count opened, unfolded and closed
(ordered by when each first counted, so a re-run on an old film reorders
nothing), and `yt styles` warns when its primary row reuses one's treatment
or palette. A project whose files cannot be read keeps its last row, with a
warning, and stops no other film. A
standalone project has no channel and none of this happens; a channel's first
film is asked exactly what it always was. Typography is recorded, never
compared: eleven shipped families make a shared one the catalogue's, not the
film's.

## Standalone, or in the pipeline

Every tool runs on its own or as one step of the pipeline, through the same
code path. Inside the pipeline a stage finds its inputs in the project
directory and writes beside them. On its own it is handed files:

```bash
yt styles --dossier dossier.json
yt script --dossier dossier.json --direction styles.json
yt voice  --script script.json --engine kokoro --lang pt
yt vfx    --script script.json --voice voice.json --styles styles.json
yt sound  --script script.json --direction styles.json --dossier dossier.json
yt build  --script script.json --voice voice.json --vfx vfx.json --out cut/scene.xml
yt render --scene cut/scene.xml
yt retime --script script.json --voice voice.json
yt pilot  --scene cut/scene.xml --seconds 30,60      # markers are the shots
yt gate   --scene s.xml --video v.mp4 --render render.json --captions v.vtt --voice-end 212.4
yt review --scene cut/scene.xml --engine rs
yt qa     --scene cut/scene.xml                      # samples from the markers
yt props  --project DIR "<set piece>" "<hdri>"       # phrases given, or derived from the project
yt assess "a photoreal droplet, 30 s" --styles styles.json --script script.json
```

`yt research` is the first stage, so an explicit `--project DIR` is its
standalone form; `yt doctor --project DIR` reads one directory.

The rules are the same everywhere. An explicit `--project DIR` is
authoritative, even before the directory has an idea (one is made from
`--title` or the directory's name). An explicit input file anchors a
standalone run to that file's directory: outputs, `decisions/` and `qa/` land
there, and nothing is planted in `work/`. Each upstream artifact a stage reads
has a `--<artifact> FILE` flag (the voice stage's `--voice` is a voice id, so
its screenplay flag is `--script`). A stage that normally reads a manifest
reads the document instead when there is none: `yt pilot` and `yt qa` take
the shots from the scene's `kind="beat"` markers, and slice the runtime evenly
when there are no markers either. The decisions are still the model's; only
the richness of what it is told changes.

## The harness

Every stage decides through `claude_engine`; the Python around it is
plumbing. `ytstudio/harness.py` is the one door: `Harness.decide(name, prompt,
schema)` asks one structured question and `Harness.inspect(name, prompt,
schema, cwd=…)` lets an agent read files — frames, a PDF, a report — before it
answers, with read-only tools (`max_turns=N` caps that agent by turns, as
`budget_usd` caps it in dollars; no stage sets one, and a run the cap
stops is a failed, billed row, never saved). Both write the answer under
`decisions/<stage>/<name>.json` with a digest of everything the model saw, so
a stage re-run with the same inputs costs nothing (the answers are served from
`decisions/<stage>/`) and a changed input is asked again; `--fresh` asks
again where the stage has it. `yt ideas` records its decisions but always
asks afresh. The web-agent passes in `yt research` and the `yt assess`
investigator are agents, not decisions. Each stage's record (`research.json`,
`styles.json`, `script.json`, `vfx.json`, `keyframe.json`, `props.json`,
`assess.json`) carries its spend once, under `usage`: `{cost_usd, model,
calls}`. `engine` is not that key: it names the TTS engine in `voice.json`
and the binary in `render.json`.

There is no flag to skip the model. A decision the harness could not get is an
error the stage reports, never a quiet default, because a default is a decision
nobody made. Where a stage validates the answer (the pilot's budget and
nesting, the build's image paths), an invalid one is asked again once and then
recorded as a finding.

Vision is real on the inspection path: the `claude` CLI's `Read` tool hands a
PNG to the model as an image, which is how `yt qa` and `yt review` judge
frames. The plain `decide` path is text only, so an inspection ends with the
agent that looked filing the form itself.

## Standards and keys

Editorial rules that bind more than one stage live in `work/house.md`, in
Markdown sections keyed by stage (`## all`, `## script`, `## styles`, `##
sound`, `## vfx`, `## voice`, `## format`). Each stage is handed `## all` plus
its own (the script stage `## format` too), so no prompt
carries a rule that does not apply to it, and a `house.md` inside one video's
directory replaces sections of the same name for that video alone. `--house
FILE` points elsewhere, `--no-house` ignores it. The digest of what was loaded
goes into every manifest, so an artifact says which standards produced it.

Under all of them sits the shipped default, `ytstudio/house/default.md`: the
craft corpus in `docs/narrative/` distilled by hand into `## all`, `## script`,
`## styles`, `## vfx`, `## sound`, `## voice` and `## format` (the narrator, opening, ending, hand-off, series and form, which the script stage reads before its
own section). Each film's format is chosen for its subject: an opening or an
ending device recurs across films only when the channel's own `## format` names
one, and the channel record below measures where a film repeats an earlier one
anyway. The narrator is one proxy across films;
its temperament is a device of its own (AUTO-158): the same every film when
the channel's `## format` sets one, otherwise each film's own, chosen from its
subject and named in the plan's `persona`. So is the film's form (AUTO-157):
fixed only by a `- Form: <name>` line in the channel's `## format`, otherwise
chosen from this film's subject. Each section opens with a comment
naming the corpus rules it states; comments never reach a prompt, and a test
holds the ids to the corpus. The channel's file replaces a section by writing
one of the same name. The keyframe, which has no section of its own, reads
`## vfx` until a house writes `## keyframe`. A cached decision is keyed on its
stage's block (`## all` and the sections that stage reads), so an edit to
`## all` re-asks every decision in every project on its next run, an edit to
one section re-asks only the stages that read it, and an edit to a comment
re-asks nothing; `yt doctor` warns for each manifest whose block was decided
under a different house digest.

The order each stage sees is deliberate: its own system prompt says what it is,
`house.md` says how this channel works, the dossier and the direction say what
is true of this video, and `--notes` outranks all of it for one run.

Parts of a standard are measurable rather than promptable, and those became
checks: supers nobody can read in the time they are up (17 characters a second,
the same limit delivery holds captions to), figures put on screen that the
narration never says, and narration faster than a viewer can follow.

API keys are read from `--env-file`, `$YT_ENV_FILE`, `./_tmp/do_not_delete.env`,
`./.env` or `~/.config/pals-notes/.env`, whichever has them first. Only the
names are ever printed, never a value, and an exported variable always wins
over a file.

## The stages

### `yt ideas`

Generates ideas in deduplicated batches, in parallel, so a long list stays
varied instead of becoming fifty variants of one idea. Every run also appends
to `work/ideas.store.json` and excludes everything already in it, so repeated
runs accumulate one growing, duplicate-free backlog. Ideas generated past
`--count` are kept in the store rather than thrown away.

`--niche` · `--inspo "Cool Worlds,Asianometry"` (26 channels have a hand-written
voice brief; `--list-styles` prints them) · `--rich` for hooks and keywords ·
`--rank` to score 1–10 on effort-to-value · `--genres` · `--no-store`

`--rich` also plans each idea past its title, so a script starts from a
premise rather than a headline: `question` (what the title raises and does not
answer), `stakes`, `viewer_belief` (what a typical viewer already believes,
the half-knowledge the question is pitched at) and `promise` (title plus
thumbnail concept in one sentence). `yt script`'s brief prints all four, the
promise marked frozen, and the plan is told the promise is frozen: its own
promise, what the first fifteen seconds promise, keeps to it, and the last
section pays it off; nothing changes it. `--rank` also labels each idea's
`curiosity` (`sustained`, `brief` if the gap closes well before the end of the
idea's stated length, `none`) and `title_concrete` (`concrete` if the title
names a specific thing, place, person or event, else `abstract`) beside the
score; they are the model's judgements, and the order is the score's alone. A
label outside its set is kept as written, said on stderr and noted on the
idea's `why` (docs/narrative: attention 1, 3, 4; structure 5, 6, 13).

### `yt research`

Staged, cached web research: it plans the questions, researches them in
parallel with real browsing, extracts claims with the source each came from,
builds the source list, plans the visuals, finds footage in open catalogues
(NASA, Openverse, Wikimedia Commons, Internet Archive), vets it against
look-alikes, verifies each licence and that each URL really serves a file,
audits its own gaps, and writes a script outline. The decisions run in
dependency levels, `--jobs` wide: claims, the source list and the shot list
at once, then the asset search beside the gap audit, then the outline. Every
stage is cached, so a re-run resumes instead of paying twice; `--fresh`
forces the work again.

`--depth` questions · `--jobs` · `--shots` / `--per-shot` · `--asset-sources` ·
`--allow-sa` · `--agentic-assets` · `--download` · `--budget-usd` · `--fresh` ·
`--paper`

The plan depends on the genre. A narrated explainer gets the full scope; a
wordless piece -- a genre or premise that says cinematic, music video,
ambient loop, "no narration" -- gets the lighter cinematic scope
(`research.scope_for`): three questions about reference, look and the
physics of what moves, no misconception hunt, claims only about what is on
screen, and gaps that do not block on unsourced numbers. The dossier records
which scope it got; an explicit `--depth` wins over either.

`--paper REF` (or `yt run --paper REF`; a DOI, an arXiv id or page, a URL,
or a local PDF) makes a film that explains one paper: the `paper` scope.
The paper is read first (`raw/00-paper.json`), by WebFetch or, for a local
file, Read; the read fails unless the agent's own tool events show the paper
opened, and a local path never enters the dossier or a prompt, which name
the file `file:<basename>`; idea.json keeps its absolute path, so a later
run from another folder finds it, and `yt run` hands a paper planted there
to research and to its estimate. A dossier cached for another paper, or for
none, is researched again from the questions on instead of replayed. The questions then ask after the problem's cost, the state
of the art before it, the reception, the consequences and the limitations;
the paper is the first source, and its own claims cite it. One more decision,
`06b-paper` (one `-again` at most), writes the dossier's `paper` digest:
its citation and six items (problem, contribution, significance,
implications, consequences, limitations), each resting on claims quoted
from the dossier, the contribution on one sourced to the paper itself. The
dossier gains a `## The paper` section. `yt script` then places each item in
a section (`framing`, `mechanism_section`), and the plan's pre-checks hold
the problem, significance and implications before the mechanism, with a
misplaced item recorded as a `warning: paper framing:` finding that
`yt doctor` reports. Under `YT_ENGINE=codex`, `--paper` is refused with exit
2: codex reports no WebFetch or Read events, so the read cannot be evidenced.

Downloaded assets are untrusted bytes from the open web: they are written to
`downloads/` and never opened or executed.

### `yt styles`

Works out what the video has to make a viewer *understand*, then ranks visual
treatments against that — not against what looks expensive. 29 treatments have
a hand-written brief (`--list-styles`); `--only` narrows the pool, `--add`
extends it, `--avoid` drops from it, and a name it has never heard of is passed
through for the model to characterise itself.

Constraints are hard gates, not discounts: `--solo` · `--no-face` ·
`--vertical` · `--money {shoestring,modest,funded}` · `--tools` · `--time`.

Every treatment is judged against the one stack that will produce it. When
`yt assess` has run for the video, its verdict goes into the brief as
`assess.stack_brief` -- the constructs the engine proved, what it only
approximates, what nothing in the pipeline supplies, the render estimate --
and the tools a treatment names are scene-render constructs, never other
software.

By default the choice is one call. `--deliberate` buys more rigour:

| stage | what it adds |
|---|---|
| `sweep` | score the whole pool first, then detail only the shortlist |
| `independent` | score each candidate in its own call, so none is judged in the shadow of the last |
| `critique` | attack every pick afterwards, revise the scores, name what was wrongly left out |
| `full` | `sweep` + `critique` |

Once the pick is final, after any critique's reorder, the primary row is
measured against the channel's counted earlier films (`work/channel.json`):
the same treatment name, or a background and an accent each within 12 ΔE of
one film's, is printed as a `warning: channel:` line and saved under
`styles.json` `channel`. It is a measurement, not a judgement: no prompt sees
the earlier films, since the critique is the author's own family, and the pick
and its order are what they would be without it.

### `yt script`

Writes the screenplay one call per section — a 15-minute script written in a
single pass loses its thread and re-announces its own numbers. Each section is
written knowing its beats, its word budget (`seconds/60 × --wpm`, which is what
actually controls runtime), the previous section's last shots verbatim, the
figures already spoken aloud, and the next section's heading to set up.

With research in the project, every shot carries the source behind its claim,
and `--verify` re-reads the finished script for lines the research does not
support, then fixes them in the same run: each blocking or important line
gets the smallest rewrite, or is kept with a reason (a kept flag still binds
the picture), and only the revised shots are checked again, for at most two
rounds. `--report-only` keeps the old report-without-fixing behaviour.
Each sourced shot also names the dossier claim it rests on (`c1`, `c2` ..)
and how settled the line is (`established`, `hypothesis`, `speculation`,
`what-if` or `none`); the code caps it at what the claim allows (a
limitation the paper names is at most a hypothesis, a low-confidence claim
speculation), and `script.md` prints it as `**STATUS**`. With `--review`,
`--verify` also asks the reviewer of another family whether each line is as
sure as its research (`verify-status`), and the fact-check's revise may
weaken a status, never raise it; without `--review` that check is recorded
as not evidenced, never as passed.
`--polish` reads the narration end to end and rewrites only what repeats,
stumbles or buries its point.

`--review` has a reviewer of another model family (`--reviewer-model`,
default `opus`, refused with exit 2 when it is the author's own family) read
the beat sheet before any section is drafted. `--reviewer-model pool` (or
`sdk:<provider/model>`) reads with a model from another maker on the gateway
engine instead (see Requirements): the plan review, the picture check and
the story review's readers ask it, the drafting and every revision stay on
the author's CLI, and `plan_review`, `review` and `picture` in script.json
name it under `reviewer`. The `plan-review` decision
reads seven rows of the narrative rubric (the train, the stakes, the open
loops, the reveal's setup, the ending, the devices, and the film's form, a
row only the plan review reads) under a reviewer role of its own
(`narrative.reviewer_role(plan=True)`: accept, revise or reject, never the
story review's recommend or consider) and is told how to attack the plan:
reorder the sections and see whether the story survives, find a section that
advances no question, check that the payoff names something concrete. It
also reads the pre-checks code can count (`rule:` an AND between sections,
a question never closed, a reveal before its constraint, an empty hook gap,
a middle section with no escalation or the same one as another, a rehook of
a question not in the ledger or already closed; `house:` a long film with no
rehook, section 2 with none, more than 180 s without one, a one-section plan
over 180 s asked to split) and decides on its
own. Its grounds to reject are the train, the order, a section that moves
nothing and the payoff; a plan with no concrete consequence the viewer can
picture before its mechanism, and no declared twist, is grounds to revise,
never on its own to reject (AUTO-161: the reading says so in
`stakes_concrete`, and a second plan is told it even when the problems list
is thin; each round of `plan_review`, in script.json or `plan_review.json`,
keeps what the reading's fields found under `field_lines`). A form that does
not follow from the subject is a ground to revise only, never to reject
(AUTO-157): the plan names its `form` (one of `script.FORMS`, why this subject
calls for it, the shape the sections give it, the obvious form it passed
over), the pre-checks gain `house:` lines for an empty reason, a generic one
(it names only the form, or nothing the train, promise, title or headings
name), an `other` that names none, and a form other than the one the
channel's `## format` fixes (`- Form: countdown`), and the reading says
`form_fits` with its `form_problem`, which a second plan is told. A second
`revise` whose only fault is the form drafts on: `plan_review` records
`advisory: ["form"]`, script.md an `advisory: form` line and the findings a
note. A reading whose fields
contradict its verdict (an accept that finds the order a list or
`stakes_concrete` or `form_fits` false, a revise that names no problem and whose fields find
no fault, a reject on stakes or the form alone: `narrative.check_plan_review`) is asked once more as
`plan-review-again`; what still contradicts is recorded under `unsettled`,
and the verdict stands. `accept` drafts; `revise`
sends the plan back once with the problems (`plan-2`, read again as
`plan-review-2`); `reject`, or a second `revise` (but one on the form alone), exits 1 before a section
is written, and no `script.json` is left for `yt run` to take as done: the
reading, the stage's engine report and its spend go to `plan_review.json`
instead, removed by the next run that gets past the plan.
With counted earlier films in the channel record the plan's writer and its
reviewer are shown how they opened, unfolded and closed (newest first, each
by its slug), the pre-checks gain `channel:` lines (an opening or closing device, or an exact section shape, an earlier film had,
and one advisory line for a form earlier films share, since a form may recur
when it fits the subject), and the attack gains
a sixth objection, after the stakes (AUTO-161): could a returning viewer predict this film from one of
them? A noticeable repeat is listed under `repeats` with a section 0 problem
naming the film, and is a ground to revise, never to reject (a reject whose
only grounds are repeats is asked again); a `channel:`
line the reviewer sets aside goes under `dismissed` with why. Without
`--review` the measured lines are kept as `note: channel:` findings. With no
counted earlier film the prompts, the schema and the role are what they were.
`--report-only` records a revise and drafts on; a reject still stops. No score
is set as a bar here, as keyframe and vfx set one: the verdict is the bar. The review is capped by
`--review-budget-usd` (2), adds about $0.50 (the picture-check below about
$0.50 more, and the story review is priced from the script's words), and is kept in `script.json`
`plan_review` and the `## Plan review` section of `script.md`.

The same `--review` reads the drafted, polished script before the rules and
the fact-check, so a story the reviewer would not voice is caught before any
take is recorded. Three samples (`review-r1-s1`, `-s2`, `-s3`) are asked the
same prompt, each with the rubric's four levels in its own order; a sample
whose answer breaks its own rubric (`narrative.check_review`) is asked once
more as `review-r1-sK-again`. The grade per criterion and the verdict are the
median of the valid samples; two that disagree on the verdict are a
consider; a failed call is a failed engine row, and fewer than two valid
samples is `not_evidenced`, never a pass. A flag on a shot or a section the
script does not have is a broken answer, asked again like any other. So is a
flag whose `quote` is not whole words its shot or section says, read past
whitespace and curly or straight quote marks and nothing else (empty for
the whole script, and only empty on a silent shot or section), or whose `conflicts_with` names a shot that does not
say its quote: a contradiction quotes both lines. The writer, `script.md`
and the merged flag's `quotes` show the words each sample meant. A cold
reader (`cold-read`) hears the narration alone, with no research, plan or
house in its prompt or its system, and restates the premise, names the terms
it was never told and says why each major claim matters; only then is it
shown the plan's questions and asked where they were answered
(`cold-read-answers`), so they cannot prime the premise. `cold-read-match`
checks that premise against the train. Each sample also names the curiosity
trigger the opening's first three spoken sentences pull (`first_minute.trigger`),
the round keeps the label most samples give, and script.md shows it against
the plan's `hook.trigger`; only an opening heard to pull none is a flag, a
whole-script hook flag left unresolved (`hook-trigger-match`, carrying the
samples' own whole-script hook problem and fix). The `tension`
row grades whether each middle section puts more at risk than the one before.
The `voice` row (AUTO-138) grades whether the narrator finds things out with
the viewer rather than lecturing: speaking to them, asking aloud what they are
about to ask, flagging the step likeliest to lose them, letting a discovery
land, and any humour serving the point (allowed, never required). A Poor
voice is a consider, never a reject on its own, and a voice flag must name
its shot and quote it: one on shot 0 is asked again, so a lecture-register
line goes to `revise-story-1`.
A consider sends its binding flags
back: a shot's to `revise-story-1` (the line may be restructured, never given
a new fact), a section's (shot 0) to `redraft-NN` (the section written again
with the flags, how the section before it ends and how the one after it
opens), and a flag on the plan itself stays unresolved with `yt script
--fresh --review`. The revision is read again with the review it revises
(`review-r2-s1`.., `previous` required) and stands only when the samples call
it better, for at most `--rounds`; when it stands, the cold reader reads the
draft that stands (`cold-read-r2`..), and what is unresolved is that draft's.
Beside the cold read sits a viewer quiz (AUTO-115), a measure that gates
nothing: an author shown the plan and the research, never the narration,
writes 4 to 8 keyed multiple-choice items (`quiz`), and the cold reader takes
them before hearing anything (`quiz-before`) and after hearing the narration
(`quiz-after`, again as `quiz-after-r2` when a revision stands), never shown
the key. The right answers before and after, the gain and the items missed
are in `review.cold_read.quiz` and on a `Viewer quiz` line in script.md; a
model's "before" is a prior, not a lay viewer's. A gain under
`QUIZ_GAIN_FLOOR` (1 question, house, no standard) with items still missed
after hearing is one `warning: story review: viewer quiz gain ...` finding
(AUTO-162), naming each missed question and the fact its key gives, so
`yt doctor` warns before voice is paid for; a ceiling score (nothing missed)
or a failed quiz says nothing (a failed re-ask after a revision drops round
1's measure too). `yt script --revise` asks no quiz, so a warning it keeps
measured the narration the `--review` run left behind. It is no flag, unresolved item or `--gate`
input, and no revise sees it: the quiz still gates nothing.
It is priced from the script's words: about $0.84 for 10 minutes at opus. Every verdict is appended to
`work/review-log.jsonl` with `label: null`, the raw material of the
calibration set; copy it out, since `work/` is disposable.

A reject is an `error: story review: reject` finding that `yt doctor` fails
on; `narration.txt` is still written and the stage exits 0, because the
reviewer is not calibrated yet. `--gate` makes a reject or `not_evidenced`
leave `narration.txt` unwritten and exit 1, and script.json then records
`gate: true`. Turn it on only when `python -m ytstudio.narrative calibrate
tests/fixtures/narrative` shows the seeded-bad fixtures all fail, the good ones
pass and the length audit passes (the padded copies of the good script and
of the recap ending may not win more than 60% of their pairs, and an audit
whose every pair was read at the ceiling fails), and work/review-log.jsonl
holds at least 20 rows a person has labelled reject, consider or recommend
(coverage's Pass counts as reject). The calibration reads the cases on the
real models with the plan review where the case names a plan verdict, the
three samples on every case, and the cold read where there is narration;
the premise and jargon cases are judged on the cold read alone, since the
rubric has no criterion for them, and the plan that explains before it gives
a reason to care (`no-stakes`) also fails a plan review that says its
`stakes_concrete` is true. It costs about $1 a case at opus, revises
nothing and logs nothing, and prints a pass/fail table and how often the
reviewer agreed with the labels; `yt doctor` warns `gate on with N labelled
verdicts (20 needed)` for a gated script.json below that count. `yt run` never
passes it, and it is refused without `--review` or with `--revise`, which
never reads the story again. The record is `script.json` `review` and the
`## Story review` section of `script.md`.

`yt qa`'s judge has a calibration of its own (AUTO-156). `python -m
ytstudio.qa calibrate tests/fixtures/qa --model sonnet` scores each fixture
film (tests/fixtures/qa/<case>/: build.json, script.json, voice.json,
samples.json, 480 px frames and expected.json) through the same evidence and
prompt as a QA pass, in a folder `work/calibration-qa/case-<hash>/`
(`qa.case_key` of the case's name, so the judge cannot read the label;
the report's `cases[].dir` maps it back), and checks the raw card: the verdict, the
categories that must fall under or stay over the bar, the moments a
two-sample finding must land in, and how far a category must drop against
the good case. Mechanical findings reach the prompt and are recorded, never
judged. It prints a row a case and the false positives and negatives per
category, writes `work/calibration-qa/report.json` (removed while the cases
are scored, so a judge reading past its folder finds no labels) with the
`fixture_set` digest of the set it ran, and caches its readings, so a second
run costs nothing (the cost of a live run is not measured yet). Only a
finding two readings agree on counts, and a case whose findings merge failed
fails. `capture-fixture` empties the case it writes into, keeping only
`expected.json`.
`scripts/seed_qa_fixtures.py` draws the cases byte for byte; its good films
are stand-ins, so the set does not pass until `python -m ytstudio.qa
capture-fixture work/<slug>/qa/pass-NN tests/fixtures/qa/good` cuts down one
of your own passes, you label it (`"label": "owner"`) and re-seed with
`--base`. Every pass then records `judge_calibration` in `qa.json` and prints
`judge: fixture-calibrated (<model>); 0 owner-labelled films` or `judge: not
calibrated against the QA fixtures (<why>); ...` under its verdict:
calibrated only for the report's model, judge digest and the shipped
`tests/fixtures/qa` set, and never for a `--listen` or `--watch` pass, whose
judge the fixtures do not run. It gates
nothing: READY, `yt doctor` and `yt improve` read the verdict as before.

The same reviewer then reads the drafted screenplay with the sound off. After
the story review, `picture-check` is shown each shot's picture (`visual`), what it must
demonstrate (`shows`) and its on-screen text, with the plan's beat for each
section, and never the narration. It writes the claim each section's
pictures carry on their own and says whether that is the beat, then names
the shots with a large number left bare, a mechanism the picture does not
show, a list nothing signposts, or a process the picture shows in one fixed
state while `shows` says it demonstrates a change (`static_process`,
AUTO-144), a formula put on screen before anything showed what it relates,
or one the beat does not need (`picture:formula`, direction 9), and a size,
arrangement, path or interior given only in words or a flat label, with no
human-scale comparison, cutaway or camera move through it
(`picture:spatial`, AUTO-145). Each is a flag for one `revise-picture`
decision, the only revision allowed to change a shot's picture (the
fact-check's revise still rewrites only the narration). A flag the writer
keeps, with its reason, binds nothing: the reviewer never heard the line,
so a number compared aloud or a claim the line carries reads as a fault to
it, and the writer, who sees both, has answered. It goes to `picture.kept`
and a `note:`. A flag on a shot the revision changed in any way counts as
answered; the pictures are not read again. What stands goes to
`script.json` `picture.flags`, a `warning:` in the findings and `## Picture
check` in `script.md`, and its important flags, with the story review's,
reach the vfx planner, every section author and reviewer and the keyframe
brief as `## Story constraints`, beside the fact-check's; a story flag on a
section binds that section's shots. `yt script --revise` moves the flags of
a shot it changes to `picture.stale`, where they bind nothing.

A static process is not answered by a writer's keep (a line cannot make a
still picture move), and it is checked again after authoring. The vfx
planner is asked once more when it covers such a shot with a type that keeps
the picture still (`vfx.STATIC_TYPES`); a second such answer is recorded as
the section plan's `warnings`. Then `yt vfx`'s motion audit asks the
reviewer's model, on rows read from each effect's document (what changes,
which property, when, during its shots), whether the viewer watches the
change (`reach-motion`); the answer is checked against those rows and asked
once more before it is believed. What must change and does not is vfx.json
`reach.motion.static`, `REQUIRED:` work in the refinement plan, a `yt doctor`
warn naming `yt vfx --sections N`, and the section `yt improve` re-authors.
It fails no stage, and a flat treatment that builds the process in steps
passes.

Sections are drafted in parallel (`--jobs`, default 4; `--jobs 1` drafts in
order, each reading the one before) against a ledger that reserves each
figure for the section that owns it; a figure spoken twice is rewritten after
drafting. A shot's visible text lives in `on_screen_text`, apart from the
direction in `on_screen`, and the reading-speed check measures only that text.

The plan is a story brief: the film's promise and payoff, who wants what
and what is in the way, the stakes for the viewer, the misconception it
breaks (from the dossier's sourced misconceptions, numbered M1, M2 ...), the
audience, the reveal, a ledger of questions each opened in one section and
answered in the same section or a later one (or left open on purpose, with
why), and an ending with a callback and the next film.
Each section joins the one before with but, therefore or because, and
every shot names its function (hook, setup, reveal, rehook, payoff ...) and
what its picture shows of its line; `script.md` prints the question ledger.

The question ledger is kept across sections drafted side by side
(`script.QuestionLedger`): each writer is told which questions its section
opens and which later section answers them, which it answers and where they
were opened, and names the shot of its own that raises or answers each
(`opens_at`, `closes_at`, its number inside the section). After drafting,
the ledger is measured: `rule:open-loop` (a plan question no shot answers,
unless it is left open on purpose), `rule:closed-unopened` (an answer
before the question was raised), `rule:raises-nothing` (a section the
ledger has open and close nothing, minor) and, with `--review`,
`rule:unanswered-by-reader` (an answer a writer marked that the cold reader
did not hear) and `rule:unexplained-term` (AUTO-138: a term the cold reader
needed and was never told, on the first line that says it; important on a
lay plan, otherwise minor). The important ones go back to the writer with
the figures said twice (`revise-ledger`; the reader's, questions and lay
terms, in `revise-ledger-2`), and the writer names the questions each
flagged line answers as it leaves it (`closes`): a line that changed is no
answer by itself, except to a term, whose definition keeps the word. What
stands is a warning, and script.json's `questions` names each question's
`opened_at` and `closed_at` shot labels.

After the polish the narration is measured against `script.RULES`, each a
bound in `script.BOUNDS` with the corpus rule it comes from
(`docs/narrative/`) and that rule's strength. The rules count; they do not
judge. Four tiers:

- **error**: `rule:filler` (welcome, subscribe, my name is ... before the
  first sentence about the subject; English and Portuguese; a greeting that
  says a word of the title, "Welcome to Chernobyl, where ...", is that first
  sentence and passes) and
  `rule:sentence` (a sentence over 25 words).
- **important**: `rule:unspoken-number` and `rule:legibility` (the two
  older checks, still recorded under their old script.json keys
  `unspoken_numbers` and `legibility` too), `rule:wall-of-text` (an
  on-screen string over six words, a long numeral counting as one, or one
  that prints more than four words of its own line: on-screen text is labels and keywords, never the
  narration), `rule:numbers` (two or more
  numerals in one sentence), `rule:hook-subject` (no content word of the
  title said or shown in the first 30 seconds; the title's verb counts, since
  telling the key noun from it needs a parser), `rule:hook-answer` (the payoff said in
  those 30 seconds), `rule:payoff-last` (the payoff line not in the last
  section), `rule:marks` (a shot's delivery mark on a word its line does
  not say, two emphasis words in one sentence, or a pronouncer with an
  empty `say`; AUTO-139) and `rule:unframed-status` (AUTO-132: a hypothesis
  or speculation line, after its claim's ceiling, that says no hedge such
  as "might be", "so far" or "researchers think" and shows no label such
  as "Hypothesis", or a what-if line with no "what if", "imagine" or
  "suppose"; the problem names the claim id and why the cap lowered it).
- **house**, the house's own prior, recorded and never revised:
  `rule:clause`, `rule:passive` (English only), `rule:person`, `rule:pace`
  (the words do not fit the shots at `--wpm`: consistency with the planned
  read, not the corpus's 170-190 wpm band), `rule:density`, `rule:hook-promise`, `rule:hook-stakes`, `rule:end-signal`,
  `rule:recap` and `rule:pronouncer` (one warning per section listing the
  capitalised names said mid-sentence with no pronouncer; English and
  Portuguese, and only in a screenplay that carries marks: one with none
  records `not_evidenced: rule:pronouncer (pre-marks script)`) and
  `rule:uncited-claim` (AUTO-132: an established or hypothesis line that
  cites a source but no claim id). The two status rules run only where some
  shot carries a status.
- **minor**: `rule:last-line` (a last sentence over 20 words).

The error and important flags go to one revision (`revise-rules`, a second
round for a line still breaking its rule): the smallest change to the
narration or, for `rule:legibility`, `rule:unspoken-number`,
`rule:wall-of-text` and `rule:unframed-status`, to the shot's on-screen text, or a reason to keep it. The rules
are read again on the final text, after `--verify`. What stands is written
to script.json's `findings` and script.md's Findings section: an error-tier
rule as `error: rule:x: ...`, the rest as `warning: rule:x: ...` (`warning:
house:rule:x: ...` for the house tier), and a rule with no evidence in the
film's language as `not_evidenced: rule:x (pt-BR)`. A standing `error:`
exits 1 and leaves `narration.txt` unwritten (an older one is removed);
`--report-only` records it, revises nothing and writes it. `yt doctor` fails
on it under script (once: the copy in build.json is not failed again under
build) and `yt build` carries it into build.json, where qa lifts it as a
blocker. A `warning:` blocks nothing.

A line can go back without the screenplay. `--revise 1.3,2.1` (with `--why`
the reason), `--revise retime` (every shot `retime.json` sent back as
`rewrite`, with the read it measured and the editor's note) or `--revise
feedback` (the shots `feedback/script.json` names, the `.json` twin `yt
improve` writes beside the brief) asks one `revise-labels` decision over
those shots only, the whole screenplay shown around them: fix the line,
change its supers when the reason is about the screen, or keep it with a
reason. Nothing is planned or drafted again and every other shot is written
back byte for byte. The rules then read the changed shots only, and with
research the changed lines are fact-checked again (`verify-labels`), a
flagged one revised and checked once more as in the stage's own loop
(`revise-labels-facts-1`, `verify-labels-1`); the four files are rewritten with
`script.json.bak` kept, script.json records the round under `revised`, and
the run prints `yt voice --redo <labels>` for the takes the change made
stale.

`--minutes` · `--wpm` · `--sections` · `--outline` to use the dossier's own beat
sheet · `--inspo` for voice · `--notes` which outrank everything else ·
`--jobs` · `--report-only` · `--review` · `--gate` · `--fresh` · `--revise` · `--why`

The runtime floor is 10 seconds (`yt sound` keeps the same floor), so a short
film keeps its own length instead of being padded to half a minute.

### `yt keyframe`

Locks the look before `yt vfx` spends, and the look is part of the film. It
builds the same planner `yt vfx` runs, plans every section (the plan `yt
vfx` reuses) and authors the lead section (`--section`, default 1) once into
`vfx/vNN/`. The mechanical gate judges it: the program built twice to the
same document, the engine's validation, proof frames at every shot, the
frame, legibility and geometry rules. No vision review and no revision round
is paid for before the operator has looked. The frame that shows the most is
copied to `keyframe/keyframe.png` and every proof frame is tiled on
`keyframe/frames.png`; `keyframe.json` records the section, its program and
document, the gate's findings and the approval, and `keyframe.md` is the
sheet for a human.

`yt keyframe --approve` records the approval against the section document's
digest. `yt vfx` then carries the section forward at $0 and every other
author's brief and review name its program (`keyframe/look.py`, copied into
every section workspace as an importable `look.py`; a later look pass's own
workspace gets none, it makes the next look) as the look to build on
(`from look import ...` its palette and type constants, the helpers that build
labels, formulas and recurring objects, the camera language; the build records
its hash in the document's provenance). A look approved after the
animation exists goes in with `yt vfx --relook`: the section is carried and
the others are authored again on the same plan. `--reject "notes"` records the rejection and
the next run puts the notes at the top of the lead section's brief. `yt run`
stops before `yt vfx` while a look awaits approval. On the Euler film the
separate style frame this replaces took 51 minutes and $20 for a frame the
film never used.

A look authored on `design.py`'s house fallback palette (no `styles.json`,
or a primary treatment whose palette names no hex code: `keyframe.json`'s
`palette_source` is `fallback`) cannot be approved: `--approve` exits 1
naming `yt styles --rich`, then `yt keyframe --fresh`, and `yt run` stops
before `yt vfx` naming the same in place of `--approve` (`--skip keyframe`
runs without a look). `yt build` and `yt render` still run on such a film,
but `yt qa` blocks READY and `yt doctor` refuses delivery (AUTO-155).

`--section` · `--jobs` · `--budget-usd` · `--fresh` · `--approve` ·
`--reject` · `--sheet` · `--inspo` · `--per-section` · `--width` · `--height`

#### `yt keyframe --sheet`: the separate style frame

One composed 1920x1080 frame presents the video's most qualified visual
elements together, in the chosen treatment, for the operator's approval. It
is not part of the film.

One harness decision picks the elements from `styles.json` (treatment,
palette roles via `design.py`, typography, motion), `script.json` (what is
on screen; the hero subject; the supers, in the film's language),
`props.json` when present (models, HDRIs, textures with licence and credit),
the dossier's licensed images and the assessment's verdict
(`assess.stack_brief`): the hero, its environment and key light, the
materials of the two or three surfaces that matter most, the palette roles
as swatches, one type sample, the camera language -- for each why it is the
most qualified candidate and its source (a props id, an asset, or
`authored`), ranked by what the frame must prove, capped at eight.

An author agent then works in `keyframe/` with the same workspace `yt vfx`
gives its authors (`sr.py`, `schema.md`, `examples.scene.xml`, `tokens.xml`,
the props with relative paths) and writes `keyframe.scene.xml`: a 1-second
document whose single symbol composes the hero in its environment with the
lights and materials -- real 3D when the treatment is 3D, 2D or 2.5D when it
says so -- plus an unobtrusive band carrying the swatches with their role
names, the type sample and a small label per element naming it and its
source. Colours come from the tokens, nouns from the project. The document
is checked with the SDK and the engine, the frame is rendered on the fast
render host through `render.run_job` (job `still`), `framecheck` runs the
eyeless checks, and a vision review reads the PNG and scores it 0-10 on
visual quality, cinematography, graphics/overlays and overall. Under `--bar`
(7) the findings go back verbatim for up to `--rounds` (2) revision rounds.

Writes `keyframe/keyframe.png`, `keyframe.json` (the elements with sources
and licences, the document, the frame, every round's score and findings, and
the approval: `pending`, `approved` or `rejected`, with the document's
digest) and `keyframe.md`, the sheet for a human. `yt keyframe --approve`
records approval against the document's digest, so a document changed
afterwards is stale; `yt keyframe --reject "notes"` records the rejection,
and the next run feeds the notes to both model calls as the operator's own
instructions, outranking everything else. A re-run without `--fresh` asks
nothing the inputs did not change.

An approved keyframe is the look every vfx world inherits:
`keyframe.approved_keyframe(root)` hands `yt vfx` the document, the frame and
the elements, and `yt doctor` warns when the animation was authored without
an approved keyframe or predates it, and notes a keyframe awaiting approval.

`--rounds` · `--bar` · `--budget-usd` · `--fresh` · `--approve` · `--reject`
· `--host`

### `yt voice`

Records the narration, measures what it really takes, and writes the captions
from the timings the engine reports. The script's shot runtimes are a
words-per-minute budget; the voice is the only thing that knows the truth, so
this stage reports the drift per shot, per section and in total, along with
`voice_end` — the argument the delivery gate asks for.

| engine | what it is |
|---|---|
| `elevenlabs` | the hosted API, through super-tools' kit: exact word timings, prosody carried across cuts, paid per character |
| `kokoro` | local open weights, fast and free, no cloning |
| `chatterbox` | local, clones a voice from a short reference recording |
| `espeak` | a formant synthesiser: robotic but instant. Proves the timeline, never publishes |
| `stub` | silence of the estimated length, for exercising the pipeline with no engine |

`--engine auto` takes the best one available; `yt voice --list-engines` says
which are usable here and what the others need. Keys come from `--env-file` or
the files listed under it (`_tmp/do_not_delete.env`, `.env`,
`~/.config/pals-notes/.env`); only the names are ever printed.

Takes are cached on the hash of their text, engine, voice and settings, so
editing one line re-records one shot. `--redo LABELS` forces only the named
shots; every other take stays cached. After a run the director names the
takes to record again and, per take, may ask for a `speed` (clamped to
0.85-1.15) and ElevenLabs settings (`stability`, `similarity_boost`, `style`);
the next run applies them to those takes alone, each in its own call, and
they are part of that take's cache key. A rate limit is waited out, not
fatal: finished takes come from cache, so nothing is paid twice.

The screenplay's delivery marks reach the engine that can take them
(AUTO-139, direction 27 and 28). Each shot may carry `emphasis` (the word
to stress in each sentence), `pauses` (the words a beat lands before) and
`pronounce` (`{term, say}`, `say` one hyphenated token with the stressed
syllable in capitals, `SHROH-ding-er`). `voice.MARK_SUPPORT` says what
each engine takes: espeak the pronouncer, the pause (`<break
time="300ms"/>`) and emphasis as SSML read with `-m`, and speed; ElevenLabs
the pronouncer, speed and settings (its break tag is not used until it is
checked); kokoro the pronouncer and speed; chatterbox the pronouncer; the
stub nothing. A pronouncer is sent lowercased with its hyphens
(`shroh-ding-er`), so it is never read as an initialism, and one word of
the line is always one word sent. What an engine cannot take is kept on the
take as `recorded_only`. The take's `spoken` is what was sent and is part
of its cache key; an unmarked take keeps today's key and argv. Captions,
cps and caption timing stay on the line as written: ElevenLabs' alignment
of the sent text is mapped back word for word, and where the counts differ
the caption words are estimated from the line (`caption_mismatch` on the
take), never the respelling.

The casting director's per-section delivery notes become engine settings:
a `delivery` row names a section by its heading (or its number) and may
give that section its own `speed` (0.85-1.15) and, for ElevenLabs,
`settings`. The layering is the cast's, then the section's, then a named
retake's; a speed replaces, never multiplies (espeak scales `--wpm` by it).
ElevenLabs makes one call per distinct settings value, in film order, and
a film with no section settings makes today's calls. A row that names no
section reaches no take and is listed in voice.json `delivery.unmatched`;
`delivery` also counts what was applied and what was only recorded, by kind.

A screenplay with no marks (a pre-marks screenplay, written before them) is
asked the casting and retake questions as before, word for word: its kept
cast serves with no model call, the director's section notes stay notes (no
section speed or settings), and its ElevenLabs calls, scenes.json, audio and
captions are today's. A marked screenplay is asked the new questions (the
shots' notes in the Sections brief, a per-section speed and settings
offered), so its cast is a new decision: a model call, and where the voice,
speed or settings come back different, re-billed takes, each changed take
also re-billing the takes after it in its call (the kit's cache key holds
the neighbours' text and request ids). Before that paid run, pin the voice
with `--voice`, cap the spend with `--max-chars`, and compare voice.json
`casting` with the one before; `--dry-run` does not cast and cannot show
it. The marks come from the screenplay, and the first `yt script` after the
marks landed asks each section with the new schema, so an existing
project's sections are drafted again rather than served from their kept
answers, and the new lines make the paid voice stale; `yt script --revise
LABELS` changes only the lines it names.

`yt voice --draft` is direction 29's read-aloud before any voice is paid
for: kokoro, else espeak-ng, reads the screenplay sentence by sentence with
its marks, with no casting and no model call (elevenlabs and the stub are
refused). Each sentence's trimmed WAV is its measured length;
`voice-draft/` holds the takes and `voice-draft.json` the `engine`, the
`script_sha` of the lines and marks read, per sentence `seconds`, `words`
and `wpm`, the per-section drift and the findings: a sentence over 10 s
fails (a practitioner number, not calibrated), a numeral said again in a
later sentence warns, and the drift is info. `listened` stays "not
evidenced"; no stage reads the draft as the voice. A paid (elevenlabs) run
then checks it: no draft, or one of another script, is a warning naming
rule 29; a current draft with a fail stops the run before casting unless
`--accept-draft`. voice.json records `draft: {status, fails}`
(current, stale, missing or accepted); local engines and `--dry-run` are
not gated and get no `draft` key.

Writes `voice/` (one file per shot), `voice.json`, `narration.vtt` and
`narration.srt`, plus `voice.wav` with `--stitch`. `--layout sequential` (the
default) places takes back to back with real gaps and gives the honest runtime;
`--layout planned` uses the script's own timecodes and warns when a take runs
into the next, because then the audio genuinely overlaps. A screenplay with no
narration gets a descriptive captions track and a wordless `voice.json` instead (see `docs/production.md`).

`--format` picks what the hosted engine returns: the default `pcm_<rate>` is
wrapped as WAV here and level-matched, while `mp3_44100_128` and friends are
kept as sent — smaller on disk, but no level matching and no stitching without
ffmpeg.

### `yt vfx`

Specifies the motion-graphics work shot by shot — what has to be animated,
how, and at what cost — and then has the model **author it**: one
claude_engine agent per section writes a scene-render document under
`vfx/vNN/` with **one `<symbol>` per section**, the world: the set, the
materials, the light rig, the hero model and one camera keyed across the
section, every shot a phase at its offset from the section's start. A
section is split into two worlds only where the plan asks for a cut. The
build includes the world once and its clock runs continuously, so a camera
move reads as one take, not as symbols with cameras of their own.

Each world has a **setting** (AUTO-143): the effects plan names the
section's first one (`setting`), and each cut names the setting of the world
it starts. A cut is warranted when the story moves to another place, scale,
time or real-world record, or when no camera move can bridge the change;
inside one setting one continuous take stays the rule. The planner of each section sees
the film's sections in order, each with its opening picture, so the film can
change setting where the story does. A multi-world author brief lists each
world with its setting and keeps the hero's scale, material and colour, the
grade and the type language across them. After authoring, a continuity
review on the reviewer's model (`--reviewer-model`) sees one proof frame per
world, at most two per section, side by side; a planned change of setting is
not a break, and the review names any run of sections that stays in one set,
scale and look longer than the story warrants (`monotony` in `vfx.json`'s
`continuity`), which `yt doctor` warns of. The continuity verdict records
who judged it (`continuity.reviewer`: model, author, `authors` -- the
`author` of every section, carried ones too -- and engine); a break it marks
`blocking`, from a reviewer of another family than every author, keeps `yt
qa` off READY. When a carried section's author is unrecorded or of the
reviewer's family, the break cannot block, and `yt doctor` says so on its
row: re-authoring that section, or `yt vfx --fresh`, makes it block again.
A section `--recover` or `--take-attempt` rebuilds from its rounds records
this run's model and `--bar`. A section whose best reviewed round ended under its bar
(`authored[].bar`, the `--bar` it was reviewed against) is kept, as before,
and `yt doctor` warns of it with the reason it stopped. No other
animation tool is in the loop; a plan that cannot be expressed in
scene-render constructs is a plan this stage will not make. Hour and second
totals are computed in Python, and `--budget-hours` shapes what gets cut.

It plans from the research outline and claims, so it wants `yt research` to
have run; without it, it plans from the idea alone. When `yt props` has
fetched models, HDRIs or textures, `props.json` is listed in the brief with
paths relative to the document, so the author places real assets rather
than primitives.

The author names the object the camera must never lose with an id starting
`hero-`. The frame gate then renders a proof frame at the midpoint of every
shot the world holds (plus the symbol's start, middle and end where no
midpoint is within a second), and `framecheck` judges each without eyes:
black is an error, nearly flat, clipped or too dark a warning; one short
draft encode per shot of a 3D symbol (the gate's, once a round on the
encode host; the author's `sr.py frame` renders stills alone) asks the
engine which objects it never
framed, a `hero-` object out of view is an error on that shot's frame, set
objects out of view are counted, and a set mostly out of view across the
frames is a camera pointing at nothing. The model then reviews the frames
against the publication bar and scores them. An error, or a score under
`--bar` (7), sends the findings back to the author for up to
`--rounds` rounds (2); every round and its score are kept in `vfx.json`.
A revision's reviewer sees the round before it and judges the revision
`better`, `same` or `worse`: only `better` replaces the round the section
stands on, and a revision judged `same` or `worse` ends the rounds.
A frame the host could not render is the host's failure, not the author's:
it is retried once on the other host, else filed as `infra:`, and a round
blocked only by such frames is gated again after a bounded wait with no
session and no revision paid (`rounds[].infra` in `vfx.json` says what the
host lost). A round the host loses twice does not pass: with no reviewed
round to stand on it ends on an `error:` naming the host and `yt vfx
--sections N` (`stopped: host` in the section's budget row), so the world
the gate never saw rendered stays out of the film and an earlier reviewed
version of the section is kept over it; a revision whose frames stay lost
ends the section on the round it stands on, `stopped: infra`. A frame both
hosts stop at the still deadline or kill for
memory is the author's error, naming the document's cost drivers: no host
holds that world as written.

Authoring is versioned. Each run lands in the next `vfx/vNN/`, `vfx.json`
names the active version, and `--fresh` replans and opens a new version;
nothing authored before is overwritten. A section `--sections` or
`--relook` carries forward keeps its review only while its inputs are the
ones reviewed (`inputs_sha256`: its shots and their timings, its effects,
the tokens, the approved look); otherwise it is carried unreviewed, its old
score kept under `inherited`, and `yt doctor` names `yt vfx --sections N`.

After authoring it audits **reach**: which constructs each symbol uses, set
against every element the engine has (by family: 3D stage, simulation,
motion, post, ...) and against the engine's capability manifest for what it
only approximates. The model then judges each effect `strongest` or
`upgrade`, naming the stronger construct, and scores the documents 0-10 for
how far they reach into what the engine offers under the chosen treatment.
The audit goes into `vfx.json` and `vfx.md`, the refinement plan takes the
upgrades as work, and `yt qa` reads it so a weak-motion finding can name the
construct left unused. The planner and the author both see the whole
vocabulary, and `examples.scene.xml` now proves a lit 3D set with a physical
camera and particles alongside the 2D constructs.

`--budget-usd` (18, what the default rounds cost at the most) is one section's
budget for its author sessions and its reviews together, and
`--stage-budget-usd` caps all sections (default: the section budget times the
sections). The reviewer is `--reviewer-model` (`opus`, never the author's own:
`--model opus` needs a reviewer of another family, say `--reviewer-model
sonnet`, or the stage is refused before anything is paid; `yt run` hands both
reviewer flags to keyframe and vfx) under `--review-budget-usd` (2) a review. The run prints its estimate before
any agent starts and its spend against both budgets after; a section that
cannot pay another session and review stops and keeps its best reviewed
round. An author is told its session's cap, and at 80% of it is asked for its
build and a still (as at 80% of its time) rather than being cut off with exit 1.

Each section writes its record (`vfx/vNN/rounds/<stem>/record.json`) as it
finishes, and a partial one after every gate, so a stopped or killed run keeps
every section that finished and `--recover` reads them back. `yt vfx
--finish-now --title X` writes `vfx/vNN/FINISH`, which the run reads between
rounds (as it reads `SIGUSR1`) to end every section on its best reviewed round
and run the film-wide stages.

A gate error that a later round of the same section fixed is a candidate
engine lesson (AUTO-111): the section's reviewer distils it (the SDK elements,
one sentence, the finding it answers), it is checked for unknown elements and
for any colour or style token, and a general one is appended to
`work/lessons-log.jsonl`. A score gain is never a lesson. `yt vfx --lessons`
lists the rows and `yt vfx --promote-lesson N` appends one to
`ytstudio/kit/engine_facts.json` with its own `verified_on`, which every
later kit's `ENGINE.md` prints; nothing else writes that file.

`--per-section` · `--jobs` · `--budget-usd` per section · `--stage-budget-usd` ·
`--reviewer-model` · `--review-budget-usd` · `--budget-hours` · `--width` ·
`--height` · `--rounds` · `--bar` · `--inspo` · `--fresh` · `--finish-now` ·
`--recover` · `--lessons` · `--promote-lesson`

### `yt sound`

Spots audio against the screenplay: where music enters and what it is doing
there, which effect lands on which word or frame, what the room sounds like
underneath, and where everything drops out. Produces a sonic palette with named
motifs, mix targets in real numbers (`narration_lufs`,
`bed_below_narration_db`, `effects_peak_dbtp` beside the prose; the fetch
levels every file against the first, with the prose figure as fallback),
the master's normalise mode with its reason (`normalize` in `sound.json`,
decided against the measured trade-off: integrated under-reaches a voice-led
film with designed silences, dynamic lifts the beds between sentences), and a
numbered cue sheet. `silence` is a cue kind, because scored silence is a
decision. Every cue carries `fade_in` and `fade_out` in seconds
beside its `entry` and `exit` prose, required for music and ambience
(`rule:fade` flags a bed with no fade out) and optional for the rest; `yt
build` sets the track's `fadeIn`/`fadeOut` from them.

A fresh spot then anchors every cue to the spoken word it lands on
(`sound.anchor_cues`, AUTO-140), on the timeline `yt build` places the
takes on: `anchor` is the shot (the cue's `shot` when it names one, else the
shot its `at` falls in), the take as `<file>@<sha256>` (`build.take_mark`;
"" with no recording), the index and text of the word whose start is nearest
`at` within 1 s (else -1 and "", the shot's start) and the `offset` in
seconds from it. The model is never asked for it and `at` stays its whole
second; `sound.md` and `cues.csv` list it beside the shot. A `--fetch` or
`--synth` re-entry keeps the sheet as it is, anchors or none, and `anchor`
is not in the listen-back's `heard` digest, so adding anchors leaves a
review current.

Coverage is computed, not asked for — merged music intervals, share of runtime
scored, longest unbroken run, unscored stretches — and `--review` hands those
numbers back for a listen-back pass that flags wall-to-wall music, cues that
fight the narration, effects with no sync point and sourcing that will not
survive a claim. What the timecodes say outright is not asked of the model:
`sound.rule_findings` flags two cues of one kind sounding at once
(`rule:overlap`), a cue with no level (`rule:level`), a cue past the film's
end (`rule:clock`) and a short effect with no bed under it (`rule:no-bed`,
the build's own transient classification), and each goes to the same revise
call before the first listen-back, read off the sheet again after every change
so a second rule on a cue the change already answered is not asked; a rule
flag the model keeps, or answers
with a change that still breaks the rule, stands in `unresolved` with its
`rule` and reason, and `yt build` records it as a `warning:` naming the cues.
Unless `--report-only` is given the review then revises: each
important or blocking flag gets one revise call that may edit only the cue's
design fields, its `fade_in` and `fade_out` among them (never `file`,
`licence`, `credit`, `gain_db` or `loop`),
only the revised cues are checked again, for at most two rounds, a flag that
cannot be fixed is kept with its reason, and coverage is recomputed when cues
changed. `--report-only` reports the findings and changes nothing.

`--review` · `--report-only` · `--free-only` (CC0, public domain, CC-BY or self-recorded only) · `--library
"epidemic sound"` · `--no-music` · `--mood` · `--inspo` · `--minutes` when there
is no screenplay yet (floored at 10 seconds) · `--jobs` cues fetched at once with
`--fetch` (default 4)

Every cue except silence also carries a `query`: the few plain words a
library search would want, naming the sound rather than its purpose.
`--fetch` turns the sheet into files: each cue is searched on Freesound
(`ytstudio/freesound.py`) -- `sound.cue_queries` tries the cue's
`search_description` first (whole when short, then shortened), then its
`query`, its search terms and the layers it is built from, and never the
name, which labels the cue sheet and not a sound -- under CC0 and CC-BY only, every hit judged again by the
pipeline's one licence policy; the best fit by duration and rating is
downloaded as the high-quality mp3 preview into `sound/` and written back
into the cue as `file`, `licence`, `credit`, `source_url` and `duration`,
which is what `yt build` places. A preview is fine for a scratch mix and for
placing against picture, and the manifest says so. `sound/index.json` maps
each Freesound id to its file, so a re-spotted sheet that lands on the same
sound downloads nothing; a cue nothing matched keeps no file and is listed
under `unfilled[]` with the queries tried. Cues are searched, downloaded and
judged `--jobs` at a time (default 4; none of it runs on this machine), the
index written under a lock and the verdicts applied in cue order, so the
progress lines and the record read as a serial run. With a `sound.json` already in the
project `--fetch` reuses it and asks the model nothing; `--fresh` spots again
first. The keys are `FREESOUND_CLIENT_ID` and `FREESOUND_CLIENT_SECRET`
(the secret is the token the API wants); without one nothing is fetched and
only the name is reported. `yt run` always passes `--fetch --synth`, so a cue
Freesound cannot fill is synthesised rather than built as silence. Each fetched file is
measured on a render host (one ssh per batch; ffmpeg's ebur128: integrated
loudness, true peak, and the noise floor, the level of the quietest tenth of
400 ms windows) and the cue gets the `gain_db` that brings it to its design
level: by loudness for music and ambience, by peak for every other kind. A source
the track's gain cap cannot reach, or whose noise floor the lift would raise
within 12 dB of the narration level the sheet states (else the programme target),
is refused with the measured floor and gain, and the next-ranked hit of the same
search is tried (three in all) before the cue is left unfilled. With no render
host, or `YT_NO_REMOTE` set, nothing is measured: the summary and the stage's
output say `NOT LEVELLED`, and a later `--fetch` measures the kept files.
A music or stinger file also brings back, in the same ssh, an RMS envelope
of its first minute at a 10 ms hop (mono, resampled, ffmpeg's `astats`,
the chain TO VERIFY on the host); a failure there costs the file nothing
of its level checks. `sound.beat_grid` works the tempo, the first beat and
a 4/4 downbeat out of it in stdlib Python (onset strength, autocorrelation
over `BEAT_BPM` 60-180, a comb for the phase, a line through the onset
peaks; `BEAT_CONFIDENCE` 0.3; all TO VERIFY on real previews) and the cue
keeps `beat_grid` (`bpm`, `period`, `first_beat`, `downbeat_first`,
`confidence`), or none for a bed with no steady beat. A refetch clears it,
and it is not in `heard`. `--synth` measures its music the same way.
A new candidate that passes those checks is then judged against its cue by the model (the library's name, tags, description and length, the measured levels and spectral centroid and flatness, and a spectrogram made on the same host): a reject tries the next-ranked hit, the cue keeps the `judgement` with its evidence, and a cue every candidate failed is left unfilled with the reasons.

### `yt build`

Compiles the project into one scene-render document, built with the
`sr_core.scene` SDK and checked three ways (the SDK, the written file read
back, and the guards below). The timeline comes from the measured narration
when `yt voice` has run and from the screenplay's estimate otherwise, and the
manifest says which, per shot.

Each shot is drawn by the first of these that applies:

1. **An authored world** from the document `yt vfx` wrote for its section
   under `vfx/vNN/`, included by shot label. Consecutive shots the same
   symbol draws share **one continuous `<include>`**, so the symbol's clock
   runs across them and a camera move is not restarted at every cut; a gap
   the editor extends over does not break the run, and the same symbol used
   again later is a separate include. `build.json` lists the merges under
   `editorial.merged`.
2. **A licensed picture** the research downloaded, carrying its `license` and
   `credit` into the document: an image, or a clip placed as a silent
   `<video>` (`hasAudio="false"`, AUTO-143). A clip's length is read only
   from an `.mp4`, `.m4v` or `.mov` file's own `mvhd` header
   (`build.clip_seconds`, no ffprobe); a clip known to be shorter than the
   shot is not placed (the editor's brief says why, under `notes`), and a
   clip in another container (`.webm`, `.ogv`) or whose header does not read
   is placed over the shot's length with a `warning:`. A row served with a
   generic type (`application/octet-stream`, as the Internet Archive serves
   clips) and no dossier kind takes its kind from its suffix. Only pictures
   draw a shot: a downloaded model, sound or document never does.
3. **What the editor decides.** Every shot neither of those covers goes to the
   model with the shots around it, the images and the footage available and
   the design system, and it answers under a schema: `extend_previous` or
   `extend_next` when a neighbouring effect genuinely carries the shot (the
   include is stretched over the gap), `image` with an exact path, `footage`
   with the exact `clip` path of a licensed clip showing the real event or
   place (refused, with a block, when the path is not a downloaded clip or
   the clip is shorter than the shot), or `block` with the reason the
   animation stage needs. The same decision names the shots whose
   caption would fight the picture and the hold after the last word.
4. **An honest placeholder** for what was blocked, counted and named. A
   placeholder is disqualifying, so the manifest carries `publishable: false`
   while any remain, and so does anything the editor listed under `blocking`.
   The card draws in the design's `ink` role at its `label` size, with no
   literal colour.

A malformed number from the editor (`tail_seconds`) or the sound designer (a
cue's `fade_in`, `fade_out` or `gain_db`, or a `level` written in words only)
is replaced by its default (`tail_seconds` 0.5, the others 0) and recorded as
a `warning:` in `build.json`'s `findings`, naming the field, the cue and the
value used. A cue whose file runs longer than its window and has no
`fade_out` is cut hard mid-sound at `clipOut`, and is a `warning:` naming the
cue; so is each rule finding the sound design's listen-back kept.

An `<include>` carries neither the included document's styles, paints nor
lights (the engine warns, E12), so the builder lifts them: every `<light>`
becomes a scene light named after its include and gated by an intensity
animation to the include's span, the paints are carried into the scene, and
the files' `<token>`s become the scene's `<styles>` — the authored values
first, because the symbols were composed against them, the design's role
tokens filling what no file declares. The include itself points not at the
authored file but at a cleaned copy beside it,
`vfx/vNN/<section>.include.scene.xml`, with every `var(--name)` inlined to
the file's resolved value and the three sections dropped, so the engine
raises no E12 warning. The authored document is read, never written.
`build.json` records what was hoisted, which copy was cut from which file,
and the editor's decisions under `editorial`. A hoisted light keeps its own
`<animate>` verbatim (its `defaultInterpolation` and every key attribute),
re-timed by the include's start; the gate keys that hold it at 0 outside the
include's span are explicit holds, so an authored ramp stays a ramp and no
light fades into the neighbouring section. A ramp that crosses the include's
end is clipped at its last frame, not stepped. A light with several intensity
tracks gets one gate per track, stacked as the engine stacks them, and a
key's `marker` is read from the document's own `<markers>`. What the gate
cannot carry as written is converted and named in the findings: an
`additive` track with no replacing track beside it is folded onto the
light's intensity, an `extrapolateBefore`/`After` other than hold becomes the
gate's hold, a marker the document does not define (a beat grid's `beat.N`)
leaves the key at its offset, and a `normalized` time base is read as
seconds. A `downloads/` image an
included document embeds is credited from `raw/08-downloads.json` (a file the
manifest does not list is a build error).

Every shot and section also becomes a marker, because a marker survives
re-timing and a timecode does not. Supers are checked for reading time and
captions are split on word boundaries. The build's caption track places the
same `design.caption_cues` as the sidecar, from the `voice.json` takes (a
spoken shot with no take is split from its own timing), so burned-in and
sidecar captions are the same cues.

The mix is the sound design's. Every cue of `sound.json` that has a file
(`yt sound --fetch`) becomes an `<audio>` asset and an `<audioTrack>` on the
bus of its role — `music`, `effects` (sfx, stingers, transitions),
`ambience` — at the cue's time with its fades, a looping cue repeated to
fill its window; the narration takes keep their own `narration` bus, and
music, effects and ambience all duck under it when there is one, and the master
is normalised once (`integrated`); `dynamic` only with a stated reason. The level is staged
across bus and track inside the schema's own facets, because gain saturates
at +24 dB per stage; a cue that still cannot reach its level is a warning
that says where it sits. A `silence` cue is a window on the music and effects
buses' volume, a held 1→0→1, not a muted track and never the master: the voice
and the room carry on through it. The master carries the loudness target,
true peak and limiter. A cue with no file is listed under `audio_missing`
in `build.json`, not invented, and a sound design of which nothing could be
placed sets `publishable: false` — a project with no sound design at all is
not blocked. `build.json` also counts `audio_tracks`, `narration_tracks`,
`buses` and `silence_windows`.

A cue with an `anchor` (AUTO-140) is placed on its word, not its second:
`build.anchor_time` finds the anchored shot's take on the current timeline,
adds the word's start and the offset, and calls the cue `kept`, `moved`
(the take was redone, by file or sha256, or the shot starts elsewhere) or
`lost`. A word no longer at its index is looked for by its text (case and
punctuation folded) within `build.ANCHOR_SEARCH_WORDS` (8, TO VERIFY) of it,
and only a single match is used: a word said twice there, a missing word or
a missing shot is `lost`, placed at the sheet's `at`, and a `warning:`
names the cue, its anchor and the time used. The placed times are checked
again with `sound.rule_findings` against the built runtime, and an overlap
or a cue past the end that only re-anchoring made is a `warning:`.
`build.json`'s `cue_anchors` lists each anchored cue (`cue`, `sheet_at`,
`placed_at`, `anchor`, `status`) and the summary names the moved and the
lost. A sheet with no anchors places as before, has no `cue_anchors`, and the
build prints one line saying cues will not follow a redone voice. `yt qa`'s
scorecard reviewer and `--listen`'s listener are given each cue's
`placed_at` in place of its `at` when build.json has it.

`build.json`'s `beat_sync` measures how the cuts sit on the music, and
never moves one: cuts stay on the narration. For every placed music or
stinger cue the mix reports where it starts, each play's length, its loops
and its `clipOut`, and `build.beat_sync` lays the cue's `beat_grid` over
each play. Each join (the incoming shot's `at`) under measured music gets
its signed offset to the nearest beat and downbeat; the record is
`cuts_in_music`, `on_beat_share` (within `BEAT_TOLERANCE_S`, 83 ms, TO
VERIFY), `median_abs_offset_ms`, `downbeat_share`, each reveal or payoff
shot's offset to the nearest downbeat (`reveals`), the music cues with no
grid (`unmeasured`, their cuts not scored) and `cuts_outside_music`. A film
with no music has none. `yt qa` hands `beat_sync` and `cue_anchors` to the
scorecard reviewer only when build.json has them, and `--listen` adds one
brief, the picture's timeline as built (cuts, reveals with their lines, each
anchored cue's placed time and word, the beat summary), so the listener can
say by ear whether a hit lands on its word or cut; it still hears without
seeing, and `--watch` still sends no sound. A film rebuilt with these keys
asks its scorecard once more (the prompt changed); one not rebuilt keeps its
cached decision.

Short effects are staged before the gain is split. The limiter runs after
normalisation, so a tap or stinger far above the bed is limited after the
lift and the programme loses loudness it never gets back. So, when the
master normalises, every sfx, stinger or transition cue of 2 s or less is a
transient, and the transients come down **as a group** — keeping the
sheet's balance between them — until the loudest sits
`transient_headroom` above the loudest bed (anything sustained that is not
narration). The headroom is derived from the master's own targets: the
true-peak ceiling minus the loudness target, minus the bed's own crest.
The crest is this project's own measurement: the first build starts from a
generic 6 dB, and once a render exists the build raises it by the shortfall
that render measured against the target (`measured_crest`; within 0.5 LU is
on target). `build.json` records it under `audio_staging` (`bed_db`,
`headroom_db`, `shift_db`, `crest_db`, `crest_from`, the cues `shifted`) and a note says by how much;
transients with no bed under them are a warning, because the target will be
missed. Nothing changes the loudness target itself.

**Fonts.** Every text node (`text`, `span`, `textStyle`, and `object3D`
text) is pinned to a licensed file the repo ships under `ytstudio/fonts`
(Inter, Inter Display, Lora, Source Sans 3, STIX Two Text, Liberation
Sans/Serif/Mono, DejaVu Sans/Serif/Sans Mono; licences in `ytstudio/fonts/LICENSES`). The build
resolves family, weight and style (`fonts.py`), copies the file to the
project's `fonts/` so the render sync ships it, adds the `<font>` asset
(with its sha256 and licence) and points the node at it; the cleaned include
copies get `fontFile`. A family with no file is an `error:` finding and
blocks the build; the explicit alias table (Helvetica, Arial, `serif`,
`monospace`…) and nearest-weight choices are listed under `fonts.notes` in
`build.json`. A node naming no family gets DejaVu Sans.

Captions are a sidecar track by default (the platform takes it and the viewer
turns it on; `yt gate` checks `narration.vtt` either way), because burned in
they share the lower band with the authored type; `--burn-captions` burns them
into the picture. The master's normalise mode is the sound designer's
decision: `sound.json`'s `normalize` (`mode`, `reason`) is read, and
`integrated` is the fallback when no stage decided; `--normalize` is the
operator's override. `dynamic` is honoured only with a reason (the flag's
`--normalize-reason`, or the design's), else it falls back to integrated with
a finding, and `none` leaves the master unnormalised. `build.json` records the
mode and `normalize_from` (`sound.json`, `the --normalize flag` or
`default`). Every bus but the narration ducks under it.

`--width` · `--height` · `--fps` · `--codec` (checked against the schema's own
list) · `--bitrate` and friends (never `crf`) · `--loudness` · `--true-peak`
· `--normalize` · `--normalize-reason` · `--no-limiter` · `--burn-captions` ·
`--no-captions` · `--no-audio` · `--fresh` · `--force`

### `yt render`

Finds the engine through `RS_RENDER_BIN` (the binary, or a directory holding
it), on PATH, in a checkout beside this one (`cargo build --release`), or in a
release archive from GitHub Releases unpacked beside this one
(`scene-render-vX-linux-x86_64/scene-render`, the newest of several), then
runs it and keeps the report. The engine renders on a software adapter
(Mesa's llvmpipe or lavapipe) where there is no GPU, many times slower but
with the same output. `--quality draft|preview|final`,
`--start`/`--end` for a range, `--still TIME` or `--frames A..B` for pictures,
`--output ID` for one declared output, `--validate` for the engine's own
validation, and `--strict` to fail on any finding.

**[docs/production.md](docs/production.md) is the production brief**: what a
finished piece must be, which file feeds which decision, the checks, and the
loop that runs until it is done. Read it before composing anything.
What a good factual story does is in [docs/narrative/](docs/narrative/README.md):
numbered craft rules with their sources, the check each one allows, the
stages it bears on and how strong its evidence is.

Heavy work does not run on this machine: see
[docs/compute.md](docs/compute.md) for the two render hosts, which job goes
where (the rule is memory, not GPU), the traps, and how a job's directory on
the host is scoped and cleaned.

On a host the project lives once, in a persistent mirror
`/tmp/yt-render/<slug>/mirror/`, refreshed by an incremental rsync before
every job (what no render needs stays behind: caches, `raw/`, `qa/`,
`decisions/`, rendered outputs, the proof frames and `build/proof/`;
`props/` and `sound/` travel because the scene references them). Every job
then gets a directory of its own, `/tmp/yt-render/<slug>/<job>-<pid>-<suffix>/`,
hardlinked from the mirror in a second (`cp -al`, or `rsync --link-dest`
where cp cannot), with the parent directories of its `-o`/`--report` outputs
created first because the mirror carries no output trees; the engine runs
there, the results (video, stills, WAV, the reports) are pulled back and the
job directory is removed. The mirror is never removed by a job;
`--prune-remote` deletes it on every host and stops, and the next job
uploads it again in full. Two jobs on one project never share a directory,
so a `pilot --render`, a still and a full encode of the same film can run
at once without overwriting each other. `--keep-remote` leaves a job's
directory on the host for debugging; `--local` renders here even when hosts
are configured. Only the film's own encode writes `render.json`; a still, a
validation, an audio pass or a named output (a pilot) writes
`render-<job>.json`, so the report the gate reads for `video.mp4` is never
overwritten. A report written on the host has its paths rewritten to the
project here before it is kept. `render.run_job(cmd, project_root, host,
job=...)` is the public hook other stages use to run one engine command the
same way.

A still is cached on a fingerprint of the scene bytes, every file shipped with
it, the document path, the time, the quality, the options and the engine's
identity, under `$YT_STILL_CACHE` or the project's `.cache/stills`; a hit is
served locally before any host is chosen, only rendered PNGs are stored, and
an unknown engine identity caches nothing. Every render outcome leaves a job
report in the engine's `scene-render-cli/1` envelope with a `status`:
`rendered`, `failed`, `timeout` or `blocked-inputs` (the files the scene names
that are missing are listed), with its seconds and the engine's version and
sha256; a detached job that never reported is marked `outcome_unknown`. `yt
doctor` lists the jobs that did not render.

`--audio-only` encodes just the mix to WAV. It is the cheapest way to find out
whether the audio graph fits in memory, because the mix is built whole and
costs tracks times duration — a long programme with a track per cue has run a
machine out of RAM before it ever reached the GPU.

### `yt retime`

Writes the measured narration durations back into the screenplay, because the
script's shot lengths are a words-per-minute budget and only the recording
knows the truth — but only where the editor says so. Every shot whose read
drifted goes to the model with its words, its super and its visual, and the
answer per shot is `accept` (the screenplay takes the measured length),
`rerecord` (the read is wrong for the house pacing; `yt voice --redo`),
`rewrite` (the line is wrong for the shot; `yt script --revise retime` sends
exactly those lines back, with the read each measured) or
`keep_planned`. `retime.json` keeps the decisions; `--dry-run` shows them and
writes nothing; applying keeps `script.json.bak`.

It refuses to be quiet when a take's recorded text no longer matches the
screenplay — retiming a rewritten line against an old recording anchors words
nobody spoke.

### `yt pilot`

Cuts the finished film shorter at full fidelity, so an operator can approve it
or send it back before paying for the whole render. Each run of adjacent shots
becomes a `<segment>` of a new output, so the engine rebuilds the audio in
output time and remaps the captions with it, and a visible transition marks
each join so a jump does not read as a continuity error.

The cut is the editor's. The model reads every shot — its narration, its
visual, what it proves (treatment, effect, cue, super, hook, payoff) — and
chooses the shots of each budget and the join between runs. A
coverage-maximising sweep in Python is handed to it as a proposal, not a
constraint, and is the validator afterwards: whole shots only, within budget,
and each cut inside the next larger one so the pilots are one edit growing. An
invalid selection is asked again once; if it still does not hold, the coverage
cut is used and `pilot.json` says so (`selection: coverage`). **What a cut
could not fit is written down** either way, because that is the list an
operator must not approve blind.

`--seconds 30,60,180` gives nested cuts, `--render` hands them straight to the
engine, `--fresh` asks the editor again.

### `yt gate`

Arranges the arguments for scene-render's `production_gate.py`, because every
one of them is something an earlier stage already produced: the scene from
`yt build`, the video and encode report from `yt render`, the captions and
`voice_end` from `yt voice`. It checks loudness and true peak on the decoded
file, the scopes (legal range, gamut, chroma), black and frozen frames, and the
caption timeline and line lengths.

The tool measures; the model triages. Its report, with the build's findings
and the narration's drift, goes to the model, which sorts every item into
blocking or acceptable, names the stage that owns each fix and the `yt`
command that runs it, and `gate.json` keeps the verdict. A blocking item fails
the gate even when the tool passed; the tool's own failure is never overruled.
`gate.json` also keeps the sha256 of the video and the scene it judged
(`video_sha256`, `scene_sha256`), and `yt doctor` compares those with the
files on disk instead of their times: a touched file leaves the gate
current, a replaced one makes it stale. A gate.json without them is judged
by time, as before.

The gate needs the engine and ffprobe, and this workstation has neither by
policy, so like a render it runs on a host: `--host USER@HOST`, or by
default the host an encode would go to when `RS_RENDER_HOSTS` is set and the
tools are not here. The candidate, its envelope and the two tools
directories are synced into `/tmp/yt-gate/<slug>/gate-<tag>/`, every path
is rewritten for the copy, the gate runs with the host's engine and ffprobe,
the output comes back and the directory is removed (`--keep-remote` keeps
it). `--local` runs it here.

The gate's three audio checks live in `ytstudio/audiotools/`: `sr-margin`
(the narration's margin over the rest of the mix, per line and per worst 3 s,
floor 11 dB), `sr-audiocheck` (true peak, digital silence, DC offset, loudness
range inside the narration, a muted channel of a stereo pair beside a live one,
and stereo whose phase correlation averages under 0 outside digital silence or
sits at or under -0.5 for 1 s, which cancels in mono) and `sr-sibilance`
(5-10 kHz bursts per take).
Python standard library and ffmpeg only, so they run on a render host; they ship
into the job's `tools/audio` whenever the tools directory lacks them. The gate's
tail check is yt's own `tail_check.py` in the same directory (pnkit's rules 2-4
ported without numpy; an ending's effects are judged by measuring each one: a
short attack that decays), always shipped and passed as `--tail-check`. `yt doctor` and `yt gate --preflight` name each one that is missing.
Only the true-peak ceiling and the 11 dB floor are standard figures; the other
limits are named working conventions in `sr_audio.py`.

`production_gate.py` fails closed on the first requirement that does not
hold, so `gate.json` carries `stopped_by`: the requirement (identification,
captions, loudness, true peak, scopes, a tool that failed), the message, the
owning stage and the command that fixes it. The summary prints the same
line.

`--preflight` lists what the gate needs and runs nothing. `--frames` runs only
the decoded-frame check. A technical pass is not a perceptual one — that is
`yt qa`.

### `yt review`

Writes the PDF review sheet with `sr-review`: a frame, the captions and markers
of each section, the open to-dos, and the sources and credits. Everything it
wants is already in the document — which is why `yt build` carries the licence
and credit line into each image asset. `--engine rs` renders a real frame per
section; without one it lays out what the document says.

The sheet is then read, not just written: the model opens the PDF and the
frames and critiques each section against the screenplay — what a reviewer
would circle, with the owning stage and the fix — into `review.json`.

### `yt qa`

The review-and-improve harness of [docs/production.md](docs/production.md),
run over the rendered film. Sampling is deterministic and kept
(`samples.json`): every shot once, at the midpoint of the shot, never at a
boundary. Each row's `why` keeps the reasons that give a shot priority: the
hook, the ending, the payoff, every section's opening, the re-hooks, 25/50/75 %
of the runtime and the longest authored effects; a shot with none of these
says `shot`. Two shots that share a midpoint get a still each, the second
a frame away. `--frames N` sets how many stills; without it, every shot up
to 60 (one inspection reads every still, and each is a render job), past
which a film is sampled as `--frames 60` would. Above the shot count the
extra stills (`why: evenly spaced`) are spread evenly over the runtime: each
is the moment nearest an even step that is inside a shot, half a burst from
its edges and at least half a second from every other sample, so a larger N
gives more evidence; a film too short for N gets every moment that fits.
Below the shot count the hook, the ending, the payoff and the section
starts are kept first, then the other shots spread over the runtime; the
reviewer is told which shots it has no still of before it scores, and the
scorecard names them (`unsampled`, and a line under `not_evidenced`).
A midpoint misses what happens elsewhere in its shot (AUTO-104): each
narrated line, one sentence of a take in `voice.json` placed on the film by
`build.json`'s shot `at`, also gets a still where its first word is said,
half a burst inside its shot, labelled `line: <first words>`; the line,
when it starts and `frame`, the file that shows that moment, sit beside
that still in the brief (`lines`), so whether it is on screen when it is
said is judged at that moment. A line merges into a still of its own shot
only when that still's burst has the frame where the line starts. The line
stills come after every shot's own: the payoff, re-hook and reveal lines
(`qa.KEY_LINES`) first, then the lines said inside a shot before the ones
that open their take, spread over the runtime; without `--frames` they are
added up to 60 stills in all, and under `--frames N` they count against N
before the evenly spaced ones. The lines left without a still are named to
the reviewer and in the scorecard (`lines_unsampled`, and a `lines:` line
under `not_evidenced`). A wordless film has none.
The stills are rendered by the engine on the render
host from the very document the encode uses. A still cannot evidence motion,
so each sample is rendered as one contiguous window of frames centred on
its midpoint — half a second, at most 16 consecutive frames, kept inside
the shot when the shot is shorter so no step is a real cut — in a single
`--frames A..B` job; a pass of N samples costs N jobs, and the midpoint
still is copied out of the window's middle frame (rendered on its own only
when the window did not come back). The window lands under
`frames/burst/`, planned in `samples.json`. The tooling measures the mean
absolute difference between consecutive frames (`steps`) and between the
first and last frame (`total`): `frozen` needs both every step and the
whole window under the floor, so a slow drift is still motion; one step
far above the others' median is a `jump`; anything else is `moving`. The
stills are tiled with their timestamps into `contact-sheet.png`.

Before the model scores anything, the mechanical checks file their own
findings, each with a timestamp, an owner (`vfx` for an authored shot,
`build` otherwise) and a fix: a nearly flat still, clipped highlights, a
frozen burst, a jump inside a shot, and the objects `render.json` says the
camera never framed, per shot, with their ids. A mechanical finding is
blocking when it sits on the hook or the ending, or when the same kind is on
more than one sampled shot. Two more are advisory, always minor and capping
nothing until their thresholds are measured (AUTO-145). The first shot of
each diagram effect in vfx.json (2d-diagram or data-animation, 3 s or
more, not extended from another shot, even when it runs on in its section's
world) gets a still just after its cut and one just before its end, under
`frames/build/` and shown to no model; its ink (the share of the frame that is edges) at those and
at its line stills is a series, and a series that never grows by 15 % of its
last reading is `no-build`: the diagram was on screen whole from the cut and
talked over. And the words on screen at each still are counted from the
document QA renders, never read off the frame: a node over six words, or
twelve at once (a formula counts four), is `text-dense`; a node named
`quote-`, `headline-` or `credit-` is timed against its reading time
instead. Then the model reads the frames and the sheet —
with the shot each should show, the editorial decisions, the numbers and the
mechanical findings beside each — and files the ten-category scorecard under
a schema, every finding with a timestamp, what is visible, why it lowers
quality, the root cause, the owning stage and the fix. What stills cannot
evidence (the voice as heard, the mix, the music) is scored only from a
listening review that heard what the film carries: `sound.json`'s
listen-back (`yt sound --review`, which reads the cue sheet cue by cue, not
the rendered track) for `sound_design` and `music_score`, and `voice.json`'s
take review for `voiceover`. Each review records `heard`, a digest of
what it heard (`sound.heard`: each cue's design, the palette's mix targets
and the master's `normalize` mode; `voice.heard`: the takes), and QA
computes it again: a review whose digest no longer matches its file (a mix
ride, a re-spotted sheet, a new mix target or normalise mode, a re-recorded
take), one written before the mark, one that never answered, or one whose
file is newer than `build.json` is stale. The listen-back never reads the
recorded narration, so a voice re-record under an unchanged sheet leaves it
current; the take review is what hears the takes. For a stale review the
prompt says so and leaves out what it said, and the scorecard's
`listening` lists every row with why. A category no current
review covers is recorded not evidenced with no score (`null` in
`scores`), named under `not_evidenced` and `audio_not_evidenced` (also in
`qa.json`) with the command that produces the evidence (`yt sound --fresh
--review --fetch --synth` or `yt voice`, then `yt build`, `yt render` and
`yt qa`, every step with the film's `--project`), and the bar says READY
cannot be judged for it instead of NOT READY on a frozen number. The
overall is then the mean of the categories that were scored, whatever the
reviewer filed (kept as `overall_filed`). Two passes that did not score the
same categories compare their overalls over the ones both scored, so a pass
that only lost its low audio scores is no gain for `yt improve`'s keep or
revert. A wordless film has no take review, so in the default pipeline its
`voiceover` stays not evidenced and READY is out of reach for it; the bar
and `improve.md` say so. Only the opt-in `--listen` below hears the
rendered track.

Every listening row carries its `basis`: `read` for the listen-back and the
take review, which read the cue sheet and the takes, and `heard` for the
`--listen` row. `heard` is still the sha256 digest of what the review
covered, never a flag. The scorecard and `qa.json` record `audio_basis`, the
basis of each scored audio category: `heard` when a current row covering it
heard the rendered track, else `read`. An unscored category has none, and
neither do a silent film's sound and music. Each `read` category adds one
line under `not_evidenced`, for example "voiceover: scored from the take
review (voice.json: words, lengths, pace), not from the rendered track; run
yt qa --listen". The scores, the bar, the overall and
`audio_not_evidenced` are left as they were, and nothing caps a read
voiceover. The rows go to the reviewer without `basis`, so the scorecard
prompt is unchanged and a cached pass is not asked again.

A narration read by a scratch engine (stub or espeak,
`doctor.SCRATCH_ENGINES`) proves the timeline and is never published. When
voice.json's engine or any take's is one, QA adds a mechanical blocker
naming the engine and how many takes it read: "voice: narration is an
espeak scratch take (12 of 40 takes); run `yt voice --engine auto`" (with
the film's `--project`). The film is then NOT READY whatever it scores. A
wordless film has no narration to block on. The blocker counts in the
verdict and the scorecard's `blocking` but is not briefed to the reviewer:
briefed, it would change a stub or espeak film's scorecard prompt and ask
its cached pass again. `yt doctor` warns on the same takes by the same rule
(`doctor.scratch_reads`), so a kokoro voice.json that kept stub takes is a
doctor warning too, and `yt improve` has it to route.

`--listen` (AUTO-126, `ytstudio/listen.py`) is the review that hears the
film itself. It is off by default, and `YT_GATEWAY` never turns it on.
Before a frame renders or a pass directory is made, it checks every piece
and refuses with exit 1, naming each one that is missing: `YT_GATEWAY=vercel`
and the gateway engine's key, Node and `ai`; a `--listen-model` in
`models.LISTEN_MODELS` with a provider verified to take audio (the mimo
models have none yet, so they are refused); a listener from another maker
than every model that wrote what is heard (script.json's and sound.json's
`engine` and `usage` models and answering models, and voice.json's engine
when it names a model, judged by `flags.own_reviewer`); `video.mp4` present
and not older than `scene.xml`; an audio track in build.json; `ffmpeg` and
`ffprobe` on PATH; with `--reuse PASS`, a pass whose scorecard records this
scene's digest; the track under `listen.LISTEN_MAX_MB` (14); a price row for
the model; and an estimate within `--listen-budget-usd` (0.50). The estimate
is the track at `LISTEN_TOKENS_PER_SECOND` (32) plus the prompt, and
`LISTEN_OUTPUT_TOKENS` out, doubled for the one retry; for a narrated film
it adds the transcript below the same way, the same track in and
`TRANSCRIPT_OUTPUT_TOKENS` (8000) out, doubled. The track is the
film's first audio stream by stream copy (`ffmpeg -vn -map 0:a:0 -c copy -f
adts`) into `qa/listen/<film sha256[:16]>.aac`. It is reused while the film
is the same, and its length is ffprobe's stream duration of `video.mp4`. One
gateway decision, `listen-<audio sha256[:16]>` (`google/gemini-3.6-flash`
allows Google as its only provider), hears it under a role that lists
docs/production.md §2 rows 6-8 word for word. It is given the narration and
the cue sheet as the writer's declaration, not evidence. It answers, per
category, what it heard and its findings (timestamp in seconds, problem,
why, root cause, owner of voice, sound, build, render or external, fix,
blocking or minor), plus what it could not hear (`not_heard`), with no
score. The same mix asks nothing again. A finding whose owner or severity
is outside the schema, or whose timestamp is outside the measured duration,
is named in one more decision (`-again`), which files only those findings,
corrected. The first answer's kept findings stand, and the second adds what
the first did not say. What still fails is dropped and named under
`not_evidenced`, never repaired. The record is `qa/pass-NN/listening.json`:
the model asked and the one that answered (on a rerun served from the cache,
the one the record that asked names), the provider, the audio and film
digests, the decisions, the answer and the second one (`again`), the kept
and dropped findings and the listener's report. It is current only while
`video.mp4` is the film heard and not older than `scene.xml`.

A current listen is one more row of the listening evidence, covering all
three audio categories. The reviewer then runs under `qa.ROLE_LISTENED`,
with a `Listening review:` brief after the measurements, and the listen's
findings join the scorecard's with `source: "listen"` and `frame:
listening.json`, so they block, route and print like any other. The
scorecard's `listen` holds the listener's report, and `usage` (and
qa.json's) is the reviewer's and the listener's merged
(`spend.merge_records`). A listen that fails (a 402, a different model
answering, a missing field) is recorded not current, with its reason, its
billed `failed` row and one `external` finding, and the frame review runs
under today's role. `yt improve` and `yt run` do not pass `--listen`, so
the next pass's estimate leaves a past listen out of a plain `yt qa`
(`qa.pass_usd`) and adds the listen's own estimate each time `--listen` is
asked, whether the last one was paid, served from the cache or failed.

A listen that answered on a narrated film also checks the words (AUTO-152),
unless a render landed during the listen: then nothing more is asked, and a
render that lands during the transcript leaves both not current. The
transcript check is one more decision on the same harness, model and
track, `transcript-<audio sha256[:16]>` (`listen.TRANSCRIPT_SCHEMA`: per shot
its `label`, `start`, `end` and `words`). It is cached, budgeted and billed
like the listen. Its prompt gives each voiced shot's label, `at` and
`seconds` from build.json and never the words the shot should say. A voiced
shot is one whose placed `narration` is words, not a bracketed direction.
`listen.transcript_check` keeps a shot that is answered, with `0 <= start <
end` and `end` at most the track's length plus `TRANSCRIPT_TIME_SLACK_S`
(1.0), and a start within that slack of the shot's place. The shots that
fail are asked once more, alone (`-again`). Those that still fail are named
under `not_evidenced`, and a call that fails leaves them all there. Each
kept shot's words are held to its narration as placed, never to the voice's
`spoken` text or `narration.txt`. build.json keeps 300 characters of a
shot's narration, so the text compared is the whole take text from
voice.json (the caption text), else the screenplay shot's, whenever it
begins with what build.json placed; otherwise the placed text stands. Both
sides are normalised first (`listen.normalise_words`: lower case,
punctuation gone but for an apostrophe or hyphen inside a word, numbers
spelled out, so "1.5%" is "one point five percent", "$5" "five dollars" and
"the 1990s" a plural). `listen.compare_words` then splits each hyphenated
word into its parts on both sides, so "twenty-one" and "twenty one" are the
same words, and holds each year written (four digits from 1100 to 2099, no
comma or decimals) to whichever reading is nearer what was heard: "one
thousand eight hundred fifty-nine" or "eighteen fifty-nine" ("nineteen oh
five", "nineteen hundred", "twenty twenty-six"). Units ("5km") are compared
as written. The screenplay's pronouncer terms are dropped from both sides
and counted. `listen.wer` is the word error rate: replaced (the longer
run), deleted and inserted words over the words expected.

A shot over `INTELLIGIBILITY_WER` (0.15, provisional, with no standard
behind it) is a minor `voiceover` finding owned by voice at the shot's
time, joining the scorecard with the listen's: "shot 1.2: heard 'And its
sail is in front.' for 'And its tail is in front.' (WER 0.17)". The record
is `transcript` in `listening.json` (`status`, `reason`, `model`,
`served_model` and `provider` for the transcript call itself, `shots`
with `label`, `at`, `expected`, `heard`, `wer` and `pronounced`, `flagged`,
`not_evidenced`), and the scorecard's `listen.transcript` is its tally. A
wordless film, or one with no voiced shot, is recorded `not_evidenced` with
its reason and asks nothing. Without `--listen`, or with the gateway off,
there is no listen and so no transcript. The model's transcript fidelity on a
real mix with music under it, the call's tokens, whether 8000 output tokens
hold a ten-minute film, whether the start times land within the slack, and
the 0.15 bar against a kokoro and an ElevenLabs film are TO VERIFY.

`--watch` (AUTO-127, `ytstudio/watch.py`) is the review that watches the
cut in motion, for what falls between the stills: a frame that pops, a
transition that stutters, a super that lands late. It is off by default,
and `YT_GATEWAY` never turns it on. Its prerequisites are Node 22 or later
with `gateway/node_modules` installed (the npm step in the requirements
above), `YT_GATEWAY=vercel` and `AI_GATEWAY_API_KEY`. Before a pass directory is made it checks, and
refuses with exit 1 naming what is missing: the gateway engine's readiness;
the catalogue (`GET /v1/models`, read once a run, 20 s); a `--watch-model`
(default `google/gemini-3.6-flash`) whose catalogue row is a language model
tagged `video-input` and `structured-output`, not deprecated, not `vmc/`
and not `-batch`; a watcher whose maker (`models.maker`) is known and
differs from that of every model script.json, keyframe.json (elements and
review), vfx.json (and each authored section) and sound.json record, as
requested model or answering model; providers verified for it in
`watch.WATCH_PROVIDERS` (Google for the default; kimi-k3 and glm-5.3-flash
have none yet, so they are refused); and a render host (`--host`, else the
one an encode would use; `--local` does not move it). A `video.mp4` that
`doctor.cut_problems` calls stale or partial is not watched: the problem
and its `yt render` command are a `not_evidenced` line, and scoring goes on.
The sound is never sent (the listener hears the mix). The cut's picture is
sent as it is when it is under `watch.WATCH_MAX_BYTES` (14 MiB) and
`WATCH_MAX_SECONDS` (600), straight from here when build.json has no audio
track, else copied without its sound on the render host first; otherwise
the render host re-encodes it at
`WATCH_HEIGHT` (480) and `WATCH_KBPS` (600) with no audio, split at shot
boundaries when it is still too long or too large (a shot longer than a
piece is cut inside, with 1 s of overlap). The cut goes up once by rsync,
one ssh cuts every piece, the pieces come back to `qa/pass-NN/watch/`, and
the host's directory is removed; a piece over the limit or off its planned
length is an error and nothing is sent. Each piece is one gateway decision,
`watch-sNN`, cached by its bytes; an answer whose times fall outside the
piece, or whose `watched_seconds` is not near its length, is asked once
more (`-again`) and then recorded unwatched, both answers forgotten so the
next run asks again. A failed call (a 401, a model mismatch, an exhausted
retry) leaves its piece unwatched and the rest are asked; a 402 stops the
watch.
`qa/pass-NN/watch.json` keeps the model, the catalogue row, who served each
call, the plan, the findings (W1.., film times) and what was not watched;
the same cut, model, providers, constants and role reuse an earlier pass's
record (`reused_from`) when it watched every piece, unless `--fresh`. Each finding's start becomes a still of
the pass beyond `--frames` (at most `qa.WATCH_SAMPLES_MAX`, 12, blocking
ones first; one under `qa.WATCHED_NEAR`, 0.5 s, from a still merges into it), and the scorecard
gains a `Watched findings:` brief and a required `watched` verdict per
finding (confirmed, not_visible or disagree, naming the frame as `frames`
lists it; `frames/<name>` is read as `<name>`). A finding
two scoring samples confirm on its own still joins the findings as `source:
"watched:Wn"`; the rest are listed in findings.md as watched, not confirmed
or not sampled, and block nothing. The scorecard's `watch` and
`watch_engine` hold the record and the watcher's report, and `usage` merges
it. `--reuse PASS --watch` reads that pass's watch.json and watches nothing.
The estimate adds `watch_usd` (the programme at 258 tokens a second plus an
answer a piece, at the pinned price) each time `--watch` is asked, and, as
for a listen, `qa.pass_usd` leaves a past watch out of a plain `yt qa`. The
size, length, height,
bitrate, providers and token rate are the card's starting values, to be
measured live.

One reading is not a measurement: scored once, a category moves about ±0.5
from run to run on the same frames, the size of the steps a pass is read
for. So the scorecard is asked `qa.QA_SAMPLES` (3) times on the same
evidence, each its own inspection (`pass-NN-scorecard-s1`, `-s2`, `-s3`)
with the ten categories in a different order. Each category scores the
median of the valid samples, and the scorecard records its `spread` (max -
min; the overall's as `spread.overall`) beside it and in `findings.md`. The
samples' findings are grouped by one decision (`pass-NN-scorecard-merge`,
the improve loop's "same problem" test): a finding two or more samples
report stands (blocking only when two called it blocking) and names them in
`samples`; one only a single sample reports is listed under
`low_confidence`, not routed by `yt improve` and blocking nothing. The
reviewer's list of what it calls blocking goes through the same decision:
one sample's claim is low-confidence, and only a claim two samples make
stops READY. A category a rule sets (a silent film's sound, a capped
category) records no spread, since its score is no sample's. A sample
whose call fails is a failed row in the harness report, billed and never a
score; with fewer than two valid samples the pass is `not_evidenced`: no
scores, never READY, exit 1. The next pass compares with the last scored
one, `yt improve` scores a not-evidenced pass again before judging it, and
`yt doctor` points at `yt qa`, not at routing. `scoring` in the scorecard records every
sample (its order, its overall, whether it failed).

The story is read from the film as well. The reviewer's role carries the
narrative rubric's rows for the hook, stakes, open loops, reveal setup and
ending, rendered from `ytstudio/narrative.py`, for scoring
`narrative_clarity` and `editing_pacing`. The prompt carries the
screenplay's story (train, promise, stakes, payoff line, each question with
the time its section opens and the time the section that answers it does,
the re-hook shots) as what the writer declared. The pass holds the whole
narration as `narration.txt` beside the frames (the voiced takes' words
once `voice.json` has them, the screenplay's lines before that, and the
file's first line says which), so the inspection's evidence digest covers
it. Can the film be followed by ear alone, and by eye alone? It should need
both: frames that print the narration, or narration the pictures add
nothing to, count against `narrative_clarity`. The scorecard's `story`
block restates the premise as the film made it, says what the viewer has at
the end of each section (one non-blank line per screenplay section; an
answer that skips or blanks one is asked again once, and what is still
missing is named under `not_evidenced`), and predicts a viewer's
satisfaction. That prediction scores nothing and is listed under
`not_evidenced` until the work directory holds the channel's own
`work/retention.json`; the per-film `work/<slug>/retention.json` that `yt
retention` writes does not count. A pass written before this has no `story` and still
compares, since the comparison reads the scores only.

Nothing judges picture and sound together. `--listen` hears without seeing
and `--watch` sees without hearing. A pass that checks whether each shot's
`on_screen_text` and supers appear while their matching words are spoken is
gated, and no code exists for it (AUTO-152 Part C). It is built only when
both of two conditions hold. First, the owner records that AUTO-127's
visual-only default is reopened for an opt-in mode. Second, a live test
shows that a `models.LISTEN_MODELS` or watch model on the gateway accepts
picture and sound in one request, and records that request's size and
duration limits. Until both hold, `--watch` sends no sound. If the gate
opens, the pass measures only what AUTO-140 cannot: each text's offset in
seconds from its words. It takes `beat_sync` and the listen's picture
timeline as given and does not judge them again, so cue-to-reveal sync stays
AUTO-140's. Its judge must be of another model family than the
screenplay's author (`listen.family`).

The bar is applied mechanically on top: READY TO PUBLISH needs an overall of
9.0, no category under 8.5 and nothing blocking, where blocking includes what
the manifests already prove (a placeholder, a build error, a failed gate, a
blocking mechanical finding) and each vfx continuity break marked `blocking`
whose recorded reviewer is of another family than every recorded author
(`vfx continuity: section N (<heading>): <problem>` in `scorecard.json`'s
`blocking`). A verdict by the author's own family, or one that does not say
who gave it (a `vfx.json` from before `continuity.reviewer`), a major break,
`one_film: false` alone, and a skipped or failed continuity review stay
`yt doctor` warns and never change the verdict; a `yt vfx --sections N`
judges continuity again, and the new verdict decides the next QA. While a
blocking mechanical finding stands, `visual_quality` and `animation_vfx` are capped at 6, whatever the reviewer
wrote. The film's rhythm is measured too (`ytstudio/rhythm.py`, `pass-NN/rhythm.json`, AUTO-141): a `hold` (over 8 s with no visual change while the narration runs, from ffmpeg scene scores read on a render host), a `slow-opening`, a `metronomic` film (cut and build intervals with a CV under 0.3) and a section's `wpm` outside 130-200; a blocking one caps `editing_pacing` at 6 instead (a hold caps all three), the scan's findings are minor and TO VERIFY until its thresholds are measured on real renders, and with no host, `YT_NO_REMOTE` or a stale `video.mp4` the hold and the opening are not evidenced, never passed; with `--watch` the scan reads the cut the watch already sent, so it goes up once. When `build.json` has no audio track, `sound_design` and
`music_score` are 2.0 by rule and named as not evidenced, never a neutral 5.
Each pass lives under `qa/pass-NN/` with its frames, `narration.txt`, the
contact sheet, `scorecard.json` and `findings.md` (the mechanical section and the window's
numbers included), the next pass is compared with it, and a category that
drops by 0.5 is a regression. `qa.json` at the project root carries the
latest verdict. The exit code is 0 only when the film is ready.

Two scores inside the samples' spread cannot say whether a revision helped,
so each pass is also judged against the previous kept cut side by side
(AUTO-108). The previous cut is `--previous PASS` (`yt improve` names its
best kept pass), else the latest scored pass, else, when `improve.json`
says `yt improve` reverted that one, the best kept pass it records. At
every sampled moment the reviewer is shown the same timestamp of both cuts,
labelled A and B: the previous still is reused from that pass's `frames/`
when it sampled the same time, else rendered from the copy under
`improve/` whose document and every file it references (textures, images,
models, HDRIs, fonts, includes and what they name, as the renderer reads
them) match the `cut` digest that pass recorded; a moment with neither is
unpaired and named under `not_evidenced` and in `findings.md`. Which cut is
A is drawn per moment (half each, seeded by the pass) and recorded, so
position bias can be checked. One `<pass>-compare-pass` inspection, reading
the pairs from a temporary directory outside the pass, answers per moment
which is better (A, B or same), why, and the visual categories it bears on,
without being shown the scores. The scorecard files `side_by_side`: the
moments, the entries (`{category, result: better|worse|same, source:
"stills", moment, t}`, one per category the moment bears on, or one with
`category: null` when it names none; the shape a video comparison files
with `source: "video"`) and their tally (`qa.tally`: entries per category,
and under `total` one vote a moment, the result most of its entries give),
beside the absolute scores; `findings.md` has a Side by side section and
`qa.json` the total. The comparison is one decision a pass, priced once
(`spend.COMPARE_PASS_USD` until one is measured) whenever an earlier scored
pass exists. Nothing is compared without an earlier scored pass, with the
same cut, under `--reuse` (whose card keeps the `cut` its frames were drawn
from), or when the pass is not evidenced.

The scoring itself is blind to the pass before (AUTO-106). Shown the last
pass's scores and findings, the reviewer filed them again, so the scoring
prompt names no previous pass, score, overall or finding; the Δ column is
computed from the two scorecards once the new one exists, and `yt improve`
matches an old finding to a new one only after both exist. `--recheck`
asks, after the scoring, whether each of the previous pass's findings (at
most 20, by place and words only) still stands in this pass's stills: one
`<pass>-recheck` inspection, each answered stands, gone or cannot_tell with
why, filed under `recheck` in the scorecard, its tally in `qa.json` and a
section in `findings.md`. Nothing that scores reads it; the estimate adds
`spend.RECHECK_USD` each time it is asked and leaves a past recheck out of
the next pass, which does not ask it unless told.

QA reads stills; it never watches the film. `--against FILE --compare-model
MODEL` (AUTO-128) has a video model watch the previous kept cut FILE and this
pass's `video.mp4` whole, picture and sound, through the gateway engine, on a
harness of its own that takes no revision notes. It asks twice, once with the
previous cut as Cut A and once with the new one, each request blind: the
files are linked as `cut-a.mp4` and `cut-b.mp4` and labelled `Cut A:` and
`Cut B:`, and nothing names a pass, a score or which cut is new. Each of the
ten categories is answered A, B, same or cannot_tell. A category both orders
call for the new cut is better, for the previous one worse; two answers that
name different cuts are a tie (same), and cannot_tell in either files nothing.
Each category is one entry with `source: "video"`, and their tally is the
pass's `side_by_side`; the still pairs are then not asked. An answer that
breaks the schema after one retry, a timeout or a gateway failure fails the
comparison; a missing cut, a `video.mp4` older than the document, two cuts
over 15 MB or no `AI_GATEWAY_API_KEY` skips it. Either way it files nothing,
says why under `not_evidenced`, and the still pairs run as they would without
it. `qa/pass-NN/comparison.json` keeps both cuts' sha256, each order's answer
and the reconciled rows; the scorecard keeps the comparison's own account as
`compare_engine`, and `usage` is both. Either option alone, a model the
gateway engine cannot ask, or one of the family that made the script,
styles, keyframes, vfx, sound or build exits 2 before anything renders; when
none of their reports names a model, an `anthropic/` or `openai/` one is
refused, since the default CLIs made them.
`$YT_COMPARE_MODEL` names the model when `--compare-model` is not given; it
turns nothing on by itself.

`--frames N` stills (default: every shot once and each narrated line's
start, up to 60; more adds evenly spaced stills, fewer names the shots left
out) · `--quality` of the stills ·
`--reuse PASS` to
score an earlier pass's frames again, measuring them again and rendering
nothing but proof frames that were rendered graded ·
`--previous PASS` the earlier pass compared with, its scores and its cut
side by side (default: the latest scored pass, or improve's best kept one
when it reverted that) ·
`--recheck` the previous pass's findings asked again after the blind
scoring, scoring nothing ·
`--against FILE` the previous kept cut, watched beside the new one by
`--compare-model MODEL` (a gateway `provider/model` id) ·
`--host` / `--local` · `--fresh`

### `yt improve`

The improvement loop of `docs/production.md` §8, so a QA finding no longer
needs a human to carry it to the stage that owns it. From the latest QA pass
(scored again first if it is older than the newer of `video.mp4` and `scene.xml`), plus the gate's blocking
rows and doctor's fails, every finding is routed to one stage: the measured
mechanical kinds by their stated owner, the rest by one harness decision that
also merges duplicates and writes each stage a revision brief. Findings that
are a wrong fact (`upstream-fact`) or a policy outside the repo (`external`)
go to a human and are not looped on. A finding on the story (hook, stakes,
re-hooks, open questions, payoff) goes to `script` even though rewriting the
screenplay is expensive, and its brief names the narrative rubric key the
finding bears on (`[rubric: ending]`), which the Screenwriter's prompt then
carries. A finding that says the film lacks what the plan declares (no
payoff while `ending.payoff_line` exists) is annotated "the plan declared
..." and routed all the same: the reviewer watched the film, and the plan is
the writer's own report.

The brief lands in `feedback/<stage>.md`, and the harness puts it into the
system prompt of every decision and inspection that stage makes, as notes
that outrank the house standards (`harness.feedback_for`; the prompts a stage
builds itself use `harness.with_feedback`). Then the earliest routed stage
runs with `--fresh`, every stage downstream of it runs up to `yt render`, `yt
gate` and `yt qa --previous` score a new pass against the best one so far.
The side-by-side tally is read first, one vote a moment: more moments where
the new cut was better than worse keeps the pass even when its overall moved inside the
spread, more worse reverts it, and a tie leaves it to the score delta, read
second: an overall drop beyond the spread, a category drop or a new blocking
mechanical finding reverts whatever the tally says. With `--compare-model
MODEL` (or `$YT_COMPARE_MODEL`) every `yt qa` of a pass also gets
`--against` the `video.mp4` its snapshot kept, so the tally read is the
video comparison's (AUTO-128). That is the pass's own `snapshot/`, taken
before it changed anything, for `--resume` and `--rejudge` too, so the cut is
always the kept pass's: after a pass was kept, the project holds the attempt,
and a snapshot of it would compare the cut with its own earlier render. The
estimate adds `spend.COMPARE_USD` ($0.10 assumed) for the comparison until a
scorecard measures one, nothing without `AI_GATEWAY_API_KEY` (it is skipped),
and once one is measured no still-pair comparison either, since those run
only when the video comparison fails. A snapshot with no video.mp4 is noted on the
pass and the stills are compared, and a model of `--model`'s family is
refused before any stage. Without a tally the
pass is kept only if the overall
improves, no category drops more than 0.5 and no new blocking mechanical
finding appears (a category QA left not evidenced has no score, so it neither
drops nor gains, and when two passes scored different categories the overall
is compared over the ones both scored); a change smaller than the scoring
samples' spread is no change, so a gain inside it is not kept and a drop
inside it is no regression. Otherwise the snapshot in `improve/pass-NN/snapshot/` is
restored. A QA pass that is `not_evidenced` leaves the pass unjudged, and
the next run scores it again first. QA's finding owner is one of the stages, `upstream-fact` or `external`, so routing is a lookup, and `--resume` and `--rejudge` carry the earlier passes and survivor counts forward in `improve.json`. It stops at READY TO PUBLISH, at the pass or budget limit, or when
a defect survives three briefed passes, and writes `improve.json` and an
`improve.md` in the shape of the §11 report. The estimate is per stage, read from
each stage's own report (else a like project's) and marked measured, like or
assumed (QA at its cost per scoring sample times the three samples, so a
pass scored once prices the two it did not ask, and the side-by-side comparison once on top), and a routed vfx pass re-authors only the sections its findings fall in
(`--sections`), the whole film (`--fresh`) when a finding cannot be placed;
a `yt doctor` vfx warn whose fix names `--sections N` (a section owning a
serious continuity break, a section left under its bar, an effect shown
static) is placed in section N. A
routed script pass whose findings all name a shot sends only those lines back
(`yt script --revise feedback`, read from the brief's `.json` twin); one
finding that names none, or one briefed before and still there, runs the
whole screenplay again (`--polish --verify`). A finding that needs more than
its own line (a hook that does not state its promise, a section with no
payoff) has to be routed without a shot to get that full run.

```bash
yt improve --title X --dry-run                      # the routing, the briefs, the estimate
yt improve --title X --max-passes 1 --budget-usd 15
```

`--max-passes N` (3) · `--budget-usd B` (15) · `--target` (9.0) · `--from-pass
pass-NN` · `--quality draft|preview|final` (final) · `--dry-run` · `--resume` (run
the last failed pass again from the stage that failed, once its cause is fixed) ·
`--rejudge` (put the last pass's kept files back, render, gate and score it again on
clean evidence, then keep or revert by the same rule). A prose finding that
`build.json` contradicts (no audio, no narration, a placeholder) is routed nowhere
and listed in `improve.md` as contradicted. The stages it reruns review with
`opus` (the script's plan and story review, keyframe, vfx), so a `--model` of that
family is refused with exit 2 before any stage runs, as `yt run` refuses it.
A script the pass writes again whose story review rejects it stops the pass
(`story-review`): the snapshot is restored, the rejected screenplay is kept
under `attempt/`, and `improve.md` gives the verdict, before any voice or
authoring is paid for. The published film's retention findings (`yt
retention`, below) are routed beside QA's while `scene.xml` is the cut they
measured. A pass that rebuilds the film cannot say whether a dip it was
briefed on is gone, since no audience has watched the new cut: `improve.md`
lists it as not re-measured, never as fixed, until the new cut is published
and `yt retention` reads it. A voice, sound or music category the best pass
left not evidenced (no current listening review) is listed under "Not
evidenced (READY cannot be judged)" with what is missing and the command
that produces it. Doctor's warning that the narration is a stub or espeak
scratch take, which QA blocks READY on, is routed by lookup and blocking:
to `voice` when `doctor.real_engines` finds an engine that may be published
on this machine, else to `external` with the fix "pip install kokoro, or
set ELEVENLABS_API_KEY". It is then listed under "Needs a human decision",
and the loop stops there rather than reading the film into espeak again.

### `yt retention`

The evidence loop's first half (AUTO-101): what the audience did with the
published film, read back onto the shots that were on screen. Every number
in `docs/narrative/` is a prior until the channel's own curves replace it;
this is where they come in. Nothing is fetched and no model is asked: you
read Studio's retention report for the film and write it, in Studio's own
terms, as `work/<slug>/retention/*.json` (several files are merged, so the
key moments, the curves and the traffic can be typed in separately), or
pass `--export FILE`. The contract is `retention.INGEST_SCHEMA`:
`intro_retention_30s`, `above_typical_intro`, `top_moments`, `spikes`,
`dips` (each a `timestamp` in seconds, a dip with `kind` skipped, abandoned
or unknown), the standard `curve` and the `typical_curve` (points of
`timestamp` and `retention` percent), and, when Studio shows them, the
`traffic` shares (search, external, suggested, browse); without them the
Intro is recorded, not judged. `tests/fixtures/retention/` pins it with a
hand-built file until a real export, anonymised, replaces it.

Each timestamp lands on its shot and section from `build.json`'s timings,
with the questions the screenplay's ledger had open there. The findings
carry the corpus rule they apply, how far the standard curve sits from the
typical one (`delta_vs_typical`), and a rubric key as `hypothesis`, a
labelled guess from a fixed table, not a cause:

- a dip after 0:30: one finding naming the beat, `dip-skipped`,
  `dip-abandoned` or `dip-unknown` (judging 3);
- a top moment in the second half: `move_earlier` (judging 3);
- the Intro: a hook finding only when it is under the median of the
  channel's last 10 measured films of similar length published before it
  (a film of unknown length, or a later one, is left out and noted) and
  under half the views came from search and external traffic (structure
  38);
- a last 5% falling at least twice as steeply as a falling 75-95%: `outro`
  (a 75-95% stretch that held has no slope to compare, and is noted), and a
  spike after the payoff shot: `unforeshadowed-payoff` (structure 40).

A moment read from the relative curve is noted and filed nowhere (judging
2). Every constant (`retention.BOUNDS`) and both house readings are tagged
`sanity_bound` with their rule, and until the channel has 10 measured films
every finding is (judging 6); `yt doctor` says how many there are. It writes
`retention.json`, which `yt improve` reads.

```bash
yt retention --title X                       # reads work/<slug>/retention/*.json
yt retention --title X --export studio.json
```

### `yt doctor`

Reads what the stages wrote and says what to do next. No model calls, but it
does reach the render hosts: one short ssh per host asks for the engine, the
memory and the GPU. Nothing else goes on the network unless you pass
`--gateway-ping`. It reports the toolchain (engine, ffmpeg, the scene SDK,
the delivery gate, the third-party packages the stages import, which voice
engines are usable, which keys are set) and then every video,
with fail, warn and note lines and the command that fixes each; a record that
bills a model neither engine priced (`spend.PRICES`, `$CLAUDE_PRICES`,
`codex_engine.PRICES`, `$CODEX_PRICES`) is a warning, since a Claude call on
it that times out is booked at $0 and a Codex call on it is recorded without a
cost. A row the gateway engine booked says how each call was priced
(`cost_gateway_calls`, `cost_generation_calls`, `cost_table_calls`,
`cost_unpriced_calls`): one the gateway priced itself is not warned about, a
table-priced one is quiet when `$GATEWAY_PRICES` (or the tables above) has its
row, and one with an unpriced call still warns. A claude call booked through
the gateway at the CLI's own figure rather than the table (`cost_cli_calls`
on its row) is a warning of its own. Where `gateway/node_modules`
exists, a "gateway engine" block checks Node 22 or later, `ai` 7.0.150 and
whether `AI_GATEWAY_API_KEY` is set (by name, never its value). With
`YT_GATEWAY` set, a "gateway" block fails when the route cannot run (no
`AI_GATEWAY_API_KEY`, an unknown switch, a cloud provider switch left on),
warns when `ANTHROPIC_BASE_URL` was set by hand to another endpoint, which yt
replaces for the claude CLI, or ends in `/v1`, which the gateway answers with
a 404, notes a parent `ANTHROPIC_API_KEY` that the CLI's child gets blank
(in subscription mode it warns: the CLI would send it to the gateway), and
names the claude and codex endpoints in use; a proxy's credentials in
`YT_GATEWAY_URL` are not printed. `yt doctor --gateway-ping` (opt-in; doctor
makes no gateway call without it) sends one `GET /v1/credits` with the key
as a Bearer token and a 5-second timeout and shows the team's `balance` and
`total_used` in USD (team-wide, not this run's). It runs no model and uses no
tokens. A 401 or 403 fails with the AI Gateway API Keys page as its fix, a
429 or 5xx warns, and only the HTTP status and the answer's `error.type` are
printed. A redirect is not followed, so the key goes to no other host (it
fails naming the status), and a user and password in `YT_GATEWAY_URL` are
not sent by the ping (a note says so). It is refused under `YT_NO_REMOTE`. Once a
published film has been read by `yt retention`, it notes how many the channel
has measured: under ten, every retention finding carries `sanity_bound`.
Two warnings serve the film over the presentation (AUTO-143): a dossier
whose vetted image or video assets have no `ok` row in
`raw/08-downloads.json` is an `assets` warning naming `yt research
--download` (with no manifest, the assets were never downloaded; with rows
but none ok, none of the downloads succeeded, and the deferred and failed
rows say why), and each run of sections the vfx continuity review
names as staying in one setting (`monotony`) is a `vfx` warning with the
review's fix. Each section that owns a blocking or major continuity break is
one `vfx` warn (`continuity: section N (<heading>): <problem> [blocking;
reviewer <model>]`, several breaks of one section joined), and each section
whose best reviewed round ended under its bar is one (`section N ended at
S/10, under its bar B (budget spent)`, or whichever reason stopped it); both
name `yt vfx --project P --sections N` as the fix, so `yt improve`
re-authors that section alone. A blocking break QA cannot block on (no
reviewer of another family recorded) says so on its row, and a continuity
review that was skipped or failed is a note. None of these is a fail, so the
govern lane's vfx stage is never held by them.
Exit 1 on any fail, so it works in a script.

### `yt assets`

The catalogue search on its own, for when you just need footage of one thing:

```bash
./yt assets "EUV lithography scanner" --limit 8 --json hits.json
```

### `yt props`

Fetches CC0 3D props for one video from Poly Haven: glTF models with their
textures, HDRIs to light a set, PBR texture maps for a surface — what the
schema can place but no stage could provide, so `yt vfx` authors against
real assets instead of a blockout of primitives. Phrases come from the
command line, or, with none, are chosen by two model decisions: one picks
search phrases from what the project already says it needs on screen (the
premise, the script's asset and visual fields, the styles' shots) against the
catalogue's vocabulary, and one keeps only the candidates that fit what is on
screen, before anything is downloaded. "Nothing fits" is a valid answer and
leaves an empty `props.json`; the tooling itself knows no subject, and a
project with nothing to search exits 1.
Everything on Poly Haven is CC0, so there is no licence to check and no key
to load. The catalogue listing is cached for a day under
`work/.props-cache/`; a file already on disk is not fetched again.

```bash
./yt props --title lithography "cleanroom" "studio hdri" "brushed steel"
./yt props --title lithography --kinds model,hdri --limit 2 --redownload
```

Writes `props/<kind>/<id>/` (the files exactly as served, never opened or
parsed here) and `props.json` with `id`, `kind`, `file`, `files`, `licence`,
`credit`, `about` and `source_url` per item. With phrases on the command line
the stage asks nothing; otherwise its two decisions are cached like any other
(`--fresh` asks again) and it takes the model flags beside `--project`,
`--title` and its own.

### `yt probe`

Measures what the system can make look real. Each probe is a fixed brief
that names a class (polished and brushed metal, coated surfaces, glass and
liquid, light through thin material, terrain and distance, emitters, light
in a medium, macro and depth of field, dense detail, motion) and what must
read as real. It never says how to get there and names no film.

1. **Compose.** An agent writes a Python program with the sr-core SDK in a
   workspace that `ytstudio/sdkkit.py` prepares. The workspace holds
   `srkit.py`, `kit.py`, the SDK's own guide, the engine's capability
   manifest with every renderer-dependent attribute, and example programs
   that rendered on the engine.
2. **Build and frame.** The harness builds the program itself: it runs it
   twice and refuses a document that changes between runs, checks and
   validates every document, and writes its provenance. It then renders
   frames on the still host.
3. **Review.** A different model scores the frames and names, for every
   defect, whether the authoring, the engine or an asset caused it.
4. **Revise.** Under `--bar` the findings go back verbatim to the same
   author session, for up to `--rounds` revisions. The best round is the
   probe's score; a revision that scored lower is put back before the next
   one. `--resume --name RUN` continues an interrupted run.

```bash
./yt probe --list
./yt probe --fetch-props                       # a neutral CC0 set under work/.probes/props
./yt probe --only metal,glass --rounds 1
./yt probe --model sonnet --reviewer-model opus --jobs 3
```

**Output**

- `work/.probes/<run>/`: one workspace per probe (program, document,
  provenance, frames, review), `summary.json` and `summary.md` (score,
  rounds, change since the last full run, defects by cause).
- `work/.probes/latest.json` after a run that covered every probe. Its
  scores join every kit's `ENGINE.md` as measured on this engine.

### `yt run`

Every stage in order for one idea — research, styles, script, props,
voice, retime, keyframe, vfx, sound, build, render — skipping any whose
output is already in the project. `yt retime` follows voice so nothing is
authored against the screenplay's guess once the read is measured; `yt
keyframe` follows it, so the look is a section of the film timed to that
read, and the run stops before `yt vfx` until the look is approved (`yt
keyframe --approve`, then `yt run` again; a rejected look is authored again
with the notes; `--skip keyframe` runs without one). `--redo stage[,stage]` or `--redo all` forces them, `--skip` leaves
stages out, and `--quick` drops the quality passes (deliberation, polish,
fact-check, plan review, listen-back) for a cheaper first draft and renders at draft
quality. `cli.run_extras` is the flags each stage gets (`--download` for
research on every run, `--quick` included, with `--fresh` beside it on a
`--redo` without `--quick`, so build and vfx have the vetted pictures and
clips on disk (AUTO-143); `--rich` and
`--deliberate critique` for styles, `--polish --verify --review` for script
(with `--reviewer-model` and `--review-budget-usd`, as keyframe and vfx get them;
a `pool` reviewer is resolved once and handed to script alone as `sdk:<id>`,
keyframe and vfx keep `opus`, so with `--model opus` the run is refused
unless they are skipped),
`--fetch --synth` and `--review` for sound), and `cli.stage_argv` builds each
stage's argv; `yt props` gets no extras, only the flags every model stage
gets (only `yt render` goes without the model flags). Only the build reads
the cue sheet, so `yt sound` runs beside `yt keyframe` and `yt vfx` on its
own thread (`cli.Beside`, a daemon, so a Ctrl-C ends the run and the sound
stage with it; the terminal's interrupt reaches the stage's engine through
the process group, where a `kill -INT` of `yt` alone leaves that engine to
finish on its own) and is joined before `yt build`; a sound stage that
failed fails the run there, and a walk that stops earlier still waits for it
and reports its code. The synthesis `--synth` asks for runs on this
workstation (the agent's NumPy program, no ffmpeg); its WAVs are levelled on
a render host like the fetched ones. `core.write_json` writes every record
beside the file and moves it into place, so a write an interrupt cut short
leaves the old record whole rather than a truncated one.
`--dry-run` prints the stages the walk would take from the project as it
stands, one row per stage with its estimated cost (`spend.stage_estimates`:
measured from the project's own report, else a like project's, else
assumed) and the total, and runs nothing (the project is planted, as for any
stage). With `--reviewer-model pool` it still checks what the real run
would refuse on this machine (`YT_GATEWAY=vercel`, the gateway engine's key,
Node and install, the maker rule) and prices the chosen row, but reads
nothing from the gateway: the providers are read when the script runs.
`--budget-usd N` caps the whole
run: a stage whose estimate plus what the run has spent does not fit is not
started, the refusal names the estimate and the run exits 3
(`retime.UNSETTLED`), and `yt vfx` gets what is left as `--stage-budget-usd`.

### `yt ls`

One line per video, newest first, with a mark per stage. `--files` also lists
every file in each project. `yt doctor` is the same question answered in more
depth.

---

# `claude_engine` — the library

```python
from claude_engine import ClaudeEngine, EngineConfig, READ_ONLY_TOOLS

# 1 — pure reasoning (no tools, no filesystem access)
engine = ClaudeEngine(EngineConfig(tools=(), model="sonnet"))
print(engine.ask("Summarise the CAP theorem in two sentences.").text)

# 2 — structured reasoning (the schema is enforced by the CLI)
data = engine.ask_json(
    "Classify this ticket.",
    {"type": "object",
     "properties": {"severity": {"type": "string", "enum": ["low", "high"]}},
     "required": ["severity"]},
)

# 3 — agentic work (Claude reads, searches, edits, runs commands)
worker = ClaudeEngine(EngineConfig(tools=list(READ_ONLY_TOOLS), cwd="./repo"))
for event in worker.stream("Find every TODO and group them by module."):
    if event.subtype == "tool_use":
        print("tool:", event.tool_name)
    elif event.text:
        print(event.text)
```

`engine.agent(prompt)` is the same as `stream()` but runs to completion and
returns the final `Answer`.

## Conversations

Pass `resume=True` to continue the engine's last session; the context carries over.

```python
engine.ask("Pick a prime between 50 and 100.")
engine.ask("Is it a sum of two squares?", resume=True)
engine.reset()  # start fresh
```

## Task pipelines

Tasks are dependency-ordered; `{name}` placeholders in a prompt are filled with
the output of the task it depends on. Each task can enforce a JSON schema, run a
`check()` predicate, and retry on failure.

```python
from claude_engine import Task, TaskRunner

tasks = [
    Task("facts", "List 3 causes of CI flakiness.", schema=SCHEMA,
         check=lambda o: len(o["causes"]) == 3, retries=2),
    Task("runbook", "Write a triage runbook from:\n{facts}", depends_on=["facts"]),
]
results = TaskRunner(engine).run(tasks)
print(results["runbook"].output)
```

With `jobs=N`, tasks whose dependencies are done run side by side, up to N at
a time. Each runs on a worker the engine spawns (`engine.spawn()`: the same
configuration, its own session), and when it ends the worker's usage and
calls are folded back into the engine (`engine.absorb(worker)`), so
`engine.usage` is still the whole bill and each `TaskResult.usage` is one
task's share. In that mode `resume=True` continues the session of the task's
last dependency, so a chain keeps its context while unrelated chains overlap.
A task whose dependency failed is recorded as failed and never launched.

```python
tasks = [
    Task("fall",    "Author section 1 ...", agentic=True),
    Task("impact",  "Author section 2 ...", agentic=True),
    Task("ripples", "Author section 3 ...", agentic=True),
    Task("review",  "Read all three:\n{fall}\n{impact}\n{ripples}",
         depends_on=["fall", "impact", "ripples"]),
]
results = TaskRunner(engine, jobs=3).run(tasks)   # three authors at once, then the review
print(engine.usage)                                # the total, workers included
```

The same from the CLI: `python claude_engine.py run tasks.json --jobs 3`
(or a top-level `"jobs"` in the file). Fan-out this way keeps the program in
charge of every worker's tool set, budget cap and cost line. Handing the model
the `Agent` tool (part of `FULL_TOOLS`) is the other way: the model spawns
subagents inside the one `claude -p` process, under that one budget cap.

## Capability and safety controls

`EngineConfig` decides what the engine is allowed to do:

Capability has **two independent layers**. `--tools` decides what exists;
`--permission-mode` / `--allowed-tools` decide what may actually run. A tool
listed in `tools` is still denied under `dontAsk` unless it is also allow-listed —
`auto_allow_tools=True` (the default) does that for you.

MCP tools are a **third** surface: `--tools` does not filter them, so
`strict_mcp_config=True` (the default) is what makes `tools=()` genuinely
tool-free. Set it to `False` to inherit the servers from your `claude mcp` config.

Every `claude -p` flag is a field; a field left `None` is not passed, so the
CLI's own setting applies. Three places can fill a `None` field, in order:
`claude_engine.set_defaults(effort="high")`, then `CLAUDE_ENGINE_<FIELD>` in the
environment (`CLAUDE_ENGINE_MODEL`, `CLAUDE_ENGINE_EFFORT`,
`CLAUDE_ENGINE_FALLBACK_MODEL`, ...), then the CLI's `settings.json`.

| Field | Meaning |
|---|---|
| `model`, `fallback_model`, `effort` | which model, what to try when it is overloaded, how hard it thinks |
| `betas`, `agent`, `autocompact` | API beta headers; run as a named agent; compaction window |
| `system_prompt`, `append_system_prompt` | replace or extend the default prompt |
| `system_prompt_snapshot`, `exclude_dynamic_system_prompt_sections` | prompt-cache controls |
| `tools=()` | no built-in tools — pure reasoning (with `strict_mcp_config=True`) |
| `tools=READ_ONLY_TOOLS` | Read, Glob, Grep, WebFetch, WebSearch |
| `tools=EDIT_TOOLS` | the above + Edit, Write |
| `tools=FULL_TOOLS` | the above + Bash, the `Agent` subagent tool (the CLI also accepts its old name `Task`), TodoWrite |
| `permission_mode` | `dontAsk` (default), `acceptEdits`, `plan`, `bypassPermissions`, … |
| `allowed_tools` / `disallowed_tools` | fine-grained rules, e.g. `("Bash(git *)",)` |
| `auto_allow_tools` | allow-list whatever `tools` selected (default `True`) |
| `strict_mcp_config` | ignore the user's MCP servers (default `True`) |
| `max_turns_budget_usd` | hard spend cap per call |
| `max_turns` | hard cap on agentic turns per call (`--max-turns`); the CLI's stop, `error_max_turns`, is a permanent error that names it. 1 or more; 0 is refused. Codex has no equivalent: ignored, warned once; the chat bridge refuses it |
| `timeout`, `max_retries` | per-call wall clock and retry policy |
| `cwd`, `add_dirs` | which directories the agent may touch |
| `restricted`, `skip_permissions` | no code-running tools; or `--dangerously-skip-permissions` |
| `mcp_config`, `agents`, `plugin_dirs`, `plugin_urls`, `settings`, `setting_sources` | what the session loads |
| `safe_mode`, `bare`, `persist_session`, `fork_session`, `name` | ignore local config; minimal mode; keep nothing on disk; session identity |
| `include_partial_messages`, `include_hook_events`, `forward_subagent_text` | extra `stream()` events |
| `debug`, `debug_file`, `verbose` | CLI diagnostics |

Nobody is at the keyboard, so the engine passes `--permission-prompts none`:
anything that would prompt is denied instead of hanging. Grant capability
deliberately through `tools` and `permission_mode`.

Every call accumulates `engine.usage` (tokens, cost, wall clock, and the same
per model under `usage.by_model`) and `engine.log` (raw result payloads).
`Answer.model` names the model that actually answered, which matters once
`fallback_model` is set; `Answer.structured` is the CLI-validated output of a
schema call, and `Answer.json()` / `ask_json()` prefer it over re-parsing text.

### Retries and timeouts

Every attempt is a fresh launch with its own pinned session id, recorded on
`engine.pending_session_id` before the process starts so you can `resume` it
after a crash (the CLI refuses an id it has already used, so a retry can never
reuse one). A failure is classified: timeouts, overloads, dropped connections
and unexplained exits are retried up to `max_retries`; a session clash, a usage
error, an auth failure or a budget stop raise at once with
`ClaudeExecutionError.transient == False`. `stream()` keeps the same rules but
only retries while nothing beyond the CLI's own `system` messages has been
delivered, since a retry after content would replay it. A stream has a real
deadline (a watchdog kills a silent child) and stderr is drained on its own
thread, so a chatty child cannot stall the pipe.

### Denials are silent — check for them

A tool the permission layer refuses does **not** raise. The refusal returns to
the model as ordinary `tool_result` text, so it apologises and carries on,
often answering from memory. `is_error` stays `False` and the prose looks fine:

```python
answer = engine.ask("Search the web for X.")
if answer.denied_tools:                 # e.g. ['WebSearch']
    raise RuntimeError(f"ungrounded: {answer.denied_tools} were denied")
```

`Answer.permission_denials` and `Event.permission_denials` (on the `result`
event) carry the raw entries, each with `tool_name`, `tool_use_id` and
`tool_input`. Check them on any answer that was supposed to be grounded in a
tool.

## Command line

```bash
python claude_engine.py ask   "Explain this repo's layout"
python claude_engine.py json  "List 3 risks" --schema schema.json
python claude_engine.py agent "Summarise every Python file here"
python claude_engine.py agent "Add type hints to utils.py" --write   # Edit/Write/Bash
python claude_engine.py chat
python claude_engine.py run   tasks.example.json
```

Every `EngineConfig` field has a flag; `python claude_engine.py --help` groups
them (model, prompting, workspace, session / limits, diagnostics). The ones you
reach for first: `--model`, `--fallback-model`, `--effort {low..max}`, `--cwd`,
`--timeout`, `--budget`, `-v`.

---

# Layout

```
yt                      the pipeline entry point (also `python -m ytstudio`)
claude_engine.py        the engine, task runner, and its own CLI
codex_engine.py         the same API on the Codex CLI (`YT_ENGINE=codex`)
PURPOSE.md              why the pipeline exists, the bar, the principles
example_usage.py        five runnable engine demos (`python example_usage.py all`)
tasks.example.json      a pipeline spec for `claude_engine.py run`
ytstudio/
├── cli.py              the `yt` command: shared flags, subcommands, `run`, `ls`
├── workspace.py        one directory per video, and how a stage finds the last
├── harness.py          the one door to claude_engine: decide, inspect, cache, account
├── core.py             the Idea, the name key, usage accounting, shared readers
├── house.py            the channel's standards, sliced per stage
├── house/default.md    the shipped standards, docs/narrative/ distilled per stage
├── env.py              API keys from a file, names logged and never values
├── scene_sdk.py        finds sr-core, installed or in a checkout
├── ideas.py            Generator, the voice briefs, the idea store
├── research.py         Researcher, Dossier, asset vetting and downloads
├── assets.py           the open-catalogue search (NASA, Openverse, Commons, Archive)
├── styles.py           Director, the style library, the deliberation stages
├── script.py           Screenwriter, Section, Shot
├── narrative.py        the narrative rubric: criteria, levels, the review's and the plan review's schemas; `python -m ytstudio.narrative calibrate`
├── voice.py            VoiceOver, the TTS engines, captions and drift
├── props.py            CC0 models, HDRIs and textures from Poly Haven, into props/
├── vfx.py              the model authors each section's world, versioned under vfx/vNN/
├── framecheck.py       the checks on a proof frame that need no eyes, and the engine's off-camera report
├── design.py           the design system read from styles.json: roles, tokens, bands
├── sound.py            SoundDesigner, Cue, coverage analysis, the Freesound fetch
├── freesound.py        Freesound search, pick and preview download, CC0 and CC-BY only
├── build.py            SceneBuilder: the editor's decisions applied, continuous includes, hoisting, the mix
├── render.py           the engine driver, host routing, job-scoped remote directories, sliced encodes
├── retime.py           measured durations back into the screenplay, as accepted
├── pilot.py            the editor's cuts, validated, as segment outputs
├── gate.py             the delivery gate, triaged
├── review.py           the PDF review sheet, read
├── qa.py               sampling, stills, the scorecard, pass against pass, the judge's fixture calibration
├── assess.py           can this be produced? an agent investigates and proves it
└── doctor.py           health, status and what to do next
tests/                  stdlib unittest cases (`python -m unittest discover -s tests`)
work/                   your videos (created on demand)
```

Each stage module is importable on its own if you would rather drive the
classes from Python than through the CLI:

```python
from ytstudio.styles import Director
from ytstudio.core import Idea

director = Director(Idea("How EUV light is made", "explainer", "..."), count=3)
data, styles = director.run(...)
```
