---
name: yt
description: Operate this repo's yt video pipeline (ytstudio + claude_engine) that takes a video from idea to a rendered, gated, QA-scored film on scene-render. Use whenever the conversation is about making, changing, rendering, checking or delivering a video with `yt`, repairing or re-running one of its stages, where a project's files live, or what counts as done.
---

# Operating the yt pipeline

This file, `ytstudio/SKILL.md`, is the source of truth for how the pipeline
is operated. `.claude/skills/yt/SKILL.md` is a link to it, and
`tests/test_skill.py` fails when the link breaks or a stage goes unnamed
here. The depth lives elsewhere and this file points at it:
`docs/production.md` (the publication bar and the improvement loop),
`docs/compute.md` (the render machines, measured), `README.md` (the engine
and the stages for a newcomer), and `yt <stage> --help` (every flag).

## What this is

A gen-AI production pipeline that creates and composes top-tier YouTube
videos on scene-render (the Rust engine) and sr-core (the schema and its
Python SDK): visually intense explainers with voice-over and motion
graphics, in 2D, 2.5D and 3D, in whatever aesthetic the editorial decisions
call for. The intelligence is injected through `claude_engine.py`; the
stages in `ytstudio/` own only the plumbing. `yt` is `./yt` at the repo
root, or `python3 -m ytstudio`.

## The invariants

These are the rules that are not derivable from the code. Every drift so far
came from breaking one of them.

1. **The rendered video is the evidence.** Not the manifest, not the exit
   code, not a document the engine accepted. Nothing is judged without
   looking at frames.
2. **The aesthetic is editorial, never hardcoded.** The treatment comes from
   `styles.json`; no stage, prompt or template bakes in a colour, a layout,
   "2D explainer" or any look. The model authors against the full schema.
3. **Every judgement goes through the harness.** A decision is a
   `Harness.decide` or `Harness.inspect` call in `ytstudio/harness.py`, under
   a schema and the house rules, cached by digest under
   `work/<slug>/decisions/<stage>/`. A deterministic default that silently
   replaces the model's answer is a decision nobody made: validate, retry
   once, then fail or record the fallback as a finding. Every stage's
   structured judgement goes this way: styles, script, sound, research, the vfx
   planner, ideas and assess decide under `decisions/<stage>/`, the answer is
   checked for the schema's required fields, and the stage's record carries the
   harness's `engine` report beside `usage`. `ideas` is recorded and checked
   but always asked afresh. The web-agent passes in `research` and the
   `assess` investigator are agents, not decisions.
4. **Edit the owning stage, never the output.** `scene.xml` is compiled by
   `yt build`; the `vfx/vNN/*.scene.xml` documents are authored by `yt vfx`.
   Fix the source stage and re-run. Authoring is versioned: every run lands
   in the next `vfx/vNN/`, `vfx.json`'s `authoring.version` names the active one, and `yt vfx
   --fresh` opens a new version rather than overwriting an earlier one.
5. **`work/` is disposable.** A project directory is an output. The
   deliverable is the pipeline that regenerates it end to end; a golden
   standard is a fresh run, never a preserved folder. Never restore a trashed
   project, and never treat `work/` as the only copy of something that cost
   money.
6. **One authoring layer.** Document authoring happens in `ytstudio/vfx.py`;
   `design.py` is the token source. Do not add a second geometry or
   composition writer. The documents themselves are written by the authors'
   programs, through the kit (`ytstudio/sdkkit.py`); pipeline code never
   writes a scene's content.
7. **An `<include>` carries neither styles, paints nor lights.** `yt build`
   hoists them (the file's tokens win, design fills the rest; a light keeps its
   `<animate>` verbatim, re-timed by the include's start and gated to its span
   by explicit holds, so the author's curve reaches the film and no light
   fades into a neighbouring section; each intensity track is gated on its
   own, a ramp crossing the include's end is clipped, a key's `marker` is
   read from the document's own `<markers>`, and an `additive`,
   `normalized`, `extrapolateBefore`/`After` or generated marker the gate
   cannot carry is converted and named in the findings; and a `downloads/` image inside
   an include is credited from the manifest or is an error) and points the
   include at a cleaned copy, `vfx/vNN/<section>.include.scene.xml`, with
   the tokens inlined and those sections dropped, so the engine raises no
   E12 warning. The authored file is never written. The same copy drops a
   section's own `colorManagement`: the film has one colour pipeline. `yt
   build` asks the harness once for it (`look`: tone mapping, exposure and
   one ASC CDL, from the treatment and palette; `none` is valid), clamps it
   to sane bounds, writes one `colorManagement` with `looks=` naming the
   look, and records it as `look` in build.json. The builder holds no grade;
   the kit example `colour_and_look.py` shows the elements.
   **Text is pinned to a shipped font file.** `yt build` resolves every text
   node's family/weight/style to `ytstudio/fonts` (`fonts.py`), copies the
   file into the project's `fonts/`, adds the `<font>` asset, and treats a
   family it has no file for as an `error:` finding. Author with a shipped
   family (Inter, Inter Display, Lora, Source Sans 3, STIX Two Text, Liberation,
   DejaVu); a brand face
   such as JetBrains Mono or IBM Plex fails the build, it does not fall back.
   The same refusal reaches the author earlier: `sdkkit.rule_findings` reports it as
   `error: rule:font` in the author's own `kit.py build`, and both author briefs list the families.
8. **A placeholder is disqualifying,** not deductible. While any remain the
   verdict is NOT READY whatever the other scores say.
9. **Nothing heavy runs on this workstation.** Renders, encodes, mixes and
   the engine's validation go to the render hosts. Never `cargo build`,
   install or run the engine here. `yt qa --listen`'s stream copy of the
   film's audio and its ffprobe of the duration decode and encode nothing,
   so they are not this rule's work; where ffmpeg is not on PATH the flag
   is refused.

**The invariants hold under Vercel AI Gateway, and the CLIs stay the
default** (AUTO-117). The claude and codex CLIs answer every call they can;
the gateway is an opt-in on top. `YT_GATEWAY=vercel` changes only their
transport, never which tool runs, and with it unset each call's argv, child
environment, booking and decision cache are as they were. A pass only a
gateway model can do (listening, watching, a reviewer from another maker)
sits behind its own flag; without the gateway the flag is refused or the
pass skipped and recorded as not evidenced, and a CLI call is never
replaced by a gateway one. Through it a judgement is still a
`Harness.decide` under a schema, validated, asked once more, then failed
(3); every call is still booked per model, as an estimate of the bill
whose row says how it was priced (the cost the gateway reports, else a
price row at list price, else the CLI's own figure); the reviewer still
differs from the author by family, or by maker for a reviewer from another
maker (`sdk:<id>` or `pool`), a gateway id taking its direct name's family
and a `vmc/<slug>` refused on either side; nothing
heavy moves here (9); and Python stays dependency-free, the gateway
engine's Node program installed only for a stage that opts in. The
README's "Vercel AI Gateway (optional)" section has the variables, the
endpoints and what is not yet checked live.

## One word per concept

The same meaning is spelled the same way in every stage that has it
(`ytstudio/flags.py`, asserted by `tests/test_stage_flags.py`). An old
spelling still works for one release, hidden from `--help`, and prints a
one-line note.

| Concept | Flag | Stages | Old spelling |
|---|---|---|---|
| revision rounds the author gets | `--rounds` | `keyframe` (with `--sheet`), `vfx`, `probe`, `script` (the fact-check's revisions and the story review's, each; the plan review sends a plan back once) | `--revisions` (`vfx`) |
| the review score under which work goes back | `--bar` | `keyframe` (with `--sheet`), `vfx`, `probe`; not `script`, whose plan review and story review have no score: the verdict (accept, revise, reject; recommend, consider, reject) is the bar | `--author-bar` (`vfx`) |
| a spend cap in dollars | `--budget-usd` (`--stage-budget-usd`, `--synth-budget-usd`, `--review-budget-usd` cap the whole stage, the synthesis, a review) | `research`, `keyframe`, `vfx`, `probe`, `assess`, `improve`, `sound`, `run` (the whole run); `--review-budget-usd` on `script` too (each plan or story review decision) | `--budget` (`research`) |
| the model that reviews, never the author's own: it must differ from `--model` by family (a gateway id is its direct name's family, `openai/gpt-5` is `gpt-5`'s; a gateway virtual model, `vmc/<slug>`, is refused on either side), or the stage is refused before anything is paid. `pool` or `sdk:<provider/model>` (script and `narrative calibrate` only; AUTO-125, `ytstudio/reviewers.py`) is a reviewer from another maker on the gateway engine: `pool` takes the first pinned row of `reviewers.POOL` whose maker (the gateway id's prefix) is not the author's, `sdk:` names one row; it needs `YT_GATEWAY=vercel` and the gateway engine's prerequisites, is judged by maker (an unknown maker on either side is refused), and keyframe, vfx and probe refuse it, since their reviewers open images | `--reviewer-model` (default `opus`; `--review-budget-usd` is what one review may cost, 2) | `keyframe`, `vfx`, `probe`, `script` (with `--review`), `run` (handed to script, keyframe and vfx; `pool` is resolved once and handed to script alone as `sdk:<id>`, while keyframe and vfx keep `opus`, so `--model opus` with `pool` is refused unless they are skipped) | |
| ignore what is cached and do the stage again | `--fresh` | `research`, `keyframe`, `vfx`, `props`, `script` (every decision asked again: what a story review's plan flag names), `voice`, `sound`, `build`, `retime`, `review`, `qa`, `gate`, `pilot` | |
| the model, its reasoning effort, its fallbacks | `--model`, `--effort`, `--fallback-model` | every stage that calls a model, `props` included; not `render` | |
| how many of the stage's independent pieces run at once | `--jobs` | `ideas`, `research`, `styles`, `script`, `keyframe`, `vfx`, `probe`, `sound` (with `--fetch`: cues searched, downloaded and judged at once, default 4) | |
| the shots sent back to the screenplay for one revision, by label, `retime` or `feedback` | `--revise` (`--why` the reason) | `script` | |

`vfx --budget-hours` is not money: it is an input to the plan (refinement
hours available). `voice --redo` takes take labels; `run --redo` takes stage
names; `script --revise` takes shot labels, or where to read them.

## The stages and their files

Everything for one video lives in `work/<slug>/`. Each stage reads what the
ones before it wrote, so paths are never passed by hand; `--title` matches a
project on disk, an idea in `work/ideas.json`, or is taken as the title
itself, and `--project DIR` names a directory directly.

| Stage | Reads | Writes | Model |
|---|---|---|---|
| `yt ideas` | a niche | `work/ideas.json` | yes (`--rich` adds each idea's `question`, `stakes`, `viewer_belief` and `promise`, which `yt script`'s brief prints, holding the promise frozen; `--rank` labels `curiosity` and `title_concrete` beside the score, which alone sorts) |
| `yt research` | the idea (`--paper REF`: one paper to explain, read first) | `dossier.json` (with `--paper`, its `paper` digest), `README.md`, `claims.csv`, `assets.csv`, `raw/`; with `--download`, `downloads/` | yes |
| `yt styles` | dossier | `styles.json`, `styles.md` | yes (only `--rich` writes the palette with hex codes; a film whose primary treatment names none, or with no `styles.json`, is in `design.py`'s house fallback palette: it builds and renders, `yt build` warns naming the cause, and the look cannot be approved at `yt keyframe` nor the film be READY or delivered until `yt styles --rich`, AUTO-155) |
| `yt script` | dossier, styles | `script.json`, `script.md`, `narration.txt`, `shotlist.csv` | yes (`yt script --revise 1.3,2.1`, `yt script --revise retime` or `--revise feedback` sends only those lines back, see below; the narrative rubric is in `ytstudio/narrative.py`: ten criteria of four labelled levels (AUTO-138: `voice` grades whether the narrator finds things out with the viewer, speaking to them, asking aloud what they are about to ask, flagging the step likeliest to lose them and letting a discovery land, and whether any humour serves the point; humour is allowed and never required, a Poor voice is a consider and never a reject on its own, and a voice flag on shot 0 of a drafted script breaks the sample and is asked again, so a lecture-register line reaches `revise-story-1` on its named shot; AUTO-135: `devices` grades whether each analogy, metaphor, prediction or thought experiment maps onto the mechanism and says where it breaks, the first house-strength row, so a Poor on it alone is a consider; AUTO-136: the hook names a curiosity trigger, a question, a paradox, an unexpected observation, an unresolved outcome or something someone knows, and `tension` grades whether each middle section puts more at risk than the one before; the plan declares `hook` {`trigger`, `gap`}, and every section carries `rehook` and `escalation`, empty only in section 1 and, for the escalation, the final payoff section), the coverage-shaped story review's schema (verdict reject, consider or recommend) and the plan review's. `--review` has `--reviewer-model`, never the author's family (`pool` or `sdk:<id>`: a reviewer from another maker on the gateway engine reads the plan, the pictures and the story while the author stays on its CLI, and `plan_review`, `review` and `picture` record it as `reviewer`), read the beat sheet before any section is drafted, about $0.50 more for the plan review (`spend.PLAN_REVIEW_USD`) and about $0.50 for the picture-check and its revision (`PICTURE_CHECK_USD`): `plan-review` reads the plan against the train, stakes, open loops, reveal setup, devices and ending and the `rule:`/`house:` pre-checks (AUTO-136 adds `rule:` an empty `hook.gap`, a middle section with no escalation or the same one as another, a rehook naming a question not in the ledger or already closed; `house:` section 2 with no rehook, and more than `REHOOK_EVERY_SECONDS` (180, a house prior) without one, a one-section plan asked to split, never beside the seven-minute line; AUTO-135 adds `rule:` a device with no `maps` or `breaks_at`, a thought experiment with no `framing`, and `house:` a prediction that names no scoring section or one past the last, or is scored before it is asked or before the main reveal), under a role of its own (accept, revise or reject, not the story review's verdicts); a reading whose fields contradict its verdict is asked once more as `plan-review-again`; revise sends it back once as `plan-2` and `plan-review-2`; a reject, or a second revise, exits 1 before a section is written and leaves no `script.json`, its reading and spend kept in `plan_review.json` until a run gets past the plan; `--report-only` records a revise and drafts on, but a reject still stops. There is no `--bar`: the verdict is the bar. The record is `script.json` `plan_review` and the `## Plan review` of `script.md`. `--review` then reads the drafted, polished script before the rules and the fact-check (the story review, AUTO-93), about $0.84 more for 10 minutes at opus, priced from the script's words (the writer's answers it sends back, `revise-story-1`, `redraft-02` and `revise-ledger-2`, are the author's and inside the script's base estimate): three samples on one prompt, `review-r1-s1`, `review-r1-s2`, `review-r1-s3`, each the reviewer's role with the rubric's levels in its own order; a sample whose answer breaks its rubric is asked once more (`review-r1-s2-again`); the grade per criterion and the verdict are the median of the valid samples, two that disagree on the verdict are a consider, and fewer than two valid samples (a failed call is a failed engine row, never a verdict) is `not_evidenced`; each sample names the trigger the first three spoken sentences pull (`first_minute.trigger`: one of the plan's five, `none`, or `not_heard` when no line is drafted, which over narration is asked again, as is `none` under a hook graded Good or better), the round keeps the label at least two valid samples give (a tie or `not_heard` is `not_evidenced`), `review.trigger` records it against the plan's, script.md shows `hook trigger: planned .., heard ..`, and a heard `none` alone files a whole-script hook flag (`hook-trigger-match`, carrying the samples' own whole-script hook problem and fix, and no `(differs)`) unresolved with `yt script --fresh --review`; a flag on a shot or section the script does not have breaks the sample and is asked again, and so does a flag whose `quote` is not words its shot or section says (whole words, whitespace and curly or straight quote marks aside, nothing matched loosely; empty for the whole script, and only empty on a silent shot or section) or whose `conflicts_with` names a shot that does not say its quote (AUTO-112): `revise-story-1` and `script.md` show each flag's quote and the line it contradicts, and a merged flag lists its samples' `quotes`. `cold-read` hears the narration alone, no research, plan or house in its prompt or system, and restates the premise, the unexplained terms and why each major claim matters (a term the narration says that the reader lists is a `rule:unexplained-term` flag for `revise-ledger-2` on a lay plan and a warning on any other, AUTO-138); `cold-read-answers` then shows the same reader the plan's questions and asks where they were answered, so they never prime the premise, and a question the film answers that the reader does not list is a `rule:unanswered-by-reader` flag for `revise-ledger-2` (AUTO-96, see "The question ledger"); `cold-read-match` matches its premise to the train, and a mismatch is a whole-script train flag; beside it the viewer quiz (`quiz`, `quiz-before`, `quiz-after`, AUTO-115) is recorded as a measure that gates nothing, and a gain under `QUIZ_GAIN_FLOOR` with facts missed is one warning (AUTO-162), see "The viewer quiz". A consider's binding flags go back: a shot's to `revise-story-1` (it may restructure the line, never add a fact), a section's (shot 0) to `redraft-02` (the section written again with the flags, how the section before ends and how the one after opens), and a flag on the plan or the whole script stays unresolved with `yt script --fresh --review`; the revision is read again with the review it revises (`review-r2-s1`.., `previous` required) and stands only when the samples call it better, up to `--rounds`; a revision that stands is cold-read again (`cold-read-r2`..), and the unresolved flags are the standing draft's. The record is `script.json` `review` (grades, verdict, samples, flags, revisions, redrafts, kept, unresolved, cold_read, previous_verdicts) and the `## Story review` of `script.md`; every verdict is appended to `work/review-log.jsonl` with `label: null` for the calibration set. A reject is an `error: story review: reject` finding doctor fails on, and narration.txt is still written with exit 0; `--gate` leaves narration.txt unwritten and exits 1 on a reject or `not_evidenced` (refused without `--review` or with `--revise`; script.json then records `gate: true`), and is to be turned on only when `python -m ytstudio.narrative calibrate tests/fixtures/narrative` (AUTO-97, see "Calibrating the story reviewer") shows the seeded-bad fixtures all fail, the good ones pass, the length audit passes, and work/review-log.jsonl holds at least 20 rows with a human `label`; `yt doctor` warns `gate on with N labelled verdicts (20 needed)` on a gated script.json while fewer than 20 labelled rows are in the log; `yt run` never passes it; `--report-only` reports the review and revises nothing; after the story review the same reviewer reads the drafted pictures without the words, `picture-check` and `revise-picture`, recorded as `picture`, see "Say it, show it") |
| `yt props` | script, styles (or phrases given) | `props/<kind>/<id>/`, `props.json` | yes (two decisions: `phrases`, the search phrases picked from the catalogue's vocabulary, and `keep`, the candidates worth building with, each with its `fit` note; the files are Poly Haven, all CC0) |
| `yt voice` | script | `voice/`, `voice.json`, `narration.vtt`, `.srt`; with `--draft`, only `voice-draft/` and `voice-draft.json` | yes (casting, retakes; the take review keeps `heard`, `voice.heard`'s digest of the takes it was asked about, which `yt qa` checks before it scores voiceover from it; the shots' delivery marks are rendered per `voice.MARK_SUPPORT`, see "Delivery reaches the voice") |
| `yt retime` | script, voice | `retime.json`, the screenplay's measured lengths (`script.json.bak` kept) | yes (accept, rerecord, rewrite, keep; a `rerecord` prints `yt voice --redo <labels>` and a `rewrite` prints `yt script --revise retime`) |
| `yt keyframe` | script, voice, styles, props (what `yt vfx` reads) | the vfx plan, the lead section in `vfx/vNN/`, `keyframe/keyframe.png`, `keyframe/frames.png`, `keyframe.json`, `keyframe.md` | yes (the vfx plan; one author agent, one attempt, no vision review; `--sheet`: the older composed frame) |
| `yt vfx` | script, voice, styles, props | `vfx/vNN/<section>.scene.xml`, `vfx/vNN/frames/` (and `frames/grid/`, the review's grid copies), `vfx.json`, `vfx.md`, `vfx.csv` | yes (one author agent per section, a frame review per round) |
| `yt sound` | script | `sound.json`, `sound.md`, `cues.csv`; with `--fetch`, `sound/` and `sound/index.json` | yes (spotting; `yt sound --review` listens back and fixes its own important and blocking findings, one revision of the cue's design fields each, two rounds at most, and `yt sound --review --report-only` only reports them; the review keeps `heard`, `sound.heard`'s digest of the cue sheet's design as the review left it (not the files `--fetch` adds), which `yt qa` checks before it scores sound and music from it; the fetch judges each candidate that passes the level checks against its cue, from its evidence and a spectrogram, and records the verdict in the cue's `judgement`; a fresh spot then anchors each cue to the spoken word it lands on, `anchor`, derived and never asked, outside `heard`, which `yt build` follows when a take is redone and reports in build.json's `cue_anchors`, a word it cannot find once `lost` at the sheet's `at` with a warning, AUTO-140) |
| `yt build` | all of the above | `scene.xml`, `build.json`, `vfx/vNN/<section>.include.scene.xml` | yes (coverage: `extend_previous`, `extend_next`, `image`, `footage` (a licensed clip placed as a silent `<video>`, its length read from the mp4 header, refused when shorter than the shot), `block`; captions, tail, blocking) |
| `yt render` | `scene.xml` | `video.mp4`, `render.json` (a still, a validation, an audio pass or a pilot writes `render-<job>.json`) | no |

**One spend record.** Every stage's JSON carries `usage`: `{cost_usd, model,
calls}` (`ytstudio/spend.py` names it once; an engine's token counters may ride
along). `engine` is not the spend's key: in `voice.json` it is the TTS engine,
in `render.json` the scene-render binary, in `assess.json` host facts; the
harness stages (`retime`, `pilot`, `gate`, `review`, `qa`) still also carry
their harness account there. `yt improve` reads cost through
`spend.cost_in`, which takes `usage.cost_usd`, else the older `cost_usd`, else
the older `engine.cost_usd`, so records written before still read. `sound.json`
and `build.json` are not on the shape yet. Every model has a price:
`spend.PRICES` is keyed by the ids the CLI reports in `modelUsage`, the harness
installs it into the engine on import, `$CLAUDE_PRICES` overrides a row, and a
call cut off by its timeout is billed from it. A short name (`sonnet`) is
`spend.price_for`'s to resolve, not a row: the engine's table holds no alias,
so a session on a short name keeps the CLI's own stop at 80% of its cap beside
the stream meter. A decision that raised is a `failed` row in the harness report with
what the engine spent, so the record bills it; `yt doctor` warns when a
record's `by_model` names an id neither engine priced (a Codex id is priced
from `codex_engine.PRICES` and `$CODEX_PRICES`); a row the gateway engine
priced itself (`cost_gateway_calls`, `cost_generation_calls`) is not warned
about, `$GATEWAY_PRICES` prices a gateway id too, a claude call booked through
the gateway at the CLI's own figure (`cost_cli_calls`) is warned about, every row of the
reviewer pool (`reviewers.POOL`, AUTO-125) is a row of `spend.PRICES` at its
pinned upper bound (no cache discount), and where
`gateway/node_modules` exists doctor shows a "gateway engine" block (Node,
the pinned `ai`, the key by name). A Vercel AI Gateway id is its direct
name (`ytstudio/models.py`, AUTO-120): `anthropic/claude-opus-5.5`,
`claude-code/anthropic/claude-haiku-4.5` and `openai/gpt-5` take the row,
the family and the Claude check of `claude-opus-5-5`, `claude-haiku-4-5` and
`gpt-5` when no row names the gateway id itself, while a name with no
provider is looked up exactly as before. A row is its own provider's:
Anthropic's rows price `anthropic/` ids, Codex's `openai/` ones, and a Claude
model from another provider (`bedrock/claude-opus-5`) has no row, its rate
being that provider's. A 1M-token context (`...[1m]`) has the family of its
model but no row on either route, as it may bill above the base rate, so its
session keeps the CLI's budget stop and `yt doctor` warns until a row names
it; a virtual model (`vmc/<slug>`) has no row, and any `claude-code/` id is
dropped by Codex as a Claude one. A quiet doctor means `spend.price_for`
has a row, which for a short name (`opus`, `anthropic/opus`) is an
estimate's: the engine's table holds no alias, so a cut-off call on one is
booked at $0. The decision cache names the model as passed, so a gateway id is a
new question and an alias passed on either route (`--model opus`) reuses
the same decisions, which is meant: it is the same model.

**What `yt build` does with a bad number.** A malformed editor or
sound-designer number (`tail_seconds`, a cue's `fade_in`, `fade_out` or
`gain_db`, a `level` written in words only) is replaced by its default and
also recorded as a `warning:` finding in `build.json`, naming the field, the
cue and the value used. A cue's `fade_in` and `fade_out` (seconds, from the
sheet) set the track's fades; a file longer than its window with no fade out
is cut hard mid-sound, and that is a `warning:` naming the cue. The caption track places the same
`design.caption_cues` as the sidecar, from the `voice.json` takes, so
burned-in and sidecar captions are the same cues. The placeholder card draws
with the design's ink and label size, no literal colour. `yt build` prints
"N include(s) for M shots" and files an `error:` finding, with the run under
the manifest's `split_worlds` key, when one symbol's run of consecutive shots
in a section is left in more than one include: the build is not publishable
and the scene is not written without `--force`. After building, `yt build`
checks each world against its proof frame before the encode, the gate and QA
are paid for (`build.proof_check`, recorded as `proof_check` in build.json):
the built document is rendered at each world's proof-frame time by the still
renderer (cached) on the host the look check probed, or for a path-traced
film or one with a medium on the host an encode would use, one kit render
per host at a time as the vfx gate's proof frames go, and the stage bands
are compared as `yt qa` does; a mismatch is an `error:` finding naming the
world, the time and the difference, the build is not publishable, and the
film's frame is kept under `build/proof/`. With no render host reachable
the worlds are `not checked`, a frame that did not come back is `unread`,
and a `warning:` says so either way.

`yt run` (`yt run --title X`) walks research, styles, script, props,
voice, retime, keyframe, vfx, sound, build, render in that order -- `yt
props` after the script so the vfx brief can list real assets, `yt retime`
after voice so nothing is authored against the screenplay's guess once the
read is measured (a `rerecord` verdict records those takes again and
retimes once more; `rewrite`, or a read sent back twice, stops the run,
and the hint names `yt script --revise retime` and `yt run --redo
voice,retime` after it, which records only the changed lines),
`yt keyframe` right after it so the look is a section of the film timed to
that read, and the run STOPS before `yt vfx` while the look awaits the
operator (`yt keyframe --approve`, then `yt run` again; a rejected look is
authored again with the notes; `--skip keyframe` runs without a look) --
and skips a stage whose output exists unless it is named in
`--redo` (`--redo all` repeats everything, `--skip` leaves stages out,
`--quick` drops the deliberation, polish, fact-check, plan review and
story review (`yt script --review`, never with `--gate`) and listen-back passes and renders at draft). It always passes `--download` to `yt research` (`--quick` keeps it; AUTO-143), so build and vfx have the vetted pictures and clips on disk, and `--fetch --synth` to `yt sound`, so
the sheet never leaves the stage with holes (the synthesis program the agent
writes runs on this workstation, NumPy only and no ffmpeg; its WAVs are
levelled on a render host like the fetched ones), and runs that stage beside
`yt keyframe` and `yt vfx` on its own thread (only `yt build` reads the cue
sheet), joining it before the build and failing the run there if it failed,
a walk that stops earlier still reading its code (`cli.Beside`, a daemon
thread: a Ctrl-C ends the run at once and the sound stage with it, since the
terminal's interrupt reaches the stage's engine through the process group,
where a `kill -INT` of `yt` alone leaves that engine to finish on its own;
an exit on that thread counts with its code; `core.write_json` moves the
sheet into place whole, so an interrupted write leaves the old one);
`cli.run_extras` lists what each stage gets and `cli.stage_argv` builds the
argv (`yt render` is the only stage handed no model flags, `cli.NO_MODEL`;
`yt props` asks a model and takes them like the rest). `yt run --dry-run`
prints the stages the walk would take from the project as it stands
(`cli.run_plan`: the same skip, done and look judgement, `cli.stage_state`),
one row per stage priced by `spend.stage_estimates` (measured, like or
assumed) and the total, and runs nothing (the project is planted, as for any
stage). `yt run --budget-usd N` caps the
whole run: a stage whose estimate plus what the run's stages have reported
does not fit is not started, the refusal names the estimate and the run exits
`retime.UNSETTLED` (3); sound counts at its estimate while it runs beside,
and the voice re-record a `rerecord` verdict asks for is capped like any
stage; `yt vfx` is handed what is left as `--stage-budget-usd`. The plan and the
cap price the stages in the one order the walk reaches them
(`cli.walk_order`, sound before the keyframe), since an assumed vfx pass
folds in what follows it.

**The look-lock.** `yt keyframe` takes the look from the film itself, so
nothing made for it is thrown away. It builds `yt vfx`'s own planner
(`vfx.planner_from_args`), plans every section (cached; `yt vfx` reuses the
plan) and authors the lead section (`--section`, default the one named last,
else 1) into `vfx/vNN/`: the mechanical gate judges it (the program built
twice, the engine's validation, proof frames at every shot, the frame,
legibility and geometry rules), a revision goes back only for what the gate
finds, and no vision review is paid for before the operator has seen it
(`VfxPlanner(lead=N)`, record "12-lead"). A look the gate failed (errors, no
frame) cannot be approved (`keyframe.approvable`) and `yt run` authors it
again. The frame
showing the most becomes `keyframe/keyframe.png` and every proof frame goes
on `keyframe/frames.png`. The operator runs `yt keyframe --approve` (tied to
the section document's digest), or `--reject "notes"`, which puts the notes
at the top of the lead section's brief next time; `--fresh` plans and
authors it again. `--approve` refuses a record whose digests no longer match
the files (the document or program changed after the record was written, a
stopped run): run `yt keyframe` again before approving. Once approved, `yt vfx` carries that section forward
(`vfx.look_section`, `carried_from`, $0) and every other author's brief and
review name its program as the look -- palette and type constants,
the helpers that build labels, formulas and recurring objects, the camera
language -- with `fidelity` scored against it. The approved bytes,
`keyframe/look.py`, are copied into every section workspace as `look.py`
beside `srkit.py` (`sdkkit.install(look=)`, again on every new version and
`--relook`; not into a later look pass's own version, which makes the next
look and builds on none); the brief says `from look import ...` and redefine nothing it
provides, importing it writes no document (srkit's `save` writes nothing during
an import; a helper of the look's that saves writes when the program calls it),
and the build records its hash in the document's provenance. An
author reads `keyframe/` and its own directory, never another version's. The look's section
enters the film in two places only: the first animation after the approval,
and `yt vfx --relook`, which carries it into an existing animation and
authors every other section again on the same plan (a broken attempt keeps
the section it replaces; no score contest across looks). After that the
active version is the film: `--sections N` re-authors the look's section like
any other (a sound re-authoring then stands for the look, `reauthored_look`),
and a broken attempt at it keeps the look. `yt vfx --fresh` replans and
authors everything; `yt keyframe --fresh` replans and forgets the authoring
made under the old plan. An approved look is the operator's: a re-run
downstream of voice or retime keeps it, and only `--fresh`, another
`--section` or another `--width/--height` replaces it. `yt run` stops before
vfx while a look is pending (and names the `--redo` that goes on), authors a
failed or rejected look again, and runs `yt vfx --relook` (then build and
render) when the approved look is not in the animation (`keyframe.film_carries_look`).
`yt doctor` warns when the animation was
authored without an approved look or does not hold it, and names
`yt vfx --relook` rather than `--fresh`; a failed look pass is a warning
whose fix is `yt keyframe`. Measured on the Euler run
(2026-10-20): the separate style frame cost 51 min and $20 for a frame the
film never used. `--sheet` still composes that frame (elements, rounds of
review, `--rounds`, `--bar`) when a look is wanted before any section.

**Where a finding is (AUTO-110).** The frame reviewers (`yt vfx`'s, and
`yt keyframe --sheet`'s) get, beside each frame, a grid copy made at review
time: `vfx/vNN/frames/grid/<frame>.png`, `keyframe/grid/keyframe.png`, the
same picture with a labelled 8x6 grid (`framecheck.grid_overlay`, columns
A-H, rows 1-6). It is a ruler, not evidence and not a composition rule: the
reviewer judges the clean frame, a round keeps only the clean frames, and
nothing else reads the copies. A finding may name its `cells` (checked
against `framecheck.GRID_CELLS` like `category`, so a bad name fails the
review); a whole-frame finding names none. The next round's brief gives the
cells, their pixel rectangle and, for vfx, the object3D ids in them from one
eval of that frame on the host (`Workspace.cell_objects`; 2D text has no
box), or why there are none; the finding keeps them as `objects`. With no
Pillow, or a frame it cannot read, no copy is made and the reviewer is not
told of one; an eval that fails or a host that refuses (`YT_NO_REMOTE`)
leaves the review standing, with `objects` null and the reason.

**The research's assets.** `yt research` fills its shot list from every
catalogue in `--asset-sources` (default all of `assets.SOURCES`): NASA
(stills, footage, audio), Openverse images and sound, Commons (stills,
footage, sound, meshes), the Internet Archive, the RCSB PDB (structures,
CC0), open-access papers in PMC (the PDF, captioned figures, supplementary
movies), the open-access papers the research itself cites, and news
(Wikinews under CC BY; the rest of the press as `reference_only` records,
cited and never downloaded). Each shot's shortlist holds the best relevant
hit of each kind before more stills, the subject's own searches are one need
searched once, and the vetting decision keeps at most `--per-shot` of each
kind per shot. `kind` is one of `assets.KINDS`. A cached search made with
other catalogues, another licence policy or an older searcher
(`Researcher.ASSET_SEARCH`) is searched again, and the outline with it.
`--download` writes `downloads/` and gives each asset in `dossier.json` its
`file` (an STL becomes OBJ, the original kept as `source_file`); that `file`
is what the vfx and keyframe briefs offer the authors, and `yt build` places
the licensed images and clips. `yt run` always passes it (AUTO-143), and
`yt doctor` warns in `assets`, naming `yt research --download`, when vetted
image or video assets have no `ok` row in `raw/08-downloads.json`: never
downloaded when there is no manifest, none succeeded when its rows are all
deferred or failed.

**Research scales to the film's length.** `yt research` reads the idea's
stated runtime ("60 seconds", "8-12 min"). A film under ten minutes shrinks the
question depth, the shot count and the claim ceiling linearly (a 60 s film:
2 questions, 6 shots, about 40 claims); ten minutes or more, or no stated
length, plans as before. `--depth` or `--shots` given on the command line
always wins.

**Research decides in levels.** After the web passes, the decisions run on
one pool, `--jobs` wide, in dependency order: claims, sources and the shot
list need only the notes and run at once; the asset search (the catalogues,
then the vetting decision) and the gap audit run next; the outline last.
The stage names under `raw/` and `decisions/research/` are the same as when
they ran in series, so an older dossier replays, and `Dossier.save` lands a
whole file or none.

**A paper film reads the paper first.** `yt research --paper REF` (or `yt run
--paper REF`; a DOI, an arXiv id, a URL or a local PDF) plans the `paper`
scope. `raw/00-paper.json` is the paper as read, by WebFetch or, for a local
file, Read; it fails unless the agent's own tool events show the paper opened,
and a local path is named `file:<basename>` in the dossier and every prompt
(idea.json keeps the absolute path). A dossier cached for another paper, or
for none, is researched again from `01-questions` on, never replayed. The
questions ask after the problem's cost, the prior state of the art, the
reception, the consequences and the limitations; the paper is the first source
and its own claims cite it. `06b-paper` (and at most `06b-paper-again`) decides
the dossier's `paper`: a citation and six items, problem, contribution,
significance, implications, consequences and limitations, each with the claims
it rests on quoted from the dossier, and the contribution on one sourced to the
paper. `yt script` then gives the plan `framing` (each item's sentence, claims
and section) and `mechanism_section`; its pre-checks want the problem, the
significance and the implications before the mechanism and the limitations
said on air, and a misplaced item is a `warning: paper framing:` finding doctor reports. A
film with no paper is asked exactly what it was before. Under the codex engine
`--paper` exits 2: codex reports no WebFetch or Read events. The paper pass is
priced at `spend.PAPER_USD` ($1.00, assumed until a record measures it).

**The story review binds voice under `--gate`.** `yt script --review` reads the screenplay before a take is recorded; its verdict is in script.json (`review`) and its reject is an `error:` finding that `yt doctor` fails on with the command that fixes it (`yt script --fresh --review --notes ...`). Without `--gate` narration.txt is still written and the stage exits 0, and the build does not carry the reject to QA: the reviewer is a prior until it is calibrated (judging 9 and 10). With `--gate` a reject or a `not_evidenced` review leaves narration.txt unwritten and exits 1. Every verdict is appended to `work/review-log.jsonl`, and doctor names it as evidence to copy out, since work/ is disposable.

**Delivery reaches the voice (AUTO-139).** A shot may carry `emphasis` (the word to stress in each sentence, in sentence order), `pauses` (the words a beat lands just before) and `pronounce` (`{term, say}`, `say` one hyphenated token with the stressed syllable in capitals); none is required, so an older screenplay loads. A polish or revise rewrite drops a mark whose word it took out (`script.prune_marks`) and names it as `marks_dropped` in its applied row (the polish's rows are script.json `polish.applied`); `revise-rules` rewrites a shot's marks only for a `rule:marks` flag, an empty list leaving them alone. `yt voice` renders the marks per `voice.MARK_SUPPORT` (espeak: pronounce, pause, emphasis as SSML with `-m`, speed; elevenlabs: pronounce, speed, settings; kokoro: pronounce, speed; chatterbox: pronounce; stub: nothing): a pronouncer is sent lowercased (`shroh-ding-er`, never its capitals), one word of the line one word sent, and what an engine cannot take is the take's `delivery.recorded_only`. The take's `spoken` (what was sent; "" when it is the line) is its cache key; an unmarked take keeps today's key, argv and audio. Captions, cps and caption timing are always from `text`: ElevenLabs' alignment of the sent text is mapped back word by word (`voice.caption_words`), or estimated from the line with `caption_mismatch` recorded. The casting director's `delivery` rows (by heading, or a section number) may give a section its own `speed` and, for elevenlabs, `settings`; they layer cast, then section, then a named retake, a speed replacing rather than multiplying, and ElevenLabs makes one call per distinct settings value in film order (no section settings, today's calls). A row naming no section is voice.json `delivery.unmatched`; `delivery` counts applied and recorded-only controls by kind. `yt voice --draft` is direction 29's free read: kokoro, else espeak-ng (elevenlabs and stub refused), no casting and no model, each sentence read with its marks and its trimmed length measured into `voice-draft/` and `voice-draft.json` (`script_sha`, per sentence `seconds`/`words`/`wpm`, drift, findings: over 10 s fails, a numeral said again warns; `listened` "not evidenced"). An elevenlabs run warns naming rule 29 when the draft is missing or stale and stops before casting on a current draft with a fail unless `--accept-draft`; voice.json `draft` keeps `{status, fails}`. Local engines and `--dry-run` are not gated. A pre-marks screenplay (no shot carries a mark) is asked the casting and retake questions as before, so its kept cast serves and its calls and bill are today's, and its section notes stay notes; a marked one is cast again once (a model call, and re-billed takes where the voice, speed or settings change): pin `--voice`, cap with `--max-chars` and compare voice.json `casting` before and after, since `--dry-run` does not cast. The first `yt script` after the marks drafts every section again (the schema changed), which makes the paid voice stale.

**The viewer quiz is a measure, not a bar.** The cold reader (judging 14) checks that the story gets through; the viewer quiz (AUTO-115, after Code2Video's TeachQuiz) checks whether the facts do. Under `--review` its author (`quiz`, the reviewer's model under a role of its own, no house) is shown the train, the misconception, the plan's questions the film answers (one left open on purpose has no answer to key) and the research, and never sees the narration, since a quiz written from the words would test them against themselves; it writes 4 to 8 multiple-choice items, each with four distinct options, a keyed letter and where it came from, and a set with a repeated id, a repeated or empty option or a key outside A to D is asked again once (`quiz-again`). The cold reader then takes it twice, shown the items without their key or source: `quiz-before`, told it has heard nothing yet and to say unsure rather than answer from expertise, and `quiz-after`, with the narration laid out as in `cold-read`. Code counts the letters that match the key, unweighted, and never reads the narration's words. The items and the before are written once; a revision that stands is quizzed again as `quiz-after-r2`. The record is script.json `review.cold_read.quiz` (items, before, after, gain, missed_after with each missed item's choice and key, round) and a `Viewer quiz` line in the `## Story review` of `script.md`; each failed call is a line in `quiz_failed` and the review stands. The gain gates nothing: it is no verdict, flag, unresolved item, `--gate` input or exit code, and a model's "before" is a prior, not a lay viewer's. A gain under `QUIZ_GAIN_FLOOR` (1 question, house, NO STANDARD, a prior) with items still missed after hearing is one `warning: story review: viewer quiz gain ...` finding (AUTO-162, `quiz_findings`, read from the quiz of the draft that stands), naming each missed item's question and the fact its key gives, with `yt script --review --notes "teach: <fact>"`; it reaches script.md's Findings and a `yt doctor` warn under script, and no revise-story, redraft or revise-ledger prompt. A ceiling score (nothing missed), a failed before or after (no gain; a failed `quiz-after-r2` drops round 1's measure too) or a run without `--review` raises none, and `yt script --revise` asks no quiz, so a warning it keeps measured the narration the `--review` run left behind. Its cost is in the story review's estimate (`spend.QUIZ_TOKENS`).

**Calibrating the story reviewer.** `--gate` trusts the reviewer, so the reviewer is checked first (judging 9, 10 and 11). `tests/fixtures/narrative/` is the floor: one case per seeded fault (a list-shaped plan, a topic-only train, an empty payoff, an AND chain, a reveal before its constraint, a recap ending, a hook that enumerates its conclusions, a middle whose stakes stay flat, an opening whose first three sentences pull no curiosity trigger, an analogy that maps a comet's tail onto its path, the good film read as a lecture (`lecture-voice`: voice), an aside that serves no claim (`aside-joke`: voice), a script whose premise a cold reader could not restate, terms a lay audience is never given), each `expected.json` naming the rubric criterion it fails and the best verdict a correct reviewer may give it, a good plan and script that expects recommend, and a second good script whose one joke makes the point (`claim-joke`: recommend, voice no lower than Good), which must not be graded down for it: its `expected.json` sets `at_least` (`{"voice": "Good"}`) and the reason a failure line gives for it (`at_least_why`), and the run fails a reviewer that grades voice below Good or flags it there, since a recommend may carry a Fair. The last two faults have no rubric criterion: judging 14 puts them in the cold read, and the stage files the cold read's mismatch without touching the story verdict, so their `expected.json` says `"story": false` and names what the cold read must find (the mismatch; the terms, each with the names a reader may give it, such as `IMF`), and the story review is read on them but not judged. The good script and the recap ending each have `padded.json`, the same script at about twice the words: the good pair may be read at the ceiling (recommend, every grade Excellent), where the longer copy cannot win, and the consider-level one leaves it room. `python -m ytstudio.narrative calibrate tests/fixtures/narrative [--model M --reviewer-model R --budget-usd 20 --work work]` (a module entry point, not a `yt` stage) reads every case with the script stage's own readers on the real models: `plan-review` where the case names a plan verdict, the three samples `review-r1-s1..s3` on every case (over the plan alone for a case with no script: the prompt says no section is drafted, ties every flag to shot 0 and grades voice from the plan, never flagged, since no line exists to name; a voice flag on shot 0 there is not asked again), and `cold-read` with `cold-read-match` where there is narration; each padded script is read again and each sample paired with its twin. Nothing is revised, and nothing is appended to `work/review-log.jsonl`: the fixtures are the floor, never the channel's scripts. It prints one row per case (fixture, criterion, expected, got, plan, cold read, pass or fail, and under a failing row what the reviewer missed: a verdict better than the case allows, the criterion graded above Fair, no flag on it, a plan review that accepts a bad plan, a cold read that misses the mismatch or the terms, a round that hears a trigger other than the case's `trigger`, a criterion graded below the case's `at_least` or flagged there), then the length audit (it fails when the padded script wins more than 60% of its pairs, judged by verdict and then by criteria graded up or down, never by a number; it names the pairs read at the ceiling and fails when every pair was, since it could not have seen a bias), then whether `--gate` may be turned on, with how many of the labelled rows carry the reviewer's own verdict (judging 9 asks for the agreement to be reported; it sets no bar). The condition is: the seeded-bad fixtures all fail, the good ones pass, the length audit passes, and work/review-log.jsonl holds at least 20 rows with a human `label`. A label is one of reject, consider or recommend, set by a person who read that script; case does not matter and coverage's Pass is read as reject, and doctor's review-log note counts a label that is no verdict, with the reviewer's agreement. It exits 0 when the reviewer passes, 1 when it does not. Readings are cached under `work/calibration/<case>/decisions/script/` and the report is `work/calibration/calibration.json`; a live run costs about $1 a case at opus, about $18 for the shipped set with its two padded reads (TO VERIFY: `CALIBRATE_USD_PER_CASE` is an estimate), and stops reading cases at `--budget-usd`. `yt doctor` warns `gate on with N labelled verdicts (20 needed)` for a script.json written with `--gate` while the log holds fewer.

**Calibrating the QA judge** (AUTO-156). `yt qa`'s READY is the bar applied to a model's median scores, so the judge is checked on labelled films. `tests/fixtures/qa/<case>/` holds what `qa.score_evidence` reads (build.json, script.json, voice.json, samples.json, 480 px frames and bursts) and `expected.json` (`label` owner or seeded, `judge_only`, `verdict` READY or NOT_READY, `under_bar`, `over_bar`, `must_find` [{`t_range`, `owners`}], `base`, `drop`): `good`, `good-b`, and seeded faults no mechanical check sees, `slide-drift`, `wall-of-text`, `band-violation`, `illegible-type`, `placeholder-moving` (build.placeholders empty) and `tells-not-shows` (three shots' narration rewritten to describe processes the frames never show, frames untouched). `python -m ytstudio.qa calibrate tests/fixtures/qa [--model sonnet --work work --budget-usd 20 --fresh --quiet]` scores each case through score_evidence in `work/calibration-qa/case-<hash>/` (`qa.case_key`, never the case's name, so the judge cannot read the label; the report's `cases[].dir` maps it back, and the last report is removed while the cases are scored), with the mechanical findings in the prompt as in a pass, and judges the raw card (`qa.judge_case`: the side categories' medians, the judge's own blocking claims and only findings two readings agree on, a failed merge failing the case; no cap, no mechanical blocker, no silence rule); it prints a row a case and the false positives and negatives per category, writes `work/calibration-qa/report.json` (with `fixture_set`, the digest of every file of the set it ran) and exits 0 only when every case passes and at least one is labelled `owner`. The committed good cases are stand-ins drawn by `scripts/seed_qa_fixtures.py` (deterministic), so the set does not pass until `python -m ytstudio.qa capture-fixture <pass> <case>` cuts one of the owner's own passes down (8 samples, 3 burst frames each; it empties the case it writes into but for expected.json), the owner writes its expected.json, and the seed runs again with `--base`. `qa.json` records `judge_calibration` (`fixtures`: a passing report at this model with this judge's `qa.judge_digest` on the shipped set's `fixture_set`, and never on a `--listen` or `--watch` pass, whose judge the fixtures do not run; `labelled`: 0) and `yt qa` prints `judge: fixture-calibrated (<model>); 0 owner-labelled films` or `judge: not calibrated against the QA fixtures (<why>); 0 owner-labelled films` under its verdict line. It gates nothing: READY, `yt doctor` and `yt improve` are unchanged. A card that edits qa.ROLE, the categories, the scorecard schema or the prompt changes the digest and must run calibrate again and add a fixture case for what it changes.

**The fact-check and the story review bind the picture.** `yt script --verify` writes its flags into script.json (`check`); the story review its flags (`review.flags`) and `yt script --review`'s picture-check the flags it left standing (`picture.flags`). Their blocker and important flags reach the people who draw the film, verbatim (`vfx.picture_constraints`): the vfx planner, each section's brief and reviewer (the whole-film flags plus that section's shots), and the keyframe brief, the fact-check's under `## Fact-check constraints` and the other two under `## Story constraints` (`vfx.constraint_lines`); a minor one binds nothing. A story flag on a section and no shot (n 0, `section` k) binds that section's shots only, printed as `section k`. A claim the narration avoids can still be shown wrong by a picture, and a claim no picture shows is not told.

**Say it, show it (AUTO-98).** With `--review`, after drafting, the polish and the story review and before the narration rules, the reviewer's model reads the screenplay's pictures with the narration taken away (`picture-check`, under `narrative.picture_role()` and `narrative.PICTURE_SCHEMA`): each shot's `visual`, `shows` and `on_screen_text`, and the plan's beat for each section, never a line of narration (direction 4, Fong's picture-off test). It writes each section's claim from the pictures alone, then says whether that is the beat and where the thread is lost; a section left unread is asked once more (`picture-check-again`) and then recorded as a `note:`. Per shot it reports a figure of 1,000 or more with no comparison and no picture (attention 15), a mechanism the shot says it demonstrates that its picture does not, a list with no count or labels (attention 22), and a change over time the shot's `shows` says it demonstrates while its `visual` is one fixed state (`static_process`, direction 7 and 9, AUTO-144; a mechanism drawn but frozen is this and not an unshown claim, which is for one the picture does not show at all). Each is a flag (`script.picture_flags`; important, an unsignposted list minor) answered by one `revise-picture` decision, the only revision that may change a shot's `visual`, `shows` and supers (`PICTURE_REVISE_SCHEMA`); every other revise ignores them, so the fact-check's still rewrites the narration only. What stands (a flag left unanswered or unchanged, every flag when `revise-picture` failed or under `--report-only`) is script.json's `picture.flags`, a `warning: picture:...` in `findings` and the `## Picture check` of `script.md`. A flag the writer kept with its reason is `picture.kept` and a `note:`, and binds nothing: the reviewer read no narration, so a figure compared aloud (attention 15 accepts the comparison in the same or the next sentence) or a claim the line carries looks bare to it, and the writer, who sees both, is the one who answers. A kept `picture:static-process` is the exception (`script.PICTURE_UNKEPT`): a line cannot make a still picture move, so it stands with the writer's reason and binds as before. A flag on a shot `revise-picture` changed in any way (its line, a super, its picture) counts as answered; the pictures are not read a second time, so a section flag can go while the section still misses its beat. `yt script --revise` moves the picture flags of a shot it changes to `picture.stale`, out of what binds vfx, and drops their findings lines. A failed check is a `warning:` and changes nothing. A super that prints the line is counted, not judged: `rule:wall-of-text` below. No treatment, colour or layout is named in the rule or the prompt; motion is measured from frames at vfx and qa, not here.

**A process its picture freezes (AUTO-144).** A standing `picture:static-process` flag binds the vfx planner like any picture flag; on top of that, an effect of a type that keeps the picture still (`vfx.STATIC_TYPES`: text-and-callout, photo-composite, stock-treatment) on a flagged shot (`vfx.static_cover`) is asked once more as `plan-effects-<slug>-again`, and a second still answer stands as the section plan's `warnings` (vfx.json `sections[i].warnings`, a `warning:` line under the section in vfx.md, a doctor note); a cached `11-effects` gets them recomputed without asking. After authoring, the motion audit asks the reviewer's model (`--reviewer-model`, its own harness, `reach.MOTION_ROLE`) `reach-motion` under `reach.MOTION_SCHEMA`: per authored effect, what it must convey and the rows of its document that change something during its shots (`reach.motion_slice`: a motion or simulation element, its target, property and window, key times shifted by every ancestor's `start`, a world's phase by its shots' windows), and whether the viewer watches the change. `reach.motion_problems` checks the answer against those rows (an id unknown, missing or twice answered; a change named that is not in the effect's rows; none named; only one fade on one target, while reveals of two or more targets at distinct times are a step build) and asks once more as `reach-motion-again`; still unreadable, or a failed reviewer, is `motion.reason` with no static process, not evidenced; a failed reviewer is also `motion.failed`, and the next `yt vfx` asks again. plan-reach, its score and its model are unchanged; 14-reach and vfx.json `reach` gain `motion` (`answer`, `static`, `reason`), and a cached 14-reach without it gets only the motion audit (an unnamed element is `<tag>#(unnamed N)`, one target for all its changes; a constant `animate`/`morph` and a constraint, pin, beatGrid or cue are no row; a textAnimator's fade preset, or opacity alone, is a fade). Each static process is the refinement plan's `REQUIRED:` work ahead of any upgrade (when 15-plan is made: on an existing project the cached plan stands until `--sections` or `yt improve` makes it stale, so its text lags the motion audit while doctor, vfx.md and QA already show it), `### Static processes` in vfx.md, `static_processes` in `reach.summary` (QA's reach context), a count on the CLI line, and a `yt doctor` warn (owner vfx) whose fix is `yt vfx --sections N`, N from its effect's section heading and `authored[].n` (no match: plain `yt vfx`); `yt improve` routes that warn to section N alone (`section_n`), and that run redoes the motion audit. Nothing fails `yt vfx`, build, gate or the lane on it, and no treatment is demanded: a flat step build passes.

**The screenplay is a story brief (AUTO-90).** `yt script`'s plan names the
film's `promise` and `payoff`, its `train` (who wants what, what is in the
way), the `stakes` and `viewer_stake`, the `misconception` it breaks (cited
as `[M1]` from the research block, which numbers the dossier's sourced
misconceptions and names its identifiable case), the `audience` (lay,
enthusiast, expert), an optional `persona` (AUTO-158: the narrator's
`temperament` for this film, free text, and `why` the subject calls for it;
the house format's temperament when it names one), told to every section
writer as one line beside the audience and to `yt voice`'s casting in its
Film brief (absent, both prompts are as before; one given but left empty is
a `house:` pre-check for the plan reviewer), the `reveal` and the section
that constrains it, a question ledger (`questions`, each opened in one
section and answered in the same section or a later one, or marked left
open, `closed_in` 0, with `left_open_why`) and the `ending` (`payoff_line`,
`callback`, `next_film`). Each section names the questions it `opens` and
`closes`, its `connective` to the one before (and, but, therefore, because),
a `rehook`, an `escalation` and an `anchor_visual`, and may declare
`devices` (AUTO-135): each a `kind` (analogy, visual_metaphor, prediction,
thought_experiment), the `device`, what it `maps` (which familiar part
stands for which part of the mechanism), where it `breaks_at`, its `framing`
("imagine..." for a thought experiment) and, for a prediction, the section
whose reveal it is `scored_in`. The section's writer is told to use each
device and say where it breaks; the scoring section is told to say whether
the guess was right. A kind off the list is asked again once, then recorded
as a `note:` finding and the device ignored; script.md prints the devices on
the story line. With `--outline` the outline keeps its sections and these
fields come from one `plan-fields` decision (an answer with another number of
sections is asked once more as `plan-fields-retry`; wrong twice, both are
dropped and the stage fails). Every shot names its `function`
(hook, context, setup, reveal, rehook, payoff, handoff) and `shows`, what its
picture must demonstrate of its line. An audience, connective or function
outside its list is kept as written and recorded in script.json's
`findings`; an older script.json without these fields still loads.
Downstream: build.json's shot rows carry `function` and the editor is handed
the promise, the payoff line and the shot that pays it (its `tail_seconds`
holds after the film's last shot, so it is the payoff's landing only when
the payoff ends the film, and the prompt says which); `yt pilot` takes a
beat's hook, rehook and payoff traits from the function (the last shot
stands for the payoff only when none is marked, with a note); `yt qa`
samples every rehook and payoff shot beside the hook and the ending; each vfx
section author is told what each shot `shows`.

**The question ledger holds across sections (AUTO-96).** Sections drafted
side by side cannot read each other, so `script.QuestionLedger` (from the
plan's `questions` and each section's `opens` and `closes`) briefs every
`write-NN` prompt, `--jobs 4` and `--jobs 1` alike: "You open Q2 (...);
section 4 closes it", "You close Q1 (...), opened in section 1", or that a
question is left open on purpose. The writer names the shot that raises and
answers each (`opens_at`, `closes_at`: the shot's number inside its section,
the only number it knows); the stage puts the question on that shot (its
`opens`, `closes`), and a number past the section's shots is a `note:`.
After drafting, `script.question_findings` measures the ledger in the
rules' flag shape (`script.LEDGER_RULES`, bounds in `script.BOUNDS`):
`rule:open-loop` (important: a plan question, not left open on purpose, no
shot answers; on the last shot of the section the plan answers it in),
`rule:closed-unopened` (important: an answer before any shot raised the
question, or to one the plan lacks), `rule:raises-nothing` (minor: a section
the plan's ledger, its questions' `opened_in`/`closed_in` and the section's
`opens`/`closes`, has open and close nothing) and, with `--review`,
`rule:unanswered-by-reader` (important: a question a shot says it answers
that the cold reader's `cold-read-answers` did not list; one no shot answers
is the open loop alone) and `rule:unexplained-term` (AUTO-138: a term the
cold reader lists that a line says, on the first line that says it, whole
words, case folded, the flag keeping the reader's spelling; important on a lay plan, minor, a warning never sent,
on an enthusiast, expert or unset audience; a term no line says stays only
in script.md). The important flags go to
the writer with the figures said twice in one `revise-ledger` decision,
and the reader's (unanswered questions and lay terms) in
`revise-ledger-2` after the story review. For each line flagged about a
question the writer names the flagged questions the line answers as it
leaves it (`closes`, `script.LEDGER_REVISE_SCHEMA`): that marks the line or
takes its mark off, and the ledger is measured again; a changed line that
names none answers nothing, since the same line can carry a figure's flag.
A reader's flag is answered by a changed line that names its question, and
a term's by a changed line alone (a definition keeps the word, and the
cold read is not asked again); one kept carries its reason. Both hold for
that line only: a section the story review writes again in its place is
measured afresh, and a redraft's
`note:` lines join the findings only when its round stands. What stands
is a `warning:` in script.json's `findings` and blocks nothing; under
`--report-only` the ledger's flags are recorded, not revised. What counts as
an answer is the writer's mark and the reader's report, never code's reading
of a line. script.json's `questions` is the plan's ledger with `opened_at`
and `closed_at`, the labels of the first shots that raise and answer each
("" where none does), for qa and the retention curve; `script.md`'s question
ledger prints them. Whether a setup is paid or too many loops are open is
judged under the rubric's `open_loops` criterion (`ytstudio/narrative.py`),
not counted.

**The narration is measured against rules (AUTO-94).** `script.RULES` are
counted from the text, each against a bound in `script.BOUNDS` that names
its corpus rule (`docs/narrative/`) and its strength; none judges by a word
list, and no folklore threshold or vendor hook window is used. The error
tier: `rule:filler`, `rule:sentence`. The important tier:
`rule:unspoken-number`, `rule:legibility` (the older checks; their
script.json keys stay), `rule:wall-of-text` (an on-screen string over six
words, a numeral with its separators counted as one, or more than four
words of its own line printed verbatim: direction 2's check, AUTO-98), `rule:numbers`, `rule:hook-subject`,
`rule:hook-answer`, `rule:payoff-last`, `rule:marks` (a delivery mark on a
word its line does not say, two emphasis words in a sentence, an empty
pronouncer; AUTO-139). The house tier, recorded as
`warning: house:rule:x` and never revised: `rule:clause`, `rule:passive`
(English only), `rule:person`, `rule:pace`, `rule:density`,
`rule:hook-promise`, `rule:hook-stakes`, `rule:end-signal`, `rule:recap`,
`rule:pronouncer` (a section's mid-sentence capitalised names with no
pronouncer, one flag per section; run only where some shot carries a mark,
else `not_evidenced: rule:pronouncer (pre-marks script)`).
The minor tier: `rule:last-line`. A greeting that says a word of the
title is the first content sentence, not filler; `rule:hook-subject` takes
any content word of the title, its verb included; `rule:pace` is
consistency with `--wpm`, not direction 25's band. A rule outside the film's language is
`not_evidenced: rule:x (lang)`. The error and important flags go to one
revision (`revise-rules`, `revise-rules-2` for a line still breaking its
rule), which fixes the line, or its on-screen text for `rule:legibility`,
`rule:unspoken-number` and `rule:wall-of-text` only, or keeps it with a reason; the rules are read again on the final text and what
stands goes to script.json's `findings`, `error:` for the error tier and
`warning:` for the rest. A standing `error:` exits 1 and leaves
`narration.txt` unwritten unless `yt script --report-only`; `yt doctor`
fails on it under `script` with the fixing command (and not again under
`build`), and `yt build` carries
it into build.json's findings, where `yt qa` lifts it as a blocker. A
`warning:` blocks nothing.

**A line goes back without the screenplay (AUTO-95).** `yt script --revise
LABELS` asks one `revise-labels` decision over the named shots only, each
with its reason (the whole screenplay is shown with its supers, so a
rewrite reads against the lines around it), and writes nothing else into
the screenplay: every other shot is written back as it was read, byte for
byte. The labels are given
(`1.3,2.1`, with `--why` the reason), or read: `retime` takes every shot
`retime.json` sends back as `rewrite`, with the read it measured and the
editor's note; `feedback` takes the shots `feedback/script.json` names (the
`.json` twin `yt improve` writes, never its prose; a finding that names no
shot is noted and left to a full run). The revision fixes the line, as
direction 29 asks of a read that stumbles, runs out of breath or buries its
point, changes its supers when the reason is about what the screen shows
(the take still holds, so no `--redo` for that), or keeps it with a reason,
which the run prints and records; an answer that returns the line as it
was is kept the same way. Then the narration rules read the changed shots
only (their old flags and findings go, what the new text breaks comes in,
every other flag stands as recorded), and with research the changed lines
are fact-checked again as the stage's own loop does: `verify-labels`, a
flagged line to `revise-labels-facts-1` and checked again
(`verify-labels-1`), two rounds at most, the check's old flags and keep
reasons for those lines replaced (a check that fails writes nothing). script.json gains a
`revised` entry (where the lines came from, what changed, what was kept),
the changed shot its `revised` reason, `usage` what this run spent, and the
four files are written with `script.json.bak` kept; a standing `error:`
still leaves `narration.txt` unwritten. Nothing is planned or drafted
again and no take is touched: the run prints `yt voice --redo <labels>` for
the changed lines, whose takes are now stale.

Beside the chain:

| Tool | What it does | Writes |
|---|---|---|
| `yt retime` | writes the measured narration durations back into the screenplay | `retime.json` |
| `yt pilot` | cuts nested short approval reels at full fidelity; the model picks the cuts | `pilot.json`, `pilot.scene.xml` |
| `yt gate` | the delivery gate (loudness, true peak, scopes, black and frozen frames, captions), run on a render host that has ffprobe and the engine, triaged by the model; `stopped_by` names the requirement that stopped it and the owning stage; `video_sha256` and `scene_sha256` are the bytes it judged | `gate.json` |
| `yt review` | the PDF review sheet, read by the model | `review.pdf`, `review.json` |
| `yt qa` | the publication scorecard per pass: every shot sampled once at its midpoint (up to `qa.EVERY_SHOT_UP_TO`, 60, without `--frames`), each row's `why` naming its priority reasons or `shot` and its `at` naming the shot (`--frames N`: above the shot count the extra stills are evenly spaced over the runtime, inside a shot and 0.5 s from every other sample; below it the hook, ending, payoff and section starts are kept first and the shots left out are named to the reviewer before it scores and in the scorecard's `unsampled` and a `not_evidenced` line, AUTO-103), and each narrated line (one sentence of a take in voice.json, placed on the film by build.json's shot `at`) a still where its first word is said, half a burst inside its shot, `why` `line: <first words>` and the line with its start and `frame` (the file that shows that moment) beside the still in the brief (`lines`), so a picture is judged when its line is said; a line merges into a still of its own shot only when that still's burst has the frame where it starts, line stills come after every shot's own with the payoff, re-hook and reveal lines (`qa.KEY_LINES`) first, then the lines said inside a shot before the ones that open their take, spread over the runtime, count against `--frames N` before the evenly spaced ones (without it, up to 60 stills in all), the lines left without one are named to the reviewer and in the scorecard's `lines_unsampled` and a `lines:` `not_evidenced` line, and a wordless film has none (AUTO-104); one frame window per sample (one `--frames A..B` job each, the still its middle frame), a contact sheet, the mechanical findings (flat, clipped, frozen, jump, off-camera), then the model's review, which reads the screenplay's story (`qa.story_brief`: train, promise, stakes, payoff line, the question ledger on the timeline, the re-hook shots) as the writer's declaration and the pass's `narration.txt` (the voiced takes, else the screenplay's lines, said in its first line) beside the frames as evidence that the explanation needs both ear and eye (attention 23), under a role whose narrative_clarity and editing_pacing sentences are rendered from `narrative.rubric_text`; the scorecard's `story` (premise restated, what the viewer has at each section's end, one non-blank line per screenplay section or asked again once and the gap named `not_evidenced`, a predicted satisfaction that scores nothing and is `not_evidenced` until the channel-level `work/retention.json` exists; the per-film `work/<slug>/retention.json` that `yt retention` writes does not count) is required of a new pass, and `compare` reads scores only, so an older pass still compares (AUTO-99); `voiceover`, `sound_design` and `music_score` are scored only from a listening review that heard what the film carries (AUTO-105): the brief's listening evidence is sound.json's listen-back and voice.json's take review, each marked with what it heard (`heard`; the listen-back's covers the cues, the palette's mix targets and the `normalize` mode, never the takes) and current only while that digest matches its file and build.json is not older than it, a stale one named and what it said left out; a category no current review covers is `null` in `scores`, named under `not_evidenced` and `audio_not_evidenced` (scorecard and qa.json) with the command that produces the review, the overall is the mean of the scored categories (the filed one kept as `overall_filed`), and the bar says READY cannot be judged for it; the scorecard's `listening` keeps every row; the scorecard is asked `qa.QA_SAMPLES` (3) times on the same evidence (`<pass>-scorecard-s1`.., the categories in a different order each), each category is the median of the valid samples with its `spread` (max - min) recorded, the findings and the reviewer's `blocking` claims are grouped by one `<pass>-scorecard-merge` decision (reported by two or more samples: kept, blocking only when two said so; by one: `low_confidence`, listed, not routed and blocking nothing; a merge that fails keeps everything unmerged), a category set by rule (silent, capped) records no spread, a failed sample is a failed engine row (a failed `-again` keeps the first answer), and fewer than two valid samples is `not_evidenced`, no scores and never READY: `compare` skips it for the pass before, `yt improve` scores it again first and refuses it as `--from-pass`, and `yt doctor` sends it back to `yt qa` (AUTO-107); an audio category no current listening review covers is `null` in every sample's scores and in the result, never the median of the 0s the reviewers entered, and has no spread; the overall is then the mean of the scored medians, the median of the reviewers' overalls kept as `overall_filed`, and `spread.overall` is the spread of each sample's mean over the scored categories; the scoring is blind to the pass before (AUTO-106): the samples' prompt names no previous pass, score, overall or finding, `compare` takes the deltas from the two scorecards once the new one exists, and `--recheck` (off by default) puts the previous pass's findings (at most `qa.RECHECK_MAX`, 20, their place and words only) to one `<pass>-recheck` inspection after the scoring, each answered stands, gone or cannot_tell with why, filed under `recheck` (scorecard, qa.json's tally, a findings.md section) and read by nothing that scores, an unanswered one `unanswered`, a failed one a billed failed row; each pass is then judged against the previous kept cut side by side (AUTO-108): `--previous PASS` (`yt improve` names its best kept pass), else the latest scored pass, else, when improve.json says it was reverted, the best kept pass improve.json records; its still at each sampled timestamp reused from its `frames/` when it sampled the same time, else rendered from a copy under `improve/` whose `scene.xml` and every file it references (`render.reference_files`: textures, images, models, HDRIs, fonts, includes and what they name) match the `cut` digest it recorded (a reference that cannot be read leaves `cut` null, which matches nothing), else the moment is unpaired and named under `not_evidenced` and in findings.md, whatever the status; one `<pass>-compare-pass` inspection over the A/B pairs, laid out in a temporary directory outside the pass so the reviewer cannot match them to `frames/`, answers A, B or same, why, and the visual categories each moment bears on whatever the answer, with which cut is A drawn per moment (`qa.side_order`, half each, seeded by the pass) and recorded so position bias can be checked; the scorecard's `side_by_side` keeps the moments, the engine-agnostic `entries` (`{category, result: better|worse|same, source: "stills", moment, t}`, one per category the moment bears on or one with `category: null` when it names none; a video comparison files the same with `source: "video"`) and their `tally` (`qa.tally`: entries per category, and `total`, one vote a moment, the result most of its entries give, a tie same), findings.md a Side by side section and qa.json the total; nothing is compared with no earlier scored pass, the same cut, `--reuse` (whose card keeps the `cut` the reused frames were drawn from) or a pass not evidenced; `--against FILE --compare-model MODEL` (default `$YT_COMPARE_MODEL`, read only with `--against`) watches the previous kept cut FILE and this pass's `video.mp4` whole, picture and sound, through the gateway engine on a harness of its own (`ytstudio/cutcompare.py`, AUTO-128: no revision notes, `cutcompare.COMPARE_TIMEOUT` 1200 s), twice, previous cut as A then new cut as A (`cutcompare-<pass>-ab` and `-ba`), each request blind (the files linked as `cut-a.mp4` and `cut-b.mp4` in a temporary directory, labelled `Cut A:` and `Cut B:` by a text part before each, no pass, score or finding named), every category of the ten answered A, B, same or cannot_tell with a reason and timestamps; a category both orders call for the new cut is better, for the previous one worse, same in both same, two that disagree a tie (same), cannot_tell in either nothing, each category its own moment, so the entries (`source: "video"`) and their tally are `side_by_side` (`source: "video"`, the still pairs not asked); an answer that breaks the schema after the harness's one retry (forgotten, so the next run asks again), a timeout or a gateway failure fails it, and a missing FILE, a missing `video.mp4` or one older than the document, two cuts over `cutcompare.COMPARE_MAX_MB` (15) or no `AI_GATEWAY_API_KEY` skips it; either way it files nothing, a `not_evidenced` line says why and the still pairs run as without it; `qa/pass-NN/comparison.json` keeps the record (both cuts' sha256, each order's answer, the reconciled rows), the scorecard `against` and `compare_engine` (its own account; `usage` is both); either option alone, a model the gateway engine cannot ask (`gateway_engine.model_problem`) or one of the family of the model in script, styles, keyframe, vfx, sound or build json's `usage` or `engine` (when those that exist name no model at all, an `anthropic/` or `openai/` one, the default CLIs' providers) exits 2 before anything renders; `--listen` (AUTO-126, `ytstudio/listen.py`, off by default and never turned on by `YT_GATEWAY`) hears the film's own audio: before anything renders it refuses with exit 1, naming each missing piece (`YT_GATEWAY=vercel` and the gateway engine's readiness, a `--listen-model` in `models.LISTEN_MODELS` with a verified provider, a listener from another maker than every author of script.json, sound.json and a voice.json that names a model, `video.mp4` not older than `scene.xml`, an audio track, `ffmpeg` and `ffprobe` on PATH, a `--reuse` pass of this scene, the track under `listen.LISTEN_MAX_MB`, a price row, an estimate within `--listen-budget-usd`, 0.50), copies the first audio stream out by stream copy (`-vn -map 0:a:0 -c copy -f adts`) into `qa/listen/<film sha256[:16]>.aac`, measures it with ffprobe, and asks one gateway decision `listen-<audio sha256[:16]>` (served from decisions/qa/ for the same mix) under a role that lists docs/production.md §2 rows 6-8, with no score; a finding whose owner or severity is outside the schema or whose timestamp is outside the duration is asked once more (`-again`, for those findings alone: the first answer's kept findings stand and the second adds what the first did not say), then dropped and named under `not_evidenced`; `listening.json` in the pass is the record, current only while `video.mp4` is the film heard and not older than `scene.xml`, and a current one is one more listening-evidence row covering all three audio categories, the reviewer runs under `qa.ROLE_LISTENED` with a `Listening review:` brief, and its findings join the scorecard's (`source: listen`, `frame: listening.json`); a rerun served from the cache records the served model and provider the asking pass's `listening.json` named; the scorecard's `listen` holds the listener's report and `usage` merges both harnesses (`spend.merge_records`); a failed listen (a 402, another model answering) is not current, billed, one `external` finding, and the frame review runs under `qa.ROLE`; `qa.pass_usd` prices the next pass without a past listen, and `spend.FLAG_USD` adds the listen's estimate whenever `--listen` is asked; `--watch` (AUTO-127, `ytstudio/watch.py`, off by default and never turned on by `YT_GATEWAY`) watches `video.mp4` in motion: before a pass directory is made it refuses with exit 1, naming each missing piece (`YT_GATEWAY=vercel` and the gateway engine's readiness, Node >=22 among it; the catalogue `GET /v1/models` reached; a `--watch-model`, default `google/gemini-3.6-flash`, whose row is a language model tagged `video-input` and `structured-output`, not deprecated, not `vmc/`, not `-batch`; a known maker (`models.maker`) differing from every model script.json, keyframe.json's elements and review, vfx.json and its authored sections and sound.json record; providers in `watch.WATCH_PROVIDERS`; a render host, `--host` else the encode's); a cut `doctor.cut_problems` calls stale or partial is not watched and its problem is a `not_evidenced` line; the sound is never sent: the cut's picture goes as it is under `watch.WATCH_MAX_BYTES` and `WATCH_MAX_SECONDS` (straight from here when build.json has no audio track, else copied without its sound on the render host), else the render host re-encodes it (`WATCH_HEIGHT`, `WATCH_KBPS`, no audio), split at shot boundaries (1 s of overlap inside a shot longer than a piece), by one rsync up, one ssh and one rsync back into `watch/`, never on the workstation, a piece over the limit or off its length an error with nothing sent; one gateway decision `watch-sNN` a piece (cached by its bytes), its times shifted to the film, an answer outside the piece or with `watched_seconds` off its length asked once more (`-again`) then recorded unwatched and both answers forgotten, a failed call leaving its piece unwatched and the rest asked, a 402 stopping the rest; `watch.json` keeps the model, the catalogue row, who served each call, the plan and the findings W1.., and is reused for the same cut, model, providers, constants and role when it watched every piece, unless `--fresh`; each finding's start is a still beyond `--frames` (at most `qa.WATCH_SAMPLES_MAX`, 12, blocking first; one under `qa.WATCHED_NEAR`, 0.5 s, from a still merges into it), the scorecard is asked a `watched` verdict per finding naming its frame, and only one two samples confirm on its own still joins the findings (`source: watched:Wn`), the rest listed in findings.md; `watch` and `watch_engine` on the scorecard, its cost in `usage`; `--reuse PASS --watch` reads that pass's watch.json, and exits 1 when it has none; `spend.FLAG_USD` adds `watch_usd` whenever `--watch` is asked and `qa.pass_usd` leaves a past watch out | `qa/pass-NN/` (`frames/`, `frames/burst/`, `samples.json`, `narration.txt`, `contact-sheet.png`, `previous/`, `side-by-side/`, `comparison.json` with `--against`, `scorecard.json`, `findings.md`; with `--listen`, `listening.json`; with `--watch`, `watch.json` and `watch/`), `qa/listen/` (with `--listen`), `qa.json` |
| `yt improve` | the improvement loop of `docs/production.md` §8 as code: routes each QA finding to the stage that owns it, writes that stage a revision brief the harness puts in front of every decision it makes, re-runs the earliest routed stage and everything downstream, scores a new pass, keeps it or restores the snapshot; the dry run estimates per stage from measured reports (marked measured, like or assumed), a vfx pass names only the routed `--sections` (a doctor warn whose fix names `--sections N` routes to N, AUTO-144) and a script pass whose findings all name a shot revises only those lines (`--revise feedback`); a `--model` of the reviewer's family (`opus`) is refused with exit 2 before any stage runs | `feedback/<stage>.md` (+ `.json`), `improve/pass-NN/`, `improve.json`, `improve.md` |
| `yt retention` | the published film's audience curve read back onto its shots (AUTO-101). No model, no fetch: the operator places what YouTube Studio shows (Intro at 0:30, top moments, spikes, dips with skipped, abandoned or unknown, the standard and typical curves, the traffic shares if Studio shows them; `retention.INGEST_SCHEMA`, pinned by `tests/fixtures/retention/`, which is hand-built until a real export replaces it) in `work/<slug>/retention/*.json` or names it with `--export`; each timestamp lands on the shot and section `build.json` timed, with the questions the ledger had open. Findings for `yt improve`, each with the corpus `rule` it applies, `delta_vs_typical` and a rubric key as `hypothesis` (a guess from the tag table, never a cause): one per dip after 0:30 (`dip-skipped`, `dip-abandoned`, `dip-unknown`, judging 3), `move_earlier` for a top moment in the film's second half (judging 3), `intro` only under the median Intro of the channel's last 10 measured films of similar length published before it (a film of unknown length or a later one is left out and noted) with under 50% of views from search and external traffic (without the traffic shares the Intro is recorded, not judged; structure 38), `outro` when the last 5% falls at least twice as steeply as a falling 75-95% (one that held is noted, not filed), and `unforeshadowed-payoff` for a spike after the payoff shot (structure 40). Only the standard curve's timestamps are filed (judging 2). Every constant (`retention.BOUNDS`) and the two house readings are tagged sanity_bound with their rule, and under 10 measured films so is every finding (judging 6); `yt doctor` notes the baseline's size | `retention.json` |
| `yt doctor` | the toolchain and every video: what is missing, stale or failing, and the command that fixes it. No model; `--gateway-ping` adds one GET of Vercel AI Gateway's credits (the team's balance, AUTO-122), never made without it. | `--json FILE` |
| `yt assess` | can a described video be produced at all; proves it with a frame | `work/.assess/<slug>/` |
| `yt probe` | the realism probe suite: fixed neutral briefs, one per class of material, light or motion, each composed by an agent coding with the SDK through the kit, built and framed by the harness, scored by a different model that names each defect's cause (authoring, engine, asset), revised in the same session while under `--bar` | `work/.probes/<run>/` (`summary.json`, `summary.md`, one workspace per probe), `work/.probes/latest.json` |
| `yt ls` | which videos exist and how far each got | |
| `yt assets` | search open catalogues for one shot's footage | |
| `yt props` | fetch CC0 3D props (glTF models, HDRIs, texture maps) from Poly Haven, by phrase, or with none chosen and vetted by model decisions over the script and styles ("nothing fits" keeps nothing); the vfx brief lists them | `props/<kind>/<id>/`, `props.json` |

Keys load from `--env-file`, then `$YT_ENV_FILE`, `./_tmp/do_not_delete.env`,
`./.env`, `~/.config/pals-notes/.env`; `yt sound --fetch` wants
`FREESOUND_CLIENT_ID` and `FREESOUND_CLIENT_SECRET` (the secret is the
token), and without them nothing is fetched. `YT_GATEWAY=vercel` with
`AI_GATEWAY_API_KEY` sends the claude engine's calls through Vercel AI Gateway
(`ytstudio/gateway.py`; `YT_GATEWAY_AUTH=subscription` keeps the Claude login,
so unset `ANTHROPIC_API_KEY` there or the CLI sends that key to the gateway
instead, and `YT_GATEWAY_URL` replaces the host); it is never on by itself, a
set switch with no key stops the call before it starts, as do `bare` and a
`CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` or `CLAUDE_CODE_USE_FOUNDRY`
left on, and with it unset (or `YT_GATEWAY=off`, `0`, `false`, `no`, `none`)
each call, its argv, its booking and the decision cache are as they were.
With the switch set, `yt doctor` shows a "gateway" block: a missing key (or
anything else the route refuses) fails, a hand-set `ANTHROPIC_BASE_URL` that
yt replaces or that ends in `/v1` (a 404) warns, a parent `ANTHROPIC_API_KEY`
is noted as blanked for the CLI (warned about in subscription mode), and the
endpoints in use are named, never the key. `yt doctor --gateway-ping` asks
`GET /v1/credits` for the team's `balance` and `total_used` (one GET, five
seconds, no model, never without the flag; a refused key fails with the AI
Gateway API Keys page as its fix; a redirect is not followed, so the key
goes to no other host; a proxy's user and password in `YT_GATEWAY_URL` are
not sent; refused under `YT_NO_REMOTE`).
Through the gateway a claude call is booked at its model's price row times
the tokens in `modelUsage`, an estimate of the gateway's bill (list price,
no markup; a discount or a surcharge is not in it), and the CLI's own
figure stays on the `by_model` row as `cli_cost_usd` beside
`cost_table_calls` or `cost_cli_calls`. Each web search
(`webSearchRequests`) adds $0.01, which the CLI's figure holds and the
tokens do not; a model with no row, a result with no tokens, a prompt above
a model's tier (Haiku 5.5 bills 5x above 100K prompt tokens) or another
server tool's requests is booked at the CLI's figure, counted in
`cost_cli_calls` and warned about by `yt doctor`. The stream meter's budget check reads the
table's figure; the CLI's `--max-budget-usd` stays as a backstop. A gateway
budget's 402 (`quota_for_entity_exceeded`, "budget exceeded", an API key's
"Quota limit exceeded") is permanent, on stderr or in an `is_error`
result, so the call stops on the first one, and a 429 is retried. Under
`YT_ENGINE=codex` the switch makes the gateway Codex's model provider with
`-c` pairs instead (base URL `https://ai-gateway.vercel.sh/codex/v1`, the key
read from `AI_GATEWAY_API_KEY`, nothing written to `$CODEX_HOME/config.toml`,
your own `codex_config` pairs winning), sends a bare model as `openai/<name>`,
Codex's own default included, and never a Claude id, not even one atop the
models cache (a `model_provider` of your own gets the model as given, and a
key with whitespace around it is refused); an effort above `high`
that `models_cache.json` lists for neither spelling goes as `high`, with a
warning. The gateway engine
(`gateway_engine.py`, AUTO-124) is a separate opt-in: a stage builds
`Harness(engine="gateway")` behind its own flag for a model neither CLI reaches
(audio, video, another reviewer family), and only that harness runs
`gateway/gateway-engine.mjs` on Node 22 or later (`cd gateway && npm ci
--ignore-scripts`, after `npm install --package-lock-only --ignore-scripts`
while no lockfile is committed; `YT_GATEWAY_NODE` names the binary,
`AI_GATEWAY_API_KEY` is its key, `GATEWAY_PRICES` prices a gateway id the
gateway returns no cost for, `YT_REVIEWER_EXCLUDE_QUANT` the quantizations
a pool reviewer's providers may not serve at, comma-separated, default
`fp4`).
It serves `decide` (with `media=` files) only, never `inspect`; `YT_GATEWAY`
and `YT_ENGINE` do not select it, and Python stays dependency-free. Its
users are the script's other-maker reviewer (`--reviewer-model pool` or
`sdk:<id>`, AUTO-125), `yt qa --listen` (AUTO-126), which sends the
film's audio track (`.aac`, sent as `audio/aac`) to a model of
`models.LISTEN_MODELS`, each served only by the providers listed with it,
`yt qa --against` (AUTO-128) and `yt qa --watch` (AUTO-127), which sends
the cut, or the pieces a render host cut it into, to a model tagged
`video-input`, served only by `watch.WATCH_PROVIDERS`. A media entry may be
`{"path": ..., "label": "Cut A:"}`: the label goes as a text part
immediately before its file and joins the digest, and a plain path is sent
and digested as before. Every stage that asks the
model takes `--model`, `--effort` and `--fallback-model` (default sonnet);
`yt doctor`, `yt ls` and `yt assets` call no model.

The house standards ship: `ytstudio/house/default.md` is `docs/narrative/`
distilled by hand into `## all`, `## script`, `## styles`, `## vfx`,
`## sound`, `## voice` and `## format` (the script stage reads the format
before its own section; the keyframe reads `## vfx` until a house writes
`## keyframe`), each with a comment naming the corpus rules it states, which
never reaches a prompt. The channel's `work/house.md`, then
`work/<slug>/house.md`, then `--house FILE` replace its sections by name;
`--no-house` drops all of it. The digest names the default as
`ytstudio/house/default.md`. A decision's cache key holds its stage's block
(`## all` and its own section), so a changed section is a changed question:
an edit to `## all` re-asks every cached decision in every project on the
next run, an edit to `## voice` only the read's, and an edit to a comment
nothing. `yt doctor` reads `house.md` from `--work` and warns for each
manifest whose stage's block was `decided under a different house digest`.

## Rendering

The engine is not bundled and never runs here. `RS_RENDER_HOSTS` in `.env`
lists the ssh targets; `yt render` probes each once and routes by memory,
not GPU, as `docs/compute.md` measured: a full encode and the audio mix go
to the host with the most RAM (192.168.199.7, 125 GB, RTX 3050), stills,
frame ranges and validation to the one with the least (192.168.199.13,
15 GB, RTX 3070 Ti, the faster GPU), unless its live reading says it is
saturated (`render.host_health`: a one-minute load over its cores, or under
`render.GPU_FREE_FLOOR_MB` free on its GPU) while the other is quiet, in
which case the light jobs go there too; an encode follows the memory
whatever the load. `--host` pins one, `--local` is for a
machine that has the engine, `--slices N` splits a long encode across both
hosts with the mix rendered once (more slices, and slices even without `--slices`, when the GPU memory rate an encode measured into its report's `gpu_memory` needs them; a still's peak is not a rate and is ignored; with no encode measured, the count stays and the output says so); give every `<layer>` of a long world a start/end or the vfx gate warns, since unbounded layers stay loaded. A pull-back never overwrites a newer local file and a still brings back only its own output; slices live in `.slices/<key>/` (not mirrored, reused after a failed run, removed after the join) and `--slices` honours `--output` and `--keep-remote`.

- **Iterate at draft, deliver at final.** `--quality draft` for every look
  and `--still T` or `--frames A..B` for one frame or a range. The delivery
  encode is `--quality final --strict`, so a warning fails it instead of
  passing quietly.
- **One mirror per project, one directory per job.** A host keeps
  `/tmp/yt-render/<slug>/mirror/`, refreshed by an incremental rsync before
  every job and never removed by one; each job runs in
  `/tmp/yt-render/<slug>/<job>-<pid>-<suffix>/`, hardlinked from the mirror
  (`cp -al`, `rsync --link-dest` as the fallback), pulled back from and
  removed. `--keep-remote` leaves a job's directory for debugging;
  `--prune-remote` deletes the project's mirror on every host and stops,
  so the next job uploads in full. A full encode, a `pilot --render`, a
  still and an `--audio-only` run for the same project no longer overwrite
  each other. Only the film's encode writes `render.json`; every other job
  writes `render-<job>.json`, so the report the gate reads is never
  replaced by a pilot's. A report that came back from a host has its paths
  rewritten to the project here. `render.run_job(...)` is how another stage
  runs one engine command the same way.
- **Stills are cached by fingerprint.** A still whose scene bytes, every
  file it references, time, quality, options and engine identity match an
  earlier one is served from `.cache/stills/` of the project (or
  `$YT_STILL_CACHE`) with no host, ssh or rsync, and the note says `served
  from cache`. A still with an unreadable reference, or an engine of unknown
  identity, is never cached. Delete the directory to force a render.
- **Every job leaves a status.** A render job's report (`render.json`,
  `render-<job>.json`, a kit still's under `.cache/reports/`) carries
  `status`: `rendered`, `failed`, `timeout` or `blocked-inputs` (the files
  it lacked are listed), and `outcome_unknown` when a detached job never
  reported. `yt doctor`'s `render-jobs` area lists every one that did not
  render, with the fix: lower the cost drivers or render in windows, produce
  the missing files and `yt build`, or read the report and run again.
- **A dropped ssh does not kill the job.** A job runs detached on the host
  (`nohup setsid`), records its pid, output and exit status, and survives a
  dropped ssh. Only the exit marker it writes is authoritative: with none,
  `yt render` attaches again (bounded), a job whose pid is gone is reported
  lost, and when the polls run out the job directory stays and the output
  says where. Never pull back or remove under a live job. A host's ssh is
  checked again before routing (the same round trip reads its load) and a
  refusing host is skipped for the next; every ssh and rsync carries
  `ConnectTimeout=10`, so a host that takes TCP but never answers ssh holds
  nothing for long, and a sync the host does not take (a lost connection,
  not a partial transfer) marks it dead for `render.ALIVE_TTL` and sends
  the job to the other host, once (never the slices' mix: the join happens
  on its host). A slice that moves waits for its turn there, one slice per
  host at a time, since `plan_slices` sized it for the GPU alone, and the
  slices after it pass the marked host over while the mark is fresh.
  `yt doctor` warns `accepts TCP on the ssh port but not SSH` for a host
  that answers the port and not the login, and `answered the probe but not
  ssh now` for a usable one the live reading cannot reach: restart sshd
  there or take it out of `RS_RENDER_HOSTS`.
- **Mix once.** `--audio-only` builds the whole mix and tells you whether
  the audio graph fits in memory. A `--start/--end` range does not make it
  cheaper; the mix is assembled entire either way.
- **The lock is a convention, not code.** Other sessions share these
  machines. `yt render` does not take a lock; any command you run on a host
  by hand does, as `ssh root@HOST flock -n /run/lock/render-host.lock -c
  '<command>'`, with a `/root/render/.reserva` file naming the session, PID,
  time and job. A `.reserva` with a live PID means ask the owner first.
  Whether this pipeline has priority there is the owner's call.
- **Disk on .7 is the binding constraint.** It had about 14 GB free when
  measured, and a 13-minute master is 1.7 GB. Check `df` before a long
  encode, clean intermediates, and do not leave masters there; `yt doctor`
  probes each host's free space where the mirrors live and warns under
  `doctor.LOW_DISK_GB`, and warns when the encode host has no ffprobe (the
  gate runs there). The mirrors
  under `/tmp/yt-render/<slug>/mirror/` persist by design; `yt render
  --title X --prune-remote` is how a finished project's copy comes off.
- **ffmpeg is not on this workstation,** and neither is ffprobe, so `yt
  gate` runs on a host too: `--host USER@HOST`, or by default the host an
  encode would go to; the job lives in `/tmp/yt-gate/<slug>/gate-<tag>/`
  and is removed after (`--keep-remote` keeps it, `--local` runs it here).
  On .13 ffmpeg lives inside the AudioForge venv, not on PATH. `yt qa
  --listen` needs both on PATH where it runs, and refuses, naming the one
  that is missing, before anything renders.

## Delivering: the order and what "done" means

`build.json`'s `publishable` only says the build has no placeholder and no
blocking editorial note. It is not a delivery verdict. The order is:

1. `yt build`, then `yt render --quality draft` and look at it. A film in
   `design.py`'s house fallback palette builds and renders (one `warning:
   design:` finding names why), but its look cannot be approved at `yt
   keyframe` and it is not delivered; the fix is `yt styles --rich`.
2. `yt pilot --seconds 30,60,180`, optionally `--render`, for approval.
3. The improvement loop of `docs/production.md` §8: locate, describe,
   explain, trace to the owning file, fix at the source, re-render only what
   changed, compare, keep only a measured improvement. One section at a
   time, watched with sound. The same defect surviving three passes means
   the approach is wrong, not the parameters. `yt improve` runs it (below);
   `yt improve --dry-run` first shows the routing and the briefs.
4. `yt render --audio-only` once, to prove the mix fits in memory, then
   `yt render --quality final --strict`. Both route to the big host.
5. `yt gate` (its sr-audiocheck, sr-margin and sr-sibilance are bundled in
   `ytstudio/audiotools` and shipped to the host with the gate; sr-audiocheck
   also flags a muted channel and out-of-phase stereo, owned by `yt sound`;
   `yt doctor` and `--preflight` name a missing one; its tail check is the bundled stdlib `tail_check.py`, which judges the effects after the last word by measuring them) (on a render host; `gate.json`'s `stopped_by` says which
   requirement stopped it and which stage owns the fix; a wordless film passes it with the descriptive captions `yt voice` writes and `voice_end` = the last shot's start), then `yt review
   --engine rs`, then `yt qa`.
6. READY TO PUBLISH only when `yt qa` reports an overall of 9.0 with no
   category under 8.5, nothing blocking and no placeholder. A film in the
   house fallback palette (`build.json`'s `design.source` `fallback`) is a
   `styles:` blocker naming `yt styles --rich`, whatever it scores. A vfx
   continuity break marked `blocking` whose recorded reviewer is of another
   family than every recorded author of the film is a `vfx continuity:`
   blocker, whatever it scores; the fix is `yt vfx --sections N`, which
   judges continuity again.
   A blocking mechanical finding (flat, clipped, frozen, a jump, an
   off-camera object on the hook, the ending, the payoff shot or more than
   one sampled shot) blocks on its own and caps `visual_quality` and
   `animation_vfx` at 6 until it is fixed.
   The rhythm findings (`ytstudio/rhythm.py`, `pass-NN/rhythm.json`: a hold over 8 s while the narration runs, a slow opening, shot or change intervals with a CV under 0.3, a section outside 130-200 wpm) cap `editing_pacing` at 6 instead (a hold caps all three); until the scan's thresholds are measured a hold, a slow opening or an events-basis metronomic finding is minor and TO VERIFY, and a stale or unscanned render leaves them not evidenced.
   QA's mechanical pass also compares the film with the authors' proof
   frames (as `yt build` did on the built document before the encode):
   for each world's latest proof frame it renders the film at the
   build's placement of the world's first shot plus the frame's time in the
   world and compares the stage bands (rows 230..820 of 1080). Both frames
   are ungraded: the film's is rendered from a copy of the scene with its
   `<colorManagement>` removed, as the proof frame was, so an applied look
   is not a mismatch (each row under `qa/pass-NN/proof/plan.json` says
   `graded: false`; a `--reuse` pass whose plan does not has its proof
   frames rendered again; `yt build`'s check renders the same way). A
   difference above `qa.PROOF_DIFF` (4; measured: 0.12 for a matching pair,
   7.9 for a sparse world's opening state against 24 s on, 24.6 for another
   world) is a `proof-mismatch`, owned by `build` and always blocking: it caps
   `visual_quality` and `animation_vfx` and blocks READY, and every other
   mechanical finding on that world's shots names `build` as owner too
   (`reowned` says why). A film with no proof
   frames gets a note in the scorecard. The film's frame is kept under
   `qa/pass-NN/proof/`; the author's stays in `vfx/vNN/frames/`;
   with no audio track in `build.json`, `sound_design` and `music_score`
   are 2.0 by rule. `voiceover`, `sound_design` and `music_score` are
   scored only from a current listening review (sound.json's listen-back,
   voice.json's take review, each with the digest of what it heard); with
   none the category is not evidenced, unscored, the overall is the mean
   of the scored categories, and READY cannot be judged until the command
   the scorecard names produces one (for a wordless film's voiceover no
   command does yet, and the bar says READY is out of reach). Each pass keeps its frames, frame windows, contact
   sheet, scorecard and findings under `qa/pass-NN/`, so the next pass is a
   comparison and a category that drops 0.5 is a regression.
7. `yt doctor --title X` aggregates all of it and names the next command.
   Exit code 1 means something fails. Its `delivery` findings are the
   delivery verdict, read from files and never from `build.json`: a fail for
   a render below `final`, any warning or unsupported construct in the
   engine's report (what `--strict` refuses), a render short of the whole
   programme, a programme with no audio track, a film in `design.py`'s house
   fallback palette (the fix `yt styles --rich`, then `yt build`; before a
   render it is a `styles` warning), no technical `gate.json` or
   one not `deliverable`, no `qa.json` at READY TO PUBLISH, or a gate or QA
   pass older than the newer of `video.mp4` and `scene.xml` (QA records the
   digest of the scene it judged, and the gate, like QA, the byte digests of
   the video and scene it judged, `video_sha256` and `scene_sha256`;
   those are compared instead where present, so a touched file is not
   stale and a replaced one is; the gate hashes them before it runs, and
   doctor reads the project's `video.mp4` whole each time, so a gate run on
   `--video` another file reads as judging a different video), or a video
   older than `scene.xml`. Only with none of them does doctor
   print `delivery: READY TO PUBLISH`.

The frame has three bands at 1920x1080, measured from the top: supers and
credits in rows 0 to 230, the stage in 230 to 820, and captions in 820 to
1080, where nothing else may enter.

## The improvement loop: `yt improve`

QA names an owner for every finding; `yt improve --title X` routes them
back. It starts from the latest QA pass (`--from-pass pass-NN` to pick one;
a pass older than `video.mp4` or `scene.xml` is scored again first), with
`gate.json`'s blocking rows and `yt doctor`'s fails and warnings beside it,
and the published film's `retention.json` findings (source `retention`,
`yt retention`) while `scene.xml` is still the cut the curve measured: a
pass that rebuilds the film drops them, since nobody watched the new cut.
A briefed one that is gone for that reason is neither fixed nor survived:
the pass records it under `unmeasured` and `improve.md` says "not
re-measured" until a new publish and `yt retention` measure it.

- **Routing.** Each finding goes to exactly one stage of `workspace.STAGES`,
  or to `upstream-fact` (a claim the research or screenplay got wrong) or
  `external` (a policy, licence or approval outside this repo). Those two
  are listed for a human and never looped on. The mechanical kinds
  (penetration, reads-merged, flat, clipped, frozen, jump, off_camera) go to
  their stated owner without the model; the prose ones, whose `owner` is
  free text, go through one harness decision that also merges duplicates
  and writes each stage a brief: what must change, how the next pass checks.
  First, a prose finding that makes a claim `build.json` can check (no
  audio, no narration, a placeholder) and that the manifest contradicts is
  routed nowhere and listed in `improve.md` as "contradicted by
  build.json: <field>", so no stage is briefed to fix what is not broken.
  A story finding (hook, stakes, re-hooks, open questions, payoff: what
  `qa.DUTIES["script"]` names) goes to `script` even though it is
  expensive, and the brief names the narrative rubric key each bears on
  (`rules`, one of `narrative.KEYS`, attached only to a finding routed to
  that brief's stage; a word outside the rubric that survives the harness's
  one retry is kept as `rule_outside`). A finding that says the film lacks what `script.json`'s
  plan declares (a payoff line, stakes, questions, re-hooks, a promise:
  `improve.PLAN_CLAIMS`) is not routed away: it is annotated "the plan
  declared ..." after the routing, since the reviewer watched the film and
  the plan is the writer's own report (AUTO-99). A `retention` finding is
  fingerprinted by its shot and tag, decided by the router (never a
  lookup: invariant 3), which is told to send it to `script` by its corpus
  rule unless it names a picture at that timestamp; `feedback/script.md`
  prints it as `[retention: judging 3, dip-skipped; hypothesis: open_loops]`
  with its `sanity_bound` reasons, and the `.json` twin keeps `corpus_rule`
  apart from the brief's rubric `rule` (AUTO-101).
- **Feedback.** `feedback/<stage>.md` (and `.json`) holds the brief with
  the findings verbatim, timestamps and the pass they came from, a story
  finding's `[rubric: <key>]` and its plan annotation (which `yt script
  --revise feedback` adds to the line's reason). Every
  `Harness` of that stage puts it in its system prompt as revision notes
  that outrank the house standards, and the prompts a stage builds itself
  (the vfx planner and authors, the keyframe author, sound, script and
  styles, the synthesis brief) take it through `harness.with_feedback`. A
  pass that consumed the notes moves them to `feedback/closed/pass-NN/`.
- **A pass.** The earliest routed stage with the flags that force a new
  decision (`--fresh`; `yt vfx --fresh` opens a new `vfx/vNN/`; a script
  pass whose findings all name a shot runs `yt script --revise feedback`,
  those lines only, and the full `--polish --verify --review` otherwise, or when one
  of those findings was briefed before and is still there: a revision of
  the line did not fix it, so the screenplay runs whole. A finding that
  needs more than its own line, a hook that does not state its promise or a
  section with no payoff, is routed without a shot to get the full run),
  then every
  stage downstream up to `yt render` (`--quality`, default final), then `yt
  gate` and `yt qa`. Research and script run only when routed to them, and a
  stage the film never ran (no narration, no voice) is left out. What those
  stages write is snapshotted first under `improve/pass-NN/snapshot/`.
- **Keep or revert.** Kept when the overall beats the best pass so far, no
  category drops more than 0.5 and no new blocking mechanical finding
  appears (a category QA left not evidenced has no score: neither a drop
  nor a gain, and when the two passes scored different categories the
  overall is compared over the ones both scored); otherwise the snapshot is restored (for vfx that means
  `vfx.json`'s active version; the new `vfx/vNN/` stays on disk). A change
  smaller than the scoring samples' `spread` (the larger of the two
  passes', `improve.judge`) is no change: an overall gain inside it is not
  kept, and a category drop inside it is no regression (AUTO-107). A pass
  whose `yt qa` is `not_evidenced` is `unjudged`, and the next run scores
  it again first.
  The side-by-side tally is read first (AUTO-108): improve runs `yt qa
  --previous` with the best pass so far, and when the new scorecard's
  `side_by_side` is against it, more `better` than `worse` moments (the
  tally's `total`, one vote a moment however many categories it bears on) keeps
  the pass even when its overall moved inside the spread, more `worse`
  reverts it, a tie leaves it to the score delta. The score delta is read
  second: an overall drop beyond the spread, a category drop or a new
  blocking mechanical finding reverts whatever the tally says.
  `improve.json` and `improve.md` record the tally beside the deltas.
  `--compare-model MODEL` (default `$YT_COMPARE_MODEL`; neither: off, every
  argv as before) adds `--against <snapshot>/video.mp4 --compare-model MODEL`
  to every `yt qa` of a pass (the loop's, an unjudged pass scored later,
  `--resume`'s and `--rejudge`'s, each against the pass's own `snapshot/`,
  taken before it changed anything, so the cut is always best_before's,
  never `snapshot-rejudge/`'s, which after a kept pass is the attempt's own
  earlier render; `--resume` then names best_before with `--previous` too),
  so the video comparison's tally is the one read
  (AUTO-128); a snapshot holding no `video.mp4` is noted on the pass
  (`compare`) and QA compares stills; a model the gateway engine cannot ask,
  or one of `--model`'s family, exits 2 before any stage.
- **Stops** at READY TO PUBLISH (or `--target` with nothing blocking), at
  `--max-passes` (3), when the next pass's estimate exceeds what is left of
  `--budget-usd` (15) -- `spend.stage_estimates` prices each stage of the
  pass from its own report (`measured`), else from a like project (`like`),
  else an assumed default (`assumed`), and only a pass with nothing measured
  falls back to the last pass's cost; QA is priced per scoring sample times
  `qa.QA_SAMPLES` (`qa.pass_usd`; assumed, `spend.QA_SAMPLE_USD` each), so a
  pass scored once before AUTO-107 prices the samples it did not ask;
  the side-by-side comparison is one decision a pass, priced once and
  never times the samples (`spend.COMPARE_PASS_USD` assumed, added to qa's
  row as its `--previous` flag whenever the flag is asked or an earlier
  scored pass is under `qa/` (`spend.qa_compares`; a plain `yt qa` from
  `yt run` compares too), unless the pass measured carries a
  `<pass>-compare-pass` decision, AUTO-108); the video comparison is priced
  once the same way (`spend.COMPARE_USD`, 0.10 assumed, as qa's `--against`
  flag unless the latest scorecard's `compare_engine` holds a `cutcompare-`
  decision, which then measures `--previous` too, since the still pairs run
  only when the video comparison fails; without `AI_GATEWAY_API_KEY` it is
  skipped, so `--against` adds nothing and `--previous` is priced as
  without it, AUTO-128); `yt qa --recheck` adds `spend.RECHECK_USD` (0.50
  assumed) each time it is asked, and `qa.pass_usd` leaves a past
  `<pass>-recheck` out of the next pass, which a plain `yt qa` or
  `yt improve` never asks (AUTO-106);
  `--dry-run` prints the table -- when a
  defect survives three briefed passes (a mechanical one by kind, shot and
  objects; a prose one by a cached model decision), when only human
  decisions remain, or when the script the pass wrote is rejected by its
  story review (`story-review`): the loop does not pass `--gate`, so it
  reads the verdict in script.json itself, restores the snapshot, keeps
  the rejected screenplay under `attempt/` and says so with the verdict
  in `improve.md`, before any voice or authoring is paid for.
- **Report.** `improve.json` (each pass's routing, briefs, argv, scores
  before and after, keep or revert with the numbers, cost, the stop) and
  `improve.md` in the shape of `docs/production.md` §11, whose remaining
  issues list the categories the best pass left not evidenced, with the
  missing listening review and the command that produces it. `yt improve
  --dry-run` routes, writes the feedback and `improve/plan.json`, prints the
  plan with its estimated cost, and runs nothing. `tests/test_improve.py`
  pins all of it.
- **When a pass breaks.** A stage that fails restores the snapshot, keeps
  what the attempt wrote under `improve/pass-NN/attempt/` and leaves the
  notes open; once the cause is fixed, `yt improve --resume` runs that pass
  again from the failed stage (the attempt's files put back, or the stages
  before it replayed, vfx from its own stage cache without `--fresh`, so
  nothing is authored twice), with the earlier spend counted. A pass whose
  stages all ran but whose `yt qa` filed nothing (a render host down) is
  `unjudged`: its outputs stay, and the next `yt improve` scores and judges
  it first. A reverted pass keeps what it made under `attempt/` too. When
  `yt gate` writes nothing for the new video (its preflight refuses a film
  with no captions), the old video's `gate.json` is set aside as
  `improve/pass-NN/stale-gate.json`, so `yt qa` never judges the new video
  by the old one's gate. `yt improve --rejudge` judges the last pass again
  on clean evidence: the current state is snapshotted, the attempt's files
  are put back, the video is rendered again from them, the gate and `yt qa`
  run, and the same rule keeps it or restores that snapshot; nothing
  upstream is authored or decided again.

## Composing with the SDK: the kit and the probes

The owner's rule: the creative part is a model writing code against the
sr-core SDK (`sr_core.scene`), under the harness's rules. The program is the
source and the document it saves is its output, so a revision changes the
program, never the XML. A program can make detail by procedure, compute
motion from a function and expose the parameter a review will ask about.
Hand-written XML can do none of that; the 10,841 literal keys of one
hand-authored world showed the cost.

- **The kit** (`ytstudio/sdkkit.py`). `install(dir)` gives an author's
  workspace `srkit.py` (`S`, `FRAME`, `save`), `kit.py` (`describe`, `enum`,
  `build`, `check`, `precheck`, `frame`) and `kit/`. That directory holds sr-core's own
  `SDK-GUIDE.md`; an `ENGINE.md` with the engine's capability manifest,
  every attribute the schema documents as renderer-dependent (the path
  tracer drops clearcoat, sheen, anisotropy, iridescence and dispersion) and
  the last probe run's scores; `kit/engine_facts.json`, those facts stamped
  with the engine version they were verified on (flagged when it changes;
  a fact promoted from a run carries its own `verified_on`);
  and `examples/`, programs that built,
  validated and rendered on the engine. Their values are placeholders.
  `kit.py precheck <doc or frame.png>` runs the mechanical part of the
  reviewers' checklist (`framecheck.CHECKLIST`) before a frame is paid for,
  over the author's own previews in `frames/kit/` and the gate's proof
  frames in `frames/` alike; `sr.py frame` and the vfx gate run the same
  items on every proof frame they render (`framecheck.precheck`), so what
  the reviewer is told the author was handed, the author was handed. `kit.py
  frame` stops a still at 420 s (`sdkkit.STILL_BUDGET`) rather
  than wait on a render that will not finish; `kit.py` prints the time left.
  `AUTHOR_SYSTEM`, `AUTHOR_TOOLS` and `BRIEF` are what a stage hands such an
  author.
- **The build.** `python3 kit.py build <program.py>` runs the program twice
  in fresh interpreters. A document whose bytes differ between the runs is
  refused: seed every random choice. Every document it saved then goes
  through the SDK's checks, the harness's rules (`rule:shadow`: a 3D world
  in which no light casts a shadow; `rule:band`: a 2D text or formula node
  whose box enters the caption band, rows from `design.CAPTION_FRACTION` of
  the frame height, where `yt build` burns the captions, with the move
  named) and the engine's validation, and gets
  `<doc>.provenance.json` (program and document hashes, the hash of every
  workspace module the program imported -- `imports`: `srkit.py`, `look.py`
  -- and the schema release). `sdkkit.provenance_of(doc)` lapses the moment
  the document, or a module it imported, is edited by hand. The kit counts:
  a release that changes `srkit.py` lapses every document built with the old
  one, carried sections included, by design -- the claim is that `build`
  here reproduces the document, and a rebuild makes it current again.
  Nothing gates on a lapsed provenance; the record shows it.
- **Who uses it.** Every authoring agent: `yt vfx` (one program per
  section, `vfx/vNN/<section>.py` saving `<section>.scene.xml`), `yt
  keyframe` (`keyframe/keyframe.py`), `yt assess` (`proof.py`) and `yt
  probe`.
  - Each stage's gate runs `sdkkit.build_findings` first, so the document
    it validates, frames and reviews is the program's output.
  - A missing program, a build that fails or does not reproduce, or a
    program that saved another file is an error. A hand-written document
    does not count as authored, and a hand-edited one is overwritten by the
    next build.
  - srkit records every scene document a program reads. A program that
    saves a document it read is refused (`rule:program-passthrough`).
    Reading another document for its values is only flagged
    (`rule:program-reads-document`).
  - The build judges what the program left on disk: a document changed,
    removed or renamed after `save()` is refused.
  - An author's own previews (`kit.py frame`) go to `frames/kit/`. The
    gate clears a section's proof frames before rendering them again, so
    `frames/` holds only this round's; the round before is read from
    `rounds/<stem>/rNN/frames/`, where the reviewer is pointed to it
    (`vfx.PREVIOUS_ROUND`).
  - Revisions resume the author's own session.
  - Every gated round is kept under `vfx/vNN/rounds/<stem>/rNN/` (program,
    document, provenance, proof frames). A revision's reviewer is handed the
    round the section stands on (its score, findings and kept frames) and
    says whether the revision is `better`, `same` or `worse` and what became
    of each finding (`review.previous`); a round replaces the incumbent only
    on `better`, a tie keeps it, since the score's round-to-round noise is as
    large as the gains. A revision judged `same` or `worse` ends the loop
    (`stopped` on the record): the listing was answered and nothing was
    gained, so no further revision is paid for. The next author's listing
    leads with what persists or regressed. A section ends on the round the
    verdicts promoted, so a last revision that crashes, breaks the build or
    is judged no better falls back to that round. A section whose authoring
    raises becomes an error record; the other sections are kept.
  - A proof frame the host could not render (unreachable, no engine, or an
    engine that died naming no finding: `vfx.infra_failure`) is retried
    once on the other host and otherwise filed as `infra:`, never
    `error:`. A still stopped at the deadline or killed for memory
    (`vfx.cost_failure`) gets the same one retry; when the other host
    stops it the same way, no host holds the world as written and the
    frame is the author's `error:` naming the document's cost drivers, as
    `kit.py frame` rules. A round whose only blockers are infra findings is gated
    again after `vfx.INFRA_WAIT` seconds without a session, gets no review
    while frames are missing, and the frames the first pass lost are
    recorded on the round (`rounds[].infra` in `vfx.json`, "lost to the
    host" in `vfx.md`); no revision is paid for the host's failure. A round
    the host loses twice does not pass: with no reviewed round to stand on,
    the section ends on an `error:` naming the host and `yt vfx --sections
    N`, with `stopped: host` in its budget row, so the world the gate never
    saw rendered stays out of the film (`has_errors`) and an earlier
    reviewed version of the section is kept over it (`keeps_previous`); a
    revision whose frames stay lost (or that got no review) ends the
    section on the round it stands on, `stopped: infra` (`unreviewed`).
  - Money is budgeted per section and per stage (`vfx.SpendBudget`):
    `--budget-usd` (18: what the default `--rounds` cost at the most, so the
    estimate and the budget agree) covers one section's author sessions and
    its reviews, `--stage-budget-usd` (default: that times the sections)
    covers all of them, and the estimate prints before the first agent
    starts. The reviewer is `--reviewer-model` (`opus`; never the author's
    own model: `--model opus` without a reviewer of another family, say
    `--reviewer-model sonnet`, is refused with exit 2 before anything is
    paid, as `yt probe` rules, on `yt vfx`, `yt keyframe` and `yt run`)
    under `--review-budget-usd` (2) a review, and each round's
    review cost is on the record (`rounds[].review_usd`, `budget.review_usd`).
    Each session's cap is what the budgets leave minus the cap of the review
    that follows it; a section that cannot pay another session and review stops
    ("budget") and ends on its best reviewed round, and the run prints
    spend against budget per section and for the stage (`budget` in each
    record). A session is told its cap (`sdkkit.budget_line`, and `kit.py`
    prints the budget left via `SRKIT_BUDGET_FILE`); at 80% of it
    `sdkkit.stream_round(budget_usd=)` stops the stream and resumes it once
    for the build and a still, as the 80% clock does, instead of letting
    the CLI's hard cap end it with exit 1. Spend is metered from the
    stream's usage and `spend.PRICES` (`$CLAUDE_PRICES` overrides a row). An
    unpriced model (no row anywhere) runs its first session with `--max-budget-usd` at 80% of the
    grant; on the CLI's `error_max_budget_usd` the reported cost is counted
    once and the session is resumed once for the build and a still with the
    rest of the grant.
  - `yt vfx --sections 2,3` re-authors only those sections into the next
    `vfx/vNN/` and carries the others forward from the active version
    (`carried_from`, $0). Continuity, reach and plan are then redone. Each
    record keeps the `bar` it was reviewed against and the model that
    wrote it (`author`), carried with its score.
    `--sections` refuses `--fresh`. Each section record keeps
    `inputs_sha256` (`vfx.section_inputs_digest`: the section's shots with
    their measured timings, `at` counted from its first shot so re-timing
    an earlier section unreviews no later one, its effects and cuts, the
    tokens and the approved look's digest, none for a record made as the
    look; JSON only). A section carried by `--sections`
    or `--relook` whose inputs changed since its review (or whose record has
    no digest) is unreviewed until re-authored: its score, review and best
    round move under `inherited`, it reads `reviewed: false` with a
    `warning: carried:` finding, and no model is asked; in the competition
    such a previous version is kept only over a broken attempt, as under
    `--relook`, and one carried so in an earlier run (`reviewed: false`)
    likewise; `inherited.from_version` names the version that made the
    review (`reviewed_in` on a record carried whole). `yt doctor` names
    `yt vfx --sections N` for each; the approved look, never scored, is a
    note instead. `--recover` puts such a record back as it stands.
  - Each section writes its own record as it goes:
    `vfx/vNN/rounds/<stem>/record.json`, partial after every gate
    (`finished: false`, the rounds so far) and whole when the section ends,
    written through a temporary file and one rename. 12-author is assembled
    from those files, so a stop, a kill or a crash keeps every section that
    finished (Run 2 lost eight finished sections to one kill).
  - `yt vfx --recover` rebuilds the record of a run that ended before it saved
    12-author: a section with a record goes back on the round its record
    names (a partial one on the round its verdicts promoted); one without
    goes back on its best reviewed round from `vfx/vNN/rounds/` and the
    cached reviews; a section never reviewed is recorded with an error for
    `--sections N`, and the film-wide stages run. Nothing is authored.
    Measured on the Euler film: a 6 h run killed by a wrapping `timeout` came
    back for the price of the continuity pass. Never wrap `yt vfx` in a
    timeout shorter than its clock.
  - `yt vfx --finish-now --title X` tells the run authoring that project to
    finish: it writes `vfx/vNN/FINISH` into the version being authored, and
    the run reads it between rounds (as it reads a `SIGUSR1`) to end every
    section on its best reviewed round (`stopped: finish`) and run
    continuity, reach and plan. A section already at the bar is done, not
    finished. No revision in flight is cut off; the one after it is not paid
    for (throughput item 33). The command names the version the file went
    to and warns when that version is finished (its record is in the
    dossier): a run still planning has not opened its own version yet and
    will not read it.
  - `yt vfx --take-attempt N --why "..."` overrides the competition after the
    fact: a section whose new attempt lost to the version it replaced
    (`kept_from`) gets the attempt back from `rounds/<stem>.attempt`, with the
    reason on its record (`taken`). On the Euler film the kept version of
    section 7 scored higher and could not be encoded on any 8 GB GPU.
  - A gate error that a later round of the same section fixed (the later
    round not cut off, with no error and a review; infra findings are the
    host's) is a candidate engine lesson (`vfx.fixed_errors`, AUTO-111),
    never a score gain or a verdict alone. The section's reviewer is asked
    once for it (`lesson-<stem>`, booked with the section) from the fixed
    findings, numbered, and the two rounds' kept programs and their diff (a
    round whose build failed keeps its program alone under
    `rounds/<stem>/rNN/`, never restored): topic, the SDK `elements`, one
    sentence, `cause` (engine or authoring), the `finding` it answers (its
    number, or its words with any spacing), `general`. An element the SDK
    lacks, a colour, a style token, a palette, style or font name (whole
    words, any case), or a finding that was not fixed, is asked once more
    (the refused answer forgotten), then left as a `warning: lesson` finding
    on the record; whatever fails in the step is that finding, never a
    section without its record. A section whose budget cannot hold two asks
    at `--review-budget-usd`, or a run asked to finish, is not asked (a
    `note: lesson: not asked` finding). `yt keyframe`'s lead section is
    authored by the same planner and logs its lessons too. A general lesson is
    appended to `work/lessons-log.jsonl` with the engine version and
    `label: null` (once per section and lesson, `--fresh` or not), and
    `yt doctor` notes the log as evidence to copy out of `work/`.
    `yt vfx --lessons` lists the rows; `yt vfx --promote-lesson
    ROW[,ROW]` is the only way one reaches `ytstudio/kit/engine_facts.json`
    (source `yt vfx <slug> section <n> r<from>-><to>`, its own
    `verified_on`), so every later kit's `ENGINE.md` prints it. The author's
    `kit.py` has no such command: a person decides what enters the repo.
  - `vfx.json` and `keyframe.json` record each document's `program` and
    `provenance`.
- **`yt probe`.** `--list` shows the classes, `--only` picks some,
  `--fetch-props` fills `work/.probes/` (`props.json` and `props/`) with a
  neutral CC0 set, which a run uses by default (`--props DIR` names any
  folder laid out the same way, a project root included).
  `--reviewer-model` must differ from `--model`: a model never grades its own
  work.
  - Each probe gets the first review plus up to `--rounds` (2) revisions
    while under `--bar` (8), each in the author's resumed session with the
    findings verbatim. Every round's evidence is kept under `rounds/rNN/`.
    The probe scores its best round, and a revision that scored lower is put
    back before the next one: only a measured improvement is kept.
  - `--resume --name RUN` continues an interrupted run: finished probes keep
    their rows, unfinished ones start again.
  - `kit.py frame` renders one still per host at a time, behind a lock on
    this machine, and sends a path-traced document or one with a medium to
    the encode host. Such a still takes minutes, and the still host is
    shared with work that is not ours.
  - A run that covered every probe becomes `latest.json`, and the next run
    shows each score's change against it.
  - The cause columns show what is the authoring's to fix and what is the
    engine's. One probe with one round cost $1.06 (author $0.53).

## Traps already paid for

| Trap | What happens | What to do |
|---|---|---|
| The machine suspends mid-stage | a stage waiting on a model or a render host looks idle, so the desktop sleeps and the wait is lost | every stage but `yt ls`, `yt assets` and `yt props` (`cli.LOCAL_ONLY`) holds a `systemd-inhibit` sleep and idle lock for its life; with the tool absent it warns `systemd-inhibit not found` and nothing keeps the machine awake: install it or stop the suspend by hand |
| `light/@castShadow` defaults to false | every object floats: no contact, no grounding, whatever its y | set it on the light that should ground the scene; the kit's build warns (`rule:shadow`) |
| Sections reviewed 5-6.5 while the first watch finds dozens of blocking findings | the build split a world into per-shot includes that restart at t=0 | read the build's include line and its split-world error (`split_worlds` in `build.json`; the scene is not written without `--force`); QA's `proof-mismatch` names the world and the time |
| A dome lights but is not seen | `environmentVisible` defaults to false: the sky or HDRI lights and reflects, and the background stays black | set `environmentVisible` when the environment is the backdrop |
| A glTF at the wrong size | the engine imports y-up metre models at 100 scene units per metre with a half turn about x, before the object's scale; scaling by the raw bounds makes it 100 times too big | `kit/examples/model_and_environment.py` reads the bounds, applies the node transforms and grounds the model |
| A dark metal shows no reflection | a metal's reflectance is its `baseColor`; a dark base swallows the environment and any iridescent film | give a metal a light base colour and let roughness and the environment make the look |
| Specks around an emitter | the path tracer counts an emissive surface only when a path happens to hit it, never aims at one | light a scene with lights; use emission for what glows |
| Layered materials vanish | the path tracer ignores clearcoat, sheen, anisotropy, iridescence and dispersion, silently | choose the renderer per shot knowing which it drops (`kit/ENGINE.md`) |
| A peer's message inside a stage | interactive sessions on the machine list every headless `claude -p` the pipeline starts as a peer, and a broadcast lands in that model mid-task (measured: a probe author received one) | `yt` calls `harness.isolate_sessions()`, so every engine gets `--settings '{"crossSessionInbound":"refuse"}'` unless `CLAUDE_ENGINE_SETTINGS` is set; verified that such a session refuses a message |
| `project/@fps` is a string | `fps=30` is refused at construction; it is a rational (`"30000/1001"`) | `fps=str(30)` |
| `crf` with NVENC | ignored; a default bitrate is used and nothing warns | `yt build` sets `bitrate` and `maxBitrate`; do not add crf |
| The AAC true-peak pass | the decoded AAC overshoots, the engine pulls the programme down about 1 dB | target and compensation are two numbers; `yt build --loudness` |
| Limiter after normalise | strong short peaks eat loudness; raising the target cannot fix it | `yt build` brings sfx/stinger/transition cues of 2 s or less down as a group to the master's headroom above the loudest bed (`audio_staging` in build.json); `--no-limiter` makes any remaining shortfall visible |
| Transients with no bed | nothing sustained under the taps; the limiter takes the loudness and the target is missed | `yt build` warns; the sound design needs music or ambience under them |
| `gain` saturates at ±24 dB | per track and per bus; a quiet library cannot be lifted by either alone | stage the gain across both |
| Section documents with `<styles>`, `<paints>` or `<lights>` | E12 warnings; the include is lit by the parent's lights | the build points the include at a cleaned copy and hoists them; an authored file still should not rely on them |
| Off-camera object3D | the engine reports it (only an encode computes it); the shot shows nothing | name the hero `hero-…` so the frame gate makes it an error; fix the camera or the geometry in the vfx source |
| Flat, black, clipped or dark frame | the frame gate in `yt vfx` and `yt qa`'s mechanical pass report it | the subject is unframed, unlit, overexposed or elsewhere; look again |
| Frozen window, or a jump inside a shot | `yt qa` measures it from one half-second frame window (at most 16 frames) around the midpoint; frozen needs every step and the whole window still | author movement across the shot, or ease the key; the finding names the owner |
| Hero inside the set (a penetration) | the hero's path passes through a set solid, or it comes to rest inside one; a still cannot show a merge, the reviewers mention it and never block on it, and the delivery gate has no notion of a solid | the geometry check does: `yt vfx`'s gate, `yt qa`'s mechanical `penetration` and `yt gate`'s blocking item evaluate the world every 0.25 s (`scene-render eval`, no GPU, one job) and fail a `hero-` box inside a set solid by more than a tenth of its smallest dimension, naming time, object and depth; key its path to clear the set, key the rest to touch and declare it with `<meta name="hero-rests" value="<hero> <surface> <seconds>"/>` so floating is caught too; a hero that touches an object by design (a cap on its strand) is not pulled apart but declared joined with `<meta name="hero-attached" value="<hero> <object or prefix*> ..."/>`, and the nodes it follows by `parent` never count |
| Hero reads merged into the set | the hero is above a surface in 3D, but on screen it lies over that surface's top with no shadow of it there and too little clearance for parallax, so the viewer sees it sunk into the surface; no box intersects | the same check projects the evaluated camera (calibrated against a render to under 1 px): over a farther surface's visible top by more than 30% of its screen area, under 25% of the camera distance above it, no shadow of it on the surface, for 0.5 s or more, is `reads merged`, an error in `yt vfx`, a blocking mechanical finding in `yt qa`, a blocking row in `yt gate`; cast its shadow onto the surface, lift the line of sight toward the surface's plane, or frame them apart |
| A pilot's sync over the film's | the old remote dir was keyed by slug only; a pilot overwrote a finished film | fixed: every job owns `/tmp/yt-render/<slug>/<job>-<tag>/` hardlinked from the mirror, and only the encode writes `render.json` |
| A job writing into a tree the mirror lacks | the mirror excludes `qa/` and other output trees, so `-o qa/.../x.png` had no parent directory on the host | fixed: the job script creates the parent of every `-o`/`--report` path before the engine runs |
| Sampling a shot's start | lands in the inter-take gap and shows an empty stage | sample midpoints, as `yt qa` does |
| One caption per shot | 145 characters do not fit; clipped mid-sentence | split on word timings, two lines at most; `design.caption_cues` is the one splitter (limits `design.CAPTION_*`), and `narration.vtt`/`.srt` are written from it; a cue too fast to make legal is placed once and is a build finding naming it (`design.caption_findings`) |
| `key/@value` is a string | a float is refused at construction | write values as text |
| `<segment>` needs schema 1.2 | a 1.1 document is refused | raise the version where it is used |
| An empty enumeration | means free string, not nothing allowed | do not filter against it |
| A User-Agent with no contact | Wikimedia answers HTTP 429 to every request, so Commons files can be neither verified nor downloaded | the default agent names the project's URL; a `--user-agent` of your own must too |
| Openverse unpaced | an anonymous client gets 20 requests a minute; a parallel shot list bursts past it and each refusal used to vanish | the searcher paces itself, and failed searches are printed per catalogue |

## Known limits

- **A world is one setting with one camera.** `yt vfx` authors one
  `<symbol>` per world (set, materials, light rig, `hero-` model, one
  camera keyed across it) with the shots as phases at their offsets, and
  `yt build` includes consecutive shots of it as one continuous
  `<include>`, so a camera move reads as a take. A section is one world
  unless its effects plan cuts it: the plan names the section's `setting`,
  each cut the `setting` of the world it starts (AUTO-143), warranted when
  the story moves to another place, scale, time or real-world record, or
  when no camera move can bridge the change; inside one setting one
  continuous take stays the rule. Each section's planner sees the film's sections in order, each with
  its opening picture. Across worlds the hero keeps its scale, material and
  colour, and the grade and type language carry over.
- **The sound is Freesound previews, not masters.** `yt sound --fetch`
  fills each cue with the best CC0/CC-BY hit's high-quality mp3 preview and
  `yt build` places it on a bus per role, every non-narration bus ducked under the narration and the
  master normalised once, in the mode the sound designer decided on the
  palette (sound.json's `normalize`: `integrated`, `dynamic` with its reason,
  or `none`; `--normalize` overrides, `integrated` when nobody decided, and
  `build.json` records `normalize_from`). The fetch measures each file on a render host, by
  loudness for beds and peak for effects, writes the cue's `gain_db`, and refuses a source the
  track's gain cannot reach or whose noise floor the lift would put within 12 dB of the
  narration, trying the next-ranked hit; with no host it says `NOT LEVELLED`. Cues
  are searched, downloaded and judged `--jobs` at a time (default 4), the verdicts
  applied in cue order. A cue
  nothing matched stays `unfilled`, shows under `audio_missing` in
  `build.json`, and a design of which nothing could be placed makes the
  build unpublishable. `yt sound --review` checks the sheet's own rules
  before the model listens back (`rule:overlap`, `rule:level`, `rule:clock`,
  `rule:no-bed`, and `rule:fade` for a bed with no fade out); the model
  answers each still standing through `revise` (one change often clears a
  cue's second rule, which is then not asked), the listen-back is told what
  it kept and why, and a rule flag it keeps stands in
  `review.unresolved` with its reason, which `yt build` records as a
  `warning:` naming the cues. `yt sound --synth` realises every cue still without
  a file as original procedural sound: an agent writes and runs
  `sound/synth/make_audio.py` (NumPy and SciPy, no samples, a fixed seed),
  every WAV must pass `check.py` -- the pipeline's own check: 48 kHz stereo
  16-bit, the cue's length, neither silent nor clipped -- before its cue
  gets a `file`, and the cue records the script's sha256 so the sound can
  be regenerated. The spotter writes each cue for those two paths, a
  `search_description` in a sound library's words and a `synth_spec` of the
  sound itself, and the fetch queries come from the description, never from
  the cue's name. `--fetch --synth` fetches first and synthesises the rest;
  a later `--fetch` keeps a synthesised cue. `yt doctor` warns while any
  cue has no file and names that command.
- **Sound follows the words; cuts do not follow the beat** (AUTO-140). A
  cue's `anchor` lets `yt build` re-place it when a take is redone
  (`cue_anchors`: kept, moved, lost); a sheet spotted before anchors places
  on its seconds, and re-spotting with `yt sound --fresh` is the fix. A music
  or stinger file's `beat_grid` is measured on the render host with its
  levels (none without a host, or for a bed with no steady beat), and
  build.json's `beat_sync` reports how near cuts and reveals land to beats
  and downbeats: a measure for the reviewers, never a bar, and cuts stay on
  the narration. Nothing judges picture and sound together yet: `--listen`
  gets a text timeline to judge timing by ear, and `--watch` sends no sound.
  The search window, the beat tempo range, its confidence, the 4/4 downbeat,
  the 83 ms tolerance and the host's envelope chain are TO VERIFY.
- **The ocean is a height field.** It cannot overturn, break or pinch off a
  droplet. A liquid that must do that is a GLSL `effect type="shader"` over
  a rect, which the engine's effect pass renders with full ray intersections:
  a Shadertoy `mainImage` reading `iTime` and `iResolution`, each `uniform
  float` with a `// = default` that a `<param>` child overrides. The vfx
  workspace's `examples.scene.xml` carries one (`fx-surface`, with
  `shaders/ring-surface.glsl` beside it), validated and rendered on this
  engine, and `schema.md` lists every value of every enumeration, so the
  route is visible to every author.
- **Sections are reviewed alone, then together.** After authoring, `yt vfx`
  shows one proof frame per world (the middle one, at most two per section,
  each labelled with its setting) side by side to a continuity review on the
  reviewer's model (`--reviewer-model`; `continuity` in `vfx.json`: one film
  or not, a score, each break with the section that should change and the
  fix, and `monotony`, each run of sections that stays in one set, scale and
  look longer than the story warrants). A planned change of setting is not a
  break. The verdict records who gave it (`continuity.reviewer`: model,
  author, `authors` -- every section's `author`, so a section carried from
  another model counts, None for one carried from before authors were kept
  -- and the engine the reviewer ran on). A break marked `blocking` by a
  reviewer of another family than every author (`flags.own_reviewer`)
  keeps `yt qa` off READY (`vfx
  continuity: section N (<heading>): <problem>` among the blocking items); a
  same-family or unrecorded verdict, a major break and `one_film: false`
  alone never change the verdict. A section `--recover` or `--take-attempt`
  rebuilds from its rounds records this run's model and `--bar`. `yt doctor`
  says on a blocking break's row why it does not block QA: no reviewer of
  another family, or a section whose author is unrecorded or of the
  reviewer's family (re-author it, or `yt vfx --fresh`). It warns once per
  section owning a blocking or major break, once when the sections do not read as one film
  with no serious break, and once per monotony run (at most three); a
  skipped or failed review is a note. A section whose best reviewed round
  ended under its bar (`authored[].bar`) is kept, never a hard stop, and is
  a doctor warn naming why it stopped (`budget spent`, `revision did not
  change it`, ...). These rows name `yt vfx --sections N`, so `yt improve`
  re-authors that section alone; none is a fail, so the lane's vfx stage is
  never held by them. Every author and reviewer the stage
  starts reports once into `vfx.json`'s `usage`, so it is the stage's whole
  bill.
- **Vision reaches the model only on the agentic Read path.** `inspect`
  sees frames; a text-only decision does not.
- **An inspection may be capped by turns.** `Harness.inspect(...,
  max_turns=N)` passes `--max-turns N` to the agent that looks
  (`EngineConfig.max_turns`, AUTO-116); its stop, `error_max_turns`, is a
  permanent error and a failed, billed row, never retried or saved. N is
  1 or more (0 is refused). No stage sets a cap yet; Codex has no
  equivalent (it runs uncapped) and the chat bridge refuses one.

## Running a video as a govern lane

The workspace's `.govern/` makes this repo a govern workspace, and
`.govern/processes/yt-video.json` is the process for a video: every
pipeline stage, then `deliver`, each with `requires: check`, and the owner's
gates `script` (before anything is recorded or animated) and `final`. The
file is generated from `workspace.STAGES` by `python3 ytstudio/lane.py
process --write`, and `tests/test_lane.py` fails until it is regenerated
when the stages change. Every stage's check is `ytstudio/lane.py check`,
which answers from the same files `yt doctor` reads: the stage's artifact
and no fail it owns (`render` also needs every render job report to have rendered, `vfx` every section reviewed without an error; warnings never block); `keyframe` also needs the approved look; `deliver`
needs doctor's delivery verdict. A lane's folder is
`.govern/projects/<slug>`, and its video is `work/<slug>`:

```bash
govern new <slug> --process yt-video --title "..."
govern work <slug> script          # the brief: stage, exit, gates, commands
govern advance <slug>              # runs the stage's check; refuses on a fail
govern approve <slug> script --quote "<the owner's own words>"
```

Gates open only on the owner's verbatim words. Run renders as govern jobs
(`govern run <slug> <cmd> --bg`, then `govern wait`) so no model waits on
them.

## Never, in production work

- No subagents or the `Agent` tool inside a stage's harness call; one sealed
  session per decision or per section author, with its own budget and cost
  line (`TaskRunner(jobs=N)` is how a program fans out).
- No polling a render from inside a model call; renders are code waiting on
  ssh, and the result is read back from `render.json`.
- No raw logs or whole files in a prompt. A decision gets its data as
  labelled JSON (`brief()` in the harness); an inspection reads the files it
  needs with its own Read tool.
- No overwriting an approved artifact; write the new one beside it.
- No hand edits to `scene.xml` or to a rendered file to make a check pass.
- No cargo builds, engine installs or ffmpeg work on this machine (`yt qa
  --listen`'s stream copy and ffprobe excepted: neither encodes).

## Where to read more

- `docs/production.md`: the ten categories, the bar, the bands, the
  improvement loop, the traps, the final report.
- `docs/compute.md`: the two machines, the measurements, the reservation
  convention.
- `docs/narrative/`: the story craft corpus, numbered rules with their
  sources, a `Check:` and the `Stages:` they bear on, and in its README the
  strength of each rule; what is contested and what is folklore. Read it
  before changing a story prompt, a lint or a reviewer rubric.
  `ytstudio/narrative.py` is the rubric drawn from it; `test_narrative_rubric`
  holds each criterion's rule ids and strength to the corpus, and
  `test_narrative_calibration` the calibration set and its entry point;
  `test_qa_calibrate` the QA judge's fixture films and theirs.
- `README.md`: `claude_engine.py` and each stage for a newcomer.
- `ytstudio/harness.py`: how a stage asks the model and caches the answer.
- `tests/`: `python3 -m unittest discover -s tests`. The ones behind this
  file: `test_vfx_world`, `test_framecheck` (one world, the frame gate, the
  versions), `test_build_include`, `test_build_audio` (the cleaned copies,
  the continuous includes, the mix), `test_sound_fetch`, `test_freesound`,
  `test_render_contract`, `test_gate_remote` (the job directories, the
  reports, the host), `test_qa_motion`, `test_props`, `test_keyframe` (the
  elements, the briefs, the review loop, the approval, the doctor),
  `test_scope`, `test_run_wiring` and `test_skill`; and `test_delivery`
  (doctor's delivery verdict), `test_sound_synth` (the synthesis contract),
  `test_capability_surface` (the full cheat-sheet and the shader example),
  `test_vfx_continuity` (the side-by-side review, the one account, its
  doctor rows), `test_qa_vfx_blockers` (a continuity break that blocks READY),
  `test_doctor_vfx` (a section under its bar), `test_lane` (the govern process and its checks), `test_sdkkit` (the kit,
  the build's determinism, rules and provenance, the examples) and
  `test_probes` (the suite, the separate reviewer, the revision loop, the
  run table).
