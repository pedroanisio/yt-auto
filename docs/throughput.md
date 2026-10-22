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
| vfx `--sections 2,3` | 3 h 34 min (15:52 → 19:26) | $59.53 | section 2: 5 → 5.5 → **6.5** (v01 had none); section 3: round 0 failed the geometry gate (cap through the protein coat), revision 1 hit the 60-min cap, ended on the failing round: no review, worse than v01's 4/10, and the run predated the keep-the-previous rule; continuity 6/10 with 7 breaks |
| draft render after the box fix | 25 min (19:39 → 20:04) | $0 | sections 1, 2, 4 authored; the sliced encode now writes render.json (AUTO-28 merged); section 2 shows a restart of its world near 25.5 s and a letter row inside the caption band |
| vfx `--sections 3` (third attempt) | 1 h 46 min (19:27 → 21:13), stopped | not reported | round 0: 19 errors, no review, the same `hero-cap` penetration; the author's session timed out twice waiting on stills queued behind another film's vfx; stopped by the operator to wait for the gate fix (item 32) |
| vfx `--sections 3` (fourth attempt, from the gate-fix worktree, renders on .7 only) | 3 h 25 min (22:16 → 01:41) | stage reports $91.22 | first pass of the geometry gate in four versions (`hero-attached` declared); rounds 4.5 → 6 → 5.5 and best-round promotion kept round 1 (6/10); its author's subprocesses ran code rebased underneath it at 01:00 |
| build, all sections | ~3 min | small | **publishable**: 11 includes, 11 of 11 shots, 40 audio tracks; supers still flagged by the old direction-counting check |
| full first draft render, 2 slices | 38 min (01:43 → 02:21) | $0 | 64.3 s, all 11 shots; the mix alone took ~10 min on .7 after both slices; no black stretch, steady brightness |
| gate on the draft | ~15 min (02:25 → 02:40) | opus triage | not deliverable, 7 blocking: off-camera hero and reveals (vfx), an animated light flattened by the hoist, loudness 2.9 LU short, cues the schema capped, a CC BY image embedded in a section and missing from the credits |
| QA pass 1 on the draft | ~35 min (02:40 → 03:00) | opus review | **3.0** overall (editing 2, narrative 2, voiceover 6 best), 32 blocking; its worst findings ("shot 1.2 replays the opening", "4.2 rewinds the world", jumps) traced to the build, not the authoring: item 38 |
| rebuild with the merge fix + draft render + QA pass 2 | 1 h 05 min (03:00 → 04:05) | opus review | **4.4** overall (+1.4): narrative 5 (+3), editing 4 (+2), production 4 (+1); 24 blocking (was 32); voiceover 5 (−1) on the rushed takes 3.2/3.3 no retake could slow (item 10); still blocking: off-camera haze and bases in 1.2 and 3.1, frozen holds at 14.7 s and 59.6 s |
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
    an abandoned still dies on the host. Still open: the local side. When an
    author's session times out, its `sr.py frame` children are re-parented
    and keep their place in this machine's still queue (two of them held
    it 23 and 27 min for a session that no longer existed). *Proposed:* the
    author's tools run in a process group the harness kills with the
    session; `kit.py frame` takes a
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
18. **One still host, no health check.** (Also: two films' vfx stages share the same two still slots with no queue between projects; on 2026-10-20 a 12-minute film's authors held them while a 60-second film's last section waited.) Stills go to .13 by memory
    routing; when it stopped answering SSH (load from an unrelated process,
    then a reboot) every still in those ~45 min hung until a timeout, and
    the author's waits counted against its session. *Proposed:* `yt render`
    probes a host's SSH before routing a still and falls back to the other
    host (the encode host takes stills fine), and `yt doctor` reports a host
    that accepts TCP but not SSH. *Done (AUTO-69):* every job ssh and rsync
    gives up on a connection after 10 s (the pre-routing check sooner); it reads the
    host's load and GPU memory in use, and a saturated host (load over its
    cores, under 1 GB of GPU free) loses the stills to the quiet one; a
    lost connection on a sync sends the job to the other host once (a slice
    waits for its turn there, one per host at a time, and later slices pass
    the dead host over); doctor prints both numbers beside the routing.
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
    from the asset's `use_for`, with a regression test. The second latent
    bug on the same path: the image layer was written with `fit="contain"`
    and no box, which the SDK's check passes and the engine refuses (C16),
    failing both slices of a draft encode. *Done:* the layer carries the
    frame as its box (`tests/test_build_media.py`). That path had never run:
    no research image reached a build before the asset fix.
26. **A sliced encode leaves no report.** `render.encode_sliced` joins the
    slices and the one mix, but writes no `render.json`; the gate, `yt
    doctor`'s delivery verdict and `yt qa` read it, so the delivery encode
    has to run unsliced on one host, slower, to be judged at all. *Proposed:*
    the sliced encode writes the same `render.json` (the slices' engine
    reports merged, the mix's loudness, the joined file's duration).
27. **Messages to headless sessions.** A session list shows the pipeline's
   own `claude -p` calls next to people's sessions; a message to one lands in
   a model mid-answer. *Done:* operators message only long-lived sessions.

32. **The geometry gate cannot be told two parts are joined.** A hero
    attached to another object by design (a cap on the end of a strand built
    from overlapping beads) is reported as a penetration of that object every
    round; the author keeps trying to separate parts that must touch, and the
    round ends with errors and no review. One section lost three versions to
    it (~5 h). *Done (merging after the other film's vfx run):* `<meta
    name="hero-attached" value="<hero> <object or prefix*>"/>` declares a
    joint, a hero is never checked against its own ancestors, and the
    finding's advice and the vfx brief name the meta.

33. **A stage cannot be told to finish now.** Once a section has a
    reviewed round, the operator may judge that more revisions are not
    worth their hour; but `yt vfx` writes its records only when the stage
    ends, so stopping it throws away the reviewed round with the rest. The
    only choices are to wait out every revision (each up to 60 min) or to
    lose the round. *Proposed:* a signal (or a `--rounds` change read
    between rounds) that makes the stage promote its best round so far,
    finish continuity, reach and plan, and write its records.

34. **An author workspace pins the checkout that created it.** `kit.py`
    and `sr.py` in `vfx/vNN/` insert the absolute path of the code tree that
    wrote them (a temporary worktree, here) into `sys.path`, and load its
    `.env`. Removing that tree breaks every later build or frame of the
    workspace, including a revision still running, and makes an old
    version impossible to rebuild. *Proposed:* the workspace resolves the
    repo at run time (an env var or the project's own pointer), not a path
    frozen at creation.

35. **A hoisted light loses its animation.** `yt build` lifts each
    section's lights into the scene (invariant 7) but carries only their
    attributes: a light keyed inside its section (a rim that brightens on a
    cue) arrives static, and the gate blocks on the build warning.
    *Proposed:* the hoist carries a light's `animate`/`key` children,
    re-timed by the include's offset.
36. **An image an author embeds is not credited.** The build credits the
    research images it places itself; an author who embeds one inside a
    section's world (a micrograph as the opening plate) leaves the shot's
    image field empty, so the CC BY credit never reaches the credits and
    the gate blocks on the licence. *Proposed:* the build scans the
    included documents for project files under `downloads/` and credits
    each from the download manifest.
37. **Quiet sources cannot be gained up.** Freesound previews arrive tens of
    dB under design; the schema caps gain at +24 dB per track and bus, so a
    cue asking +50 dB sits 52 dB short, and the mix lands 2.9 LU under its
    loudness target. *Proposed:* `yt sound --fetch` normalises each fetched
    file to its cue's design level before the build, and refuses a source
    it cannot bring within reach.

38. **Measured narration split every world into per-shot restarts.** The
    build folded consecutive shots of one world into one include only when
    their spans touched; `yt voice` places takes a breath apart (0.35 s in a
    section, 0.8 s between), so with measured narration no two shots ever
    merged and every shot restarted its world at t=0: the film replayed
    its opening at 4.4 s and 12.4 s and rewound the ribosome at 56.6 s. QA
    scored editing and narrative clarity 2. Every film timed by `yt voice`
    had it. *Done:* shots of the same world merge across a gap no other shot
    occupies; the beats stay on the takes (`tests/test_build_media.py`).
    The same bug froze the other film's draft (86 per-shot includes, 23
    after the fix). Follow-on risk, measured there: once a world runs
    continuously, one with many asset layers that are never time-bounded
    (220 layers, 105 unbounded) runs an 8 GB GPU out of memory about 20 s
    into the encode, while single stills render. *Proposed:* the vfx gate
    flags layers without start/end in a long world, and the encode slices
    by memory, not only by time.

39. **`yt improve` underestimates a pass by an order of magnitude.** Its
    dry run for this film estimated $17.50 for voice, retime, keyframe, vfx
    `--fresh`, sound, build, a final render, gate and QA; today's measured
    vfx passes alone cost $59.53 and $91.22. With the default $15 budget the
    loop stops itself before the stage that matters, or, given a budget set
    from the estimate, overspends it several times. *Proposed:* the estimate
    reads each stage's measured cost from its own report (`vfx.json`'s
    `usage`, the decisions' `cost_usd`) for this project or a like one, and
    a vfx pass names only the sections QA routed to it (`--sections`),
    not `--fresh` for all.

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
| sound (`--fetch --review`, sonnet) | 51 min (18:10 → 19:01), beside the keyframe and vfx | $2.71 (10 calls, 215k output tokens); Freesound free | 139 cues, 33% of the runtime scored; Freesound filled 83 (30 reused a file already fetched), 32 unfilled (27 sfx such as chalk strokes, 5 music), 24 silences skipped; licences CC0 50, CC-BY 33, so the credits must carry them. Run beside the keyframe, so its 51 min cost the film nothing. The fetch is serial, ~13 s a cue for search plus download, about 25 min of the 51. *Proposed:* fetch cues 4-8 at a time (done: `--jobs`, default 4, covers the search, the download and the judge). The listen-back's revise pass changed 0 cues and kept 15, yet its verdict is "not ready for an editor" (timecode overlaps M8/S9/Q9, A14–A16, M10/Q13/M11; levels missing on five silences). A review that reports and then keeps everything is the script fact-check's old gap again. *Proposed:* an overlap between two cues of the same kind, or a silence with no level, is a rule finding the stage fixes before the model is asked |
| sound `--synth` (the 32 unfilled cues) | 1.5 min (19:02 → 19:03) | $0.21 | one agent wrote a 315-line numpy program (filtered-noise chalk strokes, plaster and felt taps, accelerating ticks, two-note pads, a piano line) and every WAV passed the check: 32 of 32, peaks −14 to −18 dBFS. It is cheaper and faster than the Freesound pass, and nothing in it waits on a network. *Done:* `yt run` passes `--synth` with `--fetch`, so a sheet never leaves the stage with holes, and runs the stage beside the keyframe and vfx (AUTO-75) |
| vfx (opus, `--jobs 8`, $6 a session, 2 revisions) | 6 h 0 min (18:55 → 00:55), **killed by the run's own `timeout 21600`**, so no `vfx.json` and no 12-author record were written | reviews $26.11 (10 opus reviews, $1.80-3.82 each); author sessions not recorded (the account dies with the process), estimated $40-60 | planning 14 min (all 8 sections at once). First attempts 25-110 min; scores 2.5-6.5. Section by section: 1 at 5.5, 2 at 5 (two of its three rounds lost to the still host, `unavailable`/`failed`), 3 at 6, 4 at 6.5 (best round restored over a 5.5 last), 5 at 4, 6 at 2.5 → 4 → 5.5, 7 never reviewed (a deliberate slice reported as a penetration, then a reads-merged), 8 never reviewed (its one error was a host failure). What took the time, in order: (1) the 8 GB still host saturated by 8 authors plus a peer's DNA run (load 29, GPU 7.8/8 GB): stills failed and the gate charged the author for them. (2) Every `sr.py frame` of a 3D section also ran the whole-symbol encode for the off-camera check: 20-40 min per call for section 7, so its revisions got one or two looks. (3) Revisions could not finish: 4 of the first 4 hit the 60 min mark and the 15 min finish, kept the previous round and cost ~$3-5 each for nothing. Once the peer's run stopped (21:40), revisions finished in ~30 min and raised scores (4: 4.5 → 6.5, 6: 2.5 → 5.5). (4) The stage saves its record only when every section is done, so seven finished sections waited 1.5 h on one, and a kill lost the record of all eight. *Proposed:* the gate retries infrastructure failures and never charges them to the author; `sr.py frame` renders stills only, and the encode check runs once per round on the big host; a revision hands in its program at a fixed mark; the stage saves each section's record as it finishes; `yt vfx --recover` rebuilds the record from `vfx/vNN/rounds` and the cached reviews (done, below); never wrap a stage in a timeout shorter than its own clock |
| vfx `--recover` (new) | ~15 min (00:58 → 01:13) | $2.73 (the continuity pass) | rebuilt 12-author from vfx/v01/rounds and the 10 cached reviews: sections 1-6 back on their best reviewed rounds (5.5, 5, 6, 6.5, 4, 5.5), 7 and 8 marked for `--sections` (never reviewed); then continuity 6/10 (7 breaks between sections), reach 5/10, plan. Instead of re-authoring 8 sections (~6 h, ~$80) |
| vfx `--sections 5,7,8` (opus, `--jobs 3`, no wrapping timeout) | 58 min (01:14 → 02:12) | $40.79 (5: $11.83, 7: $13.14, 8: $12.78; continuity, reach, plan the rest) | first attempts in ~20 min with 0 errors on a quiet still host (last night: 25-110 min); 7 passed the geometry gate for the first time (5 → 6), 8 was reviewed for the first time (4.5 → 6), 5 went 4 → 5. AUTO-26-27's budget stopped every section after one revision (next round estimated past $15) and kept the best round: no 75-min revision that hands in nothing. The film: 8/8 sections, scores 5.5 · 5 · 6 · 6.5 · 5 · 5.5 · 6 · 6, 23 of 24 effects, 88 shots, continuity 7/10 (one film, 4 breaks), reach 6/10. The contrast is the measurement: the same authors on an uncontended host with no false geometry alarm take 20 min a round, not 1-2 h |
| build, first attempt | ~3 min (02:13 → 02:16) | small (sonnet edit) | refused: 20 check errors, every one a font the repo does not ship since AUTO-30 pinned fonts to files: section 2's author wrote `font="Source Sans 3"` (the treatment's type, from styles.json) with no fallback on 19 text nodes, and one node `STIX Two Text`. The build printed only the first 12 of 39 findings, all warnings, so the errors needed a second `--force` build to a scratch path to be seen. Three editorial blocks (image provenance) were settled from the Commons pages in ~5 min and written to the project's house.md. *Proposed:* sdkkit flags an unshipped family at author time, so the author fixes it in its own round; styles.json chooses type from the shipped families or ships what it chooses; print_build lists errors before warnings |
| build (after the fonts shipped, e3165f9) | ~10 min of waiting on the fonts lane, then 2 builds of ~1 min | small (sonnet edit, twice) | the first wrote the scene but was not publishable: the editor blocked shots 3.8 and 7.1 for a Goldbach portrait that the screenplay planned and the authors had already declined (their programs say so; neither document references the file). Once the project's house.md said what the authored sections show, the second build came out publishable: 11:59, 88 shots, 71 captions, 200 audio tracks on 4 buses, 86 includes. *Proposed:* the build's editor reads the authored documents (or vfx.json's per-shot picture) before blocking on the screenplay's planned visual |
| render draft, `--slices 4` over both hosts | 4 min lost (02:24:35 → 02:28) | $0 | the two slices on .13 (15 GB) were killed for memory (exit 137) on a 3-minute range of an 86-include, 200-track scene; the two on .7 rendered 3 min of film in ~3 min each, and were thrown away because one failed slice fails the run. *Proposed:* slices go only to hosts with the memory the document needs (an estimate from the include count and the asset bytes), a failed slice is retried on the big host, and finished slices are kept |
| render draft, one encode on .7 | **4.5 min for 12 min of film** (02:29:29 → 02:34) | $0 | the encode is not the bottleneck: 276 s for 21,581 frames at draft on the RTX 3050 with 125 GB. What watching it found, all general: (1) **24 designed silences muted the voice**: the build wrote each `silence` cue as a window on the master's volume, so whole spoken lines were gone (−91 dB at 0:16-0:31 and 0:47-1:10). *Done* (9b55617): the window sits on the music and effects buses. (2) **E12 on every include of section 8**: its own `<markers>` (a note for the sound) — `--strict` would have refused the final. *Done* (7d92050): the include copy drops markers nothing names. (3) **Loudness −17.3 LUFS, LRA 19.4 LU** under `normalize=integrated` with the limiter: quiet narration-only stretches against swells. `--normalize dynamic` gave −15.2 / 6.5 LU, and `--loudness -13` compensates the ~1 dB AAC pass: **−14.6 LUFS, LRA 6.2, TP −2.4**. *Proposed:* `dynamic` as the default for a voice-led film, and the build's compensation computed from the measured AAC loss instead of left to the operator. Three draft renders of 4.5-5 min each made these measurable in 25 min |
| watch (8 reviewers, one per section, frames at every shot midpoint) | 3.5 min | ~$15 (557k subagent tokens) | **the draft was frozen**: 53 blocking findings; most sections showed their opening state shot after shot (section 3 identical in 8 of 10 frames, section 8 frozen for 75 s, section 6's identity never on screen), while the authors' own proof frames at those times showed the full builds. Cause: 86 per-shot `<include>`s, each playing its section's world from t=0. The include has no time offset, so the build must fold a section's consecutive shots into one include, and its rule only folded spans that touched to the millisecond, while measured takes sit 0.35 s apart. automation-4c found and fixed the same rule for its own film (fold across a breath when no other shot starts in it): 23 includes. Lesson: the build's own report said `86 include(s)` for 88 shots, a number that alone gives it away, and no gate reads it. *Proposed:* the build warns when a symbol with consecutive shots is split into several includes; `yt qa` compares a film frame with the section's proof frame at the same time |
| render draft with 23 includes | 3 attempts, ~15 min | $0 | **GPU out of memory** at 562.8 s (8 GB RTX 3050), in section 7, a 2D world with 220 layers (105 never time-bounded; 76 formula assets). Stills at that time render; encodes die about 20-27 s into the world at draft and at final, so the cause is textures held for the world's life, not one frame. The engine has no memory option. Workaround: `--slices 48` (15 s each, a fresh process each) pinned to the big host with `RS_RENDER_HOSTS`. *Proposed:* the kit's rules flag a world whose untimed asset layers exceed a budget (time-bound every layer to the phase that shows it); the engine frees a layer's targets while it is invisible; the build sizes slices from that |
| render draft, `--slices 48` on .7 | 38 min (03:20 → 03:58), failed | $0 | 46 of 48 slices rendered (~50 s each, ~35 s of it start-up and sync); slices 37 and 38 (555-585 s, section 7 shots 7.3-7.4) ran out of GPU memory 13 s and 2.5 s in. Stills at those exact times render; on the 15 GB host a 45 s range was killed for system memory (137). So section 7's world is too heavy to encode on this hardware, whatever the slicing. *Done:* the project's house.md gives the vfx authors a memory budget (time-bound every layer and group, ~60 layers alive at most, no opacity-0 leftovers); section 7 is re-authored under it. *Proposed (general):* the kit measures a world's live layers and shapes over time and makes an over-budget world a rule finding; the gate's encode check, which already encodes the world, reports an out-of-memory as the author's error with the time it struck |
| vfx `--sections 7` (memory budget in house.md) | ~50 min (04:00 → 04:53) + 10 min film-wide | $15.06 (budget reached after one revision) | the author kept the look and timing with 704 shapes (was 3,125) and every one of 242 layers time-bounded (was 115 of 220); reviewed 4.5 → 5.5. The competition then kept the heavier v02 (6.0), which no GPU here can encode: the score knows nothing about cost. *Done* (5788891): `yt vfx --take-attempt N --why` takes the attempt back from rounds/<stem>.attempt with the reason on the record; the reviews took 8-15 min each because they audit the program, not just the frames. *Proposed:* the gate's encode check makes an out-of-memory an error, so a section that cannot encode never wins a competition |
| render draft with the light section 7 | **3.3 min, one encode** (05:25) | $0 | no out-of-memory; 23 includes; −14.6 LUFS, LRA 6.2, TP −2.4, 0 warnings |
| watch #2 (8 reviewers on the fixed draft) | 4 min | ~$15 | blocking 53 → 8, mean section score 2 → 5.5: every section now shows its planned build. The 8 left: burned captions printed over authored type in sections 3, 6, 7, 8 (*done*, 5dbd83b: captions are a sidecar track unless `--burn-captions`), shot 1.5 shows Euler's hypothetical Figure 3 under "a city's bridges" (the project's house.md had called it the Königsberg figure; corrected), and one label overlap in 6.14 |
| **stopped by the operator** (2026-10-21 ~05:55) | — | — | last state: a draft at 3.3 min per encode with sidecar captions, −14.6 LUFS, LRA 6.2, TP −2.4, 0 engine warnings; sections at 5–7/10; no final encode, gate, review or QA. Measured spend about US$170 plus ElevenLabs. What it would still have taken: sections 1 and 6 re-authored for 1.5 and 6.14 (~50 min, ~$30), then the final encode (~5 min), gate, review and QA (~45 min) |

**The benchmark the operator set (2026-10-20 19:09).** A ChatGPT work environment made
`_tmp/the_last_signal_30s_vertical.mp4` in 46 minutes: a 30 s 1080x1920 voxel sci-fi short
with a story, a consistent cast (a courier and a war machine) across every shot, cinematic
framing, a grade change for the resolution, boxed captions about 60 px tall, a title card,
and audio at −16.4 LUFS integrated (LRA 2.4, no silence until the last 0.5 s); 4 hard cuts,
the rest continuous camera moves; muxed by ffmpeg (Lavf60). Its faults: the camera clips into
the robot at about 19 s, characters are cut by the frame edge in the first seconds, and the
captions repeat the narration word for word. That is 92 s of wall time per second of film;
this run projects about 70, so the rate is not the gap. The gap is what the time buys: Run 1
spent 2 h 33 min and $88 on 60 s of animation that its own reviewer scored 4-6. The short
spends no time on research, fact-checking, deliberation or rounds of review, and keeps one
context and one program for the whole film, so its cast is the same in every shot. Our 8
section authors share no code, and a recurring character is authored 8 times. *Proposed:* a
fast lane for shorts (one author, one program for the whole film, one review at the end, no
research for fiction), timed against this exact brief; a shared `cast/` module of SDK
functions written once after the keyframe and imported by every section; and a revision
only where the previous one moved the score.
