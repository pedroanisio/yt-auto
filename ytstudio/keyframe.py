#!/usr/bin/env python3
"""Lock the look before `yt vfx` spends: one composed frame for the operator's approval.

    yt keyframe --title monty                 # elements, document, frame, review
    yt keyframe --title monty --approve       # record the approval on disk
    yt keyframe --title monty --reject "the main surface reads as plastic; a warmer key"
    yt keyframe --title monty --fresh --rounds 3 --bar 8

A studio locks a style frame before animation starts. This stage is that
frame for the pipeline: ONE 1920x1080 picture that presents the video's most
qualified visual elements together, in the chosen treatment -- the hero
subject in its environment under its key light, the materials of the surfaces
that matter, the palette roles as swatches, one typography sample in the
film's language, the camera language -- each labelled with its source. An
operator approves or rejects it before `yt vfx` authors the film.

Two model calls, both through the harness:

1. `elements` (`Harness.decide`): from styles.json (treatment, palette roles,
   typography, motion), script.json (what is on screen; the hero subject),
   props.json when present (CC0 models, HDRIs, textures with licence and
   credit), the dossier's licensed assets, and the assessment's verdict
   (`assess.stack_brief`), the model picks the elements, says for each why it
   is the most qualified candidate and what its source is (a props id, an
   asset, or "authored"), and ranks them by what the frame must prove; the
   list is capped at MAX_ELEMENTS here.
2. The composition: an authoring agent works in `<project>/keyframe/` with
   the same workspace `yt vfx` gives its authors (sr.py, schema.md,
   examples.scene.xml, tokens.xml, the props with relative paths) and writes
   `keyframe.scene.xml`, a 1-second document whose one symbol composes the
   hero in its environment with the lights and materials, rendered in the
   treatment, plus an unobtrusive band carrying the swatches with their role
   names, the type sample and a small label per element naming it and its
   source. Colours come from the tokens, nouns from the project; nothing is
   hardcoded here. The document is checked with the SDK and the engine, the
   frame is rendered on the fast render host through `render.run_job` (job
   "still"), `framecheck.judge_frame` runs the eyeless checks, and a vision
   review (`Harness.inspect`, reading the PNG) scores it 0-10 on
   docs/production.md's visual quality, cinematography, graphics/overlays and
   overall categories. Under `--bar` (7) the findings go back verbatim to the
   author for up to `--rounds` (2) revision rounds.

Writes keyframe/keyframe.scene.xml, keyframe/keyframe.png, keyframe.json
(elements, document, frame, rounds with their scores and findings, and the
approval: pending | approved | rejected, with the operator's notes and the
document's digest) and keyframe.md, the sheet for a human. `--approve`
records approval against the document's digest; `--reject "notes"` records
the rejection, and the next run feeds the notes to both model calls as the
operator's own instructions, outranking everything else. A re-run without
`--fresh` asks nothing the inputs did not change.

Downstream contract -- what `yt vfx` and `yt doctor` do with it:

- `keyframe.approved_keyframe(project_root)` returns, when keyframe.json is
  approved and the document still matches the approval's digest, a dict with
  `document` (keyframe/keyframe.scene.xml), `frame` (keyframe/keyframe.png),
  `elements` (kind, name, source, file, licence, credit, why) and `approved_at`;
  otherwise None. `keyframe.approval_state(project_root)` is the finer
  reading: none | pending | approved | rejected | stale.
- `yt vfx` must list the approved document and PNG in every author's brief
  as "the approved look: inherit these materials, lights, hero model and
  camera language; a world that departs from it is wrong", and point the
  author at the document to copy its materials, lights and hero geometry.
- `yt doctor` warns when vfx.json predates an approved keyframe or no
  approved keyframe exists, and notes a keyframe awaiting approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from claude_engine import ClaudeError, EngineConfig, Usage
from . import assess, design, framecheck, harness, house, render, vfx, workspace
from .core import Idea, _merge_usage, compact, read_json, write_json
from .vfx import (AUTHOR_SYSTEM, AUTHOR_TOOLS, FRAME, MISSION, STACK, Workspace,
                  prop_textures, props_items)

__all__ = ["KeyframeStage", "KeyframeError", "RENDERER", "approve", "reject",
           "approval_state", "approved_keyframe", "load_record", "rank_elements",
           "check_document", "document_digest", "register", "run"]

#: Where the author works, under the project; the document and the frame.
KEYFRAME_DIR = "keyframe"
DOC_NAME = "keyframe.scene.xml"
FRAME_NAME = "keyframe.png"
BRIEF_NAME = "brief.md"
#: The one symbol the document holds, and where in its second the frame is taken.
SYMBOL_ID = "keyframe-look"
STILL_AT = 0.5
#: Elements the frame presents, at most; past that it is a catalogue, not a look.
MAX_ELEMENTS = 8
DEFAULT_ROUNDS = 2
DEFAULT_BAR = 7.0

ELEMENT_KINDS = ("hero", "environment", "key_light", "material", "palette",
                 "typography", "camera", "other")
SOURCE_KINDS = ("props", "asset", "authored")

#: The categories of docs/production.md section 2 one still can evidence.
CATEGORIES = ("visual_quality", "cinematography", "graphics_overlays", "overall_production")

ELEMENTS_ROLE = (
    "You are the art director locking the look of one film before it is "
    "authored. You choose the few visual elements one still must present so "
    "an operator can approve the look: the hero subject, its environment and "
    "key light, the materials that matter, the palette as roles, one type "
    "sample, the camera language. You choose from what the project holds -- "
    "the treatment, the screenplay, the fetched props, the licensed images, "
    "what the engine proved -- never from what you would like it to hold, "
    "and for each element you say why it is the most qualified candidate and "
    "where it comes from."
)

REVIEW_ROLE = (
    "You are the quality reviewer of docs/production.md looking at a film's "
    "style frame before anything is authored: one still that must present "
    "the hero in its environment under its key light, with the materials, "
    "the palette roles, a type sample and the camera language, in the chosen "
    "treatment. You judge it by professional standards on " + ", ".join(CATEGORIES)
    + ", and you are especially critical of " + vfx.CRITICAL_OF + ". A 9 would "
    "not look out of place on a channel people subscribe to; 8 is competent "
    "and unremarkable; 6 is functional; 3 is a document that renders. Every "
    "finding names what is visible, why it lowers quality, and the fix in the "
    "document's own terms (camera, light, material, geometry, layout) that the "
    "author can apply without asking you anything."
)

ELEMENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "hero": {"type": "string"},
        "treatment": {"type": "string"},
        "camera": {"type": "string"},
        "elements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rank": {"type": "integer", "minimum": 1},
                    "kind": {"type": "string", "enum": list(ELEMENT_KINDS)},
                    "name": {"type": "string"},
                    "shows": {"type": "string"},
                    "why": {"type": "string"},
                    "source_kind": {"type": "string", "enum": list(SOURCE_KINDS)},
                    "source": {"type": "string"},
                    "file": {"type": "string"},
                    "licence": {"type": "string"},
                    "credit": {"type": "string"},
                    "sample": {"type": "string"},
                },
                "required": ["rank", "kind", "name", "shows", "why", "source_kind", "source"],
            },
        },
        "notes_for_author": {"type": "string"},
    },
    "required": ["hero", "treatment", "elements", "camera"],
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "seen": {"type": "string"},
        "scores": {
            "type": "object",
            "properties": {c: {"type": "number", "minimum": 0, "maximum": 10}
                           for c in CATEGORIES},
            "required": list(CATEGORIES),
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "problem": {"type": "string"},
                    "fix": {"type": "string"},
                    "severity": {"type": "string", "enum": ["blocking", "major", "minor"]},
                },
                "required": ["category", "problem", "fix", "severity"],
            },
        },
        "elements_missing": {"type": "array", "items": {"type": "string"}},
        "score": {"type": "number", "minimum": 0, "maximum": 10},
        "summary": {"type": "string"},
    },
    "required": ["seen", "scores", "findings", "score", "summary"],
}


class KeyframeError(RuntimeError):
    """Nothing to approve, or an approval that cannot be recorded."""


# --------------------------------------------------------------------------- #
# the record and the approval
# --------------------------------------------------------------------------- #

def record_path(root: Path) -> Path:
    return Path(root) / workspace.ARTIFACTS["keyframe"]


def load_record(root: Path) -> dict:
    return read_json(record_path(root), warn=False) or {}


def document_digest(path: Path) -> str:
    """What an approval is tied to: the document's bytes."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _epoch(stamp: str) -> float:
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except (TypeError, ValueError):
        return 0.0


def operator_notes(record: dict) -> list[dict]:
    rows = record.get("operator_notes") or []
    return [r for r in rows if isinstance(r, dict) and r.get("notes")]


def approval_state(root: Path) -> dict:
    """none | pending | approved | rejected | stale, with what the record says.

    `stale` is an approval whose document has changed since: the digest no
    longer matches, so the look on disk is not the one that was approved.
    """
    root = Path(root)
    record = load_record(root)
    approval = record.get("approval") or {}
    state = str(approval.get("state") or "")
    out = {"state": "none", "at": "", "notes": "", "digest": "", "score": record.get("score"),
           "epoch": 0.0, "document": record.get("document", ""), "frame": record.get("frame", "")}
    if not record or state not in ("pending", "approved", "rejected"):
        return out
    out.update(state=state, at=str(approval.get("at") or ""),
               notes=str(approval.get("notes") or ""), digest=str(approval.get("digest") or ""),
               epoch=_epoch(str(approval.get("at") or "")))
    if state == "approved":
        current = document_digest(root / (record.get("document") or f"{KEYFRAME_DIR}/{DOC_NAME}"))
        if not current or current != out["digest"]:
            out["state"] = "stale"
    return out


def approved_keyframe(root: Path) -> dict | None:
    """The approved look, for `yt vfx`: the document, the frame and the
    elements -- or None when there is no approval, or the document changed."""
    state = approval_state(root)
    if state["state"] != "approved":
        return None
    record = load_record(root)
    return {"document": record.get("document") or f"{KEYFRAME_DIR}/{DOC_NAME}",
            "frame": record.get("frame") or f"{KEYFRAME_DIR}/{FRAME_NAME}",
            "elements": record.get("elements") or [], "hero": record.get("hero", ""),
            "camera": record.get("camera", ""), "treatment": record.get("treatment", ""),
            "approved_at": state["at"], "digest": state["digest"]}


def _record_for_approval(root: Path) -> tuple[dict, Path]:
    record = load_record(root)
    doc = Path(root) / (record.get("document") or f"{KEYFRAME_DIR}/{DOC_NAME}")
    if not record or not doc.is_file():
        raise KeyframeError("there is no keyframe to approve or reject; run `yt keyframe` first")
    return record, doc


def approve(root: Path) -> dict:
    """Record the operator's approval of the keyframe on disk, tied to the
    document's digest; a later change to the document makes it stale."""
    record, doc = _record_for_approval(root)
    record["approval"] = {"state": "approved", "notes": "", "digest": document_digest(doc),
                          "at": _now()}
    write_json(record_path(root), record)
    write_sheet(Path(root), record)
    return record


def reject(root: Path, notes: str) -> dict:
    """Record a rejection and the operator's notes; the next run feeds them
    to both model calls as instructions that outrank everything else."""
    record, doc = _record_for_approval(root)
    notes = " ".join(str(notes or "").split())
    record["approval"] = {"state": "rejected", "notes": notes, "digest": document_digest(doc),
                          "at": _now()}
    rows = operator_notes(record)
    if notes:
        rows.append({"at": _now(), "notes": notes})
    record["operator_notes"] = rows
    write_json(record_path(root), record)
    write_sheet(Path(root), record)
    return record


# --------------------------------------------------------------------------- #
# the elements
# --------------------------------------------------------------------------- #

def rank_elements(raw: list, root: Path) -> tuple[list[dict], list[str]]:
    """The model's elements in rank order, capped at MAX_ELEMENTS, with
    every file checked against the project: an element naming a file that
    is not there becomes `authored` and the finding says so."""
    rows = [dict(e) for e in raw if isinstance(e, dict) and e.get("name")]
    rows.sort(key=lambda e: (float(e.get("rank") or 999), str(e.get("name"))))
    findings: list[str] = []
    kept: list[dict] = []
    for n, e in enumerate(rows[:MAX_ELEMENTS], 1):
        e["rank"] = n
        if e.get("kind") not in ELEMENT_KINDS:
            e["kind"] = "other"
        if e.get("source_kind") not in SOURCE_KINDS:
            e["source_kind"] = "authored"
        file = str(e.get("file") or "").strip()
        if file and not (Path(root) / file).is_file():
            findings.append(f"warning: element {e['name']!r} names {file}, which the project "
                            "does not hold; it is treated as authored")
            e.pop("file", None)
            e["source_kind"] = "authored"
        elif not file:
            e.pop("file", None)
        kept.append(e)
    if len(rows) > MAX_ELEMENTS:
        findings.append(f"note: {len(rows) - MAX_ELEMENTS} element(s) past the cap of "
                        f"{MAX_ELEMENTS} were dropped: "
                        + ", ".join(str(e.get("name")) for e in rows[MAX_ELEMENTS:]))
    return kept, findings


def palette_roles(direction: dict | None) -> tuple[list[dict], str]:
    """The palette's semantic roles from design.py, each with its hex and the
    words the film put beside the colour, and where they came from."""
    d = design.Design.from_styles(direction, height=FRAME[1])
    assigned = design.assign(d.swatches)
    # A role the palette never named is filled from design.py's fallback so
    # the author's tokens are complete; it is not the film's palette, and the
    # swatch strip must not present it as one.
    rows = [{"role": role, "hex": sw.hex, "words": " ".join(sw.words.split()),
             "from": "styles.json" if sw in d.swatches else "fallback"}
            for role, sw in assigned.items()]
    return rows, d.source


def tokens_for(direction: dict | None) -> dict[str, str]:
    """The <styles> tokens the author references as var(--role)."""
    d = design.Design.from_styles(direction, height=FRAME[1])
    return {name.lstrip("-"): value for name, value in d.tokens().items()}


def direction_block(direction: dict | None) -> str:
    """The editorial treatment `yt styles` chose, as the vfx brief states it."""
    styles = (direction or {}).get("styles") or []
    if not styles:
        return ("No treatment has been chosen yet (`yt styles` has not run); say which "
                "treatment the frame presents and why.")
    parts = ["The editorial treatment for this video, best first (from `yt styles`; "
             f"message: {direction.get('message', '')}):"]
    for n, st in enumerate(styles[:3], 1):
        line = f"{n}. {st.get('name', '')} (fit {st.get('fit', '?')}/10): {st.get('shows_well', '')}"
        for key in ("palette", "typography", "motion"):
            if st.get(key):
                line += f"\n   {key}: {st[key]}"
        for shot in st.get("shots", [])[:5]:
            line += f"\n   planned shot: {shot}"
        parts.append(line)
    if direction.get("combination"):
        parts.append(f"How they combine: {direction['combination']}")
    return "\n".join(parts)


def script_facts(script: dict | None) -> dict:
    """What the screenplay puts on screen: the spine, each section's shots
    (picture, super, asset), every super for the type sample, the language."""
    script = script or {}
    sections = []
    supers: list[str] = []
    for section in script.get("sections") or []:
        if not isinstance(section, dict):
            continue
        shots = []
        for shot in section.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            shots.append({"label": shot.get("label"), "visual": str(shot.get("visual", ""))[:240],
                          "on_screen": shot.get("on_screen", ""), "asset": shot.get("asset", "")})
            if str(shot.get("on_screen") or "").strip():
                supers.append(str(shot["on_screen"]).strip())
        sections.append({"heading": section.get("heading", ""), "shots": shots[:6]})
    return {"logline": script.get("logline", ""), "promise": script.get("promise", ""),
            "payoff": script.get("payoff", ""), "lang": script.get("lang", ""),
            "sections": sections, "supers": supers[:12]}


def asset_rows(dossier: dict | None) -> list[dict]:
    """The research's licensed images an author may place: usable ones only."""
    rows = []
    for a in (dossier or {}).get("assets") or []:
        if not isinstance(a, dict) or not a.get("title"):
            continue
        if not (a.get("commercial_ok") or a.get("licence_verified")):
            continue
        rows.append({"title": a.get("title"), "licence": a.get("licence_verified") or a.get("licence"),
                     "credit": a.get("credit_line", ""), "file": a.get("file") or a.get("path") or "",
                     "use_for": str(a.get("use_for", ""))[:200]})
    return rows[:12]


def props_rows(root: Path) -> list[dict]:
    return [{"id": it.get("id"), "kind": it.get("kind"), "name": it.get("name"),
             "file": it.get("file"), "licence": it.get("licence"), "credit": it.get("credit"),
             "about": str(it.get("about", ""))[:200]} for it in props_items(root)]


def _notes_block(notes: list[dict]) -> str:
    if not notes:
        return ""
    return ("THE OPERATOR'S OWN INSTRUCTIONS, from rejecting the earlier keyframe. They "
            "outrank everything else below (the latest last, and it outranks the earlier "
            "ones):\n" + "\n".join(f"- ({n.get('at', '')}) {n['notes']}" for n in notes))


def _digest(*parts) -> str:
    blob = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# the author's brief
# --------------------------------------------------------------------------- #

def write_brief(ws: Workspace, *, idea: Idea, house_rules: str, direction: dict | None,
                elements: dict, roles: list[dict], tokens: dict[str, str],
                facts: dict, assets: list[dict], notes: list[dict]) -> Path:
    """What the author has to make and the facts it has: the elements, the
    roles as tokens, the type sample, the camera language, the props with
    paths relative to the workspace, the bands, the deliverable."""
    bands = design.bands(FRAME[1])
    root = ws.root
    lines = [f"# The keyframe: the look of \"{idea.title}\"", "",
             f"Video: {idea.title}", f"Premise: {idea.description}", "",
             f"Frame {FRAME[0]}x{FRAME[1]} at 30 fps, ONE second long, judged on ONE frame at "
             f"{STILL_AT:g} s. This is the film's style frame: one composed picture presenting "
             "its most qualified visual elements together, in the chosen treatment, so an "
             "operator can approve the look before the film is authored. Everything `yt vfx` "
             "authors afterwards inherits what this frame shows.", ""]
    if notes:
        lines += ["## The operator's instructions (outrank everything below)", "",
                  _notes_block(notes), ""]
    lines += ["## Mission", "", MISSION, "",
              "## Editorial treatment", "", direction_block(direction), ""]
    if house_rules:
        lines += ["## Standards", "", house_rules, ""]
    lines += ["## What the frame must present", "",
              f"Treatment: {elements.get('treatment', '')}",
              f"Hero: {elements.get('hero', '')}",
              f"Camera language: {elements.get('camera', '')}", ""]
    if elements.get("notes_for_author"):
        lines += [f"Art director's notes: {elements['notes_for_author']}", ""]
    lines += ["The elements, ranked by what the frame must prove (each must be visible, and "
              "each gets a small label in the band naming it and its source):", ""]
    for e in elements.get("elements", []):
        src = e.get("source", "")
        if e.get("file"):
            src += f" (`{os.path.relpath(root / e['file'], ws.dir)}`)"
        lines.append(f"{e.get('rank')}. **{e.get('name')}** ({e.get('kind')}; source: "
                     f"{e.get('source_kind')} -- {src})")
        lines.append(f"   Shows: {e.get('shows', '')}")
        lines.append(f"   Why: {e.get('why', '')}")
        if e.get("sample"):
            lines.append(f"   Sample text: \"{e['sample']}\"")
        if e.get("licence") or e.get("credit"):
            lines.append(f"   Licence: {e.get('licence', '')}; credit: {e.get('credit', '')}")
        lines.append("")
    sample = next((e.get("sample") for e in elements.get("elements", [])
                   if e.get("kind") == "typography" and e.get("sample")), "")
    if not sample:
        sample = (facts.get("supers") or [idea.title])[0]
    lines += ["## Palette roles and tokens", "",
              "Copy tokens.xml into <styles> and reference every colour as var(--role); the "
              "swatch strip shows each role the film's palette named as a small rect with its "
              "role name and the palette's own words beside it. A role marked `fallback` was "
              "filled by design.py because the palette never named it: use its token if you "
              "must, but it is not the film's palette and does not go in the strip. No literal "
              "hex anywhere in the document except inside <styles>.", ""]
    lines += [f"- --{r['role']}: {r['hex']}"
              + (f" -- {r['words']}" if r.get("words") and r.get("from") != "fallback" else "")
              + (" (fallback, not the film's palette)" if r.get("from") == "fallback" else "")
              for r in roles]
    lines += ["", "Tokens available (tokens.xml):", ""]
    lines += [f"- --{k}: {v}" for k, v in tokens.items()]
    primary = ((direction or {}).get("styles") or [{}])[0]
    lines += ["", "## Type sample", "",
              f"Set this text as the film's typography ({primary.get('typography', '') or 'as the treatment says'}): "
              f"\"{sample}\"" + (f" (language: {facts['lang']})" if facts.get("lang") else ""), ""]
    lines += ["## Composition", "",
              f"- The stage, rows {bands.stage.top}-{bands.stage.bottom}, holds the hero in its "
              "environment under the key light, with the materials of the surfaces that matter, "
              "framed in the camera language above, in the treatment: real 3D (object3D, one "
              "camera, lights, materials with maps) when the treatment is 3D; 2D or 2.5D "
              "constructs (shapes, images, layout groups, parallax through z) when it says so. "
              "Give the hero an id starting with `hero-`; the camera must frame it.",
              f"- An unobtrusive band -- the caption band, rows {bands.caption.top}-"
              f"{bands.caption.bottom}, or a side margin, never over the hero -- carries the "
              "palette swatches with their role names, the type sample, and one small label per "
              "element: `name · source` (the props id, the asset file, or `authored`). Small, "
              "quiet, legible; the band is a sheet's margin, not a title card.",
              f"- Rows {bands.super.top}-{bands.super.bottom} stay clear unless the treatment "
              "puts a super there.",
              "- Colours only through var(--role). Nouns, labels and the sample come from the "
              "elements and the film, never invented.", ""]
    usable = [a for a in assets if a.get("file")]
    if usable:
        lines += ["## Licensed images in the project you may use as <image> src", ""]
        lines += [f"- `{os.path.relpath(root / a['file'], ws.dir)}` -- {a.get('title', '')} "
                  f"({a.get('licence', '')}; credit: {a.get('credit', '')})" for a in usable]
        lines.append("")
    props = props_items(root)
    if props:
        hdris = [it for it in props if it.get("kind") == "hdri"]
        models = [it for it in props if it.get("kind") == "model"]
        others = [it for it in props if it.get("kind") not in ("hdri", "model")]
        lines += ["## Props (CC0, fetched by `yt props`; paths relative to this directory)", "",
                  "Confirm every attribute with `python3 sr.py describe light`, `describe mesh`, "
                  "`describe object3D` and `describe material` before writing it. An HDRI is "
                  "the default light rig (`<light type=\"dome\" environment=...>` plus one "
                  "directional key with `castShadow=\"true\"`); a model that fits a set piece is "
                  "placed as a `<mesh>` asset on an `object3D primitive=\"mesh\"`, never rebuilt "
                  "from primitives; large surfaces carry texture maps (`baseColorMap`, "
                  "`normalMap`, `metallicRoughnessMap`/`occlusionMap`, `uvScaleX`/`uvScaleY`); "
                  "a flat `baseColor` on a wall or any broad surface reads as flat-shaded CG.", ""]
        for title, rows in (("### HDRIs (environment maps)", hdris),
                            ("### Models (glTF with textures)", models),
                            ("### Other files", others)):
            if not rows:
                continue
            lines += [title, ""]
            for it in rows:
                rel = os.path.relpath(root / it["file"], ws.dir)
                lines.append(f"- `{rel}` ({it.get('kind', '')}) -- {it.get('about', '')} "
                             f"[{it.get('licence', '')}"
                             + (f", {it['credit']}" if it.get("credit") else "") + "]")
                maps = prop_textures(root, it)
                if maps:
                    lines.append("  maps, usable on any surface: "
                                 + ", ".join(f"`{os.path.relpath(m, ws.dir)}`" for m in maps))
            lines.append("")
    lines += ["## Deliverable", "",
              f"Write `{DOC_NAME}` in this directory: a complete scene-render document "
              f"(`<project width=\"{FRAME[0]}\" height=\"{FRAME[1]}\" fps=\"30\" duration=\"1\">`, "
              "styles with the tokens, assets, materials, symbols, composition, lights, output) "
              f"holding exactly ONE `<symbol id=\"{SYMBOL_ID}\" width=\"{FRAME[0]}\" "
              f"height=\"{FRAME[1]}\" duration=\"1\">` that composes everything -- the world "
              "and the band -- instanced once in the composition from 0 to 1 s, with the "
              "<lights> declared at scene level. Children of <scene> must appear in the "
              "schema's declared order (project, styles, output, assets, materials, symbols, "
              "composition, lights ...). Read examples.scene.xml first: every construct in it "
              "has rendered on this engine. Ask the schema with sr.py before guessing an "
              f"attribute. Run `python3 sr.py check {DOC_NAME}` and fix every error until it "
              f"reports 0 error(s); then `python3 sr.py frame {DOC_NAME}` renders the symbol "
              "and you Read the PNG: a black, flat, empty or wrong frame, a hero out of view "
              "or an unreadable band means the document is not done. Do not create any other "
              "file. The stage then renders the frame at "
              f"{STILL_AT:g} s as `{FRAME_NAME}` and a reviewer scores it against the "
              "publication bar; its findings come back to you if it falls short."]
    path = ws.dir / BRIEF_NAME
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# the checks and the render
# --------------------------------------------------------------------------- #

def check_document(ws: Workspace) -> tuple[list[str], list[str]]:
    """(symbol ids, findings) for the authored document: the SDK's read and
    check, then the engine's own validation when one is reachable."""
    return ws.validate(DOC_NAME, frames=False)


def engine_renderer(project_root: Path, host: str, *, verbose: bool = False):
    """The engine on the render host through `render.run_job` (job "still",
    which the router sends to the fast GPU host): one frame of the document
    at `at`, written to `out` inside the project; (ok, message)."""
    def render_frame(doc: Path, out: Path, at: float) -> tuple[bool, str]:
        cmd = ["scene-render", "render", str(doc), "-t", f"{at:g}", "-o", str(out)]
        binary = render.engine()
        try:
            if host:
                proc = render.run_job(cmd, Path(project_root), host, job="still", verbose=verbose)
            elif binary is not None:
                import subprocess
                cmd[0] = str(binary)
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            else:
                return False, "no engine reachable"
        except Exception as exc:    # ssh refused, rsync failed, the engine timed out
            return False, f"render host unreachable: {str(exc)[:200]}"
        message = (proc.stderr or proc.stdout or "").strip()
        return proc.returncode == 0 and Path(out).is_file(), message
    return render_frame


#: The seam: what the stage renders the frame with. A test puts a fake here.
RENDERER = engine_renderer


# --------------------------------------------------------------------------- #
# the stage
# --------------------------------------------------------------------------- #

class KeyframeStage:
    """Picks the elements, has the model compose the frame, renders and
    reviews it, and writes the record awaiting approval."""

    def __init__(self, project: workspace.Project, idea: Idea, *, model: str = "sonnet",
                 house: str = "", house_digest: dict | None = None, rounds: int = DEFAULT_ROUNDS,
                 bar: float = DEFAULT_BAR, budget_usd: float = 2.0, fresh: bool = False,
                 verbose: bool = True, host: str = "", work: Path | None = None,
                 script_path: str | None = None, styles_path: str | None = None) -> None:
        self.project = project
        self.root = Path(project.root)
        self.idea = idea
        self.model = model
        self.house = house
        self.house_digest = house_digest or {}
        self.rounds = max(0, int(rounds))
        self.bar = float(bar)
        self.budget_usd = budget_usd
        self.fresh = fresh
        self.verbose = verbose
        self.host = host
        self.work = Path(work) if work else None
        self.direction = read_json(project.source("styles", styles_path), warn=False) or {}
        self.script = read_json(project.source("script", script_path), warn=False) or {}
        self.dossier = read_json(project.source("dossier"), warn=False) or {}
        self.previous = load_record(self.root)
        self.notes = operator_notes(self.previous)
        self.usage = Usage()
        self.render = RENDERER(self.root, host, verbose=verbose)

    def _say(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr)

    def _harness(self, role: str) -> harness.Harness:
        return harness.Harness(self.root, "keyframe", model=self.model, role=role,
                               house=self.house, house_digest=self.house_digest,
                               fresh=self.fresh, verbose=self.verbose, timeout=1800.0)

    # -- 1. the elements ---------------------------------------------------

    def elements(self, picker: harness.Harness) -> tuple[dict, list[str]]:
        facts = script_facts(self.script)
        roles, source = palette_roles(self.direction)
        parts = [f"Choose the elements of the keyframe for \"{self.idea.title}\".", "",
                 self.idea.block()]
        if self.notes:
            parts += ["", _notes_block(self.notes)]
        parts += ["", direction_block(self.direction), "",
                  harness.brief("The screenplay: what is on screen, the supers, the language", facts),
                  harness.brief(f"Palette roles (design.py, from {source})", roles)]
        props = props_rows(self.root)
        parts.append(harness.brief("Props in the project (`yt props`, CC0; the file is the path "
                                   "relative to the project)", props)
                     if props else "Props: none fetched (`yt props` has not run).")
        assets = asset_rows(self.dossier)
        parts.append(harness.brief("Licensed images the research found", assets)
                     if assets else "Licensed images: none usable.")
        stack = assess.stack_brief(self.root, work=self.work, idea=self.idea)
        parts.append(stack or f"The production stack is {STACK}; no assessment has run.")
        parts += ["",
                  "From these and nothing else, pick what ONE still must present so an operator "
                  "can approve the look: the hero subject of the film; its environment and key "
                  "light; the materials of the two or three surfaces that matter most; the "
                  "palette roles as swatches (one element); one typography sample -- a super in "
                  "the film's language when the screenplay has one, else the title (put the text "
                  "in `sample`); and the camera language. For each say what the frame shows of "
                  "it, why it is the most qualified candidate over the alternatives, and its "
                  "source: `props` with the props id in `source` and its `file`, `licence` and "
                  "`credit`; `asset` with the image's title in `source` and its `file`; or "
                  "`authored` when the author builds it from the schema's primitives and "
                  "materials. A props model that matches only a word of what the film needs is "
                  "not the hero; say so and author the hero. Rank by what the frame must prove "
                  f"to an operator, hero first; at most {MAX_ELEMENTS} elements. In `treatment` "
                  "say 2D, 2.5D or 3D and the treatment's name; in `camera` the lens, distance, "
                  "height and movement language in one or two lines."]
        answer = picker.decide("elements", "\n".join(parts), ELEMENTS_SCHEMA)
        ranked, findings = rank_elements(answer.get("elements") or [], self.root)
        chosen = {"hero": str(answer.get("hero", "")), "treatment": str(answer.get("treatment", "")),
                  "camera": str(answer.get("camera", "")),
                  "notes_for_author": str(answer.get("notes_for_author") or ""),
                  "elements": ranked}
        return chosen, findings

    # -- 2. the composition ------------------------------------------------

    def _stream(self, engine, prompt: str) -> int:
        calls = 0
        try:
            system = harness.with_feedback(AUTHOR_SYSTEM, self.root, "keyframe")
            for event in engine.stream(prompt, system=system):
                if event.subtype == "tool_use" and event.tool_input:
                    calls += 1
                    what = event.tool_input.get("command") or event.tool_input.get("file_path") or ""
                    self._say(f"    author {event.tool_name}: {str(what)[:90]}")
        except ClaudeError as exc:
            self._say(f"    author failed: {exc}")
        finally:
            _merge_usage(self.usage, engine.usage)
        return calls

    def _review(self, reviewer: harness.Harness, ws: Workspace, chosen: dict,
                checks: list[str], round_no: int) -> dict | None:
        prompt = "\n\n".join([
            f"Review round {round_no} of the keyframe of \"{self.idea.title}\". Read "
            f"`{FRAME_NAME}` in this directory: the film's style frame, one still at "
            f"{STILL_AT:g} s of `{DOC_NAME}`, rendered by the engine. Judge it by the "
            "publication bar as the look every shot of the film will inherit: is the hero "
            "there, in its environment, under its key light, with materials that read; do the "
            "swatches, the type sample and the labels sit in a quiet band; is it the treatment "
            "named. Score each category 0-10 and give one score for the frame.",
            harness.brief("What the frame must present (the elements, by rank)",
                          {"treatment": chosen.get("treatment"), "hero": chosen.get("hero"),
                           "camera": chosen.get("camera"),
                           "elements": [{k: e.get(k) for k in ("rank", "kind", "name", "shows",
                                                               "source_kind", "source")}
                                        for e in chosen.get("elements", [])]}),
            harness.brief("What the checks found before you looked", checks or ["nothing"]),
            "The editorial treatment:\n" + direction_block(self.direction),
            "List under `elements_missing` every element the frame does not show. Every "
            "finding must carry a fix the author can apply in the document: which camera "
            "key, light, material, geometry or layout to change, and to what.",
        ])
        try:
            return reviewer.inspect(f"review-r{round_no}", prompt, REVIEW_SCHEMA, cwd=ws.dir)
        except harness.HarnessError as exc:
            self._say(f"    review failed: {exc}")
            return None

    @staticmethod
    def _review_listing(review: dict | None) -> str:
        if not review:
            return ""
        lines = [f"The reviewer scored the frame {review.get('score', '?')}/10 "
                 f"({', '.join(f'{k} {v}' for k, v in (review.get('scores') or {}).items())}): "
                 f"{review.get('summary', '')}"]
        if review.get("elements_missing"):
            lines.append("Elements the frame does not show: " + "; ".join(
                str(m) for m in review["elements_missing"]))
        lines += ["", "Requirements for this round (verbatim from the review; each must be "
                  "answered in the document and the answer visible in the frame):"]
        for f in review.get("findings", []):
            lines.append(f"- [{f.get('severity', '')}] ({f.get('category', '')}): "
                         f"{f.get('problem', '')}\n  REQUIRED FIX: {f.get('fix', '')}")
        return "\n".join(lines)

    def _gate(self, ws: Workspace, reviewer: harness.Harness, chosen: dict,
              round_no: int) -> dict:
        """Check the document, render the frame, run the eyeless checks, then
        the review; one round's record."""
        symbols, findings = check_document(ws)
        errors = [f for f in findings if f.startswith("error")]
        png = ws.dir / FRAME_NAME
        if not errors:
            if png.exists():
                png.unlink()
            ok, message = self.render(ws.dir / DOC_NAME, png, STILL_AT)
            if not ok or not png.is_file():
                findings.append("error: the frame was not rendered: "
                                + (message.strip()[-300:] or "the engine wrote no PNG"))
            else:
                findings += framecheck.judge_frame(png.read_bytes(), STILL_AT, SYMBOL_ID)
            errors = [f for f in findings if f.startswith("error")]
        checks = [f for f in findings if not f.startswith("note")]
        review = None if errors else self._review(reviewer, ws, chosen, checks, round_no)
        score = float(review["score"]) if review and review.get("score") is not None else None
        self._say(f"  round {round_no}: {len(symbols)} symbol(s), {len(errors)} error(s)"
                  + (f", review {score:g}/10" if score is not None else ", no review"))
        return {"round": round_no, "symbols": symbols, "errors": errors, "findings": checks,
                "score": score, "scores": (review or {}).get("scores") or {},
                "review": review, "frame": png.is_file()}

    def compose(self, chosen: dict, element_findings: list[str], picker: harness.Harness) -> dict:
        ws = Workspace(self.root, KEYFRAME_DIR)
        tokens = tokens_for(self.direction)
        ws.prepare(tokens=tokens, frame=FRAME)
        roles, _ = palette_roles(self.direction)
        facts = script_facts(self.script)
        brief = write_brief(ws, idea=self.idea, house_rules=self.house, direction=self.direction,
                            elements=chosen, roles=roles, tokens=tokens, facts=facts,
                            assets=asset_rows(self.dossier), notes=self.notes)
        inputs = _digest(chosen, self.notes, tokens, self.house_digest)
        engine = harness.ENGINE(EngineConfig(
            tools=AUTHOR_TOOLS, model=self.model, permission_mode="dontAsk", cwd=ws.dir,
            max_turns_budget_usd=self.budget_usd, timeout=1800.0))
        reviewer = self._harness(REVIEW_ROLE)
        doc = ws.dir / DOC_NAME
        reuse = (doc.is_file() and not self.fresh
                 and self.previous.get("inputs_digest") == inputs)
        tool_calls = 0
        if reuse:
            self._say(f"  {DOC_NAME} exists for these inputs; not authored again")
        else:
            self._say(f"  authoring {DOC_NAME} in {ws.rel}/ (up to ${self.budget_usd:g}, "
                      f"bar {self.bar:g}/10, {self.rounds} revision round(s))")
            tool_calls += self._stream(engine, (
                f"Read {brief.name}, then examples.scene.xml and schema.md, and author "
                f"{DOC_NAME} as the brief says: one symbol `{SYMBOL_ID}` composing the hero in "
                "its environment with the lights and materials, in the treatment, and the band "
                f"with the swatches, the type sample and the element labels. Check it with "
                f"`python3 sr.py check {DOC_NAME}` and fix every error; then `python3 sr.py "
                f"frame {DOC_NAME}` and Read the frame it writes until it shows the look. When "
                "check reports 0 error(s) and the frame shows the look, reply with one line: "
                "the file name and the symbol id."))
        rounds: list[dict] = []
        round_no = 0
        while True:
            rounds.append(self._gate(ws, reviewer, chosen, round_no))
            last = rounds[-1]
            short = last["score"] is not None and last["score"] < self.bar
            if not last["errors"] and not short:
                break
            if last["score"] is None and not last["errors"]:
                break               # the review itself failed; nothing to hand back
            if round_no >= self.rounds:
                break
            round_no += 1
            why = (last["errors"][0][:100] if last["errors"]
                   else f"review {last['score']:g}/10 under the bar {self.bar:g}")
            self._say(f"  revision {round_no}: {why}")
            listing = "\n".join(f"- {f}" for f in last["findings"])
            prompt = (f"{DOC_NAME} is not done."
                      + (f" The gate reports:\n{listing}\n\n" if listing else "\n\n")
                      + (self._review_listing(last["review"]) + "\n\n" if last["review"] else "")
                      + (("The operator's instructions still outrank everything:\n"
                          + _notes_block(self.notes) + "\n\n") if self.notes else "")
                      + f"Fix the document -- the same elements, one symbol `{SYMBOL_ID}`, the "
                      f"hero kept -- run `python3 sr.py check {DOC_NAME}` until it reports 0 "
                      f"error(s), then `python3 sr.py frame {DOC_NAME}` and Read the frame; "
                      "iterate until it shows the look and the findings above are answered. "
                      "Reply with one line.")
            tool_calls += self._stream(engine, prompt)
        last = rounds[-1]
        frame_ok = bool(last["frame"]) and not last["errors"]
        return {
            "document": f"{ws.rel}/{DOC_NAME}" if doc.is_file() else "",
            "frame": f"{ws.rel}/{FRAME_NAME}" if frame_ok else "",
            "brief": f"{ws.rel}/{BRIEF_NAME}",
            "rounds": [{k: v for k, v in r.items() if k != "frame"} for r in rounds],
            "score": last["score"], "bar": self.bar, "rounds_allowed": self.rounds,
            "at_bar": last["score"] is not None and last["score"] >= self.bar and frame_ok,
            "element_findings": element_findings, "inputs_digest": inputs,
            "tool_calls": tool_calls, "reused_document": reuse,
            "engine": {"elements": picker.report(), "review": reviewer.report()},
            "cost_usd": round(self.usage.cost_usd + picker.usage.cost_usd
                              + reviewer.usage.cost_usd, 4),
        }

    def run(self) -> dict:
        picker = self._harness(ELEMENTS_ROLE)
        chosen, element_findings = self.elements(picker)
        self._say(f"  {len(chosen['elements'])} element(s): "
                  + ", ".join(f"{e['name']} ({e['source_kind']})" for e in chosen["elements"]))
        for f in element_findings:
            self._say(f"  {f}")
        result = self.compose(chosen, element_findings, picker)
        record = {"idea": compact(self.idea), "stack": STACK, "house": self.house_digest,
                  "treatment": chosen["treatment"], "hero": chosen["hero"],
                  "camera": chosen["camera"], "notes_for_author": chosen["notes_for_author"],
                  "elements": chosen["elements"], **result,
                  "operator_notes": self.notes,
                  "approval": {"state": "pending", "notes": "",
                               "digest": document_digest(self.root / result["document"])
                               if result["document"] else "", "at": _now()},
                  "when": _now()}
        write_json(record_path(self.root), record)
        write_sheet(self.root, record)
        return record


# --------------------------------------------------------------------------- #
# the sheet
# --------------------------------------------------------------------------- #

def write_sheet(root: Path, record: dict) -> Path:
    """keyframe.md: the frame, the element table with sources and licences,
    the scores per round, the approval and the commands."""
    approval = record.get("approval") or {}
    title = (record.get("idea") or {}).get("title", root.name)
    lines = [f"# Keyframe — {title}", "",
             f"**Approval: {approval.get('state', 'none')}**"
             + (f" ({approval.get('at', '')})" if approval.get("at") else "")
             + (f" — notes: {approval['notes']}" if approval.get("notes") else ""), ""]
    if record.get("frame"):
        lines += [f"![keyframe]({record['frame']})", "", f"Frame: `{record['frame']}` · "
                  f"document: `{record.get('document', '')}`", ""]
    else:
        lines += ["No frame was rendered; see the rounds below.", ""]
    lines += [f"Treatment: {record.get('treatment', '')}  ",
              f"Hero: {record.get('hero', '')}  ",
              f"Camera: {record.get('camera', '')}", "",
              "## Elements", "",
              "| # | kind | element | shows | source | licence / credit | why |",
              "|---|---|---|---|---|---|---|"]
    for e in record.get("elements", []):
        src = f"{e.get('source_kind', '')}: {e.get('source', '')}"
        if e.get("file"):
            src += f" (`{e['file']}`)"
        lic = " / ".join(x for x in (e.get("licence", ""), e.get("credit", "")) if x)
        cell = lambda s: " ".join(str(s or "").split()).replace("|", "/")  # noqa: E731
        lines.append(f"| {e.get('rank', '')} | {e.get('kind', '')} | {cell(e.get('name'))} | "
                     f"{cell(e.get('shows'))} | {cell(src)} | {cell(lic)} | {cell(e.get('why'))} |")
    for f in record.get("element_findings") or []:
        lines.append(f"\n- {f}")
    lines += ["", "## Rounds", "",
              f"Bar {record.get('bar', DEFAULT_BAR)}/10; {record.get('rounds_allowed', '?')} "
              "revision round(s) allowed.", "",
              "| round | score | " + " | ".join(CATEGORIES) + " | errors | findings |",
              "|---|---|" + "---|" * len(CATEGORIES) + "---|---|"]
    for r in record.get("rounds", []):
        scores = r.get("scores") or {}
        lines.append(f"| {r.get('round')} | {r.get('score') if r.get('score') is not None else '-'} | "
                     + " | ".join(str(scores.get(c, "-")) for c in CATEGORIES)
                     + f" | {len(r.get('errors') or [])} | {len(r.get('findings') or [])} |")
    last = (record.get("rounds") or [{}])[-1]
    review = last.get("review") or {}
    if review:
        lines += ["", f"Latest review: {review.get('summary', '')}", ""]
        for f in review.get("findings", []):
            lines.append(f"- [{f.get('severity', '')}] {f.get('category', '')}: "
                         f"{f.get('problem', '')} — fix: {f.get('fix', '')}")
        if review.get("elements_missing"):
            lines.append("- missing: " + "; ".join(str(m) for m in review["elements_missing"]))
    if last.get("findings"):
        lines += ["", "Checks:", ""] + [f"- {f}" for f in last["findings"]]
    notes = operator_notes(record)
    if notes:
        lines += ["", "## Operator's notes", ""] + [f"- ({n.get('at', '')}) {n['notes']}"
                                                   for n in notes]
    lines += ["", "## Commands", "",
              f"    yt keyframe --project {root} --approve",
              f"    yt keyframe --project {root} --reject \"what is wrong, in your words\"",
              f"    yt keyframe --project {root} --fresh      # pick and compose again", ""]
    path = Path(root) / "keyframe.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def house_rules(args, project) -> tuple[str, dict]:
    """`## all` plus `## keyframe`; with no keyframe section the `## vfx`
    section applies, because the keyframe is the look the vfx inherits."""
    work = workspace.work_dir(args) if getattr(args, "work", None) else None
    loaded = house.load(work, project.root, explicit=getattr(args, "house", None),
                        enabled=not getattr(args, "no_house", False))
    own = loaded.sections.get("keyframe") or loaded.sections.get("vfx") or ""
    parts = [p for p in (loaded.sections.get(house.ALL, ""), own) if p]
    if not parts:
        return "", loaded.digest()
    return ("The standards this channel holds every video to. They bind what you make "
            "here, and the operator's own notes for this run outrank them:\n\n"
            + "\n\n".join(parts)), loaded.digest()


def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "keyframe", parents=list(parents),
        help="lock the look: one composed frame of the film's elements, for approval",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS,
                   help="revision rounds the author gets after the first frame when the "
                        f"review is under the bar (default {DEFAULT_ROUNDS})")
    p.add_argument("--bar", type=float, default=DEFAULT_BAR,
                   help=f"review score (0-10) under which the frame goes back (default {DEFAULT_BAR:g})")
    p.add_argument("--budget-usd", type=float, default=2.0, dest="budget_usd",
                   help="spend cap for one authoring session (default 2)")
    p.add_argument("--fresh", action="store_true",
                   help="pick the elements and compose again, ignoring cached decisions")
    p.add_argument("--approve", action="store_true",
                   help="record the operator's approval of the keyframe on disk")
    p.add_argument("--reject", metavar="NOTES",
                   help="record a rejection with notes; the next run obeys them first")
    p.add_argument("--host", metavar="USER@HOST",
                   help="render the frame on this machine (default: the fast still host)")
    workspace.add_inputs(p, "script", "styles")
    p.set_defaults(run=run)
    return p


def run(args) -> int:
    idea, project = workspace.resolve(args, inputs=("script", "styles"))
    verbose = not args.quiet
    if args.approve and args.reject is not None:
        print("error: --approve and --reject together make no sense", file=sys.stderr)
        return 1
    if args.approve or args.reject is not None:
        try:
            record = approve(project.root) if args.approve else reject(project.root, args.reject)
        except KeyframeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        state = record["approval"]
        print(f"{idea.title} — keyframe {state['state']}"
              + (f": {state['notes']}" if state.get("notes") else ""))
        print(f"  {project.root / record.get('frame', '')}" if record.get("frame") else "")
        if state["state"] == "rejected":
            print(f"  next: yt keyframe --project {project.root}   (your notes lead both calls)")
        else:
            print(f"  next: yt vfx --project {project.root}   (the author inherits this look)")
        return 0

    host = render.remote_host(args, "still")
    if not host and render.engine() is None:
        print("error: no engine reachable (RS_RENDER_HOSTS unset, no local binary); the "
              "keyframe is a rendered frame, so there is nothing to make", file=sys.stderr)
        return 1
    rules, digest = house_rules(args, project)
    if verbose:
        print(f"keyframe for {STACK}: {idea.title}\n  -> {project.root}/{KEYFRAME_DIR}/"
              + (f"\n  standards: {', '.join(digest['sources'])}" if rules else "")
              + f"\n  frame on {host or 'this machine'}", file=sys.stderr)
    stage = KeyframeStage(project, idea, model=args.model, house=rules, house_digest=digest,
                          rounds=args.rounds, bar=args.bar, budget_usd=args.budget_usd,
                          fresh=args.fresh, verbose=verbose, host=host,
                          work=workspace.work_dir(args) if getattr(args, "work", None) else None,
                          script_path=workspace.override(args, "script"),
                          styles_path=workspace.override(args, "styles"))
    try:
        record = stage.run()
    except (harness.HarnessError, ClaudeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"\n{idea.title} — KEYFRAME")
    for e in record["elements"]:
        print(f"  {e['rank']}. {e['kind']:<12} {e['name']}  [{e['source_kind']}: {e['source']}]")
    scores = " · ".join(f"{r['round']}: {r['score'] if r['score'] is not None else '-'}"
                        for r in record["rounds"])
    print(f"  rounds {scores} · bar {record['bar']:g}"
          + (" · at the bar" if record["at_bar"] else " · under the bar"))
    for f in record["rounds"][-1]["findings"][:8]:
        print(f"  {f}")
    print(f"\n{project.root}/\n  {record_path(project.root).name}\n  keyframe.md")
    if record["frame"]:
        print(f"\nframe: {project.root / record['frame']}")
        print(f"approve: yt keyframe --project {project.root} --approve")
        print(f"reject:  yt keyframe --project {project.root} --reject \"notes\"")
    else:
        print("\nno frame was rendered; fix the errors above and run again", file=sys.stderr)
    print(f"{stage.usage}", file=sys.stderr)
    return 0 if record["frame"] else 1
