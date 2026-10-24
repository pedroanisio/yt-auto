"""Review the rendered film against the publication bar, pass after pass.

docs/production.md is the brief: the rendered frame is the evidence, ten
categories scored 0-10 by professional standards, READY TO PUBLISH only at an
overall of 9.0 with no category under 8.5 and nothing blocking. This stage is
the harness that runs it:

    yt qa --title monty                    # sample, render, score: qa/pass-NN
    yt qa --title monty --frames 40        # 40 stills: every shot if fewer, plus evenly spaced ones
    yt qa --title monty --reuse pass-02    # score the frames of an earlier pass again

Sampling is deterministic and written down (`samples.json`): every shot
once (up to EVERY_SHOT_UP_TO without --frames), at its midpoint, never at a
boundary, because the gap between takes is real and a frame on it shows an
empty stage (AUTO-103). Each row's `why` keeps the reasons
docs/production.md §7 gives a shot priority -- the hook, the ending, the
payoff, every section's opening, the re-hooks, 25/50/75 % of the runtime,
the longest authored effects -- or `shot` when it has none. `--frames N`
is how many stills: above the shot count the extra stills are evenly spaced
over the runtime, each inside a shot and at least half a second from every
other sample; below it the priority order decides what is kept, the
reviewer is told which shots it has no still of, and the scorecard names
them (`unsampled`, and a line under not_evidenced).

A midpoint misses what happens elsewhere in its shot, and the narration
says when each line lands (AUTO-104). So each narrated line -- one
sentence of a take in voice.json, placed on the film by build.json's shot
`at` -- also gets a still where its first word is said, half a burst
inside its shot like the evenly spaced ones, labelled `line: <first
words>`, and the brief carries the line (`lines`, with when it starts
and `frame`, the file that shows that moment) beside it, so "is it on
screen when it is said" is judged at that moment. A line merges into a
still of its own shot only when that still's burst has the frame where
the line starts: half a second off, a still does not show it. Line stills
come after every shot's own: the payoff, re-hook and reveal lines
(`KEY_LINES`) first, then the lines said inside a shot before the ones
that open their take, spread over the runtime; without --frames they are
added up to EVERY_SHOT_UP_TO stills in all, and under --frames they count
against N before the evenly spaced ones. The lines left without a still
are named to the reviewer and in the scorecard (`lines_unsampled`, and a
line under not_evidenced). A wordless film has none.

The stills are rendered by the engine on the render host, from the very document the
encode uses. A still cannot evidence motion, so each sample is rendered as
one contiguous window of frames around its midpoint (`BURST_SECONDS`, at
most `BURST_MAX_FRAMES` consecutive frames, planned in `samples.json`) in a
single `--frames A..B` job -- a pass of N samples costs N jobs -- and the
midpoint still is that window's middle frame. The tooling measures the
window: the mean absolute difference between consecutive frames, and
between its first and last, says whether the shot is frozen, moving or
jumps. The stills are also tiled into `contact-sheet.png` with timestamps.

Before the model scores anything, the mechanical checks run and file their
own findings, each with an owner and a fix: a nearly flat still, clipped
highlights, a frozen burst, a jump inside a shot, and the engine's own
encode diagnostics from `render.json` (objects never inside the camera's
view, per shot, with their ids). A mechanical finding is blocking when it
sits on the hook or the ending or when the same defect is on more than one
sampled shot, and while one stands the reviewer's visual_quality and
animation_vfx cannot exceed `MECHANICAL_CAP`. The film's rhythm
(`rhythm.py`, AUTO-141) files `hold`, `metronomic`, `slow-opening` and
`wpm` findings beside them, from the timeline always and from the render's
scene scores when video.mp4 is current and a render host scans it; a
blocking one caps editing_pacing instead (a hold caps both), and the pass
writes `rhythm.json` (CAP_RULE).

Then the model reads the frames and the sheet, with the screenplay, the
editorial decisions, the numbers and the mechanical findings beside each
frame, and files the scorecard under a schema. What stills cannot evidence
-- the voice as heard, the mix, the music -- is scored only from a listening
review that heard what the film carries (AUTO-105): sound.json's listen-back
(`yt sound --review`, the cue sheet read cue by cue) for sound_design and
music_score, voice.json's take review (`yt voice`) for voiceover. Each
records a digest of what it heard (`heard`); the prompt carries it marked
current or stale, and a review whose mark no longer matches its file, or
whose file is newer than build.json, is stale and what it said is left out.
A category no current review covers has no score (None), is named under
`not_evidenced` with the command that produces the evidence, and the bar
says READY cannot be judged for it rather than NOT READY on a frozen number;
the overall is then the mean of the scored categories, and two passes that
scored different categories compare their overalls over the ones both
scored, so losing the low audio scores is never a gain.
A film with no audio track at all scores `SILENT_SCORE` on sound_design and
music_score by rule, never a neutral 5.

One reading is not a measurement (AUTO-107): its run-to-run noise, about
±0.5 a category, is the size of the steps read as progress. The scorecard
is asked QA_SAMPLES (3) times on the same evidence, `<pass>-scorecard-s1`..,
each with the categories in its own order (`category_order`). A category
scores the median of the valid samples and the scorecard records its
`spread` (max - min; the overall's under `spread.overall`), which `yt
improve` reads as the size of no change. The samples' findings are grouped
into defects by one decision (`<pass>-scorecard-merge`, the improve loop's
"same problem" test): one two or more samples report stands, blocking only
when two called it so; one a single sample reports is `low_confidence`,
listed and not routed. The reviewer's `blocking` claims go through the same
decision, so one reading's claim blocks nothing either. A sample whose call
fails is a failed engine row, never a score, and fewer than two valid
samples is `not_evidenced`: no scores, never READY. `scoring` records each
sample; the bar reads the medians. A category set by rule (a silent film's
sound, a capped category) records no spread: its score is no sample's. An
audio category no current listening review covers (AUTO-105) is None in
every sample, never a median of the zeros the reviewers entered, and has
no spread; the overall is the mean of the scored medians, the median of
the reviewers' own overalls kept as `overall_filed`, and the overall's
spread is that of each sample's mean over the scored categories.

The story is judged from the film too (AUTO-99). The role carries the
narrative rubric's sentences for narrative_clarity and editing_pacing,
rendered from `narrative.rubric_text` rather than typed again here; the
prompt carries the screenplay's story (train, promise, stakes, payoff line,
the question ledger with where each opens and closes, the re-hook shots) as
the writer's declaration, and the whole narration (the voiced takes once
there are any) is written into the pass as `narration.txt` beside the
frames, so it is evidence the inspection's digest covers. The explanation
should need both: followed by ear alone or by eye alone, it is redundant
(attention 23). The scorecard's `story` block restates the premise as the
film made it, says what the viewer has at the end of each section (one
non-blank line per screenplay section, asked again once when it is not),
and predicts a viewer's satisfaction, which is a ranking proxy nothing
validates until the channel's `retention.json` exists: until then it is
recorded and named under not_evidenced, and it scores nothing.

Each pass keeps its frames, `scorecard.json` and `findings.md` (timestamp ->
problem -> root cause -> owner stage -> fix), so "measurably improved" is a
comparison with the pass before and a regression is a category that dropped.
`qa.json` at the project root carries the latest verdict for `yt doctor`.

The scoring is blind to that pass (AUTO-106). Shown the previous scores and
findings, the reviewer anchored to them: three passes in a row matched on
every category. So the samples' prompt names no previous pass, score,
overall or finding; `compare` takes the deltas from the two scorecards once
the new one exists, and `yt improve` matches a previous finding to a new
one only after both exist (its own "same problem" decision). `--recheck`
puts the previous pass's findings (at most RECHECK_MAX, 20, by place and
words alone) to a decision of their own after the scoring,
`<pass>-recheck`: stands, gone or cannot_tell in this pass's stills, with
why. The answers are filed under `recheck` (scorecard, qa.json's tally and
a findings.md section) and nothing that scores reads them. Off, nothing is
asked and nothing is filed. The blindness is the prompt's, not the file
system's: the reviewer still runs in `qa/<pass>` able to read files, so it
could open a sibling pass's scorecard.json or findings.md. Nothing in its
prompt names or points to one, but no tool rule forbids it either.

Two scores inside the samples' spread cannot say whether a revision helped,
so each pass is also judged against the previous kept cut side by side
(AUTO-108). The previous pass is `--previous PASS` (`yt improve` names its
best kept pass), else the latest scored one, else, when improve.json says
that one was reverted, the best kept pass it records. At every sampled
moment the new cut's still and the previous cut's still at the same
timestamp are shown as A and B: the previous one reused from that pass's
frames when it was sampled at the same time, else rendered from the
improve snapshot whose document and every file it references (the
renderer's own `render.reference_files`) match the `cut` digest that pass
recorded, else the moment is unpaired and named under not_evidenced. Which
cut is A is drawn per moment (`side_order`, half each, seeded by the pass)
and recorded, so position bias can be checked. One inspection
(`<pass>-compare-pass`, `SIDE_SCHEMA`), reading the pairs in a temporary
directory outside the pass, answers per moment A, B or same, why, and the
visual categories the moment bears on; the reviewer is not shown the
scores. The scorecard files `side_by_side`: the pass compared against,
each moment (order, reused or rendered, the answer), the engine-agnostic
`entries` (`{category, result: better|worse|same, source: "stills",
moment, t}`, one per category the moment bears on, or one with category
None; the shape a video comparison files with `source: "video"`) and their
`tally` (`qa.tally`: entries per category, and under `total` one vote a
moment). Nothing is compared with no earlier scored pass, with the same
cut, under --reuse (which keeps the `cut` the reused frames were drawn
from), or when the pass is not evidenced. findings.md has a "Side by side"
section. `yt improve` reads the tally before the score delta.

`--against FILE --compare-model MODEL` (AUTO-128) watches the cuts instead
of reading stills: a gateway video model is shown FILE, the previous kept
cut, and this pass's video.mp4 whole, in both orders and blind
(cutcompare.py has the rules). When it answers, its entries, `source:
"video"`, one a category, are `side_by_side` and the still pairs are not
asked; when it fails or is skipped it files nothing, a not_evidenced line
says why, and the stills are compared as above. qa/pass-NN/comparison.json
keeps the record, and the scorecard keeps its account as `compare_engine`
beside `engine`. Without --against nothing here changes.

`--watch` (AUTO-127, watch.py has the rules) sends this cut, video.mp4, to
a gateway model that watches it in motion: `--watch-model`, default
google/gemini-3.6-flash, a catalog row tagged video-input and
structured-output from another maker than every model that made the film.
The gateway (YT_GATEWAY=vercel, AI_GATEWAY_API_KEY, Node >=22 and
gateway/node_modules), the catalog, the model and a render host are checked
before a pass directory is made; a missing one exits 1. The sound is never
sent: the render host copies the picture without it, and a cut too large to
send is cut down there (downscaled, split at shot boundaries), never here. A stale or partial cut is not watched: the
problem is a not_evidenced line. Each moment the watcher reports (W1..,
qa/pass-NN/watch.json) becomes a still of the pass beyond --frames (at most
WATCH_SAMPLES_MAX), and the scorecard's reviewer says of each whether its
still shows it (`watched`); one at least two scoring samples confirm on its
still joins the findings as `source: watched:Wn`, the rest are listed in
findings.md and block nothing. The watcher's account is `watch_engine`,
its cost is in `usage`. `--reuse PASS --watch` reads that pass's
watch.json. Without --watch nothing here changes.

The loop is: fix the owning stage's source, rebuild, run `yt qa` again. This
stage changes no file but its own; it says what to change and who owns it.
"""

from __future__ import annotations

import argparse
import bisect
import copy
import hashlib
import math
import os
import random
import re
import shutil
import statistics
import sys
import tempfile
from collections import Counter
from pathlib import Path

from . import geometry as geo
from . import framecheck, house, narrative, spend, workspace
from . import listen as listen_pass
from .proofframes import (PROOF_DIFF, PROOF_LIMIT, mismatch_text,  # noqa: F401
                          proof_diff, proof_plan, ungraded)
from . import render as render_stage
from . import rhythm
from .build import EVEN_SHOTS, shots_from_scene  # noqa: F401  (standalone sampling)
from .core import read_json, timecode, write_json
from .harness import Harness, HarnessError, _digest, brief
from .script import BOUNDS as SCRIPT_BOUNDS, READING_CPS
from .script import SILENT as SILENT_LINE, Shot, spoken, visible_text
from .sound import heard as sheet_heard
from .voice import heard as takes_heard, words_estimated

QA_DIR = "qa"
READY, NOT_READY = "READY TO PUBLISH", "NOT READY"
OVERALL_BAR, CATEGORY_BAR = 9.0, 8.5
REGRESSION = 0.5          # a category that drops this much between passes

#: Scoring samples a pass (AUTO-107; judging 11, as the story review's
#: script.STORY_SAMPLES). One reading's run-to-run noise is about ±0.5 a
#: category, the size of the steps read as progress, so the scorecard is
#: asked this many times on the same evidence, each its own inspection
#: (`<pass>-scorecard-s<k>`) shown the categories in its own order
#: (`category_order`). A category scores the median of the valid samples and
#: records their spread (max - min); improve reads a change inside it as none.
QA_SAMPLES = 3
#: A pass fewer than two of whose samples answered: no score stands on one
#: reading, so it is neither READY nor NOT READY, and nothing keeps it.
NOT_EVIDENCED = "not_evidenced"

#: Motion evidence: one contiguous window of frames around each midpoint,
#: rendered as a single `--frames A..B` job; the midpoint still is one of them.
BURST_SECONDS, BURST_MAX_FRAMES = 0.5, 16
#: A still is judged on a thumbnail of this width, in luma 0-255.
THUMB = 320
FLAT_STD, FLAT_SHARE = 8.0, 0.97        # spread under / one-level share over: nearly flat
CLIPPED_LEVEL, CLIPPED_SHARE = 250, 0.10  # share of the frame at or above this level
#: A burst, by the mean absolute difference between consecutive frames.
FROZEN_DIFF = 0.25                      # every step under this: nothing moved
JUMP_RATIO, JUMP_MIN = 4.0, 4.0         # one step this far above the others' median

#: Build stills (AUTO-145). The house asks that every diagram be built in
#: steps keyed to the narration; the midpoint still and its burst cannot
#: tell a build from a finished diagram under a slow push-in. The first
#: shot of each effect of these types (vfx.json) that lasts BUILD_MIN_SECONDS
#: gets one single frame where it opens and one where it closes, beyond
#: --frames, at most BUILD_SAMPLES_MAX of them, longest shots first; later
#: shots of the effect continue its take and open on what it built.
BUILD_TYPES = ("2d-diagram", "data-animation")
BUILD_MIN_SECONDS = 3.0
BUILD_SAMPLES_MAX = 12
BUILD_WHY = "build: "
#: Where a build still of its own is written, under frames/: it is measured
#: for ink and judged by no model, and the reviewer reads the PNGs directly
#: under frames/, so it is kept out of their way (and out of the contact
#: sheet, the evidence rows and the side by side, `judged`).
BUILD_DIR = "build"
#: A still's `ink`: the share of its THUMB luma thumbnail whose edge
#: magnitude (PIL's FIND_EDGES) exceeds INK_EDGE. A build shot whose ink
#: never rises by more than BUILD_STEP of its last value between two
#: consecutive stills (first, each line, last) is `no-build`. House numbers,
#: not measured yet (TO VERIFY, docs/production.md's calibration table).
INK_EDGE = 32
BUILD_STEP = 0.15
#: Text density (AUTO-145), counted from the rendered document, never read
#: off a frame: one node over the screenplay's wall-of-text bound (direction
#: 2), or SCREEN_WORDS visible at once, is `text-dense`; a formula counts as
#: FORMULA_WORDS. Nodes named for an attributed quote, a recreated headline
#: or a licence credit are not counted, and are held to the time it takes to
#: read them instead (script.READING_CPS); build's placeholder card (an
#: asset `card-<label>` the composition draws) is reported as a placeholder.
#: House numbers (TO VERIFY).
SCREEN_WORDS = 12
FORMULA_WORDS = 4
WALL_WORDS = int(SCRIPT_BOUNDS["wall_of_text"]["value"])
EXEMPT_TEXT = ("quote-", "headline-", "credit-")
PLACEHOLDER_ASSET = "card-"
#: Measured kinds filed always minor (AUTO-145): they never escalate on an
#: edge or on more than one shot, so they cap nothing until calibrated.
ADVISORY_KINDS = ("no-build", "text-dense")
#: While a blocking mechanical finding stands, the reviewer cannot award more.
MECHANICAL_CAP = 6.0
CAPPED = ("visual_quality", "animation_vfx")
#: The rhythm findings (AUTO-141): measured from the timeline and the
#: render's scene scores, a blocking one caps editing_pacing instead; a
#: hold is also a picture that does not move, so it caps both.
RHYTHM_KINDS = rhythm.KINDS
RHYTHM_CAPPED = ("editing_pacing",)
CAP_RULE = (f"a blocking one caps {' and '.join(CAPPED)} at {MECHANICAL_CAP:g}; a blocking "
            f"rhythm finding ({', '.join(RHYTHM_KINDS)}) caps {' and '.join(RHYTHM_CAPPED)} "
            f"at {MECHANICAL_CAP:g} instead, and a hold caps all three; a rhythm finding "
            "that rests on the scan's thresholds is filed minor, and caps nothing, until "
            "they are measured (TO VERIFY)")
#: A film with no audio track scores this where sound would be judged.
SILENT_SCORE = 2.0
SILENT = ("sound_design", "music_score")
#: What a still cannot evidence (AUTO-105): each is scored only from a
#: listening review that heard what the film carries, and without one it is
#: recorded not_evidenced with no score (None), never a number from the
#: measurements or from the fact that a track exists.
AUDIO = ("voiceover", "sound_design", "music_score")
#: The listening reviews the pipeline writes: where each lives, what it
#: heard, the categories it evidences, and the command that produces it
#: again. The review that hears the rendered track, `yt qa --listen`
#: (AUTO-126, ytstudio/listen.py), is made in the pass itself, so it joins
#: these two as one more row (`listen.evidence_row`), not as an entry here.
LISTENERS = {
    "sound": {"file": "sound.json", "covers": ("sound_design", "music_score"),
              "review": "the listen-back of `yt sound --review`: the cue sheet as "
                        "spotted (each cue's place, level, fades and design) and the "
                        "palette's mix targets, read cue by cue, not the rendered track "
                        "and not the recorded narration; its mark also covers the "
                        "master's normalise mode",
              "redo": "sound --fresh --review --fetch --synth"},
    "voice": {"file": "voice.json", "covers": ("voiceover",),
              "review": "the director's take review in `yt voice`: the recorded takes, "
                        "by their words, lengths and pace",
              "redo": "voice"},
}
#: The contact sheet: stills tiled with their timestamps, for the reviewer.
SHEET_COLUMNS, SHEET_TILE, SHEET_CAPTION = 4, (480, 270), 28

#: The ten categories of docs/production.md §2, in its order.
CATEGORIES = ("visual_quality", "animation_vfx", "cinematography", "editing_pacing",
              "graphics_overlays", "voiceover", "sound_design", "music_score",
              "narrative_clarity", "overall_production")

ROLE = (
    "You are the quality reviewer of docs/production.md: you judge the rendered "
    "film, by professional standards, against the whole runtime, and you are "
    "especially critical of static composition, excessive text, weak motion, "
    "simplistic transitions, band violations (supers top, stage middle, captions "
    "bottom), placeholders, illegible type, and anything a normal viewer would "
    "notice. A 9 is work that would not look out of place on a channel people "
    "subscribe to; 8 is competent and unremarkable; 6 is functional; 3 is a "
    "document that renders. Every finding has a timestamp, what is visible, why "
    "it lowers quality, the root cause in a source file or stage, the owning "
    "stage and the fix. voiceover, sound_design and music_score are scored only "
    "from the listening reviews given that heard what the film carries; one that "
    "none of them covers you do not score (its number is discarded and it is "
    "recorded not evidenced), and nothing stands in for a review: never infer "
    "audio quality from the measurements, the cue sheet or the fact that a track "
    "exists. Whatever else the frames cannot show you list under not_evidenced."
)

#: The rubric rows narrative_clarity and editing_pacing are read against: the
#: ones a viewer of the finished film can judge (docs/production.md §2 row 9:
#: hook, stakes, re-hooks, open questions closed, escalation, the payoff on
#: the promise). `train` is the plan's sentence and `honesty` a claim against
#: the sources, which the frames cannot settle. Escalation and re-hooks are
#: graded by the `tension` row (AUTO-136), with each section's `escalation`
#: in the story brief as the writer's declaration beside it; visual support
#: has no row of its own and reaches the reviewer through the frames (each
#: shot's `shows`).
STORY_RUBRIC = ("hook", "stakes", "open_loops", "reveal_setup", "tension", "ending")

ROLE += (
    "\n\nnarrative_clarity and editing_pacing are judged on the story the film "
    "tells, heard in the narration and seen in the frames, never on what the "
    "screenplay declares. A 9 there shows what each of these criteria of the "
    "narrative rubric aims for; the further a film falls short of them, the lower:\n"
    + narrative.rubric_text("writer", keys=STORY_RUBRIC))

#: The role when a current `--listen` heard the film (AUTO-126): ROLE with
#: the listener's record named among the listening reviews. Without one,
#: ROLE is used unchanged.
_HEARD_AFTER = "or the fact that a track exists."
assert ROLE.count(_HEARD_AFTER) == 1
ROLE_LISTENED = ROLE.replace(_HEARD_AFTER, f"{_HEARD_AFTER} {listen_pass.LISTENED}")

#: What the screenplay owns of the story, named in DUTIES so a story finding
#: has an owner; docs/production.md §2 row 9 names the same (test_doc_production).
STORY_DUTY = ("hook", "stakes", "re-hooks", "open questions", "payoff")

#: A viewer's predicted satisfaction, on the five labelled points of a survey
#: (judging 4: a ranking proxy, not a measure of the film).
SATISFACTION = ("1 very dissatisfied", "2 dissatisfied",
                "3 neither satisfied nor dissatisfied", "4 satisfied", "5 very satisfied")

#: The channel's measured retention, under the work directory. Until it
#: exists a predicted satisfaction is recorded and not evidenced.
RETENTION = "retention.json"

#: The narration, written into each pass beside its frames.
NARRATION = "narration.txt"

#: Who can own a finding: a pipeline stage, or a human route. Improve routes by
#: this word alone; `external` carries the reason in the finding's `fix`.
OWNERS = (tuple(stage for stage, _ in workspace.STAGES)
          + (() if any(s == "keyframe" for s, _ in workspace.STAGES) else ("keyframe",))
          + ("upstream-fact", "external"))

#: What each owner is responsible for, so a finding is placed in the source that fixes it.
DUTIES = {
    "research": "the dossier: claims, sources, licensed assets",
    "styles": "the treatment: palette roles, typography, motion language, the look",
    "script": f"the screenplay: the story ({', '.join(STORY_DUTY)}), shots, narration, "
              "supers, timing plan",
    "props": "the CC0 models, HDRIs and texture maps fetched for the authors",
    "keyframe": "the look-lock frame every authored world inherits",
    "voice": "the narration takes, their timing, and the captions file",
    "retime": "the measured narration lengths written back into the screenplay",
    "vfx": "the authored worlds: geometry, materials, lights and shadows, camera, "
           "animation, composition inside the frame, the renderer settings of each "
           "document",
    "sound": "the cue sheet: effects, ambience, score, levels and timing, the fetched "
             "or synthesised files",
    "build": "the compile: placement on the timeline, includes, captions, the audio "
             "mix and its staging, the tail",
    "render": "the encode: quality, bitrate, the delivery settings",
    "upstream-fact": "a wrong or unsupported claim in the research or the screenplay; "
                     "production fixes pictures, not facts",
    "external": "a policy, a licence or an approval outside this repo, or a finding no "
                "stage above can own: say why in `fix`",
}

SCORECARD_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {"type": "object",
                   "properties": {k: {"type": "number"} for k in CATEGORIES},
                   "required": list(CATEGORIES)},
        "overall": {"type": "number"},
        "verdict": {"type": "string", "enum": [READY, NOT_READY]},
        "findings": {"type": "array",
                     "items": {"type": "object",
                               "properties": {
                                   "timestamp": {"type": "number"},
                                   "frame": {"type": "string"},
                                   "problem": {"type": "string"},
                                   "why": {"type": "string"},
                                   "root_cause": {"type": "string"},
                                   "owner": {"type": "string", "enum": list(OWNERS),
                                             "description": "the one stage that fixes it, "
                                             "upstream-fact, or external with the reason "
                                             "in `fix`"},
                                   "fix": {"type": "string"},
                                   "severity": {"type": "string",
                                                "enum": ["blocking", "minor"]}},
                               "required": ["timestamp", "problem", "why", "root_cause",
                                            "owner", "fix", "severity"]}},
        "blocking": {"type": "array", "items": {"type": "string"}},
        "not_evidenced": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
        # The story as the film told it (AUTO-99). Required of a new pass;
        # compare() reads scores only, so an older scorecard without it
        # still compares.
        "story": {
            "type": "object",
            "properties": {
                "premise_restated": {"type": "string", "description": (
                    "the film's premise in one sentence, as the narration and the "
                    "frames made it, not as the screenplay declares it")},
                "sections": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "section": {"type": "integer", "description": "section number"},
                        "viewer_has_at_end": {"type": "string", "description": (
                            "the fact, reversal or image the viewer has at the end of "
                            "this section that they lacked at its start, or that "
                            "they have nothing new (structure 20)")}},
                    "required": ["section", "viewer_has_at_end"]}},
                "satisfaction": {"type": "string", "enum": list(SATISFACTION),
                                 "description": (
                    "how satisfied a viewer who watched to the end would say they "
                    "were; a prediction recorded for calibration that scores "
                    "nothing (judging 4)")},
                "satisfaction_why": {"type": "string"},
            },
            "required": ["premise_restated", "sections", "satisfaction",
                         "satisfaction_why"],
        },
    },
    "required": ["scores", "overall", "verdict", "findings", "blocking",
                 "not_evidenced", "rationale", "story"],
}


# --------------------------------------------------------------------------- #
# scoring samples (AUTO-107)
# --------------------------------------------------------------------------- #

def category_order(seed: int) -> tuple[str, ...]:
    """The ten categories in the order sample `seed` is asked them. Seed 0
    is CATEGORIES; seeds 1 to 10 are its rotations by a stride of 3, prime
    to 10, so each of the first ten starts from a different category and a
    reviewer that anchors on the first score it writes anchors on a
    different one in each sample (judging 8)."""
    if not seed:
        return CATEGORIES
    start = (seed * 3) % len(CATEGORIES)
    return CATEGORIES[start:] + CATEGORIES[:start]


def scorecard_schema(watched: bool = False) -> dict:
    """SCORECARD_SCHEMA, or with `watched` (AUTO-127) a copy that also
    requires the reviewer's verdict on each watched finding."""
    if not watched:
        return SCORECARD_SCHEMA
    from . import watch as watch_pass      # it reads this module's owners
    out = copy.deepcopy(SCORECARD_SCHEMA)
    out["properties"]["watched"] = copy.deepcopy(watch_pass.WATCHED_SCHEMA)
    out["required"] = [*out["required"], "watched"]
    return out


def sample_schema(seed: int, watched: bool = False) -> dict:
    """The scorecard's schema for sample `seed`: the categories under
    `scores` in `category_order(seed)`. SCORECARD_SCHEMA itself is never
    changed, and seed 0 is it (`scorecard_schema(watched)`)."""
    if not seed:
        return scorecard_schema(watched)
    order = category_order(seed)
    out = copy.deepcopy(scorecard_schema(watched))
    out["properties"]["scores"]["properties"] = {k: {"type": "number"} for k in order}
    out["properties"]["scores"]["required"] = list(order)
    return out


def _number(value) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def combine_scores(cards: list[dict], *, unheard=()) -> tuple[dict, float | None, dict]:
    """The valid samples' answers as one: each category's median and the
    overall's, and their spread (max - min; the overall's, under `overall`,
    is the spread of the reviewers' own overalls). A value that is not a
    number is not counted; a category no sample scored is left out, which
    the bar reads as 0.

    A category in `unheard` (no current listening review, AUTO-105) is
    None in every sample, whatever each reviewer entered, so it gets no
    median and no spread: a median of the zeros the prompt asks for would
    be a score nobody heard. The overall's spread is then the spread of
    each sample's mean over the categories scored, the number the overall
    becomes (`score_unheard`), not of overalls that averaged those zeros
    in."""
    scores, spread = {}, {}
    unheard = set(unheard or ())

    def values(get) -> list[float]:
        return [v for v in (_number(get(c)) for c in cards) if v is not None]
    for key in CATEGORIES:
        if key in unheard:
            scores[key] = None
            continue
        got = values(lambda c: (c.get("scores") or {}).get(key))
        if got:
            scores[key] = round(statistics.median(got), 2)
            spread[key] = round(max(got) - min(got), 2)
    got = values(lambda c: c.get("overall"))
    overall = round(statistics.median(got), 2) if got else None
    if unheard:
        got = [_mean(dict(c.get("scores") or {}) | dict.fromkeys(unheard)) for c in cards]
    if got:
        spread["overall"] = round(max(got) - min(got), 2)
    return scores, overall, spread


#: The reviewer's grouping of the samples' findings into defects: the
#: improve loop's test of "the same problem" (the same thing visibly wrong
#: at about the same place, however differently it is worded), asked of a
#: model because wording cannot be matched by rule (invariant 3).
MERGE_SCHEMA = {
    "type": "object",
    "properties": {"groups": {"type": "array", "items": {
        "type": "object",
        "properties": {"findings": {"type": "array", "items": {"type": "string"},
                                    "description": "the ids of findings that are one defect"},
                       "why": {"type": "string"}},
        "required": ["findings"]}}},
    "required": ["groups"],
}


def _finding_rows(samples: list[dict]) -> dict[str, tuple[int, dict]]:
    """Every finding of the samples by its id, `s<k>.F<n>`, then each
    sample's `blocking` claims, `s<k>.B<n>`, in sample order. A claim is the
    reviewer's free-text item, held as a finding with only a `problem`,
    severity blocking and `claim` set, so one merge decides both."""
    out = {}
    for row in samples:
        for n, f in enumerate(row.get("findings") or [], 1):
            if isinstance(f, dict):
                out[f"s{row['k']}.F{n}"] = (int(row["k"]), f)
        for n, b in enumerate(row.get("blocking") or [], 1):
            if str(b).strip():
                out[f"s{row['k']}.B{n}"] = (int(row["k"]), {
                    "problem": str(b), "severity": "blocking", "claim": True})
    return out


def merge_prompt(samples: list[dict]) -> str:
    """What the merge decision reads: each sample's findings under its id."""
    rows = [{"id": fid, "sample": k, "kind": "blocking claim" if f.get("claim") else "finding",
             "timestamp": f.get("timestamp"),
             "problem": str(f.get("problem", ""))[:400], "owner": f.get("owner")}
            for fid, (k, f) in _finding_rows(samples).items()]
    return "\n\n".join([
        f"{len(samples)} independent readings of the same QA evidence each filed "
        "findings (`F` ids) and listed what they call blocking (`B` ids). Group "
        "the items that are the same problem -- the same thing visibly wrong at "
        "about the same place, however differently it is worded; a blocking claim "
        "goes in the group of the finding it names, its own reading's included. "
        "Each id belongs to at most one group; leave an item no other reading "
        "reports out of every group. Merge nothing that is a different problem.",
        brief("Findings and blocking claims, by reading", rows)])


def merge_findings(samples: list[dict], groups) -> tuple[list[dict], list[dict]]:
    """The samples' findings as defects: (kept, low_confidence).

    `samples` are rows with `k` and `findings`; `groups` the merge
    decision's lists of ids. An id it does not know, or one an earlier
    group took, is ignored; what no group holds is a group of one. A group
    two or more samples report is kept, worded as the earliest sample
    worded it (its earliest finding, when it holds one), with `samples`
    naming them; it is blocking only when two or more of them called it
    blocking, by severity or by a claim, since one reading's severity is the
    noise this removes. A kept group of claims alone stays a claim (`claim`
    set): the caller lists it under `blocking`, not as a finding to route.
    A group of one sample (two items of one reading are still one reading)
    is low-confidence: listed, not routed, blocking nothing."""
    by_id = _finding_rows(samples)
    rank = {fid: i for i, fid in enumerate(by_id)}
    taken: set[str] = set()
    clusters: list[list[str]] = []
    for group in groups or []:
        ids = sorted({str(i) for i in group or [] if str(i) in by_id and str(i) not in taken},
                     key=rank.get)
        if ids:
            taken.update(ids)
            clusters.append(ids)
    clusters += [[fid] for fid in by_id if fid not in taken]
    kept, low = [], []
    for ids in clusters:
        members = [by_id[i] for i in ids]
        ks = sorted({k for k, _ in members})
        head = next((f for _, f in members if not f.get("claim")), members[0][1])
        out = dict(head, samples=ks)
        if len(ks) >= 2:
            votes = {k for k, f in members if f.get("severity") == "blocking"}
            out["severity"] = "blocking" if len(votes) >= 2 else "minor"
            kept.append(out)
        else:
            low.append(dict(out, confidence="low"))
    when = lambda f: _number(f.get("timestamp")) or 0.0  # noqa: E731
    return sorted(kept, key=when), sorted(low, key=when)


def pass_usd(card: dict | None) -> float | None:
    """What a pass of QA_SAMPLES samples costs, from one that was scored: its
    cost per sample asked, times QA_SAMPLES. A pass from before the samples
    (no `scoring`) asked one. The side-by-side comparison (AUTO-108) is
    asked once a pass, not per sample, so what it cost is added once, not
    scaled; a pass without one is priced without it, and the estimate adds
    spend.COMPARE_PASS_USD where QA is told the pass to compare with. The
    video comparison's decisions (`compare_engine`, AUTO-128) are likewise
    added once. A `--listen` (AUTO-126), a `--watch` (AUTO-127,
    `watch_engine`) and a `--recheck` (AUTO-106, `<pass>-recheck`) are left
    out: the card's usage holds what they cost, but a plain `yt qa` and
    `yt improve` neither listen, watch nor recheck, and spend.FLAG_USD adds
    each one's own estimate whenever its flag is asked. None when the card
    holds no cost."""
    cost = spend.cost_in(card)
    if cost is None:
        return None
    heard = card.get("listen") if isinstance(card, dict) else None
    if isinstance(heard, dict) and isinstance(heard.get("engine"), dict):
        cost = max(cost - float(heard["engine"].get("cost_usd") or 0), 0.0)
    seen = card.get("watch_engine") if isinstance(card, dict) else None
    if isinstance(seen, dict):
        cost = max(cost - float(seen.get("cost_usd") or 0), 0.0)
    scoring = card.get("scoring") if isinstance(card, dict) else None
    try:
        asked = max(1, int((scoring or {}).get("asked") or 1))
    except (TypeError, ValueError):
        asked = 1
    engine = card.get("engine") if isinstance(card, dict) else None
    rows = [d for d in (engine.get("decisions") if isinstance(engine, dict) else None) or []
            if isinstance(d, dict) and not d.get("cached")]
    rechecked = sum(float(d.get("cost_usd") or 0) for d in rows
                    if str(d.get("name", "")).endswith(RECHECK_SUFFIX))
    cost = max(cost - rechecked, 0.0)
    once = sum(float(d.get("cost_usd") or 0) for d in rows
               if str(d.get("name", "")).endswith("-compare-pass"))
    # The video comparison (AUTO-128) is asked once a pass too, on its own
    # harness: every decision there is one.
    video = card.get("compare_engine") if isinstance(card, dict) else None
    once += sum(float(d.get("cost_usd") or 0)
                for d in (video.get("decisions") if isinstance(video, dict) else None) or []
                if isinstance(d, dict) and not d.get("cached"))
    once = min(max(once, 0.0), cost)
    return round((cost - once) / asked * QA_SAMPLES + once, 2)


# --------------------------------------------------------------------------- #
# sampling
# --------------------------------------------------------------------------- #

def super_fields(shot: dict) -> dict:
    """What a reviewer sees of a shot's super: the words, and the direction apart."""
    return {"super": visible_text(Shot.from_raw(shot)) if shot else "",
            "direction": shot.get("on_screen", "")}


def frame_name(t: float) -> str:
    ms = int(round(float(t) * 1000))
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"t_{m:02d}m{s:02d}s{ms:03d}.png"


def _mid(shot: dict) -> float:
    return round(float(shot["at"]) + float(shot["seconds"]) / 2, 3)


def _stamp(t) -> str:
    """mm:ss.mmm, the way a finding is read."""
    try:
        ms = int(round(float(t) * 1000))
    except (TypeError, ValueError):
        return "?"
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{m:02d}:{s:02d}.{ms:03d}"


def _fps(build: dict) -> float:
    try:
        return float((build.get("scene") or {}).get("fps") or 30) or 30.0
    except (TypeError, ValueError):
        return 30.0


def burst_plan(t: float, *, fps: float, seconds: float | None = None,
               span: float = BURST_SECONDS, limit: int = BURST_MAX_FRAMES,
               start: float | None = None) -> dict:
    """Which frames evidence the motion around one moment: one contiguous
    window of `span` seconds (at most `limit` frames) centred on its frame,
    kept inside the shot (`start`, `seconds`; without `start`, `t` is the
    shot's midpoint) so a step between two of them is never a real cut.
    `range` is the engine's `A..B` (end exclusive) and `still_frame` the
    moment's frame inside it. Deterministic: the same moment and rate plan
    the same window."""
    fps = float(fps) or 30.0
    mid = max(int(round(float(t) * fps)), 0)
    count = max(min(int(round(span * fps)), int(limit)), 1)
    first, end = max(mid - count // 2, 0), None
    if seconds is not None and float(seconds) > 0:
        begin = float(start) if start is not None else float(t) - float(seconds) / 2
        shot_first = max(int(round(begin * fps)), 0)
        shot_end = max(shot_first + int(float(seconds) * fps), shot_first + 1)
        count = min(count, shot_end - shot_first)
        first = min(max(mid - count // 2, shot_first), max(shot_end - count, shot_first))
        end = min(first + count, shot_end)
    end = end if end is not None else first + count
    frames = list(range(first, max(end, first + 1)))
    return {"frames": frames, "times": [round(f / fps, 3) for f in frames],
            "span": round(len(frames) / fps, 3), "fps": fps,
            "range": f"{frames[0]}..{frames[-1] + 1}",
            "still_frame": mid if frames[0] <= mid <= frames[-1] else None}


def _burst(t: float, *, fps: float, seconds: float | None, frame: str,
           start: float | None = None) -> dict:
    """The plan plus where each frame lands: `burst/<still stem>_<index>.png`,
    the engine's own `%04d` naming, so the files are known before rendering."""
    plan = burst_plan(t, fps=fps, seconds=seconds, start=start)
    stem = frame[:-4] if frame.endswith(".png") else frame
    plan["pattern"] = f"burst/{stem}_%04d.png"
    plan["files"] = [f"burst/{stem}_{f:04d}.png" for f in plan["frames"]]
    return plan


#: The reasons a single mechanical defect blocks on: the opening, the last
#: shot and the shot that pays the promise. A re-hook is sampled, not an edge.
EDGE_REASONS = ("hook", "ending", "beat: payoff")


#: An extra still (AUTO-103) is this far from every other sample, so two
#: stills never show the same moment, and is labelled this way in `why`.
SPREAD_GAP, SPREAD_WHY = 0.5, "evenly spaced"
#: A shot no priority reason names is still sampled, under this reason.
SHOT_WHY = "shot"
#: Without --frames every shot is sampled, up to this many stills. One
#: inspection reads every still and the evidence beside each (about 2 KB a
#: row), and each still is a render job; past this a film is sampled as if
#: --frames said it, the priority shots first and the rest spread over the
#: runtime, and the shots left out are named. --frames N raises it.
EVERY_SHOT_UP_TO = 60


def _priority(shots: list[dict]) -> list[tuple[dict, str]]:
    """The shots docs/production.md §7 names, most important first: the
    hook, the ending, the payoff, each section's start, the re-hooks, the
    shots at 25/50/75 % of the runtime and the three longest effects. A
    shot can be named more than once; each name is a reason in `why`."""
    runtime = max(float(s["at"]) + float(s["seconds"]) for s in shots)

    def containing(t: float) -> dict:
        for s in shots:
            if float(s["at"]) <= t < float(s["at"]) + float(s["seconds"]):
                return s
        return shots[-1]

    wanted: list[tuple[dict, str]] = [(shots[0], "hook"), (shots[-1], "ending")]
    # The screenplay's own beats (AUTO-90: build.json carries each shot's
    # `function`) sit beside the positional rows rather than instead of them,
    # so a mislabelled payoff cannot drop the film's real ending. The payoff
    # is an edge, so it comes with the ending; the re-hooks, which nothing
    # caps, come after the section starts so they cannot crowd them out.
    beats = lambda name: [(s, f"beat: {name}") for s in shots  # noqa: E731
                          if s.get("function") == name]
    wanted += beats("payoff")
    seen_sections = set()
    for s in shots:
        if s.get("section") and s["section"] not in seen_sections:
            seen_sections.add(s["section"])
            wanted.append((s, f"section start: {s['section']}"))
    wanted += beats("rehook")
    for pct in (25, 50, 75):
        wanted.append((containing(runtime * pct / 100), f"{pct}% of runtime"))
    effects = sorted((s for s in shots if s.get("asset") == "effect"),
                     key=lambda s: -float(s["seconds"]))
    for s in effects[:3]:
        wanted.append((s, "longest authored effect"))
    return wanted


def _spread_rest(shots: list[dict], named: list[dict]) -> list[dict]:
    """The shots no priority reason names, in the order a tight --frames
    keeps them: each next the one farthest from every shot already in line,
    so what a ceiling leaves out is spread over the runtime rather than all
    taken from its end. Ties go to the earlier shot; deterministic."""
    chosen = [_mid(s) for s in named]
    rest = [s for s in shots if not any(s is n for n in named)]
    # each remaining shot's distance to the nearest one in line, kept current
    near = [min((abs(_mid(s) - t) for t in chosen), default=0.0) for s in rest]
    order = []
    while rest:
        i = max(range(len(rest)), key=lambda k: (near[k], -float(rest[k]["at"])))
        far = rest.pop(i)
        near.pop(i)
        order.append(far)
        t = _mid(far)
        near = [min(d, abs(_mid(s) - t)) for d, s in zip(near, rest)]
    return order


def _spread_pool(shots: list[dict], taken: list[float]) -> list[tuple[float, dict]]:
    """Every moment an extra still may take, with the shot it belongs to,
    in time order: inside a shot and half a burst from its edges (so the
    burst is the shot's own), never in a gap between takes, at least
    SPREAD_GAP from every taken sample and from each other. The candidates
    are each shot's two ends and the steps of SPREAD_GAP either side of its
    midpoint; kept earliest first, which keeps the most that fit. The pool
    does not depend on how many are asked for, so more --frames never give
    fewer stills."""
    candidates: list[tuple[float, int, dict]] = []
    for i, shot in enumerate(shots):
        at, seconds = float(shot["at"]), float(shot["seconds"])
        if seconds <= 0:
            continue
        edge = min(BURST_SECONDS / 2, seconds / 2)
        lo, hi, mid = at + edge, at + seconds - edge, at + seconds / 2
        points = {round(lo, 3), round(hi, 3)}
        k = 1
        while mid - k * SPREAD_GAP >= lo or mid + k * SPREAD_GAP <= hi:
            for t in (mid - k * SPREAD_GAP, mid + k * SPREAD_GAP):
                if lo <= t <= hi:
                    points.add(round(t, 3))
            k += 1
        candidates += [(t, i, shot) for t in points]
    candidates.sort(key=lambda c: (c[0], c[1]))
    near = sorted(taken)
    gap = SPREAD_GAP - 1e-6             # timestamps are rounded to the millisecond
    pool: list[tuple[float, dict]] = []
    for t, _, shot in candidates:
        j = bisect.bisect_left(near, t)
        if any(abs(t - near[k]) < gap for k in (j - 1, j) if 0 <= k < len(near)):
            continue
        if pool and t - pool[-1][0] < gap:
            continue
        pool.append((t, shot))
    return pool


def _spread_times(shots: list[dict], taken: list[float],
                  count: int) -> list[tuple[float, dict]]:
    """`count` more moments, evenly spaced over the runtime: for each of
    `count` even steps, the moment of the pool (`_spread_pool`) nearest to
    it, in order, each later than the one before. A step that falls in a
    gap between takes or on a sample takes the nearest moment that is
    free. A film too short for `count` more gets every moment that fits."""
    if count <= 0 or not shots:
        return []
    pool = _spread_pool(shots, taken)
    if count >= len(pool):
        return pool
    runtime = max(float(s["at"]) + float(s["seconds"]) for s in shots)
    return [pool[k] for k in _even_steps([t for t, _ in pool], count, runtime)]


def _even_steps(times: list[float], count: int, runtime: float) -> list[int]:
    """The indices of `count` of the sorted `times`, one nearest each of
    `count` even steps over `runtime`, in order, each later than the one
    before: what `_spread_times` takes from its pool, and what a fixture
    keeps of a pass's samples (`fixture_samples`). `count` < len(times)."""
    out: list[int] = []
    lo = 0
    for j in range(count):
        target = runtime * (j + 0.5) / count
        hi = len(times) - (count - j)       # leave one for every step still to come
        k = bisect.bisect_left(times, target, lo, hi + 1)
        if k > hi or (k > lo and target - times[k - 1] <= times[k] - target):
            k -= 1
        out.append(k)
        lo = k + 1
    return out


#: A narrated line (AUTO-104) is one sentence of a take: its still is taken
#: where its first word is said, labelled `line: ` and its first LINE_WORDS
#: words. The screenplay's functions whose lines a tight --frames keeps
#: first, in this order: the moments the film is built to land.
LINE_WHY, LINE_WORDS = "line: ", 5
KEY_LINES = ("payoff", "rehook", "reveal")
#: What ends a sentence, once closing quotes and brackets are set aside.
_SENTENCE_END = (".", "!", "?", "\u2026")
_CLOSERS = "\"')]}\u201d\u2019\u00bb"
#: A word that ends on a period without ending its sentence: a title before
#: a name, and an initialism or initial (U.S., e.g., J.), which the pattern
#: below catches. Numbers ("in 1923.") still end one.
_TITLES = frozenset(("mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "jr.", "sr.",
                     "vs.", "mt.", "gen.", "col.", "capt.", "lt.", "sgt."))
_INITIALS = re.compile(r"^(?:[^\W\d_]\.)+$")


def _ends_sentence(word: str) -> bool:
    word = word.rstrip(_CLOSERS)
    if not word.endswith(_SENTENCE_END):
        return False
    if word.endswith(".") and not word.endswith(".."):
        bare = word.lstrip("\"'([{\u201c\u2018\u00ab")
        if bare.lower() in _TITLES or _INITIALS.match(bare):
            return False
    return True


def _sentences(take: dict) -> list[tuple[float, str]]:
    """A take's sentences, each as (its first word's start in the take,
    its words). The take's own word timings when it has them; without
    them its length is shared by the words' lengths, as `yt voice` does
    for a local engine. A word is a sentence's last when it ends on . ! ?
    or an ellipsis, a closing quote or bracket aside, unless it is a title
    (Dr., Mr.) or an initialism (U.S.): so "Dr. Lee left" is one line. The
    cost is the other way round: a sentence that ends on an initialism runs
    on into the next, whose start then has no still of its own."""
    words = [w for w in take.get("words") or [] if isinstance(w, dict)]
    try:
        timed = [(float(w["start"]), str(w.get("text", ""))) for w in words]
    except (KeyError, TypeError, ValueError):
        timed = []
    if not timed:
        try:
            seconds = float(take.get("seconds") or 0)
        except (TypeError, ValueError):
            seconds = 0.0
        timed = [(w.start, w.text) for w in words_estimated(str(take.get("text", "")), seconds)]
    out: list[tuple[float, str]] = []
    start, said = None, []
    for at, text in timed:
        if not text.strip():
            continue
        if start is None:
            start = at
        said.append(text.strip())
        if _ends_sentence(text.strip()):
            out.append((start, " ".join(said)))
            start, said = None, []
    if said:
        out.append((start, " ".join(said)))
    return out


def narrated_lines(build: dict, voice: dict | None) -> list[dict]:
    """Every narrated line on the film (AUTO-104), in time order: each
    sentence of each take in voice.json, placed on the film by build.json's
    shot `at` for the take's label (a take's word times count from its own
    start, which is its shot's), with `t` the moment its still is taken:
    the line's start, kept half a burst inside the shot so the burst is the
    shot's own and never straddles a cut. A line that starts outside its
    shot, a take no shot carries, a bracketed direction and a wordless film
    have none. Each line carries its shot, the shot's `function`, and
    `first`: whether it opens its take. Two takes with one label are the
    last one, as build.timeline places them: the other is not on the
    soundtrack."""
    if not voice or voice.get("wordless"):
        return []
    shots: dict[str, dict] = {}
    for shot in (build or {}).get("shots") or []:
        if isinstance(shot, dict):
            shots.setdefault(str(shot.get("label", "")), shot)
    takes = {str(take.get("label", "")): take for take in voice.get("takes") or []
             if isinstance(take, dict)}
    out = []
    for label, take in takes.items():
        shot = shots.get(label)
        if not shot:
            continue
        at, seconds = float(shot["at"]), float(shot.get("seconds") or 0)
        if seconds <= 0:
            continue
        edge = min(BURST_SECONDS / 2, seconds / 2)
        for n, (start, text) in enumerate(_sentences(take)):
            text = spoken(text)
            when = round(at + start, 3)
            if not text or not at <= when < at + seconds:
                continue
            out.append({"shot": shot, "at": when, "text": text,
                        "function": str(shot.get("function") or ""), "first": n == 0,
                        "t": round(min(max(when, at + edge), at + seconds - edge), 3)})
    return sorted(out, key=lambda line: (line["at"], line["text"]))


def _line_why(text: str) -> str:
    """`line: ` and the line's first LINE_WORDS words. A `;` is a comma
    here: `why` is a list of reasons joined by "; "."""
    words = text.replace(";", ",").split()
    head = " ".join(words[:LINE_WORDS]) + (" ..." if len(words) > LINE_WORDS else "")
    return LINE_WHY + head


def sample(build: dict, *, limit: int | None = None,
           voice: dict | None = None, watched: list[dict] | None = None,
           builds=frozenset()) -> list[dict]:
    """Where the evidence is taken, per docs/production.md §7: every shot,
    at its midpoint (AUTO-103), each row's `why` naming the priority
    reasons that shot has (hook, ending, payoff, section start, re-hook,
    25/50/75 % of the runtime, longest effect) or `shot` when it has none.

    `limit` is how many stills: None is one per shot, up to
    EVERY_SHOT_UP_TO. Above the shot count the extra stills (`why`: evenly
    spaced) are spread over the runtime (`_spread_times`); below it the
    priority order decides what is kept (the named shots first, then the
    others spread over the runtime) and `unsampled` says which shots were
    left out. A row is one shot's: two shots with the same midpoint get a
    row each, the second moved a frame inside itself. Each row carries its
    shot's `at`, which with `label` says which shot it is, and its burst
    plan, the frames that evidence motion, kept inside its shot.

    With `voice` (voice.json), each narrated line also gets a still where
    it starts (AUTO-104, `narrated_lines`), so a picture is judged at the
    moment its line is said: `why` is `line: <first words>` and `lines`
    carries the line, when it starts and the frame that shows that moment.
    A line whose start is a frame of the burst of a still of its own shot
    merges into it (its reason and its line added there, no new still); a
    still a few frames off still shows the moment, one half a second off
    does not. The line stills come after every shot's still, so they never
    cost a shot its only one; `_add_lines` says which lines come first.
    Without --frames they are added up to EVERY_SHOT_UP_TO stills in all;
    under --frames they count against it, before the evenly spaced ones.
    `lines_unsampled` says which lines got no still.

    With `watched` (AUTO-127: watch.candidates, each {id, t, severity,
    problem}), each watched moment gets a still too, after everything
    above and beyond `limit`: one within WATCHED_NEAR seconds of a still
    merges into it at no cost, the others are added up to
    WATCH_SAMPLES_MAX, blocking ones first, then in time order. `why` is
    `watched: <id> <first words>` and `watched` lists the ids a row shows.

    With `builds` (AUTO-145: `build_shots`, label to effect id), each such
    shot gets a single frame where it opens and one where it closes, last
    of all and beyond `limit` (`_add_builds`): `why` is `build: first` or
    `build: last` and `builds` carries which, when, the frame and the
    effect. Such a still has no burst and is never its shot's home."""
    shots = sorted(build.get("shots", []), key=lambda s: float(s["at"]))
    if not shots:
        return []
    fps = _fps(build)

    def row(shot: dict, t: float, why: str) -> dict:
        seconds = float(shot.get("seconds") or 0)
        return {"t": t, "why": why, "label": shot.get("label", ""),
                "at": float(shot["at"]), "section": shot.get("section", ""),
                "asset": shot.get("asset", ""), "frame": frame_name(t),
                "seconds": seconds,
                "burst": _burst(t, fps=fps, seconds=seconds or None,
                                frame=frame_name(t), start=float(shot["at"]))}

    wanted = _priority(shots)
    named: list[dict] = []
    for shot, _ in wanted:
        if not any(shot is n for n in named):
            named.append(shot)
    wanted += [(s, SHOT_WHY) for s in _spread_rest(shots, named)]

    cap = limit
    if cap is None and len(shots) > EVERY_SHOT_UP_TO:
        cap = EVERY_SHOT_UP_TO
    out: list[dict] = []
    # One row per shot, by the shot itself, not by its midpoint: two shots
    # closer than half a second, or nested with the same midpoint, are two
    # rows, so neither hides the other.
    by_shot: dict[int, dict] = {}
    for shot, why in wanted:
        hit = by_shot.get(id(shot))
        if hit:
            # whole reasons, not substrings: "hook" is inside "beat: rehook"
            if why not in hit["why"].split("; "):
                hit["why"] += f"; {why}"
            continue
        if cap is not None and len(out) >= cap:
            continue
        t = _free_moment(shot, {r["t"] for r in out}, fps)
        by_shot[id(shot)] = row(shot, t, why)
        out.append(by_shot[id(shot)])
    _add_lines(out, narrated_lines(build, voice), row,
               cap=limit if limit is not None else EVERY_SHOT_UP_TO, fps=fps)
    if limit is not None and limit > len(out):
        for t, shot in _spread_times(shots, [r["t"] for r in out], limit - len(out)):
            out.append(row(shot, t, SPREAD_WHY))
    if watched:
        _add_watched(out, shots, watched, row, fps=fps)
    if builds:
        _add_builds(out, shots, builds, row, fps=fps)
    return sorted(out, key=lambda r: r["t"])


def _add_builds(out: list[dict], shots: list[dict], builds, row, *, fps: float) -> None:
    """The build stills (AUTO-145), added to `out` in place: for each shot
    `builds` names, longest first, a frame BURST_SECONDS/2 after its cut and
    one BURST_SECONDS/2 before its end, where the burst of a cut does not
    reach. A still that is a frame of a burst of its own shot's row merges
    into that row at no cost; the others are new rows with no burst, up to
    BUILD_SAMPLES_MAX in all. A shot whose new stills would not fit gets
    none, so `builds_unsampled` names it whole."""
    by_label: dict[str, dict] = {}
    for shot in shots:
        by_label.setdefault(str(shot.get("label", "")), shot)
    wanted = [by_label[label] for label in builds if label in by_label]
    added = 0
    for shot in sorted(wanted, key=lambda s: (-float(s.get("seconds") or 0), float(s["at"]))):
        label, at = str(shot.get("label", "")), float(shot["at"])
        seconds = float(shot.get("seconds") or 0)
        effect = builds.get(label, "") if isinstance(builds, dict) else ""
        plan = []
        for which, t in (("first", round(at + BURST_SECONDS / 2, 3)),
                         ("last", round(at + seconds - BURST_SECONDS / 2, 3))):
            frame = max(int(round(t * fps)), 0)
            holders = [r for r in out if r["label"] == label and r["at"] == at
                       and frame in (r.get("burst") or {}).get("frames", ())]
            plan.append((which, t, frame, min(holders, key=lambda r: abs(r["t"] - t),
                                              default=None)))
        if added + sum(1 for *_, hit in plan if hit is None) > BUILD_SAMPLES_MAX:
            continue
        for which, t, frame, hit in plan:
            why = f"{BUILD_WHY}{which}"
            if hit is not None:
                burst = hit["burst"]
                frames, files = burst.get("frames") or [], burst.get("files") or []
                name = hit["frame"]
                if frame != burst.get("still_frame") and len(files) == len(frames):
                    name = files[frames.index(frame)]
                if why not in hit["why"].split("; "):
                    hit["why"] = f"{hit['why']}; {why}" if hit["why"] else why
                hit.setdefault("builds", []).append(
                    {"which": which, "t": t, "frame": name, "effect": effect})
                continue
            used = {r["t"] for r in out}
            if t in used:
                # another still has this millisecond: the nearest free frame
                # inside the shot
                t = next((m for k in range(1, int(seconds * fps) + 1)
                          for m in (round(t - k / fps, 3), round(t + k / fps, 3))
                          if at < m < at + seconds and m not in used), t)
            new = row(shot, t, why)
            new.pop("burst", None)
            new["frame"] = f"{BUILD_DIR}/{new['frame']}"
            new["builds"] = [{"which": which, "t": t, "frame": new["frame"],
                              "effect": effect}]
            out.append(new)
            added += 1


def build_shots(vfx: dict | None, build: dict | None) -> dict[str, str]:
    """The shots that get build stills (AUTO-145), as label to effect id in
    film order: for each effect of a BUILD_TYPES type that vfx.json keeps
    (not under `scene.dropped`), its earliest shot, drawn by an effect and
    lasting BUILD_MIN_SECONDS. The effect's later shots continue its take
    and open on what it built, and so does a shot extended from another;
    neither is judged. A shot that runs on in an earlier shot's include is
    judged all the same: one world per section folds every shot of the
    section into the include of its first (build.py), so a diagram that is
    the world's second phase opens no include of its own, yet it is still
    the first shot of its effect, and the ink series measures growth from
    whatever the earlier phase left on screen. Empty with no vfx.json."""
    if not isinstance(vfx, dict) or not isinstance(build, dict):
        return {}
    shots = sorted(build.get("shots") or [], key=lambda s: float(s.get("at") or 0))
    order: dict[str, int] = {}
    for n, shot in enumerate(shots):
        order.setdefault(str(shot.get("label", "")), n)
    by_label = {str(s.get("label", "")): s for s in reversed(shots)}
    dropped = set((vfx.get("scene") or {}).get("dropped") or {})
    out: dict[str, str] = {}
    for section in vfx.get("sections") or []:
        for effect in (section or {}).get("effects") or []:
            if effect.get("type") not in BUILD_TYPES or effect.get("id") in dropped:
                continue
            labels = [str(x) for x in effect.get("shots") or [] if str(x) in by_label]
            if not labels:
                continue
            label = min(labels, key=order.__getitem__)
            shot = by_label[label]
            if (shot.get("asset") != "effect" or not shot.get("include")
                    or "(extended from" in str(shot.get("file") or "")
                    or float(shot.get("seconds") or 0) < BUILD_MIN_SECONDS):
                continue
            out.setdefault(label, str(effect.get("id") or ""))
    return dict(sorted(out.items(), key=lambda kv: order[kv[0]]))


def _build_series(samples: list[dict]) -> dict[str, dict]:
    """Per shot with build stills (AUTO-145): its effect, its readings in
    time order (the first build still, each narrated line said between the
    two, the last) and the row that holds the first."""
    out: dict[str, dict] = {}
    for row in samples:
        label = str(row.get("label", ""))
        for entry in row.get("builds") or []:
            if not isinstance(entry, dict):
                continue
            rec = out.setdefault(label, {"effect": entry.get("effect", ""), "points": [],
                                         "first": None, "row": None})
            rec[entry.get("which")] = entry
            if entry.get("which") == "first":
                rec["row"] = row
    for label, rec in out.items():
        first, last = rec.get("first"), rec.get("last")
        if not first or not last:
            continue
        lines = [ln for row in samples if str(row.get("label", "")) == label
                 for ln in row.get("lines") or []
                 if isinstance(ln, dict) and first["t"] < float(ln.get("at", -1)) < last["t"]]
        points = [(first["t"], first)] + sorted(
            ((float(ln["at"]), ln) for ln in lines), key=lambda p: p[0]) + [(last["t"], last)]
        rec["points"] = points
    return out


def builds_unsampled(builds, samples: list[dict]) -> list[str]:
    """The shots `builds` names that got no build stills (AUTO-145): past
    BUILD_SAMPLES_MAX, or in a pass sampled before them; in film order."""
    have = {label for label, rec in _build_series(samples).items() if rec["points"]}
    return [label for label in builds if label not in have]


def note_builds(card: dict, builds, samples: list[dict], *, reused: bool = False) -> dict:
    """The scorecard says which diagram shots were not checked for a build
    (AUTO-145): a not_evidenced line for the shots with no build stills and
    one for the shots whose stills have no ink reading. A film with no
    build shot is left as it is."""
    if not builds:
        return card
    parts = []
    missing = builds_unsampled(builds, samples)
    if missing:
        under = ("in the reused pass" if reused else
                 f"past the ceiling of {BUILD_SAMPLES_MAX} build stills")
        parts.append(f"{len(missing)} of {len(builds)} diagram shots have no build "
                     f"stills {under}: {', '.join(missing)}")
    unread = [label for label, rec in _build_series(samples).items()
              if rec["points"] and any(not isinstance(e.get("ink"), (int, float))
                                       for _, e in rec["points"])]
    if unread:
        parts.append(f"the build stills of {', '.join(unread)} have no ink reading "
                     "(a frame was not rendered or could not be read)")
    if parts:
        card = dict(card, not_evidenced=list(card.get("not_evidenced") or []) + [
            "builds: " + "; ".join(parts) + "; whether those shots build in steps "
            "was not measured"])
    return card


#: The most stills a watch adds to a pass (AUTO-127), how near a still a
#: watched moment merges into it, and the reason such a still is taken.
WATCH_SAMPLES_MAX = 12
WATCHED_NEAR = 0.5
WATCHED_WHY = "watched: "


def _add_watched(out: list[dict], shots: list[dict], watched: list[dict], row, *,
                 fps: float) -> None:
    """The watched moments' stills (AUTO-127), added to `out` in place. A
    moment is held to the film, from 0 to its last frame: the watcher's
    times may run SLACK past its segment's end, and a still asked for past
    the end cannot be rendered, so its finding could never be confirmed."""
    added = 0
    last = max(float(s["at"]) + float(s.get("seconds") or 0) for s in shots) - 1 / fps
    for w in sorted(watched, key=lambda w: (w.get("severity") != "blocking", float(w["t"]))):
        t = round(min(max(float(w["t"]), 0.0), max(last, 0.0)), 3)
        words = str(w.get("problem") or "").split()
        why = (f"{WATCHED_WHY}{w['id']} " + " ".join(words[:LINE_WORDS])
               + (" ..." if len(words) > LINE_WORDS else "")).strip()
        near = min(out, key=lambda r: abs(r["t"] - t), default=None)
        if near is not None and abs(near["t"] - t) <= WATCHED_NEAR:
            if why not in near["why"].split("; "):
                near["why"] = f"{near['why']}; {why}" if near["why"] else why
            near.setdefault("watched", []).append(w["id"])
            continue
        if added >= WATCH_SAMPLES_MAX:
            continue
        inside = [s for s in shots if float(s["at"]) <= t < float(s["at"]) + float(
            s.get("seconds") or 0)]
        shot = inside[-1] if inside else max(
            (s for s in shots if float(s["at"]) <= t), key=lambda s: float(s["at"]),
            default=shots[0])
        new = row(shot, t, why)
        new["watched"] = [w["id"]]
        out.append(new)
        added += 1


def _add_lines(out: list[dict], lines: list[dict], row, *, cap: int,
               fps: float) -> None:
    """The line stills (AUTO-104), added to `out` in place.

    A line whose start is a frame of the burst of a still of its own shot
    is shown by that still: it merges into it at no cost, whatever the cap.
    Each other line needs a still of its own, taken while `out` is under
    `cap`, in this order: the payoff, re-hook and reveal lines (KEY_LINES,
    in that order, each in time order); then the lines said inside a shot
    before the ones that open their take, because a take opens on its
    shot's cut, so its first line is said as the shot begins, which the
    shot's own still stands for, while a later line marks something that
    happens in the shot; and within each, the line farthest from every
    still that already shows a line, so what the cap leaves out is spread
    over the runtime instead of all taken from its end (ties: the line
    farthest from its shot's stills, then the earlier). A line is never
    merged into another shot's still: the line is its own shot's."""
    rank = {name: n for n, name in enumerate(KEY_LINES)}

    def own(line: dict) -> list[dict]:
        shot = line["shot"]
        return [r for r in out if r["label"] == shot.get("label", "")
                and r["at"] == float(shot["at"])]

    def holder(line: dict) -> dict | None:
        start = max(int(round(line["at"] * fps)), 0)
        rows = [r for r in own(line) if start in (r.get("burst") or {}).get("frames", ())]
        return min(rows, key=lambda r: abs(r["t"] - line["at"]), default=None)

    def attach(r: dict, line: dict) -> None:
        why = _line_why(line["text"])
        if why not in r["why"].split("; "):
            r["why"] = f"{r['why']}; {why}" if r["why"] else why
        burst, start = r.get("burst") or {}, max(int(round(line["at"] * fps)), 0)
        frames, files = burst.get("frames") or [], burst.get("files") or []
        frame = r["frame"]
        if start != burst.get("still_frame") and start in frames and len(files) == len(frames):
            frame = files[frames.index(start)]
        r.setdefault("lines", []).append({"at": line["at"], "text": line["text"],
                                          "frame": frame})

    def after(line: dict) -> tuple:
        shown = [r["t"] for r in out if r.get("lines")]
        return (min((abs(line["t"] - t) for t in shown), default=math.inf),
                min((abs(line["t"] - r["t"]) for r in own(line)), default=math.inf),
                -line["at"])

    pending = list(lines)
    while pending:
        unseen = []
        for line in pending:
            hit = holder(line)
            if hit:
                attach(hit, line)
            else:
                unseen.append(line)
        pending = unseen
        if not pending or len(out) >= cap:
            return
        key = [line for line in pending if line["function"] in rank]
        if key:
            line = min(key, key=lambda ln: (rank[ln["function"]], ln["at"]))
        else:
            inner = [ln for ln in pending if not ln.get("first")] or pending
            line = max(inner, key=after)
        pending.remove(line)
        shot, t, used = line["shot"], line["t"], {r["t"] for r in out}
        if t in used:
            # another shot's still has this millisecond (nested shots): the
            # nearest frame inside the line's shot that none has
            at, seconds = float(shot["at"]), float(shot["seconds"])
            t = next((m for k in range(1, int(seconds * fps) + 1)
                      for m in (round(t + k / fps, 3), round(t - k / fps, 3))
                      if at < m < at + seconds and m not in used), t)
        new = row(shot, t, "")
        attach(new, line)
        out.append(new)


def lines_unsampled(build: dict, voice: dict | None, samples: list[dict]) -> list[dict]:
    """The narrated lines (AUTO-104) no sampled still shows where they start,
    in time order, each as its shot, when it starts and its text: what the
    cap on stills left unchecked. Empty for a wordless film."""
    shown = {(str(r.get("label", "")), ln.get("at"), ln.get("text"))
             for r in samples for ln in r.get("lines") or [] if isinstance(ln, dict)}
    out = []
    for line in narrated_lines(build, voice):
        label = str(line["shot"].get("label", ""))
        if (label, line["at"], line["text"]) not in shown:
            out.append({"shot": label, "at": line["at"], "text": line["text"]})
    return out


#: How many unsampled lines the not_evidenced line names; the scorecard's
#: `lines_unsampled` keeps them all.
LINES_NAMED = 10


def unseen_lines_brief(dropped: list[dict], total: int) -> str:
    """The line that tells the reviewer, before it scores, how many
    narrated lines have no still where they start. Empty when none."""
    if not dropped:
        return ""
    return (f"{len(dropped)} of the {total} narrated lines have no still where they "
            "start: do not judge from the other stills whether the picture shows "
            "them when they are said; the scorecard names them under not_evidenced.")


def note_lines_unsampled(card: dict, dropped: list[dict], *, total: int,
                         limit: int | None, stills: int, reused: bool = False) -> dict:
    """The scorecard says which narrated lines got no still (AUTO-104):
    `lines_unsampled` lists them all and a not_evidenced line counts them
    and names the first LINES_NAMED by shot and time. A film with no
    narrated line (`total` 0) is left as it is."""
    if not total:
        return card
    card = dict(card, lines_unsampled=list(dropped))
    if dropped:
        if reused:
            under = "in the reused pass"
        elif limit is not None:
            under = f"under --frames {limit}"
        elif stills >= EVERY_SHOT_UP_TO:
            under = (f"past the default of {EVERY_SHOT_UP_TO} stills "
                     "(--frames N samples more)")
        else:
            under = "by the sampler"
        names = ", ".join(f"{d['shot']} at {_stamp(d['at'])}" for d in dropped[:LINES_NAMED])
        if len(dropped) > LINES_NAMED:
            names += f" and {len(dropped) - LINES_NAMED} more (`lines_unsampled`)"
        card["not_evidenced"] = list(card.get("not_evidenced") or []) + [
            f"lines: {len(dropped)} of {total} narrated lines have no still where "
            f"they start {under}: {names}; whether the picture shows them when "
            "they are said was not seen"]
    return card


def _free_moment(shot: dict, used: set[float], fps: float) -> float:
    """The shot's midpoint, or when another shot's still already has it
    (nested shots), the nearest frame inside the shot that none has: a
    still is named by its millisecond, and two shots must not share one."""
    t = _mid(shot)
    if t not in used:
        return t
    at, seconds = float(shot["at"]), float(shot["seconds"])
    for k in range(1, int(seconds * fps) + 1):
        for step in (k, -k):
            moved = round(t + step / fps, 3)
            if at < moved < at + seconds and moved not in used:
                return moved
    return t


def unsampled(build: dict, samples: list[dict]) -> list[str]:
    """The labels of the build's shots no sampled still belongs to, in
    label order: what a --frames below the shot count left unseen. A build
    still of its own (`measured_only`) does not count: no per-shot check is
    filed on it and the reviewer is not shown it."""
    seen = {str(r.get("label", "")) for r in judged(samples)}
    return sorted({str(s.get("label", "")) for s in build.get("shots", [])} - seen)


def unseen_brief(missing: list[str], total: int) -> str:
    """The line that tells the reviewer, before it scores, which shots it
    has no picture of, so its scores are read as covering what it saw.
    Empty when every shot has a still."""
    if not missing:
        return ""
    return (f"There is no still of {len(missing)} of the {total} shots in this pass "
            f"({', '.join(missing)}): score what the frames and the measurements "
            "show, and neither credit nor fault pictures you have not seen; the "
            "scorecard names these shots under not_evidenced.")


def note_unsampled(card: dict, missing: list[str], *, total: int,
                   limit: int | None, reused: bool = False) -> dict:
    """The scorecard says which shots went unsampled (AUTO-103): `unsampled`
    lists them and a not_evidenced line counts and names them, so a score
    read from a tight --frames is not mistaken for one of the whole film."""
    card = dict(card, unsampled=list(missing))
    if missing:
        if reused:
            under = "in the reused pass"
        elif limit is not None:
            under = f"under --frames {limit}"
        elif total > EVERY_SHOT_UP_TO:
            under = (f"past the default of {EVERY_SHOT_UP_TO} stills "
                     "(--frames N samples more)")
        else:
            under = "by the sampler"
        card["not_evidenced"] = list(card.get("not_evidenced") or []) + [
            f"frames: {len(missing)} of {total} shots not sampled {under}: "
            f"{', '.join(missing)}; their pictures were not seen"]
    return card


# --------------------------------------------------------------------------- #
# what the frames measure, before anyone looks
# --------------------------------------------------------------------------- #

def _thumb(path):
    """One frame as a luma thumbnail, or None when it cannot be read."""
    from PIL import Image
    try:
        with Image.open(path) as src:
            im = src.convert("L")
    except (OSError, ValueError):
        return None
    im.thumbnail((THUMB, THUMB))
    return im


def frame_stats(path) -> dict:
    """framecheck.frame_stats of one still under this module's key names
    (`spread`, `clipped_share`); empty when the file cannot be read."""
    try:
        st = framecheck.frame_stats(path)
    except (OSError, ValueError):
        return {}
    return {"mean": st["mean"], "spread": st["std"],
            "dominant_share": st["dominant_share"], "clipped_share": st["clipped"]}


def judge_still(stats: dict) -> list[str]:
    """What one still proves on its own: `flat` (nothing to see) or
    `clipped` (the highlights are gone). A flat frame is not also clipped."""
    if not stats:
        return []
    if stats.get("spread", 99) < FLAT_STD or stats.get("dominant_share", 0) > FLAT_SHARE:
        return ["flat"]
    if stats.get("clipped_share", 0) > CLIPPED_SHARE:
        return ["clipped"]
    return []


def burst_motion(paths) -> dict:
    """What a burst evidences: the mean absolute difference between each
    consecutive pair (`steps`) and between its first and last frame
    (`total`), and from them whether the shot is `frozen` (every step and
    the whole window under FROZEN_DIFF: a slow drift is still motion),
    `moving`, or has a `jump` (one step far above the others' median: the
    index of the frame it lands on)."""
    from PIL import ImageChops, ImageStat

    def diff(a, b) -> float:
        return round(ImageStat.Stat(ImageChops.difference(a, b)).mean[0], 2)

    thumbs = [im for im in (_thumb(p) for p in paths) if im is not None]
    steps = [diff(a, b) for a, b in zip(thumbs, thumbs[1:]) if a.size == b.size]
    out = {"frames": len(thumbs), "steps": steps, "mean": 0.0, "max": 0.0, "total": 0.0,
           "frozen": False, "jump": None, "verdict": "not rendered"}
    if not steps:
        return out
    out["mean"] = round(sum(steps) / len(steps), 2)
    out["max"] = max(steps)
    if thumbs[0].size == thumbs[-1].size:
        out["total"] = diff(thumbs[0], thumbs[-1])
    if out["max"] <= FROZEN_DIFF and out["total"] <= FROZEN_DIFF:
        out["frozen"], out["verdict"] = True, "frozen"
        return out
    i = max(range(len(steps)), key=steps.__getitem__)
    others = sorted(steps[:i] + steps[i + 1:])
    median = others[len(others) // 2] if others else 0.0
    if len(steps) > 1 and steps[i] > JUMP_MIN and steps[i] > JUMP_RATIO * median:
        out["jump"], out["verdict"] = i + 1, "jump"
    else:
        out["verdict"] = "moving"
    return out


def frame_ink(path) -> float | None:
    """How much is drawn in one still (AUTO-145): the share of its THUMB
    luma thumbnail whose edge magnitude (FIND_EDGES) exceeds INK_EDGE. A
    blank frame is 0 and each stroke drawn adds to it; the same drawing
    moved a few pixels reads the same. None when it cannot be read. A
    noise-like ground saturates it, so drawing on noise does not raise it."""
    from PIL import ImageFilter
    im = _thumb(path)
    if im is None:
        return None
    hist = im.filter(ImageFilter.FIND_EDGES).histogram()
    total = sum(hist)
    return round(sum(hist[INK_EDGE + 1:]) / total, 4) if total else 0.0


def measure(samples: list[dict], frames_dir: Path, *, screen=None) -> list[dict]:
    """The numbers beside each sample: its still's statistics under `still`,
    its burst's motion under `motion`, and (AUTO-145) its ink under `ink`,
    with the ink of the frame each narrated line and build still is read
    from on that entry. With `screen` (t, label -> the text nodes on
    screen, `screen_text`) the words on screen under `text`. Rows are
    updated in place."""
    inks: dict[str, float | None] = {}

    def ink(name: str) -> float | None:
        if name not in inks:
            path = frames_dir / name
            inks[name] = frame_ink(path) if path.is_file() else None
        return inks[name]

    for row in samples:
        still = frames_dir / row["frame"]
        if still.is_file():
            row["still"] = frame_stats(still)
            value = ink(row["frame"])
            if value is not None:
                row["ink"] = value
        burst = row.get("burst") or {}
        if burst.get("files"):
            paths = [frames_dir / name for name in burst["files"]]
            row["motion"] = burst_motion([p for p in paths if p.is_file()])
        for entry in (row.get("lines") or []) + (row.get("builds") or []):
            if isinstance(entry, dict) and entry.get("frame"):
                value = ink(str(entry["frame"]))
                if value is not None:
                    entry["ink"] = value
        if screen is not None:
            row["text"] = text_record(screen(row["t"], str(row.get("label", ""))))
    return samples


def _lit(track, t: float) -> list[float] | None:
    """The span of one opacity track (keys [time, value] on the film's
    clock) that holds `t`, or None when the track has it unseen there. A
    first key above 0 holds from the start; a later key above 0 opens a
    span, read from when the text is fully in, and a key at 0 closes it;
    after the last key its value holds."""
    keys = sorted((float(k[0]), float(k[1])) for k in track or [] if len(k) == 2)
    if not keys:
        return [-math.inf, math.inf]
    spans, start = [], (-math.inf if keys[0][1] > 0 else None)
    for time, value in keys[1:]:
        if value > 0 and start is None:
            start = time
        elif value <= 0 and start is not None:
            spans.append([start, time])
            start = None
    if start is not None:
        spans.append([start, math.inf])
    return next((s for s in spans if s[0] <= t < s[1]), None)


def screen_text(nodes: list[dict], t: float, off_camera=()) -> list[dict]:
    """The text and formula nodes (geometry._composed with TEXT_KINDS) on
    screen at `t` (AUTO-145): inside their window, seen under every opacity
    track above them, and not among the ids the engine says the camera
    never sees. Each comes back with `shown`, the span it holds there.
    build's placeholder card (a top-level layer of a `card-` asset) is not
    a super and is left out."""
    off = set(off_camera or ())
    out = []
    for node in nodes or []:
        ident = str(node.get("id", ""))
        if ident in off:
            continue
        if "/" not in ident and str(node.get("asset", "")).startswith(PLACEHOLDER_ASSET):
            continue
        lo, hi = (node.get("window") or [-math.inf, math.inf])[:2]
        lo = -math.inf if lo is None else float(lo)
        hi = math.inf if hi is None else float(hi)
        if not lo <= t < hi:
            continue
        shown = [lo, hi]
        for track in node.get("fades") or []:
            span = _lit(track, t)
            if span is None:
                break
            shown = [max(shown[0], span[0]), min(shown[1], span[1])]
        else:
            out.append(dict(node, shown=shown))
    return out


def text_record(nodes: list[dict]) -> dict:
    """What a still's viewer has to read (AUTO-145), from `screen_text`:
    `words` counts every node not named for a quote, a headline or a credit
    (EXEMPT_TEXT), a formula as FORMULA_WORDS; `items` gives each node's
    words, whether it is exempt, the seconds it is on screen and the
    seconds it takes to read (script.READING_CPS)."""
    items = []
    for node in nodes:
        ident, kind = str(node.get("id", "")), str(node.get("kind", "text"))
        name = str(node.get("name") or ident.rsplit("/", 1)[-1])
        text = " ".join(str(node.get("text", "")).split())
        shown = node.get("shown") or node.get("window") or [0.0, 0.0]
        seconds = (float(shown[1]) - float(shown[0])
                   if all(math.isfinite(float(v)) for v in shown[:2]) else None)
        items.append({"id": ident, "kind": kind,
                      "words": FORMULA_WORDS if kind == "formula" else len(text.split()),
                      "exempt": any(s.startswith(EXEMPT_TEXT)
                                    for s in (name, str(node.get("asset", "")))),
                      "seconds": None if seconds is None else round(seconds, 3),
                      "needs": round(len(text) / READING_CPS, 2), "text": text})
    return {"words": sum(i["words"] for i in items if not i["exempt"]),
            "nodes": len(items), "formulas": sum(1 for i in items if i["kind"] == "formula"),
            "ids": [i["id"] for i in items], "items": items}


_OFF_CAMERA = re.compile(r"(\S+) is drawn from .*?never inside the camera's view")


def _shot_of(diag: dict, spans: list[tuple[str, float, float]]) -> str | None:
    """Which shot an engine diagnostic belongs to: by its time span, else by
    the include path it names."""
    time = diag.get("time")
    if isinstance(time, (list, tuple)) and len(time) == 2:
        try:
            mid = (float(time[0]) + float(time[1])) / 2
        except (TypeError, ValueError):
            mid = None
        if mid is not None:
            for label, a, b in spans:
                if a <= mid < b:
                    return label
    path = str(diag.get("path") or "")
    for label, _, _ in spans:
        if path == f"shot-{label}" or path.startswith(f"shot-{label}/"):
            return label
    return None


def engine_diagnostics(report: dict | None, shots: list[dict]) -> dict:
    """The engine's encode diagnostics (`diagnostics`, each output's
    `findings` and `evaluation_warnings` in render.json), per shot: the ids
    of objects never inside the camera's view, and how many other warnings."""
    if not report:
        return {}
    items = [d for d in (report.get("diagnostics") or []) if isinstance(d, dict)]
    for out in ((report.get("result") or {}).get("outputs") or []):
        for key in ("findings", "evaluation_warnings"):
            items += [d for d in (out.get(key) or []) if isinstance(d, dict)]
    spans = [(str(s.get("label", "")), float(s["at"]), float(s["at"]) + float(s["seconds"]))
             for s in shots]
    summary: dict[str, dict] = {}
    for diag in items:
        label = _shot_of(diag, spans)
        if label is None:
            continue
        row = summary.setdefault(label, {"off_camera": [], "warnings": 0})
        message = str(diag.get("message", ""))
        hit = _OFF_CAMERA.search(message)
        if hit or "OFF-CAMERA" in str(diag.get("code", "")):
            ident = hit.group(1) if hit else str(diag.get("path") or "?")
            if ident not in row["off_camera"]:
                row["off_camera"].append(ident)
        else:
            row["warnings"] += 1
    return summary


def _on_edge(row: dict) -> bool:
    """Whether a sampled row is one a single defect blocks on, by its whole
    reasons: a substring test made `beat: rehook` (and a section named after
    Hooke) an edge."""
    return any(why in EDGE_REASONS for why in str(row.get("why", "")).split("; "))


#: Mechanical kinds that block on their own, wherever they sit.
ALWAYS_BLOCKING = ("penetration", "reads-merged", "proof-mismatch")

def reown_by_proof(mechanical: list[dict], proof_found: list[dict]) -> int:
    """A mechanical finding on a shot of a world that fails its proof-frame
    check is the build's, whatever draws the shot: the film does not show
    what the author built, so vfx cannot fix a frozen or off-camera shot
    in it. Each finding moved names the world under `reowned`; returns how
    many moved. A `text-dense` stays with vfx: it is counted from the
    document vfx wrote, not from what the film shows (AUTO-145)."""
    by_shot = {label: f["world"] for f in proof_found for label in f.get("shots") or []}
    moved = 0
    for f in mechanical:
        world = by_shot.get(str(f.get("shot", "")))
        if (not world or f.get("kind") in ("proof-mismatch", "text-dense")
                or f.get("owner") == "build" or f.get("kind") in RHYTHM_KINDS):
            continue
        f["owner"] = "build"
        f["reowned"] = f"world {world} fails its proof-frame check, so the build owns this"
        moved += 1
    return moved


def proof_frame_findings(args, project, scene: str, vfx: dict | None, home: Path,
                         shots: list[dict], out_dir: Path) -> tuple[list[dict], str]:
    """The film at each world's proof-frame time against the proof frame.
    Returns the `proof-mismatch` findings and a note for the scorecard (empty
    when every world was compared). A film with no proof frames files nothing.

    The film is rendered ungraded, from a copy of the scene beside it with
    the `<colorManagement>` removed, as the proof frame was (AUTO-72); the
    copy is named for the pass and lives only for the render. Each row is
    recorded under `out_dir/proof/plan.json` with `graded: false`; a reused
    pass whose plan does not say so had its frames rendered graded, and
    they are rendered again."""
    plan = proof_plan(vfx, home, shots)
    skipped = max(0, len(plan) - PROOF_LIMIT)
    plan = plan[:PROOF_LIMIT]
    if not plan:
        return [], ("no proof frames to compare the film with (no vfx, or an older "
                    "version layout): film-versus-proof check skipped")
    for row in plan:
        row["graded"] = False
    frames = Path(out_dir) / "proof"
    frames.mkdir(parents=True, exist_ok=True)
    before = read_json(frames / "plan.json", warn=False, shape=list) or []
    ungraded_before = {r.get("frame") for r in before
                       if isinstance(r, dict) and r.get("graded") is False}
    for row in plan:
        if row["frame"] not in ungraded_before:
            (frames / row["frame"]).unlink(missing_ok=True)
    write_json(frames / "plan.json", plan)
    film = Path(scene)
    copy = film.with_name(f"{film.stem}.proof-{Path(out_dir).name}{film.suffix}")
    try:
        copy.write_bytes(ungraded(film.read_bytes()))
    except OSError as exc:
        return [], f"proof check skipped: could not write the ungraded copy {copy}: {exc}"
    try:
        problems = render_frames(args, project, str(copy), plan, frames)
    finally:
        copy.unlink(missing_ok=True)
    found: list[dict] = []
    unread = []
    for row in plan:
        diff = proof_diff(row["proof"], frames / row["frame"])
        if diff is None:
            unread.append(row["world"])
            continue
        if diff <= PROOF_DIFF:
            continue
        drift = row.get("start_drift")
        found.append({
            "timestamp": row["t"], "frame": f"proof/{row['frame']}", "shot": row["label"],
            "owner": "build", "kind": "proof-mismatch", "severity": "blocking",
            "world": row["world"], "shots": row["shots"], "difference": round(diff, 2),
            "graded": False,
            "problem": mismatch_text(row, diff),
            "why": "the composed film does not show what the author built and the vfx gate "
                   "reviewed; measured mechanically from the two frames",
            "root_cause": "the build split the world into includes that restart at t=0, "
                          "moved its start, or composed it differently from the proof render",
            "fix": "build the world as one include on one clock (see the build manifest's "
                   "split_worlds) and check its start against vfx.json",
            **({"start_drift": drift} if drift else {})})
    parts = []
    if problems or unread:
        parts.append(f"{len(problems)} render problem(s), {len(unread)} world(s) unread")
    if skipped:
        parts.append(f"{skipped} world(s) not compared (limit {PROOF_LIMIT})")
    note = f"proof-frame check incomplete: {'; '.join(parts)}" if parts else ""
    return found, note


def merged_by_shot(report: dict | None, shots: list[dict]) -> dict[str, list[dict]]:
    """The geometry check's `reads_merged` spans under the label of every
    shot each one overlaps."""
    if not report or report.get("status") != "ok":
        return {}
    step = float(report.get("step") or geo.STEP)
    out: dict[str, list[dict]] = {}
    for span in report.get("reads_merged") or []:
        a, b = float(span["first"]), float(span["last"]) + step
        for s in shots:
            at = float(s.get("at", 0) or 0)
            if at < b and a < at + float(s.get("seconds", 0) or 0):
                out.setdefault(str(s.get("label", "")), []).append(span)
    return out


def _merged_finding(spans: list[dict]) -> dict:
    """One sampled shot's `reads-merged` spans as a finding, with the numbers."""
    longest = max(spans, key=lambda s: s["seconds"])
    named = "; ".join(
        f"`{s['hero'].rsplit('/', 1)[-1]}` over `{s['object'].rsplit('/', 1)[-1]}` "
        f"{s['first']:g}-{s['last']:g}s ({s['seconds']:g}s, {s['overlap']:.0%} of it over the "
        f"surface, {s['clearance']:.1f} units above it, {s['ratio']:.0%} of the camera distance)"
        for s in spans)
    return {
        "kind": "reads-merged",
        "problem": f"the hero reads as merged into the set: {named}; no shadow of it "
                   "falls on the surface",
        "why": "on screen the hero lies over a farther surface it barely clears, with no "
               "shadow and too little parallax to place it, so the viewer sees it resting "
               "in the surface; measured mechanically by projecting the engine's evaluated "
               "camera and geometry, not judged from the still",
        "root_cause": "the camera's line of sight grazes the surface while the hero flies "
                      "just above it, and the shadow-casting light lands the hero's "
                      "shadow elsewhere",
        "fix": geo.merged_advice(longest),
    }


def geometry_by_shot(report: dict | None, shots: list[dict]) -> dict[str, list[dict]]:
    """The geometry check's penetration records (`geometry.check_project`)
    under the label of the shot each falls in."""
    if not report or report.get("status") != "ok":
        return {}
    spans = [(str(s.get("label", "")), float(s.get("at", 0) or 0),
              float(s.get("at", 0) or 0) + float(s.get("seconds", 0) or 0)) for s in shots]
    out: dict[str, list[dict]] = {}
    for row in report.get("penetrations") or []:
        t = float(row.get("time", 0))
        label = next((l for l, a, b in spans if a <= t < b), None)
        if label is not None:
            out.setdefault(label, []).append(row)
    return out


def _penetration_finding(records: list[dict]) -> dict:
    """One sampled shot's merges as a finding: the deepest instance, the
    pairs, the span, the numbers."""
    deepest = max(records, key=lambda r: r["depth"])
    pairs = sorted({(r["hero"], r["object"]) for r in records})
    times = sorted({float(r["time"]) for r in records})
    named = "; ".join(f"`{h.rsplit('/', 1)[-1]}` inside `{o.rsplit('/', 1)[-1]}`" for h, o in pairs)
    return {
        "kind": "penetration",
        "problem": f"hero penetrates the set: {named}, up to {deepest['depth']:.1f} units along "
                   f"{deepest['axis']} at {deepest['time']:g}s, {len(times)} sample(s) from "
                   f"{times[0]:g}s to {times[-1]:g}s (tolerance {deepest['tolerance']:.1f})",
        "why": "a hero inside a solid reads as a merge, not a contact; measured "
               "mechanically from the engine's evaluated frame graph and the "
               "document's geometry, not judged from the still",
        "root_cause": "the hero's keyed path (x/y/z, rotation) crosses the set's solids "
                      "in the authored world",
        "fix": geo.advice(deepest),
    }


def _build_only(row: dict) -> bool:
    """A build still of its own (AUTO-145): every reason a build reason. It
    has no burst and shows a shot at its edge, so it is never the shot's
    home and its frame is read for ink, not judged flat."""
    return all(why.startswith(BUILD_WHY) for why in str(row.get("why", "")).split("; "))


def measured_only(row: dict) -> bool:
    """A row no model is shown (AUTO-145): a build still of its own, with no
    burst. It sits a quarter second from a cut, where a transition can be
    mid-dissolve, and is there to be measured for ink, not judged; it is no
    shot's home and does not count as a shot's still."""
    return bool(row.get("builds")) and "burst" not in row and _build_only(row)


def judged(samples: list[dict]) -> list[dict]:
    """The rows a model is shown: every sample but the measured-only build
    stills (`measured_only`)."""
    return [row for row in samples if not measured_only(row)]


def _norm(text) -> str:
    return " ".join(str(text or "").lower().split())


def no_build_findings(samples: list[dict]) -> list[dict]:
    """`no-build` (AUTO-145): the first shot of a diagram effect whose ink
    never rises by more than BUILD_STEP of its last value from one reading
    to the next (the first build still, each line said between, the last):
    the diagram is on screen whole from the cut and talked over, not built
    in steps. A shot with a reading missing, or blank at its end, files
    nothing (note_builds says which)."""
    found = []
    for label, rec in _build_series(samples).items():
        points = rec["points"]
        inks = [e.get("ink") for _, e in points]
        if len(points) < 2 or not all(isinstance(v, (int, float)) for v in inks):
            continue
        last = float(inks[-1])
        if last <= 0 or any(b - a > BUILD_STEP * last for a, b in zip(inks, inks[1:])):
            continue
        row, first = rec["row"] or {}, rec["first"]
        series = [[round(float(t), 3), float(v)] for (t, _), v in zip(points, inks)]
        read = ", ".join(f"{v:.2f} at {t:g} s" for t, v in series)
        found.append({
            "timestamp": first["t"], "frame": first.get("frame") or row.get("frame", ""),
            "shot": label, "owner": "vfx", "kind": "no-build", "ink": series,
            "problem": f"effect `{rec['effect']}` is on screen whole from the cut: ink "
                       f"{read}; it never grows by more than {BUILD_STEP:.0%} of its "
                       "last reading from one still to the next, so the diagram is "
                       "talked over, not built",
            "why": "the house builds every diagram in steps keyed to the narration, so "
                   "the viewer sees each part arrive as it is named; measured "
                   "mechanically from the ink of the stills, not judged",
            "root_cause": "the effect's parts are all drawn at its first frame, with "
                          "no reveal keyed to the lines that name them",
            "fix": "reveal the diagram part by part on the narration's words (an "
                   "opacity, a draw-on or a scale key per part), starting from an "
                   "empty or near-empty frame at the cut"})
    return found


def screenplay_supers(script: dict | None) -> dict[str, list[str]]:
    """The on-screen text the screenplay asked of each shot (label to its
    on_screen_text): a dense super it asked for was already flagged there
    by rule:wall-of-text, and `text-dense` says so."""
    out: dict[str, list[str]] = {}
    for sec in (script or {}).get("sections") or []:
        for shot in (sec or {}).get("shots") or []:
            if isinstance(shot, dict) and shot.get("on_screen_text"):
                out[str(shot.get("label", ""))] = [str(s) for s in shot["on_screen_text"]]
    return out


def text_dense_findings(samples: list[dict], supers: dict | None = None) -> list[dict]:
    """`text-dense` (AUTO-145), from the words the rendered document puts on
    screen at each still (`text`): a node over the screenplay's
    wall-of-text bound, SCREEN_WORDS or more at once, or a quote, headline
    or credit off screen before it can be read. Once per shot for each
    node and for the count, on the first still that shows it."""
    found, seen = [], set()
    for row in samples:
        rec = row.get("text")
        if not isinstance(rec, dict):
            continue
        label = str(row.get("label", ""))
        base = {"timestamp": row["t"], "frame": row.get("frame", ""), "shot": label,
                "owner": "vfx", "kind": "text-dense"}
        asked = {_norm(s) for s in (supers or {}).get(label) or []}
        items = [i for i in rec.get("items") or [] if isinstance(i, dict)]
        for item in items:
            ident = item.get("id", "")
            if item.get("exempt"):
                seconds, needs = item.get("seconds"), float(item.get("needs") or 0)
                if seconds is None or seconds >= needs or (label, "read", ident) in seen:
                    continue
                seen.add((label, "read", ident))
                found.append(base | {
                    "problem": f"`{ident}` ({item.get('words')} words) is on screen "
                               f"{seconds:.1f} s, under the {needs:.1f} s it takes to "
                               "read",
                    "why": "a quote, headline or credit is there to be read whole; "
                           "counted from the document, not read off the frame",
                    "root_cause": "the node's span or its fade is shorter than its text "
                                  f"at {READING_CPS:g} characters a second",
                    "fix": "hold it on screen long enough to read, or shorten it"})
                continue
            if (item.get("kind") == "formula" or int(item.get("words") or 0) <= WALL_WORDS
                    or (label, "node", ident) in seen):
                continue
            seen.add((label, "node", ident))
            problem = (f"`{ident}` carries {item['words']} words, over the {WALL_WORDS} a "
                       "super may carry")
            if _norm(item.get("text")) in asked:
                problem += ("; the screenplay asked for this super; rule:wall-of-text "
                            "already flagged it")
            found.append(base | {
                "problem": problem,
                "why": "words on screen compete with the narration for the viewer's "
                       "attention: a super is a few words, not a sentence; counted from "
                       "the document, not read off the frame",
                "root_cause": "the effect's text node carries a sentence instead of a "
                              "label",
                "fix": "cut the text to the few words the picture needs and let the "
                       "narration say the rest"})
        counted = [i for i in items if not i.get("exempt")]
        total = int(rec.get("words") or 0)
        if total >= SCREEN_WORDS and (label, "count") not in seen:
            seen.add((label, "count"))
            names = ", ".join(f"`{i['id']}` ({i['words']})" for i in counted)
            found.append(base | {
                "problem": f"{total} words on screen at once, at or over the "
                           f"{SCREEN_WORDS} a frame may carry: {names}",
                "why": "a frame with this much to read is read, not watched; counted "
                       "from the document, not read off the frame",
                "root_cause": "labels, supers and formulas pile up in one frame instead "
                              "of arriving with the narration",
                "fix": "show fewer labels at once: bring each in when the narration "
                       "names it and take the last one out"})
    return found


def mechanical_findings(samples: list[dict], diagnostics: dict | None = None,
                        geometry: dict | None = None, merged: dict | None = None, *,
                        supers: dict | None = None) -> list[dict]:
    """What the measurements already prove, as findings in the scorecard's
    own shape plus `kind` and `shot`. Blocking when on the hook or the
    ending, or when the same kind is on more than one sampled shot; a
    `penetration` (`geometry`, records per shot label) and a `reads-merged`
    (`merged`, spans per shot label) are always blocking and owned by vfx.
    `no-build` and `text-dense` (AUTO-145; `supers`, label to the
    screenplay's on-screen text) are ADVISORY_KINDS: always minor."""
    found: list[dict] = []
    # What is recorded per shot, not per frame, is filed once per shot
    # (AUTO-103: a shot can have several stills), on its midpoint still
    # when it has one. A shot is its label and its `at`; a row without `at`
    # (a pass sampled before AUTO-103) is its own shot.
    # An evenly spaced still or a line's (AUTO-104) is never the home while
    # the shot has its own; a build still (AUTO-145) never is.
    def extra(row: dict) -> bool:
        return all(why == SPREAD_WHY or why.startswith((LINE_WHY, BUILD_WHY))
                   for why in str(row.get("why", "")).split("; "))
    home: dict[tuple, int] = {}
    for i, row in enumerate(samples):
        if measured_only(row):
            continue
        key = (str(row.get("label", "")), row.get("at", row.get("t")))
        if key not in home or (extra(samples[home[key]]) and not extra(row)):
            home[key] = i
    for i, row in enumerate(samples):
        if measured_only(row):
            continue
        base = {"timestamp": row["t"], "frame": row.get("frame", ""),
                "shot": str(row.get("label", "")),
                "owner": "vfx" if row.get("asset") == "effect" else "build"}
        st = row.get("still") or {}
        for kind in judge_still(st):
            if kind == "flat":
                found.append(base | {
                    "kind": "flat",
                    "problem": f"nearly flat frame (luma {st['mean']:.0f}, spread "
                               f"{st['spread']:.1f}, {st['dominant_share']:.0%} at one level)",
                    "why": "nothing is visible to a viewer; measured mechanically "
                           "from the still, not judged",
                    "root_cause": "the camera does not frame the shot's objects, the "
                                  "lights do not reach them, or the geometry is elsewhere",
                    "fix": "frame and light the subject in this symbol, then run the "
                           "frame gate and look at the frame"})
            else:
                found.append(base | {
                    "kind": "clipped",
                    "problem": f"{st['clipped_share']:.0%} of the frame is clipped at or "
                               f"above level {CLIPPED_LEVEL}",
                    "why": "detail is lost in the highlights and the frame reads as "
                           "blown out; measured mechanically from the still",
                    "root_cause": "exposure, or a light or emissive surface, well beyond "
                                  "the frame's range",
                    "fix": "lower the exposure or the light's intensity so the brightest "
                           "surface keeps detail"})
        mo = row.get("motion") or {}
        steps = "/".join(f"{s:.1f}" for s in mo.get("steps", []))
        span = (row.get("burst") or {}).get("span", BURST_SECONDS)
        if mo.get("frozen"):
            found.append(base | {
                "kind": "frozen",
                "problem": f"no motion across {mo.get('frames')} frames over {span}s "
                           f"(steps {steps})",
                "why": "a camera that never moves over a stage where nothing changes "
                       "reads as a slide; measured mechanically from the burst",
                "root_cause": "no animation reaches this span: the camera, the objects "
                              "and the lights all hold still",
                "fix": "author movement across the shot (camera, subject or light), or "
                       "shorten the hold to what the edit needs"})
        if mo.get("jump") is not None:
            found.append(base | {
                "kind": "jump",
                "problem": f"a jump inside the shot: one step of {mo.get('max', 0):.1f} "
                           f"against the others (steps {steps})",
                "why": "a discontinuity inside one shot reads as an unintended cut or a "
                       "glitch; measured mechanically from the burst",
                "root_cause": "a key, an include boundary or a hold that snaps instead "
                              "of easing",
                "fix": "ease the key, or move the boundary to a cut the edit intends"})
        # What follows is recorded per shot (see `home` above).
        if home.get((base["shot"], row.get("at", row.get("t")))) != i:
            continue
        diag = (diagnostics or {}).get(base["shot"]) or {}
        ids = list(diag.get("off_camera") or [])
        if ids:
            shown = ", ".join(ids[:8]) + (f" and {len(ids) - 8} more" if len(ids) > 8 else "")
            found.append(base | {
                "kind": "off_camera",
                "problem": f"{len(ids)} object{'s' if len(ids) != 1 else ''} never in the "
                           f"camera's view: {shown}",
                "why": "the engine evaluated every frame: these are drawn but never seen, "
                       "so what the shot was built from is not what the viewer gets",
                "root_cause": "the camera's frustum never contains them (engine "
                              "diagnostic OFF-CAMERA in render.json)",
                "fix": "move the camera or the objects so the shot frames what it draws, "
                       "or delete what is never seen"})
        merges = (geometry or {}).get(base["shot"]) or []
        if merges:
            found.append(base | _penetration_finding(merges) | {"owner": "vfx"})
        reads = (merged or {}).get(base["shot"]) or []
        if reads:
            found.append(base | _merged_finding(reads) | {"owner": "vfx"})
    found += no_build_findings(samples) + text_dense_findings(samples, supers)
    shots_by_kind: dict[str, set] = {}
    for f in found:
        shots_by_kind.setdefault(f["kind"], set()).add(f["shot"])
    edge = {str(row.get("label", "")) for row in samples if _on_edge(row)}
    for f in found:
        f["severity"] = ("minor" if f["kind"] in ADVISORY_KINDS else
                         "blocking" if f["kind"] in ALWAYS_BLOCKING or f["shot"] in edge
                         or len(shots_by_kind[f["kind"]]) > 1 else "minor")
    return found


def _mean(scores: dict) -> float:
    """The mean of the scored categories; one recorded not evidenced (None)
    has no score to average."""
    values = []
    for key in CATEGORIES:
        if key in scores and scores[key] is None:
            continue
        try:
            values.append(float(scores.get(key, 0)))
        except (TypeError, ValueError):
            values.append(0.0)
    return round(sum(values) / len(values), 2) if values else 0.0


def cap_scores(scores: dict, overall: float, mechanical: list[dict]
               ) -> tuple[dict, float, list[str]]:
    """While a blocking mechanical finding stands the reviewer cannot award
    more than MECHANICAL_CAP in the categories the frames measure; a
    blocking rhythm finding caps RHYTHM_CAPPED instead, and a hold both
    (CAP_RULE). The overall follows the capped mean when that is lower."""
    scores, capped = dict(scores), []
    keys: list[str] = []
    for f in mechanical:
        if f.get("severity") != "blocking":
            continue
        kind = f.get("kind")
        hit = (RHYTHM_CAPPED + CAPPED if kind == "hold" else
               RHYTHM_CAPPED if kind in RHYTHM_KINDS else CAPPED)
        keys += [k for k in hit if k not in keys]
    if keys:
        for key in [k for k in CATEGORIES if k in keys] + [k for k in keys
                                                           if k not in CATEGORIES]:
            try:
                value = float(scores.get(key, 0))
            except (TypeError, ValueError):
                value = 0.0
            if value > MECHANICAL_CAP:
                scores[key] = MECHANICAL_CAP
                capped.append(key)
    try:
        overall = float(overall)
    except (TypeError, ValueError):
        overall = 0.0
    if capped:
        overall = min(overall, _mean(scores))
    return scores, round(overall, 2), capped


def mechanical_line(f: dict) -> str:
    """One blocking mechanical finding as the card's `blocking` names it."""
    where = f"shot {f.get('shot', '')}"
    if f.get("section"):
        where += f", section {f['section']}"
    return (f"mechanical: {f.get('problem', '')} at {float(f.get('timestamp') or 0):g}s "
            f"({where})")


#: The render's scene scores, scanned on a render host (AUTO-141): a seam
#: the tests replace, as RENDERER and GEOMETRY are.
RHYTHM_SCAN = rhythm.scan_remote
#: What the reviewer is shown of the rhythm block: the numbers, labelled.
rhythm_brief = rhythm.brief_of


def rhythm_while_up(args, project, into: dict):
    """The callback `--watch` runs while its cut is on a render host
    (watch.prepare_remote's `while_up`): the rhythm scan reads that cut
    there, so the cut goes up once, and what it got (or why it failed) is
    kept in `into` for rhythm_pass. `upload` is the watch's own measure of
    that one upload, recorded as the scan's. It never raises: the watch
    goes on."""
    video = project.path("video")

    def scan(host: str, path: str, upload: dict | None = None) -> None:
        try:
            into["got"] = RHYTHM_SCAN(video, args, slug=Path(project.root).name,
                                      remote_cut=(host, path), upload=upload)
        except rhythm.RhythmError as exc:
            into["error"] = str(exc)
    return scan


def rhythm_pass(args, project, build: dict, voice: dict | None, pass_dir: Path, *,
                reused: bool, shared: dict | None = None) -> dict:
    """The film's rhythm for this pass, written to pass-NN/rhythm.json and
    returned (the record; its `scores` stay out of the scorecard). The
    timeline measures always run. The render's are scanned only when
    video.mp4 is the cut scene.xml builds and a render host answers;
    otherwise the hold and the opening are named under not_evidenced with
    why and the command that evidences them, never scored as passing. A
    reused pass measures the scores its first run recorded and scans
    nothing. `shared` is what rhythm_while_up kept: a scan of the cut the
    watch already sent, taken instead of sending it again."""
    from . import doctor                     # doctor imports nothing of qa
    record_file = pass_dir / rhythm.RECORD
    takes = [t for t in (voice or {}).get("takes") or [] if isinstance(t, dict)]
    scores = scan = unscanned = None
    rerun = " ".join(["yt", "qa", "--project", str(project.root)])
    earlier = read_json(record_file, warn=False) if reused and record_file.is_file() else None
    video = project.path("video")
    if reused:
        if isinstance(earlier, dict) and earlier.get("scores"):
            scores = [tuple(pair) for pair in earlier["scores"]]
            scan = earlier.get("scan")
        else:
            unscanned = ("--reuse scores an earlier pass again and scans nothing; that "
                         "pass recorded no scene scores", rerun)
    elif not video.is_file():
        unscanned = ("no video.mp4: the film is not rendered, so its scene scores cannot "
                     "be read", " ".join(["yt", "render", "--project", str(project.root),
                                          "--quality", "final", "--strict"]))
    else:
        stale = doctor.cut_problems(project, build)
        if stale:
            unscanned = stale[0]
        else:
            try:
                got = (shared or {}).get("got") or RHYTHM_SCAN(
                    video, args, slug=Path(project.root).name)
                scores, scan = [tuple(pair) for pair in got["scores"]], got.get("scan")
            except rhythm.RhythmError as exc:
                unscanned = (str(exc), f"{rerun} (with a render host: RS_RENDER_HOSTS, "
                                       "and without YT_NO_REMOTE)")
    block = rhythm.measure(build, takes, scores, fps=(scan or {}).get("fps"), scan=scan,
                           unscanned=unscanned,
                           rescan=f"{rerun} (the scan stopped short; run it again)")
    record = dict(block, scores=[list(pair) for pair in scores] if scores else None)
    if not (reused and isinstance(earlier, dict)):
        write_json(record_file, record)
    return record


def rhythm_lines(block: dict) -> list[str]:
    """findings.md's "## Rhythm" section: the numbers and what is missing."""
    cv, hold = block.get("cv") or {}, block.get("longest_hold")
    opening = block.get("opening") or {}

    def num(v, form="{:.2f}"):
        return "n/a" if v is None else form.format(v)
    # findings.md never says "calibrat": that word is the judge's (AUTO-156).
    gate = ("the scan's thresholds measured" if block.get("calibrated") else
            "the scan's thresholds not yet measured: TO VERIFY")
    lines = ["## Rhythm", "",
             f"Measured from build.json, voice.json and the render's scene scores ({gate}); "
             "evidence for editing_pacing, not a score.", "",
             f"- cuts and builds: CV {num(cv.get('shots'))} by shot, {num(cv.get('events'))} "
             f"by change event (basis {cv.get('basis', '?')}, floor {cv.get('floor', '?')})",
             "- longest hold under narration: " + (
                 f"{hold['seconds']:.1f} s ({hold['start']:.1f}-{hold['end']:.1f} s, shot "
                 f"{hold['shot']})" if hold else "none over the bound"
                 if (block.get("scan") or {}).get("complete") else "not measured"),
             f"- opening: {num(opening.get('rate'), '{:.1f}')} changes/min against a film "
             f"median of {num(opening.get('median'), '{:.1f}')}"]
    for row in block.get("sections") or []:
        lines.append(f"- section {row.get('section', '')}: {num(row.get('wpm'), '{:.0f}')} "
                     f"wpm ({row.get('timing', '?')} timing, {row.get('words', 0)} words)")
    for row in block.get("not_evidenced") or []:
        lines.append(f"- not evidenced: {row.get('measure')}"
                     + (f" ({row['section']})" if row.get("section") else "")
                     + f": {row.get('why', '')}"
                     + (f"; run {row['command']}" if row.get("command") else ""))
    return lines + [""]


def rhythm_not_evidenced(block: dict) -> list[str]:
    """The scorecard's not_evidenced lines for the rhythm measures it lacks."""
    out = []
    for row in block.get("not_evidenced") or []:
        what = row.get("measure", "?") + (f" of section {row['section']}"
                                          if row.get("section") else "")
        out.append(f"rhythm: {what} not measured: {row.get('why', '')}"
                   + (f"; run {row['command']}" if row.get("command") else ""))
    return out


def silent(build: dict) -> bool:
    """No audio track in the build: nothing to hear, so nothing to score."""
    tracks = (build or {}).get("audio_tracks")
    if isinstance(tracks, (int, float)):
        return int(tracks) <= 0
    return not tracks


def rule_spread(spread: dict, *, silent: bool, capped) -> dict:
    """The samples' spread with no spread on a category whose score a rule
    set (AUTO-107): a silent film's SILENT categories and the `capped` ones
    record no sample's median, so improve must read any change in them as
    real. The overall's spread stays what `combine_scores` recorded (the
    reviewers' overalls, or with an unheard audio category each sample's
    mean of the scored ones), though the bar may have lowered the overall
    itself. An unheard category (AUTO-105) has no score and no spread."""
    out = dict(spread or {})
    for key in (SILENT if silent else ()) + tuple(capped or ()):
        out[key] = 0.0
    return out


def score_silence(card: dict, build: dict) -> dict:
    """A silent film scores SILENT_SCORE on what sound would be judged by,
    and says so under not_evidenced, rather than the reviewer's neutral 5."""
    if not silent(build):
        return card
    card = dict(card)
    card["scores"] = dict(card.get("scores") or {})
    noted = list(card.get("not_evidenced") or [])
    for key in SILENT:
        card["scores"][key] = SILENT_SCORE
        noted.append(f"{key}: not evidenced -- build.json has no audio track, "
                     f"scored {SILENT_SCORE:g} by rule")
    card["not_evidenced"] = noted
    try:
        overall = float(card.get("overall", 0))
    except (TypeError, ValueError):
        overall = 0.0
    card["overall"] = round(min(overall, _mean(card["scores"])), 2)
    return card


def _yt(stage_args: str, where: str = "") -> str:
    """`yt <stage> <where> <flags>`: a command the operator can paste."""
    stage, _, rest = stage_args.partition(" ")
    return " ".join(part for part in ("yt", stage, where, rest) if part)


def _then_rebuild(first: str, where: str = "") -> str:
    """`first`, then build, render and QA again, every step aimed at the
    same project, so each one pastes as it stands."""
    build, render, review = (_yt(s, where) for s in ("build", "render", "qa"))
    return f"{first}, then {build}, {render} and {review}" if first \
        else f"{build}, then {render} and {review}"


def _said(source: str, review: dict) -> dict:
    """What a current review found, as the reviewer is shown it: the
    listen-back's flags and verdict, or the director's retakes and notes."""
    if source == "sound":
        flags = [{k: f.get(k) for k in ("cue", "severity", "problem", "fix", "rule", "kept")
                  if f.get(k)} for f in review.get("flags") or [] if isinstance(f, dict)]
        return {"verdict": review.get("verdict"), "flags": flags[:40],
                "revisions": len(review.get("revisions") or []),
                "unresolved": len(review.get("unresolved") or []),
                **({"recheck": review["recheck"]} if review.get("recheck") else {})}
    retakes = [{k: r.get(k) for k in ("label", "reason", "speed") if r.get(k)}
               for r in review.get("retakes") or [] if isinstance(r, dict)]
    return {"verdict": review.get("verdict"), "retakes": retakes[:40],
            "notes": review.get("notes", "")}


def listening_evidence(sound: dict | None, voice: dict | None, *,
                       built_before=(), where: str = "") -> list[dict]:
    """The listening reviews the film has (AUTO-105), one row per LISTENERS
    entry, each marked with what it heard and whether that is what the film
    carries. A row holds `source`, `file`, `review` (what kind of listening
    it is), `covers` (the categories it can evidence), `heard` (the mark the
    review recorded) and `now` (the same mark computed from the file as it
    is), `current`, `why` (what makes it stale or absent, "" when current),
    `command` (what produces a current one, "" when nothing does yet) and,
    only when current, `said`: its findings and verdict, all the reviewer is
    shown of it. A review is current when it answered, its mark matches the
    file, and the build is not older than the file (`built_before` names
    the sources whose file is newer than build.json): a review of a sheet
    since ridden, of takes since re-recorded, or of a sheet the film was
    not built from is stale, said so, and never stands in for the mix."""
    rows = []
    for source, data in (("sound", sound), ("voice", voice)):
        spec = LISTENERS[source]
        data = data if isinstance(data, dict) else {}
        review = data.get("review") if isinstance(data.get("review"), dict) else {}
        mark = review.get("heard") if isinstance(review.get("heard"), dict) else {}
        what = "cue sheet" if source == "sound" else "takes"
        now = (sheet_heard(data.get("cues") or [],
                           mix=(data.get("palette") or {}).get("mix"),
                           normalize=data.get("normalize"))
               if source == "sound" else takes_heard(data.get("takes") or []))
        row = {"source": source, "file": spec["file"], "review": spec["review"],
               "covers": list(spec["covers"]), "heard": mark.get("sha256"),
               "now": now["sha256"] if data else None, "current": False, "why": "",
               "command": _then_rebuild(_yt(spec["redo"], where), where)}
        if not data:
            row["why"] = f"no {spec['file']}"
        elif source == "voice" and data.get("wordless"):
            row["why"] = ("a wordless film: voice.json has no takes, so there is no "
                          "take review")
            row["command"] = ""
        elif not str(review.get("verdict") or "").strip():
            row["why"] = (f"{spec['file']} has no answered review"
                          + (" (`yt sound` ran without --review, or the listen-back "
                             "did not answer)" if source == "sound" else ""))
        elif not mark.get("sha256"):
            row["why"] = (f"the review does not record which {what} it heard (written "
                          "before AUTO-105), so it cannot be shown to have heard what "
                          "the film carries")
        elif mark["sha256"] != now["sha256"]:
            row["why"] = (f"stale: it heard {what} that {spec['file']} no longer holds "
                          f"({mark['sha256'][:12]} then, {now['sha256'][:12]} now)")
        elif source in set(built_before or ()):
            row["why"] = (f"stale: build.json predates {spec['file']}, so the film "
                          f"carries older {what} than the ones reviewed")
            row["command"] = _then_rebuild("", where)
        else:
            row["current"] = True
            row["said"] = _said(source, review)
        rows.append(row)
    return rows


def audio_unheard(rows: list[dict], *, silent: bool = False) -> dict[str, dict]:
    """Each AUDIO category no current row covers: {category: {"missing":
    what is absent or stale, "command": what produces a current review, ""
    when nothing does yet}}. A silent film's SILENT categories are left to
    their rule (`score_silence`)."""
    out = {}
    for key in AUDIO:
        if silent and key in SILENT:
            continue
        covering = [r for r in rows if key in (r.get("covers") or [])]
        if any(r.get("current") for r in covering):
            continue
        out[key] = {"missing": "; ".join(f"{r['file']}: {r['why']}" for r in covering)
                    or "no listening review covers it",
                    "command": next((r["command"] for r in covering if r.get("command")), "")}
    return out


def score_unheard(card: dict, unheard: dict[str, dict]) -> dict:
    """No current listening evidence, no score: each category in `unheard`
    is recorded not evidenced with its score None (whatever the reviewer
    wrote), named under not_evidenced with what is missing and the command
    that produces it, and kept as `audio_not_evidenced`, so the bar says
    READY cannot be judged for it rather than NOT READY on a frozen number.

    The overall is then the mean of the categories that were scored, not
    the reviewer's: the prompt asks it to leave the unheard ones out, but a
    reviewer that averaged in the 0 it entered (or an old frozen number)
    would otherwise still move the bar and keep-or-revert with a score
    nobody heard. The number it filed is kept as `overall_filed`."""
    card = dict(card)
    card["scores"] = dict(card.get("scores") or {})
    noted = list(card.get("not_evidenced") or [])
    for key, gap in unheard.items():
        card["scores"][key] = None
        noted.append(f"{key}: not evidenced -- no current listening review "
                     f"({gap['missing']}); unscored, so READY cannot be judged for it; "
                     + (f"run {gap['command']}" if gap.get("command")
                        else "nothing in the pipeline produces one yet"))
    card["not_evidenced"] = noted
    card["audio_not_evidenced"] = dict(unheard)
    if unheard:
        card["overall_filed"] = card.get("overall")
        card["overall"] = _mean(card["scores"])
    return card


def contact_sheet(samples: list[dict], frames_dir: Path, out: Path) -> Path:
    """Every still tiled in time order with its timestamp, shot and motion
    verdict, so the reviewer sees the runtime at once. A missing still is an
    empty, labelled tile. A build still of its own is left out (`judged`):
    it is measured, not looked at."""
    from PIL import Image, ImageDraw
    rows = judged(samples)
    cols = max(1, min(len(rows), SHEET_COLUMNS))
    lines = max(1, -(-len(rows) // cols))
    tw, th = SHEET_TILE
    sheet = Image.new("RGB", (cols * tw, lines * (th + SHEET_CAPTION)), (16, 16, 16))
    draw = ImageDraw.Draw(sheet)
    for i, row in enumerate(rows):
        x, y = (i % cols) * tw, (i // cols) * (th + SHEET_CAPTION)
        path = frames_dir / row["frame"]
        placed = False
        if path.is_file():
            try:
                with Image.open(path) as src:
                    im = src.convert("RGB")
                im.thumbnail((tw, th))
                sheet.paste(im, (x + (tw - im.width) // 2, y + (th - im.height) // 2))
                placed = True
            except (OSError, ValueError):
                placed = False
        if not placed:
            draw.text((x + 8, y + th // 2), "no frame", fill=(220, 90, 90))
        mo = row.get("motion") or {}
        caption = f"{_stamp(row['t'])}  shot {row.get('label', '')}  {str(row.get('why', ''))[:36]}"
        if mo.get("steps"):
            caption += f"  | {mo.get('verdict')} {mo.get('mean', 0):.1f}"
        draw.text((x + 6, y + th + 7), caption, fill=(235, 235, 235))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


# --------------------------------------------------------------------------- #
# passes and the bar
# --------------------------------------------------------------------------- #

_PASS = re.compile(r"^pass-(\d+)$")


def _passes(root: Path) -> list[Path]:
    base = root / QA_DIR
    if not base.is_dir():
        return []
    return sorted((p for p in base.iterdir() if p.is_dir() and _PASS.match(p.name)),
                  key=lambda p: int(_PASS.match(p.name).group(1)))


def latest_pass(root: Path) -> Path | None:
    found = _passes(root)
    return found[-1] if found else None


def next_pass(root: Path) -> Path:
    last = latest_pass(root)
    n = int(_PASS.match(last.name).group(1)) + 1 if last else 1
    return root / QA_DIR / f"pass-{n:02d}"


def verdict_of(scores: dict, overall: float, *, blocking: list,
               unheard: dict | None = None) -> tuple[str, list[str]]:
    """The bar of §2, applied mechanically to whatever the reviewer scored.
    A category recorded not evidenced (None) is not under the bar: READY
    cannot be judged for it, and the reason says so (AUTO-105). Where
    `unheard` (the card's `audio_not_evidenced`) says no command produces
    the evidence, as for a wordless film's voiceover, the reason says READY
    is out of reach until a review that hears the rendered track exists,
    so nobody loops on a gap no stage can close."""
    reasons = []
    if overall < OVERALL_BAR:
        reasons.append(f"overall {overall:.1f} is under {OVERALL_BAR}")
    for key in CATEGORIES:
        if key in scores and scores[key] is None:
            gap = (unheard or {}).get(key)
            if gap and not gap.get("command"):
                reasons.append(f"{key} is not evidenced: {gap.get('missing')}; nothing "
                               "in the pipeline produces a listening review for it yet, "
                               "so READY cannot be judged and is out of reach for this "
                               "film until a review that hears the rendered track exists")
                continue
            reasons.append(f"{key} is not evidenced: no current listening review "
                           "heard it, so READY cannot be judged for it")
            continue
        try:
            value = float(scores.get(key, 0))
        except (TypeError, ValueError):
            value = 0.0
        if value < CATEGORY_BAR:
            reasons.append(f"{key} {value:.1f} is under {CATEGORY_BAR}")
    if blocking:
        reasons.append(f"{len(blocking)} blocking item(s)")
    return (READY if not reasons else NOT_READY), reasons


def mechanical_blockers(build: dict, *, gate: dict | None) -> list[str]:
    """What the manifests already prove blocks publication, before anyone looks."""
    out = []
    for label in build.get("placeholders", []):
        out.append(f"placeholder card at shot {label} (disqualifying)")
    for finding in build.get("findings", []):
        if str(finding).startswith("error"):
            out.append(f"build: {finding}")
    for item in (build.get("editorial") or {}).get("blocking", []):
        out.append(f"editor: {item}")
    # A film in the house fallback palette looks like every other film that
    # fell back (AUTO-155). The build only warns; READY is refused here, and
    # the prefix names the stage that owns the fix.
    design = build.get("design") or {}
    if design.get("source") == "fallback":
        why = design.get("fallback") or "source fallback"
        out.append(f"styles: the film is in the house fallback palette ({why}); "
                   "run yt styles --rich, then yt build")
    if gate and gate.get("exit"):
        for row in (gate.get("triage") or {}).get("blocking", []):
            out.append(f"gate: {row.get('what', '')}")
        if not (gate.get("triage") or {}).get("blocking"):
            out.append(f"gate failed (exit {gate.get('exit')})")
    return out


def vfx_blockers(vfx: dict | None, authored: list | None = None) -> list[str]:
    """The vfx continuity breaks that block publication: one line per break
    marked `blocking`, when vfx.json records that the verdict came from a
    reviewer of another family than every author of the film, carried
    sections' authors too (vfx.judged_apart). Sections
    are reviewed alone, so this is the one review that sees a hero change
    size at a section boundary, and READY must not pass over it. A verdict
    by the author's own family, or one with no record of who gave it (a
    vfx.json from before the record), never blocks: doctor warns of it
    instead. A major or minor break, `one_film: false` on its own, and a
    review that was skipped or failed add nothing. `authored` defaults to
    vfx.json's own; the section is named by number when its heading joins
    to one authored section, as written otherwise."""
    from . import vfx as vfx_stage
    cont = (vfx or {}).get("continuity") if isinstance(vfx, dict) else None
    if (not isinstance(cont, dict) or cont.get("skipped") or cont.get("error")
            or not vfx_stage.judged_apart(cont)):
        return []
    authored = (vfx.get("authored") or []) if authored is None else authored
    out = []
    for b in cont.get("breaks") or []:
        if not isinstance(b, dict) or b.get("severity") != "blocking":
            continue
        owner = str(b.get("owner_section") or "?")
        n = vfx_stage.owner_section_n(owner, authored)
        heading = next((str(r.get("section") or "") for r in authored
                        if isinstance(r, dict) and r.get("n") == n), "")
        where = f"section {n} ({heading})" if n is not None else owner
        out.append(f"vfx continuity: {where}: {b.get('problem', '')}")
    return out


def compare(prev: dict | None, cur: dict) -> dict:
    """Pass against pass: per-category deltas and what regressed.

    A category not evidenced (None) on one side has no delta. When the two
    passes did not score the same categories, their overalls average
    different things: a pass that lost its listening evidence drops the
    audio scores out of its mean and its overall rises with nothing in the
    film changed. So the overall delta is then taken between the means of
    the categories both passes scored, and `unscored` names the ones left
    out (AUTO-105). Passes that scored the same categories compare their
    overalls as filed, as before. A pass that is not evidenced (AUTO-107:
    fewer than two valid scoring samples) has no scores to compare, on
    either side."""
    if not prev or NOT_EVIDENCED in (prev.get("verdict"), cur.get("verdict")):
        return {"against": None, "deltas": {}, "overall_delta": 0.0, "regressions": []}
    before_scores, after_scores = prev.get("scores") or {}, cur["scores"]
    deltas = {}
    for key in CATEGORIES:
        before, after = before_scores.get(key, 0), after_scores.get(key, 0)
        if before is None or after is None:
            deltas[key] = None      # not evidenced on one side: nothing to compare
            continue
        try:
            deltas[key] = round(float(after) - float(before), 2)
        except (TypeError, ValueError):
            deltas[key] = 0.0
    out = {"against": prev.get("pass"), "deltas": deltas,
           "overall_delta": round((_number(cur.get("overall")) or 0.0)
                                  - (_number(prev.get("overall")) or 0.0), 2),
           "regressions": [k for k, d in deltas.items()
                           if d is not None and d <= -REGRESSION]}
    unscored = [k for k in CATEGORIES
                if (k in before_scores and before_scores[k] is None)
                != (k in after_scores and after_scores[k] is None)]
    if unscored:
        left_out = dict.fromkeys(unscored)          # None: no part of either mean
        out["overall_delta"] = round(_mean(dict(after_scores) | left_out)
                                     - _mean(dict(before_scores) | left_out), 2)
        out["unscored"] = unscored
    return out


# --------------------------------------------------------------------------- #
# side by side with the previous kept cut (AUTO-108)
# --------------------------------------------------------------------------- #

#: What a pair of stills can bear on: what a picture shows. The audio
#: categories are scored only from a listening review (AUTO-105), so no
#: still pair moves them.
SIDE_CATEGORIES = tuple(k for k in CATEGORIES if k not in AUDIO)
#: A tally entry's result: the new cut against the previous one.
SIDE_RESULTS = ("better", "worse", "same")
#: Under the pass: the pairs the reviewer reads, named only by moment and
#: slot (`m01-A.png`), and the previous cut's stills they were copied from.
SIDE_DIR, PREVIOUS_DIR = "side-by-side", "previous"
#: Where `yt improve` keeps a cut's document before a pass changes it
#: (`improve/<pass>/snapshot/`, `attempt/`, `snapshot-rejudge/`): the copies
#: of the previous kept cut's scene.xml that QA can render it from.
SNAPSHOT_DOCUMENTS = "improve/*/*/scene.xml"

SIDE_SCHEMA = {
    "type": "object",
    "properties": {"moments": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "the moment's id, as listed"},
            "better": {"type": "string", "enum": ["A", "B", "same"],
                       "description": "the cut whose still is better at this moment, "
                                      "or same when neither is"},
            "why": {"type": "string", "description": "what is visibly better, in the "
                                                     "pictures"},
            "bears_on": {"type": "array",
                         "items": {"type": "string", "enum": list(SIDE_CATEGORIES)},
                         "description": "the categories this moment bears on, whatever "
                                        "the answer: where the two differ, or for same, "
                                        "what was compared"}},
        "required": ["id", "better", "why", "bears_on"]}}},
    "required": ["moments"],
}


def compare_name(pass_name: str) -> str:
    """The comparison's decision, `<pass>-compare-pass`, beside the scoring samples'."""
    return f"{pass_name}-compare-pass"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cut_digest(document: bytes, base: Path) -> dict | None:
    """What a cut's pictures are made of: the document's sha256 and that of
    every file it references as the renderer resolves them against `base`,
    the directory the document renders from (`render.reference_files`, the
    still cache's own inputs: textures, images, models and their
    directories, HDRIs, fonts, included documents and what they name),
    keyed by the path relative to `base`. Two cuts with the same digest draw
    the same frames; a copy of a document whose texture was rewritten at the
    same path since would render a cut nobody judged. None when a reference
    cannot be read: what the cut draws is then not known, so it matches
    nothing, not even itself."""
    files = render_stage.reference_files(document.decode("utf-8", "replace"), Path(base))
    if files is None:
        return None
    return {"scene": _sha(document),
            "files": {os.path.relpath(path, base): _sha(data)
                      for path, data in sorted(files.items())}}


def side_order(pass_name: str, count: int) -> list[str]:
    """Which cut is shown as A at each of `count` moments, "previous" or
    "new": half each (the odd one previous), shuffled by a generator seeded
    with the pass's name, so the order varies from moment to moment and
    from pass to pass, and the same pass asks the same question again
    (the inspection's cache holds). Recorded per moment, so how often A
    wins can be checked against how often A was the new cut."""
    order = ["previous" if k % 2 == 0 else "new" for k in range(count)]
    random.Random(f"side-by-side {pass_name}").shuffle(order)
    return order


def tally(entries) -> dict:
    """The tally of comparison entries, from any source (`stills` here; a
    video comparison files the same shape, AUTO-128).

    An entry is `{category, result, source, moment}`, more fields allowed:
    `result` is the new cut against the previous one, better, worse or
    same (anything else counts nowhere); `category` the one it bears on, or
    None for a moment that names none; `moment` the moment it was judged
    at, unique within its `source`. Per category: how many entries were
    better, worse and the same. Under `total`: one vote a moment, so a
    reviewer who names more categories does not outweigh the moments. A
    moment's vote is the result most of its entries give, a tie between
    them same; an entry with no `moment` is a moment of its own."""
    out: dict[str, dict[str, int]] = {}
    votes: dict[object, Counter] = {}
    for k, e in enumerate(entries or []):
        if not isinstance(e, dict) or e.get("result") not in SIDE_RESULTS:
            continue
        if e.get("category") is not None:
            row = out.setdefault(str(e["category"]), dict.fromkeys(SIDE_RESULTS, 0))
            row[e["result"]] += 1
        moment = e.get("moment")
        key = (str(e.get("source")), str(moment)) if moment is not None else k
        votes.setdefault(key, Counter())[e["result"]] += 1
    total = dict.fromkeys(SIDE_RESULTS, 0)
    for said in votes.values():
        top = said.most_common()
        tied = len(top) > 1 and top[0][1] == top[1][1]
        total["same" if tied else top[0][0]] += 1
    out["total"] = total
    return out


def _side(against: str | None, status: str, why: str = "", **more) -> dict:
    """The scorecard's `side_by_side` record: against which pass, whether
    the comparison was asked (`done`), not needed or not possible
    (`skipped`) or failed (`failed`), why, the moments compared and the
    tally entries. A record that is not `done` carries no entries."""
    out = {"against": against, "status": status, "why": why, "decision": None,
           "moments": [], "unpaired": [], "entries": [], "tally": tally([]),
           "picked": {"A": 0, "B": 0, "same": 0}}
    out.update(more)
    return out


def previous_document(home: Path, base: Path, want: dict | None) -> bytes | None:
    """A copy of the previous cut's document that still renders that cut:
    one of `yt improve`'s snapshots (SNAPSHOT_DOCUMENTS) whose sha256 and
    whose includes' sha256, as they are now under `base`, are what the
    previous pass recorded in `cut`. None when the previous pass recorded
    no `cut` (it was scored before AUTO-108, or a reference could not be
    read) or no copy matches."""
    if not isinstance(want, dict) or not want.get("scene"):
        return None
    for path in sorted(Path(home).glob(SNAPSHOT_DOCUMENTS)):
        data = path.read_bytes()
        if _sha(data) == want["scene"] and cut_digest(data, base) == want:
            return data
    return None


#: `yt improve`'s record at the project root (improve.REPORT_JSON; improve
#: imports this module, so the name is repeated here).
IMPROVE_JSON = "improve.json"


def kept_instead(home: Path, latest: dict) -> dict | None:
    """The best kept pass's scorecard when `yt improve` reverted `latest`,
    the latest scored pass: that pass judged a cut that was put back, and
    the cut on disk is the best kept one's, so a plain `yt qa` compares with
    that one, as `yt improve` does with --previous. None when improve.json
    does not say `latest` was reverted, or names no scored best pass."""
    record = read_json(Path(home) / IMPROVE_JSON, warn=False)
    if not isinstance(record, dict) or not isinstance(record.get("best"), dict):
        return None
    reverted = {p.get("qa_pass") for p in record.get("passes") or []
                if isinstance(p, dict) and p.get("decision") == "revert"}
    best = str(record["best"].get("pass") or "")
    if latest.get("pass") not in reverted or not best or best == latest.get("pass"):
        return None
    card = read_json(Path(home) / QA_DIR / best / "scorecard.json", warn=False)
    if not isinstance(card, dict) or card.get("verdict") == NOT_EVIDENCED:
        return None
    return card | {"pass": card.get("pass") or best}


def side_by_side(args, reviewer: Harness, pass_dir: Path, previous: dict | None, *,
                 home: Path, scene: str, samples: list[dict], cut: dict | None,
                 script: dict, quiet: bool = True) -> dict:
    """The pass against the previous kept cut, moment by moment (AUTO-108).

    Each sampled still of this pass is paired with the previous cut's still
    at the same timestamp: the previous pass's own still when it sampled
    that moment (`reused`), else rendered by RENDERER from a copy of the
    previous cut's document placed beside this one (`rendered`, only when
    `previous_document` finds one whose referenced files are unchanged),
    else the moment is `unpaired` with the reason. The pairs are laid out
    as `<id>-A.png` and `<id>-B.png`, the cut at A chosen by `side_order`,
    in a temporary directory outside the pass, and one inspection there,
    `<pass>-compare-pass`, says per moment which is better (A, B or same),
    why, and the categories it bears on; the pairs are then kept under
    SIDE_DIR. Its prompt names no pass, score or finding, and nothing says
    which cut is the new one. Each answered moment becomes one tally entry
    per category it bears on, or one with category None when it names
    none: better when the new cut won, worse when the previous one did,
    same otherwise. A failed inspection is a failed engine row, billed, and
    leaves no entries, so `yt improve` decides on the score delta alone."""
    if previous is None:
        return _side(None, "skipped", "no earlier scored pass to compare with")
    against = str(previous.get("pass"))
    before = previous.get("cut")
    # Only two full digests say the same cut: a pass from before AUTO-108
    # recorded the document alone, which says nothing of its textures.
    if before and cut and before == cut:
        return _side(against, "skipped", f"the same cut as {against}: the document and "
                     "every file it references are unchanged, so every still is the "
                     "same picture")
    prev_frames = home / QA_DIR / against / "frames"
    frames = pass_dir / "frames"
    out_dir = pass_dir / PREVIOUS_DIR
    rows, todo, unpaired = [], [], []
    for row in judged(samples):
        if not (frames / row["frame"]).is_file():
            continue                     # this pass has no still there: nothing to pair
        if (prev_frames / row["frame"]).is_file():
            out_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(prev_frames / row["frame"], out_dir / row["frame"])
            rows.append((row, "reused"))
        else:
            todo.append(row)
    if todo:
        base = Path(scene).parent
        document = previous_document(home, base, before)
        if document is None:
            why = (f"{against} recorded no cut digest (scored before AUTO-108, or a file "
                   "its document references could not be read), so no copy of its "
                   "document can be checked" if not before else
                   f"no copy of {against}'s document whose referenced files are "
                   f"unchanged is on disk ({SNAPSHOT_DOCUMENTS})")
            unpaired += [{"t": r["t"], "label": r.get("label", ""), "why": why} for r in todo]
        else:
            # Beside scene.xml, so its includes resolve as they did; removed
            # after, so nothing else ever reads it as the project's document.
            copy = base / f".{pass_dir.name}-previous.scene.xml"
            copy.write_bytes(document)
            try:
                render = RENDERER(args, str(copy))
                out_dir.mkdir(parents=True, exist_ok=True)
                for row in todo:
                    still = out_dir / row["frame"]
                    code = render({"still": row["t"], "out": str(still)})
                    if code or not still.is_file():
                        unpaired.append({"t": row["t"], "label": row.get("label", ""),
                                         "why": f"the previous cut's still did not render "
                                                f"(engine exited {code})"})
                    else:
                        rows.append((row, "rendered"))
            finally:
                copy.unlink(missing_ok=True)
    rows.sort(key=lambda r: r[0]["t"])
    if not rows:
        return _side(against, "skipped", "no moment could be paired with the previous cut",
                     unpaired=unpaired)

    shows = {s.get("label"): s.get("shows") for sec in script.get("sections") or []
             for s in sec.get("shots") or [] if isinstance(s, dict)}
    moments, listed = [], []
    with tempfile.TemporaryDirectory(prefix="side-by-side-") as tmp:
        # The reviewer reads the pairs from a directory outside the pass:
        # beside frames/ and previous/, an agent listing `..` could match A
        # and B to the cuts by name or bytes and learn which one is new.
        pairs = Path(tmp)
        for k, ((row, how), first) in enumerate(
                zip(rows, side_order(pass_dir.name, len(rows))), 1):
            mid = f"m{k:02d}"
            order = {"A": first, "B": "new" if first == "previous" else "previous"}
            for slot, cut_name in order.items():
                source = (frames if cut_name == "new" else out_dir) / row["frame"]
                shutil.copyfile(source, pairs / f"{mid}-{slot}.png")
            moments.append({"id": mid, "t": row["t"], "label": row.get("label", ""),
                            "frame": row["frame"], "order": order, "previous": how})
            listed.append({"id": mid, "timestamp": row["t"], "shot": row.get("label", ""),
                           "files": [f"{mid}-A.png", f"{mid}-B.png"],
                           **({"shows": str(shows[row.get("label")])[:300]}
                              if shows.get(row.get("label")) else {})})
        prompt = "\n\n".join([
            "Side by side: each moment below is the same timestamp of a film in two cuts, "
            "A and B, as two stills in this directory (`<id>-A.png` and `<id>-B.png`). "
            "Read both stills of every moment and say which is better by professional "
            "standards (A, B, or same when neither is), why, in what the pictures show, "
            "and which of these categories the moment bears on, whatever the answer "
            "(what differs, or for same, what you compared): "
            f"{', '.join(SIDE_CATEGORIES)}. Which cut is A changes from moment to moment "
            "and says nothing about either; judge the pictures only. Answer every moment "
            "by its id.",
            brief("Moments", listed)])
        name = compare_name(pass_dir.name)
        record = dict(decision=name, moments=moments, unpaired=unpaired)
        try:
            answer = reviewer.inspect(name, prompt, SIDE_SCHEMA, cwd=pairs)
            failed = None
        except HarnessError as exc:
            failed = exc
        # The pairs are kept with the pass, for whoever checks the answers.
        side = pass_dir / SIDE_DIR
        shutil.rmtree(side, ignore_errors=True)
        shutil.copytree(pairs, side)
    if failed is not None:
        if not quiet:
            print(f"[qa] {name} failed ({failed}); no side-by-side tally", file=sys.stderr)
        return _side(against, "failed", str(failed), **record)
    said = {}
    for a in answer.get("moments") or [] if isinstance(answer, dict) else []:
        if isinstance(a, dict) and str(a.get("id")) not in said:
            said[str(a.get("id"))] = a
    entries, picked = [], {"A": 0, "B": 0, "same": 0}
    for m in moments:
        a = said.get(m["id"])
        if not a or a.get("better") not in picked:
            m["unjudged"] = True
            continue
        picked[a["better"]] += 1
        result = ("same" if a["better"] == "same" else
                  "better" if m["order"][a["better"]] == "new" else "worse")
        bears = [c for c in dict.fromkeys(a.get("bears_on") or []) if c in SIDE_CATEGORIES]
        m.update(better=a["better"], result=result, why=str(a.get("why", "")),
                 bears_on=bears)
        # One entry per category the moment bears on; a moment that names
        # none still files one, category None, so it is counted in `total`.
        entries += [{"category": c, "result": result, "source": "stills",
                     "moment": m["id"], "t": m["t"]} for c in bears or [None]]
    return _side(against, "done", **record, entries=entries, tally=tally(entries),
                 picked=picked)


def side_lines(side: dict | None) -> list[str]:
    """findings.md's side-by-side section."""
    if not isinstance(side, dict):
        return []
    lines = ["## Side by side", ""]
    unpaired = [f"- {_stamp(u['t'])} shot {u.get('label', '')}: not compared -- "
                f"{u.get('why', '')}" for u in side.get("unpaired") or []]
    if side.get("status") != "done":
        lines += [f"{side.get('status', 'skipped').capitalize()}: {side.get('why', '')}", ""]
        return lines + (unpaired + [""] if unpaired else [])
    total = (side.get("tally") or {}).get("total") or {}
    if side.get("source") == "video":
        return lines + video_lines(side, total)
    lines += [f"Against {side.get('against')}, the previous kept cut, at the same "
              f"timestamps: the new cut better at {total.get('better', 0)} moments, worse "
              f"at {total.get('worse', 0)}, the same {total.get('same', 0)} times (one "
              "vote a moment; the table counts each category a moment bears on).", "",
              "| Category | Better | Worse | Same |", "|---|---|---|---|"]
    for key, row in (side.get("tally") or {}).items():
        if key != "total":
            lines.append(f"| {key} | {row['better']} | {row['worse']} | {row['same']} |")
    lines.append("")
    for m in side.get("moments") or []:
        lines.append(f"- {_stamp(m['t'])} shot {m.get('label', '')}: "
                     + (f"{m['result']} ({', '.join(m.get('bears_on') or []) or 'no category'})"
                        f" -- {m.get('why', '')} [A was the {m['order']['A']} cut, "
                        f"previous still {m['previous']}]"
                        if m.get("result") else "not answered"))
    return lines + unpaired + [""]


def video_lines(side: dict, total: dict) -> list[str]:
    """The side-by-side section when the two cuts were watched whole
    (AUTO-128): per category, both orders' answers and the result."""
    against = side.get("against") or side.get("file")
    lines = [f"Video comparison against {against}, the previous kept cut ({side.get('file')}): "
             f"both cuts watched whole by {side.get('model')}, once in each order. The new "
             f"cut better in {total.get('better', 0)} categories, worse in "
             f"{total.get('worse', 0)}, the same in {total.get('same', 0)}; overall "
             f"{side.get('overall')}. A category the two orders call for different cuts is "
             f"the same; one either could not tell is left out ({side.get('comparison')}).",
             "", "| Category | Result | Cut A first | Cut B first | Timestamps |",
             "|---|---|---|---|---|"]
    for row in side.get("criteria") or []:
        stamps = ", ".join(_stamp(t) for t in row.get("timestamps") or [])
        lines.append(f"| {row['category']} | {row['result'] or 'cannot tell'} | "
                     f"{row['ab']['said']} ({row['ab']['cut'] or '-'}) | "
                     f"{row['ba']['said']} ({row['ba']['cut'] or '-'}) | {stamps} |")
    return lines + [""]


# --------------------------------------------------------------------------- #
# the previous findings, rechecked after the blind scoring (AUTO-106)
# --------------------------------------------------------------------------- #

#: The recheck's decision is `<pass>` + this, asked after the scoring samples.
RECHECK_SUFFIX = "-recheck"
#: At most this many of the previous pass's findings are put to the recheck,
#: in the order that pass filed them (the cap the scoring brief once used);
#: the rest are counted under `unchecked`.
RECHECK_MAX = 20
#: A previous finding in this pass's stills: still visibly wrong, visibly
#: fixed, or not shown by any still (or not a thing a still can show).
RECHECK_STATUSES = ("stands", "gone", "cannot_tell")

RECHECK_SCHEMA = {
    "type": "object",
    "properties": {"findings": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "the finding's id, as listed"},
            "status": {"type": "string", "enum": list(RECHECK_STATUSES),
                       "description": "stands when the stills still show it, gone when "
                                      "they show it fixed, cannot_tell when no still "
                                      "shows that moment"},
            "why": {"type": "string", "description": "what the stills show, naming the "
                                                     "frame read"}},
        "required": ["id", "status", "why"]}}},
    "required": ["findings"],
}


def recheck_name(pass_name: str) -> str:
    """The recheck's decision, `<pass>-recheck`, beside the scoring samples'."""
    return f"{pass_name}{RECHECK_SUFFIX}"


def recheck_rows(previous: dict) -> tuple[list[dict], int]:
    """The previous pass's findings as the recheck lists them, `P01`.., and
    how many past RECHECK_MAX are left unchecked. Only what places and
    states a finding is carried (time, problem, owner): never a score, an
    overall, a verdict or a severity, so the checklist cannot become the
    previous scorecard again. The earlier pass's frame name is left out too:
    it names a still of the earlier cut's frames/, and the recheck reads
    this pass's, which may have sampled other times."""
    found = [f for f in previous.get("findings") or [] if isinstance(f, dict)]
    rows = [{"id": f"P{n:02d}", "timestamp": f.get("timestamp"),
             "problem": str(f.get("problem", ""))[:400], "owner": f.get("owner")}
            for n, f in enumerate(found[:RECHECK_MAX], 1)]
    return rows, max(len(found) - RECHECK_MAX, 0)


def _recheck(against: str | None, status: str, why: str = "", **more) -> dict:
    """The scorecard's `recheck` record: against which pass, whether the
    decision was asked (`done`), not needed (`skipped`) or failed (`failed`),
    why, each previous finding with its answer, and the tally."""
    out = {"against": against, "status": status, "why": why, "decision": None,
           "findings": [], "unchecked": 0,
           "tally": dict.fromkeys(RECHECK_STATUSES, 0)}
    out.update(more)
    return out


def recheck(reviewer: Harness, pass_dir: Path, previous: dict | None, *,
            quiet: bool = True) -> dict:
    """The previous pass's findings as a checklist, `--recheck` (AUTO-106).

    Asked as its own inspection, `<pass>-recheck`, in the pass directory
    after the scoring samples have filed their scorecards, so nothing it
    says reaches a score: the scoring prompt names no previous pass, and
    this decision's answers are filed under `recheck`, beside the scores,
    and read by nothing that scores. Each finding (`recheck_rows`) is
    answered stands, gone or cannot_tell, with why. An answer it leaves out
    is recorded `unanswered`; a failed inspection is a failed engine row,
    billed, and answers nothing. With no earlier scored pass, or one that
    filed no findings, nothing is asked."""
    if previous is None:
        return _recheck(None, "skipped", "no earlier scored pass to recheck")
    against = str(previous.get("pass"))
    rows, unchecked = recheck_rows(previous)
    if not rows:
        return _recheck(against, "skipped", f"{against} filed no findings to recheck")
    prompt = "\n\n".join([
        "Recheck: each finding below was filed on an earlier cut of this film. Find "
        "this cut's stills for each finding by its timestamp: they are under "
        "`frames/`, each named for its time, and the contact sheet tiles them in "
        "time order. Read those at and around it and say whether what the finding "
        "describes is still visibly wrong in this cut: stands when it is, gone when "
        "the stills show it fixed, cannot_tell when no still shows that moment or a "
        "still cannot show it. Say why in what the stills show, naming the frame of "
        "this cut you read. This is a checklist filed beside a scorecard already "
        "written: it scores nothing. Answer every finding by its id.",
        brief("Findings filed on the earlier cut", rows)])
    name = recheck_name(pass_dir.name)
    try:
        answer = reviewer.inspect(name, prompt, RECHECK_SCHEMA, cwd=pass_dir)
    except HarnessError as exc:
        if not quiet:
            print(f"[qa] {name} failed ({exc}); nothing rechecked", file=sys.stderr)
        return _recheck(against, "failed", str(exc), decision=name,
                        findings=[dict(r, status=None, why="", unanswered=True)
                                  for r in rows], unchecked=unchecked)
    said: dict[str, dict] = {}
    for a in answer.get("findings") or [] if isinstance(answer, dict) else []:
        if isinstance(a, dict) and str(a.get("id")) not in said:
            said[str(a.get("id"))] = a
    out, counts = [], dict.fromkeys(RECHECK_STATUSES, 0)
    for r in rows:
        a = said.get(r["id"]) or {}
        status = a.get("status") if a.get("status") in RECHECK_STATUSES else None
        if status:
            counts[status] += 1
            out.append(dict(r, status=status, why=str(a.get("why", ""))))
        else:
            out.append(dict(r, status=None, why="", unanswered=True))
    return _recheck(against, "done", decision=name, findings=out, unchecked=unchecked,
                    tally=counts)


def recheck_lines(record: dict | None) -> list[str]:
    """findings.md's recheck section, present only when --recheck asked."""
    if not isinstance(record, dict):
        return []
    lines = ["## Previous findings, rechecked", ""]
    if record.get("status") != "done":
        return lines + [f"{str(record.get('status', 'skipped')).capitalize()}: "
                        f"{record.get('why', '')}", ""]
    t = record.get("tally") or {}
    lines += [f"{record.get('against')}'s findings in this cut's stills, asked after the "
              f"scoring and scoring nothing: {t.get('stands', 0)} stand, "
              f"{t.get('gone', 0)} gone, {t.get('cannot_tell', 0)} cannot tell"
              + (f"; {record['unchecked']} more not asked" if record.get("unchecked")
                 else "") + ".", ""]
    for row in record.get("findings") or []:
        lines.append(f"- {_stamp(row.get('timestamp'))} "
                     f"{row.get('status') or 'not answered'} -- {row.get('problem', '')}"
                     + (f" ({row['why']})" if row.get("why") else ""))
    return lines + [""]


def findings_md(name: str, card: dict, samples: list[dict], diff: dict, *,
                reasons: list[str]) -> str:
    stamp = _stamp
    lines = [f"# {name}", "", f"**Verdict:** {card.get('verdict', '?')}  ",
             f"**Overall:** {card.get('overall', '?')}", ""]
    if reasons:
        lines += ["Why not ready:", *[f"- {r}" for r in reasons], ""]
    if card.get("capped"):
        lines += [f"**Capped:** {', '.join(card['capped'])} held at {MECHANICAL_CAP:g} "
                  "while a blocking mechanical or rhythm finding stands", ""]
    scoring = card.get("scoring") or {}
    if scoring:
        lines += [f"**Scored from:** the median of {scoring.get('valid', 0)} valid of "
                  f"{scoring.get('asked', 0)} samples; the spread is the samples' max - min, "
                  "and a change smaller than it is no change", ""]
    spread = card.get("spread") or {}
    lines += ["| Category | Score | Spread | Δ vs previous |", "|---|---|---|---|"]
    for key in CATEGORIES:
        delta = diff.get("deltas", {}).get(key)
        score = card.get("scores", {}).get(key, "?")
        lines.append(f"| {key} | {'not evidenced' if score is None else score} | "
                     f"{spread.get(key, '')} | "
                     f"{'' if delta is None else f'{delta:+.1f}'} |")
    lines.append("")
    if diff.get("regressions"):
        lines += ["**Regressions:** " + ", ".join(diff["regressions"]), ""]
    lines += side_lines(card.get("side_by_side"))
    lines += recheck_lines(card.get("recheck"))

    def finding_lines(row: dict) -> list[str]:
        seen_in = (f" (samples {', '.join(map(str, row['samples']))})"
                   if row.get("samples") else "")
        if row.get("claim"):          # a `blocking` item with nothing but its words
            return [f"### blocking claim — {row.get('problem', '')}{seen_in}", ""]
        return [f"### {stamp(row.get('timestamp'))} — {row.get('problem', '')}"
                f" [{row.get('severity', '?')}]{seen_in}",
                f"- frame: {row.get('frame', '')}",
                f"- why: {row.get('why', '')}",
                f"- root cause: {row.get('root_cause', '')}",
                f"- owner: **{row.get('owner', '?')}** — fix: {row.get('fix', '')}", ""]
    lines += ["## Findings", ""]
    if not card.get("findings"):
        lines.append("None reported.")
    for row in card.get("findings", []):
        lines += finding_lines(row)
    if card.get("low_confidence"):
        lines += ["## Low confidence", "",
                  f"Reported by one sample of {scoring.get('valid', '?')}: listed, not "
                  "routed, blocking nothing.", ""]
        for row in card["low_confidence"]:
            lines += finding_lines(row)
    story = card.get("story")
    if isinstance(story, dict):
        lines += ["## Story", "", "As the film told it, not as the screenplay declares it.", "",
                  f"- premise: {story.get('premise_restated', '')}"]
        for row in story.get("sections") or []:
            if isinstance(row, dict):
                lines.append(f"- at the end of section {row.get('section', '?')}: "
                             f"{row.get('viewer_has_at_end', '')}")
        lines += [f"- predicted satisfaction: {story.get('satisfaction', '?')} -- "
                  f"{story.get('satisfaction_why', '')} (a ranking proxy, scoring nothing)",
                  ""]
    if card.get("blocking"):
        lines += ["## Blocking", *[f"- {b}" for b in card["blocking"]], ""]
    if card.get("not_evidenced"):
        lines += ["## Not evidenced by this pass",
                  *[f"- {n}" for n in card["not_evidenced"]], ""]
    if card.get("listening"):
        lines += ["## Listening evidence", "",
                  f"{', '.join(AUDIO)} are scored only from a review that heard what "
                  "the film carries; a stale or absent one scores nothing.", ""]
        for row in card["listening"]:
            covers = ", ".join(row.get("covers") or [])
            if row.get("current"):
                lines.append(f"- {row.get('file')} ({covers}): current, heard "
                             f"{str(row.get('heard') or '')[:12]} -- {row.get('review')}")
            else:
                lines.append(f"- {row.get('file')} ({covers}): not used -- {row.get('why')}"
                             + (f"; run {row['command']}" if row.get("command") else ""))
        lines.append("")
    if card.get("watch"):
        from . import watch as watch_pass   # AUTO-127; absent without --watch
        lines += watch_pass.findings_lines(card)
    lines += ["## Mechanical", "",
              "Measured from the stills, the bursts, the engine's diagnostics and the "
              f"film's rhythm before the review; {CAP_RULE}.",
              ""]
    if card.get("proof_note"):
        lines += [f"Note: {card['proof_note']}", ""]
    if not card.get("mechanical"):
        lines.append("None found.")
    for row in card.get("mechanical") or []:
        lines += [f"- {stamp(row.get('timestamp'))} shot {row.get('shot', '')} "
                  f"[{row.get('severity', '?')}] {row.get('kind', '')}: "
                  f"{row.get('problem', '')} — owner **{row.get('owner', '?')}** — "
                  f"fix: {row.get('fix', '')}"]
    lines.append("")
    if card.get("rhythm"):
        lines += rhythm_lines(card["rhythm"])
    lines += ["## Evidence sampled", ""]
    for row in samples:
        line = (f"- {stamp(row['t'])} shot {row.get('label', '')} "
                f"({row.get('section', '')}): {row.get('why', '')}")
        st = row.get("still") or {}
        if st:
            line += (f" — still: luma {st.get('mean', 0):.0f}, spread {st.get('spread', 0):.1f}, "
                     f"clipped {st.get('clipped_share', 0):.0%}")
        mo = row.get("motion") or {}
        burst = row.get("burst") or {}
        if burst:
            steps = "/".join(f"{s:.1f}" for s in mo.get("steps", []))
            line += (f" — burst: {mo.get('frames', 0)} of {len(burst.get('frames', []))} "
                     f"frames over {burst.get('span', BURST_SECONDS)}s, steps {steps or '-'} "
                     f"({mo.get('verdict', 'not rendered')})")
        lines.append(line)
    if scoring.get("samples"):
        lines += ["", "## Scoring samples", ""]
        for row in scoring["samples"]:
            state = ("valid" if row.get("valid") else
                     f"failed: {row.get('failed')}" if row.get("failed") else "not valid")
            lines.append(f"- {row.get('name')}: {state}"
                         + (f", overall {row.get('overall')}, {row.get('findings', 0)} "
                            "finding(s)" if row.get("valid") else "")
                         + (", asked again" if row.get("again") else "")
                         + (f" (that failed: {row['again_failed']})"
                            if row.get("again_failed") else ""))
        if (scoring.get("merge") or {}).get("failed"):
            lines.append(f"- findings unmerged: {scoring['merge']['failed']}")
    lines += ["", "## Rationale", "", str(card.get("rationale", "")), ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# rendering the evidence
# --------------------------------------------------------------------------- #

def engine_renderer(args, scene: str):
    """The engine on the render host, through `yt render`'s own contract: a
    job is `{"still": T}` or `{"frames": "A..B"}` plus `out`, the file (or
    `%04d` pattern) to write. The stills job goes to the fast host."""
    def render(job: dict) -> int:
        return render_stage.run(argparse.Namespace(
            **vars(args) | {"scene": scene, "video_out": job["out"],
                            "quality": args.quality, "start": None, "end": None,
                            "still": job.get("still"), "frames": job.get("frames"),
                            "strict": False, "parallel": None, "validate": False,
                            "output": None, "audio_only": False, "slices": None,
                            "quiet": True}))
    return render


#: The seam: what `run` renders frames with. A test puts a fake here.
RENDERER = engine_renderer


def engine_geometry(args, project, scene: str, shots: list[dict]) -> dict:
    """The geometry check over the project's scene at `geometry.STEP`
    steps through every shot, evaluated by the engine on the host a
    validation would go to (`--host`, `--local` honoured): one job."""
    return geo.check_project(Path(scene), project.root, shots,
                             host=render_stage.remote_host(args, "validate"))


#: The seam: what `run` checks the geometry with. A test puts a fake here.
GEOMETRY = engine_geometry


def scene_digest(data: bytes) -> str:
    """sha256 of the scene document QA judged; doctor compares it to scene.xml."""
    return hashlib.sha256(data).hexdigest()


def render_frames(args, project, scene: str, samples: list[dict],
                  frames_dir: Path, *, render=None) -> list[str]:
    """One job per sample: its burst window as a single `--frames A..B`
    render, by the engine, from the document the encode uses; the midpoint
    still is copied out of the window, and rendered on its own only when the
    window did not come back. A frame already there is not rendered again.
    Returns the problems, one per job or frame that did not come back."""
    render = render or RENDERER(args, scene)
    frames_dir.mkdir(parents=True, exist_ok=True)
    problems = []
    for row in samples:
        still = frames_dir / row["frame"]
        burst = row.get("burst") or {}
        frames = list(burst.get("frames") or [])
        files = [frames_dir / name for name in burst.get("files") or []]
        window = burst.get("range") or (f"{frames[0]}..{frames[-1] + 1}" if frames else "")
        if files and window and not all(p.is_file() for p in files):
            files[0].parent.mkdir(parents=True, exist_ok=True)
            code = render({"frames": window,
                           "out": str(frames_dir / burst.get("pattern", "burst/%04d.png"))})
            missing = [p.name for p in files if not p.is_file()]
            if code or missing:
                problems.append(f"burst {window} for shot {row['label']}: engine exited "
                                f"{code}, {len(missing)} of {len(files)} frames missing")
        if still.is_file():
            continue
        mid = burst.get("still_frame")
        source = files[frames.index(mid)] if mid in frames else None
        if source is not None and source.is_file():
            shutil.copyfile(source, still)
            continue
        still.parent.mkdir(parents=True, exist_ok=True)       # frames/build/ (AUTO-145)
        code = render({"still": row["t"], "out": str(still)})
        if code or not still.is_file():
            problems.append(f"no frame at {row['t']}s (shot {row['label']}): "
                            f"engine exited {code}")
    return problems


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

#: build.json's sound measures handed to the scorecard when it has them.
BUILD_SOUND_MEASURES = ("beat_sync", "cue_anchors")


def placed_cue_times(build: dict | None) -> dict[str, float]:
    """cue id -> where `yt build` placed it (build.json's cue_anchors), for a
    sheet spotted with anchors; empty for one without."""
    rows = (build or {}).get("cue_anchors")
    return {str(r.get("cue")): r.get("placed_at") for r in rows
            if isinstance(r, dict) and r.get("placed_at") is not None} \
        if isinstance(rows, list) else {}


def sound_cue_rows(sound: dict | None, build: dict | None) -> list[dict]:
    """The cue sheet as the scorecard reviewer is shown it: each cue's place,
    length, kind, level and motif, the place being where the build put it
    when build.json records that and the sheet's `at` otherwise."""
    placed = placed_cue_times(build)
    return [{"at": placed.get(str(c.get("cue_id")), c.get("at")),
             "seconds": c.get("seconds"), "kind": c.get("kind"),
             "level": c.get("level"), "motif": c.get("motif")}
            for c in (sound or {}).get("cues", [])][:80]


def narration_measurements(voice: dict) -> dict:
    """What voice.json says the narration is. A wordless film says so (and how
    many descriptive captions it carries), so no reviewer reads engine "none"
    as a narration that failed to record. The director's take review is not
    among them: it is listening evidence, handed over only while it heard
    the takes the film carries (`listening_evidence`)."""
    out = {k: voice.get(k) for k in ("engine", "voice", "timing", "drift_seconds",
                                     "too_fast_for_captions", "overlaps", "casting")}
    if voice.get("wordless"):
        out["wordless"] = True
        out["descriptive_captions"] = (voice.get("descriptive_captions") or {}).get("cues")
    return out


def _placed(script: dict, build: dict) -> dict[str, float | None]:
    """Each screenplay shot's start on the film: where build.json placed it,
    else where the screenplay timed it, else None."""
    placed = {str(s.get("label")): s.get("at") for s in (build or {}).get("shots") or []
              if isinstance(s, dict)}
    out: dict[str, float | None] = {}
    for sec in (script or {}).get("sections") or []:
        for shot in sec.get("shots") or []:
            label = str(shot.get("label", ""))
            at = placed.get(label, shot.get("at"))
            try:
                out[label] = round(float(at), 3) if at is not None else None
            except (TypeError, ValueError):
                out[label] = None
    return out


def story_brief(script: dict, build: dict) -> dict | None:
    """The screenplay's story as the plan declared it (AUTO-90), placed on
    the film's timeline: the train, the promise and its payoff line, the
    stakes, the question ledger with the time each question's section opens
    and the time its answering section does, the re-hook shots and each
    section's escalation. It is the writer's self-report, which the
    reviewer checks against the narration and the frames; a screenplay
    written before the story fields has none, and None says so."""
    script = script or {}
    ending = script.get("ending") if isinstance(script.get("ending"), dict) else {}
    if not (any(script.get(k) for k in ("train", "promise", "payoff", "stakes", "questions"))
            or any(ending.values())):
        return None
    at = _placed(script, build)
    sections = [s for s in script.get("sections") or [] if isinstance(s, dict)]
    starts = {}
    for n, sec in enumerate(sections, 1):
        times = [at.get(str(shot.get("label", ""))) for shot in sec.get("shots") or []]
        starts[n] = next((t for t in times if t is not None), None)

    def start(n):
        return starts.get(n) if isinstance(n, int) and n > 0 else None

    questions = [{"id": q.get("id", ""), "text": q.get("text", ""),
                  "opened_in": q.get("opened_in"), "opened_at": start(q.get("opened_in")),
                  "closed_in": q.get("closed_in"), "closed_at": start(q.get("closed_in")),
                  "left_open_why": q.get("left_open_why", "")}
                 for q in script.get("questions") or [] if isinstance(q, dict)]
    rehooks = [{"shot": shot.get("label", ""), "at": at.get(str(shot.get("label", ""))),
                "section": n, "question": sec.get("rehook", ""),
                "narration": " ".join(str(shot.get("narration", "")).split())[:300]}
               for n, sec in enumerate(sections, 1) for shot in sec.get("shots") or []
               if shot.get("function") == "rehook"]
    return {
        "train": script.get("train", ""), "promise": script.get("promise", ""),
        "payoff": script.get("payoff", ""), "stakes": script.get("stakes", ""),
        "viewer_stake": script.get("viewer_stake", ""),
        "payoff_line": ending.get("payoff_line", ""), "callback": ending.get("callback", ""),
        "questions": questions, "rehooks": rehooks,
        "sections": [{"section": n, "heading": sec.get("heading", ""), "starts_at": starts[n],
                      "opens": sec.get("opens", []), "closes": sec.get("closes", []),
                      "rehook": sec.get("rehook", ""), "escalation": sec.get("escalation", "")}
                     for n, sec in enumerate(sections, 1)],
    }


def narration_text(script: dict, build: dict, voice: dict | None = None, *,
                   later: bool = False) -> str:
    """The film's whole narration, one shot a line with its label and its
    time on the film, for `narration.txt` in the pass: what the film says,
    read apart from what it shows (attention 23). The words are the voiced
    takes' when voice.json has any, since that is the soundtrack and a shot
    with no take is silent on it; the screenplay's lines stand in only
    before anything was voiced, and the first line says which it is.
    `later` says the file was written at a later review than the frames,
    for a reused pass that had none. "" for a screenplay with no shots."""
    at = _placed(script, build)
    takes = {str(t.get("label")): t for t in (voice or {}).get("takes") or []
             if isinstance(t, dict)}
    lines = []
    for sec in (script or {}).get("sections") or []:
        for shot in sec.get("shots") or []:
            label = str(shot.get("label", ""))
            words = (takes.get(label) or {}).get("text", "") if takes else shot.get("narration", "")
            lines.append(f"{label}  {_stamp(at.get(label))}  {spoken(words) or SILENT_LINE}")
    if not lines:
        return ""
    source = ("as voiced (voice.json's takes)" if takes else
              "as the screenplay writes it (not yet voiced)")
    when = (" -- written at a later review than the frames, from the files as they "
            "are now" if later else "")
    return (f"# The narration {source}, shot by shot: label, time on the film, the line"
            f"{when}\n" + "\n".join(lines) + "\n")


def story_gaps(card: dict, script: dict) -> list[str]:
    """Where the scorecard's `story.sections` is not one non-blank line per
    screenplay section (structure 20: a blank line fails), section by
    section; [] when it is, or when the screenplay has no sections to
    count."""
    count = len([s for s in (script or {}).get("sections") or [] if isinstance(s, dict)])
    story = card.get("story")
    if not count or not isinstance(story, dict):
        return []
    lines: dict[int, list[str]] = {}
    gaps = []
    for row in story.get("sections") or []:
        if not isinstance(row, dict):
            continue
        try:
            n = int(row.get("section"))
        except (TypeError, ValueError):
            gaps.append(f"a line names section {row.get('section')!r}")
            continue
        lines.setdefault(n, []).append(str(row.get("viewer_has_at_end") or "").strip())
    for n in range(1, count + 1):
        said = lines.get(n)
        if not said:
            gaps.append(f"section {n} has no line")
        elif len(said) > 1:
            gaps.append(f"section {n} has {len(said)} lines")
        elif not said[0]:
            gaps.append(f"section {n}'s line is blank")
    gaps += [f"section {n} is not one of the screenplay's {count}"
             for n in sorted(lines) if not 1 <= n <= count]
    return gaps


def unproven_satisfaction(card: dict, *, retention: bool) -> dict:
    """A predicted satisfaction is a ranking proxy (judging 4): until the
    channel's RETENTION exists nothing validates it, so it stays recorded in
    `story` and is named under not_evidenced with the reason. The channel's
    is the one in the work directory itself; the per-film one `yt
    retention` writes in a project (AUTO-101) measures one cut, not the
    channel, and does not count."""
    if retention or not isinstance(card.get("story"), dict):
        return card
    card = dict(card)
    card["not_evidenced"] = list(card.get("not_evidenced") or []) + [
        f"story.satisfaction: a predicted satisfaction is a ranking proxy (judging 4) "
        f"that nothing validates until the work directory's own {RETENTION} "
        f"(work/{RETENTION}, not a film's own from `yt retention`) holds the channel's "
        "measured retention; recorded, scoring nothing"]
    return card


def _positive(text: str) -> int:
    """--frames: a count of stills, at least one."""
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if n < 1:
        raise argparse.ArgumentTypeError("at least one still")
    return n


def ask_samples(reviewer: Harness, pass_dir: Path, prompt: str, script: dict, *,
                samples: int | None = None, quiet: bool = True,
                watched: bool = False) -> list[dict]:
    """The scorecard asked `samples` (QA_SAMPLES) times on the same evidence:
    `<pass>-scorecard-s<k>`, each with the categories in `category_order(k)`.
    A sample whose `story.sections` is not one line per screenplay section
    (structure 20) is asked once more as `-again`; if that call fails, the
    first answer stands with its gaps (its scores were answered; only its
    story lines are short) and the row records `again_failed`. A sample
    whose own call fails is not valid; the harness's report keeps its failed
    row, so it is billed and never read as a score. Each sample's form is
    removed before the next is asked, so every sample reads the same
    files. With `watched` (AUTO-127) each sample also answers `watched`."""
    form = "scorecard.form.json"
    rows = []
    for k in range(1, (QA_SAMPLES if samples is None else samples) + 1):
        name, order = f"{pass_dir.name}-scorecard-s{k}", category_order(k)
        schema = sample_schema(k, watched=watched)
        ask = prompt + f"\n\nScore the categories in this order: {', '.join(order)}."
        row: dict = {"name": name, "k": k, "order": list(order), "valid": False,
                     "again": False}
        (pass_dir / form).unlink(missing_ok=True)
        try:
            card = reviewer.inspect(name, ask, schema, cwd=pass_dir, form=form)
            gaps = story_gaps(card, script)
            if gaps:
                if not quiet:
                    print(f"[qa] {name}: asking again ({'; '.join(gaps)})", file=sys.stderr)
                row["again"] = True
                (pass_dir / form).unlink(missing_ok=True)   # the first answer's form
                try:
                    card = reviewer.inspect(
                        f"{name}-again", ask + "\n\nAnswer again, the whole form: an "
                        "earlier answer's `story.sections` was not one line per section "
                        f"of the screenplay (structure 20): {'; '.join(gaps)}. The "
                        f"screenplay has {len(script.get('sections') or [])} sections, "
                        "numbered from 1; give each one non-blank line.", schema,
                        cwd=pass_dir, form=form)
                    gaps = story_gaps(card, script)
                except HarnessError as exc:
                    if not quiet:
                        print(f"[qa] {name}-again failed ({exc}); the first answer stands",
                              file=sys.stderr)
                    row["again_failed"] = str(exc)
        except HarnessError as exc:
            if not quiet:
                print(f"[qa] {name} failed ({exc})", file=sys.stderr)
            row["failed"] = str(exc)
            rows.append(row)
            continue
        finally:
            (pass_dir / form).unlink(missing_ok=True)
        row.update(valid=True, card=card, gaps=gaps)
        rows.append(row)
    return rows


def _sample_summary(row: dict, unheard=()) -> dict:
    """A sample as the scorecard records it: what it said, not its whole card.
    An `unheard` category (AUTO-105) is None in its scores too: what the
    reviewer entered there was discarded, in every sample."""
    out = {k: row[k] for k in ("name", "k", "order", "valid", "again", "again_failed",
                               "failed", "gaps") if k in row}
    card = row.get("card")
    if isinstance(card, dict):
        scores = card.get("scores")
        if unheard and isinstance(scores, dict):
            scores = dict(scores) | dict.fromkeys(unheard)
        out.update(verdict=card.get("verdict"), overall=card.get("overall"),
                   scores=scores, findings=len(card.get("findings") or []))
    return out


def combine_samples(reviewer: Harness, pass_name: str, rows: list[dict], *,
                    unheard=()) -> dict:
    """The valid samples as one scorecard. Fewer than two valid is
    NOT_EVIDENCED: no scores, and the one reading's findings low-confidence.
    Otherwise each category is the median, `spread` its max - min, and the
    findings are merged by one decision (`<pass>-scorecard-merge`) asked
    only when two samples have findings or blocking claims to match. The
    reviewer's `blocking` claims are merged with the findings: one two
    samples make stays under `blocking`, one a single sample makes is
    low-confidence and blocks nothing, so the verdict reads the medians and
    what two readings agree on. A merge that fails keeps every finding and
    claim unmerged (improve's router merges duplicates too), so nothing is
    out-voted, and says so under not_evidenced. The not_evidenced lines are
    the samples' union. The story is the first sample's that has a line for
    every section, else the first valid one's, and the gaps it still has
    come with it. An `unheard` audio category (AUTO-105) is None in every
    sample and in the result, never a median (`combine_scores`); the
    overall stays the median the reviewers filed until `score_unheard`
    keeps it as overall_filed and makes the overall the mean of the scored
    medians."""
    valid = [r for r in rows if r.get("valid")]
    scoring = {"asked": len(rows), "valid": len(valid),
               "samples": [_sample_summary(r, unheard) for r in rows]}
    finding_rows = [{"k": r["k"], "findings": r["card"].get("findings"),
                     "blocking": r["card"].get("blocking")} for r in valid]
    if len(valid) < 2:
        _, low = merge_findings(finding_rows, [])
        first = valid[0]["card"] if valid else {}
        return {"scores": {}, "overall": None, "spread": {}, "findings": [],
                "low_confidence": low, "blocking": [],
                "not_evidenced": [f"scores: {len(valid)} of {len(rows)} scoring samples "
                                  "answered; fewer than two is not evidenced"],
                "rationale": str(first.get("rationale", "")), "story": first.get("story"),
                "verdict": NOT_EVIDENCED, "scoring": scoring, "gaps": []}
    scores, overall, spread = combine_scores([r["card"] for r in valid], unheard=unheard)
    noted: list[str] = []
    with_findings = [r for r in finding_rows if r["findings"] or r["blocking"]]
    if len(with_findings) >= 2:
        try:
            answer = reviewer.decide(f"{pass_name}-scorecard-merge",
                                     merge_prompt(finding_rows), MERGE_SCHEMA)
            groups = [g.get("findings") for g in answer.get("groups") or []
                      if isinstance(g, dict) and isinstance(g.get("findings"), list)]
            kept, low = merge_findings(finding_rows, groups)
            scoring["merge"] = {"name": f"{pass_name}-scorecard-merge",
                                "groups": len(groups)}
        except HarnessError as exc:
            kept = [dict(f, samples=[k]) for k, f in _finding_rows(finding_rows).values()]
            low = []
            scoring["merge"] = {"failed": str(exc)}
            noted.append(f"findings: the samples' findings could not be merged ({exc}); "
                         f"all {len(kept)} findings and blocking claims are kept unmerged")
    else:
        kept, low = merge_findings(finding_rows, [])
    claims = list(dict.fromkeys(f["problem"] for f in kept if f.get("claim")))
    kept = [f for f in kept if not f.get("claim")]
    chosen = next((r for r in valid if not r["gaps"]), valid[0])

    def union(field: str) -> list[str]:
        return list(dict.fromkeys(str(x) for r in valid
                                  for x in r["card"].get(field) or []))
    said = Counter(r["card"].get("verdict") for r in valid).most_common(1)[0][0]
    return {"scores": scores, "overall": overall, "spread": spread, "findings": kept,
            "low_confidence": low, "blocking": claims,
            "not_evidenced": union("not_evidenced") + noted,
            "rationale": "\n\n".join(f"[sample {r['k']}] {r['card'].get('rationale', '')}"
                                     for r in valid),
            "story": chosen["card"].get("story"), "verdict": said, "scoring": scoring,
            "gaps": chosen["gaps"]}


# --------------------------------------------------------------------------- #
# the review, as one seam (AUTO-156)
# --------------------------------------------------------------------------- #
#
# What run() asks the reviewer, pulled out of it so the QA calibration set
# (`calibrate`, below) is judged by the very code a pass is: the evidence
# rows, the stages' measurements, the prompt and the scoring. run() calls
# score_evidence; the prompts it sends and the inspections' names are what
# they were before the extraction (test_qa_samples pins them to a golden).

#: The contact sheet's file name in a pass.
CONTACT_SHEET = "contact-sheet.png"


def evidence_rows(samples: list[dict], script: dict, voice: dict | None, *, have: set,
                  diagnostics: dict, mechanical: list[dict]) -> list[dict]:
    """What the reviewer sees beside each frame: the shot it belongs to, the
    line said there, the take, and what was measured of it (the still's
    statistics, the burst's motion, the engine's diagnostics and the
    mechanical findings already filed on its shot). `have` is the stills
    that exist under frames/. A build still of its own is not evidence
    (`judged`): its ink is already in the mechanical findings."""
    voice = voice or {}
    shots = {s.get("label"): s for sec in script.get("sections", [])
             for s in sec.get("shots", [])}
    takes = {t.get("label"): t for t in voice.get("takes", [])}
    evidence = []
    for row in judged(samples):
        shot = shots.get(row["label"], {})
        take = takes.get(row["label"], {})
        evidence.append({
            "frame": row["frame"], "present": row["frame"] in have,
            "timestamp": row["t"], "why_sampled": row["why"],
            "shot": row["label"], "section": row["section"],
            "visual": str(shot.get("visual", ""))[:300],
            # Its role in the story and what its picture must demonstrate of
            # its line (AUTO-90): the visual support narrative_clarity reads.
            **{k: str(shot[k])[:300] for k in ("function", "shows") if shot.get(k)},
            **super_fields(shot),
            "narration": str(shot.get("narration", ""))[:300],
            # The line said where this still was taken (AUTO-104), with the
            # time it starts and the frame that shows that moment: is it on
            # screen when it is said?
            **({"lines": [{"at": ln.get("at"), "text": str(ln.get("text", ""))[:300],
                           "frame": ln.get("frame", row["frame"])}
                          for ln in row["lines"]]} if row.get("lines") else {}),
            "drawn_by": row.get("asset", ""),
            "take": {k: take.get(k) for k in ("seconds", "planned_seconds", "cps")
                     if k in take},
            # Measured, not judged: the still's statistics, the burst's motion
            # numbers, what the engine said about this shot, and what the
            # mechanical checks already filed for it.
            "still": row.get("still"),
            "burst": {"frames": (row.get("burst") or {}).get("frames"),
                      "span_seconds": (row.get("burst") or {}).get("span"),
                      "files": (row.get("burst") or {}).get("files")},
            "motion": row.get("motion"),
            "engine": diagnostics.get(row["label"]),
            "mechanical": [f"[{f['severity']}] {f['kind']}: {f['problem']}"
                           for f in mechanical if f["shot"] == row["label"]]})
    return evidence


def stage_measurements(build: dict, voice: dict | None, *, sound: dict, gate: dict | None,
                       geometry: dict, styles: dict, reach: dict | None,
                       rhythm: dict | None = None) -> dict:
    """The measurements and decisions of the stages, as the reviewer is shown
    them: the build's frame and findings, the narration, the cue sheet and
    mix, the gate's triage, the geometry check (`geometry`, its report),
    the treatment, and how far the authored documents reach into the engine
    (`reach`, vfx.json's `reach`, or None without one)."""
    from . import reach as reach_audit       # the parameter is the record
    voice, sound, styles = voice or {}, sound or {}, styles or {}
    cues_near = sound_cue_rows(sound, build)
    measurements = {
        "runtime": build.get("scene", {}).get("duration"),
        "frame": {k: build.get("scene", {}).get(k) for k in ("width", "height", "fps")},
        "layers": build.get("layers"), "placeholders": build.get("placeholders"),
        "legibility": build.get("legibility"), "stale_takes": build.get("stale_takes"),
        "editorial": build.get("editorial"), "findings": build.get("findings"),
        "narration": narration_measurements(voice),
        "sound_cues": cues_near, "mix": sound.get("mix"),
        "gate": (gate or {}).get("triage"),
        "geometry": {k: (geometry or {}).get(k) for k in
                     ("status", "why", "evaluated", "worst", "floating", "reads_merged")},
        "treatment": (styles.get("styles") or [{}])[0] if isinstance(
            styles.get("styles"), list) else styles.get("treatment"),
        # How far the authored documents reach into the engine, per `yt vfx`:
        # a weak-motion finding can name the construct that was left unused.
        "reach": reach_audit.summary(reach),
    }
    # Where the cues landed on their words and how the cuts sit on the music
    # (AUTO-140), for editing_pacing and music_score: only when the build
    # wrote them, so a film not rebuilt asks what it asked before.
    measurements.update({k: build[k] for k in BUILD_SOUND_MEASURES if k in build})
    # The film's rhythm (AUTO-141), measured and labelled so: evidence for
    # editing_pacing beside the reviewer's own judgement, never in place of
    # it. Only when `yt qa` measured it, so calibrate asks what it asked.
    if rhythm is not None:
        measurements["rhythm"] = rhythm_brief(rhythm)
    return measurements


def review_prompt(*, title: str, pass_name: str, sheet_name: str, has_narration: bool,
                  missing: list[str], total_shots: int, dropped_lines: list[dict],
                  narrated_total: int, story: dict | None, evidence: list[dict],
                  mechanical: list[dict], listening: list[dict], unheard: dict,
                  silent_film: bool, measurements: dict, blockers: list[str],
                  extra_briefs=(), measured_stills: int = 0) -> str:
    """The scorecard's prompt, before each sample's category order is added.
    `extra_briefs` are the listener's and the watcher's text (`--listen`,
    `--watch`), placed after the measurements. `measured_stills`, the
    build stills of their own under frames/build/ (AUTO-145), adds one
    sentence saying they are not to be scored; with none the prompt is
    as before, so a film with no diagram is not asked again."""
    return "\n\n".join([
        f"Review {pass_name} of \"{title}\". Read `{sheet_name}` first "
        "(every still tiled in time order with its timestamp), then every PNG "
        "directly under `frames/` (each is one still of the rendered composition "
        "at the timestamp in its name), judge each against the shot it belongs "
        "to and the bands (supers in the top 21%, stage in the middle, captions "
        "in the bottom 24%), then score the ten categories against the whole "
        "runtime and file the scorecard. Motion is evidenced by the burst under "
        f"`frames/burst/` (up to {BURST_MAX_FRAMES} consecutive frames over "
        f"{BURST_SECONDS:g}s around each still, which is the middle one) and "
        "summarised beside each frame as `motion.steps`, the mean absolute "
        "difference between consecutive frames, and `motion.total`, first frame "
        "against last: both near zero is frozen, one step far above the rest is "
        "a jump; read the burst frames themselves only where those numbers or "
        "the still call for it. A frame sampled where a line of the narration "
        "starts (`why_sampled` has `line: ...`) carries that line under `lines` "
        "with the time it starts and `frame`, the file under `frames/` that shows "
        "that moment: the still itself, or the burst frame when the line starts "
        "a few frames from the still. Judge from that file whether the picture "
        "shows what the line says at the moment it is said; a picture that does "
        "not is a finding on its shot. The "
        "mechanical findings are measured, not judged: do not repeat them as "
        "your own, but score as if they stand, because they do. Blocking items "
        "already proven by the manifests are listed; add what the frames show."
        + (f" The {measured_stills} stills under `frames/{BUILD_DIR}/` are measured "
           "for ink, not judged: each sits a quarter second from a cut, where a "
           "transition can be mid-dissolve, and what they measure is in the "
           "mechanical findings; neither read nor score them."
           if measured_stills else "")
        + (" " + unseen_brief(missing, total_shots) if missing else "")
        + (" " + unseen_lines_brief(dropped_lines, narrated_total)
           if dropped_lines else ""),
        (f"Read `{NARRATION}` too: the whole narration, one shot a line with its "
         "time on the film. Judge narrative_clarity and editing_pacing on the film "
         "as told: can the explanation be followed by ear alone, from the "
         "narration, and by eye alone, from the frames? It should need both "
         "(attention 23): frames that repeat the narration as text, or narration "
         "the pictures add nothing to, count against narrative_clarity. "
         if has_narration else
         "There is no narration to read; judge the story from the frames. ")
        + "The screenplay's story below is what the writer declared, context and "
        "not evidence: judge whether the film delivers it. Then fill `story`: "
        "restate the premise in one sentence as the film made it, say for each "
        "section what the viewer has at its end that they lacked at its start "
        "(structure 20: one line for every section of the screenplay, none blank; "
        "where a section leaves the viewer nothing new, the line says so), and predict how satisfied a viewer who watched to the "
        "end would say they were, with why. That prediction is recorded for "
        "calibration and scores nothing (judging 4). A finding on the story "
        f"itself ({', '.join(STORY_DUTY)}) is owned by `script`; a picture that "
        "does not show what its line says is owned by whoever draws it.",
        brief("The screenplay's story, as the plan declared it", story)
        if story else "The screenplay declares no story fields (written before them).",
        brief("Frames and what each should show", evidence),
        brief(f"Mechanical findings, measured before this review ({CAP_RULE})",
              mechanical),
        brief("Listening evidence: the reviews that heard the film's sound, each "
              "marked with what it heard (`heard`) and whether that is what the film "
              "carries (`current`); what a stale or absent one said is left out and "
              "`why` says what is missing", listening),
        (f"No current listening review covers {', '.join(unheard)}: do not score "
         f"{'it' if len(unheard) == 1 else 'them'} (enter 0; the number is discarded "
         "and each is recorded not evidenced), leave them out of `overall` (it is "
         "recomputed as the mean of the categories you score), and file no finding on "
         "how they sound, since nothing heard them."
         if unheard else
         f"Every one of {', '.join(k for k in AUDIO if k not in SILENT or not silent_film)} "
         "has a current listening review: score it from what that review heard, "
         "with the measurements as context only."),
        brief("Measurements and decisions from the stages", measurements),
        *extra_briefs,
        brief("Blocking, already proven", blockers),
        brief("Each finding's owner is exactly one of these; one you cannot place is "
              "`external`, with the reason in `fix`", {o: DUTIES[o] for o in OWNERS}),
        # Nothing of the previous pass (AUTO-106): shown its scores, the
        # reviewer filed them again. compare() takes the deltas from the
        # two scorecards once this one exists, and --recheck puts the
        # previous findings to a decision of their own after the scoring.
    ])


def score_evidence(reviewer: Harness, pass_dir: Path, *, title: str, build: dict,
                   script: dict, voice: dict | None, samples: list[dict], frames_dir: Path,
                   listening: list[dict], mechanical: list[dict], diagnostics: dict,
                   measurements: dict, blockers: list[str], extra_briefs=(),
                   watched: bool = False, quiet: bool = True,
                   readings: list | None = None) -> dict:
    """The review of one pass's evidence: the raw combined scorecard of
    QA_SAMPLES readings, before any rule (score_silence, score_unheard,
    cap_scores) or bar is applied.

    `samples` are measured (`measure`). The pass's narration.txt is written
    from the files when it is missing, and so is its contact sheet; then the
    evidence rows, the story brief and what went unsampled make the prompt,
    the samples are asked (`ask_samples`) and combined (`combine_samples`),
    and a story line still missing is named under not_evidenced. `readings`,
    when given, is extended with the samples' rows (the watch's merge reads
    them)."""
    pass_dir, frames_dir, voice = Path(pass_dir), Path(frames_dir), voice or {}
    narration_file = pass_dir / NARRATION
    if not narration_file.is_file():
        said = narration_text(script, build, voice)
        if said:
            narration_file.write_text(said, encoding="utf-8")
    sheet = pass_dir / CONTACT_SHEET
    if not sheet.is_file():
        contact_sheet(samples, frames_dir, sheet)
    have = {p.name for p in frames_dir.glob("*.png")} if frames_dir.is_dir() else set()
    evidence = evidence_rows(samples, script, voice, have=have, diagnostics=diagnostics,
                             mechanical=mechanical)
    story = story_brief(script, build)
    # What a --frames below the shot count (or past the default) left
    # unseen is said to the reviewer before it scores, and in the scorecard;
    # and which narrated lines no still shows where they start (AUTO-104).
    missing = unsampled(build, samples)
    dropped_lines = lines_unsampled(build, voice, samples)
    unheard = audio_unheard(listening, silent=silent(build))
    prompt = review_prompt(
        title=title, pass_name=pass_dir.name, sheet_name=sheet.name,
        has_narration=narration_file.is_file(), missing=missing,
        total_shots=len(build.get("shots", [])), dropped_lines=dropped_lines,
        narrated_total=len(narrated_lines(build, voice)), story=story, evidence=evidence,
        mechanical=mechanical, listening=listening, unheard=unheard,
        silent_film=silent(build), measurements=measurements, blockers=blockers,
        extra_briefs=extra_briefs, measured_stills=len(samples) - len(judged(samples)))
    # QA_SAMPLES readings of the same evidence (AUTO-107): one reading's
    # noise is the size of the steps read as progress. structure 20 is
    # checked past the schema in each (one non-blank line per screenplay
    # section, asked once more); what still lacks a line is named under
    # not_evidenced, never filled in.
    rows = ask_samples(reviewer, pass_dir, prompt, script, quiet=quiet,
                       **({"watched": True} if watched else {}))
    if readings is not None:
        readings.extend(rows)
    card = combine_samples(reviewer, pass_dir.name, rows, unheard=unheard)
    gaps = card.pop("gaps")
    if gaps:
        card["not_evidenced"] = list(card.get("not_evidenced") or []) + [
            f"story.sections (structure 20): {'; '.join(gaps)}, asked once more"]
    return card


# --------------------------------------------------------------------------- #
# calibrating the judge on fixture films (AUTO-156)
# --------------------------------------------------------------------------- #
#
# The bar reads the medians of an LLM judge's scores; nothing showed that
# those scores tell a good film from a bad one. tests/fixtures/qa/ holds
# labelled films, each case exactly what score_evidence reads (build.json,
# script.json, voice.json, samples.json, frames/ and frames/burst/) and an
# expected.json; `calibrate` scores every case through score_evidence and
# judges the raw card (`judge_case`), with no cap, no mechanical blocker and
# no silence rule, since what is calibrated is the judge and not the rules
# after it. `yt qa` then records whether its READY came from a judge that
# passed this set (`judge_calibration` in qa.json). The label gates nothing.

#: Under the work directory: one folder per case (named by `case_key`, never
#: by the case: the judge's working directory must not say "placeholder")
#: holding its manifests, its pass and its cached decisions, and the report.
#: <work>/calibration/ is narrative.calibrate's and is never written here.
CALIBRATION_QA, CALIBRATION_REPORT = "calibration-qa", "report.json"
#: A fixture case: at most this many samples, each burst trimmed to its
#: first, middle and last frame, every PNG at most the contact-sheet tile's
#: width; and the sizes the committed set is held to (test_qa_calibrate).
#: TO VERIFY: the sizes of a real capture against these budgets.
FIXTURE_SAMPLES = 8
FIXTURE_WIDTH = SHEET_TILE[0]
FIXTURE_CASE_BYTES, FIXTURE_TREE_BYTES = 600_000, 5_000_000
#: The files of a case read as the project's, beside its pass.
FIXTURE_INPUTS = ("build.json", "script.json", "voice.json")
#: expected.json's verdict words, and the strings the bar uses for them.
EXPECTED_VERDICTS = {"READY": READY, "NOT_READY": NOT_READY}
#: What `python -m ytstudio.qa calibrate` may spend before it reads no
#: further case: 8 cases of QA_SAMPLES readings at spend.QA_SAMPLE_USD, with
#: the merges and room to spare. TO VERIFY: one live run's cost.
CALIBRATE_QA_BUDGET_USD = 20.0
#: The fixture set the repo ships: a report counts as calibration only for
#: the set it ran (`fixture_set_digest`), so one easy case, or a set that
#: has since gained a case, is not this one.
QA_FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "qa"


def case_key(name: str) -> str:
    """A case's folder name under <work>/calibration-qa/: stable, so its
    decisions are found again, and saying nothing of what the case seeds."""
    return "case-" + hashlib.sha256(str(name).encode("utf-8")).hexdigest()[:10]


def case_dir(work, name: str) -> Path:
    return Path(work) / CALIBRATION_QA / case_key(name)


def load_case(path) -> dict:
    """One fixture case, read with the readers `yt qa` uses: {name, dir,
    build, script, voice (None without voice.json), samples, expected}. A
    missing file, or a still or burst frame samples.json names that is not
    there, is a ValueError naming its path."""
    path = Path(path)
    for name in ("expected.json", "build.json", "script.json", "samples.json"):
        if not (path / name).is_file():
            raise ValueError(f"{path / name}: missing from the case")
    expected = read_json(path / "expected.json", warn=False)
    build = read_json(path / "build.json", warn=False)
    script = read_json(path / "script.json", warn=False)
    samples = read_json(path / "samples.json", warn=False, shape=list)
    voice = read_json(path / "voice.json", warn=False) if (path / "voice.json").is_file() \
        else None
    for name, data in (("expected.json", expected), ("build.json", build),
                       ("script.json", script), ("samples.json", samples)):
        if not data:
            raise ValueError(f"{path / name}: unreadable or empty")
    named = set()
    for row in samples:
        for name in [row.get("frame", ""), *((row.get("burst") or {}).get("files") or [])]:
            if not (path / "frames" / name).is_file():
                raise ValueError(f"{path / 'frames' / name}: named in samples.json, missing")
            named.add(Path(name).as_posix())
    # The judge reads every PNG under frames/: one no sample names (a
    # stand-in's still a capture left behind) would be judged as the film.
    for png in sorted((path / "frames").rglob("*.png")):
        if png.relative_to(path / "frames").as_posix() not in named:
            raise ValueError(f"{png}: under frames/ but named by no sample in samples.json")
    return {"name": path.name, "dir": path, "build": build, "script": script,
            "voice": voice, "samples": samples, "expected": expected}


def _agreed(finding) -> bool:
    """A finding two readings filed. combine_samples keeps one reading's
    findings among the kept ones when its merge fails (samples=[k]); the
    calibration counts only what two agreed on."""
    return isinstance(finding, dict) and len(finding.get("samples") or []) >= 2


def judge_verdict(card: dict) -> tuple[str, list[str]]:
    """The bar applied to the judge alone: the side categories' medians
    (SIDE_CATEGORIES, what a still can show), their mean as the overall,
    and as blocking the judge's own two-sample `blocking` claims and the
    kept findings it called blocking. The audio categories are left out, so
    a fixture with no listening review is not NOT READY for that, and
    nothing mechanical is read: no cap, no blocker, no silence rule."""
    if card.get("verdict") == NOT_EVIDENCED:
        return NOT_EVIDENCED, ["fewer than two scoring samples answered"]
    scores = card.get("scores") or {}
    side = {k: _number(scores.get(k)) for k in SIDE_CATEGORIES}
    scored = [v for v in side.values() if v is not None]
    overall = round(sum(scored) / len(scored), 2) if scored else 0.0
    reasons = []
    if overall < OVERALL_BAR:
        reasons.append(f"overall {overall:.1f} is under {OVERALL_BAR}")
    for key, value in side.items():
        if value is None:
            reasons.append(f"{key} was not scored")
        elif value < CATEGORY_BAR:
            reasons.append(f"{key} {value:.1f} is under {CATEGORY_BAR}")
    blocking = [str(b) for b in card.get("blocking") or []] + [
        f"{f.get('problem')} at {f.get('timestamp')}s" for f in card.get("findings") or []
        if _agreed(f) and f.get("severity") == "blocking"]
    if blocking:
        reasons.append(f"{len(blocking)} blocking item(s)")
    return (READY if not reasons else NOT_READY), reasons


def judge_case(expected: dict, card: dict, base_card: dict | None = None) -> list[str]:
    """What the judge got wrong on one case, as failures ([] when it passed).

    `card` is the raw combined card after score_unheard. The case passes
    when the judge's verdict (`judge_verdict`) is the expected one, every
    under_bar category is under CATEGORY_BAR and every over_bar one at or
    over it, each must_find has a kept finding (two samples agreed on it; a
    low-confidence one does not count) whose timestamp is inside its
    t_range and, when owners are named, whose owner is one of them, and each
    `drop` category's median is at least that far under the base case's
    (`base_card`, scored in the same run). A card whose findings merge
    failed fails too: its findings and blocking claims are every reading's,
    unmerged, so what two readings agree on cannot be told."""
    failures = []
    merge = ((card.get("scoring") or {}).get("merge") or {})
    if merge.get("failed"):
        failures.append(f"merge: the readings' findings could not be merged "
                        f"({merge['failed']}); nothing two readings agree on can be told")
    want = EXPECTED_VERDICTS.get(str(expected.get("verdict")), expected.get("verdict"))
    got, _ = judge_verdict(card)
    if got != want:
        failures.append(f"verdict: expected {want}, the judge gave {got}")
    scores = card.get("scores") or {}
    for key in expected.get("under_bar") or []:
        value = _number(scores.get(key))
        if value is None or value >= CATEGORY_BAR:
            failures.append(f"under_bar: {key} scored "
                            f"{'nothing' if value is None else f'{value:g}'}, not under "
                            f"{CATEGORY_BAR}")
    for key in expected.get("over_bar") or []:
        value = _number(scores.get(key))
        if value is None or value < CATEGORY_BAR:
            failures.append(f"over_bar: {key} scored "
                            f"{'nothing' if value is None else f'{value:g}'}, under "
                            f"{CATEGORY_BAR}")
    kept = [f for f in card.get("findings") or [] if _agreed(f)]
    for need in expected.get("must_find") or []:
        a, b = (float(x) for x in need["t_range"])
        owners = list(need.get("owners") or [])
        hit = any(a <= (_number(f.get("timestamp")) if _number(f.get("timestamp")) is not None
                        else -1.0) <= b and (not owners or f.get("owner") in owners)
                  for f in kept)
        if not hit:
            failures.append(f"must_find: no finding two samples agree on in {a:g}-{b:g}s"
                            + (f" owned by {' or '.join(owners)}" if owners else ""))
    drop = expected.get("drop") or {}
    if drop and base_card is None:
        failures.append(f"drop: the base case {expected.get('base')} was not scored, so "
                        "nothing can be compared with it")
    elif drop:
        base_scores = base_card.get("scores") or {}
        for key, amount in drop.items():
            mine, theirs = _number(scores.get(key)), _number(base_scores.get(key))
            if mine is None or theirs is None or theirs - mine < float(amount):
                failures.append(f"drop: {key} {mine if mine is not None else 'unscored'} is "
                                f"not {float(amount):g} under the base case "
                                f"{expected.get('base')}'s "
                                f"{theirs if theirs is not None else 'unscored'}")
    return failures


#: The fixed inputs of review_prompt that judge_digest covers: no film's
#: narration, samples or numbers, so only the template text of the prompt
#: is in the digest. Every audio category is unheard, as in a calibration
#: run, which carries no listening review.
PROBE = {"title": "", "pass_name": "pass-01", "sheet_name": CONTACT_SHEET,
         "has_narration": True, "missing": [], "total_shots": 0, "dropped_lines": [],
         "narrated_total": 0, "story": None, "evidence": [], "mechanical": [],
         "listening": [], "unheard": {k: {"missing": "", "command": ""} for k in AUDIO},
         "silent_film": False, "measurements": {}, "blockers": []}


# The re-run rule. The judge is what this digest covers: its role, the
# categories and the bar, the scorecard and merge schemas, how many samples
# and in which orders, the prompt's template (review_prompt on PROBE), the
# model and the house rules it is handed. Any card that edits ROLE,
# CATEGORIES, the scorecard schema or review_prompt changes it, and qa.json's
# `judge_calibration.fixtures` is false until `python -m ytstudio.qa
# calibrate` is run again. AUTO-141, AUTO-145, AUTO-146 and AUTO-148 do this,
# and each must add or adjust a fixture case for the category it changes.
def judge_digest(model: str, house_digest) -> str:
    """What the QA judge is, as one digest: a calibration report carries it,
    and a pass whose judge has another is not the one calibrated. The house
    rules count by the block the judge is handed (`block_sha256`), not by
    where the files were found."""
    block = (house_digest.get("block_sha256") if isinstance(house_digest, dict)
             else house_digest)
    return _digest(ROLE, ROLE_LISTENED, scorecard_schema(False), scorecard_schema(True),
                   MERGE_SCHEMA, CATEGORIES, OVERALL_BAR, CATEGORY_BAR, QA_SAMPLES,
                   [category_order(k) for k in range(1, QA_SAMPLES + 1)],
                   review_prompt(**PROBE), model, block or "")


def fixture_set_digest(directory) -> str:
    """What a fixture set is, as one digest: every case's name and the bytes
    of every file in it (expected.json, the manifests, samples.json, the
    frames). A report records the set it ran; a set with a case more or
    less, a relabelled case or a recaptured film is another set."""
    directory = Path(directory)
    if not directory.is_dir():
        return ""
    rows = []
    for case in sorted(p for p in directory.iterdir()
                       if p.is_dir() and (p / "expected.json").is_file()):
        for file in sorted(f for f in case.rglob("*") if f.is_file()):
            rows.append([file.relative_to(directory).as_posix(),
                         hashlib.sha256(file.read_bytes()).hexdigest()])
    return _digest(rows)


def judge_calibration(work, model: str, house_digest, *, listened: bool = False,
                      watched: bool = False, fixtures_dir=None) -> dict:
    """qa.json's `judge_calibration`: whether this pass's judge is the one
    the QA fixtures last passed. `fixtures` is true only when the pass
    asked the unheard, unwatched judge calibrate runs (ROLE, no listener's
    or watcher's brief: a `--listen` pass is ROLE_LISTENED with audio
    scored, a `--watch` pass another schema), and
    <work>/calibration-qa/report.json exists, passed, was made at this
    pass's model, carries this judge's digest and ran the shipped fixture
    set (`fixtures_dir`, default QA_FIXTURES, by `fixture_set_digest`).
    `labelled` is how many owner-labelled films back it: none yet.
    Informational: nothing reads it to gate READY, doctor or improve."""
    path = Path(work) / CALIBRATION_QA / CALIBRATION_REPORT
    want = judge_digest(model, house_digest)
    shipped = Path(fixtures_dir) if fixtures_dir is not None else QA_FIXTURES
    report = read_json(path, warn=False)
    asked = [word for word, on in (("listened", listened), ("watched", watched)) if on]
    if asked:
        why = (f"a {' and '.join(asked)} pass: the QA fixtures calibrate the unheard, "
               "unwatched judge only")
    elif not isinstance(report, dict):
        why = (f"no {path}; run python -m ytstudio.qa calibrate tests/fixtures/qa "
               f"--model {model} --work {work}")
    elif not report.get("passed"):
        why = f"the last calibration ({path}) did not pass"
    elif report.get("model") != model:
        why = (f"the fixtures were calibrated at {report.get('model')}, and this pass "
               f"scores at {model}")
    elif report.get("digest") != want:
        why = ("the judge has changed since the fixtures were calibrated (its role, "
               "categories, schema, prompt or house rules); run calibrate again")
    elif not report.get("fixture_set") or report.get("fixture_set") != fixture_set_digest(
            shipped):
        why = (f"the calibration ran another fixture set than {shipped} (a case added, "
               "dropped, relabelled or recaptured since); run calibrate on it again")
    else:
        why = ""
    return {"fixtures": not why, "labelled": 0, "model": model, "digest": want,
            "why": why or f"{path} passed at {model} with this judge"}


def judge_line(calibration: dict) -> str:
    """The line `yt qa` prints under its verdict line."""
    films = f"{int(calibration.get('labelled') or 0)} owner-labelled films"
    if calibration.get("fixtures"):
        return f"judge: fixture-calibrated ({calibration.get('model')}); {films}"
    return (f"judge: not calibrated against the QA fixtures ({calibration.get('why')}); "
            f"{films}")


def _case_title(script: dict, build: dict) -> str:
    """The title the judge is told: the film's, never the case's name."""
    idea = (script or {}).get("idea") if isinstance((script or {}).get("idea"), dict) else {}
    return str(idea.get("title") or (build or {}).get("title") or "the film")


def calibrate(directory, *, work, model: str = "sonnet", budget_usd: float | None = None,
              fresh: bool = False, verbose: bool = True) -> dict:
    """Score every case under `directory` with the QA judge and compare each
    raw card with its expected.json (`judge_case`).

    Per case, its frames and samples.json are copied into
    <work>/calibration-qa/<key>/qa/pass-01/ and its manifests beside that
    pass, as a project's are; the samples are measured, the mechanical
    findings filed (they reach the prompt, as in run(), and are recorded,
    never judged), and score_evidence asks the QA_SAMPLES readings on a
    harness rooted there (decisions cached under it, so a second run pays
    for nothing that did not change), with no listening review, no engine
    diagnostics and no blockers; then score_unheard. Every case is scored
    before any is judged, so a case's `base` is read from the same run.
    Once `budget_usd` is spent no further case is read, and an unread case
    fails. The set passes when every case does and at least one case is
    labelled by the owner (`label: owner`): a set of drawn stand-ins says
    the judge separates drawings, not films. The report is written to
    <work>/calibration-qa/report.json."""
    directory, work = Path(directory), Path(work)
    # The judge's cwd is under <work>/calibration-qa/, beside report.json,
    # which maps each case folder to its labels: the last report goes before
    # any case is scored, so a judge reading past its cwd finds no answers.
    (work / CALIBRATION_QA / CALIBRATION_REPORT).unlink(missing_ok=True)
    rules = house.load(work)
    block, digest = rules.for_stage("qa"), rules.digest("qa")
    names = sorted(p.name for p in directory.iterdir()
                   if p.is_dir() and (p / "expected.json").is_file())
    rows, cards, reviewers = [], {}, []

    def spent_usd() -> float:
        return sum(float(r.report()["cost_usd"]) for r in reviewers)

    for name in names:
        row = {"name": name, "dir": case_key(name), "judge_only": None, "expected": None,
               "got": None, "mechanical": [], "ran": False, "passed": False, "failures": []}
        rows.append(row)
        try:
            case = load_case(directory / name)
        except ValueError as exc:
            row["failures"] = [f"unreadable: {exc}"]
            continue
        expected = case["expected"]
        row.update(expected=expected, judge_only=bool(expected.get("judge_only")))
        if budget_usd is not None and spent_usd() >= budget_usd:
            row["failures"] = [f"not read: the budget of ${budget_usd:g} was spent"]
            continue
        row["ran"] = True
        root = case_dir(work, name)
        pass_dir = root / QA_DIR / "pass-01"
        if pass_dir.exists():
            shutil.rmtree(pass_dir)        # the same evidence every run: the cache holds
        pass_dir.mkdir(parents=True)
        for file in FIXTURE_INPUTS:
            (root / file).unlink(missing_ok=True)
            if (case["dir"] / file).is_file():
                shutil.copyfile(case["dir"] / file, root / file)
        shutil.copytree(case["dir"] / "frames", pass_dir / "frames")
        build, script, voice = case["build"], case["script"], case["voice"]
        samples = measure(copy.deepcopy(case["samples"]), pass_dir / "frames")
        write_json(pass_dir / "samples.json", samples)
        mechanical = mechanical_findings(samples)
        reviewer = Harness(root, "qa", model=model, role=ROLE, house=block,
                           house_digest=digest, fresh=fresh, verbose=verbose, timeout=1800.0)
        reviewers.append(reviewer)
        card = score_evidence(
            reviewer, pass_dir, title=_case_title(script, build), build=build, script=script,
            voice=voice, samples=samples, frames_dir=pass_dir / "frames", listening=[],
            mechanical=mechanical, diagnostics={}, blockers=[], quiet=not verbose,
            measurements=stage_measurements(build, voice, sound={}, gate=None, geometry={},
                                            styles={}, reach=None))
        card = score_unheard(card, audio_unheard([], silent=silent(build)))
        cards[name] = card
        row["mechanical"] = mechanical
        row["got"] = {"verdict": judge_verdict(card)[0], "scores": card.get("scores"),
                      "spread": card.get("spread"), "findings": card.get("findings") or [],
                      "low_confidence": card.get("low_confidence") or [],
                      "blocking": card.get("blocking") or []}
    false_positive: Counter = Counter()
    false_negative: Counter = Counter()
    for row in rows:
        if not row["ran"]:
            continue
        expected = row["expected"]
        base = cards.get(expected["base"]) if expected.get("base") else None
        row["failures"] = judge_case(expected, cards[row["name"]], base)
        row["passed"] = not row["failures"]
        scores = row["got"]["scores"] or {}
        for key in expected.get("over_bar") or []:
            value = _number(scores.get(key))
            if value is None or value < CATEGORY_BAR:
                false_positive[key] += 1
        for key in expected.get("under_bar") or []:
            value = _number(scores.get(key))
            if value is not None and value >= CATEGORY_BAR:
                false_negative[key] += 1
    failures = []
    if not any((r["expected"] or {}).get("label") == "owner" for r in rows):
        failures.append("no case is labelled by the owner: the good cases are drawn "
                        "stand-ins until a real film is captured (`python -m ytstudio.qa "
                        "capture-fixture`) and labelled, so passing them says the judge "
                        "separates drawings, not films")
    report = {"directory": str(directory), "model": model,
              "digest": judge_digest(model, digest), "house": digest,
              "fixture_set": fixture_set_digest(directory), "cases": rows,
              "false_positive": dict(sorted(false_positive.items())),
              "false_negative": dict(sorted(false_negative.items())),
              "failures": failures,
              "passed": bool(rows) and all(r["passed"] for r in rows) and not failures,
              "cost_usd": round(spent_usd(), 4)}
    write_json(work / CALIBRATION_QA / CALIBRATION_REPORT, report)
    return report


def calibration_lines(report: dict) -> list[str]:
    """The calibration as the operator reads it: one row per case (expected
    and got verdict, the under- and over-bar categories the judge missed,
    the must_find it missed, the result), the false positives and negatives
    per category, and the set's result."""
    rows = report.get("cases") or []
    width = max([len("case")] + [len(r["name"]) for r in rows]) + 2
    out = ["case".ljust(width) + "expected".ljust(18) + "got".ljust(18) + "result"]
    for r in rows:
        want = (r.get("expected") or {}).get("verdict") or "-"
        got = (r.get("got") or {}).get("verdict") or "-"
        result = "pass" if r.get("passed") else "fail" if r.get("ran") else "not run"
        failures = r.get("failures") or []
        missed = {kind: [f.split(": ", 1)[1].split(" ", 1)[0] for f in failures
                         if f.startswith(kind + ":")] for kind in ("under_bar", "over_bar")}
        found = [f.split(": ", 1)[1] for f in failures if f.startswith("must_find:")]
        notes = "; ".join(
            [f"missed under bar: {', '.join(missed['under_bar'])}"] * bool(missed["under_bar"])
            + [f"missed over bar: {', '.join(missed['over_bar'])}"] * bool(missed["over_bar"])
            + [f"missed {len(found)} must_find"] * bool(found))
        out.append(r["name"].ljust(width) + str(want).ljust(18) + str(got).ljust(18) + result
                   + (f"  ({notes})" if notes else ""))
        out += [f"    - {line}" for line in failures]
    fp, fn = report.get("false_positive") or {}, report.get("false_negative") or {}
    out.append("false positives (an over-bar category scored under the bar): "
               + (", ".join(f"{k} {n}" for k, n in fp.items()) or "none"))
    out.append("false negatives (an under-bar category scored at or over the bar): "
               + (", ".join(f"{k} {n}" for k, n in fn.items()) or "none"))
    out += [f"set: {line}" for line in report.get("failures") or []]
    out.append(f"{'passed' if report.get('passed') else 'not passed'} at "
               f"{report.get('model')}; spent ${float(report.get('cost_usd') or 0):.2f}")
    return out


def fixture_samples(samples: list[dict], build: dict | None = None,
                    keep: int = FIXTURE_SAMPLES) -> list[dict]:
    """A real pass's samples cut down to a fixture: at most `keep`, chosen
    over the runtime by the even steps `_spread_times` takes (`_even_steps`),
    each burst trimmed to its first, middle (the still's frame) and last
    file, `frames` and `times` rewritten to match, a line whose frame was
    trimmed shown by the still, and the measurements dropped (calibrate
    measures again)."""
    rows = sorted((copy.deepcopy(r) for r in samples), key=lambda r: float(r["t"]))
    if len(rows) > keep:
        shots = (build or {}).get("shots") or []
        runtime = max([float(s["at"]) + float(s.get("seconds") or 0) for s in shots]
                      + [float(r["t"]) for r in rows])
        rows = [rows[k] for k in _even_steps([float(r["t"]) for r in rows], keep, runtime)]
    for row in rows:
        row.pop("still", None)
        row.pop("motion", None)
        burst = row.get("burst") or {}
        files, frames = list(burst.get("files") or []), list(burst.get("frames") or [])
        if len(files) > 3:
            mid = (frames.index(burst["still_frame"])
                   if burst.get("still_frame") in frames and len(frames) == len(files)
                   else len(files) // 2)
            pick = sorted({0, mid, len(files) - 1})
            burst["files"] = [files[i] for i in pick]
            if len(frames) == len(files):
                burst["frames"] = [frames[i] for i in pick]
                times = burst.get("times") or []
                if len(times) == len(files):
                    burst["times"] = [times[i] for i in pick]
        kept = set(burst.get("files") or []) | {row["frame"]}
        for line in row.get("lines") or []:
            if line.get("frame") not in kept:
                line["frame"] = row["frame"]
    return rows


def fixture_png(src: Path, dst: Path) -> int:
    """One frame as a fixture keeps it: RGB, at most FIXTURE_WIDTH wide
    (the contact-sheet tile), saved optimized. Returns its size in bytes."""
    from PIL import Image
    with Image.open(src) as im:
        frame = im.convert("RGB")
    if frame.width > FIXTURE_WIDTH:
        frame = frame.resize((FIXTURE_WIDTH, max(1, round(frame.height * FIXTURE_WIDTH
                                                          / frame.width))), Image.LANCZOS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    frame.save(dst, optimize=True)
    return dst.stat().st_size


def capture_fixture(pass_dir, case_dir) -> dict:
    """A fixture case from one real `yt qa` pass of one of the owner's own
    films: the project's build.json, script.json and voice.json (when it
    has one), the pass's samples cut down (`fixture_samples`) and the frames
    they name downscaled (`fixture_png`). Whatever else the case directory
    held is removed first, but for expected.json, which is never written:
    the owner labels the case. A frame the pass lacks is a ValueError."""
    pass_dir, case = Path(pass_dir), Path(case_dir)
    project = pass_dir.parent.parent
    samples = read_json(pass_dir / "samples.json", warn=False, shape=list)
    if not samples:
        raise ValueError(f"{pass_dir / 'samples.json'}: missing or empty")
    for name in ("build.json", "script.json"):
        if not (project / name).is_file():
            raise ValueError(f"{project / name}: missing; capture a pass inside its project")
    build = read_json(project / "build.json", warn=False) or {}
    kept = fixture_samples(samples, build)
    case.mkdir(parents=True, exist_ok=True)
    # A case captured into one in use (the drawn stand-in in
    # tests/fixtures/qa/good) keeps nothing of it but expected.json, the
    # label the owner edits: a stale still would be judged as this film's.
    if (case / "frames").exists():
        shutil.rmtree(case / "frames")
    for name in (*FIXTURE_INPUTS, "samples.json"):
        (case / name).unlink(missing_ok=True)
    for name in FIXTURE_INPUTS:
        if (project / name).is_file():
            shutil.copyfile(project / name, case / name)
    size = 0
    for row in kept:
        for name in [row["frame"], *((row.get("burst") or {}).get("files") or [])]:
            src = pass_dir / "frames" / name
            if not src.is_file():
                raise ValueError(f"{src}: named in samples.json, missing")
            size += fixture_png(src, case / "frames" / name)
    write_json(case / "samples.json", kept)
    size += sum(p.stat().st_size for p in case.iterdir() if p.is_file())
    return {"case": str(case), "samples": len(kept), "bytes": size,
            "over_budget": size > FIXTURE_CASE_BYTES}


def register(sub, parents=()) -> argparse.ArgumentParser:
    from . import cutcompare      # it reads this module's categories and tally
    p = sub.add_parser(
        "qa", parents=list(parents),
        help="score the rendered film against the publication bar, per pass",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scene", metavar="FILE",
                   help="the document (default: the project's scene.xml)")
    p.add_argument("--frames", type=_positive, default=None, dest="frame_limit",
                   metavar="N",
                   help="how many stills to sample (default: every shot once, at its "
                        "midpoint, and the start of each narrated line, up to "
                        f"{EVERY_SHOT_UP_TO}). The shots come first, then the line "
                        "stills (payoff, re-hook and reveal lines before the rest); "
                        "above that the extra stills are evenly "
                        "spaced over the runtime; below the shot count the hook, the ending, the "
                        "payoff and the section starts are kept first and the shots "
                        "left out are named under not_evidenced")
    p.add_argument("--quality", default="preview",
                   choices=["draft", "preview", "final"],
                   help="render quality of the stills (default preview)")
    p.add_argument("--reuse", metavar="PASS",
                   help="score the frames of this earlier pass again, rendering nothing "
                        "but proof frames that were rendered graded")
    p.add_argument("--previous", metavar="PASS",
                   help="the earlier pass this one is compared with: its scores for the "
                        "deltas, and its cut side by side at the same timestamps "
                        "(default: the latest scored pass; yt improve names its best "
                        "kept pass)")
    p.add_argument("--recheck", action="store_true",
                   help="after the blind scoring, ask once more whether each finding of "
                        "the pass compared with still stands in this cut's stills; the "
                        "answers are filed under `recheck` and change no score (AUTO-106)")
    p.add_argument("--against", metavar="FILE",
                   help="the previous kept cut (a video.mp4): a gateway video model "
                        "watches it and this pass's video.mp4 whole, in both orders, and "
                        "its verdicts join the side-by-side tally as source video; needs "
                        "--compare-model and AI_GATEWAY_API_KEY (AUTO-128)")
    p.add_argument("--compare-model", metavar="MODEL", dest="compare_model",
                   help="the gateway video model for --against, provider/model "
                        f"(default ${cutcompare.MODEL_ENV}); never of an author's family")
    p.add_argument("--host", help="ssh target of the render host")
    p.add_argument("--local", action="store_true", help="render the stills here")
    p.add_argument("--fresh", action="store_true",
                   help="score again even if the evidence did not change")
    listen_pass.add_flags(p)
    from . import watch as watch_pass       # it reads this module's owners and duties
    watch_pass.add_flags(p)
    workspace.add_inputs(p, "build", "script", "voice", "sound", "styles", "gate")
    p.set_defaults(run=run)
    return p


def run(args) -> int:
    from . import cutcompare      # it reads this module's categories and tally
    idea, project = workspace.resolve(args, inputs=("scene",))
    src = lambda name: project.source(name, workspace.override(args, name))  # noqa: E731
    scene = project.source("scene", args.scene)
    if not scene:
        print("error: no scene; run `yt build` first, or pass --scene FILE",
              file=sys.stderr)
        return 1
    build = read_json(src("build"), warn=False)
    if not build:
        # Standalone: the document is the manifest -- its markers are the shots.
        build = {"shots": shots_from_scene(Path(scene).read_bytes()),
                 "scene": {}, "placeholders": [], "findings": [], "from": "document"}
        if not args.quiet:
            print("note: no build.json; sampling from the document's own markers",
                  file=sys.stderr)
    script = read_json(src("script"), warn=False) or {}
    voice = read_json(src("voice"), warn=False) or {}
    sound = read_json(src("sound"), warn=False) or {}
    gate = read_json(src("gate"), warn=False)
    styles = read_json(src("styles"), warn=False) or {}
    home = Path(scene).parent if project.standalone else project.root
    # The pass this one is compared with (AUTO-108): named by --previous
    # (`yt improve` names its best kept pass), else the latest scored one.
    # A name that is not a scored pass stops here, before anything renders.
    named = getattr(args, "previous", None)
    chosen = None
    if named:
        chosen = read_json(home / QA_DIR / named / "scorecard.json", warn=False)
        if not isinstance(chosen, dict) or chosen.get("verdict") == NOT_EVIDENCED:
            print(f"error: --previous {named}: {home / QA_DIR / named} has no scored "
                  "scorecard.json to compare with", file=sys.stderr)
            return 2
        chosen.setdefault("pass", named)
    # The video comparison (AUTO-128): asked for by --against alone, and
    # refused here, before anything renders, when it could not be asked
    # fairly. $YT_COMPARE_MODEL is read only once --against is given.
    against = getattr(args, "against", None)
    flag_model = getattr(args, "compare_model", None)
    compare_model = (flag_model or os.environ.get(cutcompare.MODEL_ENV) or None
                     if against else None)
    said = cutcompare.refusal(against, flag_model, compare_model, cutcompare.author_models(
        {stage: src(stage) for stage in cutcompare.AUTHORS}) if against else [])
    if said:
        print(said, file=sys.stderr)
        return 2
    # --listen (AUTO-126): everything it needs, checked before anything renders.
    prepared = None
    if getattr(args, "listen", False):
        try:
            prepared = listen_pass.preflight(
                args, project, home=home, scene=scene, build=build, script=script,
                voice=voice, sound=sound, title=idea.title,
                house=house.for_args(args, project, "qa")[0])
        except listen_pass.Refused as exc:
            for line in str(exc).splitlines():
                print(f"error: --listen: {line}", file=sys.stderr)
            return 1
    # --watch (AUTO-127): the gateway, the catalog, the model and a render
    # host, checked before a pass directory is made or anything renders.
    watching, watched = None, None
    rhythm_shared: dict = {}      # the rhythm scan of the cut --watch sends (AUTO-141)
    if getattr(args, "watch", False):
        from . import watch as watch_pass   # it reads this module's owners and duties
        if not args.reuse and not (build or {}).get("shots"):
            # sample() would stop on this after the watch was paid for
            print("error: the build manifest has no shots to sample", file=sys.stderr)
            return 1
        try:
            watching = watch_pass.preflight(args, project, home=home, sources={
                stage: src(stage) for stage in watch_pass.AUTHOR_STAGES})
        except watch_pass.Refused as exc:
            for line in str(exc).splitlines():
                print(f"error: --watch: {line}", file=sys.stderr)
            return 1

    # -- the evidence --------------------------------------------------------
    # vfx.json is read once here: the build stills (AUTO-145), the proof
    # frames, the reach summary and the continuity blockers all read this
    # one copy.
    vfx_doc = read_json(project.source("vfx"), warn=False) if project.source("vfx") else None
    builds = build_shots(vfx_doc, build)
    if args.reuse:
        pass_dir = home / QA_DIR / args.reuse
        samples = read_json(pass_dir / "samples.json", warn=False, shape=list)
        if not samples:
            print(f"error: {pass_dir} has no samples.json to reuse", file=sys.stderr)
            return 1
        problems = []
        # A reused pass is never watched again: its record, if it has one.
        watched = watching.reused if watching is not None else None
    else:
        pass_dir = next_pass(home)
        if watching is not None:
            # The watch runs first, so what it saw becomes stills of this pass.
            pass_dir.mkdir(parents=True, exist_ok=True)
            treatment = ((styles.get("styles") or [{}])[0] if isinstance(
                styles.get("styles"), list) else styles.get("treatment"))
            rules_now, digest_now = house.for_args(args, project, "qa")
            watched = watch_pass.watch(project, pass_dir, watching, args, home=home,
                                       build=build, title=idea.title, treatment=treatment,
                                       house=rules_now, house_digest=digest_now,
                                       while_up=rhythm_while_up(args, project, rhythm_shared))
            if not args.quiet:
                print(f"[qa] watch: {watched['status']}"
                      + (f" ({watched['reason']})" if watched.get("reason") else "")
                      + f", {len(watched.get('findings') or [])} finding(s)", file=sys.stderr)
        samples = sample(build, limit=args.frame_limit, voice=voice, builds=builds,
                         **({"watched": watch_pass.candidates(watched)}
                            if watched is not None else {}))
        if not samples:
            print("error: the build manifest has no shots to sample", file=sys.stderr)
            return 1
        pass_dir.mkdir(parents=True, exist_ok=True)
        write_json(pass_dir / "samples.json", samples)
        if not args.quiet:
            bursts = sum(len((s.get("burst") or {}).get("frames", [])) for s in samples)
            print(f"[qa] {pass_dir.name}: {len(samples)} render jobs, one frame window "
                  f"each ({bursts} frames, the stills among them)", file=sys.stderr)
        problems = render_frames(args, project, scene, samples, pass_dir / "frames")
        for problem in problems:
            print(f"warning: {problem}", file=sys.stderr)
    # The narration beside the frames, as evidence: in the pass directory the
    # inspection's digest covers it, so a changed line is a new review and an
    # unchanged one is served from the cache. A reused pass keeps the
    # narration written when its frames were sampled.
    narration_file = pass_dir / NARRATION
    if not (args.reuse and narration_file.is_file()):
        # A reused pass from before narration.txt gets one from the files as
        # they are now, and its first line says so: its frames may be older.
        said = narration_text(script, build, voice, later=bool(args.reuse))
        if said:
            narration_file.write_text(said, encoding="utf-8")
    frames_dir = pass_dir / "frames"
    have = {p.name for p in frames_dir.glob("*.png")} if frames_dir.is_dir() else set()
    if not have:
        print("error: no frame was rendered, so there is nothing to judge. "
              "Is a render host reachable (RS_RENDER_HOSTS)?", file=sys.stderr)
        return 1

    # -- what the frames measure, before anyone looks --------------------------
    render_report = read_json(project.source("render"), warn=False)
    diagnostics = engine_diagnostics(render_report, build.get("shots", []))
    # The words on screen at each still (AUTO-145), counted from the
    # document the frames were rendered from, never read off a frame.
    try:
        text_nodes = geo._composed(Path(scene).read_text(encoding="utf-8", errors="replace"),
                                   "", Path(scene).parent, kinds=geo.TEXT_KINDS)
    except (OSError, ValueError):
        text_nodes = []

    def on_screen(t: float, label: str) -> list[dict]:
        return screen_text(text_nodes, t, (diagnostics.get(label) or {}).get("off_camera"))
    measure(samples, frames_dir, screen=on_screen)
    write_json(pass_dir / "samples.json", samples)        # the plan, and now the numbers
    # Does the hero pass through the set? The still cannot say; the
    # engine's evaluated frame graph and the document's geometry can.
    sampled = [s for s in build.get("shots", []) if str(s.get("label", "")) in
               {str(row.get("label", "")) for row in samples}]
    geometry_report = GEOMETRY(args, project, scene, sampled)
    if not args.quiet and geometry_report.get("status") != "ok":
        print(f"warning: geometry not checked: {geometry_report.get('why', '?')}", file=sys.stderr)
    mechanical = mechanical_findings(samples, diagnostics=diagnostics,
                                     geometry=geometry_by_shot(geometry_report, build.get("shots", [])),
                                     merged=merged_by_shot(geometry_report, build.get("shots", [])),
                                     supers=screenplay_supers(script))
    # The film against the authors' proof frames of the same worlds.
    proof_found, proof_note = proof_frame_findings(
        args, project, scene, vfx_doc, home, build.get("shots", []), pass_dir)
    mechanical += proof_found
    reowned = reown_by_proof(mechanical, proof_found)
    # The film's rhythm (AUTO-141): after the re-owning, which never moves
    # one of these, and filed beside the other mechanical findings.
    rhythm_record = rhythm_pass(args, project, build, voice, pass_dir,
                                reused=bool(args.reuse), shared=rhythm_shared)
    rhythm_block = {k: v for k, v in rhythm_record.items() if k != "scores"}
    mechanical += rhythm_block["findings"]
    if proof_note and not args.quiet:
        print(f"note: {proof_note}", file=sys.stderr)
    if reowned and not args.quiet:
        print(f"note: {reowned} mechanical finding(s) on a mismatched world's shots "
              "now name build as owner", file=sys.stderr)
    sheet = contact_sheet(samples, frames_dir, pass_dir / "contact-sheet.png")
    if not args.quiet:
        for f in mechanical:
            print(f"[qa] mechanical [{f['severity']}] {_stamp(f['timestamp'])} shot "
                  f"{f['shot']}: {f['problem']}", file=sys.stderr)

    # -- what the reviewer sees beside each frame ----------------------------
    measurements = stage_measurements(
        build, voice, sound=sound, gate=gate, geometry=geometry_report, styles=styles,
        reach=(vfx_doc or {}).get("reach") if project.source("vfx") else None,
        rhythm=rhythm_block)
    blockers = mechanical_blockers(build, gate=gate) + vfx_blockers(vfx_doc) + problems
    previous = chosen
    for earlier in [] if named else reversed(_passes(home)):
        if earlier != pass_dir:
            previous = read_json(earlier / "scorecard.json", warn=False)
            # A pass QA could not score (AUTO-107) has nothing to compare
            # with; the one before it is the previous measurement.
            if previous and previous.get("verdict") != NOT_EVIDENCED:
                break
            previous = None
    if not named and previous is not None:
        previous = kept_instead(home, previous) or previous

    # What a --frames below the shot count (or past the default) left
    # unseen is said to the reviewer before it scores, and in the scorecard.
    missing = unsampled(build, samples)
    total_shots = len(build.get("shots", []))
    # And which narrated lines no still shows where they start (AUTO-104).
    narrated = narrated_lines(build, voice)
    dropped_lines = lines_unsampled(build, voice, samples)

    # What heard the film's sound (AUTO-105): each listening review marked
    # with what it heard, current only while the film carries that. A
    # build older than the file a review heard carries an older mix.
    built_before = set()
    build_file = src("build")
    for source in LISTENERS:
        heard_file = src(source)
        try:
            if (build_file and heard_file and Path(build_file).stat().st_mtime
                    < Path(heard_file).stat().st_mtime):
                built_before.add(source)
        except OSError:
            pass
    listening = listening_evidence(
        sound, voice, built_before=built_before,
        where="" if project.standalone else f"--project {project.root}")
    # The film's own audio, heard (AUTO-126): one more row of the same
    # evidence, current when the listener answered.
    rules, digest = house.for_args(args, project, "qa")
    heard = None
    if prepared is not None:
        heard = listen_pass.listen(project, pass_dir, prepared, args, house=rules,
                                   house_digest=digest)
        listening.append(listen_pass.evidence_row(
            heard, "" if project.standalone else f"--project {project.root}"))
        if not heard["current"] and not args.quiet:
            print(f"warning: --listen: {heard['reason']}", file=sys.stderr)
    listened = bool(heard and heard["current"])
    unheard = audio_unheard(listening, silent=silent(build))

    # -- the review ------------------------------------------------------------
    # The watched findings with a still are put to the reviewer (AUTO-127).
    asked_watched = watched is not None and watch_pass.asks(watched, samples)
    reviewer = Harness(project.root, "qa", model=args.model,
                       role=ROLE_LISTENED if listened else ROLE,
                       house=rules, house_digest=digest,
                       fresh=getattr(args, "fresh", False), verbose=not args.quiet,
                       timeout=1800.0)
    readings: list[dict] = []
    card = score_evidence(
        reviewer, pass_dir, title=idea.title, build=build, script=script, voice=voice,
        samples=samples, frames_dir=frames_dir, listening=listening, mechanical=mechanical,
        diagnostics=diagnostics, measurements=measurements, blockers=blockers,
        extra_briefs=[*([listen_pass.brief_for(heard)] if listened else []),
                      *([watch_pass.brief_for(watched, samples)] if asked_watched else [])],
        watched=asked_watched, quiet=args.quiet, readings=readings)
    if heard is not None:
        card = listen_pass.merge_into(card, heard)     # the listener's own findings
    if watched is not None:
        # Only what two samples confirmed on its still joins the findings.
        card = watch_pass.merge_into(card, watched, readings, samples, frames_dir)
    evidenced = card["verdict"] != NOT_EVIDENCED
    if not evidenced and not args.quiet:
        print(f"[qa] {pass_dir.name}: {card['scoring']['valid']} of "
              f"{card['scoring']['asked']} scoring samples answered; not evidenced",
              file=sys.stderr)

    # -- side by side with the previous kept cut (AUTO-108) ----------------------
    # One reading's absolute scores move about as much as a revision does;
    # the same moment of both cuts, side by side, says which is better. Its
    # own decision after the scoring samples, whose prompts it leaves alone.
    cut = cut_digest(Path(scene).read_bytes(), Path(scene).parent)
    # The video comparison (AUTO-128), when --against asks for it: the two
    # cuts watched whole through the gateway, on a harness of its own (no
    # revision notes, its own timeout and account). Done, its entries are
    # the tally and the still pairs are not asked; skipped or failed, it
    # files nothing and the still pairs run as they would without it.
    compared, watcher = None, None
    if against:
        old_cut, new_cut = Path(against).expanduser(), home / "video.mp4"
        why = ("--reuse scores the frames of an earlier pass again; its cut is not "
               "compared" if args.reuse else
               "the pass is not evidenced; it is compared when it is scored again"
               if not evidenced else cutcompare.skip_reason(old_cut, new_cut, Path(scene)))
        if not why:
            try:
                watcher = Harness(project.root, "qa", model=compare_model, engine="gateway",
                                  role=cutcompare.COMPARE_ROLE, house=rules,
                                  house_digest=digest, feedback=False,
                                  fresh=getattr(args, "fresh", False),
                                  verbose=not args.quiet, timeout=cutcompare.COMPARE_TIMEOUT)
            except HarnessError as exc:
                why = str(exc)
        compared = (cutcompare.compare_cuts(watcher, old_cut, new_cut, pass_name=pass_dir.name)
                    if watcher else cutcompare.record("skipped", why, model=compare_model,
                                                      previous=old_cut, new=new_cut))
        write_json(pass_dir / cutcompare.RECORD, compared)
        if compared["status"] != "done":
            card["not_evidenced"] = list(card.get("not_evidenced") or []) + [
                f"video comparison {compared['status']}: {compared['reason']}"]
            if not args.quiet:
                print(f"[qa] video comparison {compared['status']}: {compared['reason']}",
                      file=sys.stderr)
    if compared and compared["status"] == "done":
        side = _side(previous.get("pass") if previous else None, "done", source="video",
                     model=compare_model, file=str(against),
                     decision=[o["decision"] for o in compared["orders"].values()],
                     entries=compared["entries"], tally=compared["tally"],
                     overall=compared["overall"], criteria=compared["criteria"],
                     comparison=cutcompare.RECORD)
    elif args.reuse:
        # The frames are that pass's, drawn from the cut it recorded then,
        # whatever scene.xml is now: the card keeps that cut (None for a
        # pass from before AUTO-108), so no later pass takes these frames
        # for another cut's.
        cut = (read_json(pass_dir / "scorecard.json", warn=False) or {}).get("cut")
        side = _side(previous.get("pass") if previous else None, "skipped",
                     "--reuse scores the frames of an earlier pass again; its cut is "
                     "not compared")
    elif not evidenced:
        side = _side(previous.get("pass") if previous else None, "skipped",
                     "the pass is not evidenced; it is compared when it is scored again")
    else:
        side = side_by_side(args, reviewer, pass_dir, previous, home=home, scene=scene,
                            samples=samples, cut=cut, script=script, quiet=args.quiet)
    if side.get("unpaired"):
        card["not_evidenced"] = list(card.get("not_evidenced") or []) + [
            f"side by side: {len(side['unpaired'])} moment(s) not compared with "
            f"{side['against']}: {side['unpaired'][0]['why']}"]

    # -- the previous findings, rechecked (AUTO-106) ------------------------------
    # The scores are filed above, blind; this decision reads the previous
    # pass's findings against this pass's stills and is kept beside them.
    rechecked = (recheck(reviewer, pass_dir, previous, quiet=args.quiet)
                 if getattr(args, "recheck", False) else None)

    # -- the bar, applied; the pass, kept ----------------------------------------
    # What the measurements proved overrides what the reviewer awarded: a
    # silent film scores no sound, and a blocking mechanical finding caps
    # the categories the frames measure.
    work = Path(args.work) if getattr(args, "work", None) else Path(project.root).parent
    card = unproven_satisfaction(card, retention=(work / RETENTION).is_file())
    card = note_unsampled(card, missing, total=total_shots, limit=args.frame_limit,
                          reused=bool(args.reuse))
    if missing and not args.quiet:
        print(f"note: {len(missing)} shot(s) not sampled: {', '.join(missing)}",
              file=sys.stderr)
    card = note_lines_unsampled(card, dropped_lines, total=len(narrated),
                                limit=args.frame_limit, stills=len(samples),
                                reused=bool(args.reuse))
    if dropped_lines and not args.quiet:
        print(f"note: {len(dropped_lines)} of {len(narrated)} narrated line(s) have "
              "no still where they start (scorecard: lines_unsampled)", file=sys.stderr)
    card = note_builds(card, builds, samples, reused=bool(args.reuse))
    capped: list[str] = []
    if evidenced:
        # The unheard audio categories were left out of the medians
        # (combine_samples); score_unheard records them not evidenced and
        # makes the overall the mean of the scored medians.
        card = score_silence(card, build)
        card = score_unheard(card, unheard)
        scores, overall, capped = cap_scores(card.get("scores", {}),
                                             card.get("overall", 0), mechanical)
        card["scores"], card["overall"] = scores, overall
        card["spread"] = rule_spread(card.get("spread"), silent=silent(build),
                                     capped=capped)
    if capped and not args.quiet:
        print(f"note: {', '.join(capped)} capped at {MECHANICAL_CAP:g}: a blocking "
              "mechanical or rhythm finding stands", file=sys.stderr)
    card["not_evidenced"] = list(card.get("not_evidenced") or []) + rhythm_not_evidenced(
        rhythm_block)
    all_blocking = blockers + [str(b) for b in card.get("blocking", [])
                               if str(b) not in blockers]
    all_blocking += [f"{f.get('problem')} at {f.get('timestamp')}s"
                     for f in card.get("findings", []) if f.get("severity") == "blocking"]
    all_blocking += [mechanical_line(f) for f in mechanical if f["severity"] == "blocking"]
    if evidenced:
        # The bar reads the median scores, never one sample's.
        verdict, reasons = verdict_of(card.get("scores", {}), float(card.get("overall", 0)),
                                      blocking=all_blocking,
                                      unheard=card.get("audio_not_evidenced"))
    else:
        verdict, reasons = NOT_EVIDENCED, [
            f"fewer than two of the {card['scoring']['asked']} scoring samples were valid "
            f"({card['scoring']['valid']}): nothing is scored on one reading"]
    if evidenced:
        # The bar names the rhythm finding that holds editing_pacing down,
        # whether the cap lowered the reviewer's score or it was already
        # under: either way that finding is why the category cannot pass.
        held = [f for f in mechanical if f["severity"] == "blocking"
                and f.get("kind") in RHYTHM_KINDS]
        for key in RHYTHM_CAPPED if held else ():
            how = "capped at" if key in capped else "held at or under"
            reasons.append(f"{key} {how} {MECHANICAL_CAP:g} by {held[0]['kind']}: "
                           f"{held[0]['problem']}")
    if evidenced and card.get("verdict") != verdict and not args.quiet:
        print(f"note: the reviewer said {card.get('verdict')!r}; the bar says "
              f"{verdict!r} ({'; '.join(reasons)})", file=sys.stderr)
    seen = scene_digest(Path(scene).read_bytes())
    card = dict(card) | {"pass": pass_dir.name, "verdict": verdict, "bar": reasons,
                         "scene_sha256": seen,
                         "blocking": all_blocking, "frames": sorted(have),
                         "samples": samples, "mechanical": mechanical, "capped": capped,
                         "rhythm": rhythm_block,
                         "proof_note": proof_note, "listening": listening,
                         "cut": cut, "side_by_side": side,
                         "contact_sheet": sheet.name, "engine": reviewer.report(),
                         "usage": spend.from_report(reviewer.report())}
    if rechecked is not None:
        card["recheck"] = rechecked
    if compared is not None:
        # The comparison's own account beside the reviewer's; `usage` is
        # both, so what the pass cost counts it once (AUTO-128).
        card["against"] = {k: compared.get(k) for k in ("status", "reason", "model",
                                                         "overall", "cost_usd")}
        card["against"] |= {"file": str(against), "record": cutcompare.RECORD,
                            "decisions": [o["decision"] for o in compared["orders"].values()]}
    if watcher is not None:
        card["compare_engine"] = watcher.report()
        card["usage"] = cutcompare.merged_usage(reviewer.report(), watcher.report())
    if heard is not None:
        # The listener is a harness of its own: its record beside the
        # reviewer's, its cost in the pass's usage.
        card["listen"] = listen_pass.card_entry(heard)
        card["usage"] = spend.merge_records(card["usage"], spend.from_report(heard["engine"]))
    if watched is not None:
        # The watcher is a harness of its own too (AUTO-127). A reused
        # pass's record was paid for by that pass, so it books nothing here.
        card["watch"] = watch_pass.card_entry(watched) | (
            {"reused_pass": args.reuse} if args.reuse else {})
        if watched.get("engine") and not args.reuse:
            card["watch_engine"] = watched["engine"]
            card["usage"] = spend.merge_records(card["usage"],
                                                spend.from_report(watched["engine"]))
    diff = compare(previous, card)
    card["comparison"] = diff
    write_json(pass_dir / "scorecard.json", card)
    (pass_dir / "findings.md").write_text(
        findings_md(pass_dir.name, card, samples, diff, reasons=reasons), encoding="utf-8")
    # AUTO-156: whether this judge is the one the QA fixtures last passed.
    # Recorded and printed; nothing reads it to gate READY, doctor or improve.
    calibration = judge_calibration(workspace.work_dir(args), args.model, digest,
                                    listened=listened, watched=asked_watched)
    write_json(home / "qa.json", {
        "idea": {"title": idea.title}, "pass": pass_dir.name, "verdict": verdict,
        "overall": card.get("overall"), "scores": card.get("scores"),
        "spread": card.get("spread"),
        "scoring": {k: card["scoring"][k] for k in ("asked", "valid")},
        "scene_sha256": seen, "blocking": len(all_blocking), "findings": len(card.get("findings", [])),
        "low_confidence": len(card.get("low_confidence") or []),
        "mechanical": len(mechanical), "capped": capped,
        "audio_not_evidenced": card.get("audio_not_evidenced") or {},
        "regressions": diff["regressions"], "reasons": reasons,
        "side_by_side": {"against": side["against"], "status": side["status"],
                         "total": side["tally"]["total"]},
        **({"recheck": {"against": rechecked["against"], "status": rechecked["status"],
                        **rechecked["tally"]}} if rechecked is not None else {}),
        "scorecard": str(pass_dir / "scorecard.json"), "house": digest,
        "judge_calibration": calibration, "usage": card["usage"]})

    print(f"\n{idea.title} — QA {pass_dir.name}: {verdict}  (overall {card.get('overall')}, "
          f"the median of {card['scoring']['valid']} of {card['scoring']['asked']} samples)")
    print(judge_line(calibration))
    for key in CATEGORIES if evidenced else ():
        delta = diff["deltas"].get(key)
        if card["scores"].get(key, 0) is None:
            print(f"  {key:<20}  --   not evidenced: READY cannot be judged")
            continue
        print(f"  {key:<20} {float(card['scores'].get(key, 0)):>4.1f}"
              f"  spread {float((card.get('spread') or {}).get(key, 0)):.1f}"
              + (f"  ({delta:+.1f})" if delta is not None else "")
              + ("  capped" if key in capped else ""))
    if reasons:
        print("\n  not ready because:")
        for r in reasons:
            print(f"    - {r}")
    if mechanical:
        print(f"\n  mechanical findings ({len(mechanical)}):")
        for f in mechanical:
            print(f"    [{f['severity']}] {_stamp(f['timestamp'])} shot {f['shot']} "
                  f"{f['kind']}: {f['problem'][:90]} -> {f['owner']}")
    if diff["regressions"]:
        print(f"\n  REGRESSED since {diff['against']}: {', '.join(diff['regressions'])}")
    if side["status"] == "done" and side.get("source") == "video":
        total = side["tally"]["total"]
        print(f"\n  video comparison against {side['against'] or against}: better "
              f"{total['better']}, worse {total['worse']}, same {total['same']} "
              f"({len(side['entries'])} categories)")
    elif side["status"] == "done":
        total = side["tally"]["total"]
        print(f"\n  side by side against {side['against']}: better {total['better']}, "
              f"worse {total['worse']}, same {total['same']} "
              f"({len(side['moments'])} moments)")
    if rechecked is not None and rechecked["status"] == "done":
        t = rechecked["tally"]
        print(f"\n  {rechecked['against']}'s findings rechecked: {t['stands']} stand, "
              f"{t['gone']} gone, {t['cannot_tell']} cannot tell")
    elif rechecked is not None:
        print(f"\n  recheck {rechecked['status']}: {rechecked['why']}")
    owners = {}
    for f in card.get("findings", []):
        owners.setdefault(f.get("owner", "?"), []).append(f)
    if owners:
        print("\n  findings by owner:")
        for owner, rows in owners.items():
            print(f"    {owner}: {len(rows)} -- " + "; ".join(
                str(r.get("problem", ""))[:50] for r in rows[:3]))
    print(f"\nwrote {pass_dir / 'scorecard.json'}", file=sys.stderr)
    print(f"wrote {pass_dir / 'findings.md'}", file=sys.stderr)
    print(f"wrote {home / 'qa.json'}", file=sys.stderr)
    return 0 if verdict == READY else 1


def main(argv: list[str] | None = None) -> int:
    """`python -m ytstudio.qa calibrate <dir>` and `python -m ytstudio.qa
    capture-fixture <pass> <case>`: module entry points, not `yt` stages,
    since they read fixtures and not a film. calibrate exits 0 when the
    judge passed the set, 1 when it did not, 2 on a refused argument."""
    from . import flags

    parser = argparse.ArgumentParser(
        prog="python -m ytstudio.qa",
        description="Check the QA judge against labelled fixture films, or capture a "
                    "pass of one of your own films as a fixture case.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("calibrate", help="score every fixture case and print the table")
    p.add_argument("directory", help="the cases, e.g. tests/fixtures/qa")
    p.add_argument("--model", default="sonnet",
                   help="the judge's model, as `yt qa --model` names it (default sonnet)")
    p.add_argument("--work", default="work",
                   help=f"where the cases' passes and readings are cached and the report is "
                        f"written (<work>/{CALIBRATION_QA}/; default work)")
    flags.budget(p, default=CALIBRATE_QA_BUDGET_USD,
                 help=f"read no further case once this much is spent (default "
                      f"{CALIBRATE_QA_BUDGET_USD:g})")
    flags.fresh(p, help="ask every reading again instead of reusing the ones on disk")
    p.add_argument("--quiet", action="store_true", help="print the table only")
    c = sub.add_parser("capture-fixture",
                       help="cut one `yt qa` pass of your own film down to a fixture case")
    c.add_argument("pass_dir", help="the pass, e.g. work/my-film/qa/pass-03")
    c.add_argument("case", help="the case folder to write, e.g. tests/fixtures/qa/my-film")
    args = parser.parse_args(argv)
    if args.command == "capture-fixture":
        try:
            summary = capture_fixture(args.pass_dir, args.case)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"wrote {summary['case']}: {summary['samples']} samples, "
              f"{summary['bytes']} bytes"
              + (f" (over the {FIXTURE_CASE_BYTES} a case may hold)"
                 if summary["over_budget"] else ""))
        print("label it: write expected.json beside samples.json (label \"owner\")",
              file=sys.stderr)
        return 0
    if not Path(args.directory).is_dir():
        print(f"error: no QA fixture cases at {args.directory}", file=sys.stderr)
        return 2
    report = calibrate(args.directory, work=args.work, model=args.model,
                       budget_usd=args.budget_usd, fresh=args.fresh, verbose=not args.quiet)
    print("\n".join(calibration_lines(report)))
    if not args.quiet:
        print(f"wrote {Path(args.work) / CALIBRATION_QA / CALIBRATION_REPORT}",
              file=sys.stderr)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    # Run as a program this file is `__main__`; `yt qa` imports it as
    # ytstudio.qa. The entry point is that one's, so one copy answers both.
    from ytstudio.qa import main as _main
    sys.exit(_main())
