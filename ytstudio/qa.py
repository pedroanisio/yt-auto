"""Review the rendered film against the publication bar, pass after pass.

docs/production.md is the brief: the rendered frame is the evidence, ten
categories scored 0-10 by professional standards, READY TO PUBLISH only at an
overall of 9.0 with no category under 8.5 and nothing blocking. This stage is
the harness that runs it:

    yt qa --title monty                    # sample, render, score: qa/pass-NN
    yt qa --title monty --frames 20        # more evidence per pass
    yt qa --title monty --reuse pass-02    # score the frames of an earlier pass again

Sampling is deterministic and written down (`samples.json`): the hook, every
section's opening, the longest authored effects, 25/50/75 % of the runtime and
the ending -- each at the midpoint of its shot, never at a boundary, because the
gap between takes is real and a frame on it shows an empty stage. The stills
are rendered by the engine on the render host, from the very document the
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
animation_vfx cannot exceed `MECHANICAL_CAP`.

Then the model reads the frames and the sheet, with the screenplay, the
editorial decisions, the numbers and the mechanical findings beside each
frame, and files the scorecard under a schema. What stills cannot evidence
-- the voice as heard, the mix, the music -- it scores from the measurements
and names in `not_evidenced`; a film with no audio track at all scores
`SILENT_SCORE` on sound_design and music_score by rule, never a neutral 5.

Each pass keeps its frames, `scorecard.json` and `findings.md` (timestamp ->
problem -> root cause -> owner stage -> fix), so "measurably improved" is a
comparison with the pass before and a regression is a category that dropped.
`qa.json` at the project root carries the latest verdict for `yt doctor`.

The loop is: fix the owning stage's source, rebuild, run `yt qa` again. This
stage changes no file but its own; it says what to change and who owns it.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from pathlib import Path

from . import geometry as geo
from . import framecheck, house, reach, workspace
from . import render as render_stage
from .build import EVEN_SHOTS, shots_from_scene  # noqa: F401  (standalone sampling)
from .core import read_json, timecode, write_json
from .harness import Harness, HarnessError, brief
from .script import Shot, visible_text

QA_DIR = "qa"
READY, NOT_READY = "READY TO PUBLISH", "NOT READY"
OVERALL_BAR, CATEGORY_BAR = 9.0, 8.5
REGRESSION = 0.5          # a category that drops this much between passes

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
#: While a blocking mechanical finding stands, the reviewer cannot award more.
MECHANICAL_CAP = 6.0
CAPPED = ("visual_quality", "animation_vfx")
#: A film with no audio track scores this where sound would be judged.
SILENT_SCORE = 2.0
SILENT = ("sound_design", "music_score")
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
    "stage and the fix. What the frames cannot show you score from the "
    "measurements given and list under not_evidenced; never infer audio quality "
    "from the fact that a track exists."
)

#: Who can own a finding: a pipeline stage, or a human route. Improve routes by
#: this word alone; `external` carries the reason in the finding's `fix`.
OWNERS = (tuple(stage for stage, _ in workspace.STAGES)
          + (() if any(s == "keyframe" for s, _ in workspace.STAGES) else ("keyframe",))
          + ("upstream-fact", "external"))

#: What each owner is responsible for, so a finding is placed in the source that fixes it.
DUTIES = {
    "research": "the dossier: claims, sources, licensed assets",
    "styles": "the treatment: palette roles, typography, motion language, the look",
    "script": "the screenplay: shots, narration, supers, timing plan",
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
    },
    "required": ["scores", "overall", "verdict", "findings", "blocking",
                 "not_evidenced", "rationale"],
}


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
               span: float = BURST_SECONDS, limit: int = BURST_MAX_FRAMES) -> dict:
    """Which frames evidence the motion around one midpoint: one contiguous
    window of `span` seconds (at most `limit` frames) centred on the midpoint
    frame, kept inside the shot when it is shorter, so a step between two of
    them is never a real cut. `range` is the engine's `A..B` (end exclusive)
    and `still_frame` the midpoint frame inside it. Deterministic: the same
    midpoint and rate plan the same window."""
    fps = float(fps) or 30.0
    mid = max(int(round(float(t) * fps)), 0)
    count = max(min(int(round(span * fps)), int(limit)), 1)
    first, end = max(mid - count // 2, 0), None
    if seconds is not None and float(seconds) > 0:
        shot_first = max(int(round((float(t) - float(seconds) / 2) * fps)), 0)
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


def _burst(t: float, *, fps: float, seconds: float | None, frame: str) -> dict:
    """The plan plus where each frame lands: `burst/<still stem>_<index>.png`,
    the engine's own `%04d` naming, so the files are known before rendering."""
    plan = burst_plan(t, fps=fps, seconds=seconds)
    stem = frame[:-4] if frame.endswith(".png") else frame
    plan["pattern"] = f"burst/{stem}_%04d.png"
    plan["files"] = [f"burst/{stem}_{f:04d}.png" for f in plan["frames"]]
    return plan


def sample(build: dict, *, limit: int = 14) -> list[dict]:
    """Where the evidence is taken, per docs/production.md §7, in priority
    order so a tight limit still keeps the hook, the ending and the sections.
    Each row carries its burst plan, the frames that evidence motion."""
    shots = sorted(build.get("shots", []), key=lambda s: float(s["at"]))
    if not shots:
        return []
    fps = _fps(build)
    runtime = max(float(s["at"]) + float(s["seconds"]) for s in shots)

    def containing(t: float) -> dict:
        for s in shots:
            if float(s["at"]) <= t < float(s["at"]) + float(s["seconds"]):
                return s
        return shots[-1]

    wanted: list[tuple[dict, str]] = [(shots[0], "hook"), (shots[-1], "ending")]
    seen_sections = set()
    for s in shots:
        if s.get("section") and s["section"] not in seen_sections:
            seen_sections.add(s["section"])
            wanted.append((s, f"section start: {s['section']}"))
    for pct in (25, 50, 75):
        wanted.append((containing(runtime * pct / 100), f"{pct}% of runtime"))
    effects = sorted((s for s in shots if s.get("asset") == "effect"),
                     key=lambda s: -float(s["seconds"]))
    for s in effects[:3]:
        wanted.append((s, "longest authored effect"))

    out: list[dict] = []
    for shot, why in wanted:
        t = _mid(shot)
        hit = next((row for row in out if abs(row["t"] - t) < 0.5), None)
        if hit:
            if why not in hit["why"]:
                hit["why"] += f"; {why}"
            continue
        if len(out) >= limit:
            continue
        seconds = float(shot.get("seconds") or 0)
        out.append({"t": t, "why": why, "label": shot.get("label", ""),
                    "section": shot.get("section", ""),
                    "asset": shot.get("asset", ""), "frame": frame_name(t),
                    "seconds": seconds,
                    "burst": _burst(t, fps=fps, seconds=seconds or None,
                                    frame=frame_name(t))})
    return sorted(out, key=lambda row: row["t"])


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


def measure(samples: list[dict], frames_dir: Path) -> list[dict]:
    """The numbers beside each sample: its still's statistics under `still`
    and its burst's motion under `motion`. Rows are updated in place."""
    for row in samples:
        still = frames_dir / row["frame"]
        if still.is_file():
            row["still"] = frame_stats(still)
        burst = row.get("burst") or {}
        if burst.get("files"):
            paths = [frames_dir / name for name in burst["files"]]
            row["motion"] = burst_motion([p for p in paths if p.is_file()])
    return samples


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
    why = str(row.get("why", ""))
    return "hook" in why or "ending" in why


#: Mechanical kinds that block on their own, wherever they sit.
ALWAYS_BLOCKING = ("penetration", "reads-merged")


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


def mechanical_findings(samples: list[dict], diagnostics: dict | None = None,
                        geometry: dict | None = None, merged: dict | None = None) -> list[dict]:
    """What the measurements already prove, as findings in the scorecard's
    own shape plus `kind` and `shot`. Blocking when on the hook or the
    ending, or when the same kind is on more than one sampled shot; a
    `penetration` (`geometry`, records per shot label) and a `reads-merged`
    (`merged`, spans per shot label) are always blocking and owned by vfx."""
    found: list[dict] = []
    for row in samples:
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
    shots_by_kind: dict[str, set] = {}
    for f in found:
        shots_by_kind.setdefault(f["kind"], set()).add(f["shot"])
    edge = {str(row.get("label", "")) for row in samples if _on_edge(row)}
    for f in found:
        f["severity"] = ("blocking" if f["kind"] in ALWAYS_BLOCKING or f["shot"] in edge
                         or len(shots_by_kind[f["kind"]]) > 1 else "minor")
    return found


def _mean(scores: dict) -> float:
    values = []
    for key in CATEGORIES:
        try:
            values.append(float(scores.get(key, 0)))
        except (TypeError, ValueError):
            values.append(0.0)
    return round(sum(values) / len(values), 2)


def cap_scores(scores: dict, overall: float, mechanical: list[dict]
               ) -> tuple[dict, float, list[str]]:
    """While a blocking mechanical finding stands the reviewer cannot award
    more than MECHANICAL_CAP in the categories the frames measure; the
    overall follows the capped mean when that is lower."""
    scores, capped = dict(scores), []
    if any(f.get("severity") == "blocking" for f in mechanical):
        for key in CAPPED:
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


def silent(build: dict) -> bool:
    """No audio track in the build: nothing to hear, so nothing to score."""
    tracks = (build or {}).get("audio_tracks")
    if isinstance(tracks, (int, float)):
        return int(tracks) <= 0
    return not tracks


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


def contact_sheet(samples: list[dict], frames_dir: Path, out: Path) -> Path:
    """Every still tiled in time order with its timestamp, shot and motion
    verdict, so the reviewer sees the runtime at once. A missing still is an
    empty, labelled tile."""
    from PIL import Image, ImageDraw
    rows = list(samples)
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


def verdict_of(scores: dict, overall: float, *, blocking: list) -> tuple[str, list[str]]:
    """The bar of §2, applied mechanically to whatever the reviewer scored."""
    reasons = []
    if overall < OVERALL_BAR:
        reasons.append(f"overall {overall:.1f} is under {OVERALL_BAR}")
    for key in CATEGORIES:
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
    if gate and gate.get("exit"):
        for row in (gate.get("triage") or {}).get("blocking", []):
            out.append(f"gate: {row.get('what', '')}")
        if not (gate.get("triage") or {}).get("blocking"):
            out.append(f"gate failed (exit {gate.get('exit')})")
    return out


def compare(prev: dict | None, cur: dict) -> dict:
    """Pass against pass: per-category deltas and what regressed."""
    if not prev:
        return {"against": None, "deltas": {}, "overall_delta": 0.0, "regressions": []}
    deltas = {}
    for key in CATEGORIES:
        try:
            deltas[key] = round(float(cur["scores"].get(key, 0))
                                - float(prev.get("scores", {}).get(key, 0)), 2)
        except (TypeError, ValueError):
            deltas[key] = 0.0
    return {"against": prev.get("pass"), "deltas": deltas,
            "overall_delta": round(float(cur.get("overall", 0))
                                   - float(prev.get("overall", 0)), 2),
            "regressions": [k for k, d in deltas.items() if d <= -REGRESSION]}


def findings_md(name: str, card: dict, samples: list[dict], diff: dict, *,
                reasons: list[str]) -> str:
    stamp = _stamp
    lines = [f"# {name}", "", f"**Verdict:** {card.get('verdict', '?')}  ",
             f"**Overall:** {card.get('overall', '?')}", ""]
    if reasons:
        lines += ["Why not ready:", *[f"- {r}" for r in reasons], ""]
    if card.get("capped"):
        lines += [f"**Capped:** {', '.join(card['capped'])} held at {MECHANICAL_CAP:g} "
                  "while a blocking mechanical finding stands", ""]
    lines += ["| Category | Score | Δ vs previous |", "|---|---|---|"]
    for key in CATEGORIES:
        delta = diff.get("deltas", {}).get(key)
        lines.append(f"| {key} | {card.get('scores', {}).get(key, '?')} | "
                     f"{'' if delta is None else f'{delta:+.1f}'} |")
    lines.append("")
    if diff.get("regressions"):
        lines += ["**Regressions:** " + ", ".join(diff["regressions"]), ""]
    lines += ["## Findings", ""]
    if not card.get("findings"):
        lines.append("None reported.")
    for row in card.get("findings", []):
        lines += [f"### {stamp(row.get('timestamp'))} — {row.get('problem', '')}"
                  f" [{row.get('severity', '?')}]",
                  f"- frame: {row.get('frame', '')}",
                  f"- why: {row.get('why', '')}",
                  f"- root cause: {row.get('root_cause', '')}",
                  f"- owner: **{row.get('owner', '?')}** — fix: {row.get('fix', '')}", ""]
    if card.get("blocking"):
        lines += ["## Blocking", *[f"- {b}" for b in card["blocking"]], ""]
    if card.get("not_evidenced"):
        lines += ["## Not evidenced by this pass",
                  *[f"- {n}" for n in card["not_evidenced"]], ""]
    lines += ["## Mechanical", "",
              "Measured from the stills, the bursts and the engine's diagnostics before "
              f"the review; a blocking one caps {' and '.join(CAPPED)} at {MECHANICAL_CAP:g}.",
              ""]
    if not card.get("mechanical"):
        lines.append("None found.")
    for row in card.get("mechanical") or []:
        lines += [f"- {stamp(row.get('timestamp'))} shot {row.get('shot', '')} "
                  f"[{row.get('severity', '?')}] {row.get('kind', '')}: "
                  f"{row.get('problem', '')} — owner **{row.get('owner', '?')}** — "
                  f"fix: {row.get('fix', '')}"]
    lines += ["", "## Evidence sampled", ""]
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
        code = render({"still": row["t"], "out": str(still)})
        if code or not still.is_file():
            problems.append(f"no frame at {row['t']}s (shot {row['label']}): "
                            f"engine exited {code}")
    return problems


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def narration_measurements(voice: dict) -> dict:
    """What voice.json says the narration is. A wordless film says so (and how
    many descriptive captions it carries), so no reviewer reads engine "none"
    as a narration that failed to record."""
    out = {k: voice.get(k) for k in ("engine", "voice", "timing", "drift_seconds",
                                     "too_fast_for_captions", "overlaps", "review",
                                     "casting")}
    if voice.get("wordless"):
        out["wordless"] = True
        out["descriptive_captions"] = (voice.get("descriptive_captions") or {}).get("cues")
    return out


def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "qa", parents=list(parents),
        help="score the rendered film against the publication bar, per pass",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scene", metavar="FILE",
                   help="the document (default: the project's scene.xml)")
    p.add_argument("--frames", type=int, default=14, dest="frame_limit",
                   help="how many stills to sample (default 14)")
    p.add_argument("--quality", default="preview",
                   choices=["draft", "preview", "final"],
                   help="render quality of the stills (default preview)")
    p.add_argument("--reuse", metavar="PASS",
                   help="score the frames of this earlier pass again, rendering nothing")
    p.add_argument("--host", help="ssh target of the render host")
    p.add_argument("--local", action="store_true", help="render the stills here")
    p.add_argument("--fresh", action="store_true",
                   help="score again even if the evidence did not change")
    workspace.add_inputs(p, "build", "script", "voice", "sound", "styles", "gate")
    p.set_defaults(run=run)
    return p


def run(args) -> int:
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

    # -- the evidence --------------------------------------------------------
    if args.reuse:
        pass_dir = home / QA_DIR / args.reuse
        samples = read_json(pass_dir / "samples.json", warn=False, shape=list)
        if not samples:
            print(f"error: {pass_dir} has no samples.json to reuse", file=sys.stderr)
            return 1
        problems = []
    else:
        pass_dir = next_pass(home)
        samples = sample(build, limit=args.frame_limit)
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
    frames_dir = pass_dir / "frames"
    have = {p.name for p in frames_dir.glob("*.png")} if frames_dir.is_dir() else set()
    if not have:
        print("error: no frame was rendered, so there is nothing to judge. "
              "Is a render host reachable (RS_RENDER_HOSTS)?", file=sys.stderr)
        return 1

    # -- what the frames measure, before anyone looks --------------------------
    measure(samples, frames_dir)
    write_json(pass_dir / "samples.json", samples)        # the plan, and now the numbers
    render_report = read_json(project.source("render"), warn=False)
    diagnostics = engine_diagnostics(render_report, build.get("shots", []))
    # Does the hero pass through the set? The still cannot say; the
    # engine's evaluated frame graph and the document's geometry can.
    sampled = [s for s in build.get("shots", []) if str(s.get("label", "")) in
               {str(row.get("label", "")) for row in samples}]
    geometry_report = GEOMETRY(args, project, scene, sampled)
    if not args.quiet and geometry_report.get("status") != "ok":
        print(f"warning: geometry not checked: {geometry_report.get('why', '?')}", file=sys.stderr)
    mechanical = mechanical_findings(samples, diagnostics=diagnostics,
                                     geometry=geometry_by_shot(geometry_report, build.get("shots", [])),
                                     merged=merged_by_shot(geometry_report, build.get("shots", [])))
    sheet = contact_sheet(samples, frames_dir, pass_dir / "contact-sheet.png")
    if not args.quiet:
        for f in mechanical:
            print(f"[qa] mechanical [{f['severity']}] {_stamp(f['timestamp'])} shot "
                  f"{f['shot']}: {f['problem']}", file=sys.stderr)

    # -- what the reviewer sees beside each frame ----------------------------
    shots = {s.get("label"): s for sec in script.get("sections", [])
             for s in sec.get("shots", [])}
    takes = {t.get("label"): t for t in voice.get("takes", [])}
    evidence = []
    for row in samples:
        shot = shots.get(row["label"], {})
        take = takes.get(row["label"], {})
        evidence.append({
            "frame": row["frame"], "present": row["frame"] in have,
            "timestamp": row["t"], "why_sampled": row["why"],
            "shot": row["label"], "section": row["section"],
            "visual": str(shot.get("visual", ""))[:300],
            **super_fields(shot),
            "narration": str(shot.get("narration", ""))[:300],
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
    cues_near = [{"at": c.get("at"), "seconds": c.get("seconds"), "kind": c.get("kind"),
                  "level": c.get("level"), "motif": c.get("motif")}
                 for c in sound.get("cues", [])][:80]
    measurements = {
        "runtime": build.get("scene", {}).get("duration"),
        "frame": {k: build.get("scene", {}).get(k) for k in ("width", "height", "fps")},
        "layers": build.get("layers"), "placeholders": build.get("placeholders"),
        "legibility": build.get("legibility"), "stale_takes": build.get("stale_takes"),
        "editorial": build.get("editorial"), "findings": build.get("findings"),
        "narration": narration_measurements(voice),
        "sound_cues": cues_near, "mix": sound.get("mix"),
        "gate": (gate or {}).get("triage"),
        "geometry": {k: geometry_report.get(k) for k in
                     ("status", "why", "evaluated", "worst", "floating", "reads_merged")},
        "treatment": (styles.get("styles") or [{}])[0] if isinstance(
            styles.get("styles"), list) else styles.get("treatment"),
        # How far the authored documents reach into the engine, per `yt vfx`:
        # a weak-motion finding can name the construct that was left unused.
        "reach": reach.summary((read_json(project.source("vfx"), warn=False) or {}).get("reach")
                               if project.source("vfx") else None),
    }
    blockers = mechanical_blockers(build, gate=gate) + problems
    previous = None
    for earlier in reversed(_passes(home)):
        if earlier != pass_dir:
            previous = read_json(earlier / "scorecard.json", warn=False)
            if previous:
                break

    # -- the review ------------------------------------------------------------
    rules, digest = house.for_args(args, project, "qa")
    reviewer = Harness(project.root, "qa", model=args.model, role=ROLE,
                       house=rules, house_digest=digest,
                       fresh=getattr(args, "fresh", False), verbose=not args.quiet,
                       timeout=1800.0)
    prompt = "\n\n".join([
        f"Review {pass_dir.name} of \"{idea.title}\". Read `{sheet.name}` first "
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
        "the still call for it. The "
        "mechanical findings are measured, not judged: do not repeat them as "
        "your own, but score as if they stand, because they do. Blocking items "
        "already proven by the manifests are listed; add what the frames show.",
        brief("Frames and what each should show", evidence),
        brief("Mechanical findings, measured before this review (a blocking one "
              f"caps {' and '.join(CAPPED)} at {MECHANICAL_CAP:g})", mechanical),
        brief("Measurements and decisions from the stages", measurements),
        brief("Blocking, already proven", blockers),
        brief("Each finding's owner is exactly one of these; one you cannot place is "
              "`external`, with the reason in `fix`", {o: DUTIES[o] for o in OWNERS}),
        brief("Previous pass, for comparison", {
            "pass": previous.get("pass"), "scores": previous.get("scores"),
            "overall": previous.get("overall"),
            "findings": previous.get("findings", [])[:20]} if previous else None),
    ])
    try:
        card = reviewer.inspect(pass_dir.name, prompt, SCORECARD_SCHEMA,
                                cwd=pass_dir, form="scorecard.form.json")
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # -- the bar, applied; the pass, kept ----------------------------------------
    # What the measurements proved overrides what the reviewer awarded: a
    # silent film scores no sound, and a blocking mechanical finding caps
    # the categories the frames measure.
    card = score_silence(dict(card), build)
    scores, overall, capped = cap_scores(card.get("scores", {}), card.get("overall", 0),
                                         mechanical)
    card["scores"], card["overall"] = scores, overall
    if capped and not args.quiet:
        print(f"note: {', '.join(capped)} capped at {MECHANICAL_CAP:g}: a blocking "
              "mechanical finding stands", file=sys.stderr)
    all_blocking = blockers + [str(b) for b in card.get("blocking", [])
                               if str(b) not in blockers]
    all_blocking += [f"{f.get('problem')} at {f.get('timestamp')}s"
                     for f in card.get("findings", []) if f.get("severity") == "blocking"]
    all_blocking += [f"mechanical: {f['problem']} at {f['timestamp']}s (shot {f['shot']})"
                     for f in mechanical if f["severity"] == "blocking"]
    verdict, reasons = verdict_of(card.get("scores", {}), float(card.get("overall", 0)),
                                  blocking=all_blocking)
    if card.get("verdict") != verdict and not args.quiet:
        print(f"note: the reviewer said {card.get('verdict')!r}; the bar says "
              f"{verdict!r} ({'; '.join(reasons)})", file=sys.stderr)
    seen = scene_digest(Path(scene).read_bytes())
    card = dict(card) | {"pass": pass_dir.name, "verdict": verdict, "bar": reasons,
                         "scene_sha256": seen,
                         "blocking": all_blocking, "frames": sorted(have),
                         "samples": samples, "mechanical": mechanical, "capped": capped,
                         "contact_sheet": sheet.name, "engine": reviewer.report()}
    diff = compare(previous, card)
    card["comparison"] = diff
    write_json(pass_dir / "scorecard.json", card)
    (pass_dir / "findings.md").write_text(
        findings_md(pass_dir.name, card, samples, diff, reasons=reasons), encoding="utf-8")
    write_json(home / "qa.json", {
        "idea": {"title": idea.title}, "pass": pass_dir.name, "verdict": verdict,
        "overall": card.get("overall"), "scores": card.get("scores"),
        "scene_sha256": seen, "blocking": len(all_blocking), "findings": len(card.get("findings", [])),
        "mechanical": len(mechanical), "capped": capped,
        "regressions": diff["regressions"], "reasons": reasons,
        "scorecard": str(pass_dir / "scorecard.json")})

    print(f"\n{idea.title} — QA {pass_dir.name}: {verdict}  (overall {card.get('overall')})")
    for key in CATEGORIES:
        delta = diff["deltas"].get(key)
        print(f"  {key:<20} {float(card['scores'].get(key, 0)):>4.1f}"
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
