"""How far into the engine the authored documents reach.

`yt vfx` validates that a document is correct and renders something. This
answers a different question: did the author use what scene-render offers
for what each shot must convey, or the first construct that worked? The
schema has far more families than a flat diagram touches -- 3D with cameras,
lights and materials; particles, fluids, pyro, oceans, fractures, flocks;
effects, geo, mesh gradients, motion paths -- and a video that stays in
shapes and text is competent, not top tier.

The plumbing here is deterministic: which constructs each document and
symbol uses (`construct_usage`), how that maps onto the engine's families
(`report`), and which entries of the engine's capability manifest touch a
construct in use (`caveats`: what it only approximates or reports). The
judgement -- is the current construct the strongest the schema offers for
this effect, and what would raise it -- is the model's, asked by
`VfxPlanner.reach` under `REACH_SCHEMA`, fed to the refinement plan, written
into `vfx.json` and shown to the QA reviewer.

Beside it, the motion audit (AUTO-144): per authored effect, the rows of
its document that change something during its shots (`motion_slice`), and
the reviewer's model asked whether the viewer watches the change the effect
must show (`MOTION_SCHEMA`), its answer checked against those rows
(`motion_problems`). What must change and does not (`static_processes`) is
required refinement work, a doctor warning and a section `yt improve`
re-authors; it fails nothing.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

from . import render

__all__ = ["FAMILIES", "REACH_SCHEMA", "family_of", "families_text",
           "construct_usage", "usage_of", "manifest", "caveats", "report",
           "prompt", "summary", "MOTION_ROLE", "MOTION_SCHEMA", "motion_slice",
           "motion_prompt", "motion_problems", "static_processes", "needs_motion"]

#: The engine's elements, grouped so a planner can see the whole vocabulary
#: at a glance. Grouping is editorial convenience; every element the schema
#: has is listed under its family or "other", never dropped.
FAMILIES: dict[str, tuple[str, ...]] = {
    "document": (
        "scene", "project", "styles", "token", "textStyle", "output", "assets",
        "symbols", "symbol", "composition", "instance", "include", "markers",
        "marker", "meta", "metadata", "safeArea", "safeAreas", "thumbnail",
        "poster", "accessibility", "colorManagement", "look", "tonemap",
        "destination", "font", "strokeFont"),
    "2d stage": (
        "layer", "group", "sequence", "shape", "connector", "repeat", "text",
        "image", "video", "imageSequence", "lottie", "pdf", "code", "formula",
        "chart", "series", "vector", "word", "span", "layout", "layouts",
        "slot", "mask", "region", "tiles", "pattern", "paints",
        "linearGradient", "radialGradient", "conicGradient", "meshGradient",
        "point", "points", "stop", "generated", "generator", "data",
        "trackData", "bind", "variant", "override", "master", "set", "param",
        "parameters", "iterate", "representation", "link", "textPath"),
    "motion": (
        "animate", "key", "expression", "textAnimator", "motionPath",
        "timeRemap", "transition", "shake", "deform", "modifier",
        "shapeModifier", "morph", "constraint", "transformConstraint", "pin",
        "segment", "route", "flyTo", "beatGrid", "cue", "program", "compute"),
    "3d stage": (
        "viewport3D", "object3D", "camera", "lights", "light", "materials",
        "material", "mesh", "meshSequence", "voxelAsset", "volume", "medium",
        "skeleton", "bone", "joint", "parametricSurface", "parametricPath",
        "heightfield", "crater", "erosion", "scene360"),
    "simulation": (
        "physics", "rigidBody", "softBody", "fracture", "forceField",
        "particleEmitter", "particles3D", "burst", "fluid", "fluidSource",
        "pyro", "pyroSource", "pyroImpulse", "pyroBlast", "ocean", "wave",
        "waterImpulse", "whitewater", "flock", "blob", "slime", "blackHole",
        "accretionDisk", "fractal"),
    "post": ("effect", "effects", "adjustment", "tracking"),
    "geo": ("map", "basemap", "geo", "geoLayer", "featureStyle", "graticule"),
    "audio": ("audio", "audioEffect", "audioMix", "audioTrack", "audiogram",
              "band", "bus", "captions", "captionTrack"),
}

#: Families whose constructs are the ones that separate a flat explainer
#: from a visually intense one; the audit is asked to weigh these.
INTENSE = ("3d stage", "simulation", "post", "motion")

_FAMILY_OF = {m: fam for fam, members in FAMILIES.items() for m in members}

#: Attributes whose value names something (an id, a file, another node)
#: rather than choosing a construct; their values are never counted.
REFERENCE_ATTRS = frozenset((
    "id", "name", "material", "symbol", "asset", "parent", "parentJoint",
    "target", "from", "to", "font", "src", "path", "lights", "node", "mesh",
    "text", "label", "tags", "startMarker", "endMarker", "data", "source",
    "mask", "matte", "camera", "layer", "track", "bus", "ref", "href", "sha256",
    "cacheSha256", "credit", "license", "condition", "values", "labels"))
_ENUMISH = re.compile(r"^[A-Za-z][\w-]{0,31}$")
_NUMERIC = re.compile(r"^[-+]?\d")


def family_of(element: str) -> str:
    return _FAMILY_OF.get(element, "other")


def families_text(element_names) -> str:
    """One line per family with its elements, from the schema that is
    installed: an element the families do not know lands under `other`."""
    names = sorted(set(element_names))
    rows: dict[str, list[str]] = {fam: [] for fam in FAMILIES}
    rows["other"] = []
    for name in names:
        rows[family_of(name)].append(name)
    return "\n".join(f"- {fam}: {', '.join(members)}"
                     for fam, members in rows.items() if members)


# --------------------------------------------------------------------------- #
# usage
# --------------------------------------------------------------------------- #

def _strip(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _count(node: ET.Element, elements: dict, attributes: dict) -> None:
    name = _strip(node.tag)
    elements[name] = elements.get(name, 0) + 1
    for attr, value in node.attrib.items():
        key = f"{name}/@{_strip(attr)}"
        attributes[key] = attributes.get(key, 0) + 1
        value = value.strip()
        # An enum-like value is a construct in its own right (renderer=pathtrace);
        # a number, a colour, a path, a sentence or a reference to an id is not.
        if (_strip(attr) not in REFERENCE_ATTRS and _ENUMISH.match(value)
                and not _NUMERIC.match(value) and value not in ("true", "false")):
            vkey = f"{key}={value}"
            attributes[vkey] = attributes.get(vkey, 0) + 1


def construct_usage(doc_text: str) -> dict:
    """The elements and attributes one document uses, in total and per
    `<symbol id>`; attribute keys read like the capability manifest's
    (`camera/@renderer=pathtrace`, `material/@unevenness`)."""
    out: dict = {"elements": {}, "attributes": {}, "symbols": {}, "error": ""}
    try:
        root = ET.fromstring(doc_text)
    except ET.ParseError as exc:
        out["error"] = str(exc)
        return out
    for node in root.iter():
        _count(node, out["elements"], out["attributes"])
    for symbol in root.iter():
        if _strip(symbol.tag) != "symbol":
            continue
        sid = symbol.get("id") or f"symbol-{len(out['symbols']) + 1}"
        per: dict = {"elements": {}, "attributes": {}}
        for node in symbol.iter():
            if node is symbol:
                continue
            _count(node, per["elements"], per["attributes"])
        out["symbols"][sid] = per
    return out


def usage_of(files: dict[str, str]) -> dict:
    """`construct_usage` over several documents, merged, keyed by file."""
    merged: dict = {"files": {}, "elements": {}, "attributes": {}, "symbols": {}}
    for name, text in files.items():
        one = construct_usage(text)
        merged["files"][name] = one
        for key in ("elements", "attributes"):
            for k, n in one[key].items():
                merged[key][k] = merged[key].get(k, 0) + n
        for sid, per in one["symbols"].items():
            merged["symbols"][sid] = dict(per, file=name)
    return merged


# --------------------------------------------------------------------------- #
# the engine's own word on what it approximates
# --------------------------------------------------------------------------- #

def manifest() -> list[dict]:
    """The entries of scene-render's capability manifest: constructs the
    engine only approximates or reports. From `$SR_CAPABILITIES`, else the
    local binary's `capabilities`, else a checkout beside this one. Empty
    when none can be found; the audit then runs without caveats."""
    explicit = os.environ.get("SR_CAPABILITIES")
    if explicit:
        return _entries_from_file(Path(explicit))
    binary = render.engine()
    if binary is not None:
        try:
            out = subprocess.run([str(binary), "capabilities"], capture_output=True,
                                 text=True, timeout=20).stdout
            if "{" in out:
                return list((json.loads(out[out.index("{"):]) or {}).get("entries") or [])
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    here = Path(__file__).resolve().parent.parent
    for candidate in (here.parent / "scene-render", Path.home() / "src/scene-render"):
        entries = _entries_from_file(candidate / "capabilities.json")
        if entries:
            return entries
    return []


def _entries_from_file(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return list(data.get("entries") or []) if isinstance(data, dict) else []


def caveats(usage: dict, entries: list[dict]) -> list[dict]:
    """The manifest entries whose construct the documents actually use."""
    used = set(usage.get("elements", {})) | set(usage.get("attributes", {}))
    # A bare name in the manifest (`pattern`) is the construct wherever it
    # appears: as an element, or as the attribute that applies it.
    bare = set(usage.get("elements", {})) | {
        k.split("/@", 1)[1].split("=", 1)[0] for k in usage.get("attributes", {})}
    hits = []
    for entry in entries:
        construct = str(entry.get("construct", ""))
        if construct in used or ("/" not in construct and construct in bare):
            hits.append({"construct": construct, "status": entry.get("status", ""),
                         "when": list(entry.get("when") or []),
                         "note": entry.get("note", "")})
    return hits


# --------------------------------------------------------------------------- #
# the report, and the question
# --------------------------------------------------------------------------- #

def report(usage: dict, element_names, entries: list[dict]) -> dict:
    """Coverage of the engine's families by the documents, with caveats."""
    names = sorted(set(element_names))
    used = set(usage.get("elements", {}))
    families: dict[str, dict] = {}
    for fam in list(FAMILIES) + ["other"]:
        members = [n for n in names if family_of(n) == fam]
        if not members:
            continue
        hit = [n for n in members if n in used]
        families[fam] = {"available": len(members), "used": hit,
                         "unused": [n for n in members if n not in used],
                         "coverage": round(len(hit) / len(members), 2)}
    return {"families": families,
            "used_total": len(used & set(names)),
            "available_total": len(names),
            "caveats": caveats(usage, entries),
            "unknown": sorted(used - set(names))}


REACH_SCHEMA = {
    "type": "object",
    "properties": {
        "effects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["strongest", "upgrade"]},
                    "current": {"type": "array", "items": {"type": "string"}},
                    "upgrade_to": {"type": "array", "items": {"type": "string"}},
                    "why": {"type": "string"},
                    "gain": {"type": "integer", "minimum": 0, "maximum": 5},
                    "risk": {"type": "string"},
                },
                "required": ["id", "verdict", "current", "upgrade_to", "why", "gain", "risk"],
            },
        },
        "unused_worth_using": {
            "type": "array",
            "items": {"type": "object",
                      "properties": {"construct": {"type": "string"},
                                     "where": {"type": "string"},
                                     "why": {"type": "string"}},
                      "required": ["construct", "where", "why"]},
        },
        "reach": {"type": "object",
                  "properties": {"score": {"type": "integer", "minimum": 0, "maximum": 10},
                                 "summary": {"type": "string"}},
                  "required": ["score", "summary"]},
    },
    "required": ["effects", "unused_worth_using", "reach"],
}


def _constructs_of(per: dict, limit: int = 40) -> str:
    elements = sorted(per.get("elements", {}).items(), key=lambda kv: -kv[1])
    enum_attrs = sorted(k for k in per.get("attributes", {}) if "=" in k)
    parts = [f"{k}×{n}" for k, n in elements[:limit]]
    parts += enum_attrs[:limit]
    return ", ".join(parts) or "(nothing)"


def prompt(*, idea_block: str, direction_block: str, mission: str,
           effects: list[dict], usage: dict, rep: dict, element_names) -> str:
    """The question: for each authored effect, is this the strongest the
    schema offers for what it must convey?"""
    lines = [idea_block, "", mission, "", direction_block, "",
             "## The engine's vocabulary, by family", "",
             families_text(element_names), "",
             "## What the authored documents reach", ""]
    for fam, row in rep["families"].items():
        lines.append(f"- {fam}: {len(row['used'])}/{row['available']} used"
                     + (f" ({', '.join(row['used'][:12])})" if row["used"] else ""))
    if rep.get("caveats"):
        lines += ["", "## What the engine only approximates or reports, among the "
                      "constructs in use", ""]
        lines += [f"- {c['construct']}: {c['status']}"
                  + (f" when {', '.join(c['when'])}" if c["when"] else "")
                  + (f" -- {c['note']}" if c.get("note") else "") for c in rep["caveats"]]
    lines += ["", "## Each authored effect, and the constructs its symbol uses", ""]
    for e in effects:
        per = usage.get("symbols", {}).get(e["id"], {})
        lines += [f"### `{e['id']}` -- {e.get('name', '')} ({e.get('type', '')})",
                  f"Must convey: {e.get('conveys', '')}",
                  f"Brief: {str(e.get('brief', ''))[:1500]}",
                  f"Uses: {_constructs_of(per)}", ""]
    lines += [
        "## The question", "",
        "For every effect above, judge whether the constructs its symbol uses are "
        "the strongest the schema offers for what it must convey, under the "
        "treatment chosen for this video. `strongest` means a stronger construct "
        "would not serve the shot better; `upgrade` names the constructs that "
        "would (exact element names from the vocabulary, with the decisive "
        "attribute when one matters, e.g. `camera/@renderer=pathtrace`), why, the "
        "gain 0-5 for a viewer, and the risk (render time, an approximation the "
        "engine reports, a treatment it would break). Then list the constructs the "
        "video does not use at all that would raise it, and where. Finally score "
        "0-10 how far these documents reach into what the engine can do for this "
        "treatment -- 10 is a video that could not look more intense on this "
        "engine without changing the treatment, 5 is competent 2D where 3D, "
        "simulation or post would have carried it, and say so in one sentence. "
        "Judge against the treatment: a deliberately flat treatment is not "
        "penalised for being flat, but it is for weak motion, no depth cues and "
        "no post where the treatment allows them.",
    ]
    return "\n".join(lines)


def summary(data: dict | None) -> dict | None:
    """What a reviewer or a report needs in one glance: the audit's score and
    upgrades, and the motion audit's static processes. A record with no
    audit and no static process (an older one, or nothing authored) is
    None, as before, so QA's context does not change for it; one with only
    static processes has score None and no upgrades."""
    motion = (data or {}).get("motion")
    motion = motion if isinstance(motion, dict) else {}
    if not data or not (data.get("audit") or motion.get("static")):
        return None
    audit, rep = data.get("audit") or {}, data.get("report") or {}
    return {
        "score": audit.get("reach", {}).get("score") if audit else None,
        "summary": audit.get("reach", {}).get("summary", ""),
        "upgrades": [{"id": e["id"], "to": e.get("upgrade_to", []), "gain": e.get("gain", 0)}
                     for e in audit.get("effects", []) if e.get("verdict") == "upgrade"],
        "unused_worth_using": [u.get("construct") for u in audit.get("unused_worth_using", [])],
        "coverage": {fam: f"{len(row['used'])}/{row['available']}"
                     for fam, row in rep.get("families", {}).items()},
        "caveats": [c["construct"] for c in rep.get("caveats", [])],
        "static_processes": [{"id": s.get("id", ""), "what_changes": s.get("what_changes", "")}
                             for s in motion.get("static") or [] if isinstance(s, dict)],
    }


# --------------------------------------------------------------------------- #
# the motion audit (AUTO-144): does the viewer watch the change?
# --------------------------------------------------------------------------- #
#
# The reach audit asks whether each effect uses the strongest construct; it
# never asks whether what must change is seen changing, and it is shown
# construct counts, not which property moves when. This asks that, of the
# reviewer's model (another family than the planner's), on evidence computed
# from the XML: per authored effect, the rows of its document that change
# something during its shots. The answer is checked mechanically against
# those rows before it is believed (`motion_problems`), so a construct that
# is merely present, or one entrance fade, is not a change.

#: The families whose elements change something over time. A row is one of
#: these inside the effect's window; no other construct counts as change.
CHANGE_FAMILIES = ("motion", "simulation")

#: Properties whose change only shows or hides a target: one such change is
#: an entrance, not the change a process needs; reveals of two or more
#: targets at distinct times are a step build. A textAnimator's fade presets
#: are its property too, as is "opacity" when that is all it offsets.
FADE_PROPERTIES = frozenset(("opacity", "visible", "visibility", "fade", "fade-in",
                             "fade-out"))

#: Members of the motion family that hold something in place, or mark time,
#: without changing anything themselves: never a row. A change authored
#: inside one (an `animate` under a map `pin`) is a row on it.
STILL_MOTION = frozenset(("constraint", "transformConstraint", "pin", "beatGrid", "cue"))

#: The constructs whose change is their keys: fewer than two distinct key
#: times is a constant, not a row.
KEYED = ("animate", "morph")

#: What a textAnimator offsets on its units (scene-render's textAnimatorType);
#: its `start`/`end` are the selector's range, never seconds.
TEXT_OFFSETS = ("x", "y", "zDepth", "scale", "scaleX", "scaleY", "rotation", "rotationX",
                "rotationY", "skew", "opacity", "fill", "stroke", "strokeWidth", "tracking",
                "lineSpacing", "blur", "baselineShift", "characterOffset", "variation",
                "anchorX", "anchorY")


def _changes(tag: str) -> bool:
    """An element of the families whose elements change something over time."""
    return family_of(tag) in CHANGE_FAMILIES and tag not in STILL_MOTION


#: The motion reviewer's role.
MOTION_ROLE = (
    "You are the film's motion reviewer. For each authored effect you are given what it "
    "must convey, its brief, the picture-check's findings on its shots, and the changes "
    "its scene document makes during those shots, read from the XML (an element, the "
    "property that changes, and when). You judge one thing: where an effect must show a "
    "change over time, does the viewer watch that change during its shots. You name the "
    "changes that show it exactly as they are listed, and you never count a construct "
    "that is present but does not change. Judge what is shown, never how it looks: the "
    "treatment decides the look, and a flat treatment that builds the process in steps "
    "shows the change as well as any other.")

MOTION_SCHEMA = {
    "type": "object",
    "properties": {
        "effects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "the effect's id, as listed"},
                    "unfolds": {"type": "boolean", "description": (
                        "true when what the effect must convey is a change over time: a "
                        "sequence of states, a flow, growth, a cascade, a cause acting on "
                        "something")},
                    "what_changes": {"type": "string", "description": (
                        "the change the viewer must watch, in a few words; empty when it "
                        "does not unfold")},
                    "shown_changing": {"type": "boolean", "description": (
                        "true when the listed changes let the viewer watch that change "
                        "during the effect's shots")},
                    "changed_by": {"type": "array", "items": {"type": "string"},
                                   "description": "the targets, exactly as listed under the "
                                                  "effect, whose changes show it"},
                    "fix": {"type": "string", "description": (
                        "what must change, and when, for the viewer to watch it, in this "
                        "video's treatment; empty when shown_changing is true or it does "
                        "not unfold")},
                },
                "required": ["id", "unfolds", "what_changes", "shown_changing",
                             "changed_by", "fix"],
            },
        },
    },
    "required": ["effects"],
}


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _overlaps(start: float, end: float, windows) -> bool:
    for a, b in windows:
        if (end > start and start < b and end > a) or (end <= start and a <= start < b):
            return True
    return False


def motion_slice(doc_text: str, symbol_id: str, windows=None) -> list[dict]:
    """What changes in one symbol during `windows` (a list of (start, end) in
    the symbol's own seconds; None for the whole symbol): one row per element
    of the motion or simulation families, {target, construct, family,
    property, start, end}.

    A motion element changes its parent: the target is the parent's
    "<tag>#<id>" ("<tag>#(unnamed N)" for one with no id, the same N for
    every change on it), the property its `property` (or `preset`), and an
    element with `<key time>` children, or `animate` children with keys,
    runs from its first key to its last. An `animate` or `morph` with fewer
    than two distinct key times is a constant and no row, and STILL_MOTION
    elements are never rows. One without keys runs over its presetStart
    (on its parent's parent timeline, as scene-render defines it) for
    presetDuration, or its own start/end, else its parent's life; a
    textAnimator's start/end are its selector's range, so it runs from its
    text's start for presetDuration (1 s by default). A simulation changes
    itself: the target is the element, the property "", and it runs over
    its parent's start..end, or the symbol's span. Every ancestor's `start`
    (a layer, a group, a sequence) is added to the times under it. An
    element inside another changing one (a `key`, a `burst`) is part of it,
    not a row. Pure."""
    try:
        root = ET.fromstring(doc_text)
    except ET.ParseError:
        return []
    symbol = next((n for n in root.iter() if _strip(n.tag) == "symbol"
                   and n.get("id") == symbol_id), None)
    if symbol is None:
        return []
    span = _number(symbol.get("duration")) or max(
        (float(b) for _, b in windows or ()), default=0.0)
    rows: list[dict] = []
    names: dict[ET.Element, str] = {}

    def name_of(node: ET.Element) -> str:
        # One name per element, so two changes on one unnamed shape are one
        # target; "(unnamed N)" cannot be a real id's "<tag>#N".
        if node not in names:
            names[node] = (f"{_strip(node.tag)}#{node.get('id')}" if node.get("id")
                           else f"{_strip(node.tag)}#(unnamed {len(names) + 1})")
        return names[node]

    def walk(node: ET.Element, offset: float, base: float,
             life: tuple[float, float]) -> None:
        # offset: where node's own times start; base: its parent's offset.
        inside = _changes(_strip(node.tag))
        for child in node:
            tag = _strip(child.tag)
            family = family_of(tag)
            start, end = _number(child.get("start")), _number(child.get("end"))
            if _changes(tag) and not inside:
                row = _row(child, tag, family, node if family == "motion" else child,
                           offset, base, life, name_of)
                if row is not None:
                    rows.append(row)
            own = offset + (start or 0.0)
            child_life = ((offset + start if start is not None else life[0]),
                          (offset + end if end is not None else life[1])) \
                if start is not None or end is not None else life
            walk(child, own, offset, child_life)

    walk(symbol, 0.0, 0.0, (0.0, span))
    if windows is not None:
        rows = [r for r in rows if _overlaps(r["start"], r["end"], windows)]
    return rows


def _row(el: ET.Element, tag: str, family: str, target: ET.Element, offset: float,
         base: float, life: tuple[float, float], name_of) -> dict | None:
    """One row of `motion_slice`, or None for a constant: `offset` is where
    the target's times start, `base` its parent's, `life` its (start, end)."""
    def keys(node: ET.Element) -> list[float]:
        return [t for t in (_number(k.get("time")) for k in node
                            if _strip(k.tag) == "key") if t is not None]
    times = keys(el) + [t for a in el if _strip(a.tag) == "animate" for t in keys(a)]
    if family == "simulation":
        prop, (start, end) = "", life
    else:
        if tag in KEYED and len(set(times)) < 2:
            return None
        preset = el.get("preset")
        if tag == "textAnimator":
            prop = str(preset or ",".join(a for a in TEXT_OFFSETS if el.get(a) is not None))
        else:
            prop = str(el.get("property") or preset or "")
        own_start, own_end = _number(el.get("start")), _number(el.get("end"))
        if times:
            start, end = offset + min(times), offset + max(times)
        elif tag == "textAnimator":
            # presetStart and presetDuration are textAnimator's alone; a
            # stagger stretches the run by an amount not read here, so it
            # runs to its text's end, as does an animator with no preset.
            ps, pd = _number(el.get("presetStart")), _number(el.get("presetDuration"))
            start = base + ps if ps is not None else life[0]
            end = (start + pd if pd is not None
                   else start + 1.0 if preset and el.get("stagger") is None else life[1])
        elif own_start is not None or own_end is not None:
            start = offset + (own_start or 0.0)
            end = offset + own_end if own_end is not None else life[1]
        else:
            start, end = life
    return {"target": name_of(target), "construct": tag, "family": family,
            "property": prop, "start": round(start, 3), "end": round(end, 3)}


def motion_prompt(effects: list[dict], slices: dict[str, list[dict]],
                  constraints: list[dict] | None = None, *, idea_block: str = "") -> str:
    """The motion audit's question: for each authored effect, what it must
    convey, its brief, the picture-check's standing findings on its shots,
    and its slice as "target property start-end" rows."""
    lines = ([idea_block, ""] if idea_block else []) + [
        "## Each authored effect, and what its document changes during its shots", ""]
    for e in effects:
        shots = [str(s) for s in e.get("shots") or []]
        found = [c for c in constraints or [] if isinstance(c, dict)
                 and c.get("source") == "picture" and c.get("label") in shots]
        rows = slices.get(e["id"]) or []
        lines += [f"### `{e['id']}` -- {e.get('name', '')} ({e.get('type', '')})",
                  f"Shots: {', '.join(shots) or 'the whole world'}",
                  f"Must convey: {e.get('conveys', '')}",
                  f"Brief: {str(e.get('brief', ''))[:1500]}"]
        if found:
            lines += ["The picture-check on these shots:"]
            lines += [f"- {c.get('problem', '')}" for c in found]
        lines += ["Changes during its shots (target property start-end):"]
        lines += ([f"- {r['target']} {r['property'] or r['construct']} "
                   f"{r['start']:g}-{r['end']:g}" for r in rows]
                  or ["- none: nothing of the motion or simulation families changes in "
                      "these shots"])
        lines.append("")
    lines += [
        "## The question", "",
        "For every effect above, say whether what it must convey unfolds over time "
        "(`unfolds`) and what the viewer must watch change (`what_changes`). Then say "
        "whether a viewer watches that change during the effect's shots "
        "(`shown_changing`), from the changes listed under it, and name in `changed_by` "
        "the targets, exactly as listed, whose changes show it. Only a listed change "
        "counts: a construct that is present but does not change shows nothing changing, "
        "and one fade in or out of one target is an entrance, not the change; reveals of "
        "two or more parts at different times are a step build and count. Where it "
        "unfolds and is not shown changing, `fix` says what must change and when; "
        "otherwise `fix` is empty. Judge against the treatment: a flat treatment that "
        "builds the process in steps, one state per beat, shows the change, and 3D or a "
        "simulation is never asked for its own sake.",
    ]
    return "\n".join(lines)


def motion_problems(answer, effect_ids, slices: dict[str, list[dict]]) -> list[str]:
    """What makes a motion answer unreadable, checked against the rows: an
    authored effect unanswered or answered twice, an id that is not one, a
    change named that is not in that effect's rows, `shown_changing` with no
    change named, or with only one fade (opacity or visibility) on one
    target, or fades all at one time. Empty when the answer can be believed.
    Pure; nothing is judged here."""
    rows = (answer or {}).get("effects") if isinstance(answer, dict) else None
    rows = [r for r in rows or [] if isinstance(r, dict)]
    wanted = [str(e) for e in effect_ids]
    problems: list[str] = []
    seen: dict[str, int] = {}
    for r in rows:
        eid = str(r.get("id", ""))
        seen[eid] = seen.get(eid, 0) + 1
    problems += [f"{eid} is not an authored effect" for eid in seen if eid not in wanted]
    problems += [f"{eid} is answered {n} times" for eid, n in seen.items()
                 if eid in wanted and n > 1]
    problems += [f"{eid} is not answered" for eid in wanted if eid not in seen]
    done: set[str] = set()
    for r in rows:
        eid = str(r.get("id", ""))
        if eid not in wanted or eid in done or r.get("shown_changing") is not True:
            continue
        done.add(eid)
        mine = slices.get(eid) or []
        named = [str(t) for t in r.get("changed_by") or []]
        outside = [t for t in named if t not in {m["target"] for m in mine}]
        if not named:
            problems.append(f"{eid} is said to show its change but names no change "
                            "(changed_by is empty)")
        elif outside:
            problems.append(f"{eid} names {', '.join(outside)}, which change(s) nothing "
                            "during its shots")
        else:
            used = [m for m in mine if m["target"] in named]
            if all(m["property"] in FADE_PROPERTIES for m in used):
                starts = {}
                for m in used:
                    starts[m["target"]] = min(starts.get(m["target"], m["start"]), m["start"])
                if len(starts) < 2 or len(set(starts.values())) < 2:
                    problems.append(f"{eid} names only a fade ({', '.join(named)}): one "
                                    "fade is an entrance, not the change")
    return problems


def static_processes(answer) -> list[dict]:
    """The effects that must show a change and do not: {id, what_changes, fix}
    for each answer whose `unfolds` is true and `shown_changing` false."""
    rows = (answer or {}).get("effects") if isinstance(answer, dict) else None
    return [{"id": str(r.get("id", "")), "what_changes": str(r.get("what_changes", "")),
             "fix": str(r.get("fix", ""))}
            for r in rows or [] if isinstance(r, dict)
            and r.get("unfolds") is True and r.get("shown_changing") is False]


def needs_motion(record) -> bool:
    """A cached reach record the motion audit is asked for again: one made
    before it (it has `usage`, None when nothing was authored, and no
    `motion`), or one whose reviewer failed (`motion.failed`, a timeout or
    a CLI error, which says nothing about the film). An answer that could
    not be read is an answer, and is kept."""
    if not isinstance(record, dict) or "usage" not in record:
        return False
    motion = record.get("motion")
    return "motion" not in record or (isinstance(motion, dict) and motion.get("failed") is True)
