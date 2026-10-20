# Where the time goes in an ultra-quality run

A measured log of one full `yt` run held to the publication bar
(`docs/production.md` §2), kept so the pipeline can be made faster without
lowering that bar. Each row is a measurement: wall time from the stage's own
usage line, cost from `claude_engine`'s `Usage`, and the cause that cost the
time, with the fix that would remove it. A fix marked **done** is in the code;
**proposed** is not yet.

The run: a 60-second 3D science explainer in English, ElevenLabs voice,
every stage on opus, started 2026-10-19 20:47 (-03).

## Stage log

| Stage | Wall time | Cost | What took the time |
|---|---|---|---|
| research (depth 4, opus) | 76 min of compute, plus 8 h 29 min with the host asleep | $15.98 | 4 web-research sessions (18 min, 3 at a time), then claims, sources, shots, assets, gaps and outline in series; 382k output tokens for 193 claims and 126 sources behind 140 spoken words |
| styles, first attempt | 5 min lost | $1.00 | a cross-session message reached the headless call after its structured answer; its prose reply was parsed as the result |
| styles (rich, deliberate critique) | 8.6 min | $1.10 | |
| research, asset search again | 10.4 min | $2.19 | the first search had looked for stills only; one vetting call over 99 candidates and a new outline |
| script v1 (polish, verify) | 27.9 min | $5.10 | its fact-check flagged 5 important lines and on-screen text at 25-35 characters a second; the stage only reports findings, so fixing them is a full re-run |
| script v2 | 30.1 min | $4.36 | re-run with the findings as `--notes`; its fact-check raised a false blocker (the claim it wanted had been cut before it saw the dossier) |
| script v3 | 30.5 min | $5.84 | re-run with the remaining findings and the full claim set; verdict: the narration holds up; what is left is for the picture |
| props (derived phrases) | ~5 min | $0 | 46 Poly Haven items, 422 MB, none usable at molecular scale |
| props (`studio`, HDRIs only) | <1 min | $0 | 3 studio HDRIs to light and reflect; the 46 others deleted so the render mirror does not carry them |
| voice (ElevenLabs, 11 takes, 722 characters) | ~2 min | small | ran beside the keyframe; the director asked for 2 retakes for pace |
| voice `--redo 3.2,3.3` | ~1 min | small | every take sent again; the two came back at the same pace and the director asked again |
| retime | ~1 min | small | applied every length but one; 3.2 "rerecord", which no retake can satisfy |
| keyframe round 0 (author + still + review) | ~27 min | review $1.52 | 10 min of capability research before the first program, a first frame blown out, a second at 4/10 (helix seen down its axis, colliding letters); the review alone took 9 min and 13 tool calls |
| keyframe round 1 | ~31 min | review $1.35 | 18 min of revision with 4 stills, then a 10-min review: 4.5/10 |
| keyframe round 2 | ~28 min | review $2.13 | 14-min review that re-read both earlier reviews and grepped the schema again: 6/10 |
| keyframe round 3 | ~33 min | review $1.78 | 6.5/10, under the bar; whole first keyframe run 2 h 01 min, $7.33 in decisions plus an author spend `keyframe.json` does not record; rejected by the operator with notes (overexposure, molecules built from primitives where PDB coordinates were in the project) |
| keyframe, second run, round 0 | 36 min | author ≤ $4 | built the polymerase from PDB coordinates (a real step up), but its first still had 1,690 blobs and ran past 10 min; the author session hit the 1,800 s call timeout while waiting on it: 1 build error, no review, round lost |
| keyframe, second run, review 1 → approved | 7 min | review $1.43 | 5.5/10, harsher than the first run's 6.5 on a stronger look (real molecular surface); stopped before revision 2 and approved by the operator to save 30-60 min: the remaining findings are per-shot craft the vfx authors and reviewer handle |
| keyframe total | 3 h 21 min | ≈ $16 | two runs; of that, ~65 min were author sessions killed by the call timeout and ~45 min a still host refusing SSH |
| vfx planning (style, effects) | 14 min | | 4 sections planned with the screenplay's fact-check flags as constraints |
| vfx authoring, 4 sections at once (`--jobs 4`) | 2 h 33 min (13:10 → 15:43) | **$88.43** for the whole stage (29 opus calls, 2.06 M output and 71 M cache-read tokens) | final scores 6 / none / 4 / 5 against a bar of 7; three sections lost round 0 to the brief/gate duration bug, two authors crashed (exit 1), one final round broke its document; reviews cost $1.35-2.65 each and two revisions moved scores by 0 to +1; "up to $3 each" bounded no section's real spend |
| build | ~3 min | small | crashed first on `Media.about` (fixed); then not publishable: section 2 blocked, six supers flagged by the direction-counting legibility check |
| draft render, 2 slices | 18 min (15:48 → 16:06) | $0 | 64.3 s at draft across both hosts, mixed once; the joined encode wrote no `render.json`, which the gate reads |
| vfx `--sections 2,3` | running | | re-author the crashed and the weakest section into v02; 1 and 4 carried forward |
| sound (fetch + synth + review), beside vfx | 42 min | $5.75 | 38 cues on the measured clock, Freesound CC0 fills plus procedural synthesis for the rest; 254k output tokens; its own review says "not ready yet" (10 important flags) and, like the script's fact-check, only reports |
| keyframe, second run, revision 1 | 34 min | author ≤ $4 | lightened the surface (a good still at 12:20), then kept tuning; the session hit the 1,800 s timeout again; the still host (.13) refused SSH for about 45 min (an unrelated process, then a reboot), so stills hung; the stage rendered the program itself and sent it to review |

## What slows an ultra-quality run, and the fix

1. **The host went to sleep (8 h 29 min).** The workstation suspends after
   15 idle minutes on AC power, and a stage waiting on the network is idle to
   it. *Fix, done for this run:* every stage launched under
   `systemd-inhibit --what=sleep:idle`. *Proposed:* `yt` takes that inhibitor
   itself for any stage that calls a model or a render host.
2. **Research is sized for a ten-minute film whatever the runtime.** 193
   claims and 126 sources (76 min, $16) for a 60-second script that speaks
   about a dozen facts. *Proposed:* scale questions, claims and shots to the
   target runtime the idea carries (a minute: 2-3 questions, ~40 claims,
   6-8 shots).
3. **The screenplay's fact-check only reports.** Each round of fixes is a
   full `yt script` re-run (about 30 min and $4.50); two were needed.
   *Proposed:* a verify-then-revise loop inside the stage, like polish: apply
   the smallest fix to each important or blocking line, re-check only those
   shots, stop when nothing important is left. `yt sound --review` has the
   same shape: ten important findings with exact fixes, none applied.
4. **The script saw 60 of 193 claims.** The cut was by confidence alone, so
   claims the outline was built on fell out and the fact-check called their
   lines unsourced. *Done:* `script.research_block` always includes the
   outline's claims.
5. **The asset search looked for stills only, and Wikimedia refused its
   agent.** One extra asset pass (10 min, $2.19). *Done:* footage, sound,
   papers, structures and news; a User-Agent with a contact; paced checks.
6. **The screenplay is written section by section on opus at high effort**
   (7 calls, 120-170k output tokens, ~30 min for 140 words). *Proposed:*
   draft sections in parallel against a shared ledger of the numbers already
   spoken, and keep the highest effort for the fact-check.
7. **The legibility check counts directions as text.** `script.legibility`
   divides the whole `on_screen` field by the shot's length, and that field
   holds the animator's directions around the words actually shown: a shot
   showing 18 characters at 5 a second was flagged at 43. The false flags
   drove one round of `--notes` aimed at text that was not too dense.
   *Proposed:* the screenplay keeps the visible strings in their own field
   (or quotes them), and the check measures only those.
8. **Picture findings have nowhere to go.** Draft 3's remaining important
   flags are about what the picture may show (schematic geometry, which
   structures to use), but the fact-check is written into `script.json`'s
   `check`, which no picture stage reads. *Proposed:* `yt vfx` and
   `yt keyframe` put the screenplay's open picture findings in their briefs.
9. **Props are chosen by keyword, not by judgement.** `props.derive_phrases`
   matches the catalogue's tag vocabulary against every word of the script
   and styles: "strand" fetched oriented strand board, "base" a climbing-wall
   base, "stretch" a stretch fabric, "camera" (from camera directions) camera
   models. Nothing judged whether any fits, and every listed prop must then
   be used or declined by each vfx author, and is uploaded to the render
   hosts. *Proposed:* phrases and the kept results become a harness decision,
   as the research's asset vetting is, with "none fit" a valid answer.
10. **A retake cannot be directed.** The voice director can ask for a slower
    read but not give one: `RETAKE_SCHEMA` carries a label and a reason, and
    `--redo` sends the same text at the cast speed, so a take rejected for
    pace returns at the same pace and `yt voice` → `yt retime` → rerecord
    never settles (`yt run` stops at "sent back twice"). *Proposed:* each
    retake carries a speed (and, for ElevenLabs, settings) within
    `SPEED_RANGE`, and the recorder applies it to that take alone.
11. **`--redo` re-records everything.** The ElevenLabs path passes
    `force=bool(self.redo)` to the kit's `tts_scenes`, which forces every
    scene, so a two-line retake pays for and replaces all eleven takes and
    shifts the prosody of the ones that were accepted. *Proposed:* force only
    the named scenes (the kit takes one `force` for the whole list today).
12. **Authors wander outside their workspace.** The keyframe author spent
    turns listing and reading the operator's `~/.claude/.../memory/` before
    composing: time, tokens, and operator context in a creative session.
    Ten minutes in it was reading the engine's Rust source
    (`scene-render/crates/sr-eval`) to learn whether text can be a 3D mesh,
    which says the kit's `ENGINE.md` does not answer the capability questions
    an author actually has. *Proposed:* the author's tools are confined to
    its workspace (the kit's `kit/`, the examples, the brief), and
    `ENGINE.md` answers what the authors went to the source for.
13. **Review rounds converge slowly and cost a review each.** The keyframe
    went 4 → 4.5 → 6 against a bar of 8, about 28 min a round, and every
    review spends 9-14 min re-deriving the geometry and re-reading the schema.
    The author never sees what the reviewer will check until a round is
    spent. *Proposed:* give the author the reviewer's checklist up front and
    a cheap mechanical pre-check it runs itself (framing, contrast steps,
    overlapping labels, visible haze), and keep the expensive vision review
    for what only a model can judge.
14. **Real geometry sits unused.** The research downloaded PDB structures
    (the CC0 coordinates of the very molecules on screen), and the brief
    listed them as data to build from, but the author hand-shaped a blob and
    used the structure only as a silhouette reference. Four review rounds
    polished primitives instead. *Proposed:* the kit ships a tested example
    that turns a structure file into a surface (sphere blobs at residue
    coordinates, one clay object), so using the real data is the short path.
15. **The keyframe does not record what its author spent.** Its decisions
    carry their cost; the authoring session's cost is in no file, so a
    stage's real cost has to be estimated. *Proposed:* `keyframe.json` keeps
    a `usage` block like `vfx.json`'s.
16. **A still has no time limit.** Under the notes the author built the
    molecular surface as one clay object of 1,690 blobs; the engine
    evaluates every blob at every ray step, and the still ran past 8 minutes
    on one CPU thread with the encode host's GPU at 100%, while the still
    host (.13) stopped answering SSH and other sessions' stills queued
    behind. Nothing bounds a still's time: the author waits on its own tool
    timeout, and a still its caller gave up on kept rendering on the host
    (four orphaned probe stills held .7 for up to 2 h 21 min). *Done:*
    `render.engine_render_still` runs the remote engine under `timeout`, so
    an abandoned still dies on the host. *Proposed:* `kit.py frame` takes a
    budget well under the 600 s ceiling, reports the cost driver (blob count,
    samples), and the structure-to-surface example names a blob budget that
    renders in seconds. The cost of not having it: the keyframe author's
    whole session timed out (1,800 s) waiting on one still, and the round
    ended with no review.
17. **The author does not know its clock.** An author session is killed at
    the engine's 1,800 s call timeout, but nothing tells the author that: it
    keeps tuning parameters until the timeout ends the round, and a round cut
    off before the review is lost. The keyframe re-run's revision 1 was still
    adjusting constants at 25 min. The vfx authors show the other face of
    it: their sessions are not cut at 30 min, so a revision runs as long as
    the author keeps tuning (v02's revision 1 for sections 2 and 3 passed
    50 min). *Proposed:* the brief states the time budget, the kit prints
    the time left on every `build` and `frame`, and the harness asks for
    the current program's still at 80% of the budget so a review always
    happens.
18. **One still host, no health check.** Stills go to .13 by memory
    routing; when it stopped answering SSH (load from an unrelated process,
    then a reboot) every still in those ~45 min hung until a timeout, and
    the author's waits counted against its session. *Proposed:* `yt render`
    probes a host's SSH before routing a still and falls back to the other
    host (the encode host takes stills fine), and `yt doctor` reports a host
    that accepts TCP but not SSH.
19. **A stopped keyframe run leaves a mixed record.** The record
    (`keyframe.json`) is written only when a run finishes; stopping the
    second run after its review to save the remaining rounds left the first
    run's record (elements, 6.5 score) beside the second run's document and
    a program edited after it, and `--approve` accepted that mix. *Proposed:*
    the record is written after every round, and approval refuses a
    document newer than the record or a program whose hash no longer matches
    the document's provenance.
20. **The vfx brief and the frame gate disagree on a section's length.**
    The brief prints the symbol's `duration` and tells the author to use it
    (16.32 s, 12.32 s, 16.32 s); the gate computes the shots' run from
    another clock (17.02 s, 13.02 s, 16.67 s) and fails the document. Three
    of four sections lost round 0 to that one number, with no review, and
    the fourth crashed (author exit code 1, 37 key-order errors). The cause:
    the brief summed the shots' lengths, the gate required the end of the
    last shot, and the two differ by the breaths between measured takes.
    *Done:* `vfx.world_span(shots)` gives both numbers, and a test pins that
    the brief's number passes the gate.
21. **Authors run out mid-revision.** Two of the four opus section authors
    ended with "claude exited with code 1" (section 3 in round 0, section 2
    in its final revision), most likely the $3 per-section budget: an opus
    author spends it on research, stills and two revisions. A crashed final
    round leaves no review and a document out of step with its program.
    *Proposed:* size the budget from the measured spend of a revision, and
    let an author that nears its budget stop at its last good build.
22. **A worse final round replaces a better one.** vfx keeps whatever the
    last round left: earlier rounds are overwritten in place, and nothing
    restores the best-scoring round when the final one scores lower or
    breaks (section 2: round 1 reviewed 4/10, final round no symbol and no
    review). The probe suite already keeps only a measured improvement.
    *Proposed:* vfx keeps each round's program and document and promotes the
    best-reviewed one, as `yt probe` does. *Done:* every gated round is kept
    under `vfx/vNN/rounds/<stem>/rNN/`, the section ends on its best-reviewed
    round, and a crashed or worse final round falls back to it; a section
    whose author raises no longer aborts the others.
23. **The per-section budget is not the section's cost.** `--budget-usd 3`
    caps one authoring session, but each revision resumes in a session with
    its own cap, the reviews are billed apart, and four sections in parallel
    multiply all of it: the stage came to $88.43 against a nominal $12.
    *Proposed:* one budget per section covering its author sessions and its
    reviews, a stage budget over all sections, and the planner's estimate
    printed before authoring starts.
24. **A failed section cannot be redone alone.** `12-author` is cached as
    one unit: a single section whose author crashed leaves `yt vfx` with
    replay-all or `--fresh`-all (here ~$88 and 2.5 h to fix one of four),
    and `yt build` blocks the whole film on it. *Done:* `yt vfx --sections N`
    re-authors only those sections into the next version, carries the rest
    forward at $0 and recomputes continuity, reach and plan. Gap found in
    its first use: a re-authored section can end worse than the version it
    replaces (section 3: v01 gate-passing and reviewed at 4/10; v02 ended on
    a round with 2 geometry errors and no review after its revision timed
    out). *Proposed:* the previous version's record counts as a candidate in
    best-round promotion, so a worse or unreviewed attempt keeps the old one.
25. **A latent crash in the build's media path.** `build.py` read
    `Media.about`, a field the dataclass never had; it fired the first time
    research downloads reached a build. *Done:* the field exists, filled
    from the asset's `use_for`, with a regression test.
26. **A sliced encode leaves no report.** `render.encode_sliced` joins the
    slices and the one mix, but writes no `render.json`; the gate, `yt
    doctor`'s delivery verdict and `yt qa` read it, so the delivery encode
    has to run unsliced on one host, slower, to be judged at all. *Proposed:*
    the sliced encode writes the same `render.json` (the slices' engine
    reports merged, the mix's loudness, the joined file's duration).
27. **Messages to headless sessions.** A session list shows the pipeline's
   own `claude -p` calls next to people's sessions; a message to one lands in
   a model mid-answer. *Done:* operators message only long-lived sessions.

## What an earlier sr_core.scene project already solved

`_tmp/ORBIT_*.zip` holds the ORBIT Atlas, 1,299 records built with the same
SDK and rendered on scene-render (1,227 rendered, 72 blocked on missing
inputs). Read as an index and source only, nothing run. Four of its patterns
answer problems measured above:

28. **Fonts are pinned to files, never left to the host.** (AUTO-30) Its `pin_fonts`
    walks every text node (and 3D text) and points family, weight and style
    at a font file shipped with the project. Ours names families only: the
    encode host has 0 of the faces our treatments ask for (Inter, JetBrains
    Mono, IBM Plex, Source Sans) and 22 DejaVu faces, so every super falls
    back to DejaVu, and the keyframe review marked down the faked weight.
    *Proposed:* the kit and `yt build` pin each text node to a licensed file
    in the project (the Atlas ships Inter, InterDisplay, Lora and Liberation
    under OFL), and the build fails a family it cannot resolve.
29. **A render is skipped when nothing it depends on changed.** (AUTO-31) Its runner
    fingerprints scene digest, engine binary, assets, time, quality and
    strictness, and `--resume` reuses a frame whose fingerprint matches. Our
    stills have no cache: authors and reviewers re-render identical stills
    on two shared hosts (item 16). *Proposed:* `kit.py frame` and
    `engine_render_still` cache by that fingerprint.
30. **Every job writes a report, including the ones that did not render.** (AUTO-32)
    Its reports record `blocked-inputs` with the missing files, and a timeout
    as `timeout: true` with the seconds spent. Ours lose the sliced encode's
    report (item 26) and an abandoned still's outcome (item 16). *Proposed:*
    one report shape for every render job, written whatever the outcome.
31. **A film-wide colour pipeline.** (AUTO-33) Its cinematic recipe sets
    `colorManagement` (ACEScg working space, ACES tone mapping, exposure)
    and a CDL `look`; none of our documents set either, and the reviews
    flagged surfaces blown past their palette and sections lit like different
    rooms. *Proposed:* a validated example in the kit, and the build offering
    one look for the whole film, chosen by the model, not hard-coded.

## Run 2: a 12-minute math explainer (Euler), sonnet, ElevenLabs

Started 2026-10-20 16:50 (-03) by automation-48, one stage at a time so each
output is looked at before the next is paid for; 2D/2.5D motion graphics with
formulas, graphs, the complex plane and polyhedra, where the engine is strong.
Wall time is clock time from the stage's start to its end; cost from the
stage's own usage lines.

| Stage | Wall time | Cost | What took the time |
|---|---|---|---|
| research (`--download`, default depth, sonnet) | 22 min (16:50 → 17:12) | $3.06 (sonnet $2.33, haiku $0.73) | 55 claims, 46 sources, 28 assets (21 downloaded), 10-section outline; the per-shot asset search ran in series, about 1 min a shot, and Openverse timed out on 15 searches (each a lost wait and fewer candidates) |
| styles (`--rich --deliberate critique`, opus) | 9 min (17:13 → 17:22) | not recorded | 29 candidates, 5 picked, each critiqued in series; chose 2D motion graphics (fit 8.5) with a chalkboard and a 3D treatment for the polyhedra. The stage writes no usage into styles.json and keeps no decisions/, so its spend is invisible to the run's account |
| script (12 min, `--polish --verify --lang en`, opus) | 25 min (17:23 → 17:48) | $6.90 | 8 sections drafted in 12 min, 88 shots, 1,682 words at exactly 12:00; polish rewrote 18 lines; the fact-check flagged 6 lines and the stage revised them itself (no full re-run, unlike Run 1's three script versions); left: 1 important flag (the working title overstates the research) and 17 minor, which reach the picture's authors as constraints |
| props (`studio hdri`, `parchment paper`, `chalkboard`; hdri and texture) | 1 min | $0 | 1 HDRI (art_studio, 6.6 MB); Poly Haven has no paper or chalkboard surface, so those are left to procedural generators in the programs. Phrases were chosen by hand: derived ones fetch props a math film would only have to decline, one by one |
| keyframe (opus) | ~21 min (17:52 → ~18:13) | $8.66, 66 tool calls | rounds 6.5 → 7.0, at the bar on the first revision, written as a program with provenance; against Run 1's 3 h 21 min the difference is the author clock, the still budget and the checklist up front (AUTO-12..16) and a 2D treatment that renders in seconds; the remaining findings are craft (the superscript pi reads as capital Pi, an oversized correction tag) |
| voice (ElevenLabs, en), first attempt | 6 min lost (17:49 → 17:55) | 55 of 86 takes | stopped on take 56: a polish or revise pass had handed back the review listing's `[picture only, no narration]` mark as shot 6.9's line; ElevenLabs strips bracketed text, refused the empty input (HTTP 400), and one bad take ends the whole stage. *Done:* `script.sections_of` reads a bracket-only line as silence, polish and revise ignore it (tests/test_silent_shots.py); takes cached on disk were not sent again |
| voice (ElevenLabs, en), resumed | ~6 min (17:58 → ~18:04) | 9,223 characters (ElevenLabs) + casting/retake calls | 85 takes, 680.8 s spoken against 720 s planned; 55 takes came from disk; the director asked for 1 retake (6.17); takes are sent one at a time, so the ~10 min of a 12-minute read could be ~2 in parallel |
| retime | ~5 min (18:04 → 18:09) | small | accepted 59 of 62 reads, kept 3 planned holds, no rerecord, judged from numbers and shot descriptions, not by listening |
| keyframe, rejected by the operator | ~5 min of operator time | | text too small to read on a phone: the reviewer had passed 7.0 on a frame whose labels were ~25 px. *Done:* `work/house.md` states the legibility bar (≥54 px labels, ≥100 px formulas and titles at 1080p) and `sdkkit.rule_findings` flags film text under 1/20 of the frame height before the review is paid for |
| keyframe, second run (opus, `--reject` notes) | 30 min (18:17 → 18:47) | $11.19 (author $8.21 over 5 calls, 62k output tokens) | rounds 6.0 → 7.5, at the bar on the first revision; film text now 60-160 px. The revision was spent on one engine gap: `<formula>` takes one colour, so the amber π the operator asked for is three registered copies of the TeX layer cut by fixed-pixel masks, measured by trial stills (any size change breaks the cut). *Proposed:* per-glyph colour in `<formula>` (`\color` in the TeX or a span map). The frame gate's "too dark" warning fired on both rounds on a deliberately flat navy field; each review spent words dismissing it. *Proposed:* judge darkness on the subject's contrast, not the field's mean luma, for a 2D treatment |
| sound (`--fetch --review`) | running since 18:10, beside the keyframe | | moved ahead of vfx: it reads only the screenplay, so waiting for the picture costs its whole run in series; the Freesound fetch runs one cue at a time, about 30 s a cue (search plus download), and many sfx cues come back unfilled after 2-4 queries each (chalk strokes) |
