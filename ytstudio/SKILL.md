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
   once, then fail or record the fallback as a finding.
4. **Edit the owning stage, never the output.** `scene.xml` is compiled by
   `yt build`; the `vfx/vNN/*.scene.xml` documents are authored by `yt vfx`.
   Fix the source stage and re-run. Authoring is versioned: every run lands
   in the next `vfx/vNN/`, `vfx.json` names the active one, and `yt vfx
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
   hoists them (the file's tokens win, design fills the rest) and points the
   include at a cleaned copy, `vfx/vNN/<section>.include.scene.xml`, with
   the tokens inlined and those sections dropped, so the engine raises no
   E12 warning. The authored file is never written.
8. **A placeholder is disqualifying,** not deductible. While any remain the
   verdict is NOT READY whatever the other scores say.
9. **Nothing heavy runs on this workstation.** Renders, encodes, mixes and
   the engine's validation go to the render hosts. Never `cargo build`,
   install or run the engine here.

## The stages and their files

Everything for one video lives in `work/<slug>/`. Each stage reads what the
ones before it wrote, so paths are never passed by hand; `--title` matches a
project on disk, an idea in `work/ideas.json`, or is taken as the title
itself, and `--project DIR` names a directory directly.

| Stage | Reads | Writes | Model |
|---|---|---|---|
| `yt ideas` | a niche | `work/ideas.json` | yes |
| `yt research` | the idea | `dossier.json`, `README.md`, `claims.csv`, `assets.csv`, `raw/`; with `--download`, `downloads/` | yes |
| `yt styles` | dossier | `styles.json`, `styles.md` | yes |
| `yt script` | dossier, styles | `script.json`, `script.md`, `narration.txt`, `shotlist.csv` | yes |
| `yt props` | script, styles (or phrases given) | `props/<kind>/<id>/`, `props.json` | no (Poly Haven, all CC0) |
| `yt keyframe` | styles, script, props, dossier assets, the assessment | `keyframe/keyframe.scene.xml`, `keyframe/keyframe.png`, `keyframe.json`, `keyframe.md` | yes (the elements; one author agent; a vision review per round) |
| `yt voice` | script | `voice/`, `voice.json`, `narration.vtt`, `.srt` | yes (casting, retakes) |
| `yt retime` | script, voice | `retime.json`, the screenplay's measured lengths (`script.json.bak` kept) | yes (accept, rerecord, rewrite, keep) |
| `yt vfx` | script, voice, styles, props | `vfx/vNN/<section>.scene.xml`, `vfx/vNN/frames/`, `vfx.json`, `vfx.md`, `vfx.csv` | yes (one author agent per section, a frame review per round) |
| `yt sound` | script | `sound.json`, `sound.md`, `cues.csv`; with `--fetch`, `sound/` and `sound/index.json` | yes (spotting; the fetch asks nothing) |
| `yt build` | all of the above | `scene.xml`, `build.json`, `vfx/vNN/<section>.include.scene.xml` | yes (coverage, captions, tail, blocking) |
| `yt render` | `scene.xml` | `video.mp4`, `render.json` (a still, a validation, an audio pass or a pilot writes `render-<job>.json`) | no |

`yt run` (`yt run --title X`) walks research, styles, script, props,
keyframe, voice, retime, vfx, sound, build, render in that order -- `yt
props` after the script so the vfx brief can list real assets, `yt keyframe`
right after it so the look is locked from those assets before anything is
authored (`--skip keyframe` leaves it out), `yt retime` between voice and
vfx so nothing is authored against the screenplay's guess once the read is
measured (a `rerecord` verdict records those takes again and retimes once
more; `rewrite`, or a read sent back twice, stops the run) -- and skips a stage whose output exists unless it is named in
`--redo` (`--redo all` repeats everything, `--skip` leaves stages out,
`--quick` drops the deliberation, polish, fact-check and listen-back passes
and renders at draft). It always passes `--fetch` to `yt sound`;
`cli.run_extras` lists what each stage gets and `cli.stage_argv` builds the
argv (`yt props` takes only `--project` and `--quiet`).

**The look-lock.** `yt keyframe` is a studio's style frame: one 1920x1080
frame composing the film's most qualified visual elements together in the
chosen treatment -- the hero in its environment under its key light, the
materials that matter, the palette roles as swatches, one type sample in the
film's language, the camera language -- each labelled with its source (a
props id, a licensed asset, or authored). One harness decision picks and
ranks the elements (at most eight) from styles, script, props, the dossier
and the assessment; an author agent composes `keyframe/keyframe.scene.xml`
in the same workspace `yt vfx` gives its authors; the frame renders on the
still host; `framecheck` runs the eyeless checks; a vision review scores it
and the findings go back verbatim for up to `--rounds` (2) while under
`--bar` (7). The operator then runs `yt keyframe --approve`, or `yt keyframe
--reject "notes"` so the notes lead both model calls next time; `--fresh`
picks and composes again. An approved keyframe is the look every vfx world
inherits (`keyframe.approved_keyframe`), and `yt doctor` warns when the
animation was authored without one or predates it.

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

**The fact-check binds the picture.** `yt script --verify` writes its flags into script.json (`check`). Its blocker and important flags reach the people who draw the film, verbatim (`vfx.picture_constraints`): the vfx planner, each section's brief and reviewer (the whole-film flags plus that section's shots), and the keyframe brief. A claim the narration avoids can still be shown wrong by a picture.

Beside the chain:

| Tool | What it does | Writes |
|---|---|---|
| `yt retime` | writes the measured narration durations back into the screenplay | `retime.json` |
| `yt pilot` | cuts nested short approval reels at full fidelity; the model picks the cuts | `pilot.json`, `pilot.scene.xml` |
| `yt gate` | the delivery gate (loudness, true peak, scopes, black and frozen frames, captions), run on a render host that has ffprobe and the engine, triaged by the model; `stopped_by` names the requirement that stopped it and the owning stage | `gate.json` |
| `yt review` | the PDF review sheet, read by the model | `review.pdf`, `review.json` |
| `yt qa` | the publication scorecard per pass: one frame window per sampled midpoint (one `--frames A..B` job each, the still its middle frame), a contact sheet, the mechanical findings (flat, clipped, frozen, jump, off-camera), then the model's review | `qa/pass-NN/` (`frames/`, `frames/burst/`, `samples.json`, `contact-sheet.png`, `scorecard.json`, `findings.md`), `qa.json` |
| `yt improve` | the improvement loop of `docs/production.md` §8 as code: routes each QA finding to the stage that owns it, writes that stage a revision brief the harness puts in front of every decision it makes, re-runs the earliest routed stage and everything downstream, scores a new pass, keeps it or restores the snapshot | `feedback/<stage>.md` (+ `.json`), `improve/pass-NN/`, `improve.json`, `improve.md` |
| `yt doctor` | the toolchain and every video: what is missing, stale or failing, and the command that fixes it. No model. | `--json FILE` |
| `yt assess` | can a described video be produced at all; proves it with a frame | `work/.assess/<slug>/` |
| `yt probe` | the realism probe suite: fixed neutral briefs, one per class of material, light or motion, each composed by an agent coding with the SDK through the kit, built and framed by the harness, scored by a different model that names each defect's cause (authoring, engine, asset), revised in the same session while under `--bar` | `work/.probes/<run>/` (`summary.json`, `summary.md`, one workspace per probe), `work/.probes/latest.json` |
| `yt ls` | which videos exist and how far each got | |
| `yt assets` | search open catalogues for one shot's footage | |
| `yt props` | fetch CC0 3D props (glTF models, HDRIs, texture maps) from Poly Haven, by phrase, or with none chosen and vetted by model decisions over the script and styles ("nothing fits" keeps nothing); the vfx brief lists them | `props/<kind>/<id>/`, `props.json` |

Keys load from `--env-file`, then `$YT_ENV_FILE`, `./_tmp/do_not_delete.env`,
`./.env`, `~/.config/pals-notes/.env`; `yt sound --fetch` wants
`FREESOUND_CLIENT_ID` and `FREESOUND_CLIENT_SECRET` (the secret is the
token), and without them nothing is fetched. The channel's standards are
`work/house.md`; `work/<slug>/house.md` overrides sections by name. Every
stage that asks the model takes `--model`, `--effort` and `--fallback-model`
(default sonnet); `yt doctor`, `yt ls` and `yt assets` call no model.

## Rendering

The engine is not bundled and never runs here. `RS_RENDER_HOSTS` in `.env`
lists the ssh targets; `yt render` probes each once and routes by memory,
not GPU, as `docs/compute.md` measured: a full encode and the audio mix go
to the host with the most RAM (192.168.199.7, 125 GB, RTX 3050), stills,
frame ranges and validation to the one with the least (192.168.199.13,
15 GB, RTX 3070 Ti, the faster GPU). `--host` pins one, `--local` is for a
machine that has the engine, `--slices N` splits a long encode across both
hosts with the mix rendered once.

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
  encode, clean intermediates, and do not leave masters there. The mirrors
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
5. `yt gate` (on a render host; `gate.json`'s `stopped_by` says which
   requirement stopped it and which stage owns the fix), then `yt review
   --engine rs`, then `yt qa`.
6. READY TO PUBLISH only when `yt qa` reports an overall of 9.0 with no
   category under 8.5, nothing blocking and no placeholder. A blocking
   mechanical finding (flat, clipped, frozen, a jump, an off-camera object
   on the hook, the ending or more than one sampled shot) blocks on its own
   and caps `visual_quality` and `animation_vfx` at 6 until it is fixed;
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
   pass older than the video it judged. Only with none of them does doctor
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
  decision (`--fresh`; `yt vfx --fresh` opens a new `vfx/vNN/`), then every
  stage downstream up to `yt render` (`--quality`, default final), then `yt
  gate` and `yt qa`. Research and script run only when routed to them, and a
  stage the film never ran (no narration, no voice) is left out. What those
  stages write is snapshotted first under `improve/pass-NN/snapshot/`.
- **Keep or revert.** Kept when the overall beats the best pass so far, no
  category drops more than 0.5 and no new blocking mechanical finding
  appears; otherwise the snapshot is restored (for vfx that means
  `vfx.json`'s active version; the new `vfx/vNN/` stays on disk).
- **Stops** at READY TO PUBLISH (or `--target` with nothing blocking), at
  `--max-passes` (3), when the next pass's estimate (the last pass's cost,
  or $10 for a vfx pass) exceeds what is left of `--budget-usd` (15), when a
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
  `build`, `check`, `frame`) and `kit/`. That directory holds sr-core's own
  `SDK-GUIDE.md`; an `ENGINE.md` with the engine's capability manifest,
  every attribute the schema documents as renderer-dependent (the path
  tracer drops clearcoat, sheen, anisotropy, iridescence and dispersion) and
  the last probe run's scores; and `examples/`, programs that built,
  validated and rendered on the engine. Their values are placeholders.
  `AUTHOR_SYSTEM`, `AUTHOR_TOOLS` and `BRIEF` are what a stage hands such an
  author.
- **The build.** `python3 kit.py build <program.py>` runs the program twice
  in fresh interpreters. A document whose bytes differ between the runs is
  refused: seed every random choice. Every document it saved then goes
  through the SDK's checks, the harness's rules (`rule:shadow`: a 3D world
  in which no light casts a shadow) and the engine's validation, and gets
  `<doc>.provenance.json` (program and document hashes, schema release).
  `sdkkit.provenance_of(doc)` lapses the moment the document is edited by
  hand.
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
    the reviewer only sees this round's.
  - Revisions resume the author's own session.
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
| `light/@castShadow` defaults to false | every object floats: no contact, no grounding, whatever its y | set it on the light that should ground the scene; the kit's build warns (`rule:shadow`) |
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
| Hero inside the set (a penetration) | the hero's path passes through a set solid, or it comes to rest inside one; a still cannot show a merge, the reviewers mention it and never block on it, and the delivery gate has no notion of a solid | the geometry check does: `yt vfx`'s gate, `yt qa`'s mechanical `penetration` and `yt gate`'s blocking item evaluate the world every 0.25 s (`scene-render eval`, no GPU, one job) and fail a `hero-` box inside a set solid by more than a tenth of its smallest dimension, naming time, object and depth; key its path to clear the set, key the rest to touch and declare it with `<meta name="hero-rests" value="<hero> <surface> <seconds>"/>` so floating is caught too |
| Hero reads merged into the set | the hero is above a surface in 3D, but on screen it lies over that surface's top with no shadow of it there and too little clearance for parallax, so the viewer sees it sunk into the surface; no box intersects | the same check projects the evaluated camera (calibrated against a render to under 1 px): over a farther surface's visible top by more than 30% of its screen area, under 25% of the camera distance above it, no shadow of it on the surface, for 0.5 s or more, is `reads merged`, an error in `yt vfx`, a blocking mechanical finding in `yt qa`, a blocking row in `yt gate`; cast its shadow onto the surface, lift the line of sight toward the surface's plane, or frame them apart |
| A pilot's sync over the film's | the old remote dir was keyed by slug only; a pilot overwrote a finished film | fixed: every job owns `/tmp/yt-render/<slug>/<job>-<tag>/` hardlinked from the mirror, and only the encode writes `render.json` |
| A job writing into a tree the mirror lacks | the mirror excludes `qa/` and other output trees, so `-o qa/.../x.png` had no parent directory on the host | fixed: the job script creates the parent of every `-o`/`--report` path before the engine runs |
| Sampling a shot's start | lands in the inter-take gap and shows an empty stage | sample midpoints, as `yt qa` does |
| One caption per shot | 145 characters do not fit; clipped mid-sentence | split on word timings, two lines at most |
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
  `yt build` places it on a bus per role, ducked under the narration; a cue
  nothing matched stays `unfilled`, shows under `audio_missing` in
  `build.json`, and a design of which nothing could be placed makes the
  build unpublishable. `yt sound --synth` realises every cue still without
  a file as original procedural sound: an agent writes and runs
  `sound/synth/make_audio.py` (NumPy and SciPy, no samples, a fixed seed),
  every WAV must pass `check.py` -- the pipeline's own check: 48 kHz stereo
  16-bit, the cue's length, neither silent nor clipped -- before its cue
  gets a `file`, and the cue records the script's sha256 so the sound can
  be regenerated. `--fetch --synth` fetches first and synthesises the rest;
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
and no fail in its area; `keyframe` also needs the approved look; `deliver`
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
