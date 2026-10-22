"""Read a published film's audience retention back onto its beats.

    yt retention --title X                      # reads work/<slug>/retention/*.json
    yt retention --title X --export readings.json

Every threshold in `docs/narrative/` is a prior until the channel's own
curves replace it (its README, item 4), and nothing read them back. This
stage is that loop's first half: it takes what the operator reads off
YouTube Studio for one published film, in Studio's own vocabulary (judging
1: Intro at 0:30, top moments, spikes, dips with timestamps, the standard
curve and the typical one, and the traffic shares), places each timestamp
on the shot `build.json` had on screen, and files findings that `yt
improve` routes like any other (`improve.collect`, source `retention`).

Nothing is fetched. Whether the Analytics API carries key moments is
unchecked (judging, Folklore) and nothing in the corpus states the columns
of Studio's export, so the file contract is `INGEST_SCHEMA`, written by the
operator, and `tests/fixtures/retention/` pins it. No model is asked
either: the mapping is arithmetic, and each finding's `hypothesis` is the
rubric key `TAGS` gives its tag, so the screenplay receives a measurement
and a labelled guess, never an attribution.

What becomes a finding, and the rule it applies:

- **a dip after 0:30**, one finding naming its shot and section, tagged
  `dip-skipped`, `dip-abandoned` or `dip-unknown` as Studio says (judging
  3); a dip inside the Intro is the Intro's to speak for;
- **a late top moment**, `move_earlier` (judging 3);
- **the Intro**, a hook finding only when it is under the median of the
  channel's like-length films and little of the traffic came from search or
  outside YouTube (structure 38);
- **a steep last 5%**, an `outro` reading, and **a spike after the payoff
  shot**, an `unforeshadowed-payoff` one (structure 40, house readings).

Only the standard curve's timestamps reach the script (judging 2): a moment
marked as read from the relative curve is noted and filed nowhere. Each
finding says how far the standard curve sits from the typical one there
(`delta_vs_typical`, judging 6), never a vendor band. Every constant is in
`BOUNDS`, tagged `sanity_bound` with the corpus rule it serves, and a
finding that leans on one, or on a house reading, or on a channel of fewer
than ten measured films, lists why under its `sanity_bound`.

`retention.json` records the film it measured (`film`, the digest of
`scene.xml`): once a pass rebuilds the film, the curve describes a cut
nobody watched, and `current` returns nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from . import workspace
from .core import read_json, write_json
from .harness import enum_violations, missing_fields, type_errors

#: Where the operator places what Studio shows, inside the project.
EXPORT_DIR = "retention"
RECORD = workspace.ARTIFACTS["retention"]

#: YouTube's own Intro: the share still watching after the first 30 seconds
#: (answer/9314415). A platform definition, not a bound.
INTRO_SECONDS = 30.0
#: Typical retention is "the last 10 of your videos of a similar length"
#: (answer/12942217); under ten the channel has no baseline of its own yet.
BASELINE_FILMS = 10

_MOMENT = {"type": "object", "properties": {
    "timestamp": {"type": "number", "description": "seconds into the film"},
    "curve": {"type": "string", "enum": ["absolute", "relative"],
              "description": "the curve it was read from; the standard (absolute) "
                             "one when not said"}},
    "required": ["timestamp"]}
_DIP = {"type": "object", "properties": dict(_MOMENT["properties"], kind={
    "type": "string", "enum": ["skipped", "abandoned", "unknown"],
    "description": "what Studio says viewers did there; unknown when it does not say"}),
    "required": ["timestamp", "kind"]}
_POINT = {"type": "object", "properties": {
    "timestamp": {"type": "number"},
    "retention": {"type": "number", "description": "percent of the views still "
                                                   "watching; may exceed 100"}},
    "required": ["timestamp", "retention"]}

#: The file contract: one published film, in Studio's words (judging 1).
INGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "video": {"type": "string"},
        "published": {"type": "string", "description": "the publish date, ISO"},
        "views": {"type": "number"},
        "duration_seconds": {"type": "number"},
        "intro_retention_30s": {"type": "number",
                                "description": "percent still watching after 0:30"},
        "above_typical_intro": {"type": "boolean",
                                "description": "Studio lists the film among the "
                                               "above-typical intros"},
        "top_moments": {"type": "array", "items": _MOMENT},
        "spikes": {"type": "array", "items": _MOMENT},
        "dips": {"type": "array", "items": _DIP},
        "curve": {"type": "array", "items": _POINT,
                  "description": "the standard (absolute) retention curve"},
        "typical_curve": {"type": "array", "items": _POINT,
                          "description": "the channel's typical retention for "
                                         "films of this length"},
        "traffic": {"type": "object", "properties": {
            name: {"type": "number", "description": "percent of views"}
            for name in ("search", "external", "suggested", "browse")},
            "required": ["search", "external", "suggested", "browse"]},
    },
    "required": ["intro_retention_30s", "above_typical_intro", "top_moments", "spikes",
                 "dips", "curve", "typical_curve", "traffic"],
}

#: Every number this stage judges by that YouTube does not define. Each is a
#: sanity bound (judging 6): the rule it serves and why it is not evidence.
BOUNDS: dict[str, dict] = {
    "late_top_moment": {
        "value": 0.5, "unit": "of the film's length, at or after which a top moment "
                              "is late", "corpus": ["judging 3"], "sanity_bound": True,
        "note": "YouTube says to bring compelling content earlier when top moments "
                "fall late; where late begins is this pipeline's own"},
    "outro_ratio": {
        "value": 2.0, "unit": "times as steep a fall over the last 5% as over 75-95%",
        "corpus": ["structure 40"], "sanity_bound": True,
        "note": "structure 40's Check line, a house reading of practitioner cases"},
    "outside_traffic": {
        "value": 50.0, "unit": "percent of views from search and external traffic, "
                               "under which a low Intro is read as the hook's",
        "corpus": ["structure 38"], "sanity_bound": True,
        "note": "the rule asks for a threshold and states none; its one case had 86% "
                "of views from search"},
    "similar_length": {
        "value": 2.0, "unit": "ratio of lengths within which another film counts "
                              "toward the baseline", "corpus": ["judging 1", "structure 38"],
        "sanity_bound": True,
        "note": "YouTube compares films of a similar length and does not say how "
                "similar"},
}

#: Each tag: the rule it applies, the rubric key guessed for it (a label, not
#: a cause; narrative.KEYS) and the pipeline's own candidate fixes.
TAGS: dict[str, dict] = {
    "intro": {"rule": "structure 38", "hypothesis": "hook",
              "fix": "state the title's promise and open the film's question inside "
                     "the first 30 seconds"},
    "dip-skipped": {"rule": "judging 3", "hypothesis": "open_loops",
                    "fix": "cut the beat, shorten it, or carry a live question through it"},
    "dip-abandoned": {"rule": "judging 3", "hypothesis": "stakes",
                      "fix": "cut the beat, shorten it, or say before it why it matters"},
    "dip-unknown": {"rule": "judging 3", "hypothesis": "open_loops",
                    "fix": "cut the beat or shorten it"},
    "move_earlier": {"rule": "judging 3", "hypothesis": "hook",
                     "fix": "bring this material, or a promise of it, earlier"},
    "outro": {"rule": "structure 40", "hypothesis": "ending",
              "fix": "end on the payoff: cut the recap or the long sign-off"},
    "unforeshadowed-payoff": {"rule": "structure 40", "hypothesis": "open_loops",
                              "fix": "promise this payoff earlier, as a question the "
                                     "film opens"},
}

_HOUSE_READING = {
    "outro": "a house reading of structure 40 (practitioner cases), not a measured cause",
    "unforeshadowed-payoff": "a house reading of structure 40 (one practitioner's "
                             "case), not a measured cause",
}


# --------------------------------------------------------------------------- #
# the readings
# --------------------------------------------------------------------------- #

def problems(data: Any) -> list[str]:
    """What breaks the contract, by path: a missing field, a value of another
    type or outside its list, a negative time or percentage."""
    found = (missing_fields(data, INGEST_SCHEMA) + type_errors(data, INGEST_SCHEMA)
             + enum_violations(data, INGEST_SCHEMA))
    if not isinstance(data, dict):
        return found or ["not an object"]
    for key in ("top_moments", "spikes", "dips", "curve", "typical_curve"):
        for n, row in enumerate(data.get(key) or []):
            if not isinstance(row, dict):
                continue
            for field in ("timestamp", "retention"):
                value = row.get(field)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
                    found.append(f"{key}[{n}].{field} is negative")
    return found


def ingest(paths: Iterable[Path | str]) -> dict:
    """The readings for one film, from one file or several (later files win a
    field): the operator reads Studio's panes one at a time. A file that is
    not JSON, or a result that breaks INGEST_SCHEMA, is a ValueError naming
    what is wrong."""
    data: dict = {}
    for path in paths:
        if not Path(path).is_file():
            raise ValueError(f"{path}: no such file")
        raw = read_json(path, warn=False)
        if raw is None:
            raise ValueError(f"{path}: not a JSON object")
        data.update(raw)
    bad = problems(data)
    if bad:
        raise ValueError("the retention readings break the contract (INGEST_SCHEMA): "
                         + "; ".join(bad))
    for key in ("curve", "typical_curve"):
        data[key] = sorted(data[key], key=lambda p: p["timestamp"])
    return data


def at(curve: list[dict] | None, t: float) -> float | None:
    """The curve's value at `t` seconds, linear between its points and held
    flat past its ends; None for no curve."""
    points = [(float(p["timestamp"]), float(p["retention"])) for p in curve or []]
    if not points:
        return None
    points.sort()
    if t <= points[0][0]:
        return points[0][1]
    for (t0, r0), (t1, r1) in zip(points, points[1:]):
        if t <= t1:
            return r0 if t1 == t0 else r0 + (r1 - r0) * (t - t0) / (t1 - t0)
    return points[-1][1]


def outside_share(traffic: dict | None) -> float | None:
    """Percent of the views from search and external traffic (structure 38),
    or None when the shares are not there to read."""
    if not isinstance(traffic, dict):
        return None
    try:
        shares = {k: float(traffic[k]) for k in ("search", "external", "suggested", "browse")}
    except (KeyError, TypeError, ValueError):
        return None
    total = sum(shares.values())
    if total <= 0:
        return None
    return 100.0 * (shares["search"] + shares["external"]) / total


def clock(seconds: float) -> str:
    """m:ss, with a tenth only when there is one: 0:48, 1:54.5."""
    minutes, rest = divmod(float(seconds), 60)
    text = f"{rest:04.1f}"
    return f"{int(minutes)}:{text[:-2] if text.endswith('.0') else text}"


# --------------------------------------------------------------------------- #
# the mapping
# --------------------------------------------------------------------------- #

def _section_number(label: str, heading: str, headings: list[str]) -> int | None:
    head = str(label).split(".", 1)[0]
    if head.isdigit():
        return int(head)
    if heading in headings:
        return headings.index(heading) + 1
    return None


def timeline(script_json: dict, build_json: dict) -> list[dict]:
    """build.json's shots in time order, each with its section's number."""
    headings = [str(s.get("heading") or "") for s in (script_json or {}).get("sections") or []
                if isinstance(s, dict)]
    rows = []
    for shot in (build_json or {}).get("shots") or []:
        if not isinstance(shot, dict) or shot.get("at") is None:
            continue
        label, heading = str(shot.get("label") or ""), str(shot.get("section") or "")
        rows.append({"label": label, "at": float(shot["at"]),
                     "seconds": float(shot.get("seconds") or 0.0),
                     "section": _section_number(label, heading, headings),
                     "heading": heading, "function": str(shot.get("function") or "")})
    return sorted(rows, key=lambda r: r["at"])


def place(shots: list[dict], t: float) -> tuple[dict, str]:
    """The shot on screen at `t`, and a note when `t` falls outside every
    shot (before the first, in a gap, past the last)."""
    for shot in shots:
        if shot["at"] <= t < shot["at"] + shot["seconds"]:
            return shot, ""
    before = [s for s in shots if s["at"] <= t]
    if not before:
        return shots[0], f"{clock(t)} is before the first shot; placed on {shots[0]['label']}"
    last = before[-1]
    end = last["at"] + last["seconds"]
    if last is shots[-1]:
        return last, (f"{clock(t)} ({t:g} s) is past the last shot, which ends at "
                      f"{clock(end)}; placed on {last['label']}")
    return last, f"{clock(t)} falls between shots; placed on {last['label']}, the one before"


def _live(questions: list[dict], order: dict[str, int], index: int) -> list[str]:
    """The ledger's questions open at the shot of `index`, the shots that
    raise and answer one included."""
    out = []
    for q in questions:
        opened = order.get(str(q.get("opened_at") or ""))
        closed = order.get(str(q.get("closed_at") or ""))
        if opened is not None and opened <= index and (closed is None or closed >= index):
            out.append(str(q.get("id") or ""))
    return out


def map_to_beats(ingest: dict, script_json: dict, build_json: dict) -> dict:
    """Every key moment placed on its shot and section from build.json's
    timings, with the question ledger's open questions where script.json
    has one (`questions`, AUTO-96); the film's length; where the payoff shot
    ends. A build.json with no shot timings is a ValueError."""
    shots = timeline(script_json, build_json)
    if not shots:
        raise ValueError("build.json has no shot timings to place the curve on")
    scene = (build_json or {}).get("scene") or {}
    curve = ingest.get("curve") or []
    duration = float(ingest.get("duration_seconds") or scene.get("duration")
                     or max(s["at"] + s["seconds"] for s in shots)
                     or (curve[-1]["timestamp"] if curve else 0.0))
    payoff = next((s for s in shots if s["function"] == "payoff"), None)
    order = {s["label"]: n for n, s in enumerate(shots)}
    ledger = (script_json or {}).get("questions")
    questions = [q for q in ledger if isinstance(q, dict)] if isinstance(ledger, list) else None
    moments, notes = [], []
    for name, key in (("dip", "dips"), ("spike", "spikes"), ("top_moment", "top_moments")):
        for raw in ingest.get(key) or []:
            t = float(raw["timestamp"])
            shot, note = place(shots, t)
            if note:
                notes.append(f"a {name.replace('_', ' ')} at {note}")
            row = {"moment": name, "timestamp": raw["timestamp"], "shot": shot["label"],
                   "section": shot["section"], "heading": shot["heading"],
                   "function": shot["function"]}
            if name == "dip":
                row["kind"] = raw["kind"]
            if raw.get("curve"):
                row["curve"] = raw["curve"]
            if questions is not None:
                row["questions"] = _live(questions, order, order[shot["label"]])
            moments.append(row)
    moments.sort(key=lambda m: float(m["timestamp"]))
    return {"duration": duration, "shots": shots, "moments": moments, "notes": notes,
            "payoff_end": (payoff["at"] + payoff["seconds"]) if payoff else None}


# --------------------------------------------------------------------------- #
# the findings
# --------------------------------------------------------------------------- #

def _bound(name: str) -> str:
    b = BOUNDS[name]
    return f"{name} {b['value']:g} {b['unit']} ({', '.join(b['corpus'])}, house)"


def _finding(tag: str, *, timestamp: float, shot: dict | None, section: int | None,
             heading: str, measured: str, delta: float | None,
             bounds: list[str], questions: list[str] | None = None) -> dict:
    row = TAGS[tag]
    where = (f"during shot {shot['label']} (section {section}, \"{heading}\")" if shot
             else f"over the opening (section {section}, \"{heading}\")")
    gap = ""
    if delta is not None:
        gap = (f", {abs(delta):.1f} points {'under' if delta < 0 else 'over'} the "
               "channel's typical curve")
    problem = (f"{clock(timestamp)}: {measured}, {where}{gap} [{row['rule']}]; "
               f"hypothesis, a guess from the tag and not a cause: {row['hypothesis']}")
    out = {"source": "retention", "owner": "script", "timestamp": timestamp,
           "shot": shot["label"] if shot else None, "section": section, "heading": heading,
           "tag": tag, "rule": row["rule"], "delta_vs_typical": delta,
           "hypothesis": row["hypothesis"], "sanity_bound": bounds,
           "severity": "minor", "problem": problem, "fix": row["fix"]}
    if questions:
        out["questions"] = questions
    return out


def _delta(ingest: dict, t: float) -> float | None:
    mine, typical = at(ingest.get("curve"), t), at(ingest.get("typical_curve"), t)
    if mine is None or typical is None:
        return None
    return round(mine - typical, 3)


def reading(ingest: dict, mapped: dict, channel_baseline: dict) -> dict:
    """The findings and the notes for one film (see the module's docstring).
    `channel_baseline` is {films, intro_median} (`channel_baseline`)."""
    notes = list(mapped.get("notes") or [])
    rows: list[dict] = []
    shots, duration = mapped["shots"], float(mapped["duration"])
    films = int(channel_baseline.get("films") or 0)
    thin = ([f"channel baseline of {films} film(s), under {BASELINE_FILMS} (judging 1): "
             "a reading, not yet the channel's own measure"]
            if films < BASELINE_FILMS else [])

    for m in mapped["moments"]:
        t = float(m["timestamp"])
        if m.get("curve") == "relative":
            notes.append(f"the {m['moment'].replace('_', ' ')} at {clock(t)} was read from "
                         "the relative curve; only the standard curve's timestamps reach "
                         "the script (judging 2)")
            continue
        shot = next(s for s in shots if s["label"] == m["shot"])
        place_of = dict(shot=shot, section=m["section"], heading=m["heading"],
                        questions=m.get("questions"))
        if m["moment"] == "dip":
            if t <= INTRO_SECONDS:
                notes.append(f"the dip at {clock(t)} is inside the Intro, which speaks "
                             "for it (judging 3)")
                continue
            said = {"skipped": "viewers skipped", "abandoned": "viewers stopped watching",
                    "unknown": "a dip, which Studio does not say was skipped or left"}
            rows.append(_finding(f"dip-{m['kind']}", timestamp=m["timestamp"],
                                 measured=said[m["kind"]], delta=_delta(ingest, t),
                                 bounds=list(thin), **place_of))
        elif m["moment"] == "top_moment":
            late = BOUNDS["late_top_moment"]["value"] * duration
            if duration and t >= late:
                rows.append(_finding(
                    "move_earlier", timestamp=m["timestamp"],
                    measured=f"a top moment, where almost no one dropped off, "
                             f"{100 * t / duration:.0f}% of the way in",
                    delta=_delta(ingest, t), bounds=[_bound("late_top_moment")] + thin,
                    **place_of))
        elif m["moment"] == "spike":
            end = mapped.get("payoff_end")
            if end is not None and t >= end:
                rows.append(_finding(
                    "unforeshadowed-payoff", timestamp=m["timestamp"],
                    measured=f"a spike, rewatched or shared, after the payoff shot "
                             f"(it ended at {clock(end)})", delta=_delta(ingest, t),
                    bounds=[_HOUSE_READING["unforeshadowed-payoff"]] + thin, **place_of))
    spikes = [m for m in mapped["moments"] if m["moment"] == "spike"]
    if spikes and mapped.get("payoff_end") is None:
        notes.append("no shot in build.json is marked payoff, so no spike is read as "
                     "an unforeshadowed payoff (structure 40)")

    outro = _outro(ingest, shots, duration, thin)
    if outro:
        rows.append(outro)

    intro = _intro(ingest, shots, channel_baseline, thin, notes)
    if intro:
        rows.append(intro)
    rows.sort(key=lambda r: float(r["timestamp"]))
    return {"findings": rows, "notes": notes}


def findings(ingest: dict, mapped: dict, channel_baseline: dict) -> list[dict]:
    """The findings alone (`reading` also gives the notes)."""
    return reading(ingest, mapped, channel_baseline)["findings"]


def _outro(ingest: dict, shots: list[dict], duration: float, thin: list[str]) -> dict | None:
    """Structure 40's tail: the standard curve's fall over the last 5% against
    its fall over 75-95%, as a rate."""
    curve = ingest.get("curve") or []
    if len(curve) < 2 or duration <= 0:
        return None
    r75, r95, r100 = (at(curve, duration * p) for p in (0.75, 0.95, 1.0))
    mid = (r75 - r95) / (0.20 * duration)
    end = (r95 - r100) / (0.05 * duration)
    ratio = BOUNDS["outro_ratio"]["value"]
    if end <= 0 or end < ratio * max(mid, 0.0):
        return None
    t = round(duration * 0.95, 3)
    shot, _ = place(shots, t)
    measured = (f"the last 5% fell {end / mid:.1f} times as steeply as the 75-95% stretch"
                if mid > 0 else "the last 5% fell while the 75-95% stretch held")
    return _finding("outro", timestamp=t, shot=shot, section=shot["section"],
                    heading=shot["heading"], measured=measured, delta=_delta(ingest, t),
                    bounds=[_bound("outro_ratio"), _HOUSE_READING["outro"]] + thin)


def _intro(ingest: dict, shots: list[dict], base: dict, thin: list[str],
           notes: list[str]) -> dict | None:
    """Structure 38: a hook finding only under the channel's median for films
    of similar length, and with little search and external traffic, which
    lowers retention whatever the opening does."""
    intro = float(ingest["intro_retention_30s"])
    median = base.get("intro_median")
    share = outside_share(ingest.get("traffic"))
    if median is None:
        notes.append("no channel baseline of similar-length films yet: the Intro is "
                     "recorded, not judged (structure 38)")
        return None
    if share is None:
        notes.append("no traffic shares: the Intro is recorded, not judged (structure 38)")
        return None
    if intro >= float(median):
        return None
    if share >= BOUNDS["outside_traffic"]["value"]:
        notes.append(f"the Intro ({intro:g}%) is under the channel's median ({median:g}%), "
                     f"but {share:.0f}% of views came from search and external traffic: "
                     "not read as the hook's (structure 38)")
        return None
    first = next((s for s in shots if s["section"] == 1), shots[0])
    return _finding(
        "intro", timestamp=INTRO_SECONDS, shot=None, section=first["section"] or 1,
        heading=first["heading"],
        measured=f"Intro {intro:g}% still watching, under the channel's median of "
                 f"{float(median):g}% for films of similar length, with {share:.0f}% of "
                 "views from search and external traffic",
        delta=_delta(ingest, INTRO_SECONDS),
        bounds=[_bound("outside_traffic"), _bound("similar_length")] + thin)


# --------------------------------------------------------------------------- #
# the channel's baseline, and the record
# --------------------------------------------------------------------------- #

def channel_baseline(work: Path | str, *, exclude: Path | str | None = None,
                     duration: float | None = None) -> dict:
    """The channel's own baseline: the median Intro of its last
    BASELINE_FILMS measured films of similar length (`retention.json` in
    each project, newest by `published`, else by the file's time), the film
    itself left out."""
    skip = Path(exclude).resolve() if exclude else None
    films = []
    try:
        found = workspace.projects(Path(work))
    except OSError:
        found = []
    for project in found:
        path = project.root / RECORD
        # a directory beside the films that this user may not read is no film
        try:
            if skip is not None and project.root.resolve() == skip:
                continue
            record = read_json(path, warn=False)
            stamp = path.stat().st_mtime if record else 0.0
        except OSError:
            continue
        data = (record or {}).get("ingest")
        if not isinstance(data, dict):
            continue
        intro = data.get("intro_retention_30s")
        if not isinstance(intro, (int, float)) or isinstance(intro, bool):
            continue
        seconds = data.get("duration_seconds") or (record or {}).get("duration")
        if duration and isinstance(seconds, (int, float)) and seconds > 0:
            if max(seconds, duration) / min(seconds, duration) > BOUNDS["similar_length"]["value"]:
                continue
        films.append((str(data.get("published") or ""), stamp,
                      project.root.name, float(intro)))
    films = sorted(films)[-BASELINE_FILMS:]
    return {"films": len(films),
            "intro_median": statistics.median(f[3] for f in films) if films else None,
            "slugs": [f[2] for f in films]}


def film(root: Path | str) -> dict:
    """Which cut the curve was measured on: the digest of `scene.xml`."""
    scene = Path(root) / workspace.ARTIFACTS["scene"]
    digest = hashlib.sha256(scene.read_bytes()).hexdigest() if scene.is_file() else None
    return {"scene_sha256": digest}


def current(root: Path | str) -> list[dict]:
    """retention.json's findings while the film is the cut they measured;
    nothing once the film was rebuilt, or when there is no record."""
    record = read_json(Path(root) / RECORD, warn=False)
    if not isinstance(record, dict) or record.get("film") != film(root):
        return []
    return [f for f in record.get("findings") or [] if isinstance(f, dict)]


# --------------------------------------------------------------------------- #
# command line
# --------------------------------------------------------------------------- #

def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "retention", parents=list(parents),
        help="read a published film's audience retention back onto its shots",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--export", action="append", default=[], metavar="FILE",
                   help="the readings to take, instead of the project's "
                        f"{EXPORT_DIR}/*.json (repeatable; later files win a field)")
    p.set_defaults(run=run)
    return p


def run(args) -> int:
    quiet = getattr(args, "quiet", False)
    _idea, project = workspace.resolve(args)
    root = project.root
    if args.export:
        paths = [Path(p) for p in args.export]
        shown = [str(p) for p in args.export]
    else:
        paths = sorted((root / EXPORT_DIR).glob("*.json"))
        shown = [p.relative_to(root).as_posix() for p in paths]
    if not paths:
        print(f"error: no retention readings for {root.name}: place what Studio shows for "
              f"the published film as {root / EXPORT_DIR}/*.json (INGEST_SCHEMA in "
              "ytstudio/retention.py), or pass --export FILE", file=sys.stderr)
        return 1
    build_json = read_json(project.path("build"), warn=False)
    if not isinstance(build_json, dict) or not build_json.get("shots"):
        print(f"error: no build.json with shot timings in {root}: run `yt build` for the "
              "film that was published", file=sys.stderr)
        return 1
    try:
        data = ingest(paths)
        mapped = map_to_beats(data, read_json(project.path("script"), warn=False) or {},
                              build_json)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    base = channel_baseline(workspace.work_dir(args), exclude=root,
                            duration=mapped["duration"])
    got = reading(data, mapped, base)
    write_json(project.path("retention"), {
        "ingest": data, "sources": shown, "duration": mapped["duration"], "film": film(root),
        "baseline": base, "moments": mapped["moments"], "findings": got["findings"],
        "notes": got["notes"], "bounds": BOUNDS, "tags": TAGS,
        "written": time.strftime("%Y-%m-%dT%H:%M:%S")})
    for f in got["findings"]:
        delta = ("" if f["delta_vs_typical"] is None
                 else f" {f['delta_vs_typical']:+.1f} vs typical")
        bound = " [sanity_bound]" if f["sanity_bound"] else ""
        print(f"{clock(f['timestamp']):>7}  {f['shot'] or '-':<6} {f['tag']:<22} "
              f"{f['rule']:<13} hypothesis {f['hypothesis']}{delta}{bound}")
    if not quiet:
        for note in got["notes"]:
            print(f"note: {note}", file=sys.stderr)
        print(f"{len(got['findings'])} finding(s) for `yt improve`, against a channel "
              f"baseline of {base['films']} film(s); wrote {project.path('retention')}",
              file=sys.stderr)
    return 0
