#!/usr/bin/env python3
"""Plan the animation for one video and have the model author it for scene-render.

The stack is scene-render and sr-core, and the document is the interface. This
stage does not own a vocabulary of effects, a set of templates or a single
default size: the model authors scene-render documents itself, against the
full schema, through claude_engine. What this stage owns is the plumbing that
makes that honest -- the brief it writes, the workspace it builds, the checks
the author runs (the SDK's and the engine's own, on the render host), and the
map from shot label to symbol that `yt build` includes.

    yt vfx --title monty
    yt vfx --title monty --inspo "3Blue1Brown,Kurzgesagt" --budget-usd 3
    yt vfx --title monty --fresh                  # replan and re-author
    yt render --title monty --scene vfx/<section>.scene.xml   # one section's reel

Stages, cached under raw/: the visual system (10-style), the effects per
section with the shots each covers (11-effects: the phases one world passes
through, and the cuts, if any, the section genuinely needs), the authoring
(12-author: one claude_engine agent per section, working in
<project>/vfx/vNN/ with `sr.py` to ask the schema, check a document and
render its proof frames, writing one document with ONE <symbol> per section:
the shared world -- set, materials, light rig, hero model -- and one camera
keyed across the section, every shot a phase at its offset), the shot map and
validation (13-scene, no model), the reach audit (14-reach: which constructs
each symbol uses against everything the engine offers, and whether a stronger
one would carry the shot) and the refinement plan (15-plan, which takes the
audit's upgrades as work).

The frame gate renders a proof frame at each shot's midpoint and judges it
with `framecheck` (flat, black, clipped, too dark, hero or set out of the
camera's view); then the model reviews the frames through the harness against
the publication bar of docs/production.md and scores them 0-10. Under
`--bar` (default 7) the findings go back to the author, up to
`--rounds` rounds. Every round of authoring lands in a new vfx/vNN/; an
earlier version is never overwritten, and `--fresh` opens the next one.

Writes into the project: vfx/vNN/<section>.scene.xml for every section,
vfx.json (plan, totals, the active version, the review rounds and scores, and
the shot -> {file, symbol} map `yt build` reads), vfx.md, vfx.csv.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from xml.etree import ElementTree as _ET
from dataclasses import asdict
from pathlib import Path

from claude_engine import ClaudeError, EngineConfig, Usage
from . import (flags, fonts, framecheck, geometry, harness, house, reach, render, sdkkit, spend,
               workspace)
from .core import Idea, _key, _merge_usage, read_json, slugify, write_json
from .framecheck import DIM_STD, FLAT_STD, frame_stats, judge_frame  # noqa: F401  (re-exported)
from .ideas import CHANNEL_STYLES
from .research import Dossier
from .scene_sdk import sdk
from .script import Shot, load_script, visible_text

#: The stack. Stated once, read by the prompts and the reports.
STACK = "scene-render (Rust engine) + sr-core (schema and SDK)"

#: The mission, as the owner states it: the aesthetic is an editorial variable
#: decided per video in `yt styles`, never a constant of this stage.
MISSION = (
    "This pipeline creates and composes top-tier YouTube videos on scene-render "
    "and sr-core: visually intense explainers with voice-over, motion graphics, "
    "2D, 2.5D and 3D, multi-aesthetic according to the editorial decisions. The "
    "treatment chosen for this video is the variable; the schema -- shapes, "
    "text, charts, layout groups, symbols, images and video, object3D, cameras, "
    "lights, materials, particles, physics, fluids, parallax through z -- is "
    "what it is drawn with. Draw what the treatment calls for, with whatever "
    "the schema provides, and say when it cannot."
)

#: Where the authored documents live, under the project: vfx/vNN/, one
#: directory per round of authoring, the highest number the active one.
SCENE_DIR = "vfx"
VERSION_RE = re.compile(r"^v(\d{2,})$")

#: A section's symbol is its world; the id says so.
WORLD_PREFIX = "world-"

#: The ten categories of docs/production.md section 2, in its order. The
#: review of proof frames scores what frames can show; the audio categories
#: are listed so the reviewer names them as not evidenced rather than guesses.
CATEGORIES = ("visual_quality", "animation_vfx", "cinematography", "editing_pacing",
              "graphics_overlays", "voiceover", "sound_design", "music_score",
              "narrative_clarity", "overall_production")

#: Be-especially-critical-of, docs/production.md section 3, as the reviewer reads it.
CRITICAL_OF = (
    "generic or obviously synthetic imagery; weak maps and diagrams; fake-looking "
    "3D; flat lighting; a camera that never moves, or that zooms and pans without "
    "reason; repetitive parallax; excessive glow and bloom; low-resolution assets; "
    "inconsistent visual styles between scenes; poor masking and compositing; "
    "typography that looks templated; overlays that float disconnected from the "
    "scene; long stretches with no visual evolution; narration describing "
    "something the picture never demonstrates; transitions with no narrative "
    "purpose; too much information arriving at once")

#: Tools the authoring agent gets: the files of its workspace and a shell for
#: `python3 kit.py` and `python3 sr.py`. Nothing reaches outside the workspace
#: by design of the brief.
AUTHOR_TOOLS = sdkkit.AUTHOR_TOOLS

#: One authoring call may render proof frames many times; a revision round
#: that answers a review needs more than the harness's default 900 s.
AUTHOR_TIMEOUT = 3600.0

#: What a section may spend in all: its author sessions and its reviews. A session
#: is capped at AUTHOR_SESSION_USD (docs/throughput.md: authors spent up to $4) and
#: a review at REVIEW_USD, the default `--review-budget-usd` ($1.35-2.65 measured
#: uncapped); the review's cap is held back from a session's cap for the review
#: that follows it. A revision needs MIN_SESSION_USD to be worth starting. The
#: section's budget is what the default rounds cost at the most, so the estimate
#: the run prints and the budget say the same thing (AUTO-79).
AUTHOR_SESSION_USD = 4.0
REVIEW_USD = 2.0
MIN_SESSION_USD = 1.0
DEFAULT_ROUNDS = 2
SECTION_BUDGET_USD = (1 + DEFAULT_ROUNDS) * (AUTHOR_SESSION_USD + REVIEW_USD)
#: The reviewer is never the author's model: a model never grades its own work.
REVIEWER_MODEL = "opus"


def estimate_spend(sections: int, revisions: int, section_usd: float,
                   stage_usd: float, *, review_usd: float = REVIEW_USD) -> dict:
    """What authoring `sections` sections is expected to cost at the most: every round
    (the first and each revision) is a session and a review at its cap."""
    rounds = 1 + max(0, revisions)
    per = rounds * (AUTHOR_SESSION_USD + review_usd)
    return {"sections": sections, "rounds": rounds, "per_section_usd": per,
            "review_usd": review_usd,
            "stage_usd": sections * per, "section_budget_usd": section_usd,
            "stage_budget_usd": stage_usd}


def estimate_line(e: dict) -> str:
    """The estimate, in one line, for printing before the first agent starts."""
    line = (f"estimate: {e['sections']} section(s) x {e['rounds']} round(s) x (author session "
            f"up to ${AUTHOR_SESSION_USD:g} + review up to ${e.get('review_usd', REVIEW_USD):g}) "
            f"= up to ${e['per_section_usd']:.2f} a section, ${e['stage_usd']:.2f} for the "
            f"stage; budgets: ${e['section_budget_usd']:g} a section, "
            f"${e['stage_budget_usd']:g} for the stage")
    if e["per_section_usd"] > e["section_budget_usd"] or e["stage_usd"] > e["stage_budget_usd"]:
        line += ("; a section that would pass its budget stops, and ends on its best "
                 "reviewed round")
    return line


class SpendBudget:
    """One account for a run: a section's spend is its author sessions plus its
    reviews, the stage's is its sections'. Money is read live from the engines'
    `Usage.cost_usd`, which already holds a cut-off call's spend
    (`timed_out_cost_usd`). A session is `grant`ed its cap, and the grant is held
    against the budgets until it is `release`d, so sections running side by side
    cannot together pass the stage."""

    def __init__(self, section_usd: float, stage_usd: float) -> None:
        self.section_usd = float(section_usd)
        self.stage_usd = float(stage_usd)
        self._usages: dict[int, dict[str, Usage]] = {}
        self._held: dict[int, float] = {}       # section -> its spend ceiling while a session runs
        self.stopped: dict[int, str] = {}
        self._lock = threading.Lock()

    def track(self, n: int, *, author: Usage, review: Usage) -> None:
        self._usages[n] = {"author": author, "review": review}

    def _part(self, n: int, which: str) -> float:
        usage = self._usages.get(n, {}).get(which)
        return usage.cost_usd if usage is not None else 0.0

    def spent(self, n: int) -> float:
        return self._part(n, "author") + self._part(n, "review")

    def stage_spent(self) -> float:
        return sum(self.spent(n) for n in self._usages)

    def section_left(self, n: int) -> float:
        return max(0.0, self.section_usd - self.spent(n))

    def stage_left(self) -> float:
        return max(0.0, self.stage_usd - self.stage_spent())

    def _free(self, n: int) -> float:
        """What the budgets leave section `n`: its own, and the stage's less what
        the other sections have spent or hold for a running session."""
        others = sum(max(self.spent(m), self._held.get(m, 0.0)) for m in self._usages if m != n)
        return min(self.section_usd - self.spent(n), self.stage_usd - others - self.spent(n))

    def available(self, n: int) -> float:
        with self._lock:
            return max(0.0, self._free(n))

    def grant(self, n: int, want: float, reserve: float = 0.0) -> float:
        """The cap of a session of section `n`: at most `want`, and what the budgets
        leave once `reserve` (the review that follows) is kept back. 0 when none."""
        with self._lock:
            cap = max(0.0, min(want, self._free(n) - reserve))
            if cap > 0:
                self._held[n] = self.spent(n) + cap + reserve
            else:
                self._held.pop(n, None)
            return cap

    def release(self, n: int) -> None:
        with self._lock:
            self._held.pop(n, None)

    def row(self, n: int) -> dict:
        timed_out = sum(u.timed_out_cost_usd for u in self._usages.get(n, {}).values())
        return {"budget_usd": self.section_usd, "spent_usd": round(self.spent(n), 4),
                "author_usd": round(self._part(n, "author"), 4),
                "review_usd": round(self._part(n, "review"), 4),
                "timed_out_usd": round(timed_out, 4), "stopped": self.stopped.get(n)}

    def report(self) -> dict:
        return {"stage": {"budget_usd": self.stage_usd, "spent_usd": round(self.stage_spent(), 4)},
                "sections": {n: self.row(n) for n in sorted(self._usages)}}


FRAME = (1920, 1080)
HEX = re.compile(r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")

VFX_SYSTEM = (
    "You are a motion-graphics director for a factual YouTube channel whose "
    "only renderer is scene-render, a declarative engine with a large schema. "
    "You design the fewest visuals that carry the argument, you say for each "
    "what the viewer must understand that words alone would not deliver, and "
    "you give an honest cheaper fallback for every costly one."
)

AUTHOR_SYSTEM = (
    sdkkit.AUTHOR_SYSTEM + " A section is one world: one set, one hero model, one "
    "light rig and one camera whose path is keyed across the whole section, so the "
    "shots are phases of a single continuous take, never separate builds. Its proof "
    "frames come from `python3 sr.py frame <file>`, which renders each shot's "
    "midpoint the way `yt build` includes the world; Read every one. You never write "
    "prose where the brief asks for a program."
)

REVIEW_ROLE = (
    "You are the quality reviewer of docs/production.md looking at an author's "
    "proof frames before the film is built: one still per shot of one section, "
    "rendered exactly as `yt build` will include the section's world. You judge "
    "them by professional standards against the ten categories of the "
    "publication bar (" + ", ".join(CATEGORIES) + ") and you are especially "
    "critical of " + CRITICAL_OF + ". Frames cannot evidence voiceover, sound "
    "design or music; say so and do not score them from guesswork. A 9 is work "
    "that would not look out of place on a channel people subscribe to; 8 is "
    "competent and unremarkable; 6 is functional; 3 is a document that renders. "
    "Every finding names the frame, what is visible, why it lowers quality, and "
    "the fix in the document's own terms (camera, light, material, animation, "
    "geometry) that the author can apply without asking you anything. The author "
    "was handed the same checklist you judge by, and ran its mechanical items "
    "itself; spend your attention on the `[review]` items:\n"
    + framecheck.checklist_text()
)

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "frames": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "frame": {"type": "string"},
                    "shot": {"type": "string"},
                    "seen": {"type": "string"},
                    "findings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "category": {"type": "string", "enum": list(CATEGORIES)},
                                "problem": {"type": "string"},
                                "fix": {"type": "string"},
                                "severity": {"type": "string",
                                             "enum": ["blocking", "major", "minor"]},
                            },
                            "required": ["category", "problem", "fix", "severity"],
                        },
                    },
                },
                "required": ["frame", "seen", "findings"],
            },
        },
        "continuity": {"type": "string"},
        "not_evidenced": {"type": "array", "items": {"type": "string"}},
        # Only asked for when an approved keyframe exists: 0-10 fidelity to it.
        "fidelity": {"type": "number", "minimum": 0, "maximum": 10},
        "score": {"type": "number", "minimum": 0, "maximum": 10},
        "summary": {"type": "string"},
        # From round 1 on (`review_schema(delta=True)` requires it): the
        # revision against the round the section stands on. Promotion follows
        # the verdict, not the score, whose round-to-round noise is as large
        # as the gains.
        "previous": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["better", "same", "worse"]},
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "problem": {"type": "string"},
                            "status": {"type": "string",
                                       "enum": ["resolved", "persists", "regressed"]},
                        },
                        "required": ["problem", "status"],
                    },
                },
            },
            "required": ["verdict", "findings"],
        },
    },
    "required": ["frames", "score", "summary"],
}

#: The heading under which a revision's reviewer is handed the round before it.
PREVIOUS_ROUND = "The previous round, which this round must beat"


def review_schema(*, delta: bool) -> dict:
    """The review's schema: a revision's must carry the verdict on the round
    it was shown; the first round has nothing to be compared with."""
    if not delta:
        return REVIEW_SCHEMA
    return dict(REVIEW_SCHEMA, required=REVIEW_SCHEMA["required"] + ["previous"])

#: The continuity supervisor: one frame from each section, side by side.
CONTINUITY_ROLE = (
    "You are the film's continuity supervisor. You look at one proof frame from "
    "each section of a film, in film order, each rendered exactly as `yt build` "
    "will include it, and judge whether they read as one film: the same hero at "
    "the same scale, material and colour; the same world and light; one grade; a "
    "camera language that carries across the cuts. A change the editorial "
    "treatment calls for is not a break, and you say so. Every break names the "
    "sections it lies between, the one section whose document should change, and "
    "the fix in that document's own terms (camera, light, material, scale, grade) "
    "that its author can apply without asking you anything."
)

CONTINUITY_SCHEMA = {
    "type": "object",
    "properties": {
        "one_film": {"type": "boolean"},
        "score": {"type": "number", "minimum": 0, "maximum": 10},
        "breaks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sections": {"type": "array", "items": {"type": "string"}},
                    "owner_section": {"type": "string"},
                    "problem": {"type": "string"},
                    "fix": {"type": "string"},
                    "severity": {"type": "string", "enum": ["blocking", "major", "minor"]},
                },
                "required": ["sections", "owner_section", "problem", "fix", "severity"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["one_film", "score", "breaks", "summary"],
}

#: Effect families, roughly cheapest to most expensive to refine by hand.
EFFECT_TYPES = ("text-and-callout", "2d-diagram", "data-animation", "map-move",
                "photo-composite", "stock-treatment", "2d-character",
                "3d-render", "simulation", "compositing-vfx", "practical")


# --------------------------------------------------------------------------- #
# schemas
# --------------------------------------------------------------------------- #

STYLE_SCHEMA = {
    "type": "object",
    "properties": {
        "look": {"type": "string"},
        "palette": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "hex": {"type": "string"},
                    "use": {"type": "string"},
                },
                "required": ["name", "hex", "use"],
            },
        },
        "typography": {"type": "string"},
        "camera_language": {"type": "string"},
        "transitions": {"type": "string"},
        "motion_rules": {"type": "array", "items": {"type": "string"}},
        "avoid": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["look", "palette", "typography", "camera_language",
                 "transitions", "motion_rules", "avoid"],
}

EFFECTS_SCHEMA = {
    "type": "object",
    "properties": {
        "effects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": list(EFFECT_TYPES)},
                    "shots": {"type": "array", "items": {"type": "string"}},
                    "conveys": {"type": "string"},
                    "on_screen": {"type": "string"},
                    "data_or_claim": {"type": "string"},
                    "brief": {"type": "string"},
                    "seconds": {"type": "number"},
                    "difficulty": {"type": "integer", "minimum": 1, "maximum": 5},
                    "hours": {"type": "number"},
                    "technique": {"type": "array", "items": {"type": "string"}},
                    "inputs": {"type": "array", "items": {"type": "string"}},
                    "fallback": {"type": "string"},
                    "risk": {"type": "string"},
                    "template": {"type": "string"},
                },
                "required": ["name", "type", "shots", "conveys", "on_screen",
                             "brief", "seconds", "difficulty", "hours",
                             "technique", "fallback"],
            },
        },
        # A section is one continuous world by default; a cut is the exception
        # the plan has to ask for, by the shot label where the new world starts.
        "cuts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "before": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["before", "why"],
            },
        },
    },
    "required": ["effects"],
}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "build_order": {"type": "array", "items": {"type": "string"}},
        "templates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "covers": {"type": "string"},
                    "saves_hours": {"type": "number"},
                },
                "required": ["name", "covers", "saves_hours"],
            },
        },
        "cut_first": {"type": "array", "items": {"type": "string"}},
        "outsource": {"type": "array", "items": {"type": "string"}},
        "verdict": {"type": "string"},
    },
    "required": ["build_order", "templates", "cut_first", "verdict"],
}

OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "runtime": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "seconds": {"type": "integer"},
                    "beats": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["heading", "seconds", "beats"],
            },
        },
    },
    "required": ["runtime", "sections"],
}


# --------------------------------------------------------------------------- #
# input
# --------------------------------------------------------------------------- #

def load_dossier(path: Path, idea: Idea) -> dict:
    """Pull the outline, claims and assets out of a research dossier."""
    data = json.loads((path / "dossier.json").read_text(encoding="utf-8"))
    return {
        "idea": Idea.from_raw(data["idea"]) if data.get("idea") else idea,
        "outline": data.get("outline") or {},
        "claims": data.get("claims", []),
        "assets": data.get("assets", []),
        "premise": data.get("premise", ""),
    }


def shot_super(shot: dict) -> str:
    """The words a viewer reads in a shot (a screenplay dict), not its direction."""
    return visible_text(Shot.from_raw(shot))


def super_lines(shot: dict) -> list[str]:
    """Brief lines for a shot's super: the words, then the direction labelled as such."""
    words = shot.get("super") or ""
    direction = str(shot.get("on_screen") or "").strip()
    lines = [f"Super (the words a viewer reads): {words}"] if words else []
    if direction and " ".join(direction.split()) != " ".join(str(words).split()):
        lines.append(f"Direction for the super (not text on screen): {direction}")
    return lines


def script_sections(path: str | None) -> list[dict]:
    """The screenplay's sections with their shots, labels and timings."""
    if not path:
        return []
    sections, _ = load_script(path)
    return [{"heading": s.heading, "seconds": s.actual_seconds or s.seconds,
             "beats": s.beats,
             "shots": [{"label": sh.label, "at": sh.at, "seconds": sh.seconds,
                        "visual": sh.visual, "on_screen": sh.on_screen,
                        "super": visible_text(sh), "narration": sh.narration, "asset": sh.asset}
                       for sh in s.shots]}
            for s in sections]


#: Fact-check severities that bind what the picture may show.
BINDING_SEVERITIES = ("blocker", "important")


def picture_constraints(script: dict | str | Path | None) -> list[dict]:
    """The screenplay fact-check's binding flags (`yt script --verify` writes
    them to script.json under `check`), verbatim, each with the shot label it
    binds ("" for the whole film). They reach every author who draws the film:
    a claim the narration avoids can still be shown wrong by a picture."""
    data = script if isinstance(script, dict) else (read_json(script, warn=False) or {}
                                                     if script else {})
    labels = {sh.get("n"): str(sh.get("label") or "")
              for sec in data.get("sections") or [] if isinstance(sec, dict)
              for sh in sec.get("shots") or [] if isinstance(sh, dict) and "n" in sh}
    out = []
    for f in ((data.get("check") or {}).get("flags") or []):
        if not isinstance(f, dict) or f.get("severity") not in BINDING_SEVERITIES:
            continue
        try:
            n = int(f.get("n") or 0)
        except (TypeError, ValueError):
            n = 0
        out.append({"label": labels.get(n, "") if n else "", "severity": f["severity"],
                    "problem": str(f.get("problem", "")), "fix": str(f.get("fix", ""))})
    return out


def constraint_lines(constraints: list[dict], labels: set[str] | None = None) -> list[str]:
    """The binding flags for some shots (with the whole-film ones), as a brief
    section; [] when none applies."""
    rows = [c for c in constraints or []
            if labels is None or not c["label"] or c["label"] in labels]
    if not rows:
        return []
    lines = ["## Fact-check constraints (from `yt script --verify`; they outrank the "
             "treatment: a picture must not show what the research does not support)", ""]
    for c in rows:
        lines += [f"- [{c['severity']}] {'shot ' + c['label'] if c['label'] else 'whole film'}: "
                  f"{c['problem']}", f"  REQUIRED: {c['fix']}"]
    return lines + [""]


#: A symbol may fall this far short of its shots before it is an error.
TIMING_SLACK = 0.25


def measured_sections(sections: list[dict], words: dict[str, dict]) -> list[dict]:
    """The screenplay's sections on the narration's clock.

    `yt script` budgets a shot by words per minute; once `yt voice` has
    measured the take, that number is the shot's length and the take's
    position is its `at`, exactly as `yt build` will place it. A silent shot
    keeps its planned length and follows what came before. The plan is kept
    beside the measurement (`planned_seconds`, `timed_by`) so a brief can
    say which clock it is on. The input is not changed.
    """
    out: list[dict] = []
    cursor = 0.0
    for section in sections:
        shots: list[dict] = []
        for shot in section.get("shots") or []:
            take = words.get(str(shot.get("label")))
            row = dict(shot)
            if take and float(take.get("seconds") or 0) > 0:
                at, seconds = float(take.get("at", 0)), float(take["seconds"])
                row.update(at=round(at, 3), seconds=round(seconds, 3),
                           planned_seconds=shot.get("seconds"), timed_by="voice")
            else:
                at, seconds = cursor, float(shot.get("seconds") or 1)
                row.update(at=round(at, 3), seconds=seconds, timed_by="script")
            shots.append(row)
            cursor = max(cursor, at + seconds)
        measured = dict(section, shots=shots)
        if shots:
            measured["seconds"] = round(max(s["at"] + s["seconds"] for s in shots)
                                        - min(s["at"] for s in shots), 3)
        out.append(measured)
    return out


def voice_words(path: str | None) -> dict[str, dict]:
    """Per shot label: where the take sits and when each word is spoken.

    A beat should land on a spoken word, not on a fraction of the shot; the
    author gets the timings the narration itself reported.
    """
    data = read_json(path, warn=False) if path else None
    out: dict[str, dict] = {}
    for take in (data or {}).get("takes", []):
        words = [{"w": w.get("text", ""), "s": round(float(w.get("start", 0)), 2)}
                 for w in take.get("words", [])]
        out[str(take.get("label"))] = {"at": float(take.get("at", 0)),
                                       "seconds": float(take.get("seconds", 0)),
                                       "timing": take.get("timing", "none"),
                                       "words": words}
    return out


def ident(text: str) -> str:
    """An xs:ID: letters, digits, dashes, never starting with a digit."""
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-") or "x"
    return clean if clean[0].isalpha() or clean[0] == "_" else f"fx-{clean}"


def tokens_from(style: dict) -> dict[str, str]:
    """The <styles> tokens the author will reference as var(--name).

    Role names come from the palette's own words; `design.tokens()` supplies
    them when that module is present, the palette's stated uses otherwise.
    Every swatch is also available under its own slug, so nothing the style
    stage chose is unreachable.
    """
    try:
        from . import design  # optional: the standards session's role derivation
        if hasattr(design, "tokens"):
            got = {k: v for k, v in design.tokens(style).items() if HEX.match(str(v))}
            if got:
                return got
    except Exception:
        pass
    swatches = [c for c in (style or {}).get("palette", []) if HEX.match(str(c.get("hex", "")))]
    out: dict[str, str] = {}

    def claim(role: str, pattern: str) -> None:
        for c in swatches:
            if c["hex"] in out.values():
                continue
            if re.search(pattern, c.get("use", "") + " " + c.get("name", ""), re.I):
                out[role] = c["hex"]
                return

    claim("background", r"background|\bbg\b|canvas|base|fundo")
    claim("ink", r"\btext\b|ink|label|type|texto")
    claim("accent", r"accent|number|key|highlight|hero|destaque|número|numero")
    claim("muted", r"muted|secondary|dim|grid|support|apoio|secund")
    for c in swatches:
        name = slugify(c.get("name", ""), 24)
        if name and name not in out:
            out.setdefault(name, c["hex"])
    return out


# --------------------------------------------------------------------------- #
# the workspace the author works in
# --------------------------------------------------------------------------- #

#: A document the engine has rendered correctly, construct by construct, in
#: this project's own tests: tokens, layout groups (explicit children are
#: laid out; copies from a <repeat> are not), text presets including the
#: digit counter, a chart with comma-separated labels, a path with its box and
#: a transparent fill, a connector, a symbol and its instance. It is evidence
#: of what works, not the limit of what may be used: the 3D stage, cameras,
#: lights, materials, particles and physics are in the schema too.
EXAMPLES = """<?xml version="1.0" encoding="UTF-8"?>
<!-- Every construct here validated and rendered on this engine: the 2D stage in
     fx-stage, and in fx-room a lit 3D set with a physical camera, materials and
     particles, lifted from a rendered project, and in fx-surface a GLSL shader
     effect that ray-casts an analytic liquid surface (shaders/ring-surface.glsl,
     beside this file). pyro, fluid, ocean, fracture, softBody, flock and the
     other effect types exist too; describe them with sr.py. -->
<scene version="1.3">
  <project width="1920" height="1080" fps="30" duration="16" background="#14213D"/>
  <!-- a hero that rests on a surface is declared, so the geometry gate checks it
       touches (not floats, not sinks): "<hero id> <surface id> <seconds>" -->
  <metadata>
    <meta name="hero-rests" value="hero-block plinth 0"/>
  </metadata>
  <styles>
    <token name="background" value="#14213D"/>
    <token name="ink" value="#F2F5F7"/>
    <token name="accent" value="#3DD6FF"/>
    <token name="muted" value="#8E9BB0"/>
    <textStyle id="title" size="72" color="var(--ink)" weight="700"/>
  </styles>
  <!-- child order matters in hand-written XML: output comes before assets -->
  <output path="preview.mp4" codec="h264"/>
  <assets>
    <text id="t-title" text="A title" width="1200" height="160" size="72" color="var(--ink)" align="center"/>
    <text id="t-count" text="190 of 300" width="900" height="200" size="120" color="var(--accent)" align="center"/>
    <chart id="c-bars" kind="column" width="900" height="500" labels="option A,option B" showValues="true">
      <series name="trials" values="190 110" color="var(--accent)"/>
      <animate property="progress"><key time="0" value="0"/><key time="2" value="1" interpolation="ease-out"/></animate>
    </chart>
  </assets>
  <!-- materials are scene-level, after assets; lights come after the composition -->
  <materials>
    <material id="m-wall" doubleSided="true" baseColor="#B9AE9F" roughness="0.9"/>
    <material id="m-solid" doubleSided="true" baseColor="#6E7B8B" roughness="0.6"/>
    <material id="m-glow" baseColor="#FFF1D0" emissive="#FFF1D0" emissiveStrength="1.3" roughness="0.5"/>
    <material id="m-dust" baseColor="#E8B65C" emissive="#E8B65C" emissiveStrength="1" unlit="true" opacity="0.7" alphaMode="blend"/>
  </materials>
  <symbols>
    <!-- a 3D set: primitives with materials, a physical camera that moves, dust in the light -->
    <symbol id="fx-room" width="1920" height="1080" duration="4">
      <camera id="cam" x="-300" y="-45" z="-220" yaw="27" pitch="25" focalLength="50" depthOfField="true" fStop="4" focusDistance="160" near="5" far="4000" active="true">
        <animate property="x"><key time="0" value="-300"/><key time="4" value="-240" interpolation="ease-in-out"/></animate>
      </camera>
      <object3D id="floor" primitive="plane" material="m-wall" x="50" y="0" z="0" width="900" height="800" rotationX="90"/>
      <object3D id="wall" primitive="plane" material="m-wall" x="0" y="-150" z="400" width="900" height="300"/>
      <object3D id="window" primitive="plane" material="m-glow" x="210" y="-165" z="397" width="180" height="150" castShadow="false"/>
      <object3D id="plinth" primitive="box" material="m-solid" x="210" y="-37.5" z="230" width="180" height="75" depth="180"/>
      <!-- resting: y points down, so the plinth's top is -37.5 - 75/2 = -75 and a 4-high
           block's centre sits at -75 - 4/2 = -77; it casts a shadow, its grounding cue -->
      <object3D id="hero-block" primitive="box" material="m-solid" x="230" y="-77" z="210" width="40" height="4" depth="28" rotationY="12"/>
      <object3D id="ball" primitive="sphere" material="m-solid" x="150" y="-95" z="200" radius="20">
        <animate property="y"><key time="0" value="-95"/><key time="2" value="-180" interpolation="ease-out"/><key time="4" value="-95" interpolation="ease-in"/></animate>
      </object3D>
      <particles3D id="dust" x="-50" y="-125" z="160" emitterShape="box" emitterWidth="400" emitterHeight="140" emitterDepth="260" rate="0" lifetime="60" speed="1.2" speedVariance="1" spread="360" shape="billboard" size="1.2" color="#E8B65C" material="m-dust" maxParticles="200" seed="7" castShadow="false" receiveShadow="false">
        <burst time="0" count="120"/>
      </particles3D>
    </symbol>
    <!-- a shader on a full-frame rect: the effect computes every pixel, so the
         rect's own fill never shows. The effect lives in <effects>, after <lights> -->
    <symbol id="fx-surface" width="1920" height="1080" duration="4">
      <shape id="surface" shape="rect" x="0" y="0" width="1920" height="1080" fill="#000000" effects="fx-ring-surface"/>
    </symbol>
    <symbol id="fx-stage" width="1920" height="1080">
      <!-- word-by-word title: presetStart is on the parent timeline -->
      <layer id="l-title" asset="t-title" x="360" y="80">
        <textAnimator preset="word-by-word" presetStart="0.2" presetDuration="1.0" presetEase="cubic-out"/>
      </layer>
      <!-- the digits in the text count up to their value -->
      <layer id="l-count" asset="t-count" x="510" y="820">
        <textAnimator preset="counter" presetStart="1" presetDuration="3"/>
      </layer>
      <!-- the layout engine places explicit children: three cards in a row -->
      <group id="cards" layout="row" gap="60" justify="center" alignItems="end" x="0" y="300" width="1920" height="420">
        <shape id="card-1" shape="rounded-rect" width="300" height="400" radius="18" fill="var(--muted)" opacity="0">
          <animate property="opacity"><key time="0.5" value="0"/><key time="1.0" value="1" interpolation="ease-out"/></animate>
        </shape>
        <shape id="card-2" shape="rounded-rect" width="300" height="400" radius="18" fill="var(--muted)"/>
        <shape id="card-3" shape="rounded-rect" width="300" height="400" radius="18" fill="var(--accent)"/>
      </group>
      <!-- a grid of explicit cells, 30 columns -->
      <group id="grid" layout="grid" gridColumns="30" gap="8" x="200" y="100" width="1520" height="120">
        <shape id="cell-1" shape="rect" width="40" height="30" fill="var(--accent)"/>
        <shape id="cell-2" shape="rect" width="40" height="30" fill="var(--muted)"/>
      </group>
      <!-- a path needs width/height (its box); an open stroke needs a transparent fill -->
      <shape id="arrow" shape="path" path="M 960 900 L 1470 820" width="510" height="80" stroke="var(--accent)" strokeWidth="6" markerEnd="arrow" fill="#00000000" trimEnd="0">
        <animate property="trimEnd"><key time="2" value="0"/><key time="2.8" value="1" interpolation="ease-out"/></animate>
      </shape>
      <layer id="l-bars" asset="c-bars" x="510" y="500"/>
      <group id="nodes">
        <shape id="n1" shape="ellipse" x="1300" y="300" width="80" height="80" fill="var(--accent)"/>
        <shape id="n2" shape="ellipse" x="1600" y="600" width="80" height="80" fill="var(--accent)"/>
        <connector id="n1-n2" from="n1" to="n2" stroke="var(--ink)" markerEnd="arrow"/>
      </group>
    </symbol>
  </symbols>
  <composition>
    <instance id="use-room" symbol="fx-room" start="0" end="4"/>
    <instance id="use-stage" symbol="fx-stage" start="4" end="12"/>
    <instance id="use-surface" symbol="fx-surface" start="12" end="16"/>
  </composition>
  <lights>
    <light id="sun" type="directional" color="#FFF1D0" intensity="3" yaw="214" pitch="-25" castShadow="true" shadowSoftness="2"/>
    <light id="fill" type="ambient" color="#6F7C8C" intensity="1.0"/>
  </lights>
  <effects>
    <effect id="fx-ring-surface" type="shader" src="shaders/ring-surface.glsl">
      <param name="amplitude" value="0.035"/>
    </effect>
  </effects>
</scene>
"""

#: Shader sources the examples reference, written beside them by `prepare()`.
#: The engine runs a Shadertoy `mainImage` program in its effect pass:
#: `iTime`, `iResolution`, `iChannel0` (the node's own pixels), and each
#: `uniform float` with a `// = default` is overridden by a `<param>` child.
EXAMPLE_SHADERS = {
    "shaders/ring-surface.glsl": """\
// A liquid surface computed per pixel: an analytic height field (the ring wave
// a drop leaves), its normal, Fresnel reflection of one softbox and refraction
// of a lit floor. This is the route for water the ocean solver cannot make --
// a crown, a jet, a pinch-off: the shape is a function of position and time
// and the camera is a ray, so anything you can write as h(x, z, t) renders.
uniform float amplitude; // = 0.035
uniform float speed;     // = 0.45

float height(vec2 p, float t) {
    float r = length(p);
    float packet = exp(-pow((r - (0.05 + speed * t)) / 0.16, 2.0));
    return amplitude * sin(26.0 * r - 6.0 * t) * packet * exp(-0.4 * t);
}

vec3 sky(vec3 d) {
    vec3 c = mix(vec3(0.03, 0.05, 0.07), vec3(0.16, 0.23, 0.27), clamp(d.y * 0.5 + 0.5, 0.0, 1.0));
    float box = exp(-pow((d.x + 0.35) / 0.12, 2.0) - pow((d.y - 0.45) / 0.25, 2.0));
    return c + vec3(3.0) * box;  // a clear surface reads only by what it reflects
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    float t = mod(iTime, 6.0);
    vec2 uv = (fragCoord - 0.5 * iResolution.xy) / iResolution.y;
    vec3 ro = vec3(0.0, 1.2, -2.2);
    vec3 rd = normalize(vec3(uv.x, uv.y - 0.35, 1.4));
    if (rd.y >= 0.0) { fragColor = vec4(pow(sky(rd), vec3(1.0 / 2.2)), 1.0); return; }
    float s = -ro.y / rd.y;  // the mean plane, then two Newton steps onto h
    for (int i = 0; i < 2; i++) {
        vec3 q = ro + rd * s;
        s -= (q.y - height(q.xz, t)) / rd.y;
    }
    vec3 p = ro + rd * s;
    vec2 e = vec2(0.002, 0.0);
    vec3 n = normalize(vec3(height(p.xz - e.xy, t) - height(p.xz + e.xy, t), 2.0 * e.x,
                            height(p.xz - e.yx, t) - height(p.xz + e.yx, t)));
    float fres = 0.02 + 0.98 * pow(1.0 - max(dot(-rd, n), 0.0), 5.0);
    vec3 tr = refract(rd, n, 1.0 / 1.333);
    vec2 below = p.xz + tr.xz / max(-tr.y, 0.2) * 0.4;  // a floor 0.4 under the surface
    vec3 floorCol = vec3(0.05, 0.12, 0.14) * (0.6 + 0.4 * smoothstep(0.9, 0.0, length(below)));
    vec3 col = mix(floorCol, sky(reflect(rd, n)), fres);
    col = col / (col + 1.0);
    fragColor = vec4(pow(col, vec3(1.0 / 2.2)), 1.0);
}
""",
}


SR_WRAPPER = '''#!/usr/bin/env python3
"""sr -- ask the scene-render schema, and check a document the way the pipeline does.

    python3 sr.py elements                 every element the schema knows
    python3 sr.py describe <element>       attributes, types, enums, children
    python3 sr.py enum <element> <attr>    the values one attribute allows
    python3 sr.py check <file.scene.xml>   SDK read + check, then the engine's validation
    python3 sr.py frame <file.scene.xml> [--symbol ID] [--at SECONDS]
                                           render a proof frame at each shot's midpoint
                                           (shots.json) the way `yt build` includes the
                                           symbol; writes frames/*.png -- Read them.
                                           Stills only: the gate's own encode reports
                                           what the camera never frames, once a round
"""
import json, sys
sys.path.insert(0, {sr_root!r})
sys.path.insert(0, {automation_root!r})
from sr_core import scene as S
from ytstudio import env as _env
_env.load({automation_root!r} + "/.env")   # the render hosts live there; `yt` loads it too


def frame(argv):
    from pathlib import Path
    from ytstudio.vfx import Workspace
    path = Path(argv[0]).resolve()
    symbol = at = None
    i = 1
    while i < len(argv):
        if argv[i] == "--symbol" and i + 1 < len(argv):
            symbol = argv[i + 1]; i += 2
        elif argv[i] == "--at" and i + 1 < len(argv):
            at = float(argv[i + 1]); i += 2
        else:
            i += 1
    ws = Workspace.of(path)
    # Stills only: the draft encode that reports what the camera never frames
    # runs once a round at the gate, on the encode host, not on every look.
    results = ws.frames(path.name, symbol=symbol, times=[at] if at is not None else None,
                        encode=False)
    worst = 0
    for png, findings in results:
        print(f"frame: {{png if png else '(not rendered)'}}")
        for f in findings:
            print(f"  {{f}}")
            worst = max(worst, 2 if f.startswith("error") else 1 if f.startswith("warning") else 0)
    if not results:
        print("no symbol found in the document")
        return 1
    print("\\nnow Read every frame above and judge it against the brief: the hero "
          "legible and framed, the set lit, one continuous camera; a flat, black "
          "or empty frame is not a finished shot. The gate encodes each shot once "
          "a round and reports every object the camera never frames; keep the "
          "hero in view in every frame here and that report stays clean.")
    return 1 if worst == 2 else 0


def describe(el):
    d = S.describe(el).to_dict()
    print(f"<{{el}}>  {{str(d.get('doc', '')).strip()[:400]}}")
    print("  min version:", d.get("min_version"))
    for a in d.get("attributes", []):
        t = a.get("type") or {{}}
        kind = t.get("enum") or t.get("name") or t.get("base") or "?"
        if isinstance(kind, list):
            kind = "|".join(kind[:16]) + (" …" if len(kind) > 16 else "")
        doc = str(a.get("doc", "")).strip().replace("\\n", " ")[:160]
        print(f"  @{{a['xml_name']}}: {{kind}}" + (f"  -- {{doc}}" if doc else ""))
    kids = [c.get("xml_name") or c.get("class_name") for c in (d.get("children") or []) if isinstance(c, dict)]
    if kids:
        print("  children:", ", ".join(str(k) for k in kids))


def check(path):
    from ytstudio import render
    data = open(path, "rb").read()
    try:
        doc = S.read(data)
    except Exception as exc:
        print(f"error: the document does not parse: {{exc}}"); return 1
    findings = [str(f) for f in doc.check()]
    for f in findings:
        print(f)
    engine = render.engine_validate(data)
    if engine is None:
        print("note: no engine reachable; only the SDK's checks ran")
    else:
        for f in engine:
            print(f)
    errors = [f for f in findings + (engine or []) if f.startswith("error")]
    print(f"\\n{{len(errors)}} error(s) · version {{doc.version}}")
    return 1 if errors else 0


cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if cmd == "elements":
    print("\\n".join(sorted(S.element_names())))
elif cmd == "describe" and len(sys.argv) > 2:
    describe(sys.argv[2])
elif cmd == "enum" and len(sys.argv) > 3:
    d = S.describe(sys.argv[2]).to_dict()
    for a in d.get("attributes", []):
        if a.get("xml_name") == sys.argv[3]:
            print(json.dumps((a.get("type") or {{}}).get("enum") or "(free)"))
elif cmd == "check" and len(sys.argv) > 2:
    sys.exit(check(sys.argv[2]))
elif cmd == "frame" and len(sys.argv) > 2:
    sys.exit(frame(sys.argv[2:]))
else:
    print(__doc__)
'''

#: Elements worth a line in the cheat sheet: the document's skeleton, the 2D
#: stage, the 3D stage, the simulations. The author can `describe` the rest.
SHEET_ELEMENTS = ("scene", "project", "styles", "token", "textStyle", "assets",
                  "text", "image", "video", "chart", "series", "formula",
                  "mesh", "lottie", "symbols", "symbol", "composition", "group",
                  "sequence", "layer", "shape", "connector", "repeat", "instance",
                  "include", "animate", "key", "expression", "textAnimator",
                  "mask", "transition", "object3D", "camera", "lights", "light",
                  "materials", "material", "particleEmitter", "particles3D",
                  "physics", "fluid", "viewport3D", "effect", "markers",
                  "marker", "output")


# --------------------------------------------------------------------------- #
# the frame gate
# --------------------------------------------------------------------------- #
# A document that validates can still render nothing: a camera that frames no
# object, a light the include does not carry, a solver that refuses its grid.
# So every symbol is rendered the way `yt build` will include it, at each
# shot's midpoint, and the frames are judged -- by `framecheck` for what needs
# no eyes (flat, black, clipped, dark, hero or set out of the camera's view),
# by the author, who looks at them, and by the review, which scores them.


def hoist_lights(doc_text: str) -> str:
    """The <lights> block of a document, or "" -- an include does not carry it."""
    m = re.search(r"<lights>.*?</lights>", doc_text, re.S)
    return m.group(0) if m else ""


def symbol_facts(doc_text: str) -> list[dict]:
    """Every <symbol id> with its duration and background, from the text."""
    out = []
    proj = re.search(r"<project\b[^>]*>", doc_text)
    proj_dur = float(re.search(r'duration="([\d.]+)"', proj.group(0)).group(1)) \
        if proj and re.search(r'duration="([\d.]+)"', proj.group(0)) else 0.0
    proj_bg = re.search(r'background="([^"]+)"', proj.group(0)) if proj else None
    for m in re.finditer(r"<symbol\b[^>]*>", doc_text):
        tag = m.group(0)
        sid = re.search(r'\bid="([^"]+)"', tag)
        if not sid:
            continue
        dur = re.search(r'\bduration="([\d.]+)"', tag)
        bg = re.search(r'\bbackground="([^"]+)"', tag)
        out.append({"id": sid.group(1),
                    "duration": float(dur.group(1)) if dur else proj_dur,
                    "background": bg.group(1) if bg else (proj_bg.group(1) if proj_bg else "#000000")})
    return out


def unbounded_layer_findings(doc_text: str) -> list[str]:
    """Layers with no start, end or duration are resident for the whole world, which a long
    continuous world cannot afford. A warning naming them when the document outlasts its own
    median bounded layer (or has none); the count is reported and the author decides, since
    the repo holds no measured per-layer cost to derive a limit from."""
    try:
        root = _ET.fromstring(doc_text)
    except _ET.ParseError:
        return []
    project = root.find("project")
    try:
        seconds = float(project.get("duration")) if project is not None else 0.0
    except (TypeError, ValueError):
        seconds = 0.0
    if seconds <= 0:
        return []
    free, spans = [], []
    for layer in root.iter("layer"):
        a, b, d = layer.get("start"), layer.get("end"), layer.get("duration")
        if a is None and b is None and d is None:
            free.append(layer.get("id") or layer.get("asset") or "?")
            continue
        try:
            spans.append(float(d) if d is not None else
                         float(b if b is not None else seconds) - float(a or 0))
        except ValueError:
            continue
    if not free:
        return []
    spans.sort()
    if spans and seconds <= spans[len(spans) // 2]:
        return []
    return [f"warning: {len(free)} layer(s) have no start/end and stay loaded for all "
            f"{seconds:g}s of the world ({', '.join(free[:12])}{' ...' if len(free) > 12 else ''}); "
            "a long continuous world can run a host out of memory -- give each layer the "
            "start/end of the span it is seen in"]


def timing_findings(doc_text: str, required: dict[str, float]) -> list[str]:
    """Does each symbol span the shots it draws?

    `required` is seconds per symbol id (the end of its last shot). A symbol
    authored shorter than that is an error: `yt build` will hold its last
    frame while the narration goes on, which is the picture and the voice
    out of sync. Animation that stops well before the end is a warning,
    since a held final frame can be the plan.
    """
    found: list[str] = []
    try:
        root = _ET.fromstring(doc_text)
    except _ET.ParseError:
        return found
    project = root.find("project")
    project_seconds = (float(project.get("duration"))
                       if project is not None and project.get("duration") else None)
    for symbol in root.iter("symbol"):
        sid = symbol.get("id")
        need = required.get(sid) if sid else None
        if need is None:
            continue
        seconds = float(symbol.get("duration")) if symbol.get("duration") else project_seconds
        last_key = max((float(k.get("time")) for k in symbol.iter("key") if k.get("time")),
                       default=None)
        if seconds is not None and seconds + TIMING_SLACK < need:
            found.append(
                f"error: symbol {sid} is authored for {seconds:g}s but its shots run "
                f"{need:g}s: set duration=\"{need:g}\" and key the phases to the "
                f"narration's words, or the picture ends {need - seconds:.1f}s before "
                "the voice does")
        elif last_key is not None and last_key + 1.0 < need:
            found.append(
                f"warning: symbol {sid}: the last keyframe is at {last_key:g}s of "
                f"{need:g}s, so the picture holds still for the final "
                f"{need - last_key:.1f}s; fine only if the plan says so")
    return found


def proof_document(doc_name: str, doc_text: str, symbol: str, frame: tuple[int, int],
                   duration: float, background: str) -> str:
    """The document `yt build` effectively makes for one shot: one include.

    Its lights are hoisted from the authored document because an include does
    not carry them; `yt build` does the same for the real scene.
    """
    lights = hoist_lights(doc_text)
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<scene version="1.2">\n'
            f'  <project width="{frame[0]}" height="{frame[1]}" fps="30" '
            f'duration="{max(duration, 0.1):g}" background="{background}"/>\n'
            f'  <output path="proof.mp4" codec="h264"/>\n'
            f'  <composition>\n    <include id="shot" src="{doc_name}" symbol="{symbol}"/>\n'
            f'  </composition>\n' + (f"  {lights}\n" if lights else "") + "</scene>\n")


#: Attributes that name a local file the engine will open.
FILE_ATTRS = ("src", "href", "environment", "ies", "proxy", "materialX", "baseColorMap",
              "normalMap", "metallicRoughnessMap", "occlusionMap", "emissiveMap",
              "displacementMap")
MAX_SHIP = 64 * 2**20


def referenced_files(doc_text: str, base: Path, root: Path | None = None) -> dict[str, bytes]:
    """Local files the document references (src=, environment=, the material
    maps...), keyed as the engine will see them from the project root
    (`root`; by default the parent of `base`, the document's directory). A
    glTF/OBJ model travels with its whole directory, since its buffers and
    textures are named inside it, not in the document."""
    out: dict[str, bytes] = {}
    top = (root or base.parent).resolve()

    def ship(path: Path) -> None:
        try:
            rel = path.relative_to(top)
        except ValueError:
            return
        if path.is_file() and path.stat().st_size < MAX_SHIP:
            out[rel.as_posix()] = path.read_bytes()

    pattern = r'\b(?:' + "|".join(FILE_ATTRS) + r')="([^"]+)"'
    for m in re.finditer(pattern, doc_text):
        ref = m.group(1)
        if "://" in ref or ref.startswith("#") or ref.endswith(".scene.xml"):
            continue
        path = (base / ref).resolve()
        ship(path)
        if path.is_file() and path.suffix.lower() in (".gltf", ".obj", ".usd", ".usda", ".mtl"):
            for sibling in sorted(path.parent.rglob("*")):
                ship(sibling)
    return out


def _envelope(text: str) -> dict | None:
    """The engine's JSON envelope on a command's stdout, or None."""
    try:
        start = text.index("{")
        data = json.loads(text[start:])
    except (ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


#: The seam of the geometry gate: `EVAL_RUNNER(doc_path, files=...)` gives a
#: `runner(times)` that returns the engine's eval frame per time (one job on
#: a host), or None when no engine is reachable. A test puts a fake here.
EVAL_RUNNER = geometry.engine_runner


def encode_findings(files: dict[str, bytes], doc_path: str, *, start: float, end: float,
                    quality: str = "draft", host: str = "", timeout: float = 600.0
                    ) -> list[str] | None:
    """The engine's findings for one window of a document, from a short draft
    `encode` of a scratch bundle: the camera analysis (`object3D ... is never
    inside the camera's view`) is computed only by an encode, never by a
    still. Lines are `severity: message`; None when no engine is reachable.

    The bundle is shipped and run through render's public job hook
    (`render.run_job`; a scratch directory is a project as far as it is
    concerned), so the hosts, the sync and the binary lookup stay in one place.

    An encode of a whole world wants the memory an encode gets: it goes to
    the encode host, one kit render per host at a time (`sdkkit.host_lock`),
    and its lines are cached by the still fingerprint of the bundle and the
    window, so a round that changed nothing in a world pays no encode. An
    encode the host killed for memory (exit 137) is the author's `error:`
    -- the world is too heavy to encode anywhere -- and is never cached;
    nor is any other failure.
    """
    import subprocess
    import tempfile
    host = host or render.remote_host(None, "encode")
    binary = render.engine()
    if binary is None and not host:
        return None
    cached = _encode_cache(files, doc_path, start=start, end=end, quality=quality, host=host)
    if cached is not None and cached.is_file():
        hit = read_json(cached, warn=False)
        if isinstance(hit, dict) and isinstance(hit.get("lines"), list):
            return [str(l) for l in hit["lines"]]
    with tempfile.TemporaryDirectory(prefix="proof-") as tmp:
        root = Path(tmp)
        for rel, data in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(data)
        cmd = ["scene-render", "encode", doc_path, "-o", ".proof.mp4", "--no-upload",
               "--json", "--report", ".estimate-proof.json", "--start", f"{start:g}",
               "--end", f"{end:g}", "--quality", quality]
        try:
            with sdkkit.host_lock(host):
                if host:
                    proc = render.run_job(cmd, root, host, job="proof", verbose=False)
                else:
                    proc = subprocess.run([str(binary)] + cmd[1:], cwd=root,
                                          capture_output=True, text=True, timeout=timeout)
        except (subprocess.SubprocessError, OSError) as exc:
            return [f"warning: the proof encode of {start:g}-{end:g}s could not run: "
                    f"{str(exc)[:120]}"]
        report = _envelope(proc.stdout or "") or read_json(root / ".estimate-proof.json", warn=False)
    lines = framecheck.report_findings(report)
    stderr = (proc.stderr or "").strip()
    if proc.returncode != 0 and not any(l.startswith("error") for l in lines):
        tail = stderr.splitlines()[-2:]
        # What render.run_job says of a job the host lost ("engine killed") is
        # the host's failure, not the kernel's, so it is not read for memory.
        engine_said = "\n".join(l for l in stderr.splitlines() if not l.startswith("remote job"))
        if proc.returncode == 137 or OUT_OF_MEMORY.search(engine_said):
            lines.insert(0, f"error: the proof encode of {start:g}-{end:g}s was killed for memory "
                            f"on {host or 'this machine'} (exit {proc.returncode}): no host can "
                            "encode this world as written. Time-bound every layer and group "
                            "to its shots, drop what has faded to opacity 0, and lower the "
                            "samples, the resolution of clay objects or the medium density")
        else:
            lines.insert(0, f"warning: the proof encode of {start:g}-{end:g}s exited "
                            f"{proc.returncode}" + (": " + " | ".join(t.strip()[:120] for t in tail)
                                                   if tail else ""))
    if proc.returncode == 0 and cached is not None:
        # Written whole, as render stores a still: a reader in another
        # process must never see half a file and encode the window again.
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            tmp = cached.with_name(f".{cached.stem}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"host": host or "this machine", "start": start,
                                       "end": end, "lines": lines}, indent=2) + "\n",
                           encoding="utf-8")
            os.replace(tmp, cached)
        except OSError:
            pass
    return lines


#: What the engine's stderr says when the host killed its encode for memory:
#: the kernel's `Killed` keeps its case, so a sentence about a killed job is
#: not read as one.
OUT_OF_MEMORY = re.compile(r"(?i:out of memory)|\bKilled\b|\bOOM\b")

#: How long the gate waits before gating a round again when the host, not the
#: author, lost its frames: a saturated still host clears in minutes, and a
#: paid revision for it is money for nothing (throughput.md, run 2).
INFRA_WAIT = 120.0

#: The line engine_render_still writes for a render that died saying nothing.
NO_FINDING = "error: the engine could not render the frame"


#: What a still stopped at the deadline says: the ssh side's timeout, the
#: host's `timeout` wrapper (exit 124) or sdkkit.frame's own line.
STILL_TIMEOUT = re.compile(r"timed out after|ran past|\(exit 124\)")

#: What a still the host killed for memory says: exit 137, the kernel's word.
STILL_KILLED = re.compile(r"\(exit 137\)|\bKilled\b")


def cost_failure(message: str) -> bool:
    """Whether a still that rendered nothing was stopped for its cost: it ran
    past the still deadline, or the host killed it for memory. The other host
    gets one try; a world both hosts lose this way is the author's to
    lighten, as sdkkit.frame rules, never the host's failure."""
    if any(m in message for m in sdkkit.UNREACHABLE):
        return False
    lines = message.splitlines()
    said = "\n".join(lines[:1] + [l for l in lines[1:] if l.startswith(NO_FINDING)])
    return bool(STILL_TIMEOUT.search(said) or STILL_KILLED.search(said))


def infra_failure(message: str) -> bool:
    """Whether a still that rendered nothing failed for the host's reasons
    and not the document's: the host did not answer (sdkkit.UNREACHABLE),
    no engine was reachable, or the engine died naming no finding. A still
    stopped for its cost (`cost_failure`) is not the host's; a document the
    engine refused with a diagnostic, or one blocked on a file it names, is
    the author's."""
    head, _, rest = message.partition("\n")
    if any(m in message for m in sdkkit.UNREACHABLE):
        return True
    if cost_failure(message):
        return False
    if head.startswith(("render host unreachable", "no engine reachable")):
        return True
    if head.startswith("error:"):
        return False
    return not any(l.strip() and not l.startswith(NO_FINDING) for l in rest.splitlines())


def fallback_host(host: str) -> str:
    """The other host a still is retried on once `host` lost it: where an
    encode would go when that is another machine, else the first configured
    host that is not `host`; "" when there is none."""
    other = render.remote_host(None, "encode")
    if other and other != host:
        return other
    return next((h for h in render.host_names() if h and h != host), "")


def _encode_cache(files: dict[str, bytes], doc_path: str, *, start: float, end: float,
                  quality: str, host: str) -> Path | None:
    """Where one proof window's lines are cached: beside the still cache, under
    the still fingerprint of the bundle, the engine, the window and the
    quality. None when the engine on `host` is not known, since then the
    lines cannot be keyed to the engine that produced them."""
    cache = render.still_cache_dir()
    ident = render.engine_identity(host, cache)
    if not ident or doc_path not in files:
        return None
    shipped = {rel: data for rel, data in files.items() if rel != doc_path}
    key = render.still_fingerprint(files[doc_path], shipped, doc_path, start, quality, ident,
                                   options=f"encode:{start:g}-{end:g}")
    return cache.parent / "encodes" / f"{key}.json"


def frame_times(duration: float) -> list[float]:
    """Start, middle and end of a symbol, inside its life."""
    if duration <= 1.0:
        return [round(duration / 2, 2)]
    return [round(max(0.2, duration * 0.1), 2), round(duration / 2, 2),
            round(min(duration - 0.2, duration * 0.9), 2)]


def shot_times(shots: list[dict], duration: float, *, apart: float = 1.0) -> list[float]:
    """Where a symbol's proof frames sit: the midpoint of every shot it holds
    (`at` is the shot's offset inside the symbol), plus the symbol's own
    start, middle and end unless a midpoint already sits within `apart`."""
    mids = [round(float(s["at"]) + float(s["seconds"]) / 2, 2) for s in shots or []]
    mids = sorted({t for t in mids if 0 < t < duration}) if duration > 0 else sorted(set(mids))
    for t in frame_times(duration):
        if all(abs(t - m) >= apart for m in mids):
            mids.append(t)
    return sorted(mids)


def has_errors(record: dict) -> bool:
    """Whether a section record carries a gate error (or a failed authoring)."""
    return any(str(f).startswith("error") for f in record.get("findings") or [])


def passes_gate(record: dict) -> bool:
    """A section record that passed its gate and was reviewed."""
    return (record.get("score") is not None
            and not [f for f in record.get("findings") or [] if str(f).startswith("error")])


def verdict_of(review: dict | None) -> str | None:
    """What a revision's reviewer said of it against the round it was shown:
    better, same or worse; None for a first round or a review from before
    the verdict was asked."""
    previous = (review or {}).get("previous")
    return previous.get("verdict") if isinstance(previous, dict) else None


def best_round(rounds: list[dict]) -> dict | None:
    """The round a section stands on. The first reviewed round is the
    incumbent; a later reviewed round replaces it only when its reviewer,
    shown the incumbent, judged it `better` (a tie keeps the incumbent). A
    review from before the verdict was asked is judged on its score. A round
    with errors or cut off has no say."""
    best = None
    for r in rounds:
        if r.get("score") is None or r.get("errors") or r.get("timed_out"):
            continue
        if best is None:
            best = r
            continue
        verdict = verdict_of(r.get("review"))
        if verdict == "better" or (verdict is None and r["score"] > best["score"]):
            best = r
    return best


def keeps_previous(before: dict, attempt: dict) -> bool:
    """Whether an earlier version's section beats a new attempt at it: the new
    one failed its gate or went unreviewed while the earlier passed, or both
    passed and the earlier scored higher. A tie keeps the new attempt."""
    if not passes_gate(before):
        return False
    if not passes_gate(attempt):
        return True
    return float(before["score"]) > float(attempt["score"])


def world_span(shots: list[dict]) -> float:
    """How long a world runs: to the end of its last shot, the gaps between
    takes included. The brief states it and the timing gate requires it, from
    this one function."""
    return round(max((float(s.get("at", 0)) + float(s.get("seconds") or 0)
                      for s in shots), default=0.0), 3)


def world_symbols(section: dict, effects: list[dict],
                  cuts: list[str] | None = None) -> list[dict]:
    """The symbol(s) one section is authored as: by default ONE world holding
    every shot of the section at its offset from the section's start, with
    the section's effects as its phases. A cut (a shot label where a new
    world has to start) splits the section; the first shot is never a cut.

    Each world: id, start (film time of its first shot), offset (seconds into
    the section), seconds, shots (label, at within the world, seconds, and
    the screenplay's words) and phases (the effects whose shots it covers; an
    effect with no shots belongs to all).
    """
    slug = ident(slugify(section.get("heading", "section"), 40))
    shots = list(section.get("shots") or [])
    if not shots:
        return [{"id": f"{WORLD_PREFIX}{slug}", "start": 0, "offset": 0,
                 "seconds": float(section.get("seconds") or 0),
                 "shots": [], "phases": list(effects)}]
    origin = float(shots[0].get("at", 0))
    labels = [str(s["label"]) for s in shots]
    cut_at = {c for c in (cuts or []) if c in labels[1:]}
    runs: list[list[dict]] = [[]]
    for s in shots:
        if str(s["label"]) in cut_at and runs[-1]:
            runs.append([])
        runs[-1].append(s)
    out = []
    for n, run in enumerate(runs, 1):
        start = float(run[0].get("at", 0))
        covered = {str(s["label"]) for s in run}
        world_shots = [dict(s, label=str(s["label"]),
                            at=round(float(s.get("at", 0)) - start, 3),
                            seconds=float(s.get("seconds") or 0)) for s in run]
        phases = [e for e in effects
                  if not e.get("shots") or covered & set(map(str, e.get("shots", [])))]
        # The world spans to the end of its last shot, gaps between takes
        # included: the same number the timing gate requires (world_span), so
        # an author who gives the symbol the brief's duration passes it.
        out.append({"id": f"{WORLD_PREFIX}{slug}" + (f"-{n}" if n > 1 else ""),
                    "start": round(start, 3), "offset": round(start - origin, 3),
                    "seconds": world_span(world_shots),
                    "shots": world_shots, "phases": phases})
    return out


#: What the engine imports as a `<mesh>` (`mesh/@format`).
MESH_SUFFIXES = (".gltf", ".glb", ".obj", ".usd", ".usda", ".usdc", ".usdz", ".fbx", ".ply",
                 ".splat")

#: How each kind of research asset enters a program; audio and documents are
#: not the picture's.
ASSET_USE = {
    "image": "an `<image>` asset (`src`), or a material map (`baseColorMap`, `normalMap`, "
             "...) on a surface",
    "video": "a `<video>` asset (`src`)",
    "3d-model": "a `<mesh>` asset (`src`) placed with `object3D primitive=\"mesh\"`",
    "structure": "atomic coordinates (mmCIF text): the program reads the file and builds the "
                 "geometry with the SDK; it is data, not a mesh",
}


def research_asset_lines(assets: list[dict], root: Path, here: Path) -> list[str]:
    """The research's downloaded, licensed files an author may use, by kind,
    with paths from the author's directory and how each kind enters a program."""
    by_kind: dict[str, list[tuple[str, dict]]] = {}
    for a in assets or []:
        f = a.get("file") or a.get("path")
        kind = str(a.get("kind") or "image")
        if not f or kind not in ASSET_USE:
            continue
        by_kind.setdefault(kind, []).append((os.path.relpath(Path(root) / f, here), a))
    if not by_kind:
        return []
    lines = ["## Licensed files from the research (paths relative to this directory)", ""]
    for kind, rows in by_kind.items():
        lines.append(f"### {kind}: {ASSET_USE[kind]}")
        for rel, a in rows:
            licence = a.get("licence_verified") or a.get("licence") or ""
            credit = a.get("credit_line") or a.get("credit") or ""
            line = (f"- `{rel}` — {a.get('title', '')} ({licence}"
                    + (f"; credit: {credit}" if credit else "") + ")")
            if a.get("paper"):
                line += f" — from {a['paper']}"
            if kind == "3d-model" and Path(rel).suffix.lower() not in MESH_SUFFIXES:
                line += (f" — not loadable: the engine imports "
                         f"{', '.join(s.lstrip('.') for s in MESH_SUFFIXES)}")
            lines.append(line)
        lines.append("")
    return lines


def props_items(root: Path) -> list[dict]:
    """The CC0 props `yt props` fetched into the project (props.json: models,
    HDRIs, textures), or [] when there is no such file."""
    data = read_json(Path(root) / "props.json", warn=False)
    if not isinstance(data, dict):
        return []
    return [it for it in data.get("items", []) if isinstance(it, dict) and it.get("file")]


TEXTURE_SUFFIXES = (".jpg", ".jpeg", ".png", ".exr", ".tif", ".tiff", ".webp")

#: The author's opt-out: <meta name="props-declined" value="<prop id or kind>: <reason>">.
DECLINE_META = "props-declined"
MAP_ATTRS = ("baseColorMap", "normalMap", "metallicRoughnessMap", "occlusionMap")


def timed_out_usage(usage) -> dict:
    """What calls cut off by their timeout spent, for a record: the tokens
    and cost are already inside the totals; this marks them as such."""
    return {"calls": usage.timed_out_calls, "tokens": dict(usage.timed_out_tokens),
            "cost_usd": round(usage.timed_out_cost_usd, 4),
            "cost_estimated": usage.timed_out_cost_estimated}


def props_declined(doc_text: str) -> dict[str, str]:
    """The props the document declines with a reason, from its
    `<meta name="props-declined" value="<prop id or kind>: <reason>">` entries;
    an entry without a reason does not count."""
    out: dict[str, str] = {}
    for tag in re.findall(r"<meta\b[^>]*>", doc_text):
        name = re.search(r'\bname="([^"]*)"', tag)
        value = re.search(r'\bvalue="([^"]*)"', tag)
        if not name or not value or name.group(1) != DECLINE_META:
            continue
        key, sep, why = value.group(1).partition(":")
        if sep and key.strip() and why.strip():
            out[key.strip()] = why.strip()
    return out


def props_findings(doc_text: str, props: list[dict], textures: list[str]
                   ) -> tuple[list[str], dict[str, str]]:
    """The props rule on one document: a listed HDRI with no light carrying
    `environment=`, or a listed model with no `<mesh>` asset, is an error
    naming the unused ids, unless the document declines the kind (or every
    one of its ids) with a reason; generator maps where texture maps are
    listed are a note. Returns (findings, declined)."""
    declined = props_declined(doc_text)
    findings: list[str] = []
    if not props:
        return findings, declined

    def rule(kind: str, used: bool, how: str) -> None:
        items = [p for p in props if p.get("kind") == kind]
        if not items or used or kind in declined:
            return
        left = [str(p.get("id")) for p in items if str(p.get("id")) not in declined]
        if left:
            findings.append(
                f"error: props: {len(left)} {kind}(s) listed in props.json ({', '.join(left[:8])}"
                + (", ..." if len(left) > 8 else "") + f") and the document uses none ({how}); "
                f"use one, or decline it with `<meta name=\"{DECLINE_META}\" value=\"{kind}: "
                "<why it does not fit the treatment>\"/>` (or one per id) in <metadata>")

    rule("hdri", bool(re.search(r'<light\b[^>]*\benvironment="[^"]+"', doc_text)),
         'no <light type="dome" environment="<hdr>"> in <lights>')
    rule("model", bool(re.search(r'<mesh\b[^>]*\bsrc="', doc_text)), "no <mesh> asset")
    if textures:
        maps = re.findall(r'\b(?:' + "|".join(MAP_ATTRS) + r')="([^"]+)"', doc_text)
        names = [Path(t).name for t in textures]
        if maps and all(m.startswith("#") for m in maps) and not any(n in doc_text for n in names):
            findings.append(
                f"note: props: the material maps are generator assets ({', '.join(sorted(set(maps))[:4])}) "
                f"while texture maps are listed ({', '.join(names[:4])}"
                + (", ..." if len(names) > 4 else "") + "): a large surface (floor, wall, any broad set piece) "
                "reads better with a real map")
    return findings, declined


def declined_of(authored: list[dict]) -> dict[str, str]:
    """Every prop declined across the authored documents, for vfx.json."""
    out: dict[str, str] = {}
    for doc in authored:
        out.update(doc.get("props_declined") or {})
    return out


def approved_look(root: Path) -> dict | None:
    """The operator-approved keyframe (`yt keyframe`): document, frame and
    elements, for the brief and the review; None when there is none, it is
    not approved, or its document changed since. Reads keyframe.json's shape
    directly when the keyframe module is not importable."""
    root = Path(root)
    try:
        from . import keyframe
        return keyframe.approved_keyframe(root)
    except ImportError:
        pass
    data = read_json(root / "keyframe.json", warn=False)
    if not isinstance(data, dict) or (data.get("approval") or {}).get("state") != "approved":
        return None
    return {"document": data.get("document") or "keyframe/keyframe.scene.xml",
            "frame": data.get("frame") or "keyframe/keyframe.png",
            "elements": data.get("elements") or [],
            "source": data.get("source") or "sheet", "section": data.get("section"),
            "program": data.get("program") or "", "sheet": data.get("sheet") or "",
            "record": data.get("record"), "frame_size": data.get("frame_size"),
            "look_program": data.get("look_program") or "",
            "approved_at": str((data.get("approval") or {}).get("at") or ""),
            "digest": str((data.get("approval") or {}).get("digest") or "")}


def look_section(root: Path) -> dict | None:
    """The section record of an approved look that came from the film itself
    (`yt keyframe` authored it), when its document is still on disk; `yt vfx`
    carries it forward instead of authoring that section again."""
    look = approved_look(root)
    if not look or look.get("source") != "section":
        return None
    record = look.get("record")
    if not isinstance(record, dict) or record.get("n") is None:
        return None
    if not (Path(root) / str(record.get("file") or "")).is_file():
        return None
    if any(str(f).startswith("error") for f in record.get("findings") or []):
        return None
    return dict(record, look=True, frame_size=look.get("frame_size"))


def prop_textures(root: Path, item: dict) -> list[Path]:
    """The texture maps shipped beside a model prop (its `textures/` directory,
    sorted), usable as material maps on any surface; [] for other kinds."""
    if item.get("kind") != "model":
        return []
    folder = (Path(root) / item["file"]).parent / "textures"
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in TEXTURE_SUFFIXES)


class Workspace:
    """<project>/vfx/vNN/: the author's files for one round of authoring, and
    the checks it runs. With no version the directory is vfx/ itself."""

    def __init__(self, root: Path, dirname: str = SCENE_DIR,
                 version: str | None = None) -> None:
        self.root = Path(root)
        self.base = self.root / dirname
        self.version = version
        self.dir = self.base / version if version else self.base
        self.dir.mkdir(parents=True, exist_ok=True)

    @property
    def rel(self) -> str:
        """The directory as the project sees it: `vfx` or `vfx/v02`."""
        return self.dir.relative_to(self.root).as_posix()

    @staticmethod
    def versions(base: Path) -> list[str]:
        """The authoring versions under vfx/, lowest first."""
        if not Path(base).is_dir():
            return []
        return sorted((p.name for p in Path(base).iterdir()
                       if p.is_dir() and VERSION_RE.match(p.name)),
                      key=lambda v: int(v[1:]))

    def new_version(self) -> str:
        """Open the next vNN/ under vfx/ and make it this workspace's directory;
        nothing authored before is touched."""
        have = self.versions(self.base)
        n = int(have[-1][1:]) + 1 if have else 1
        self.version = f"v{n:02d}"
        self.dir = self.base / self.version
        self.dir.mkdir(parents=True, exist_ok=True)
        return self.version

    @classmethod
    def of(cls, doc_path: Path) -> "Workspace":
        """The workspace a document lives in: vfx/vNN/<doc> or vfx/<doc>."""
        here = Path(doc_path).resolve().parent
        if VERSION_RE.match(here.name):
            return cls(here.parent.parent, here.parent.name, here.name)
        return cls(here.parent, here.name)

    #: Where every gated round of a section is kept: rounds/<stem>/rNN/.
    ROUNDS_DIR = "rounds"

    def _section_files(self, name: str) -> list[str]:
        stem = name[:-len(".scene.xml")]
        return [name, f"{stem}.py", f"{stem}.provenance.json"]

    #: A section's record as the stage writes it: rounds/<stem>/record.json,
    #: partial after each gate, whole when the section ends, so a run that is
    #: stopped, killed or crashes keeps every section that finished (AUTO-82).
    RECORD_NAME = "record.json"

    def record_path(self, name: str) -> Path:
        return self.dir / self.ROUNDS_DIR / name[:-len(".scene.xml")] / self.RECORD_NAME

    def write_record(self, name: str, record: dict) -> Path:
        """Write a section's record whole: a temporary file beside it, then
        one rename, so a kill mid-write leaves the last one, never half."""
        path = self.record_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return path

    def read_record(self, name: str) -> dict | None:
        return read_json(self.record_path(name), warn=False)

    def keep_round(self, name: str, round_no: int) -> Path | None:
        """Copy what a gated round judged -- program, document, provenance and
        proof frames -- under rounds/<stem>/rNN/, so it can be promoted later."""
        if not (self.dir / name).is_file():
            return None
        stem = name[:-len(".scene.xml")]
        kept = self.dir / self.ROUNDS_DIR / stem / f"r{round_no:02d}"
        if kept.exists():
            shutil.rmtree(kept)
        (kept / "frames").mkdir(parents=True)
        for rel in self._section_files(name):
            if (self.dir / rel).is_file():
                shutil.copy2(self.dir / rel, kept / rel)
        for png in (self.dir / "frames").glob(f"{stem}__*.png"):
            shutil.copy2(png, kept / "frames" / png.name)
        return kept

    def restore_round(self, name: str, round_no: int) -> bool:
        """Put a kept round back as the section's program, document,
        provenance and proof frames; False when it was never kept."""
        stem = name[:-len(".scene.xml")]
        kept = self.dir / self.ROUNDS_DIR / stem / f"r{round_no:02d}"
        if not (kept / name).is_file():
            return False
        for rel in self._section_files(name):
            if (kept / rel).is_file():
                shutil.copy2(kept / rel, self.dir / rel)
            else:
                (self.dir / rel).unlink(missing_ok=True)
        frames = self.dir / "frames"
        frames.mkdir(exist_ok=True)
        for png in frames.glob(f"{stem}__*.png"):
            png.unlink()
        for png in (kept / "frames").glob("*.png"):
            shutil.copy2(png, frames / png.name)
        return True

    def carry_section(self, record: dict, n: int) -> dict | None:
        """Copy a section authored in an earlier version into this one --
        program, document, provenance, brief, proof frames and kept rounds --
        and return its record pointing here; None when its document is gone."""
        src_doc = self.root / str(record.get("file") or "")
        if not src_doc.is_file():
            return None
        src, name = src_doc.parent, src_doc.name
        stem = name[:-len(".scene.xml")]
        for rel in self._section_files(name) + [f"brief-{n:02d}.md"]:
            if (src / rel).is_file():
                shutil.copy2(src / rel, self.dir / rel)
        (self.dir / "frames").mkdir(exist_ok=True)
        for png in (src / "frames").glob(f"{stem}__*.png"):
            shutil.copy2(png, self.dir / "frames" / png.name)
        if (src / self.ROUNDS_DIR / stem).is_dir():
            shutil.copytree(src / self.ROUNDS_DIR / stem, self.dir / self.ROUNDS_DIR / stem,
                            dirs_exist_ok=True)
        program = self.dir / f"{stem}.py"
        return dict(record, file=f"{self.rel}/{name}", version=self.version,
                    program=f"{self.rel}/{program.name}" if program.is_file() else "",
                    carried_from=record.get("version"),
                    carried_cost_usd=record.get("cost_usd", 0.0), cost_usd=0.0)

    def keep_previous(self, before: dict, attempt: dict, n: int) -> dict | None:
        """Put an earlier version's section back in place of a worse new
        attempt: the attempt's kept rounds move to rounds/<stem>.attempt/ and
        its proof frames are cleared, then the earlier files are carried in."""
        name = Path(str(attempt.get("file") or "")).name
        if not name.endswith(".scene.xml"):
            return None
        stem = name[:-len(".scene.xml")]
        rounds = self.dir / self.ROUNDS_DIR / stem
        if rounds.is_dir():
            aside = self.dir / self.ROUNDS_DIR / f"{stem}.attempt"
            if aside.exists():
                shutil.rmtree(aside)
            rounds.rename(aside)
        for png in (self.dir / "frames").glob(f"{stem}__*.png"):
            png.unlink()
        carried = self.carry_section(before, n)
        if carried is None:
            return None
        carried.pop("carried_from", None)
        carried.pop("carried_cost_usd", None)
        errors = [f for f in attempt.get("findings") or [] if f.startswith("error")]
        return dict(carried, kept_from=before.get("version"),
                    cost_usd=attempt.get("cost_usd", 0.0),
                    attempt={"version": attempt.get("version"), "score": attempt.get("score"),
                             "best_round": attempt.get("best_round"), "errors": errors[:5]})

    def write_shots(self, shots: dict[str, list[dict]]) -> Path:
        """shots.json: per symbol id, its shots (label, at inside the symbol,
        seconds), which `sr.py frame` reads to place the proof frames."""
        path = self.dir / "shots.json"
        path.write_text(json.dumps(
            {sid: [{"label": str(s["label"]), "at": s["at"], "seconds": s["seconds"]}
                   for s in rows] for sid, rows in shots.items()}, indent=1),
            encoding="utf-8")
        return path

    def _shots(self) -> dict[str, list[dict]]:
        data = read_json(self.dir / "shots.json", warn=False)
        return data if isinstance(data, dict) else {}

    def prepare(self, *, tokens: dict[str, str], frame: tuple[int, int]) -> None:
        sr_root = str(Path(sdk().__file__).resolve().parents[2])
        automation_root = str(Path(__file__).resolve().parent.parent)
        (self.dir / "sr.py").write_text(
            SR_WRAPPER.format(sr_root=sr_root, automation_root=automation_root),
            encoding="utf-8")
        (self.dir / "examples.scene.xml").write_text(EXAMPLES, encoding="utf-8")
        for rel, code in EXAMPLE_SHADERS.items():
            (self.dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.dir / rel).write_text(code, encoding="utf-8")
        (self.dir / "schema.md").write_text(self.cheat_sheet(), encoding="utf-8")
        (self.dir / "tokens.xml").write_text(
            "<styles>\n" + "".join(f'  <token name="{k}" value="{v}"/>\n'
                                   for k, v in tokens.items())
            + "</styles>\n", encoding="utf-8")
        (self.dir / "frame.json").write_text(
            json.dumps({"width": frame[0], "height": frame[1], "fps": 30}), encoding="utf-8")
        # The author composes by program: srkit.py, kit.py and kit/ (the SDK's
        # guide, what the engine does per renderer, proven example programs).
        from .probes import latest_measured
        sdkkit.install(self.dir, measured=latest_measured(self.root.parent) or None)

    @staticmethod
    def program_name(doc: str) -> str:
        """The program that writes a document: `01-open.scene.xml` -> `01-open.py`."""
        return doc[:-len(".scene.xml")] + ".py" if doc.endswith(".scene.xml") else doc + ".py"

    @staticmethod
    def cheat_sheet() -> str:
        """Generated from the schema at run time, never written by hand."""
        S = sdk()
        lines = [f"# scene-render schema, as the SDK describes it", "",
                 "Ask for anything not here: `python3 sr.py describe <element>`.", ""]
        for el in SHEET_ELEMENTS:
            try:
                d = S.describe(el).to_dict()
            except Exception:
                continue
            lines.append(f"## <{el}>" + (f" — {str(d.get('doc', '')).strip()[:200]}"
                                           if d.get("doc") else ""))
            for a in d.get("attributes", []):
                t = a.get("type") or {}
                kind = t.get("enum") or t.get("name") or t.get("base") or "?"
                if isinstance(kind, list):
                    kind = "|".join(kind)   # every value: a cut list hid effect type="shader"
                lines.append(f"- @{a['xml_name']}: {kind}")
            kids = [c.get("xml_name") or c.get("class_name")
                    for c in (d.get("children") or []) if isinstance(c, dict)]
            if kids:
                lines.append("- children: " + ", ".join(str(k) for k in kids))
            lines.append("")
        lines += ["## by family", "", reach.families_text(S.element_names()), "",
                  "## every element", "", ", ".join(sorted(S.element_names())), ""]
        return "\n".join(lines)

    def brief(self, n: int, *, idea: Idea, house_rules: str, style: dict,
              tokens: dict[str, str], frame: tuple[int, int], section: dict,
              effects: list[dict], words: dict[str, dict], assets: list[dict],
              direction_block: str = "", worlds: list[dict] | None = None,
              constraints: list[dict] | None = None, look: bool = True) -> Path:
        """What the author of one section has to make, and the facts it has:
        one world symbol per section (or per planned cut), its shots as
        phases at their offsets, the hero convention, the props, the gate."""
        worlds = worlds if worlds is not None else world_symbols(section, effects)
        lines = [f"# Section {n}: {section.get('heading', '')}", "",
                 f"Video: {idea.title}", f"Premise: {idea.description}", "",
                 f"Frame {frame[0]}x{frame[1]} at 30 fps. This section is authored as "
                 f"{'ONE symbol' if len(worlds) == 1 else f'{len(worlds)} symbols'}: the "
                 "world. Times inside a symbol are seconds from the symbol's start; "
                 "`yt build` includes the world once for its consecutive shots, so "
                 "symbol time runs continuously through the section and each shot is a "
                 "phase at the offset given below.", ""]
        lines += ["## Mission", "", MISSION, ""]
        if direction_block:
            lines += ["## Editorial treatment", "", direction_block, ""]
        if house_rules:
            lines += ["## Standards", "", house_rules, ""]
        lines += constraint_lines(constraints or [],
                                  {str(s["label"]) for w in worlds for s in w["shots"]})
        lines += ["## Visual system", "", style.get("look", ""), "",
                  f"Type: {style.get('typography', '')}", "",
                  f"Motion rules: " + " ".join(f"({i}) {r}" for i, r in
                                              enumerate(style.get("motion_rules", []), 1)), "",
                  "Tokens (the program adds them to the document's <styles> as tokens.xml "
                  "lists them, and every colour references them as var(--name)):", ""]
        lines += [f"- --{k}: {v}" for k, v in tokens.items()]
        if style.get("avoid"):
            lines += ["", "Avoid: " + "; ".join(style["avoid"])]
        doc = self.doc_name(n, section)
        ids = ", ".join(f'`{w["id"]}`' for w in worlds)
        lines += ["", f"## The world to author ({'one <symbol>' if len(worlds) == 1 else 'these <symbol>s'}: {ids})", "",
                  "A section is one world: one set, one set of materials, one light rig, "
                  "one hero model and ONE camera whose path is keyed across the whole "
                  "symbol. The shots below are phases of that world at their offsets, "
                  "not separate builds: the hero keeps its size, colour and material "
                  "from the first phase to the last, the room does not change, and the "
                  "camera moves through the phases in one continuous path (ease between "
                  "keys; no jump in position, yaw or focal length at a shot boundary "
                  "unless the plan lists a cut). Give the symbol `duration` equal to its "
                  "length below and key every animation on that one timeline.", "",
                  "Hero convention: give every object the camera must never lose an id "
                  "starting with `hero-` (e.g. `hero-subject`); the frame gate reads the "
                  "engine's off-camera report and a `hero-` object outside the camera's "
                  "view for a whole shot is an error. Use the prefix only for the "
                  "subject, never for the set.", "",
                  "Every object3D must be seen: the engine reports each object that is "
                  "never inside the camera's view, the delivery gate fails on that "
                  "warning, and so does this gate. Build only the set the camera sees "
                  "(a room is the walls and floor in frame, not a closed box), key an "
                  "object that is seen in some shots only to exist then (`visible`, "
                  "`start`/`end` or `condition` on the object3D), and cull the rest.", "",
                  "The hero must not pass through the set: the geometry gate evaluates "
                  f"the world every {geometry.STEP:g} s with the engine (`scene-render eval`) "
                  "and intersects the hero's box (the `hero-` object with every node "
                  "parented to it) with every set solid; a penetration deeper than a "
                  "tenth of the hero's smallest dimension is an error naming the time, "
                  "the object and the depth. Key its path to clear every set solid: "
                  "the floor, the walls and the furniture of the set. A hero that touches "
                  "an object by design (attached to it, not passing through it) is declared "
                  f"joined with `<meta name=\"{geometry.ATTACH_META}\" value=\"<hero id> "
                  "<object id or prefix*> ...\"/>` (a trailing * names every id with that "
                  "prefix), and the nodes it follows by `parent` never count.", "",
                  "Resting rule: a resting hero's lowest point equals the surface's top. "
                  "y points down (the floor is y=0, up is negative), so a surface's top "
                  "is its centre y minus half its height: a box at y=-77 with "
                  "height=4 has its top at y=-79, not -75 (-75 is its underside). A hero "
                  "whose lowest point sits 1.5 below its origin rests there at y=-80.5. "
                  "Declare the rest so the gate also checks it is not floating: "
                  f"`<metadata><meta name=\"{geometry.REST_META}\" value=\"<hero id> "
                  "<surface id> <seconds>\"/></metadata>` (examples.scene.xml has one).", "",
                  "Grounding rule: the gate projects the world through the camera at "
                  "every step. When the hero lies on screen over a farther surface's top "
                  f"(over {geometry.OVERLAP_SHARE:.0%} of its own area), a little above it "
                  f"(under {geometry.CLEARANCE_RATIO:.0%} of its distance from the camera, too "
                  "little for parallax), with no shadow of it on that surface, for "
                  f"{geometry.MERGED_SECONDS:g} s or more, it reads as merged into the surface "
                  "although it is in the air: an error (`reads merged`). Give that frame a "
                  "cue: a shadow-casting light (castShadow=\"true\") whose direction lands the "
                  "hero's shadow on the surface in frame, a line of sight lifted toward "
                  "the surface's plane so the hero is seen against what is behind it, or "
                  "the hero framed clear of the surface.", ""]
        for w in worlds:
            lines += [f"### `<symbol id=\"{w['id']}\" width=\"{frame[0]}\" height=\"{frame[1]}\" "
                      f"duration=\"{w['seconds']:g}\">` — starts {w['offset']:g}s into the "
                      f"section ({w['start']:g}s into the film)", "",
                      "Shots as phases (label · offset inside the symbol · length):", ""]
            for s in w["shots"]:
                planned = s.get("planned_seconds")
                clock = (f" (recorded; the screenplay planned {planned:g}s)"
                         if s.get("timed_by") == "voice" and planned is not None
                         and float(planned) != float(s["seconds"]) else "")
                lines += [f"#### {s['label']} · at {s['at']:g}s · {s['seconds']:g}s{clock}",
                          f"Picture: {s.get('visual', '')}",
                          f"Narration: {s.get('narration') or '(none)'}"]
                lines += super_lines(s)
                wd = words.get(s["label"])
                if wd and wd.get("words"):
                    lines.append("Words (seconds from the shot's start; add the shot's "
                                 "offset for symbol time): "
                                 + " ".join(f"{x['w']}@{x['s']}" for x in wd["words"]))
                lines.append("")
            if not w["shots"]:
                lines += ["(no screenplay yet: one phase per effect, in order)", ""]
            lines += ["Phases this world must deliver (from the plan; each is a beat "
                      "inside the one symbol, at the time of the shots it names):", ""]
            for e in w["phases"]:
                lines += [f"- **{e['name']}** (`{e['id']}`; shots "
                          f"{', '.join(e.get('shots', [])) or 'all'}; ~{e.get('seconds')}s)",
                          f"  Must convey: {e.get('conveys', '')}",
                          f"  On screen: {e.get('on_screen', '')}"]
                if e.get("data_or_claim"):
                    lines.append(f"  Carries: {e['data_or_claim']}")
                lines += [f"  Brief: {e.get('brief', '')}", ""]
        lines += research_asset_lines(assets, self.root, self.dir)
        props = props_items(self.root)
        if props:
            hdris = [it for it in props if it.get("kind") == "hdri"]
            models = [it for it in props if it.get("kind") == "model"]
            others = [it for it in props if it.get("kind") not in ("hdri", "model")]
            lines += ["## Props (CC0, fetched by `yt props`; paths relative to this directory)", "",
                      "These are the rules, not suggestions; confirm every attribute with "
                      "`python3 sr.py describe light`, `describe mesh`, `describe object3D` "
                      "and `describe material` before writing it:", ""]
            if hdris:
                lines += ["- **Environment lighting from an HDRI is the default light rig.** "
                          "Declare `<light id=\"env\" type=\"dome\" environment=\"<hdr path>\" "
                          "intensity=...>` in <lights> (`environmentVisible=\"false\"` keeps the "
                          "set as the background) and add one directional key with "
                          "`castShadow=\"true\"` for the sun; flat ambient fill alone is not "
                          "a light rig. Pick the HDRI whose light matches the treatment."]
            if models:
                lines += ["- **A model that fits a set piece is used as a model**, never "
                          "rebuilt from primitives: declare `<mesh id=\"m-x\" src=\"<gltf "
                          "path>\"/>` in <assets> (the schema reads the format from the "
                          "extension; license/credit go on the asset) and place it with "
                          "`<object3D primitive=\"mesh\" mesh=\"m-x\" .../>`; the model's own "
                          "textured materials come with it (`materialOverride` to swap one, "
                          "`node` to draw a part). Scale it to the room with width/height/depth."]
            lines += ["- **Large surfaces carry texture maps**: the floor, the walls and "
                      "every broad set piece get a material with `baseColorMap` (sRGB colour), `normalMap` "
                      "(`normalScale`), `metallicRoughnessMap` and/or `occlusionMap` (the "
                      "_arm_ maps below are ambient-occlusion/roughness/metallic), with "
                      "`uvScaleX`/`uvScaleY` for tiling; a flat `baseColor` on a wall or "
                      "any broad surface is what the review calls \"flat-shaded CG\". A thin, "
                      "translucent material gets `transmission`/`subsurface` so it glows when backlit.",
                      "- The hero may stay a built primitive or extrude when no model fits "
                      "it, but it takes a real material (maps, roughness, translucency) "
                      "and casts a contact shadow.",
                      "- **This is checked.** A listed HDRI with no light carrying "
                      "`environment=`, or a listed model with no `<mesh>` asset, is an "
                      "error. A prop that does not fit the treatment may be declined, but "
                      "you must say so and why, in the document: "
                      f"`<metadata><meta name=\"{DECLINE_META}\" value=\"<prop id or kind>: "
                      "<reason>\"/></metadata>` right after <project>, one entry per "
                      "declined kind (`hdri`, `model`) or per prop id. The reason is "
                      "written into vfx.json for the operator; a decline without a "
                      "reason does not count.", ""]
            if hdris:
                lines += ["### HDRIs (environment maps)", ""]
                for it in hdris:
                    rel = os.path.relpath(self.root / it["file"], self.dir)
                    lines.append(f"- `{rel}` — {it.get('about', '')} [{it.get('licence', '')}"
                                 + (f", {it['credit']}" if it.get("credit") else "") + "]")
                lines.append("")
            if models:
                lines += ["### Models (glTF with textures)", ""]
                for it in models:
                    rel = os.path.relpath(self.root / it["file"], self.dir)
                    lines.append(f"- `{rel}` — {it.get('about', '')} [{it.get('licence', '')}"
                                 + (f", {it['credit']}" if it.get("credit") else "") + "]")
                    maps = prop_textures(self.root, it)
                    if maps:
                        lines.append("  maps, usable on any surface: "
                                     + ", ".join(f"`{os.path.relpath(m, self.dir)}`" for m in maps))
                lines.append("")
            if others:
                lines += ["### Other files", ""]
                for it in others:
                    rel = os.path.relpath(self.root / it["file"], self.dir)
                    lines.append(f"- `{rel}` ({it.get('kind', '')}) — {it.get('about', '')} "
                                 f"[{it.get('licence', '')}"
                                 + (f", {it['credit']}" if it.get("credit") else "") + "]")
                lines.append("")
        look = approved_look(self.root) if look else None
        if look and look.get("source") == "section":
            rel = lambda key: os.path.relpath(self.root / look[key], self.dir)  # noqa: E731
            shown = [f"`{rel('frame')}`"] if look.get("frame") else []
            if look.get("sheet"):
                shown.append(f"`{rel('sheet')}` (all its proof frames)")
            # The author may read its own directory and keyframe/, never another
            # version's; keyframe/look.py is the approved program, byte for byte.
            program_ref = rel("look_program") if look.get("look_program") else ""
            lines += [f"## The approved look (section {look.get('section')} of this film, "
                      f"approved {look.get('approved_at', '')})", "",
                      f"The operator approved section {look.get('section')} as the film's "
                      "look. It is part of this film, and yours must read as the same film. "
                      + (f"Read its program `{program_ref}` first, " if program_ref
                         else "")
                      + (f"look at {' and '.join(shown)}. " if shown else "")
                      + "Reuse its constructions: copy into your program its palette and type "
                      "constants, the helper functions that build its labels, formulas, "
                      "cards, materials, lights and recurring objects, and its camera "
                      "language, and change only what your shots require. Do not import or "
                      "run that program; copy what you need. The review scores fidelity "
                      "to it.", ""]
        elif look:
            doc_rel = os.path.relpath(self.root / look["document"], self.dir)
            png_rel = os.path.relpath(self.root / look["frame"], self.dir)
            lines += [f"## The approved look (keyframe, approved {look.get('approved_at', '')})", "",
                      "The operator approved one frame as the film's look. Read both files "
                      f"first: `{doc_rel}` (the document) and `{png_rel}` (the frame). "
                      "The approved look: inherit these materials, lights, hero model and "
                      "camera language; a world that departs from it is wrong. Rebuild the "
                      "document's <materials>, <lights> and the hero's geometry in your "
                      "program with the same values (its own program, beside it, shows how "
                      "they were built), keep its camera language, and change only what "
                      "the shots require (the camera path, the motion, the set beyond the "
                      "frame). The review scores fidelity to it.", ""]
            for el in look.get("elements") or []:
                if isinstance(el, dict):
                    lines.append(f"- {el.get('kind', '')}: **{el.get('name', '')}** "
                                 f"({el.get('source', '')}"
                                 + (f"; `{el['file']}`" if el.get("file") else "") + ")"
                                 + (f" — {el['why']}" if el.get("why") else ""))
            lines.append("")
        program = self.program_name(doc)
        lines += ["Fonts: text may use only the families the repo ships ("
                  + ", ".join(fonts.families()) + "); any other family fails `yt build`.", ""]
        lines += [sdkkit.BRIEF, sdkkit.time_line(AUTHOR_TIMEOUT), "## Deliverable", "",
                  f"Write `{program}` in this directory: a Python program that builds the "
                  f"section with the SDK and saves `{doc}`, a complete scene-render document "
                  f"(project, styles with the tokens, assets, materials, symbols -- {ids} "
                  "exactly, sized to the frame, with the camera, the set, the hero and "
                  "every phase inside -- a composition that instances the world as a "
                  "preview, lights, and an output). The program is the source: a change "
                  "is made in it, never in the XML it writes, and the stage builds the "
                  "program itself, so a hand-edited document is overwritten. Read "
                  "kit/SDK-GUIDE.md and kit/examples/ first: every example rendered on "
                  "this engine; examples.scene.xml shows more constructs as XML, and the "
                  "SDK writes the same elements. Ask the schema (`python3 kit.py "
                  "describe <element>`) before guessing an attribute. Then run `python3 "
                  f"kit.py build {program}` and fix every error it reports until it says "
                  "0 error(s) and a deterministic document. Time beats to the spoken words "
                  "given above. Do not create any other file than the program and the "
                  "document it saves.", "",
                  "## How your symbol is used, and the frame gate", "",
                  "`yt build` places the world with one `<include src=... symbol=...>` "
                  "spanning its consecutive shots. An include carries the symbol, its "
                  "materials, cameras and assets; it does NOT carry the document's "
                  "<styles> or <lights>. Declare your <lights> at scene level anyway: the "
                  "build hoists them, gated to the include's time. The camera inside the "
                  "symbol is the section's camera; 3D axes are x right, y DOWN, z into "
                  "the scene, and `target=\"<id>\"` aims a camera at a node (see `sr.py "
                  "describe camera`).", "",
                  "Validation cannot see the picture. After the build passes, run "
                  f"`python3 sr.py frame {doc}`: it renders a proof frame at each shot's "
                  "midpoint (from shots.json) plus the symbol's start and end, the way "
                  "the build includes it, writes frames/*.png and reports what the "
                  "engine says: flat, black, clipped or too-dark frames, and the "
                  "checklist's mechanical items (framing, tonal steps, haze, labels "
                  "that overlap; `kit.py build` warns of a label in the caption band, "
                  "`rule:band`). It renders stills only, in seconds; "
                  "the gate then encodes each shot once a round, on the encode host, "
                  "and reports every object3D the camera never frames, so keep the "
                  "hero in view in every frame you look at. Read every PNG. A black, flat, "
                  "empty or wrong frame, a hero out of view, or a hero that changes "
                  "between frames means the document is not done: fix the camera, the "
                  "lights, the materials or the geometry in the program, build and render "
                  "again (`--symbol ID --at SECONDS` for one frame). A document counts as "
                  "authored only when its program builds with 0 errors and every proof "
                  "frame shows what its shot asks "
                  "for; a reviewer then scores the frames against the publication bar "
                  "and sends the findings back to you if they fall short."]
        path = self.dir / f"brief-{n:02d}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    @staticmethod
    def doc_name(n: int, section: dict) -> str:
        return f"{n:02d}-{slugify(section.get('heading', 'section'), 36)}.scene.xml"

    def frames(self, name: str, *, symbol: str | None = None,
               times: list[float] | None = None, frame: tuple[int, int] | None = None,
               jobs: int = 3, shots: dict[str, list[dict]] | None = None,
               encode: bool = True) -> list[tuple[Path | None, list[str]]]:
        """Render each symbol of a document as `yt build` includes it, at the
        midpoint of each shot it holds (`shots`, else shots.json, else the
        symbol's start, middle and end).

        Writes frames/<doc>__<symbol>__<t>s.png and returns (png path or None,
        findings) per frame: the engine's diagnostics plus framecheck's
        verdicts (flat, black, clipped, dark; a hero the camera never frames,
        the set objects out of view, and -- once per symbol, on its first
        frame -- a camera pointing at nothing). The camera verdicts come from
        one short draft encode per shot (`encode_findings`), since only an
        encode computes them; each shot's verdict lands on its midpoint frame.
        The encodes are the gate's (`validate`, once a round): an author's own
        `sr.py frame` passes `encode=False` and gets stills alone, so a look
        costs seconds, not the 20-40 minutes a whole-world encode took
        (docs/throughput.md, run 2). The author looks at the PNGs; this only
        catches what needs no eyes.
        """
        path = self.dir / name
        if not path.is_file():
            return [(None, ["error: the author wrote no document"])]
        text = path.read_text(encoding="utf-8", errors="replace")
        frame = frame or self._frame_from_tokens()
        facts = [f for f in symbol_facts(text) if symbol is None or f["id"] == symbol]
        if not facts:
            return []
        shots = shots if shots is not None else self._shots()
        out_dir = self.dir / "frames"
        out_dir.mkdir(exist_ok=True)
        extra = referenced_files(text, self.dir, self.root)
        stem = name[:-len(".scene.xml")] if name.endswith(".scene.xml") else name
        objects = {f["id"]: framecheck.symbol_objects(text, f["id"]) for f in facts}
        jobs_list = [(f, t) for f in facts
                     for t in (times or shot_times(shots.get(f["id"], []), f["duration"]))]
        # The document's own labels are judged once per symbol, on its first frame.
        first_at = {}
        for f, t in jobs_list:
            first_at.setdefault(f["id"], t)

        def one(fact: dict, t: float) -> tuple[Path | None, list[str], list[str]]:
            proof = proof_document(name, text, fact["id"], frame, fact["duration"],
                                   fact["background"])
            files = dict(extra)
            files[f"{self.rel}/{name}"] = text.encode("utf-8")
            # A path-traced or volumetric world goes to the host with the memory
            # for it, one kit render per host at a time (sdkkit.frame's rule).
            host = sdkkit.frame_host(text)
            with sdkkit.host_lock(host):
                png, message = render.engine_render_still(
                    proof.encode("utf-8"), t, host=host, files=files,
                    doc_path=f"{self.rel}/.proof-{stem}.scene.xml")
            where = f"frame at {t:g}s of {fact['id']}"
            retried, ran = "", host
            if png is None and (infra_failure(message) or cost_failure(message)):
                # The host lost the frame, or stopped it for its cost: forget
                # what this process knew of it and render once more on the
                # other host before the author is charged with anything.
                getattr(render, "_HOSTS", {}).pop(host, None)
                other = fallback_host(host)
                if other:
                    first = message.partition("\n")[0]
                    with sdkkit.host_lock(other):
                        png, message = render.engine_render_still(
                            proof.encode("utf-8"), t, host=other, files=files,
                            doc_path=f"{self.rel}/.proof-{stem}.scene.xml")
                    retried = (f"{host or 'this machine'} answered: {first}; "
                               f"retried on {other}")
                    ran = other
            lines = message.splitlines()
            off = framecheck.never_in_view(lines)
            findings = [l for l in lines[1:] if not l.startswith("warning[E12]")
                        and not framecheck.OFF_CAMERA.search(l)]
            findings = [f"{l.split(':', 1)[0]}: {where}: "
                        f"{l.split(':', 1)[1].strip()}" if ":" in l else l for l in findings]
            findings += framecheck.view_findings(off, objects[fact["id"]],
                                                 symbol=fact["id"], at=t)
            if png is None:
                if cost_failure(message):
                    # No host holds the world as written: the author's error,
                    # naming what makes it heavy, as sdkkit.frame rules.
                    how = ("killed for memory" if STILL_KILLED.search(message) else
                           f"stopped at the {sdkkit.STILL_DEADLINE} s still deadline")
                    findings = [f for f in findings if not f.startswith("error")]
                    return None, [f"error: {where} not rendered: {how} on "
                                  f"{ran or 'this machine'}"
                                  + (f" ({retried})" if retried else "")
                                  + ": no host can render this world as written. Cost "
                                  f"drivers of this document: {sdkkit.render_settings(text)}. "
                                  "Lower them (fewer blobs or cells, fewer pathSamples or "
                                  "maxBounces, no medium) and judge composition at a "
                                  "setting that renders quickly"] + findings, off
                if infra_failure(message):
                    # Filed as the host's, never as the author's error: the
                    # gate waits and looks again instead of paying a revision.
                    return None, [f"infra: {where} was not rendered: {lines[0]}"
                                  + (f" ({retried})" if retried else "")] + findings, off
                if not any(f.startswith("error") for f in findings):
                    findings.insert(0, f"error: {where} was not rendered: {lines[0]}")
                return None, findings, off
            if retried:
                findings.append(f"note: {where} rendered after a host failed it: {retried}")
            target = out_dir / f"{stem}__{fact['id']}__{t:g}s.png"
            target.write_bytes(png)
            judged = judge_frame(png, t, fact["id"])
            # The checklist's mechanical items (framing, tonal steps, haze, and
            # the labels once per symbol), as kit.py frame runs them: the
            # author answers them in its own round, not in a paid review.
            if not any(f.startswith("error") for f in judged):
                judged += framecheck.precheck(png, t, fact["id"],
                                              text if first_at.get(fact["id"]) == t else "")
            return target, judged + findings, off

        # One encode window per shot of each 3D symbol (the whole symbol when
        # it has no shots): the only render that reports what the camera
        # never frames. None when the caller wants stills alone.
        windows = [(f, s) for f in facts if encode and objects[f["id"]]
                   for s in (shots.get(f["id"]) or
                             [{"label": "", "at": 0.0, "seconds": f["duration"]}])]

        def view(fact: dict, shot: dict) -> tuple[list[str] | None, list[str]]:
            proof_path = f"{self.rel}/.proof-{stem}.scene.xml"
            files = dict(extra)
            files[f"{self.rel}/{name}"] = text.encode("utf-8")
            files[proof_path] = proof_document(name, text, fact["id"], frame, fact["duration"],
                                               fact["background"]).encode("utf-8")
            start = max(0.0, float(shot["at"]))
            end = min(fact["duration"], start + float(shot["seconds"])) if fact["duration"] > 0 \
                else start + float(shot["seconds"])
            if end <= start:
                end = start + 0.1
            lines = encode_findings(files, proof_path, start=start, end=end)
            if lines is None:
                return None, []
            off = framecheck.never_in_view(lines)
            mid = round((start + end) / 2, 2)
            found = [l for l in lines if l.startswith("error") and not framecheck.OFF_CAMERA.search(l)]
            found += framecheck.view_findings(off, objects[fact["id"]], symbol=fact["id"],
                                              at=mid, label=str(shot.get("label") or ""))
            return off, found

        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            rendered = list(pool.map(lambda ft: one(*ft), jobs_list))
            viewed = list(pool.map(lambda fs: view(*fs), windows))
        out: list[tuple[Path | None, list[str]]] = []
        first_of: dict[str, int] = {}
        per_symbol: dict[str, list[list[str]]] = {}
        stills_off: dict[str, list[list[str]]] = {}
        for (fact, _), (png, findings, off) in zip(jobs_list, rendered):
            first_of.setdefault(fact["id"], len(out))
            stills_off.setdefault(fact["id"], []).append(off)
            out.append((png, findings))
        for (fact, shot), (off, found) in zip(windows, viewed):
            if off is None:
                continue
            per_symbol.setdefault(fact["id"], []).append(off)
            if not found:
                continue
            start = float(shot["at"])
            end = start + float(shot["seconds"])
            mid = (start + end) / 2
            inside = [i for i, (f, t) in enumerate(jobs_list)
                      if f["id"] == fact["id"] and start <= t < end]
            at = min(inside, key=lambda i: abs(jobs_list[i][1] - mid)) if inside \
                else first_of[fact["id"]]
            png, findings = out[at]
            out[at] = (png, found + findings)
        for sid in objects:
            frames_off = per_symbol.get(sid) or [o for o in stills_off.get(sid, []) if o]
            verdict = framecheck.never_framed(frames_off, objects[sid], symbol=sid)
            if verdict:
                png, findings = out[first_of[sid]]
                out[first_of[sid]] = (png, verdict + findings)
        return out

    def _frame_from_tokens(self) -> tuple[int, int]:
        meta = self.dir / "frame.json"
        if meta.is_file():
            try:
                d = json.loads(meta.read_text())
                return int(d["width"]), int(d["height"])
            except (ValueError, KeyError, TypeError):
                pass
        return (1920, 1080)

    def validate(self, name: str, *, frames: bool = True,
                 shots: dict[str, list[dict]] | None = None,
                 props: bool = False) -> tuple[list[str], list[str]]:
        """(symbol ids present, findings) for one authored document.

        Findings come from the SDK, the engine's validation, the props rule
        when `props` is set (the section author must use or decline each
        listed HDRI and model; other stages reuse this check without it)
        and, when an engine is reachable, from the proof frames of every
        symbol at its shots' midpoints (`shots` per symbol id, else shots.json).
        """
        path = self.dir / name
        if not path.is_file():
            return [], ["error: the author wrote no document"]
        data = path.read_bytes()
        try:
            doc = sdk().read(data)
        except Exception as exc:
            return [], [f"error: the document does not parse: {str(exc)[:200]}"]
        findings = [str(f) for f in doc.check()]
        engine = render.engine_validate(data)
        if engine is None:
            findings.append("note: no engine reachable; only the SDK's checks ran")
        else:
            findings += engine
        findings += unbounded_layer_findings(data.decode("utf-8", "replace"))
        listed = props_items(self.root) if props else []
        if listed:
            textures = [p.as_posix() for it in listed for p in prop_textures(self.root, it)]
            textures += [str(it["file"]) for it in listed if it.get("kind") == "texture"]
            findings += props_findings(data.decode("utf-8", "replace"), listed, textures)[0]
        # find_all() is direct children only; iter() walks the tree.
        symbols = [str(el.id) for el in doc.iter("symbol") if getattr(el, "id", None)]
        if shots:
            # Each symbol has to span its shots, or the build holds its last
            # frame under the narration. Checked before any frame is spent.
            required = {sid: world_span(rows) for sid, rows in shots.items() if rows}
            findings += timing_findings(data.decode("utf-8", "replace"), required)
        if frames and engine is not None and not any(f.startswith("error") for f in findings):
            rendered = 0
            for png, found in self.frames(name, shots=shots):
                rendered += png is not None
                findings += found
            findings.append(f"note: {rendered} frame(s) rendered under frames/")
            # The frames show the hero; only the geometry says whether it is
            # inside a set solid. Same engine, no GPU: eval at every step.
            findings += self.geometry(name, shots=shots)
        return symbols, findings

    def geometry(self, name: str, *, symbol: str | None = None,
                 shots: dict[str, list[dict]] | None = None, step: float = geometry.STEP,
                 runner=None) -> list[str]:
        """The geometry gate over one document: each 3D symbol's proof
        document (as `yt build` includes it, under the include id `shot`)
        is evaluated by the engine every `step` seconds through its shots
        (`shots` per symbol id, else shots.json, else the symbol's whole
        life), and every hero group's world box is intersected with every
        set solid. A penetration is an error naming the time, the hero, the
        object, the depth and the fix; a declared rest
        (`<meta name="hero-rests" ...>`) is checked for floating, and a
        declared attachment (`<meta name="hero-attached" ...>`) is no
        penetration. `runner` replaces EVAL_RUNNER's (a test's seam)."""
        path = self.dir / name
        if not path.is_file():
            return ["error: the author wrote no document"]
        text = path.read_text(encoding="utf-8", errors="replace")
        facts = [f for f in symbol_facts(text) if symbol is None or f["id"] == symbol]
        shots = shots if shots is not None else self._shots()
        frame = self._frame_from_tokens()
        extra = referenced_files(text, self.dir, self.root)
        stem = name[:-len(".scene.xml")] if name.endswith(".scene.xml") else name
        proof_path = f"{self.rel}/.proof-{stem}.scene.xml"
        out: list[str] = []
        for fact in facts:
            objects = geometry.document_objects(text, symbol=fact["id"], prefix="shot/")
            if not geometry.hero_groups(objects):
                continue                      # a 2D symbol, or one with no hero
            rests = geometry.rests_declared(text, prefix="shot/")
            attached = geometry.attachments_declared(text, prefix="shot/")
            held = shots.get(fact["id"]) or [{"label": "", "at": 0.0, "seconds": fact["duration"]}]
            times = geometry.sample_times(held, step)
            files = dict(extra)
            files[f"{self.rel}/{name}"] = text.encode("utf-8")
            files[proof_path] = proof_document(name, text, fact["id"], frame, fact["duration"],
                                               fact["background"]).encode("utf-8")
            run = runner if runner is not None else EVAL_RUNNER(proof_path, files=files)
            # The proof document hoists the file's <lights> unchanged, so the
            # evaluated light elements keep these ids; the camera is the
            # world's own, under the include id `shot`.
            report = geometry.evaluate(
                objects, times, run, rests=rests, attached=attached,
                cameras=geometry.document_cameras(text, symbol=fact["id"], prefix="shot/"),
                lights=geometry.document_lights(text), frame=frame, step=step)
            out += geometry.findings(report, symbol=fact["id"], shots=held)
        return out


# --------------------------------------------------------------------------- #
# the planner
# --------------------------------------------------------------------------- #

#: `yt vfx --finish-now` writes this file into the version being authored; the
#: run reads it between rounds (as it reads SIGUSR1) and ends every section
#: on its best reviewed round instead of paying for another revision.
FINISH_FILE = "FINISH"


def finish_now(root: Path) -> Path:
    """Ask the run authoring under `root` to finish: write FINISH into the
    newest vfx/vNN/, which is the one a running stage is in."""
    have = Workspace.versions(Path(root) / SCENE_DIR)
    if not have:
        raise ValueError("--finish-now needs a run to finish; there is no version under vfx/")
    path = Path(root) / SCENE_DIR / have[-1] / FINISH_FILE
    path.write_text(f"finish asked at {time.strftime('%Y-%m-%dT%H:%M:%S')}\n", encoding="utf-8")
    return path


class VfxPlanner:
    """Plans the visuals, then has the model author them, section by section."""

    def __init__(self, bundle: dict, dossier: Dossier, workspace_dir: Workspace, *,
                 model: str = "sonnet", inspo: list[str] | None = None,
                 house: str = "", house_digest: dict | None = None,
                 per_section: int = 3, jobs: int = 2,
                 budget_usd: float = SECTION_BUDGET_USD,
                 stage_budget_usd: float | None = None,
                 reviewer_model: str = REVIEWER_MODEL, review_budget_usd: float = REVIEW_USD,
                 budget_hours: float | None = None, sections: list[dict] | None = None,
                 words: dict[str, dict] | None = None, frame: tuple[int, int] = FRAME,
                 direction: dict | None = None, revisions: int = DEFAULT_ROUNDS,
                 author_bar: float = 7.0, fresh: bool = False,
                 verbose: bool = True, sections_only: set[int] | None = None,
                 lead: int | None = None, look_notes: list[str] | None = None,
                 relook: bool = False, recover: bool = False,
                 take_attempt: set[int] | None = None, take_why: str = "") -> None:
        self.idea: Idea = bundle["idea"]
        self.bundle = bundle
        self.dossier = dossier
        self.ws = workspace_dir
        self.model = model
        self.inspo = inspo or []
        self.house = house
        self.house_digest = house_digest or {}
        self.per_section = per_section
        self.jobs = max(1, jobs)
        #: One section's budget, author sessions and reviews together; the stage's
        #: covers every section (by default, each section's budget over the sections).
        self.budget_usd = budget_usd
        self.stage_budget_usd = stage_budget_usd
        self.spend = SpendBudget(budget_usd, stage_budget_usd or budget_usd)
        #: The reviewer: its own model (a model never grades its own work) under
        #: its own cap, which is what a session holds back for the review after it.
        self.reviewer_model = reviewer_model
        self.review_budget_usd = float(review_budget_usd)
        self.budget_hours = budget_hours
        self.words = words or {}
        # Once a take exists, the picture is planned on the narration's
        # clock, which is the clock `yt build` places it on.
        self.script = measured_sections(sections or [], self.words)
        self.frame = frame
        self.direction = direction or {}
        self.revisions = max(0, revisions)
        self.author_bar = float(author_bar)
        self.fresh = fresh
        self.verbose = verbose
        #: Sections to re-author (1-based); the others are carried forward from
        #: the active version, and what depends on the authoring is redone.
        self.sections_only = set(sections_only or ())
        self._stale = ({"12-author", "12-continuity", "14-reach", "15-plan"}
                       if self.sections_only else set())
        #: The look pass (`yt keyframe`): only this section is authored, once,
        #: judged by the mechanical gate alone, for the operator to approve as
        #: the film's look; `yt vfx` then carries it forward (`approved_look`).
        self.lead = lead
        self.look_notes = [str(n) for n in look_notes or () if str(n).strip()]
        # The look pass pays for no vision review: the revision rounds go back
        # to the author only for what the gate finds (errors), never for a score.
        self.review = lead is None
        if lead is not None:
            self._stale.add("12-lead")
        #: `--relook`: put the approved look into an existing film -- carry its
        #: section from the look pass and author every other section against it.
        self.relook = bool(relook)
        if self.relook:
            self._stale |= {"12-author", "12-continuity", "14-reach", "15-plan"}
        #: `--recover`: a run that ended before it saved its record (killed,
        #: timed out, crashed) left its rounds on disk; rebuild the record from
        #: them and the cached reviews instead of authoring anything.
        self.recover = bool(recover)
        #: `--take-attempt N`: the operator takes a section's new attempt over
        #: the version the competition kept (`taken`).
        self.take_attempt = set(take_attempt or ())
        self.take_why = take_why
        if self.recover or self.take_attempt:
            self._stale |= {"12-author", "12-continuity", "13-scene", "14-reach", "15-plan"}
        #: The approved look the sections of this run are authored against
        #: (its approval digest, "" for none) and the section it came from.
        self._look_digest = ""
        self._look_n: int | None = None
        #: The planner's structured asks (outline, style, effects, reach, plan),
        #: one named decision each, kept under the project like the reviewers'.
        self.harness = harness.Harness(
            workspace_dir.root, "vfx", model=model, role=VFX_SYSTEM,
            fresh=fresh, verbose=verbose)
        self.usage = self.harness.usage
        #: Section authors and reviewers run in a pool; each reports here once.
        self._account_lock = self.harness.lock
        #: Set by SIGUSR1 (`author` installs the handler); read between rounds
        #: beside vfx/vNN/FINISH. Either ends every section on its best round.
        self.finish = threading.Event()

    def finish_requested(self) -> bool:
        return self.finish.is_set() or (self.ws.dir / FINISH_FILE).is_file()

    # -- plumbing ----------------------------------------------------------

    def _say(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr)

    def _account(self, *usages) -> None:
        """Book agents' usage into the stage's one account, from any thread.
        Each agent is booked once, when its work ends: an engine's usage is
        cumulative, so booking it after every call would count it again."""
        with self._account_lock:
            for usage in usages:
                _merge_usage(self.usage, usage)

    def _decide(self, name: str, prompt: str, schema: dict) -> object:
        return self.harness.decide(name, prompt, schema)

    def _stage(self, name: str, work) -> object:
        if not self.fresh and name not in self._stale:
            cached = self.dossier.load(name)
            if cached is not None:
                self._say(f"[{name}] cached")
                return cached
        self._say(f"[{name}] running")
        data = work()
        self.dossier.save(name, data)
        return data

    def _reference(self) -> str:
        if not self.inspo:
            return ""
        known = [f"- {n}: {CHANNEL_STYLES[_key(n)]}"
                 for n in self.inspo if _key(n) in CHANNEL_STYLES]
        unknown = [n for n in self.inspo if _key(n) not in CHANNEL_STYLES]
        parts = []
        if known:
            parts.append("Visual reference -- match how these channels look and "
                         "move:\n" + "\n".join(known))
        if unknown:
            parts.append("Also reference the visual style of: " + ", ".join(unknown) + ".")
        return "\n\n".join(parts)

    def _direction_block(self) -> str:
        """The editorial treatment `yt styles` chose: the aesthetic for this video."""
        styles = self.direction.get("styles") or []
        if not styles:
            return ("No treatment has been chosen yet (`yt styles` has not run); "
                    "choose the treatment each effect needs and say which.")
        parts = ["The editorial treatment for this video, best first "
                 f"(from `yt styles`; message: {self.direction.get('message', '')}):"]
        for n, st in enumerate(styles[:4], 1):
            line = f"{n}. {st.get('name', '')} (fit {st.get('fit', '?')}/10): {st.get('shows_well', '')}"
            for key in ("palette", "typography", "motion", "sound"):
                if st.get(key):
                    line += f"\n   {key}: {st[key]}"
            for shot in st.get("shots", [])[:5]:
                line += f"\n   planned shot: {shot}"
            parts.append(line)
        if self.direction.get("combination"):
            parts.append(f"How they combine: {self.direction['combination']}")
        return "\n".join(parts)

    @staticmethod
    def vocabulary() -> str:
        """The engine's elements by family, for the planner who specifies
        effects before any author opens the schema. Empty if no SDK."""
        try:
            return reach.families_text(sdk().element_names())
        except Exception:
            return ""

    def _idea_block(self) -> str:
        bits = [f"Title: {self.idea.title}", f"Genre: {self.idea.genre}",
                f"Premise: {self.bundle.get('premise') or self.idea.description}"]
        if self.idea.length:
            bits.append(f"Target length: {self.idea.length}")
        # Every prompt in this stage starts here, so the standards ride along.
        return "\n".join(bits) + (f"\n\n{self.house}" if self.house else "")

    # -- stages ------------------------------------------------------------

    def outline(self) -> dict:
        if self.script:
            return {"runtime": "", "sections": self.script}
        existing = self.bundle.get("outline") or {}
        if existing.get("sections"):
            return existing
        return self._stage("09-outline", lambda: self._decide(
            "plan-outline",
            f"{self._idea_block()}\n\n"
            "There is no script outline yet. Sketch one: 6-9 sections with a "
            "heading, a runtime in seconds and 2-4 beats each, totalling the "
            "target length. Open on something concrete.",
            OUTLINE_SCHEMA))

    def style(self) -> dict:
        numbers = [c for c in self.bundle["claims"] if c.get("number")][:12]
        return self._stage("10-style", lambda: self._decide(
            "plan-style",
            f"{self._idea_block()}\n\n{self._reference()}\n\n"
            "Numbers this video has to put on screen:\n"
            + "\n".join(f"- {c['number']}: {c['claim'][:110]}" for c in numbers)
            + "\n\nDefine the visual system before any shot is built: the look "
            "in one paragraph, a palette of 4-6 colours as hex with what each "
            "is for -- say which is the background, which is for text, which "
            "accent is reserved for the single most important number, which is "
            "muted for grids and secondary marks -- typography, camera "
            "language, how transitions work, 4-6 motion rules an animator can "
            "follow literally, and what to avoid. The palette must survive "
            "YouTube compression and read for colour-blind viewers: never "
            "carry meaning by red/green alone.",
            STYLE_SCHEMA))

    def _section_effects(self, section: dict, style: dict) -> dict:
        claims = self.bundle["claims"]
        heading = section.get("heading", "")
        quoted = [c for c in claims
                  if any(c["claim"][:40] in used for used in section.get("claims_used", []))]
        if not quoted:
            quoted = [c for c in claims if c.get("number")][:4]
        shots = section.get("shots") or []
        shot_lines = "\n".join(
            f"- {s['label']} ({s['seconds']}s): {s['visual'][:160]}"
            + (f" | super: {s['super'][:60]}" if s.get("super") else "")
            for s in shots) or "- (no screenplay yet; leave `shots` empty)"
        prompt = (
            f"{self._idea_block()}\n\n"
            f"Visual system:\n{json.dumps(style, indent=2, ensure_ascii=False)[:1400]}\n\n"
            f"SECTION: {heading} ({section.get('seconds', 0)}s)\n"
            "Beats:\n" + "\n".join(f"- {b}" for b in section.get("beats", []))
            + f"\n\nIts shots, by label:\n{shot_lines}"
            + "\n\nFacts available for this section:\n"
            + ("\n".join(f"- {c.get('number', '')} {c['claim'][:120]}"
                         for c in quoted[:6]) or "- none specific")
            + f"\n\n{MISSION}\n\n{self._direction_block()}\n\n"
            + "\n".join(constraint_lines(self.bundle.get("picture_check") or [],
                                         {str(s.get("label")) for s in shots}))
            + ("The engine's vocabulary, by family -- specify with it; the treatment "
               "decides which families carry this video, and a flat treatment still "
               f"has motion, depth and post to draw on:\n{self.vocabulary()}\n\n"
               if self.vocabulary() else "")
            + "A separate author will build this section as ONE scene-render "
            "symbol: the world -- one set, one light rig, one hero model and one "
            "camera keyed across the whole section -- and the effects you "
            "specify are the phases that world passes through, at the time of "
            "the shots each names. So the brief must say exactly what is on "
            "screen, where, in which treatment (3D with a camera move, a flat "
            "diagram, a photoreal simulation, a composite over footage -- as the "
            "chosen treatment says), with which numbers and in what order, which "
            "spoken words the beats land on, and how the camera arrives from the "
            "previous phase. Only when the section genuinely needs a cut -- a "
            "different place or scale no camera move can bridge -- list in `cuts` "
            "the shot label where the new world starts and why; the default is "
            "none, one continuous take.\n\n"
            f"Specify at most {self.per_section} effects for this section. "
            "Each one: what the viewer must understand that words alone would "
            "not deliver, what is literally on screen, the exact number or "
            "claim it carries, which shot labels it covers (one effect may "
            "cover several consecutive shots; every shot should be covered), "
            "how long it holds in seconds (match the shots), a brief an author "
            "can build from without asking you anything, difficulty 1-5 and "
            "hours to refine the authored version by hand, the technique as "
            "steps, inputs it needs, a cheaper fallback, the risk of "
            "misleading. In `template` name the shared rig when one build "
            "serves several shots; same wording each time."
        )
        answer = self._decide(f"plan-effects-{slugify(heading, 40) or 'section'}",
                                prompt, EFFECTS_SCHEMA)
        effects = answer.get("effects", [])
        known = {s["label"] for s in shots}
        for effect in effects:
            effect["section"] = heading
            effect["id"] = ident(slugify(f"{heading}-{effect['name']}", 48))
            effect["shots"] = [s for s in effect.get("shots", []) if s in known]
        cuts = [c for c in answer.get("cuts", []) or []
                if isinstance(c, dict) and c.get("before") in known]
        return {"section": heading, "seconds": section.get("seconds", 0),
                "effects": effects, "cuts": cuts}

    def effects(self, outline: dict, style: dict) -> list[dict]:
        sections = outline.get("sections", [])

        def work() -> list[dict]:
            self._say(f"  {len(sections)} sections, {self.jobs} at a time")
            with ThreadPoolExecutor(max_workers=self.jobs) as pool:
                futures = [pool.submit(self._section_effects, s, style) for s in sections]
                out = []
                for section, future in zip(sections, futures):
                    try:
                        got = future.result()
                        self._say(f"    {section.get('heading', '')[:52]}: "
                                  f"{len(got['effects'])} effects")
                        out.append(got)
                    except ClaudeError as exc:
                        self._say(f"    {section.get('heading', '')[:52]}: failed ({exc})")
                return out
        return self._stage("11-effects", work)

    # -- the author --------------------------------------------------------

    @staticmethod
    def _worlds(section: dict, group: dict) -> list[dict]:
        """The world symbol(s) of one section, with the plan's cuts applied."""
        cuts = [c.get("before") for c in group.get("cuts", []) or [] if isinstance(c, dict)]
        return world_symbols(section, group.get("effects", []), cuts=cuts)

    def _stream(self, engine, n: int, prompt: str, *, resume: bool = False,
                finish: str | None = None, budget: float | None = None) -> tuple[int, bool]:
        """Run the authoring agent once: (tool calls, whether it timed out).
        A revision resumes the agent's own session, so the brief, the program
        and its earlier choices stay in view. A round cut off at 80% of its
        time or of its dollar cap `budget` is asked for its still (`finish`) by
        the shared round, and is told that cap the way it is told its time."""
        tools_used = 0
        timed_out = False
        try:
            system = harness.with_feedback(AUTHOR_SYSTEM, self.ws.root, "vfx")
            use = bool(resume and getattr(engine, "session_id", None))
            if budget:
                prompt = f"{prompt}\n\n{sdkkit.budget_line(budget)}"
            for event in sdkkit.stream_round(engine, prompt, system=system, resume=use,
                                             finish=finish, budget_usd=budget):
                if event.subtype == "tool_use" and event.tool_input:
                    tools_used += 1
                    what = event.tool_input.get("command") or event.tool_input.get("file_path") or ""
                    self._say(f"    [{n}] {event.tool_name}: {str(what)[:90]}")
        except ClaudeError as exc:
            timed_out = "timed out" in str(exc).lower()
            self._say(f"    [{n}] author failed: {exc}")
        return tools_used, timed_out

    def _review(self, reviewer, n: int, name: str, section: dict,
                worlds: list[dict], round_no: int, previous: dict | None = None) -> dict | None:
        """The model looks at the proof frames of one document and scores
        them against the publication bar; None when no frame was rendered.
        A revision's reviewer is also handed `previous`, the round the section
        stands on (its score, findings and kept frames), and must say whether
        this round is better, same or worse, and what became of each finding."""
        stem = name[:-len(".scene.xml")]
        frames_dir = self.ws.dir / "frames"
        pngs = sorted(frames_dir.glob(f"{stem}__*.png")) if frames_dir.is_dir() else []
        if not pngs:
            return None
        by_world = {w["id"]: w for w in worlds}
        evidence = []
        for png in pngs:
            parts = png.stem.split("__")
            sid = parts[1] if len(parts) > 2 else ""
            try:
                t = float(parts[-1].rstrip("s"))
            except ValueError:
                t = 0.0
            shot = next((s for s in by_world.get(sid, {}).get("shots", [])
                         if s["at"] <= t < s["at"] + s["seconds"]), None)
            evidence.append({"frame": f"frames/{png.name}", "symbol": sid, "at": t,
                             "shot": shot["label"] if shot else "",
                             "should_show": shot.get("visual", "") if shot else
                             "(between shots: the world's own start or end)"})
        prompt = "\n\n".join([
            f"Review round {round_no} of section {n} (\"{section.get('heading', '')}\") of "
            f"\"{self.idea.title}\". Read every PNG listed under `frames/` -- each is one "
            "proof frame of the section's world, rendered as `yt build` will include it, "
            "at the midpoint of the shot named -- and judge each against the shot it "
            "belongs to, then the frames together for continuity: the same hero at the "
            "same size, colour and material; the same room and light; one camera on one "
            "path. Then give a provisional score 0-10 for the section as it would read in "
            "the finished film, by the publication bar.",
            harness.brief("Frames to Read, and what each should show", evidence),
            harness.brief("The section's shots (label, offset, length, picture)",
                          [{"label": s["label"], "at": s["at"], "seconds": s["seconds"],
                            "visual": s.get("visual", ""), "super": s.get("super", ""),
                            "direction_for_super": s.get("on_screen", "")}
                           for w in worlds for s in w["shots"]]),
            harness.brief("The phases the world must deliver",
                          [{"id": e["id"], "name": e.get("name"), "shots": e.get("shots"),
                            "conveys": e.get("conveys"), "on_screen": e.get("on_screen")}
                           for w in worlds for e in w["phases"]]),
            "The editorial treatment:\n" + self._direction_block(),
            "\n".join(constraint_lines(self.bundle.get("picture_check") or [],
                                       {str(s["label"]) for w in worlds for s in w["shots"]}))
            or "No fact-check constraint binds these shots.",
            "Every finding must carry a fix the author can apply in the document: which "
            "camera key, light, material, animation or geometry to change, and to what.",
        ])
        look = approved_look(self.ws.root)
        if look and look.get("source") == "section":
            prompt += "\n\n" + "\n\n".join([
                harness.brief(f"The approved look (the operator approved section "
                              f"{look.get('section')} of this film as its look)", {
                                  "frame": str(self.ws.root / look["frame"]) if look.get("frame") else "",
                                  "frames": str(self.ws.root / look["sheet"]) if look.get("sheet") else "",
                                  "program": str(self.ws.root / look["look_program"]) if look.get("look_program") else "",
                                  "document": str(self.ws.root / look["document"])}),
                "Read the approved frames first. Score `fidelity` 0-10: how faithfully the "
                "proof frames read as the same film -- its palette, type, construction of "
                "labels and formulas, materials, lights and camera language. A section that "
                "departs from the approved look is wrong, whatever else it does well: name "
                "every departure as a finding with the fix being to copy the approved "
                "section's construct, and do not score the section above its fidelity."])
        elif look:
            prompt += "\n\n" + "\n\n".join([
                harness.brief("The approved look (the operator approved this keyframe as the "
                              "film's look)", {
                                  "frame": str(self.ws.root / look["frame"]),
                                  "document": str(self.ws.root / look["document"]),
                                  "elements": look.get("elements", [])}),
                "Read the approved frame first. Score `fidelity` 0-10: how faithfully the "
                "proof frames inherit its materials, lights, hero model and camera "
                "language. A world that departs from the approved look is wrong, whatever "
                "else it does well: name every departure as a finding with the fix being "
                "to copy the keyframe's construct, and do not score the section above its "
                "fidelity."])
        if previous is not None:
            kept = self.ws.dir / self.ws.ROUNDS_DIR / stem / f"r{previous['round']:02d}" / "frames"
            prompt += "\n\n" + "\n\n".join([
                harness.brief(PREVIOUS_ROUND, {
                    "round": previous["round"], "score": previous.get("score"),
                    "summary": (previous.get("review") or {}).get("summary", ""),
                    "findings": [{"frame": fr.get("frame", ""), "shot": fr.get("shot", ""),
                                  "problem": f.get("problem", ""), "fix": f.get("fix", ""),
                                  "severity": f.get("severity", "")}
                                 for fr in (previous.get("review") or {}).get("frames", [])
                                 for f in fr.get("findings", [])],
                    "frames": [f"{self.ws.ROUNDS_DIR}/{stem}/r{previous['round']:02d}/frames/"
                               f"{png.name}" for png in sorted(kept.glob("*.png"))]}),
                "Read the previous round's frames too, then fill `previous`: `verdict` is "
                "whether this round reads better, the same or worse than that one as a "
                "section of the finished film -- `better` only for a visible gain, a tie "
                "is `same` -- and `findings` says of each of its findings whether it is "
                "resolved, persists or regressed. Score this round on its own, as before."])
        try:
            return reviewer.inspect(f"review-{self.ws.version or 'v00'}-{n:02d}-r{round_no}",
                                    prompt, review_schema(delta=previous is not None),
                                    cwd=self.ws.dir)
        except harness.HarnessError as exc:
            self._say(f"    [{n}] review failed: {exc}")
            return None

    #: Findings about these are the ones the author keeps answering with
    #: primitives and flat colour; they go back first, as requirements.
    SURFACE_WORDS = re.compile(r"material|translucen|transmission|subsurface|shadow|texture|"
                               r"grain|lighting|light rig|flat.shaded|environment", re.I)

    @classmethod
    def _review_listing(cls, review: dict | None) -> str:
        """The review's findings as requirements for the next round, verbatim
        (problem and fix): what persists or regressed from the round before
        leads, then this round's, the material / translucency / shadow ones
        first."""
        if not review:
            return ""
        lines = [f"The reviewer scored the frames {review.get('score', '?')}/10: "
                 f"{review.get('summary', '')}"]
        verdict = verdict_of(review)
        if verdict:
            lines.append(f"Against the round before, the reviewer judged this one {verdict}.")
        if review.get("continuity"):
            lines.append(f"Continuity: {review['continuity']}")
        rows = []
        for f in ((review.get("previous") or {}).get("findings") or []):
            status = f.get("status", "")
            if status in ("persists", "regressed"):
                rows.append((-1, f"- [{status}] from the previous round: {f.get('problem', '')}"
                                 "\n  REQUIRED FIX: answer it in the document this time"))
        for fr in review.get("frames", []):
            for f in fr.get("findings", []):
                text = f"{f.get('category', '')} {f.get('problem', '')} {f.get('fix', '')}"
                rows.append((0 if cls.SURFACE_WORDS.search(text) else 1,
                             f"- [{f.get('severity', '')}] {fr.get('frame', '')} (shot "
                             f"{fr.get('shot', '?')}, {f.get('category', '')}): "
                             f"{f.get('problem', '')}\n  REQUIRED FIX: {f.get('fix', '')}"))
        rows.sort(key=lambda r: r[0])
        lines += ["", "Requirements for this round (verbatim from the review; each must be "
                  "answered in the document, material and light first, and the answer "
                  "must be visible in the proof frames):"]
        lines += [r[1] for r in rows]
        return "\n".join(lines)

    def _author_one(self, n: int, section: dict, group: dict,
                    style: dict, tokens: dict[str, str]) -> dict:
        """One agent, one section, one document: the world, its proof frames,
        the review, and the revision rounds the bar asks for."""
        effects = group.get("effects", [])
        worlds = self._worlds(section, group)
        shots = {w["id"]: w["shots"] for w in worlds}
        brief = self.ws.brief(n, idea=self.idea, house_rules=self.house, style=style,
                              tokens=tokens, frame=self.frame, section=section,
                              effects=effects, words=self.words,
                              assets=self.bundle.get("assets", []),
                              direction_block=self._direction_block(), worlds=worlds,
                              constraints=self.bundle.get("picture_check") or [],
                              look=self.lead is None)
        if self.look_notes and n == self.lead:
            # A rejected look comes back with the operator's words; they lead.
            brief.write_text("## The operator's notes on the last look\n\nThe operator "
                             "rejected the previous version of this section as the film's "
                             "look. These notes outrank everything else in this brief:\n\n"
                             + "\n".join(f"- {note}" for note in self.look_notes) + "\n\n"
                             + brief.read_text(encoding="utf-8"), encoding="utf-8")
        name = self.ws.doc_name(n, section)
        program = self.ws.program_name(name)
        engine = harness.ENGINE(EngineConfig(
            **sdkkit.author_config(self.ws.dir, project_root=self.ws.root, programs=[program]),
            model=self.model, timeout=AUTHOR_TIMEOUT))     # each session's cap is granted below
        finish = sdkkit.finish_prompt(program, name, f"python3 sr.py frame {name}")
        reviewer = harness.Harness(self.ws.root, "vfx", model=self.reviewer_model,
                                   role=REVIEW_ROLE, house=self.house,
                                   house_digest=self.house_digest, fresh=self.fresh,
                                   verbose=self.verbose, timeout=1800.0,
                                   budget_usd=self.review_budget_usd)
        self.spend.track(n, author=engine.usage, review=reviewer.usage)

        def reserve() -> float:
            """What the review after a session may cost: its cap."""
            return self.review_budget_usd

        def grant() -> float:
            """The cap of the next session, with the review that follows it kept
            back from the section's and the stage's budgets; 0 when there is none
            worth starting."""
            cap = self.spend.grant(n, AUTHOR_SESSION_USD, reserve())
            if cap < MIN_SESSION_USD:
                self.spend.release(n)
                return 0.0
            return cap
        ids = ", ".join(w["id"] for w in worlds)
        prompt = (f"Read {brief.name}, then kit/SDK-GUIDE.md and kit/examples/, and write "
                  f"{program}: the program that builds {name} as the brief says, the world "
                  f"symbol ({ids}) with every shot as a phase, and saves it. Build it with "
                  f"`python3 kit.py build {program}` and fix every error; then `python3 sr.py "
                  f"frame {name}` and Read every frame it writes until each shows its shot. "
                  "When the build reports 0 error(s) and the frames show the shots, reply "
                  "with one line: the program, the document it saves and the symbol ids.")
        cap = grant()
        if not cap:
            raise RuntimeError(
                f"no budget for an author session: section ${self.spend.section_left(n):.2f} "
                f"of ${self.spend.section_usd:g}, stage ${self.spend.stage_left():.2f} of "
                f"${self.spend.stage_usd:g}, with ${reserve():.2f} kept for its review")
        try:
            tools_used, _ = self._stream(engine, n, prompt, finish=finish, budget=cap)
        finally:
            self.spend.release(n)
        rounds: list[dict] = []

        def snapshot(finished: bool, symbols: list[str], findings: list[str], revisions: int,
                     chosen: dict | None, best_round_no: int | None) -> dict:
            """The section's record as of the rounds gated so far: partial after
            each gate, whole when the section ends. `chosen` is the round the
            score and review are read from."""
            final = self.ws.dir / name
            text = final.read_text(encoding="utf-8", errors="replace") if final.is_file() else ""
            return {"n": n, "section": section.get("heading", ""),
                    "file": f"{self.ws.rel}/{name}", "version": self.ws.version,
                    "program": f"{self.ws.rel}/{program}" if (self.ws.dir / program).is_file() else "",
                    "provenance": sdkkit.provenance_of(final) if final.is_file() else None,
                    "symbols": symbols, "findings": findings, "tool_calls": tools_used,
                    "props_declined": props_declined(text),
                    "worlds": [{"id": w["id"], "start": w["start"], "seconds": w["seconds"],
                                "shots": [s["label"] for s in w["shots"]],
                                "phases": [e["id"] for e in w["phases"]]} for w in worlds],
                    "revisions": revisions, "rounds": rounds, "best_round": best_round_no,
                    "score": chosen["score"] if chosen else None,
                    "review": chosen["review"] if chosen else None,
                    "cost_usd": round(engine.usage.cost_usd + reviewer.usage.cost_usd, 4),
                    "budget": self.spend.row(n), "stopped": self.spend.stopped.get(n),
                    "reviewer": {"model": self.reviewer_model,
                                 "budget_usd": self.review_budget_usd},
                    "timed_out_usage": timed_out_usage(engine.usage),
                    "engine": reviewer.report(), "finished": finished,
                    **self._provenance(n, findings)}

        def gate(round_no: int) -> tuple[list[str], list[str], dict | None]:
            # The harness builds the program itself: the document judged is the
            # program's output, never the agent's word for it.
            built = sdkkit.build_findings(self.ws.dir / program, name)
            # The reviewer reads frames/<stem>__*.png: only this round's proof
            # frames may be there, never one of an earlier document.
            stem = name[:-len(".scene.xml")]
            for stale in (self.ws.dir / "frames").glob(f"{stem}__*.png"):
                stale.unlink(missing_ok=True)
            if any(f.startswith("error") for f in built):
                symbols, findings = [], built
            else:
                symbols, findings = self.ws.validate(name, shots=shots, props=True)
                findings = built + findings
            errors = [f for f in findings if f.startswith("error")]
            # A frame the host lost is the host's failure, not the author's:
            # when it is all that blocks the round, wait a bounded time and
            # gate again without a session. What the first pass lost is
            # recorded on the round either way (vfx.json shows the host's part).
            lost = [f for f in findings if f.startswith("infra")]
            if lost and not errors:
                self._say(f"    [{n}] {name} round {round_no}: the host lost {len(lost)} "
                          f"frame(s); gating again in {INFRA_WAIT:g} s, no session")
                time.sleep(INFRA_WAIT)
                symbols, findings = self.ws.validate(name, shots=shots, props=True)
                findings = built + findings
                errors = [f for f in findings if f.startswith("error")]
            still_lost = [f for f in findings if f.startswith("infra")]
            paid = reviewer.usage.cost_usd
            # A revision is judged against the round the section stands on.
            review = (None if errors or still_lost or not self.review
                      else self._review(reviewer, n, name, section, worlds, round_no,
                                        previous=best_round(rounds)))
            score = float(review["score"]) if review and review.get("score") is not None else None
            rounds.append({"round": round_no, "symbols": symbols, "errors": len(errors),
                           "score": score, "fidelity": (review or {}).get("fidelity"),
                           "review": review, "timed_out": False, "infra": lost,
                           "review_usd": round(reviewer.usage.cost_usd - paid, 4),
                           "findings": [f for f in findings if not f.startswith("note")]})
            self.ws.keep_round(name, round_no)
            # What a kill from here on keeps: the rounds so far, standing on
            # the one the verdicts promoted, for --recover to put back.
            standing = best_round(rounds) or rounds[-1]
            self.ws.write_record(name, snapshot(False, symbols, findings, round_no, standing,
                                                standing["round"]))
            self._say(f"    [{n}] {name} round {round_no}: {len(symbols)} symbol(s), "
                      f"{len(errors)} error(s)"
                      + (f", {len(still_lost)} frame(s) lost to the host" if still_lost else "")
                      + (f", review {score:g}/10" if score is not None else ", no review")
                      + f", {tools_used} tool call(s)")
            return symbols, findings, review

        symbols, findings, review = gate(0)
        revisions = 0
        while revisions < self.revisions:
            errors = [f for f in findings if f.startswith("error")]
            score = rounds[-1]["score"]
            short = score is not None and score < self.author_bar
            if not errors and not short:
                break
            # The operator asked the run to finish (`yt vfx --finish-now`, or
            # SIGUSR1): no further revision; the section ends on its best round.
            if self.finish_requested():
                self.spend.stopped[n] = "finish"
                self._say(f"    [{n}] finish asked: no revision {revisions + 1}; the best "
                          "reviewed round is kept")
                break
            # A revision its reviewer judged the same or worse than the round
            # it answered: the listing was answered and nothing was gained, so
            # no further revision is paid for; the section ends on its incumbent.
            verdict = verdict_of(review) if not errors else None
            if verdict in ("same", "worse"):
                self.spend.stopped[n] = verdict
                self._say(f"    [{n}] revision {revisions} was judged {verdict} than the round "
                          "before it; no further revision, the best reviewed round is kept")
                break
            # The section's and the stage's budgets decide whether another session
            # (and the review after it) can be paid; if not, the section ends here,
            # on its best reviewed round.
            cap = grant()
            if not cap:
                self.spend.stopped[n] = "budget"
                self._say(f"    [{n}] budget reached (${self.spend.spent(n):.2f} of "
                          f"${self.spend.section_usd:g}; stage ${self.spend.stage_spent():.2f} of "
                          f"${self.spend.stage_usd:g}): no revision {revisions + 1}; "
                          "the best reviewed round is kept")
                break
            # The gate or the reviewer found what the author did not look at;
            # send it back with the findings, once per allowed round.
            revisions += 1
            why = errors[0][:100] if errors else f"review {score:g}/10 under the bar {self.author_bar:g}"
            self._say(f"    [{n}] revision {revisions}: {why}")
            # The host's failures are not the author's to answer.
            listing = "\n".join(f"- {f}" for f in findings if not f.startswith(("note", "infra")))
            prompt = (f"{name} is not done."
                      + (f" The gate reports:\n{listing}\n\n" if listing else "\n\n")
                      + (self._review_listing(review) + "\n\n" if review else "")
                      + f"Re-read {brief.name}, then fix the program {program} -- the same "
                      "world, one camera, the hero kept -- run `python3 kit.py build "
                      f"{program}` until it reports 0 error(s), then `python3 sr.py frame "
                      f"{name}` and Read every frame it writes; iterate until every frame "
                      "shows its shot and the findings above are answered. Reply with one "
                      "line.")
            doc_path = self.ws.dir / name
            prog_path = self.ws.dir / program
            prov_path = doc_path.with_suffix("").with_suffix(".provenance.json")
            before = {p: (p.read_bytes() if p.is_file() else None)
                      for p in (doc_path, prog_path, prov_path)}
            try:
                used, timed_out = self._stream(engine, n, prompt, resume=True, finish=finish,
                                               budget=cap)
            finally:
                self.spend.release(n)
            tools_used += used
            if timed_out:
                # The agent was cut off mid-edit: keep the program and the
                # document the last gate judged, record the round as timed
                # out, and stop; a half-edited one is not re-gated silently.
                for path, data in before.items():
                    if data is None:
                        path.unlink(missing_ok=True)     # made by the cut-off revision
                    else:
                        path.write_bytes(data)
                prev = rounds[-1]
                rounds.append(dict(prev, round=revisions, timed_out=True))
                self._say(f"    [{n}] revision {revisions} timed out after {AUTHOR_TIMEOUT:g}s; "
                          "the previous document is kept")
                break
            symbols, findings, review = gate(revisions)
        # Keep only a judged improvement: the section ends on the round the
        # verdicts promoted, so a last revision that crashed, broke the build
        # or was judged no better falls back to it instead of to nothing.
        best = best_round(rounds)
        last = next((r for r in reversed(rounds) if not r.get("timed_out")), None)
        promoted = None
        if best is not None and last is not None and best["round"] != last["round"]:
            if self.ws.restore_round(name, best["round"]):
                promoted = best["round"]
                symbols, review = best["symbols"], best["review"]
                findings = list(best["findings"])
                self._say(f"    [{n}] round {best['round']} ({best['score']:g}/10) was the "
                          f"best; its program and document are kept")
        self._account(engine.usage, reviewer.usage)
        chosen = best if promoted is not None else (rounds[-1] if rounds else None)
        record = snapshot(True, symbols, findings, revisions, chosen,
                          promoted if promoted is not None else (last or {}).get("round"))
        self.ws.write_record(name, record)
        return record

    def author(self, outline: dict, effects: list[dict], style: dict,
               tokens: dict[str, str]) -> list[dict]:
        """One agent per section, in a new vfx/vNN/; the previous version stays.

        In the look pass (`lead`) only that section is authored, once, and the
        record is kept as "12-lead"; otherwise a section the operator approved
        as the look is carried forward like one `--sections` leaves alone."""
        sections = outline.get("sections", [])
        by_heading = {s["section"]: s for s in effects}
        lead_mode = self.lead is not None
        look = None if lead_mode else approved_look(self.ws.root)
        self._look_digest = str((look or {}).get("digest") or "")

        # The approved look's section enters the film in two places only: the
        # first animation after the look (nothing authored yet), and --relook.
        # After that the active version is the film: --sections and the
        # competition below treat that section like any other. A --fresh run
        # replans, so the look pass (made for the old plan) is not carried;
        # keyframe/look.py still leads every brief.
        approved = None if lead_mode or self.fresh else look_section(self.ws.root)
        if approved is not None and approved.get("frame_size") \
                and tuple(approved["frame_size"]) != tuple(self.frame):
            self._say(f"  the approved look was authored at {tuple(approved['frame_size'])}, "
                      f"this run frames {tuple(self.frame)}; it is not carried")
            approved = None
        if self.relook and approved is None:
            raise ValueError("--relook needs an approved look from the film (yt keyframe "
                             "--approve) at this frame size, and no --fresh")
        self._look_n = int(approved["n"]) if approved is not None else None

        previous = {}
        if self.sections_only or self.relook:
            for rec in self.dossier.load("12-author") or []:
                if isinstance(rec, dict) and rec.get("n") is not None:
                    previous[int(rec["n"])] = rec
        if self.relook:
            planned = {n for n, s in enumerate(sections, 1)
                       if by_heading.get(s.get("heading", ""), {}).get("effects")}
            self.sections_only = planned - {self._look_n}
            previous[self._look_n] = approved
            carry = {self._look_n}
        else:
            carry = {n for n in previous if n not in self.sections_only}
            if approved is not None and not self.sections_only:
                previous[self._look_n] = approved
                carry.add(self._look_n)

        def work() -> list[dict]:
            version = self.ws.new_version()
            self.ws.prepare(tokens=tokens, frame=self.frame)
            jobs = [(n, s, by_heading.get(s.get("heading", ""), {}))
                    for n, s in enumerate(sections, 1)]
            jobs = [j for j in jobs if j[2].get("effects")]
            if lead_mode:
                pick = [j for j in jobs if j[0] == self.lead] or jobs[:1]
                if pick and pick[0][0] != self.lead:
                    self._say(f"  section {self.lead} has no planned effects; the look comes "
                              f"from section {pick[0][0]}")
                    self.lead = pick[0][0]
                jobs = pick
            shots: dict[str, list[dict]] = {}
            for _, s, group in jobs:
                for w in self._worlds(s, group):
                    shots[w["id"]] = w["shots"]
            self.ws.write_shots(shots)
            carried: list[dict] = []
            if carry:
                for n, s, group in list(jobs):
                    if n not in carry:
                        continue
                    record = self.ws.carry_section(previous[n], n)
                    if record is not None:
                        carried.append(record)
                        self.ws.write_record(self.ws.doc_name(n, s), record)
                        jobs.remove((n, s, group))
                self._say(f"  carrying section(s) {', '.join(str(r['n']) for r in carried) or 'none'} "
                          f"forward from {', '.join(sorted({str(r.get('carried_from')) for r in carried})) or '-'}"
                          + (f" (section {approved['n']} is the approved look)"
                             if approved is not None and any(r["n"] == approved["n"] for r in carried)
                             else ""))
            stage_usd = (self.stage_budget_usd if self.stage_budget_usd is not None
                         else self.budget_usd * len(jobs))
            self.spend = SpendBudget(self.budget_usd, stage_usd)
            self._say("  " + estimate_line(estimate_spend(
                len(jobs), self.revisions, self.budget_usd, stage_usd,
                review_usd=self.review_budget_usd)))
            self._say(f"  authoring {len(jobs)} section(s) into {self.ws.rel}/ ({version}), "
                      f"{self.jobs} at a time, up to ${self.budget_usd:g} each "
                      f"(sessions and reviews), ${stage_usd:g} for the stage, "
                      f"bar {self.author_bar:g}/10, {self.revisions} revision(s), reviewed by "
                      f"{self.reviewer_model} at up to ${self.review_budget_usd:g} a review")
            watched = self._watch_finish()
            try:
                with ThreadPoolExecutor(max_workers=self.jobs) as pool:
                    futures = [(job, pool.submit(self._author_one, job[0], job[1], job[2], style,
                                                 tokens)) for job in jobs]
                    authored = []
                    for (n, s, group), future in futures:
                        try:
                            result = future.result()
                        except Exception as exc:     # one section's failure is that section's record
                            self._say(f"    [{n}] the section's authoring failed: {exc}")
                            result = self._failed_record(n, s, group, exc)
                            self.ws.write_record(self.ws.doc_name(n, s), result)
                        # The record is the one the section wrote as it finished:
                        # what a kill before this point would have kept.
                        authored.append(self.ws.read_record(self.ws.doc_name(n, s)) or result)
            finally:
                self._unwatch_finish(watched)
            for n, row in self.spend.report()["sections"].items():
                self._say(f"  spent: section {n} ${row['spent_usd']:.2f} of ${row['budget_usd']:g} "
                          f"(author ${row['author_usd']:.2f}, reviews ${row['review_usd']:.2f}"
                          + (f", ${row['timed_out_usd']:.2f} of it in cut-off calls"
                             if row["timed_out_usd"] else "") + ")"
                          + (f"; stopped: {row['stopped']}" if row["stopped"] else ""))
            stage = self.spend.report()["stage"]
            self._say(f"  spent: stage ${stage['spent_usd']:.2f} of ${stage['budget_usd']:g}")
            # A section re-authored on request competes with the version it
            # replaces: the new one is kept only if it is at least as good.
            for i, record in enumerate(authored):
                before = previous.get(record["n"]) if self.sections_only else None
                if before is None:
                    continue
                broken = has_errors(record)
                if self.relook:
                    # judged against another look: only a broken attempt loses
                    keep = broken and not has_errors(before)
                elif before.get("look"):
                    keep = broken           # the look has no score; a sound attempt wins
                else:
                    keep = keeps_previous(before, record)
                if keep:
                    kept = self.ws.keep_previous(before, record, record["n"])
                    if kept is not None:
                        self._say(f"    [{record['n']}] the new attempt "
                                  f"({record.get('score')}) is worse than {before.get('version')}'s "
                                  f"({before.get('score')}); that version is kept")
                        authored[i] = kept
                        self.ws.write_record(Path(kept["file"]).name, kept)
            return sorted(carried + authored, key=lambda r: r["n"])
        return self._stage("12-lead" if lead_mode else "12-author", work)

    #: What `_watch_finish` returns when no handler was installed.
    _UNWATCHED = object()

    def _watch_finish(self):
        """Listen for SIGUSR1, which asks the run to finish as vfx/vNN/FINISH
        does. Only the main thread may listen; elsewhere the file alone is read."""
        if not hasattr(signal, "SIGUSR1"):
            return self._UNWATCHED
        try:
            return signal.signal(signal.SIGUSR1, lambda *_: self.finish.set())
        except ValueError:
            return self._UNWATCHED

    def _unwatch_finish(self, previous) -> None:
        if previous is not self._UNWATCHED:
            signal.signal(signal.SIGUSR1, previous)

    def _failed_record(self, n: int, section: dict, group: dict, exc: Exception) -> dict:
        """What is recorded for a section whose authoring raised: nothing it
        wrote is trusted, and the build and the doctor see why."""
        name = self.ws.doc_name(n, section)
        worlds = self._worlds(section, group)
        return {"n": n, "section": section.get("heading", ""),
                "file": f"{self.ws.rel}/{name}", "version": self.ws.version, "program": "",
                "provenance": None, "symbols": [], "tool_calls": 0, "props_declined": {},
                "findings": [f"error: the authoring of section {n} failed: "
                             f"{type(exc).__name__}: {str(exc)[:300]}"],
                "worlds": [{"id": w["id"], "start": w["start"], "seconds": w["seconds"],
                            "shots": [s["label"] for s in w["shots"]],
                            "phases": [e["id"] for e in w["phases"]]} for w in worlds],
                "revisions": 0, "rounds": [], "best_round": None, "score": None,
                "review": None, "cost_usd": 0.0, **self._provenance(n, ["error"])}

    def _provenance(self, n: int, findings: list) -> dict:
        """What a section record says about the look: made as the look (the
        look pass), the look it was authored against, and -- when it is a
        sound authoring of the look's own section the operator asked for --
        that it now stands for that look in the film."""
        out = {"look_digest": self._look_digest}
        if self.lead is not None and n == self.lead:
            out["look"] = True
        if (self._look_n == n and n in self.sections_only and self._look_digest
                and not any(str(f).startswith("error") for f in findings or [])):
            out["reauthored_look"] = self._look_digest
        return out

    def continuity(self, authored: list[dict]) -> dict:
        """One proof frame from each section, side by side: do they read as
        one film? Each section was reviewed alone, so a hero that changes
        size or a grade that shifts at every boundary passes every section
        review; this is the review that sees it."""
        def work() -> dict:
            frames: list[dict] = []
            cwd: Path | None = None
            for doc in authored:
                path = self.ws.root / doc["file"]
                stem = path.name[:-len(".scene.xml")]
                pngs = sorted((path.parent / "frames").glob(f"{stem}__*.png"))
                if not pngs:
                    continue
                cwd = cwd or path.parent
                if path.parent != cwd:
                    continue
                png = pngs[len(pngs) // 2]
                frames.append({"section": doc.get("section", ""), "document": path.name,
                               "frame": f"frames/{png.name}"})
            if len(frames) < 2:
                return {"skipped": "fewer than two sections have proof frames",
                        "frames": frames, "breaks": []}
            reviewer = harness.Harness(self.ws.root, "vfx", model=self.model,
                                       role=CONTINUITY_ROLE, house=self.house,
                                       house_digest=self.house_digest, fresh=self.fresh,
                                       verbose=self.verbose, timeout=1800.0)
            prompt = "\n\n".join([
                f"Read every PNG listed under `frames/` -- one proof frame from each "
                f"section of \"{self.idea.title}\", in film order -- and judge them "
                "together: do they read as one film? Then score 0-10 how well the "
                "sections hold together as one film, by the publication bar.",
                harness.brief("Frames to Read, in film order", frames),
                "The editorial treatment:\n" + self._direction_block(),
                "A break names the sections it lies between, the one section whose "
                "document should change, and the fix in that document's terms.",
            ])
            try:
                result = reviewer.inspect(f"continuity-{self.ws.version or 'v00'}", prompt,
                                          CONTINUITY_SCHEMA, cwd=cwd)
            except harness.HarnessError as exc:
                self._say(f"  continuity review failed: {exc}")
                return {"error": str(exc)[:300], "frames": frames, "breaks": []}
            finally:
                self._account(reviewer.usage)
            breaks = result.get("breaks") or []
            self._say(f"  continuity: {result.get('score')}/10, "
                      f"{'one film' if result.get('one_film') else 'not one film'}, "
                      f"{len(breaks)} break(s)")
            return dict(result, frames=frames,
                        cost_usd=round(reviewer.usage.cost_usd, 4))
        return self._stage("12-continuity", work)

    def scene_map(self, effects: list[dict], authored: list[dict]) -> dict:
        """Shot label -> {file, symbol}, from what was actually written: every
        shot of a section goes to its world symbol, and each effect is a
        phase of it (`phases`: effect id -> symbol id). A document authored
        the old way, one symbol per effect, still maps by effect id."""
        by_label: dict[str, dict] = {}
        symbols: dict[str, dict] = {}
        phases: dict[str, str] = {}
        dropped: dict[str, str] = {}
        docs = {d["section"]: d for d in authored}
        for group in effects:
            doc = docs.get(group["section"])
            if doc is not None:
                errors = [f for f in doc["findings"] if f.startswith("error")]
                present = set(doc["symbols"])
                for w in doc.get("worlds") or []:
                    if w["id"] in present and not errors:
                        symbols[w["id"]] = {"file": doc["file"], "shots": list(w["shots"]),
                                            "name": w["id"], "phases": list(w.get("phases", []))}
                        for label in w["shots"]:
                            by_label.setdefault(label, {"file": doc["file"], "symbol": w["id"]})
            for effect in group["effects"]:
                eid = effect["id"]
                if doc is None:
                    dropped[eid] = "no document was authored for its section"
                    continue
                world = next((w for w in doc.get("worlds") or []
                              if eid in w.get("phases", []) and w["id"] in symbols), None)
                if world is not None:
                    phases[eid] = world["id"]
                    continue
                if eid not in present:
                    dropped[eid] = ("its world symbol has engine errors: " + errors[0][:140]
                                    if errors and doc.get("worlds")
                                    else "the author wrote no symbol with this id")
                    continue
                if errors:
                    dropped[eid] = "its document has engine errors: " + errors[0][:140]
                    continue
                symbols[eid] = {"file": doc["file"], "shots": effect.get("shots", []),
                                "name": effect.get("name", "")}
                for label in effect.get("shots", []):
                    by_label.setdefault(label, {"file": doc["file"], "symbol": eid})
        return {"stack": STACK, "files": [d["file"] for d in authored],
                "symbols": symbols, "phases": phases, "by_label": by_label,
                "dropped": dropped,
                "findings": {d["file"]: d["findings"] for d in authored},
                "cost_usd": round(sum(d.get("cost_usd", 0) for d in authored), 2)}

    def reach(self, effects: list[dict], scene: dict) -> dict:
        """How far the authored documents reach into the engine, and whether
        each effect uses the strongest construct for what it must convey.

        The usage and the family coverage are computed; the verdicts are the
        model's. With nothing authored there is nothing to judge, and no call.
        """
        authored = [e for s in effects for e in s["effects"] if authored_as(scene, e["id"])]
        files = sorted({authored_as(scene, e["id"])["file"] for e in authored})

        def work() -> dict:
            if not authored:
                return {"usage": None, "report": None, "audit": None,
                        "reason": "nothing was authored, so there is nothing to audit"}
            texts = {}
            for file in files:
                path = self.ws.root / file
                try:
                    texts[file] = path.read_text(encoding="utf-8")
                except OSError as exc:
                    texts[file] = f"<unreadable error='{exc}'/>"
            usage = reach.usage_of(texts)
            # A phase is judged by the constructs of the world it lives in.
            for eid, wid in (scene.get("phases") or {}).items():
                usage["symbols"].setdefault(eid, usage["symbols"].get(wid, {}))
            names = sdk().element_names()
            rep = reach.report(usage, names, reach.manifest())
            audit = self._decide("plan-reach", reach.prompt(
                idea_block=self._idea_block(), direction_block=self._direction_block(),
                mission=MISSION, effects=authored, usage=usage, rep=rep,
                element_names=names), reach.REACH_SCHEMA)
            return {"usage": usage, "report": rep, "audit": audit, "reason": ""}
        return self._stage("14-reach", work)

    def plan(self, effects: list[dict], totals: dict, scene: dict,
             reach_data: dict | None = None) -> dict:
        flat = [e for s in effects for e in s["effects"]]
        audit = (reach_data or {}).get("audit") or {}
        upgrades = {e["id"]: e for e in audit.get("effects", [])
                    if e.get("verdict") == "upgrade"}
        listing = "\n".join(
            f"- [{e['type']}, d{e['difficulty']}, {e['hours']}h, "
            f"{'authored' if authored_as(scene, e['id']) else 'NOT authored'}"
            + (f", reach: upgrade to {', '.join(upgrades[e['id']].get('upgrade_to', []))} "
               f"(gain {upgrades[e['id']].get('gain', 0)}/5)" if e['id'] in upgrades else "")
            + f"] {e['name']} ({e['section']}; shots {', '.join(e.get('shots', [])) or '-'})"
            for e in flat)
        budget = (f"The refinement budget is {self.budget_hours} hours; the plan "
                  f"currently needs {totals['hours']:.0f}. "
                  if self.budget_hours else "")
        reach_line = ""
        if audit:
            reach_line = (f"The reach audit scored these documents "
                          f"{audit.get('reach', {}).get('score', '?')}/10 against what the "
                          f"engine offers: {audit.get('reach', {}).get('summary', '')} "
                          "An effect marked `upgrade` has a stronger construct named; "
                          "treat the upgrade as refinement work, ordered by its gain. ")
        return self._stage("15-plan", lambda: self._decide(
            "plan-refine",
            f"{self._idea_block()}\n\nEvery specified visual, and whether the "
            f"author produced a valid scene-render symbol for it:\n{listing}\n\n"
            f"Totals: {totals['hours']:.0f} hours over {totals['count']} "
            f"effects, {totals['seconds']:.0f}s of animated screen time.\n\n"
            f"{budget}{reach_line}Now plan the refinement. The authored symbols render "
            "today; the hours are what it takes to make them good, and an "
            "effect not authored has to be either authored by hand or cut. "
            "Give the order to work in and why, the templates worth building "
            "once and reusing with the hours each saves, which effects to cut "
            "first and what replaces them, anything worth outsourcing, and a "
            "one-sentence verdict on whether this is finishable as specified.",
            PLAN_SCHEMA))

    # -- run ---------------------------------------------------------------

    def run_lead(self) -> dict:
        """The look pass: plan every section (cached for `yt vfx`), author the
        lead one once and return its record; nothing film-wide is computed."""
        if self.lead is None:
            raise ValueError("run_lead needs a lead section")
        if self.fresh:
            # A new plan: what was authored under the old one no longer fits it.
            for name in ("12-author", "12-continuity", "13-scene", "14-reach", "15-plan"):
                self.dossier.drop(name)
        outline = self.outline()
        style = self.style()
        tokens = tokens_from(style)
        effects = self.effects(outline, style)
        authored = self.author(outline, effects, style, tokens)
        record = next((r for r in authored if r.get("n") == self.lead), None)
        return {"record": record, "style": style, "tokens": tokens,
                "version": (record or {}).get("version") or self.ws.version,
                "usage": asdict(self.usage), "engine": self.harness.report()}

    def recovered(self, outline: dict, effects: list[dict]) -> list[dict]:
        """The authoring record of the last version, rebuilt from what its run
        left on disk: each section's kept rounds (vfx/vNN/rounds/<stem>/rNN)
        and the reviews the harness cached for them. A section goes back on its
        best reviewed round; one with no reviewed round goes back on its last
        and is recorded with an error, so the scene leaves it out and
        `--sections N` authors it again. No model is asked and nothing renders."""
        have = Workspace.versions(self.ws.base)
        if not have:
            raise ValueError("--recover needs an authored version under vfx/; there is none")
        self.ws = Workspace(self.ws.root, version=have[-1])
        by_heading = {s["section"]: s for s in effects}
        out: list[dict] = []
        for n, section in enumerate(outline.get("sections", []), 1):
            group = by_heading.get(section.get("heading", ""), {})
            if group.get("effects"):
                out.append(dict(self._from_rounds(n, section, group), recovered=True))
        return out

    def _from_rounds(self, n: int, section: dict, group: dict) -> dict:
        """One section's record from its kept rounds in this version and the
        reviews cached for them; its best reviewed round is put back in place,
        or its last with an error when none was reviewed."""
        name = self.ws.doc_name(n, section)
        saved = self.ws.read_record(name)
        if isinstance(saved, dict) and saved.get("version") == self.ws.version:
            return self._from_record(n, name, saved)
        stem = name[:-len(".scene.xml")]
        kept = self.ws.dir / self.ws.ROUNDS_DIR / stem
        numbers = sorted(int(d.name[1:]) for d in kept.glob("r[0-9][0-9]")
                         if (d / name).is_file()) if kept.is_dir() else []
        rounds = []
        for r in numbers:
            saved = read_json(self.ws.root / harness.DECISIONS_DIR / "vfx"
                              / f"review-{self.ws.version}-{n:02d}-r{r}.json", warn=False) or {}
            review = saved.get("result") if isinstance(saved.get("result"), dict) else None
            score = review.get("score") if review else None
            rounds.append({"round": r, "symbols": [], "errors": 0 if review else 1,
                           "score": float(score) if score is not None else None,
                           "fidelity": (review or {}).get("fidelity"), "review": review,
                           "timed_out": False, "findings": [],
                           "cost_usd": float(saved.get("cost_usd") or 0.0)})
        best = max((r for r in rounds if r["score"] is not None),
                   key=lambda r: (r["score"], r["round"]), default=None)
        chosen = best or (rounds[-1] if rounds else None)
        if chosen is not None:
            self.ws.restore_round(name, chosen["round"])
        final = self.ws.dir / name
        findings = [] if best else [
            f"error: recovered: no round of section {n} was reviewed before the run "
            f"ended; author it again (`yt vfx --sections {n}`)"]
        text = final.read_text(encoding="utf-8", errors="replace") if final.is_file() else ""
        worlds = self._worlds(section, group)
        program = self.ws.program_name(name)
        self._say(f"    [{n}] {name}: " + (f"round {best['round']} ({best['score']:g}/10)"
                                           if best else "no reviewed round"))
        return {"n": n, "section": section.get("heading", ""),
                "file": f"{self.ws.rel}/{name}", "version": self.ws.version,
                "program": f"{self.ws.rel}/{program}" if (self.ws.dir / program).is_file() else "",
                "provenance": sdkkit.provenance_of(final) if final.is_file() else None,
                "symbols": [f["id"] for f in symbol_facts(text)], "findings": findings,
                "tool_calls": 0, "props_declined": props_declined(text),
                "worlds": [{"id": w["id"], "start": w["start"], "seconds": w["seconds"],
                            "shots": [s["label"] for s in w["shots"]],
                            "phases": [e["id"] for e in w["phases"]]} for w in worlds],
                "revisions": max(numbers) if numbers else 0, "rounds": rounds,
                "best_round": chosen["round"] if chosen else None,
                "score": best["score"] if best else None,
                "review": best["review"] if best else None,
                "cost_usd": round(sum(r["cost_usd"] for r in rounds), 4),
                **self._provenance(n, findings)}

    def _from_record(self, n: int, name: str, saved: dict) -> dict:
        """One section's record from the one its run wrote (rounds/<stem>/record.json):
        a whole record is put back on its round as it stands; a partial one
        (the run died between gates) ends on the round the verdicts promoted,
        or on its last with an error when none was reviewed."""
        unreviewed = (f"error: recovered: no round of section {n} was reviewed before the run "
                      f"ended; author it again (`yt vfx --sections {n}`)")
        if saved.get("finished", True):
            if saved.get("best_round") is not None:
                self.ws.restore_round(name, int(saved["best_round"]))
            self._say(f"    [{n}] {name}: its record, round {saved.get('best_round')} "
                      f"({saved.get('score')}/10)")
            findings = list(saved.get("findings") or [])
            if saved.get("score") is None:
                findings.insert(0, unreviewed)
            return dict(saved, findings=findings)
        rounds = [r for r in saved.get("rounds") or [] if isinstance(r, dict)]
        best = best_round(rounds)
        chosen = best or (rounds[-1] if rounds else None)
        if chosen is not None:
            self.ws.restore_round(name, int(chosen["round"]))
        final = self.ws.dir / name
        program = self.ws.program_name(name)
        findings = list(best["findings"]) if best else [unreviewed]
        self._say(f"    [{n}] {name}: partial record, " + (
            f"round {best['round']} ({best['score']:g}/10)" if best else "no reviewed round"))
        return dict(saved, finished=True, findings=findings,
                    symbols=list(best["symbols"]) if best else saved.get("symbols", []),
                    program=f"{self.ws.rel}/{program}" if (self.ws.dir / program).is_file() else "",
                    provenance=sdkkit.provenance_of(final) if final.is_file() else None,
                    best_round=chosen["round"] if chosen else None,
                    score=best["score"] if best else None,
                    review=best["review"] if best else None)

    def taken(self, outline: dict, effects: list[dict]) -> list[dict]:
        """`--take-attempt N`: the operator overrides the competition after the
        fact. A section whose new attempt lost to the version it replaced (its
        record says `kept_from`; the attempt's rounds sit in rounds/<stem>.attempt)
        gets the attempt back -- its rounds swap places with the kept ones and
        its best reviewed round goes in place -- with the operator's reason on
        the record. The other sections keep theirs. Nothing is authored."""
        records = [r for r in self.dossier.load("12-author") or [] if isinstance(r, dict)]
        if not records:
            raise ValueError("--take-attempt needs an authoring record; run `yt vfx` first")
        version = next((r.get("version") for r in records if r.get("version")), None)
        self.ws = Workspace(self.ws.root, version=version)
        by_heading = {s["section"]: s for s in effects}
        sections = outline.get("sections", [])
        out = []
        for record in records:
            n = int(record["n"])
            if n not in self.take_attempt:
                out.append(record)
                continue
            if not record.get("kept_from") or n > len(sections):
                raise ValueError(f"section {n} kept its new attempt already; there is no "
                                 "earlier version to override")
            section = sections[n - 1]
            stem = self.ws.doc_name(n, section)[:-len(".scene.xml")]
            rounds = self.ws.dir / self.ws.ROUNDS_DIR
            aside = rounds / f"{stem}.attempt"
            if not aside.is_dir():
                raise ValueError(f"section {n}'s attempt is not on disk ({aside})")
            beaten = rounds / f"{stem}.kept"
            if beaten.exists():
                shutil.rmtree(beaten)
            if (rounds / stem).is_dir():
                (rounds / stem).rename(beaten)
            aside.rename(rounds / stem)
            new = self._from_rounds(n, section, by_heading.get(section.get("heading", ""), {}))
            new["taken"] = {"over": record.get("kept_from"), "its_score": record.get("score"),
                            "why": self.take_why or "taken by the operator"}
            self._say(f"    [{n}] the attempt ({new.get('score')}) replaces "
                      f"{record.get('kept_from')}'s ({record.get('score')}): {new['taken']['why']}")
            out.append(new)
        return sorted(out, key=lambda r: r["n"])

    def run(self) -> dict:
        outline = self.outline()
        style = self.style()
        tokens = tokens_from(style)
        effects = self.effects(outline, style)
        if self.take_attempt:
            authored = self._stage("12-author", lambda: self.taken(outline, effects))
        elif self.recover:
            authored = self._stage("12-author", lambda: self.recovered(outline, effects))
        else:
            authored = self.author(outline, effects, style, tokens)
        continuity = self.continuity(authored)
        scene = self.scene_map(effects, authored)
        self.dossier.save("13-scene", scene)
        totals = summarise(effects)
        reached = self.reach(effects, scene)
        plan = self.plan(effects, totals, scene, reached)
        version = next((d.get("version") for d in authored if d.get("version")), self.ws.version)
        return {
            "idea": asdict(self.idea),
            "stack": STACK,
            "runtime": outline.get("runtime", ""),
            "style": style,
            "tokens": tokens,
            "sections": effects,
            "authoring": {"version": version,
                          "dir": f"{SCENE_DIR}/{version}" if version else SCENE_DIR,
                          "versions": Workspace.versions(self.ws.base),
                          "files": [d["file"] for d in authored],
                          "author_bar": self.author_bar, "revisions": self.revisions,
                          "scores": {d["file"]: d.get("score") for d in authored},
                          "props_declined": declined_of(authored),
                          "keyframe": approved_look(self.ws.root)},
            "authored": authored,
            "continuity": continuity,
            "scene": scene,
            "totals": totals,
            "reach": reached,
            "plan": plan,
            "inspo": self.inspo,
            "house": self.house_digest,
            "usage": spend.from_usage(self.usage, self.model),
            "engine": self.harness.report(),
        }


def authored_as(scene: dict, effect_id: str) -> dict | None:
    """The symbol row an effect was authored in -- its world when it is a
    phase, its own symbol the old way -- with `id`, or None if not authored."""
    symbols = scene.get("symbols") or {}
    sid = (scene.get("phases") or {}).get(effect_id, effect_id)
    row = symbols.get(sid)
    return dict(row, id=sid) if row else None


def summarise(sections: list[dict]) -> dict:
    """Arithmetic stays in Python: models are unreliable at adding up."""
    flat = [e for s in sections for e in s["effects"]]
    by_type: dict[str, dict] = {}
    by_difficulty: dict[int, dict] = {}
    templates: dict[str, dict] = {}
    for effect in flat:
        name = (effect.get("template") or "").strip().lower()
        if not name:
            continue
        row = templates.setdefault(name, {"shots": 0, "hours": 0.0})
        row["shots"] += 1
        row["hours"] += float(effect.get("hours") or 0)
    shared = {k: v for k, v in templates.items() if v["shots"] > 1}
    for effect in flat:
        for bucket, key in ((by_type, effect.get("type", "?")),
                            (by_difficulty, int(effect.get("difficulty", 0)))):
            row = bucket.setdefault(key, {"count": 0, "hours": 0.0, "seconds": 0.0})
            row["count"] += 1
            row["hours"] += float(effect.get("hours") or 0)
            row["seconds"] += float(effect.get("seconds") or 0)
    return {
        "count": len(flat),
        "hours": sum(float(e.get("hours") or 0) for e in flat),
        "seconds": sum(float(e.get("seconds") or 0) for e in flat),
        "reusable": sum(v["shots"] for v in shared.values()),
        "templates": dict(sorted(shared.items(), key=lambda kv: -kv[1]["shots"])),
        "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1]["hours"])),
        "by_difficulty": {str(k): by_difficulty[k] for k in sorted(by_difficulty)},
    }


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #

def _label_key(label: str) -> list:
    return [int(p) if p.isdigit() else p for p in label.split(".")]


def write_vfx(dossier: Dossier, data: dict) -> list[Path]:
    style, totals, plan = data["style"], data["totals"], data["plan"]
    idea = data["idea"]
    scene = data.get("scene") or {}
    symbols = scene.get("symbols", {})
    dropped = scene.get("dropped", {})
    authoring = data.get("authoring") or {}
    authored_count = sum(1 for s in data["sections"] for e in s["effects"]
                         if authored_as(scene, e.get("id", "")))
    md = [f"# Visual plan — {idea['title']}", "",
          f"*Authored for {STACK}: one document per section under "
          f"`{authoring.get('dir', SCENE_DIR)}/`, one `<symbol>` per section -- the world, "
          "every shot a phase of it; `yt build` includes it as one continuous take.*", ""]
    if data.get("inspo"):
        md += [f"*Visual reference: {', '.join(data['inspo'])}*", ""]
    md += [f"**{totals['count']} effects · {authored_count} authored and engine-valid in "
           f"{len(symbols)} symbol(s) · {len(scene.get('by_label', {}))} shots covered · "
           f"{totals['hours']:.0f} hours to refine · authoring cost ${scene.get('cost_usd', 0)}**",
           "", f"> {plan.get('verdict', '')}", ""]
    if authoring.get("version"):
        scores = ", ".join(f"`{Path(f).name}` {s if s is not None else '—'}/10"
                           for f, s in (authoring.get("scores") or {}).items())
        md += [f"*Active version `{authoring['version']}` of "
               f"{', '.join(authoring.get('versions') or [authoring['version']])}; "
               f"review bar {authoring.get('author_bar')}/10, up to "
               f"{authoring.get('revisions')} revision(s); scores: {scores or '—'}*", ""]
    glance = reach.summary(data.get("reach"))
    if glance:
        audit = data["reach"]["audit"]
        md += ["## Reach", "",
               f"**{glance['score']}/10** -- {glance['summary']}", "",
               "| family | used / available |", "|---|---|"]
        md += [f"| {fam} | {cov} |" for fam, cov in glance["coverage"].items()]
        md += ["", "| effect | verdict | uses | upgrade to | gain | why |",
               "|---|---|---|---|---|---|"]
        md += [f"| `{e['id']}` | {e['verdict']} | {', '.join(e.get('current', []))} | "
               f"{', '.join(e.get('upgrade_to', [])) or '—'} | {e.get('gain', 0)}/5 | "
               f"{str(e.get('why', ''))[:160]} |" for e in audit.get("effects", [])]
        if audit.get("unused_worth_using"):
            md += ["", "### Unused, and worth using", ""]
            md += [f"- `{u['construct']}` in {u['where']}: {u['why']}"
                   for u in audit["unused_worth_using"]]
        if glance["caveats"]:
            md += ["", "### The engine only approximates or reports", ""]
            md += [f"- `{c['construct']}`: {c['status']}"
                   + (f" when {', '.join(c['when'])}" if c.get("when") else "")
                   + (f" -- {c['note']}" if c.get("note") else "")
                   for c in data["reach"]["report"]["caveats"]]
        md.append("")
    elif (data.get("reach") or {}).get("reason"):
        md += ["## Reach", "", f"*{data['reach']['reason']}*", ""]
    md += ["## The look", "", style.get("look", ""), ""]
    md += ["### Tokens", "", "| token | hex |", "|---|---|"]
    md += [f"| `var(--{k})` | `{v}` |" for k, v in data.get("tokens", {}).items()]
    md += ["", f"**Type.** {style.get('typography', '')}", "",
           f"**Camera.** {style.get('camera_language', '')}", "",
           f"**Transitions.** {style.get('transitions', '')}", "",
           "### Motion rules", ""]
    md += [f"{n}. {r}" for n, r in enumerate(style.get("motion_rules", []), 1)]
    if style.get("avoid"):
        md += ["", "### Avoid", ""] + [f"- {a}" for a in style["avoid"]]

    md += ["", "## Effects by section", ""]
    for section in data["sections"]:
        hours = sum(float(e.get("hours") or 0) for e in section["effects"])
        md += [f"### {section['section']} ({section['seconds']}s · {hours:.1f}h)", ""]
        for effect in section["effects"]:
            stars = ("●" * int(effect.get("difficulty", 0))
                     + "○" * (5 - int(effect.get("difficulty", 0))))
            eid = effect.get("id", "")
            row = authored_as(scene, eid)
            state = (f"authored in `{row['file']}` as "
                     + (f"a phase of `{row['id']}`" if row["id"] != eid else f"`{eid}`")
                     if row else "**not authored** — " + dropped.get(eid, "no document")[:160])
            md += [f"#### {effect['name']}",
                   f"`{effect['type']}` · {stars} · {effect.get('seconds', 0)}s · "
                   f"{effect.get('hours', 0)}h · shots "
                   f"{', '.join(effect.get('shots', [])) or '—'}"
                   + (f"  · template: **{effect['template']}**"
                      if (effect.get("template") or "").strip().lower()
                      in totals.get("templates", {}) else ""),
                   "", f"*{state}*", "",
                   f"**Conveys.** {effect.get('conveys', '')}", "",
                   f"**On screen.** {effect.get('on_screen', '')}", "",
                   f"**Brief.** {effect.get('brief', '')}", ""]
            if effect.get("data_or_claim"):
                md += [f"**Carries.** {effect['data_or_claim']}", ""]
            if effect.get("technique"):
                md += ["**Technique.**", ""]
                md += [f"{n}. {s}" for n, s in enumerate(effect["technique"], 1)] + [""]
            md += [f"**Cheaper fallback.** {effect.get('fallback', '')}", ""]
            if effect.get("risk"):
                md += [f"**Risk.** {effect['risk']}", ""]

    md += ["## Authored documents", ""]
    for doc in data.get("authored", []):
        errors = [f for f in doc.get("findings", []) if f.startswith("error")]
        rounds = doc.get("rounds") or []
        md += [f"- `{doc['file']}` — {len(doc.get('symbols', []))} symbol(s), "
               f"{len(errors)} error(s), {doc.get('tool_calls', 0)} tool calls, "
               f"{doc.get('revisions', 0)} revision(s)"
               + (f", review {doc['score']:g}/10" if doc.get("score") is not None else "")
               + f", ${doc.get('cost_usd', 0)}; preview: `yt render --scene {doc['file']}`"]
        md += [f"  - round {r.get('round')}: {r.get('errors', 0)} error(s)"
               + (f", {len(r['infra'])} frame(s) lost to the host" if r.get("infra") else "")
               + (f", {r['score']:g}/10 — {str((r.get('review') or {}).get('summary', ''))[:140]}"
                  if r.get("score") is not None else ", no review")
               for r in rounds]
        md += [f"  - {e[:160]}" for e in errors[:4]]
    md += ["", "| shot | symbol | document |", "|---|---|---|"]
    for label, hit in sorted(scene.get("by_label", {}).items(), key=lambda kv: _label_key(kv[0])):
        md += [f"| {label} | `{hit['symbol']}` | `{hit['file']}` |"]
    if dropped:
        md += ["", "### Not authored", ""]
        md += [f"- `{eid}` — {why[:200]}" for eid, why in dropped.items()]
    md += ["", "## Cost", "", "| effect type | count | hours | screen time |",
           "|---|---|---|---|"]
    md += [f"| {name} | {row['count']} | {row['hours']:.1f} | {row['seconds']:.0f}s |"
           for name, row in totals["by_type"].items()]
    if totals.get("templates"):
        md += ["", "### Shared rigs (each serves more than one shot)", "",
               "| template | shots | hours as specified |", "|---|---|---|"]
        md += [f"| {name} | {row['shots']} | {row['hours']:.1f} |"
               for name, row in totals["templates"].items()]
    md += ["", "## Refinement plan", "", "### Order", ""]
    md += [f"{n}. {step}" for n, step in enumerate(plan.get("build_order", []), 1)]
    if plan.get("templates"):
        md += ["", "### Build once, reuse", ""]
        md += [f"- **{t['name']}** — {t['covers']} (saves ~{t['saves_hours']}h)"
               for t in plan["templates"]]
    if plan.get("cut_first"):
        md += ["", "### Cut first if short on time", ""] + [f"- {c}" for c in plan["cut_first"]]
    if plan.get("outsource"):
        md += ["", "### Worth outsourcing", ""] + [f"- {o}" for o in plan["outsource"]]
    md += ["", f"*{spend.to_usage(data['usage'])}*", ""]

    written = [dossier.write("vfx.md", "\n".join(md)),
               write_json(dossier.dir / "vfx.json", data)]
    rows = [{"section": s["section"],
             **{k: v for k, v in e.items() if not isinstance(v, (list, dict))},
             "shots": ", ".join(e.get("shots", [])),
             "technique": " | ".join(e.get("technique", [])),
             "inputs": "; ".join(e.get("inputs", [])),
             "authored": "yes" if authored_as(scene, e.get("id", "")) else "no"}
            for s in data["sections"] for e in s["effects"]]
    if rows:
        cols = sorted({k for r in rows for k in r})
        path = dossier.dir / "vfx.csv"
        with open(path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=cols)
            writer.writeheader()
            writer.writerows({c: r.get(c, "") for c in cols} for r in rows)
        written.append(path)
    return written


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def add_planning_args(p) -> None:
    """The flags that shape the plan and the frame. `yt keyframe` plans the
    film for `yt vfx` and authors its lead section, so both take them."""
    p.add_argument("--inspo", default="",
                   help="comma-separated channels to match visually")
    p.add_argument("--per-section", type=int, default=3,
                   help="effects per section (default 3)")
    p.add_argument("--width", type=int, default=FRAME[0], help="frame width (1920)")
    p.add_argument("--height", type=int, default=FRAME[1], help="frame height (1080)")


def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "vfx", parents=list(parents),
        help="plan the animation and have the model author it for scene-render",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_planning_args(p)
    p.add_argument("--jobs", type=int, default=2,
                   help="sections planned and authored in parallel (default 2)")
    flags.budget(p, default=SECTION_BUDGET_USD,
                 help="budget for one section: its author sessions and its reviews "
                      f"together (default {SECTION_BUDGET_USD:g})")
    p.add_argument("--stage-budget-usd", type=float, default=None, dest="stage_budget_usd",
                   help="budget for the whole stage, every section together "
                        "(default: --budget-usd times the sections authored)")
    p.add_argument("--budget-hours", type=float,
                   help="refinement hours available; shapes what to cut")
    flags.reviewer_model(p, default=REVIEWER_MODEL,
                         help="the model that reviews each round's proof frames; never the "
                              f"author's own (default {REVIEWER_MODEL})")
    flags.review_budget(p, default=REVIEW_USD,
                        help=f"what one review may cost (default {REVIEW_USD:g}); a session "
                             "holds it back for the review that follows it")
    flags.rounds(p, default=DEFAULT_ROUNDS, dest="revisions", old=("--revisions",),
                 help="rounds the author gets to fix what the frame gate and the "
                      f"review find (default {DEFAULT_ROUNDS}; the section budget affords them)")
    flags.bar(p, default=7.0, dest="author_bar", old=("--author-bar",),
              help="provisional review score (0-10) under which the frames go "
                   "back to the author (default 7)")
    flags.fresh(p, help="redo every stage, authoring included, into the next vfx/vNN/ "
                        "instead of reusing cached ones")
    p.add_argument("--recover", action="store_true",
                   help="rebuild the record of a run that ended before saving it (killed, timed "
                        "out, crashed) from vfx/vNN/rounds and the cached reviews, then finish "
                        "the film-wide stages; nothing is authored, a section with no reviewed "
                        "round is marked for `--sections`")
    p.add_argument("--finish-now", action="store_true", dest="finish_now",
                   help="ask the run authoring this project to finish: it writes vfx/vNN/FINISH, "
                        "which the run reads between rounds (as it reads SIGUSR1) to end every "
                        "section on its best reviewed round and run the film-wide stages; "
                        "nothing else is done")
    p.add_argument("--take-attempt", metavar="N[,M]", dest="take_attempt",
                   help="take these sections' new attempts over the earlier versions the "
                        "competition kept (their record says kept_from), e.g. when the kept "
                        "one cannot render; nothing is authored")
    p.add_argument("--why", default="", help="with --take-attempt: the operator's reason, "
                                              "kept on the record")
    p.add_argument("--relook", action="store_true",
                   help="put the approved look into the film: carry the section `yt keyframe` "
                        "authored and author every other section again against it, on the "
                        "same plan (what `yt doctor` names when the animation predates the look)")
    p.add_argument("--sections", metavar="N[,M]",
                   help="re-author only these sections (1-based, as in 02-...) into the next "
                        "vfx/vNN/, carrying the others' programs, documents and scores "
                        "forward from the active version; continuity, reach and plan are "
                        "redone")
    # Standalone use: hand it the files instead of a project.
    workspace.add_inputs(p, "script", "voice", "styles")
    p.set_defaults(run=run)
    return p


def planner_from_args(args, idea: Idea, project, *, sections_only: set[int] | None = None,
                      lead: int | None = None, look_notes: list[str] | None = None,
                      relook: bool = False, recover: bool = False,
                      take_attempt: set[int] | None = None, take_why: str = "") -> VfxPlanner:
    """The planner `yt vfx` runs, built from the project's files and the flags;
    `yt keyframe` builds the same one for its look pass (`lead`), so the plan it
    makes is the plan `yt vfx` reuses. A flag a command does not define takes
    `yt vfx`'s default."""
    verbose = not args.quiet
    if project.has("dossier"):
        bundle = load_dossier(project.root, idea)
    else:
        if verbose:
            print("no research in this project; planning from the idea alone",
                  file=sys.stderr)
        bundle = {"idea": idea, "outline": {}, "claims": [], "assets": [], "premise": ""}
    dossier = Dossier(project.root, bundle["idea"], at=project.root)
    script_path = project.source("script", workspace.override(args, "script"))
    sections = script_sections(script_path)
    bundle["picture_check"] = picture_constraints(script_path)
    words = voice_words(project.source("voice", workspace.override(args, "voice")))
    direction = read_json(project.source("styles", workspace.override(args, "styles")),
                          warn=False) or {}
    if verbose:
        print(f"planning visuals for {STACK}: {bundle['idea'].title}\n  -> {dossier.dir}"
              + ("" if sections else "\n  (no screenplay yet: effects will not map to shots)")
              + ("" if words else "\n  (no narration yet: beats cannot land on spoken words)"),
              file=sys.stderr)
    if render.engine() is None and not render.remote_host(args):
        print("warning: no engine reachable (RS_RENDER_HOST unset, no local binary); "
              "documents will pass only the SDK's checks", file=sys.stderr)
    rules, digest = house.for_args(args, project, "vfx")
    if rules and verbose:
        print(f"standards: {', '.join(digest['sources'])}", file=sys.stderr)
    flag = lambda name, default: getattr(args, name, default)  # noqa: E731
    return VfxPlanner(
        bundle, dossier, Workspace(project.root), model=args.model, house=rules,
        house_digest=digest,
        inspo=[s.strip() for s in str(flag("inspo", "") or "").split(",") if s.strip()],
        per_section=flag("per_section", 3), jobs=flag("jobs", 2),
        budget_usd=flag("budget_usd", SECTION_BUDGET_USD),
        stage_budget_usd=flag("stage_budget_usd", None), budget_hours=flag("budget_hours", None),
        reviewer_model=flag("reviewer_model", REVIEWER_MODEL),
        review_budget_usd=flag("review_budget_usd", REVIEW_USD),
        sections=sections, words=words,
        frame=(flag("width", FRAME[0]), flag("height", FRAME[1])), direction=direction,
        revisions=flag("revisions", DEFAULT_ROUNDS), author_bar=flag("author_bar", 7.0),
        fresh=bool(flag("fresh", False)), sections_only=sections_only, lead=lead,
        look_notes=look_notes, relook=relook, recover=recover, take_attempt=take_attempt,
        take_why=take_why, verbose=verbose)


def run(args) -> int:
    idea, project = workspace.resolve(args, inputs=("script", "voice", "styles"))
    if getattr(args, "finish_now", False):
        try:
            path = finish_now(project.root)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"finish asked: {path}\n  the run reads it between rounds; every section ends on "
              "its best reviewed round, then continuity, reach and plan run", file=sys.stderr)
        return 0
    sections_only: set[int] = set()
    if getattr(args, "sections", None):
        try:
            sections_only = {int(x) for x in str(args.sections).split(",") if x.strip()}
        except ValueError:
            print(f"error: --sections takes section numbers, e.g. 2,3 (got {args.sections!r})",
                  file=sys.stderr)
            return 2
        if args.fresh:
            print("error: --sections carries the other sections forward; with --fresh the "
                  "plan they were authored to would change under them", file=sys.stderr)
            return 2

    relook = bool(getattr(args, "relook", False))
    recover = bool(getattr(args, "recover", False))
    take: set[int] = set()
    if getattr(args, "take_attempt", None):
        try:
            take = {int(x) for x in str(args.take_attempt).split(",") if x.strip()}
        except ValueError:
            print(f"error: --take-attempt takes section numbers (got {args.take_attempt!r})",
                  file=sys.stderr)
            return 2
        if args.fresh or sections_only or relook or recover:
            print("error: --take-attempt changes the record as it stands; it takes neither "
                  "--fresh, --sections, --relook nor --recover", file=sys.stderr)
            return 2
    if recover and (args.fresh or sections_only or relook):
        print("error: --recover rebuilds the last run's record as it stands; it takes neither "
              "--fresh, --sections nor --relook", file=sys.stderr)
        return 2
    if relook and (args.fresh or sections_only):
        print("error: --relook carries the approved section and authors all the others on the "
              "cached plan; it takes neither --fresh nor --sections", file=sys.stderr)
        return 2
    planner = planner_from_args(args, idea, project, sections_only=sections_only,
                                relook=relook, recover=recover, take_attempt=take,
                                take_why=str(getattr(args, "why", "") or ""))
    dossier = planner.dossier
    try:
        from . import keyframe
        pending = keyframe.approval_state(project.root)["state"] == "pending"
    except ImportError:
        pending = False
    if pending and not args.quiet:
        print("note: a look awaits approval (yt keyframe --approve); until it is approved "
              "every section is authored without it", file=sys.stderr)
    try:
        data = planner.run()
    except ClaudeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    scene = data["scene"]
    for eid, why in scene["dropped"].items():
        print(f"  not authored: {eid}: {why[:160]}", file=sys.stderr)
    written = write_vfx(dossier, data)
    print(f"\n{dossier.dir}/")
    for path in written:
        print(f"  {path.name}")
    for file in scene["files"]:
        print(f"  {file}")
    totals = data["totals"]
    glance = reach.summary(data.get("reach"))
    print(f"\n{totals['count']} effects · {len(scene['symbols'])} authored · "
          f"{len(scene['by_label'])} shots covered · {totals['hours']:.0f}h to refine"
          + (f" · {len(scene['dropped'])} not authored" if scene["dropped"] else "")
          + (f" · reach {glance['score']}/10, {len(glance['upgrades'])} upgrade(s)"
             if glance else ""))
    print(f"{planner.usage}", file=sys.stderr)
    return 1 if scene["dropped"] and not scene["symbols"] else 0
