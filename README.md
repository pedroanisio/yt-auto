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
`CODEX_BUDGET_TOKENS`, or the budgeted call refuses to start.

## Requirements

- `claude` on PATH (`claude --version`)
- An authenticated session: `claude auth`, `claude setup-token`, or `ANTHROPIC_API_KEY`
- Python 3.10+

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
the only thing that computes the engine's camera analysis; then the model
reviews the frames against the publication bar and scores them 0-10. Under
`--author-bar` (7) the findings go back to the author, up to `--revisions`
rounds (2). Each round of authoring lands in a new `vfx/vNN/`; nothing
authored before is overwritten.

Rendering runs on other machines: `RS_RENDER_HOSTS=user@a,user@b` in `.env`.
`yt render` probes each host once (engine, RAM, GPU) and routes by
docs/compute.md's rule -- memory, not GPU: a full encode and the audio mix go
to the host with the most RAM, stills and frames to the one with the least,
which is the faster GPU. `--slices N` splits a long encode across every host
(one `--start/--end` range each, the audio mixed once, joined by stream copy).
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
./yt keyframe --title lithography                      # lock the look: one frame, for approval
./yt voice    --title lithography --engine espeak      # scratch narration, free
./yt vfx      --title lithography --budget-hours 40
./yt sound    --title lithography --review --fetch     # the cue sheet, then its files
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
| `yt research` | claims with sources, a vetted source list, licence-checked footage, gaps, an outline | **$0.78** for a cut-down run; more at the defaults |
| `yt styles` | visual treatments ranked against the video's message, with shots, palette, type, motion | $0.06–0.10 |
| `yt script` | the full AV screenplay: picture, narration verbatim, supers, assets, citations | **$0.51** for 3 minutes with polish and fact-check |
| `yt props` | CC0 glTF models, HDRIs and texture maps from Poly Haven under `props/`, listed in `props.json` for the vfx brief | no model calls, no key |
| `yt keyframe` | the look-lock, composed by the author as a program with the SDK: one 1920x1080 frame of the hero, environment, key light, materials, palette roles, type sample and camera language, each labelled with its source, reviewed and awaiting the operator's approval | one decision, one author session and one vision review per round |
| `yt voice` | one WAV per shot, measured drift against the script, captions | no model calls; ElevenLabs bills per character, local engines are free |
| `yt vfx` | every effect specified and costed, then one world `<symbol>` per section, written by the author as a Python program with the sr-core SDK that the stage builds itself (determinism and provenance checked), with a single camera, its shots as phases, proof frames checked (frame, camera and geometry: a hero that penetrates a set solid, or that reads merged into one on screen with no shadow, is an error with time, object and depth) and reviewed, versioned under `vfx/vNN/` | scales with `--per-section`, `--budget-usd` per section, `--revisions` |
| `yt sound` | effects, ambience, score, motifs, mix targets, cue sheet; with `--fetch`, a CC0/CC-BY file per cue from Freesound under `sound/` | **$1.07** for 3 minutes with listen-back |
| `yt build` | the scene-render document: continuous includes of the authored worlds, model-planned graphics drawn from the design, captions, markers, the mix with the fetched cues on buses per role | one planning call per section |
| `yt render` | the encoded video, with the engine's report, from a job-scoped directory on a render host | no model calls |
| `yt retime` | the screenplay's shot lengths, replaced by the measured ones | no model calls |
| `yt pilot` | 30/60/180-second cuts of the finished film, nested, with what each proves | no model calls |
| `yt gate` | the delivery verdict: loudness, scopes, frames, caption limits, run on a render host and naming the requirement that stopped it; plus the geometry check (a hero that penetrates the set, or reads merged into it on screen, blocks whatever the tool and the triage said) | one triage call |
| `yt review` | the PDF review sheet: frames, captions, markers, sources, credits | no model calls |
| `yt qa` | the scorecard per pass: one frame window per sampled midpoint (the still is its middle frame), a contact sheet, the mechanical findings (flat, clipped, frozen, jump, off-camera, `penetration`: the hero inside a set solid, and `reads-merged`: the hero over a surface on screen with no shadow, from the engine's evaluated geometry and camera), the model's review | one inspection call per pass; one render job per sample, one eval job per pass |
| `yt improve` | routes each QA finding to the stage that owns it, briefs that stage through `feedback/`, re-runs it and everything downstream, keeps the pass only if it measurably improved | one routing decision per pass, plus the stages it runs |
| `yt doctor` | every video's state and the command that advances it | no model calls |
| `yt probe` | the realism probe suite: one neutral brief per class of material, light or motion, composed by an agent coding with the SDK, built and framed by the harness, scored by a different model with each defect's cause | **$1.06** for one probe and one round; up to three author sessions and three reviews per probe |

Those figures are measured on `sonnet`, which is the default; `--model opus`
costs more. `--effort {low..max}` and `--fallback-model` apply to every call a
stage makes (or set `CLAUDE_ENGINE_EFFORT` / `CLAUDE_ENGINE_FALLBACK_MODEL`
once); a decision asked at a different effort is asked again rather than
served from the project's cache. `yt research` and `yt sound` are the expensive stages — research
because it actually browses the web, sound because a cue sheet is long.

## Where things land

`work/` is **ephemeral by policy**: a project directory is an output of the
pipeline, never the only copy of anything, and the standard of done is a run
that regenerates it end to end from `work/ideas.json` forward. Delete freely.

Everything for one video lives in one directory:

```
work/
├── ideas.json                      every idea from the last `yt ideas`
├── ideas.store.json                every unique idea ever generated
└── how-euv-light-is-made/          one video
    ├── idea.json
    ├── dossier.json  README.md  claims.csv  sources.csv  assets.csv  notes.md
    ├── raw/  downloads/            cached research stages, fetched assets
    ├── styles.json  styles.md
    ├── script.json  script.md  narration.txt  shotlist.csv
    ├── props/<kind>/<id>/  props.json   CC0 models, HDRIs, textures from Poly Haven
    ├── keyframe/    keyframe.scene.xml  keyframe.png  brief.md   the look-lock frame
    ├── keyframe.json  keyframe.md       its elements, scores and approval
    ├── voice/       voice.json  narration.vtt  narration.srt  voice.wav
    ├── vfx/vNN/<section>.scene.xml     one authored world per section, per round
    │   ├── frames/                     the proof frames the gate and the review read
    │   └── <section>.include.scene.xml the cleaned copy the build's <include> points at
    ├── vfx.json     vfx.md     vfx.csv  the plan, the active version, the review rounds
    ├── sound/       index.json          the fetched cue files, and which Freesound id each is
    ├── sound.json   sound.md   cues.csv
    ├── scene.xml    build.json          the compiled document, and its report
    ├── pilot.scene.xml  pilot.json      the approval cuts
    ├── retime.json  gate.json  review.json  review.pdf
    ├── video.mp4    render.json         the film's encode; other jobs write render-<job>.json
    ├── qa/pass-01/  frames/  samples.json  contact-sheet.png  scorecard.json  findings.md
    ├── qa.json                         the latest verdict
    └── decisions/<stage>/<name>.json   what the model decided, and from what
```

`--title` finds a video by matching a project already on disk, then an idea in
`work/ideas.json`, and failing both takes what you typed as the title itself.
`--project DIR` points at one directly, `--work DIR` keeps videos somewhere
other than `./work`, `--index N` picks the Nth idea, and `-` reads one idea as
JSON on stdin. Each stage writes its own files into the project *and* prints to
stdout, so `--json` / `--md` / `--csv` are only for sending a copy elsewhere.

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
answers, with read-only tools. Both write the answer under
`decisions/<stage>/<name>.json` with a digest of everything the model saw, so
a stage re-run with the same inputs costs nothing and a changed input is asked
again; `--fresh` asks regardless. Each manifest carries `"engine"`: the model,
the standards digest, the cost, and which decisions were cached.

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
sound`, `## vfx`). Each stage is handed `## all` plus its own, so no prompt
carries a rule that does not apply to it, and a `house.md` inside one video's
directory replaces sections of the same name for that video alone. `--house
FILE` points elsewhere, `--no-house` ignores it. The digest of what was loaded
goes into every manifest, so an artifact says which standards produced it.

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

### `yt research`

Staged, cached web research: it plans the questions, researches them in
parallel with real browsing, extracts claims with the source each came from,
builds the source list, plans the visuals, finds footage in open catalogues
(NASA, Openverse, Wikimedia Commons, Internet Archive), vets it against
look-alikes, verifies each licence and that each URL really serves a file,
audits its own gaps, and writes a script outline. Every stage is cached, so a
re-run resumes instead of paying twice; `--fresh` forces the work again.

`--depth` questions · `--shots` / `--per-shot` · `--asset-sources` ·
`--allow-sa` · `--agentic-assets` · `--download` · `--budget USD` · `--fresh`

The plan depends on the genre. A narrated explainer gets the full scope; a
wordless piece -- a genre or premise that says cinematic, music video,
ambient loop, "no narration" -- gets the lighter cinematic scope
(`research.scope_for`): three questions about reference, look and the
physics of what moves, no misconception hunt, claims only about what is on
screen, and gaps that do not block on unsourced numbers. The dossier records
which scope it got; an explicit `--depth` wins over either.

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

### `yt script`

Writes the screenplay one call per section — a 15-minute script written in a
single pass loses its thread and re-announces its own numbers. Each section is
written knowing its beats, its word budget (`seconds/60 × --wpm`, which is what
actually controls runtime), the previous section's last shots verbatim, the
figures already spoken aloud, and the next section's heading to set up.

With research in the project, every shot carries the source behind its claim,
and `--verify` re-reads the finished script for lines the research does not
support. `--polish` reads the narration end to end and rewrites only what
repeats, stumbles or buries its point.

`--minutes` · `--wpm` · `--sections` · `--outline` to use the dossier's own beat
sheet · `--inspo` for voice · `--notes` which outrank everything else

The runtime floor is 10 seconds (`yt sound` keeps the same floor), so a short
film keeps its own length instead of being padded to half a minute.

### `yt keyframe`

Locks the look before `yt vfx` spends: a studio's style frame. One composed
1920x1080 frame presents the video's most qualified visual elements together,
in the chosen treatment, for the operator's approval.

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
editing one line re-records one shot. `--redo LABELS` forces specific shots. A
rate limit is waited out, not fatal: finished takes come from cache, so nothing
is paid twice.

Writes `voice/` (one file per shot), `voice.json`, `narration.vtt` and
`narration.srt`, plus `voice.wav` with `--stitch`. `--layout sequential` (the
default) places takes back to back with real gaps and gives the honest runtime;
`--layout planned` uses the script's own timecodes and warns when a take runs
into the next, because then the audio genuinely overlaps.

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
move reads as one take, not as symbols with cameras of their own. No other
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
draft encode per shot of a 3D symbol asks the engine which objects it never
framed, a `hero-` object out of view is an error on that shot's frame, set
objects out of view are counted, and a set mostly out of view across the
frames is a camera pointing at nothing. The model then reviews the frames
against the publication bar and scores them. An error, or a score under
`--author-bar` (7), sends the findings back to the author for up to
`--revisions` rounds (2); every round and its score are kept in `vfx.json`.

Authoring is versioned. Each run lands in the next `vfx/vNN/`, `vfx.json`
names the active version, and `--fresh` replans and opens a new version;
nothing authored before is overwritten.

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

`--per-section` · `--jobs` · `--budget-usd` per section · `--budget-hours` ·
`--width` · `--height` · `--revisions` · `--author-bar` · `--inspo` · `--fresh`

### `yt sound`

Spots audio against the screenplay: where music enters and what it is doing
there, which effect lands on which word or frame, what the room sounds like
underneath, and where everything drops out. Produces a sonic palette with named
motifs, mix targets in real numbers (bed dB under narration, −14 LUFS master,
−1 dBTP), and a numbered cue sheet. `silence` is a cue kind, because scored
silence is a decision.

Coverage is computed, not asked for — merged music intervals, share of runtime
scored, longest unbroken run, unscored stretches — and `--review` hands those
numbers back for a listen-back pass that flags wall-to-wall music, cues that
fight the narration, effects with no sync point and sourcing that will not
survive a claim.

`--free-only` (CC0, public domain, CC-BY or self-recorded only) · `--library
"epidemic sound"` · `--no-music` · `--mood` · `--inspo` · `--minutes` when there
is no screenplay yet (floored at 10 seconds)

Every cue except silence also carries a `query`: the few plain words a
library search would want, naming the sound rather than its purpose.
`--fetch` turns the sheet into files: each cue is searched on Freesound
(`ytstudio/freesound.py`) -- the model's query first, then its search terms,
then its name -- under CC0 and CC-BY only, every hit judged again by the
pipeline's one licence policy; the best fit by duration and rating is
downloaded as the high-quality mp3 preview into `sound/` and written back
into the cue as `file`, `licence`, `credit`, `source_url` and `duration`,
which is what `yt build` places. A preview is fine for a scratch mix and for
placing against picture, and the manifest says so. `sound/index.json` maps
each Freesound id to its file, so a re-spotted sheet that lands on the same
sound downloads nothing; a cue nothing matched keeps no file and is listed
under `unfilled[]` with the queries tried. With a `sound.json` already in the
project `--fetch` reuses it and asks the model nothing; `--fresh` spots again
first. The keys are `FREESOUND_CLIENT_ID` and `FREESOUND_CLIENT_SECRET`
(the secret is the token the API wants); without one nothing is fetched and
only the name is reported. `yt run` always passes `--fetch`.

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
2. **A licensed image** the research downloaded, carrying its `license` and
   `credit` into the document.
3. **What the editor decides.** Every shot neither of those covers goes to the
   model with the shots around it, the images available and the design
   system, and it answers under a schema: `extend_previous` or `extend_next`
   when a neighbouring effect genuinely carries the shot (the include is
   stretched over the gap), `image` with an exact path, or `block` with the
   reason the animation stage needs. The same decision names the shots whose
   caption would fight the picture and the hold after the last word.
4. **An honest placeholder** for what was blocked, counted and named. A
   placeholder is disqualifying, so the manifest carries `publishable: false`
   while any remain, and so does anything the editor listed under `blocking`.

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
and the editor's decisions under `editorial`.

Every shot and section also becomes a marker, because a marker survives
re-timing and a timecode does not. Supers are checked for reading time and
captions are split on word boundaries.

The mix is the sound design's. Every cue of `sound.json` that has a file
(`yt sound --fetch`) becomes an `<audio>` asset and an `<audioTrack>` on the
bus of its role — `music`, `effects` (sfx, stingers, transitions),
`ambience` — at the cue's time with its fades, a looping cue repeated to
fill its window; the narration takes keep their own `narration` bus, and
music and ambience duck under it when there is one. The level is staged
across bus and track inside the schema's own facets, because gain saturates
at +24 dB per stage; a cue that still cannot reach its level is a warning
that says where it sits. A `silence` cue is a window on the master's volume,
a held 1→0→1, not a muted track. The master carries the loudness target,
true peak and limiter. A cue with no file is listed under `audio_missing`
in `build.json`, not invented, and a sound design of which nothing could be
placed sets `publishable: false` — a project with no sound design at all is
not blocked. `build.json` also counts `audio_tracks`, `narration_tracks`,
`buses` and `silence_windows`.

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

`--width` · `--height` · `--fps` · `--codec` (checked against the schema's own
list) · `--bitrate` and friends (never `crf`) · `--loudness` · `--true-peak`
· `--no-limiter` · `--no-captions` · `--no-audio` · `--fresh` · `--force`

### `yt render`

Finds the engine through `RS_RENDER_BIN`, on PATH, or in a checkout beside this
one, then runs it and keeps the report. `--quality draft|preview|final`,
`--start`/`--end` for a range, `--still TIME` or `--frames A..B` for pictures,
`--output ID` for one declared output, `--validate` for the engine's own
validation, and `--strict` to fail on any finding.

**[docs/production.md](docs/production.md) is the production brief**: what a
finished piece must be, which file feeds which decision, the checks, and the
loop that runs until it is done. Read it before composing anything.

Heavy work does not run on this machine: see
[docs/compute.md](docs/compute.md) for the two render hosts, which job goes
where (the rule is memory, not GPU), the traps, and how a job's directory on
the host is scoped and cleaned.

On a host the project lives once, in a persistent mirror
`/tmp/yt-render/<slug>/mirror/`, refreshed by an incremental rsync before
every job (what no render needs stays behind: caches, `raw/`, `qa/`,
`decisions/`, rendered outputs, the proof frames; `props/` and `sound/`
travel because the scene references them). Every job then gets a directory
of its own, `/tmp/yt-render/<slug>/<job>-<pid>-<suffix>/`, hardlinked from
the mirror in a second (`cp -al`, or `rsync --link-dest` where cp cannot),
with the parent directories of its `-o`/`--report` outputs created first
because the mirror carries no output trees; the engine runs there, the
results (video, stills, WAV, the reports) are pulled back and the job
directory is removed. The mirror is never removed by a job;
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
`rewrite` (the line is wrong for the shot; back to `yt script`) or
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

The gate needs the engine and ffprobe, and this workstation has neither by
policy, so like a render it runs on a host: `--host USER@HOST`, or by
default the host an encode would go to when `RS_RENDER_HOSTS` is set and the
tools are not here. The candidate, its envelope and the two tools
directories are synced into `/tmp/yt-gate/<slug>/gate-<tag>/`, every path
is rewritten for the copy, the gate runs with the host's engine and ffprobe,
the output comes back and the directory is removed (`--keep-remote` keeps
it). `--local` runs it here.

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
(`samples.json`): the hook, every section's opening, the longest authored
effects, 25/50/75 % of the runtime and the ending, each at the midpoint of its
shot, never at a boundary. The stills are rendered by the engine on the render
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
more than one sampled shot. Then the model reads the frames and the sheet —
with the shot each should show, the editorial decisions, the numbers and the
mechanical findings beside each — and files the ten-category scorecard under
a schema, every finding with a timestamp, what is visible, why it lowers
quality, the root cause, the owning stage and the fix. What stills cannot
evidence (the voice as heard, the mix, the music) it scores from the
measurements and lists under `not_evidenced`.

The bar is applied mechanically on top: READY TO PUBLISH needs an overall of
9.0, no category under 8.5 and nothing blocking, where blocking includes what
the manifests already prove (a placeholder, a build error, a failed gate, a
blocking mechanical finding). While a blocking mechanical finding stands,
`visual_quality` and `animation_vfx` are capped at 6, whatever the reviewer
wrote. When `build.json` has no audio track, `sound_design` and
`music_score` are 2.0 by rule and named as not evidenced, never a neutral 5.
Each pass lives under `qa/pass-NN/` with its frames, the contact sheet,
`scorecard.json` and `findings.md` (the mechanical section and the window's
numbers included), the next pass is compared with it, and a category that
drops by 0.5 is a regression. `qa.json` at the project root carries the
latest verdict. The exit code is 0 only when the film is ready.

`--frames N` for more evidence · `--quality` of the stills · `--reuse PASS` to
score an earlier pass's frames again, measuring them again without rendering ·
`--host` / `--local` · `--fresh`

### `yt improve`

The improvement loop of `docs/production.md` §8, so a QA finding no longer
needs a human to carry it to the stage that owns it. From the latest QA pass
(scored again first if it is older than the video), plus the gate's blocking
rows and doctor's fails, every finding is routed to one stage: the measured
mechanical kinds by their stated owner, the rest by one harness decision that
also merges duplicates and writes each stage a revision brief. Findings that
are a wrong fact (`upstream-fact`) or a policy outside the repo (`external`)
go to a human and are not looped on.

The brief lands in `feedback/<stage>.md`, and the harness puts it into the
system prompt of every decision and inspection that stage makes, as notes
that outrank the house standards (`harness.feedback_for`; the prompts a stage
builds itself use `harness.with_feedback`). Then the earliest routed stage
runs with `--fresh`, every stage downstream of it runs up to `yt render`, `yt
gate` and `yt qa` score a new pass, and the pass is kept only if the overall
improves, no category drops more than 0.5 and no new blocking mechanical
finding appears. Otherwise the snapshot in `improve/pass-NN/snapshot/` is
restored. It stops at READY TO PUBLISH, at the pass or budget limit, or when
a defect survives three briefed passes, and writes `improve.json` and an
`improve.md` in the shape of the §11 report.

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
and listed in `improve.md` as contradicted.

### `yt doctor`

Reads what the stages wrote and says what to do next. No model calls, no
network: it reports the toolchain (engine, ffmpeg, the scene SDK, the delivery
gate, which voice engines are usable, which keys are set) and then every video,
with fail, warn and note lines and the command that fixes each. Exit 1 on any
fail, so it works in a script.

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
`credit`, `about` and `source_url` per item. No model calls; the stage takes
only `--project`, `--title` and its own flags.

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
keyframe, voice, retime, vfx, sound, build, render — skipping any whose
output is already in the project. `yt keyframe` follows props so the look is
locked from real assets before anything is authored (`--skip keyframe`
leaves it out; the frame still awaits `yt keyframe --approve`). `yt retime`
sits between voice and vfx so nothing is authored against the screenplay's
guess once the read is measured. `--redo stage[,stage]` or `--redo all` forces them, `--skip` leaves
stages out, and `--quick` drops the quality passes (deliberation, polish,
fact-check, listen-back) for a cheaper first draft and renders at draft
quality. `cli.run_extras` is the flags each stage gets (`--rich` and
`--deliberate critique` for styles, `--polish --verify` for script,
`--fetch` and `--review` for sound), and `cli.stage_argv` builds each
stage's argv; `yt props` gets only `--project` and `--quiet`, because it
decides nothing.

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
├── env.py              API keys from a file, names logged and never values
├── scene_sdk.py        finds sr-core, installed or in a checkout
├── ideas.py            Generator, the voice briefs, the idea store
├── research.py         Researcher, Dossier, asset vetting and downloads
├── assets.py           the open-catalogue search (NASA, Openverse, Commons, Archive)
├── styles.py           Director, the style library, the deliberation stages
├── script.py           Screenwriter, Section, Shot
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
├── qa.py               sampling, stills, the scorecard, pass against pass
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
