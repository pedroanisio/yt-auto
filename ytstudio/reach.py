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
           "prompt", "summary"]

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
    """What a reviewer or a report needs in one glance."""
    if not data or not data.get("audit"):
        return None
    audit, rep = data["audit"], data.get("report") or {}
    return {
        "score": audit.get("reach", {}).get("score"),
        "summary": audit.get("reach", {}).get("summary", ""),
        "upgrades": [{"id": e["id"], "to": e.get("upgrade_to", []), "gain": e.get("gain", 0)}
                     for e in audit.get("effects", []) if e.get("verdict") == "upgrade"],
        "unused_worth_using": [u.get("construct") for u in audit.get("unused_worth_using", [])],
        "coverage": {fam: f"{len(row['used'])}/{row['available']}"
                     for fam, row in rep.get("families", {}).items()},
        "caveats": [c["construct"] for c in rep.get("caveats", [])],
    }
