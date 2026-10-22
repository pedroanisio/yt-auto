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
   install or run the engine here.

## One word per concept

The same meaning is spelled the same way in every stage that has it
(`ytstudio/flags.py`, asserted by `tests/test_stage_flags.py`). An old
spelling still works for one release, hidden from `--help`, and prints a
one-line note.

| Concept | Flag | Stages | Old spelling |
|---|---|---|---|
| revision rounds the author gets | `--rounds` | `keyframe` (with `--sheet`), `vfx`, `probe`, `script` (the fact-check's revisions; the plan review sends a plan back once) | `--revisions` (`vfx`) |
| the review score under which work goes back | `--bar` | `keyframe` (with `--sheet`), `vfx`, `probe`; not `script`, whose plan review has no score: the verdict (accept, revise, reject) is the bar | `--author-bar` (`vfx`) |
| a spend cap in dollars | `--budget-usd` (`--stage-budget-usd`, `--synth-budget-usd`, `--review-budget-usd` cap the whole stage, the synthesis, a review) | `research`, `keyframe`, `vfx`, `probe`, `assess`, `improve`, `sound`, `run` (the whole run); `--review-budget-usd` on `script` too (the plan review) | `--budget` (`research`) |
| the model that reviews, never the author's own: it must differ from `--model` by family, or the stage is refused before anything is paid | `--reviewer-model` (default `opus`; `--review-budget-usd` is what one review may cost, 2) | `keyframe`, `vfx`, `probe`, `script` (with `--review`), `run` (handed to script, keyframe and vfx) | |
| ignore what is cached and do the stage again | `--fresh` | `research`, `keyframe`, `vfx`, `props`, `voice`, `sound`, `build`, `retime`, `review`, `qa`, `gate`, `pilot` | |
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
| `yt research` | the idea | `dossier.json`, `README.md`, `claims.csv`, `assets.csv`, `raw/`; with `--download`, `downloads/` | yes |
| `yt styles` | dossier | `styles.json`, `styles.md` | yes |
| `yt script` | dossier, styles | `script.json`, `script.md`, `narration.txt`, `shotlist.csv` | yes (`yt script --revise 1.3,2.1`, `yt script --revise retime` or `--revise feedback` sends only those lines back, see below; the narrative rubric is in `ytstudio/narrative.py`: seven criteria of four labelled levels, a coverage-shaped story review with the verdict reject, consider or recommend (not yet wired, AUTO-93), and the plan review's schema. `--review` has `--reviewer-model`, never the author's family, read the beat sheet before any section is drafted, about $0.50 more: `plan-review` reads the plan against the train, stakes, open loops, reveal setup and ending and the `rule:`/`house:` pre-checks, under a role of its own (accept, revise or reject, not the story review's verdicts); a reading whose fields contradict its verdict is asked once more as `plan-review-again`; revise sends it back once as `plan-2` and `plan-review-2`; a reject, or a second revise, exits 1 before a section is written and leaves no `script.json`, its reading and spend kept in `plan_review.json` until a run gets past the plan; `--report-only` records a revise and drafts on, but a reject still stops. There is no `--bar`: the verdict is the bar. The record is `script.json` `plan_review` and the `## Plan review` of `script.md`; the same reviewer then reads the drafted pictures without the words, `picture-check` and `revise-picture`, recorded as `picture`, see "Say it, show it") |
| `yt props` | script, styles (or phrases given) | `props/<kind>/<id>/`, `props.json` | yes (two decisions: `phrases`, the search phrases picked from the catalogue's vocabulary, and `keep`, the candidates worth building with, each with its `fit` note; the files are Poly Haven, all CC0) |
| `yt voice` | script | `voice/`, `voice.json`, `narration.vtt`, `.srt` | yes (casting, retakes) |
| `yt retime` | script, voice | `retime.json`, the screenplay's measured lengths (`script.json.bak` kept) | yes (accept, rerecord, rewrite, keep; a `rerecord` prints `yt voice --redo <labels>` and a `rewrite` prints `yt script --revise retime`) |
| `yt keyframe` | script, voice, styles, props (what `yt vfx` reads) | the vfx plan, the lead section in `vfx/vNN/`, `keyframe/keyframe.png`, `keyframe/frames.png`, `keyframe.json`, `keyframe.md` | yes (the vfx plan; one author agent, one attempt, no vision review; `--sheet`: the older composed frame) |
| `yt vfx` | script, voice, styles, props | `vfx/vNN/<section>.scene.xml`, `vfx/vNN/frames/`, `vfx.json`, `vfx.md`, `vfx.csv` | yes (one author agent per section, a frame review per round) |
| `yt sound` | script | `sound.json`, `sound.md`, `cues.csv`; with `--fetch`, `sound/` and `sound/index.json` | yes (spotting; `yt sound --review` listens back and fixes its own important and blocking findings, one revision of the cue's design fields each, two rounds at most, and `yt sound --review --report-only` only reports them; the fetch judges each candidate that passes the level checks against its cue, from its evidence and a spectrogram, and records the verdict in the cue's `judgement`) |
| `yt build` | all of the above | `scene.xml`, `build.json`, `vfx/vNN/<section>.include.scene.xml` | yes (coverage, captions, tail, blocking) |
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
from `codex_engine.PRICES` and `$CODEX_PRICES`).

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
`--quick` drops the deliberation, polish, fact-check, plan review
(`yt script --review`) and listen-back passes and renders at draft). It always passes `--fetch --synth` to `yt sound`, so
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
is what the vfx and keyframe briefs offer the authors.

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

**The fact-check and the story review bind the picture.** `yt script --verify` writes its flags into script.json (`check`); the story review its flags (`review.flags`, once AUTO-93 writes them) and `yt script --review`'s picture-check the flags it left standing (`picture.flags`). Their blocker and important flags reach the people who draw the film, verbatim (`vfx.picture_constraints`): the vfx planner, each section's brief and reviewer (the whole-film flags plus that section's shots), and the keyframe brief, the fact-check's under `## Fact-check constraints` and the other two under `## Story constraints` (`vfx.constraint_lines`); a minor one binds nothing. A story flag on a section and no shot (n 0, `section` k) binds that section's shots only, printed as `section k`. A claim the narration avoids can still be shown wrong by a picture, and a claim no picture shows is not told.

**Say it, show it (AUTO-98).** With `--review`, after drafting and the polish and before the narration rules, the reviewer's model reads the screenplay's pictures with the narration taken away (`picture-check`, under `narrative.picture_role()` and `narrative.PICTURE_SCHEMA`): each shot's `visual`, `shows` and `on_screen_text`, and the plan's beat for each section, never a line of narration (direction 4, Fong's picture-off test). It writes each section's claim from the pictures alone, then says whether that is the beat and where the thread is lost; a section left unread is asked once more (`picture-check-again`) and then recorded as a `note:`. Per shot it reports a figure of 1,000 or more with no comparison and no picture (attention 15), a mechanism the shot says it demonstrates that its picture does not, and a list with no count or labels (attention 22). Each is a flag (`script.picture_flags`; important, an unsignposted list minor) answered by one `revise-picture` decision, the only revision that may change a shot's `visual`, `shows` and supers (`PICTURE_REVISE_SCHEMA`); every other revise ignores them, so the fact-check's still rewrites the narration only. What stands (a flag left unanswered or unchanged, every flag when `revise-picture` failed or under `--report-only`) is script.json's `picture.flags`, a `warning: picture:...` in `findings` and the `## Picture check` of `script.md`. A flag the writer kept with its reason is `picture.kept` and a `note:`, and binds nothing: the reviewer read no narration, so a figure compared aloud (attention 15 accepts the comparison in the same or the next sentence) or a claim the line carries looks bare to it, and the writer, who sees both, is the one who answers. A flag on a shot `revise-picture` changed in any way (its line, a super, its picture) counts as answered; the pictures are not read a second time, so a section flag can go while the section still misses its beat. `yt script --revise` moves the picture flags of a shot it changes to `picture.stale`, out of what binds vfx, and drops their findings lines. A failed check is a `warning:` and changes nothing. A super that prints the line is counted, not judged: `rule:wall-of-text` below. No treatment, colour or layout is named in the rule or the prompt; motion is measured from frames at vfx and qa, not here.

**The screenplay is a story brief (AUTO-90).** `yt script`'s plan names the
film's `promise` and `payoff`, its `train` (who wants what, what is in the
way), the `stakes` and `viewer_stake`, the `misconception` it breaks (cited
as `[M1]` from the research block, which numbers the dossier's sourced
misconceptions and names its identifiable case), the `audience` (lay,
enthusiast, expert), the `reveal` and the section that constrains it, a
question ledger (`questions`, each opened in one section and answered in
the same section or a later one, or marked left open, `closed_in` 0, with
`left_open_why`) and the `ending` (`payoff_line`, `callback`, `next_film`). Each
section names the questions it `opens` and `closes`, its `connective` to the
one before (and, but, therefore, because), a `rehook`, an `escalation` and
an `anchor_visual`. With `--outline` the outline keeps its sections and these
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

**The narration is measured against rules (AUTO-94).** `script.RULES` are
counted from the text, each against a bound in `script.BOUNDS` that names
its corpus rule (`docs/narrative/`) and its strength; none judges by a word
list, and no folklore threshold or vendor hook window is used. The error
tier: `rule:filler`, `rule:sentence`. The important tier:
`rule:unspoken-number`, `rule:legibility` (the older checks; their
script.json keys stay), `rule:wall-of-text` (an on-screen string over six
words, a numeral with its separators counted as one, or more than four
words of its own line printed verbatim: direction 2's check, AUTO-98), `rule:numbers`, `rule:hook-subject`,
`rule:hook-answer`, `rule:payoff-last`. The house tier, recorded as
`warning: house:rule:x` and never revised: `rule:clause`, `rule:passive`
(English only), `rule:person`, `rule:pace`, `rule:density`,
`rule:hook-promise`, `rule:hook-stakes`, `rule:end-signal`, `rule:recap`.
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
| `yt gate` | the delivery gate (loudness, true peak, scopes, black and frozen frames, captions), run on a render host that has ffprobe and the engine, triaged by the model; `stopped_by` names the requirement that stopped it and the owning stage | `gate.json` |
| `yt review` | the PDF review sheet, read by the model | `review.pdf`, `review.json` |
| `yt qa` | the publication scorecard per pass: one frame window per sampled midpoint (one `--frames A..B` job each, the still its middle frame), a contact sheet, the mechanical findings (flat, clipped, frozen, jump, off-camera), then the model's review | `qa/pass-NN/` (`frames/`, `frames/burst/`, `samples.json`, `contact-sheet.png`, `scorecard.json`, `findings.md`), `qa.json` |
| `yt improve` | the improvement loop of `docs/production.md` §8 as code: routes each QA finding to the stage that owns it, writes that stage a revision brief the harness puts in front of every decision it makes, re-runs the earliest routed stage and everything downstream, scores a new pass, keeps it or restores the snapshot; the dry run estimates per stage from measured reports (marked measured, like or assumed), a vfx pass names only the routed `--sections` and a script pass whose findings all name a shot revises only those lines (`--revise feedback`); a `--model` of the reviewer's family (`opus`) is refused with exit 2 before any stage runs | `feedback/<stage>.md` (+ `.json`), `improve/pass-NN/`, `improve.json`, `improve.md` |
| `yt doctor` | the toolchain and every video: what is missing, stale or failing, and the command that fixes it. No model. | `--json FILE` |
| `yt assess` | can a described video be produced at all; proves it with a frame | `work/.assess/<slug>/` |
| `yt probe` | the realism probe suite: fixed neutral briefs, one per class of material, light or motion, each composed by an agent coding with the SDK through the kit, built and framed by the harness, scored by a different model that names each defect's cause (authoring, engine, asset), revised in the same session while under `--bar` | `work/.probes/<run>/` (`summary.json`, `summary.md`, one workspace per probe), `work/.probes/latest.json` |
| `yt ls` | which videos exist and how far each got | |
| `yt assets` | search open catalogues for one shot's footage | |
| `yt props` | fetch CC0 3D props (glTF models, HDRIs, texture maps) from Poly Haven, by phrase, or with none chosen and vetted by model decisions over the script and styles ("nothing fits" keeps nothing); the vfx brief lists them | `props/<kind>/<id>/`, `props.json` |

Keys load from `--env-file`, then `$YT_ENV_FILE`, `./_tmp/do_not_delete.env`,
`./.env`, `~/.config/pals-notes/.env`; `yt sound --fetch` wants
`FREESOUND_CLIENT_ID` and `FREESOUND_CLIENT_SECRET` (the secret is the
token), and without them nothing is fetched. Every stage that asks the
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
  On .13 ffmpeg lives inside the AudioForge venv, not on PATH.

## Delivering: the order and what "done" means

`build.json`'s `publishable` only says the build has no placeholder and no
blocking editorial note. It is not a delivery verdict. The order is:

1. `yt build`, then `yt render --quality draft` and look at it.
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
   `ytstudio/audiotools` and shipped to the host with the gate; `yt doctor` and
   `--preflight` name a missing one; its tail check is the bundled stdlib `tail_check.py`, which judges the effects after the last word by measuring them) (on a render host; `gate.json`'s `stopped_by` says which
   requirement stopped it and which stage owns the fix; a wordless film passes it with the descriptive captions `yt voice` writes and `voice_end` = the last shot's start), then `yt review
   --engine rs`, then `yt qa`.
6. READY TO PUBLISH only when `yt qa` reports an overall of 9.0 with no
   category under 8.5, nothing blocking and no placeholder. A blocking
   mechanical finding (flat, clipped, frozen, a jump, an off-camera object
   on the hook, the ending, the payoff shot or more than one sampled shot) blocks on its own
   and caps `visual_quality` and `animation_vfx` at 6 until it is fixed.
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
   are 2.0 by rule. Each pass keeps its frames, frame windows, contact
   sheet, scorecard and findings under `qa/pass-NN/`, so the next pass is a
   comparison and a category that drops 0.5 is a regression.
7. `yt doctor --title X` aggregates all of it and names the next command.
   Exit code 1 means something fails. Its `delivery` findings are the
   delivery verdict, read from files and never from `build.json`: a fail for
   a render below `final`, any warning or unsupported construct in the
   engine's report (what `--strict` refuses), a render short of the whole
   programme, a programme with no audio track, no technical `gate.json` or
   one not `deliverable`, no `qa.json` at READY TO PUBLISH, or a gate or QA
   pass older than the newer of `video.mp4` and `scene.xml` (QA records the
   digest of the scene it judged, compared instead where present), or a video
   older than `scene.xml`. Only with none of them does doctor
   print `delivery: READY TO PUBLISH`.

The frame has three bands at 1920x1080, measured from the top: supers and
credits in rows 0 to 230, the stage in 230 to 820, and captions in 820 to
1080, where nothing else may enter.

## The improvement loop: `yt improve`

QA names an owner for every finding; `yt improve --title X` routes them
back. It starts from the latest QA pass (`--from-pass pass-NN` to pick one;
a pass older than `video.mp4` or `scene.xml` is scored again first), with
`gate.json`'s blocking rows and `yt doctor`'s fails and warnings beside it.

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
- **Feedback.** `feedback/<stage>.md` (and `.json`) holds the brief with
  the findings verbatim, timestamps and the pass they came from. Every
  `Harness` of that stage puts it in its system prompt as revision notes
  that outrank the house standards, and the prompts a stage builds itself
  (the vfx planner and authors, the keyframe author, sound, script and
  styles, the synthesis brief) take it through `harness.with_feedback`. A
  pass that consumed the notes moves them to `feedback/closed/pass-NN/`.
- **A pass.** The earliest routed stage with the flags that force a new
  decision (`--fresh`; `yt vfx --fresh` opens a new `vfx/vNN/`; a script
  pass whose findings all name a shot runs `yt script --revise feedback`,
  those lines only, and the full `--polish --verify` otherwise, or when one
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
  appears; otherwise the snapshot is restored (for vfx that means
  `vfx.json`'s active version; the new `vfx/vNN/` stays on disk).
- **Stops** at READY TO PUBLISH (or `--target` with nothing blocking), at
  `--max-passes` (3), when the next pass's estimate exceeds what is left of
  `--budget-usd` (15) -- `spend.stage_estimates` prices each stage of the
  pass from its own report (`measured`), else from a like project (`like`),
  else an assumed default (`assumed`), and only a pass with nothing measured
  falls back to the last pass's cost; `--dry-run` prints the table -- when a
  defect survives three briefed passes (a mechanical one by kind, shot and
  objects; a prose one by a cached model decision), or when only human
  decisions remain.
- **Report.** `improve.json` (each pass's routing, briefs, argv, scores
  before and after, keep or revert with the numbers, cost, the stop) and
  `improve.md` in the shape of `docs/production.md` §11. `yt improve
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
  with the engine version they were verified on (flagged when it changes);
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
    (`carried_from`, $0). Continuity, reach and plan are then redone.
    `--sections` refuses `--fresh`.
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

- **A section is one world with one camera.** `yt vfx` authors one
  `<symbol>` per section (set, materials, light rig, `hero-` model, one
  camera keyed across it) with the shots as phases at their offsets, and
  `yt build` includes consecutive shots of it as one continuous
  `<include>`, so a camera move reads as a take. A cut inside a section has
  to be asked for in the plan; it splits the section into two worlds.
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
  shows one proof frame from each section side by side to a continuity
  review (`continuity` in `vfx.json`: one film or not, a score, each break
  with the section that should change and the fix). `yt doctor` warns when
  the sections do not read as one film. Every author and reviewer the stage
  starts reports once into `vfx.json`'s `usage`, so it is the stage's whole
  bill.
- **Vision reaches the model only on the agentic Read path.** `inspect`
  sees frames; a text-only decision does not.

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
- No cargo builds, engine installs or ffmpeg work on this machine.

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
  holds each criterion's rule ids and strength to the corpus.
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
  `test_vfx_continuity` (the side-by-side review, the one account),
  `test_lane` (the govern process and its checks), `test_sdkkit` (the kit,
  the build's determinism, rules and provenance, the examples) and
  `test_probes` (the suite, the separate reviewer, the revision loop, the
  run table).
