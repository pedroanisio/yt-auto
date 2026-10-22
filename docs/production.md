# The production brief

What the production and editing stage must deliver, how it is judged, and the
loop it runs until the piece is publishable or a specific technical limit is
proven to stop it.

`work/house.md` is the editorial standard: what the film must make a viewer
understand. This is the other half — what the picture, the sound and the file
must be for the result to be a piece that could credibly appear on a
professional educational or documentary channel, rather than an animated
presentation.

Written in English to match the rest of `docs/` and the stage prompts; the
film itself is in whatever language `script.json` records.

---

## 0. What this is for

This codebase uses generative AI to create and compose **top-tier YouTube
videos** on scene-render and the sr-core SDK: visually intense explainers with
voice-over and motion graphics, in **2D, 2.5D and 3D**, multi-aesthetic according to the editorial
decisions and paths. The aesthetic is an editorial variable chosen upstream
(`styles.json`'s treatment), never a constant of the tooling: the schema has
`object3D`, cameras, lights, particles, physics and parallax, and an authoring
layer that assumes "2D" has narrowed the mission. The product is the finished,
rendered, gated film — not a document that renders.

## 1. The rule that governs everything else

**The rendered video is the evidence.** Not the manifest, not the exit code,
not the scene source, not the fact that the engine accepted the document. A
technically correct render earns no score at all on its own.

Every judgement in this document is made by looking at frames and listening to
ranges. Anything claimed without having looked is not a finding, it is a
guess, and guesses are how a placeholder gets shipped as a proof of concept.

---

## 2. The publication bar

Scored 0–10 in ten categories, by professional standards, against the whole
runtime:

| # | Category | What it measures |
|---|---|---|
| 1 | Visual quality | consistency, resolution, finish across the runtime |
| 2 | Animation / VFX | motion design, simulation, compositing, integration |
| 3 | Cinematography | framing, movement, depth, composition, continuity |
| 4 | Editing / pacing | shot length, rhythm, escalation, scene-to-scene flow |
| 5 | Graphics / overlays | typography, labels, safe areas, contrast, density |
| 6 | Voiceover | intelligibility, naturalness, emphasis, sync |
| 7 | Sound design | effects, ambience, impacts, spatial balance, dynamics |
| 8 | Music / score | progression, emotional fit, transitions, repetition |
| 9 | Narrative clarity | hook, retention, payoff, visual support for each claim |
| 10 | Overall production | does it read as a documentary, or as a slideshow |

**READY TO PUBLISH requires all of:**

- overall **≥ 9.0**
- **no category below 8.5**
- no material rendering or asset defect
- no single weak scene that lowers the perceived quality of the whole
- audio intelligible and professionally balanced
- visual and narrative continuity across the complete runtime
- no sequence that reads as a slideshow because of static composition,
  excessive text, weak motion or simplistic transitions
- nothing left that a normal viewer would notice

A 9 is not "nothing is broken". A 9 is work that would not look out of place
on a channel people subscribe to. Score 8 for competent and unremarkable, 6
for functional, 3 for a document that renders.

---

## 3. Be especially critical of

These are the failures that read as amateur even when nothing is technically
wrong, and each is a reason to mark a category down:

generic or obviously synthetic imagery · weak maps and diagrams · fake-looking
3D · flat lighting · a camera that never moves, or that zooms and pans without
reason · repetitive parallax · excessive glow and bloom · low-resolution
assets · inconsistent visual styles between scenes · poor masking and
compositing · typography that looks templated · overlays that float
disconnected from the scene · long stretches with no visual evolution ·
narration describing something the picture never demonstrates · music loops
that become audible as loops · transitions with no narrative purpose · too
much information arriving at once.

---

## 4. The files, and what each one contributes

All paths are relative to the project directory, `work/<slug>/`.

| File | What production takes from it |
|---|---|
| `script.json` | The spine. Per shot: `visual`, `on_screen_text` (the words a viewer reads, one string each), `on_screen` (the animator's direction for them, not measured), `narration`, `style`, `source`, `seconds`, `label`, `section`. |
| `styles.json` | The design system: the primary treatment's `palette` and its semantic roles, `typography`, `motion`, planned `shots`, `combination`, and what was ruled out. |
| `vfx.json` | The build plan. Per effect: `type`, `technique` (the drawing steps), `tools`, `seconds`, `difficulty`, `template` for shared rigs. Under `authoring`: the active `version` (`vfx/vNN/`), every version kept, the review rounds and scores per section, and the shot -> {file, symbol} map the build reads -- one world symbol per section, its shots as phases. Under `reach`: which constructs each symbol uses against everything the engine offers (`usage`, `report` by family, the manifest's caveats) and the model's audit -- per effect `strongest` or `upgrade` with the stronger construct named, and a 0-10 reach score. QA reads it, so "weak motion" can name the construct left unused. |
| `props.json` | The CC0 props `yt props` fetched from Poly Haven under `props/<kind>/<id>/`: per item `kind`, `file`, `files`, `licence`, `credit`, `source_url`. The vfx brief lists them so the author places real models, HDRIs and textures. |
| `voice.json` | The clock: per take `file`, `at`, `seconds`, word-level `words`, and the per-section drift. |
| `dossier.json` | The truth: `claims` with `source_url` and confidence, `assets` with `licence`, `credit_line`, `commercial_ok`, `misconceptions`, `gaps`. |
| `sound.json` | The audio design: cues with `kind` (including `silence`), `at`, `seconds`, `level`, `fade_in`, `fade_out`, `motif`, a `query` for the library, the mix targets (numbers beside the prose: `narration_lufs`, `bed_below_narration_db`, `effects_peak_dbtp`) and `normalize` (`mode`, `reason`), the master's normalise mode the build reads. After `yt sound --fetch`, each filled cue also carries `file` (under `sound/`), `licence`, `credit`, `source_url` and `duration`, which is what the build places; `unfilled[]` lists what nothing matched. |
| `work/house.md` | The editorial standard, which production serves rather than reinterprets. |
| `docs/compute.md` | Where heavy work runs, and the traps of each machine. |

**Read them; do not transcribe them.** A production stage that copies a hex, a
super or a timing into its own source is broken by construction: the upstream
document stops being the source of truth the moment it is duplicated. If a
document lacks a field production needs — `styles.json`'s palette is prose
rather than `{hex, use}` — fix that stage, do not hardcode downstream.

---

## 5. What production emits

One scene-render document, `scene.xml`, built with the `sr_core.scene` SDK:

- `<project>` at delivery resolution and frame rate, background from the palette
- `<assets>` — text, images, audio; every third-party image carrying
  `license` and `credit` from the dossier
- `<composition>` — the drawing: one `<include>` per run of consecutive
  shots the same authored world draws (its clock continuous across them),
  pointing at the cleaned copy `vfx/vNN/<section>.include.scene.xml`, with
  the file's styles, paints and lights hoisted into the scene; images and
  the editor's placements for the rest
- `<markers>` — `kind="section"` per section, `kind="beat"` per shot;
  everything downstream anchors to markers, never to timecodes
- `<audioMix>` — a track per take on the `narration` bus; an `<audio>`
  asset and an `<audioTrack>` per fetched cue on the bus of its role
  (`music`, `effects`, `ambience`), the transients (sfx, stingers,
  transitions of 2 s or less) first brought down as a group so the loudest
  sits the master's headroom above the loudest bed, then gain staged across
  bus and track within the schema's facets, music and ambience ducked under
  the narration; a `silence` cue as a window on the music and effects buses'
  volume (the voice and the room carry on); and a
  `<master>` with `normalize`, `loudness`, `truePeak`, `limiter`
- `<colorManagement>` — the film's one colour pipeline, as the colourist
  decided it: `acescg` working space, a tone mapping operator, an exposure
  and, when the treatment calls for a grade, one ASC CDL look (slope, offset,
  power, saturation, mix) listed in the pipeline's `looks`. A section's own
  pipeline is never merged. Omitted when no look was chosen or the render
  check dropped it
- `<captions>` — cues split on real word boundaries, at most two lines; a
  sidecar track (`mode="sidecar"`) unless the build is run with
  `--burn-captions`
- `<output>` — explicit `bitrate`, `maxBitrate`, `bufferSize`, `preset`; never `crf`

Plus `build.json`, whose keys are: `idea`; `scene` (size, frame rate,
duration, background, codec, language, the output and loudness settings);
`look` (the colour look applied, or null), `look_check` (the verdict on the
rendered still: `applied`, `revised`, `dropped`, `not checked` or `none
chosen`, and the `host` probed, which `proof_check` reuses unless the film
is path traced or has a medium and goes where an encode would) and
`look_declined` (the look the model chose when it was not
applied); `timeline`; `layers` (what was drawn, what remains placeholder);
`placeholders`; `publishable`; `timing`; `editorial`; `design`; `hoisted`,
`includes` and `split_worlds` (a world's run of shots left in more than one
include, which restarts it at each cut: an `error:` finding, and the build
is not publishable); `proof_check` (each world's proof frame against the
built document at the same film time, rendered on a host before the encode:
`ok`, `mismatch` (an `error:` finding per world, not publishable),
`incomplete` (a frame that did not come back is `unread`, a `warning:`
naming the world), `not checked` with no host, or `no proof frames`); `fonts`; the audio placed (`audio_tracks`,
`narration_tracks`, `buses`, `silence_windows`), the cues it could not place
(`audio_missing`), the transient staging (`audio_staging`: the bed level,
the headroom derived from the loudness and true-peak targets, the shift and
the cues shifted); `cues` and `markers`; the reading checks `legibility` and
`stale_takes`; the `findings`; the `shots` as placed; and the `engine` report
of the decisions taken. `publishable` is false while a placeholder, a
blocking editorial note, a picture that ends before its narration or a sound
design of which nothing reached the mix remains.

### The frame, in bands

At 1920×1080, scaling proportionally elsewhere:

```
  0 –  230   supers and credits
230 –  820   the stage: graphics, diagrams, footage
820 – 1080   caption safe area; nothing else may enter it
```

The captions are a sidecar track the viewer turns on, unless the build is run
with `--burn-captions`; the band stays reserved either way, because burned
captions collided with every authored label that ignored it.

---

## 6. Craft criteria

**Colour.** Every fill and stroke resolves to a palette role — background, the
tracked object, the viewer's choice, a positive outcome, a negative outcome,
muted support. A literal hex in the composer is a defect even when the colour
is right.

**Type.** Sizes form a scale. Figures use tabular numerals so they do not
jitter when animated. The emphasis weight is used only for emphasis.

**Motion.** One idea per shot. Elements transform in place rather than cutting;
the camera moves only when the shot is about movement, and then with purpose.
Easing on every transition. A beat that starts before its narration is wrong.

**Information.** Every string in a shot's `on_screen_text` is rendered, up long enough to
read at 17 characters per second. A figure appears when the voice says it, not before.

**Audio.** Narration at measured positions; music and effects from
`sound.json`, gain staged across bus and track; silence is a cue with a
window, not an absence.

**Provenance.** Third-party imagery carries its licence and credit, or is
replaced by an original drawing until the licence exists.

---

## 7. Evidence sampling

Never judge from the opening. For every review pass, inspect across the
**whole runtime**:

- the opening hook
- **every section boundary** (the `kind="section"` markers make this mechanical)
- every transition — and the frames immediately **before, during and after**
  it, because that is where defects hide
- the most visually complex shots
- every shot containing a map, diagram, data visualisation, or dense overlay
- every shot with substantial camera or element movement
- every generated, archival or 3D asset in context
- the major narrative beats
- approximately **25%, 50% and 75%** of runtime
- the final sequence, the call to action, the ending

Stills are sampled at the **midpoint of a shot**, never at its boundary: the
gaps between takes are real, and a frame at a shot's start lands in one and
shows an empty stage.

A still cannot evidence motion. Render each sample as one contiguous window
of frames around the midpoint — half a second, at most 16 frames, kept
inside the shot — take the still from its middle frame, and measure the
difference between consecutive frames and across the whole window: no
difference either way is a frozen shot (a slow drift is still motion), one
step far above the others is a jump inside the shot, and either is a
finding before anyone looks. Tile the stills with their timestamps into a
contact sheet so the whole runtime is judged at once.

For audio, inspect the time range itself. Do not infer audio quality from the
script, the cue sheet, or the fact that a track exists.

---

## 8. The improvement loop

The objective is not to review the piece once. It is to improve it through
repeated evidence-based cycles until it passes or a specific technical limit
is proven.

For **every material issue**:

1. **Locate it** — exact timestamp, frame range or scene.
2. **Describe** what is visible or audible.
3. **Explain** why it lowers production quality, in terms of section 2 or 3.
4. **Trace the root cause** to the file that owns it: a scene definition, an
   animation, an asset, a render setting, an audio element, or an upstream
   document.
5. **Implement the correction** in the composer or the source document —
   never by hand-editing the output.
6. **Re-render only what changed** — the affected time range or a
   representative set of validation frames.
7. **Inspect the new result** and **compare it to the previous version**.
8. **Keep the change only if it measurably improves** the result without
   introducing a regression. Revert it otherwise.

Then repeat: **inspect → diagnose → fix → render → inspect → compare.**

Work one section or one template at a time, not the whole film. Encode each
section and watch it with sound before moving on: pacing and collision
problems that stills hide appear only in motion.

Do not stop because one pass is complete. Stop only when section 2 is
satisfied, or when a concrete limitation is proven and named.

### When to stop and ask

- The same defect survives three passes: the approach is wrong, not the
  parameters.
- A shot needs footage or a licence that does not exist.
- A section's animation estimate exceeds its budget.
- An upstream document is wrong — a claim that does not hold, a super that
  contradicts the narration. Production fixes pictures, not facts: send it
  back to the stage that owns it.

---

## 9. Regression check

After all targeted fixes, review the assembled video again — beginning,
middle, end, every major transition, and **every scene touched during the
process**. Verify that local corrections did not introduce:

timing regressions · audio discontinuities · inconsistent visual treatment
between a fixed scene and its neighbours · broken transitions · missing assets
· synchronisation errors · new rendering artifacts.

A fix that improves its own shot and breaks the one beside it is a net loss.

---

## 10. The harness

### Composing and checking

```bash
yt build --title <slug>            # composes scene.xml and build.json
```

Three levels, in order of cost: the SDK's own `check()` (required attributes,
ids, references, version gates); the schema itself (`schema=True`, needs
lxml); and the engine (`yt render --validate`).

Plus the guards this project added, because the schema permits what delivery
does not: duplicate animation on one property, takes whose recorded text no
longer matches the screenplay, supers above 17 characters per second, figures
on screen the narration never says, and loudness values that cannot be
delivered. An error-level finding blocks the write; `yt doctor` aggregates
everything and names the command that fixes each.

### Rendering

Heavy work does not run on the workstation — see `docs/compute.md`. The rule
is memory, not GPU. `yt render` keeps one mirror of the project on the host,
`/tmp/yt-render/<slug>/mirror/`, refreshed incrementally before each job,
and gives every job a directory of its own, `/tmp/yt-render/<slug>/<job>-<tag>/`,
hardlinked from the mirror: the engine runs there, the results come back and
the job directory is removed (`--keep-remote` leaves it; `--prune-remote`
deletes the mirror). Only the film's encode writes `render.json`; a
still, a validation, an audio pass or a pilot writes `render-<job>.json`,
so the report the gate reads is never overwritten by a later job. A command
run on a host by hand still takes the shared lock and declares the
reservation:

```bash
ssh root@HOST flock -n /run/lock/render-host.lock -c '<command>'
```

Targeted re-rendering is what makes the loop affordable:

```bash
scene-render render  SCENE --time T        -o frame.png     # one still
scene-render render  SCENE --frames A..B   -o f_%04d.png    # a range
scene-render encode  SCENE --start T --end U                # one section
```

### Approving

```bash
yt pilot  --title <slug> --seconds 30,60,180   # nested cuts, full fidelity
yt render --title <slug> --audio-only          # does the mix fit in memory
yt render --title <slug>                       # the encode
yt gate   --title <slug>                       # loudness, scopes, captions; on a host; triaged
yt review --title <slug> --engine rs           # the PDF sheet, read by the model
yt qa     --title <slug>                       # this brief, as a pass: qa/pass-NN
```

`yt gate` needs ffprobe and the engine, so it runs on a render host
(`--host`, or the one an encode would go to; `--local` runs it here) in a
job directory of its own that is removed afterwards. `production_gate.py`
fails closed on the first requirement that does not hold, and `gate.json`'s
`stopped_by` names that requirement, the owning stage and the command that
fixes it.

The gate applies to a film with no narration too, unchanged. For a screenplay
with none, `yt voice` records nothing: the model writes a descriptive captions
track (bracketed sound descriptions from the cue sheet and the shots, never
dialogue) to `narration.vtt` and `.srt`, every cue is made legal mechanically
to the gate's limits (ordered, inside the film, two lines of 42 characters, 17
characters per second; lengthened or dropped, and said so), and `voice.json`
has no takes, `"wordless": true` and `voice_end` set to the last shot's start
(`"voice_end_source": "last shot start"`), so the gate's tail check, QA and
`yt doctor` read one value.

`yt qa` is the harness of sections 2, 7, 8, 9 and 11: it samples the evidence
as section 7 says, renders one frame window per sample on the render host
(one `--frames A..B` job each; the still is the window's middle frame), files
the mechanical findings first (a flat or clipped still, a frozen window, a
jump, the objects `render.json` says the camera never framed — each
with an owner and a fix, blocking on the hook, the ending or when repeated
across shots; and `proof-mismatch`, owned by `build` and always blocking: the
film at a world's proof-frame time differs from the author's proof frame by
more than `qa.PROOF_DIFF` in the stage band, both frames rendered ungraded
so an applied look is no mismatch, which is what a world split into
includes that restart at t=0 looks like; the other mechanical findings on
that world's shots are re-owned to `build`), has the model read the frames and the contact sheet as images
and file the scorecard, applies the bar of section 2 mechanically (a blocking
mechanical finding caps `visual_quality` and `animation_vfx` at 6; no audio
track in `build.json` fixes `sound_design` and `music_score` at 2.0), and
compares the pass with the one before. Its findings name the owning stage;
the loop is to fix that stage's source, rebuild, and run it again.

### Where the QA lives

```
work/<slug>/qa/
├── pass-01/frames/            the stills inspected, named by timestamp
├── pass-01/frames/burst/      each sample's frame window, the still's stem and the frame index
├── pass-01/samples.json       what was sampled, where, and each window's plan (frames, range, still frame)
├── pass-01/contact-sheet.png  the stills tiled with their timestamps
├── pass-01/scorecard.json     ten categories, overall, verdict, the caps and the mechanical findings
├── pass-01/findings.md        timestamp → problem → root cause → owner → fix → result, with the burst numbers
└── pass-02/…                  the next cycle, for before-and-after comparison
```

Keeping each pass makes "measurably improved" a comparison rather than an
opinion, and makes the regression check mechanical.

---

## 11. The final report

1. **Verdict** — READY TO PUBLISH or NOT READY.
2. **Scorecard** — all ten categories plus overall.
3. **Changes implemented** — for each: timestamp or scene → problem → root
   cause → correction → verified result.
4. **Remaining issues** — only genuine ones, classified **Blocking** or
   **Minor / acceptable**.
5. **Final artifact** — path, runtime, resolution, frame rate, audio
   configuration.
6. **Publication rationale** — why it now meets the bar, or exactly what
   prevents it.

If the verdict is NOT READY, do not soften it because the render works. Name
what blocks publication.

---

## 12. What this harness cannot yet evidence

An honest spec says where it is blind, so nobody mistakes silence for a pass:

- **No ffmpeg on the workstation, by policy.** Audio is measured through
  the engine's own report (integrated LUFS, loudness range, momentary and
  short-term peaks, true peak) and through the delivery gate, which `yt
  gate` runs on a render host that has ffprobe and the engine. Waveform
  inspection and listening still happen on a machine that has the tools, so
  "sound design" and "music" scores rest on the cue sheet, what the build
  placed and the engine's measurements, and that limit is stated in the
  report rather than scored around.
- **The music and effects are library previews.** `yt sound --fetch` fills
  each cue with a CC0 or CC-BY high-quality mp3 preview from Freesound and
  `yt build` places it; a cue nothing matched stays unfilled and is listed
  under `audio_missing`, and a sound design of which nothing could be placed
  is not publishable. A film whose `build.json` has no audio track at all
  scores 2.0 on Sound design and Music / score by rule, and `yt qa` says so
  under `not_evidenced` instead of guessing a neutral mark.
- **Placeholder shots are disqualifying, not deductible.** While any remain,
  the verdict is NOT READY regardless of the other nine categories.

---

## 13. Traps already paid for

| Trap | What happens | What to do |
|---|---|---|
| `crf` with NVENC | ignored; the encoder substitutes a default and the file bears no relation to the request | state `bitrate` and `maxBitrate` |
| The AAC ceiling pass | the decoded AAC overshoots the PCM, the engine pulls the programme down, and the delivered LUFS is not the target | target plus a measured compensation, as two separate quantities |
| `limiter` after `normalize` | strong short peaks eat loudness; raising the target cannot fix it | quieten the effects: `yt build` brings the transients (sfx, stingers, transitions of 2 s or less) down as a group to the master's headroom above the loudest bed, and warns when no bed plays under them |
| `gain` saturates at ±24 dB | per track and per bus; a quiet library cannot be lifted by either alone | stage the gain across both |
| `key/@value` is `xs:string` | a float is refused at construction | write values as text |
| `<segment>` needs schema 1.2 | a 1.1 document is refused | raise the version where the element is used |
| An empty enumeration | means *free string*, not *nothing allowed* | do not filter against it |
| One cue per shot | 145 characters do not fit; the renderer clips mid-sentence | split on word timings, at most two lines |
| Sampling a shot's start | lands in the inter-take gap and renders an empty stage | sample the midpoint |
| The audio mix | is built whole; a range does not make it cheaper, and it costs tracks × duration | measure once, on the machine with the RAM |
