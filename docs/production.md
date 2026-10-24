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

Scored 0–10 in eleven categories, by professional standards, against the whole
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
| 9 | Narrative clarity | hook, stakes, re-hooks, open questions closed, escalation, payoff on the promise, visual support for each claim |
| 10 | Overall production | does it read as a documentary, or as a slideshow |
| 11 | Editorial identity | atmosphere and personality of its own, tied to the subject; look, sound and voice as one; passes the swap test |

Editorial identity (AUTO-146) asks whether the film has a look and a
personality of its own, or a generic explainer style that would fit any
subject. The swap test: could this look, this sound and this voice be
moved onto a film about something else without anyone noticing? The film's
intended identity is written by `yt styles --rich` (atmosphere,
personality, motif, signature, and why it belongs to this subject), swap
tested there by a reviewer of another model family, and carried into the
keyframe and vfx briefs; QA judges whether the cut delivers it. QA's
evidence carries the identity's fields and not the styles reviewer's
verdict or scores, so the judge scores the frames, not an earlier opinion. A pass
scored before the category existed is compared with a later one on the
ten both scored.

Narrative clarity and editing / pacing are read against the narrative
rubric's rows for the hook, the stakes, open loops, reveal setup, tension
and the ending, rendered into the reviewer's role from `ytstudio/narrative.py`
(`qa.STORY_RUBRIC`), with the narration beside the frames in each pass
(`narration.txt`, the voiced takes once there are any) and the
screenplay's story as the writer's declaration. The explanation should need
both the narration and the frames (attention 23). The scorecard's `story`
block restates the premise as the film made it, says what the viewer has at
the end of each section, one non-blank line per section, and predicts a
viewer's satisfaction, which scores nothing and is listed as not evidenced
until the channel-level `work/retention.json` exists (§12); the per-film
`work/<slug>/retention.json` that `yt retention` writes does not count. A finding on the story
belongs to `script`, whose duty names the same story as row 9 (hook,
stakes, re-hooks, open questions, payoff).

Editing / pacing is also measured, not only judged (`ytstudio/rhythm.py`,
direction 12 and 25): `yt qa` writes `pass-NN/rhythm.json` with the
coefficient of variation of the intervals between cuts and builds (by
shot from `build.json`, by change event once the render is scanned), the
longest hold with no visual change while the narration runs, the opening
30 s's change rate against the film's median, and the words a minute of
each section from `voice.json`. The reviewer is shown these numbers as
measurements beside its own judgement, which they do not replace; what
they find is filed as `hold`, `metronomic`, `slow-opening` and `wpm`
mechanical findings (§10). The scan runs on a render host only; with
`--watch` it reads the cut the watch already sent there, so the cut goes up
once, and rhythm.json's scan block records the upload's seconds and bytes.
A hold counts the narration as running across a pause of up to 1.5 s
(`narration_gap_s`, a house choice); a scan that exits non-zero or stops
short of the film's end leaves the hold and the opening not evidenced,
never passed.

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
- no fact-check blocker standing (resolved by a revision, or marked
  `resolved` by hand)

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
| `script.json` | The spine. Per shot: `visual`, `on_screen_text` (the words a viewer reads, one string each), `on_screen` (the animator's direction for them, not measured), `narration`, `asset` (the licensed asset a shot uses, or "headline [Nk]" for a recreated press card: press item Nk of the research block, its headline verbatim and "outlet, date" in `on_screen_text`, checked as `source:headline`; AUTO-149), `style`, `source`, `seconds`, `label`, `section`, `function` (hook, context, setup, reveal, rehook, payoff, handoff), `shows` (what the picture must demonstrate of the line) and, with a dossier, `claim` (the dossier claim id it rests on), `status` (established, hypothesis, speculation, what-if or none) and `ceiling` (the strongest status the claim allows, set by the code; AUTO-132). Per section: the questions it `opens` and `closes`, its `connective`, `rehook`, `escalation` and `anchor_visual`, and optional `devices` (AUTO-135: each a `kind`, analogy, visual_metaphor, prediction or thought_experiment, the `device`, what it `maps`, where it `breaks_at`, its `framing` for a thought experiment and, for a prediction, the section it is `scored_in`). For the film: `promise`, `payoff`, `train`, `stakes`, `viewer_stake`, `misconception`, `audience`, `persona` (optional, AUTO-158: the narrator's `temperament` for this film and `why`), `hook` (the opening's `trigger` and `gap`), `reveal`, `questions`, `ending`, `cited_claims` (each cited id with its standing, confidence, caveat and source kind) and `check.status` (the `verify-status` reading by a reviewer of another family, or `not_evidenced`). |
| `styles.json` | The design system: the primary treatment's `palette` and its semantic roles, `typography`, `motion`, planned `shots`, `combination`, and what was ruled out. |
| `vfx.json` | The build plan. Per effect: `type`, `technique` (the drawing steps), `tools`, `seconds`, `difficulty`, `template` for shared rigs. Under `authoring`: the active `version` (`vfx/vNN/`), every version kept, the review rounds and scores per section, and the shot -> {file, symbol} map the build reads -- one world symbol per section, its shots as phases. Under `reach`: which constructs each symbol uses against everything the engine offers (`usage`, `report` by family, the manifest's caveats) and the model's audit -- per effect `strongest` or `upgrade` with the stronger construct named, and a 0-10 reach score; and `motion` (AUTO-144), the reviewer model's motion audit on rows read from each effect's document: `answer`, `static` (the effects that must show a change and show it static, each with `what_changes` and `fix`) and `reason` (why it could not be read, else empty; `failed` when the reviewer itself failed, so the next `yt vfx` asks again). Per section, `warnings` names an effect whose type keeps a process its picture-check says is frozen still. Per authored section (`authored[]`), its `score`, the `bar` it was reviewed against and the model that wrote it (`author`), both kept when the section is carried, and why its revisions `stopped`; `yt doctor` warns of a section under its bar. Under `continuity`: the side-by-side review of the sections (`one_film`, `score`, `breaks` each with `owner_section` and `severity`, `monotony`) and `reviewer` (`model`, `author`, `authors` -- every section's author, None for one carried from before authors were kept -- and `engine`: who judged it); a `blocking` break whose reviewer is of another family than every author blocks QA's READY. QA reads it, so "weak motion" can name the construct left unused and the static processes. |
| `props.json` | The CC0 props `yt props` fetched from Poly Haven under `props/<kind>/<id>/`: per item `kind`, `file`, `files`, `licence`, `credit`, `source_url`. The vfx brief lists them so the author places real models, HDRIs and textures. |
| `voice.json` | The clock: per take `file`, `at`, `seconds`, word-level `words`, and the per-section drift. Per take also `spoken` (what the engine was sent when the screenplay's delivery marks changed it, "" otherwise), `spoken_chars` and `delivery` (the section note, the shot's performance note, the marks, what the engine `applied` and what it could only record, `recorded_only`). Captions and cps are always from `text`. Top-level `delivery` counts the controls `applied` and `recorded_only`, `by_kind`, and lists the casting rows that named no section (`unmatched`); `casting.delivery` keeps each section row with its clamped `speed`/`settings`. A paid (elevenlabs) run also records `draft: {status: current\|stale\|missing\|accepted, fails}`, the read-aloud draft it was checked against; `voice-draft.json` (`yt voice --draft`) is that free read, never the voice. |
| `dossier.json` | The truth: `claims` with `source_url`, confidence and `standing` (how the source presents it: established, hypothesis, speculation or limitation; AUTO-132), `assets` with `licence`, `credit_line`, `commercial_ok`, `misconceptions` (each a `belief` with its `source`), `identifiable_case` (`who`, `source`), `human_stories` (AUTO-149: up to three people the subject affected, each `who`, `what_happened`, `consequence`, `source` and the `claim` it rests on, kept only when the claim is in the dossier and the source is that claim's or a page the research opened; a dropped one is a `warnings` line that `yt doctor` shows as a note, apart from the provenance warnings), `parallels` (AUTO-151: at most two historical parallels or precedents, each `event`, `year`, `how_it_maps`, `where_it_breaks` and `source_url`, kept only when the source is a page the research opened; a dropped one is a `historical parallel dropped:` `warnings` line that `yt doctor` shows as a research warn, apart from the provenance warnings; an existing dossier gets them only after `yt research --fresh`) and `parallels_why` (why the list is empty; an explainer or paper film with neither is a doctor note), `gaps`; a paper film's `paper` (its `citation` and six items, `problem` to `limitations`, each `text` with the `claims` it rests on). |
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
  `license` and `credit` from the dossier; and, when anything placed owes a
  credit, the end-credits cards `credits-card-N`: every credit line
  verbatim, centred in the ink at the label size, one card up for
  max(4 s, its characters at 17 per second), split so none runs past 12 s,
  starting at the editor's tail and fading in and out over 0.5 s; the film,
  `scene.duration` and `timeline.runtime` end at the last card's end
- `<composition>` — the drawing: one `<include>` per run of consecutive
  shots the same authored world draws (its clock continuous across them),
  pointing at the cleaned copy `vfx/vNN/<section>.include.scene.xml`, with
  the file's styles, paints and lights hoisted into the scene; images and
  the editor's placements for the rest; and a `<transition>` (`join-N`) at
  each section, world or picture join the editor chose one for and a
  reviewer of another family read (a cut writes nothing; never across a
  gap, inside one world's run, or beside a placeholder card)
- `<markers>` — `kind="section"` per section, `kind="beat"` per shot;
  everything downstream anchors to markers, never to timecodes. A credits
  card has a `kind="comment"` marker labelled `credits` with no duration,
  never a section or a beat, so no shot, section or chapter reading counts it
- `<audioMix>` — a track per take on the `narration` bus; an `<audio>`
  asset and an `<audioTrack>` per fetched cue on the bus of its role
  (`music`, `effects`, `ambience`), the transients (sfx, stingers,
  transitions of 2 s or less) first brought down as a group so the loudest
  sits the master's headroom above the loudest bed, then gain staged across
  bus and track within the schema's facets, music and ambience ducked under
  the narration; a `silence` cue as a window on the music and effects buses'
  volume (the voice and the room carry on); a music or ambience bed that
  plays to the tail runs on under the credits (its loop count raised, or
  its clip lengthened when the file lasts), else the credits are a silence
  window recorded in build.json; and a
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
`stale_takes`; the `findings`; the `shots` as placed; `joins` (every join in
time order, gaps and placeholder joins included: its kind `section`,
`world`, `shot` or `gap`, the nodes on either side, the type, duration,
alignment and the narrative `why`, and its review: `accepted`, `revised`,
`unresolved`, `fallback`, `not-asked` or `off` under `--hard-cuts`) and
`joins_review` (the reviewer model, whether it accepted and the problems it
named); `credits` (one row per credit line the film prints: `credit`,
`licence`, `src`, `id`, the licence's `tier` with share-alike allowed, and
`required` at the attribution and share-alike tiers; public domain first,
then by first appearance; a props.json item an include names is one too) and
`credit_errors` (each placed third-party file with no credit line under an
attribution, share-alike, unknown or vetoed licence, and each download with
no licence or credit: `error:` findings, and the build is not publishable);
with a card, `credits_card` (`at`, `seconds`, `cards`) and `sound` (the beds
`extended` under it, or the `silence` window it is); and the `engine` report
of the decisions taken, both models billed. `publishable` is false while a
placeholder, a blocking editorial note, a picture that ends before its
narration, a sound design of which nothing reached the mix, or a credit
error remains.

And, beside the chain, `yt package` writes `package/`, the upload set an
operator puts into YouTube Studio by hand (nothing is uploaded):
`package.json` (title, description, chapters, tags, thumbnail, credits,
sources, the `scene_sha256` and `video_sha256` it judged, its findings and a
`verdict` of `ready` or `not ready`), `description.txt`, `thumbnail.png`
(1280x720, a frame code offered), `captions.vtt` (a copy of narration.vtt)
and `UPLOAD.md`, the checklist in Studio's order. The chapters, credits,
sources and URLs are assembled from scene.xml, build.json and the dossier,
never written by a model; `yt doctor` reads delivery as not ready until a
current package says `ready`.

### The frame, in bands

At 1920×1080, scaling proportionally elsewhere:

```
  0 –  230   supers and credits
230 –  820   the stage: graphics, diagrams, footage
820 – 1080   caption safe area; nothing else may enter it
```

The bands hold for the film. The end-credits cards come after it, with no
caption on screen, and take the full frame, centred.

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
replaced by an original drawing until the licence exists. A credit is
printed where the work is seen: every placed file under an attribution or
share-alike licence is named, verbatim, on the end-credits cards, and a
placed file whose credit is empty under such a licence, or under one nobody
can read, is an `error:` the build will not publish past. The fix is at the
source (the dossier asset's `credit_line`, the cue's `credit`), never on the
card.

**Story.** House craft defaults, from `docs/narrative/`, each a prior until
the retention loop replaces it; the number after each is its rule there.
The film's train fits in one sentence: who or what pursues what, against
what, and which question the film answers (judging 13). Where the film can,
the viewer is told what is at risk, and by when, before the mechanism is
explained, unless a twist is the point (attention 8). Each section states
the one question it answers in its opening sentences and closes it in that
section or in a later section it names (structure 19). The last section
pays the title's promise in resolved form (structure 7). After the payoff,
at most a one-line pointer to the next film, then the cut; no recap
(structure 27, which that file's Contested section lists as a default, not
a law).

**A paper film.** When the dossier's scope is `paper` (`yt research
--paper`), the plan is told, from `script.PAPER_BRIEF`: "The problem, the
significance and the implications land in sections before mechanism_section,
so the viewer knows why the paper matters before how it works; the
contribution may land at mechanism_section; the consequences and the
limitations are said on air (section 1 or later), never left out. Keep what
the paper itself shows apart from what others say it means." Each of the
digest's six items rests on research claims quoted exactly, and the plan's
pre-checks hold it to that; a misplaced item is a `warning: paper framing:`
finding in script.json.

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
- the major narrative beats: the plan's `sections[].beats` in `script.json`,
  with the beat that states its `promise` and the one that pays its `payoff`
- approximately **25%, 50% and 75%** of runtime
- the final sequence, the call to action, the ending

Stills are sampled at the **midpoint of a shot**, never at its boundary: the
gaps between takes are real, and a frame at a shot's start lands in one and
shows an empty stage. **Every shot** is sampled once (up to 60 stills
unless more are asked for: one inspection reads them all); the list above
is the priority order, which decides what is kept when fewer stills are
asked for than there are shots, and the shots left out are named to the
reviewer and as not evidenced.
More stills than shots are spread evenly over the runtime, each inside a
shot and at least half a second from every other, so more stills are more
evidence rather than the same moments again.
A midpoint misses what happens elsewhere in its shot, so each narrated
line is also sampled where its first word is said (AUTO-104), half a burst
inside its shot, labelled `line: <first words>`, with the line beside the
still and the frame that shows its start: say it, show it is judged at
the moment it is said. A line merges into a still of its own shot only
when that still's burst has the frame where it starts; the payoff, re-hook
and reveal lines come first when fewer stills are asked for, after every
shot's own, then the lines said inside a shot, spread over the runtime;
the lines left without a still are named (`lines_unsampled`); a wordless
film has none.
Each rendered transition is sampled from build.json's `joins` (AUTO-142):
a still at the join, labelled `transition: <type> (<kind>)`, whose burst
spans the window from a frame before it to a frame after, so the frames
before, during and after are evidence. A burst is at most 16 consecutive
frames (`BURST_MAX_FRAMES`), so a window longer than that (over about half
a second at 30 fps) is clipped to the 16 frames around its middle: its
first and last frames, and the frames either side, are not in it. A join's
still is its own: it never
merges into a shot's, and a shot's burst still stays inside its shot. They
come after the line stills and beyond the still count, at most 12, sections
first, then worlds, then pictures. A dip, a push or a whip-pan is a
discontinuity on purpose, so the burst at a join is evidence, not a
mechanical defect: it raises no `frozen` or `jump` finding (nor `flat` at
the middle of a dip to a colour), it is no shot's still, and its motion
is shown to the reviewer as the join's, to judge a held frame or a black
gap by eye.

A still cannot evidence motion. Render each sample as one contiguous window
of frames around the midpoint — half a second, at most 16 frames, kept
inside the shot — take the still from its middle frame, and measure the
difference between consecutive frames and across the whole window: no
difference either way is a frozen shot (a slow drift is still motion), one
step far above the others is a jump inside the shot, and either is a
finding before anyone looks. Tile the stills with their timestamps into a
contact sheet so the whole runtime is judged at once.

Neither a midpoint nor its burst can tell a diagram built in steps from a
finished one under a slow push-in (AUTO-145). The first shot of each
2d-diagram or data-animation effect that vfx.json keeps, when it lasts 3 s
or more and is not extended from another shot, also gets a single frame a
quarter second after its cut (`build: first`) and one a quarter second
before its end (`build: last`): no burst, never the shot's home, beyond
`--frames`, at most 12, the longest shots first, merged into a burst of the
same shot that has the frame. A diagram that is the second phase of its
section's world, running on in the include of the section's first shot, is
judged too: it is still the first shot of its effect. The later shots of
the effect continue its take and open on what it built, so they are not
judged. A build still of its own is written under `frames/build/` and is
measured, never judged: it is left out of the contact sheet, the evidence
rows and the side by side, the reviewer is told not to score it, and a
shot it alone stands for counts as unsampled. Each still's **ink** is the
share of a 320 px luma thumbnail whose edge magnitude exceeds 32; read at
the first still, each line still between and the last, a series that never
rises by 15 % of its last reading is `no-build`, the diagram on screen whole
from the cut and talked over. Growth within the shot is judged, not a ratio
of the first and last, so a busy ground does not hide a build; a noise-like
ground saturates the measure, and a shot drawn on one cannot be told. A shot
left out, whose readings have no ink, or that ends on a blank frame, is
named under `not_evidenced`.

The words on screen are counted from the document QA renders (the scene and
the includes it names), never read off a frame: at each still, the text and
formula nodes inside their include's window and their own, visible under
every opacity key above them (from a key above 0 until the next key at 0,
so a fade out and a second fade in are both read), less the ids the engine
says the camera never sees and build's placeholder card. One node over the
screenplay's wall-of-text bound (6 words), or 12 at once, a formula counted
as 4, is `text-dense`, its node ids named; a node whose id starts `quote-`,
`headline-` or `credit-` is not counted and is held instead to the time it
takes to read (`script.READING_CPS`). samples.json rows carry `ink` and
`text`.

Both are advisory until measured: always minor, never capping a score. The
calibration that sets INK_EDGE, BUILD_STEP, SCREEN_WORDS and FORMULA_WORDS,
from the qa passes of at least two existing films, goes here; until it is
filled in, the numbers above are house numbers.

| Film | Shot | Effect (type) | Built? (by eye) | Ink series (t s: ink) | Concurrent words (max) | Verdict |
|---|---|---|---|---|---|---|
| not yet measured | | | | | | |

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
with an owner and a fix, blocking on the hook, the ending, the shot the
screenplay marks `payoff` (every `rehook` and `payoff` shot is sampled) or when repeated
across shots; and `proof-mismatch`, owned by `build` and always blocking: the
film at a world's proof-frame time differs from the author's proof frame by
more than `qa.PROOF_DIFF` in the stage band, both frames rendered ungraded
so an applied look is no mismatch, which is what a world split into
includes that restart at t=0 looks like; the other mechanical findings on
that world's shots are re-owned to `build`; and, advisory, always minor and
capping nothing until calibrated (§7, AUTO-145), `no-build` and
`text-dense`, both owned by vfx, the first re-owned to `build` with the
others and the second never, since it is counted from the document vfx
wrote), has the model read the frames and the contact sheet as images
and file the scorecard three times on the same evidence (`qa.QA_SAMPLES`,
the categories in a different order each; one reading's noise, about ±0.5 a
category, is the size of the steps read as progress), takes each category's
median and records its spread (max - min), keeps a finding two samples
report and lists one a single sample reports (a blocking claim included) as low-confidence, calls a pass
with fewer than two valid samples `not_evidenced` (never READY), applies the bar of section 2 mechanically (a blocking
mechanical finding caps `visual_quality` and `animation_vfx` at 6, a blocking
rhythm finding caps `editing_pacing` at 6 instead and a blocking hold caps
all three, while a hold, a slow opening or an events-basis metronomic
finding rests on scan thresholds not yet measured on real renders
(`rhythm.RHYTHM["calibrated"]` False) it is filed minor, marked TO VERIFY,
and caps nothing; a render that is stale or cannot be scanned leaves the
hold and the opening not evidenced, never passed; no audio
track in `build.json` fixes `sound_design` and `music_score` at 2.0; a
`voiceover`, `sound_design` or `music_score` with no current listening
review (§12) is not evidenced, unscored, and READY cannot be judged), and
compares the pass with the one before: its scores, and its cut side by
side. Two scores inside the samples' spread cannot say whether a revision
helped, so the reviewer is shown, at every sampled moment, the same
timestamp of the previous kept cut and the new one, labelled A and B in
an order drawn per moment and recorded (position bias is checkable), and
says which is better, or that they are the same, and why. The tally of
better, worse and same per category, and one vote per moment in total, is
filed beside the absolute scores, and the improvement loop reads the
moments' votes before the score delta (step 8 above).
Its findings name the owning stage;
the loop is to fix that stage's source, rebuild, and run it again.

### Where the QA lives

```
work/<slug>/qa/
├── pass-01/frames/            the stills inspected, named by timestamp
├── pass-01/frames/burst/      each sample's frame window, the still's stem and the frame index
├── pass-01/samples.json       what was sampled, where, and each window's plan (frames, range, still frame)
├── pass-01/contact-sheet.png  the stills tiled with their timestamps
├── pass-01/previous/          the previous kept cut's stills at the same timestamps (reused or rendered)
├── pass-01/side-by-side/      each moment's A and B, the order recorded in the scorecard
├── pass-01/comparison.json    with --against: both cuts watched whole in both orders, each answer and the reconciled categories
├── pass-01/watch.json         with --watch: the watcher's record, its findings W1.. and what was not watched
├── pass-01/watch/             with --watch: the pieces a render host cut the video into
├── pass-01/scorecard.json     eleven categories (the scoring samples' medians and spread), overall, verdict, the caps, the mechanical and low-confidence findings, each scoring sample
├── pass-01/findings.md        timestamp → problem → root cause → owner → fix → result, with the burst numbers
└── pass-02/…                  the next cycle, for before-and-after comparison
```

Keeping each pass makes "measurably improved" a comparison rather than an
opinion, side by side at the same moments, and makes the regression check
mechanical. The scoring is blind to the pass before (AUTO-106): a reviewer
shown the last scores files them again, so the deltas are computed from the
two scorecards afterwards, and the old findings, when wanted as a checklist
(`yt qa --recheck`), are asked after the scoring and score nothing.

---

## 11. The final report

1. **Verdict** — READY TO PUBLISH or NOT READY.
2. **Scorecard** — all eleven categories plus overall (a category an
   older pass was never scored on is an empty cell).
3. **Changes implemented** — for each: timestamp or scene → problem → root
   cause → correction → verified result. A retention finding has no
   verified result until the new cut is published and measured: it reads
   "not re-measured".
4. **Remaining issues** — only genuine ones, classified **Blocking** or
   **Minor / acceptable**, and the categories nothing heard, **Not
   evidenced**, with the command that produces the listening review.
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
  inspection and listening still happen on a machine that has the tools.
  `yt qa` scores Voiceover, Sound design and Music / score only from a
  listening review that heard what the film carries: the sound design's
  listen-back (`yt sound --review`, which reads the cue sheet cue by cue,
  not the rendered track) and the director's take review in `yt voice`, each
  recording a digest of what it heard (`heard`: the cue sheet with the mix
  targets and the normalise mode, or the takes). A review of a sheet since
  ridden, re-spotted or re-mastered, of takes since re-recorded, or of a
  file newer than the build is stale, said so, and not used. Without a
  current one the category is not evidenced, has no score, and READY cannot
  be judged for it; the overall is the mean of what was scored, and the
  scorecard and `improve.md` name the missing review and the command that
  produces it. A wordless film has no take review, so READY is out of reach
  for it until a review hears the rendered track. `yt qa --listen` is that
  review, opt-in on the gateway (AUTO-126). A model hears the film's own
  audio track against rows 6-8 above and files findings, never scores, as
  `listening.json` in the pass, and while it is current it is listening
  evidence for all three categories. The measurements (the cue list, the mix settings, the
  narration's numbers) are context, never a score.
- **The music and effects are library previews.** `yt sound --fetch` fills
  each cue with a CC0 or CC-BY high-quality mp3 preview from Freesound and
  `yt build` places it; a cue nothing matched stays unfilled and is listed
  under `audio_missing`, and a sound design of which nothing could be placed
  is not publishable. A film whose `build.json` has no audio track at all
  scores 2.0 on Sound design and Music / score by rule, and `yt qa` says so
  under `not_evidenced` instead of guessing a neutral mark.
- **Placeholder shots are disqualifying, not deductible.** While any remain,
  the verdict is NOT READY regardless of the other nine categories.
- **Narrative clarity is judged from the screenplay and the frames, never
  from audience data.** `yt retention` reads a published film's curve back
  onto its shots and `yt improve` routes those findings to the screenplay
  for the next cut (`work/<slug>/retention.json`), but a QA pass scores a
  cut nobody has watched, so the score rests on the plan, the narration and
  what the frames show, and the craft defaults in §6 stay priors until the
  channel has ten measured films; until then every retention finding is
  tagged `sanity_bound`. The reviewer's predicted
  satisfaction (`story.satisfaction` in the scorecard) is a ranking proxy
  (judging 4): `yt qa` records it and names it under `not_evidenced` until
  the work directory holds the channel's own `work/retention.json`, which a
  film's `work/<slug>/retention.json` from `yt retention` is not.
- **The QA judge is not yet calibrated on the owner's films** (AUTO-156).
  READY is the bar applied to the medians of a model's scores, and nothing
  showed those scores separate a good film from a bad one. `python -m
  ytstudio.qa calibrate tests/fixtures/qa` scores labelled fixture films
  (a good one and seeded faults a mechanical check cannot see: a drifting
  slide, a wall of text, type out of its band, illegible type, a moving
  placeholder, narration that tells what the picture never shows) and
  judges the raw card, before any cap, mechanical blocker or silence rule,
  writing `work/calibration-qa/report.json`. The committed good cases are
  drawn stand-ins, so the set cannot pass until the owner captures one of
  their own films (`python -m ytstudio.qa capture-fixture`) and labels it.
  `qa.json` records `judge_calibration` (`fixtures`, how many owner-labelled
  films back it: none yet) and `yt qa` prints it under the verdict; it gates
  nothing, and READY means what §2 says either way. `fixtures` true means
  this pass's judge (its model, role, categories, bar, schemas, prompt
  template and house rules, `qa.judge_digest`) passed every case of the
  shipped set (`fixture_set` in the report), on what two of its readings
  agree on, and that the pass neither listened nor watched, since the
  fixtures run the unheard judge only. It does not mean that this film's scores are right, that the judge
  agrees with the owner on real films (`labelled` counts those, and is 0),
  or anything about the audio categories, which the fixtures carry no
  listening review for. The re-run rule: any card that edits qa.ROLE, the
  categories, the scorecard schema or `review_prompt` changes the digest,
  so `fixtures` is false until calibrate is run again; AUTO-141, AUTO-145,
  AUTO-146 and AUTO-148 do this, and each adds or adjusts a fixture case
  for the category it changes.

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
