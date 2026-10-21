#!/usr/bin/env python3
"""Lock the look before `yt vfx` spends: the film's lead section, for the operator's approval.

    yt keyframe --title monty                 # plan the film, author section 1 once
    yt keyframe --title monty --approve       # record the approval on disk
    yt keyframe --title monty --reject "the labels are too small; a warmer key"
    yt keyframe --title monty --section 3     # take the look from another section
    yt keyframe --title monty --sheet         # the older separate style frame

A studio locks a style frame before animation starts. Here that frame is
part of the film: `yt vfx`'s own planner plans every section (the plan
`yt vfx` reuses) and authors the lead section ONCE, judged by the mechanical
gate alone -- the program builds twice to the same document, the engine
validates it, proof frames render at every shot, the frame, legibility and
geometry rules run -- with no vision review and no revision round paid for
before the operator has seen it. The frame showing the most becomes
keyframe/keyframe.png and every proof frame goes on keyframe/frames.png.
`--approve` records the approval against the section document's digest;
`yt vfx` then carries the section forward (`vfx.look_section`) and every
other author's brief names its program as the look to copy: its palette and
type constants, the helpers that build its labels, formulas and recurring
objects, and its camera language. `--reject "notes"` puts the notes at the
top of the lead section's brief on the next run. `yt run` stops before
`yt vfx` while a look awaits approval, and runs the keyframe again when it
was rejected. The keyframe runs after `yt retime`, so the section is timed
to the measured narration it will play under.

With `--sheet` the stage composes a separate style frame instead, as it did
before: one 1920x1080 picture of the video's most qualified visual elements,
reviewed in rounds. That frame is not part of the film. What follows
describes that path.

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
import shutil
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from claude_engine import ClaudeError, EngineConfig, Usage
from . import assess, design, fonts, framecheck, harness, house, render, sdkkit, vfx, workspace
from .script import Shot, visible_text
from .core import Idea, _merge_usage, compact, read_json, write_json
from .vfx import (AUTHOR_TOOLS, FRAME, MISSION, STACK, Workspace,
                  prop_textures, props_items)

#: The author's session timeout, the 80% mark of its round (sdkkit.stream_round).
AUTHOR_TIMEOUT = 1800.0

#: The keyframe's author composes by program, like every author (sdkkit).
AUTHOR_SYSTEM = sdkkit.AUTHOR_SYSTEM

__all__ = ["KeyframeStage", "KeyframeError", "RENDERER", "approve", "reject",
           "approval_state", "approved_keyframe", "load_record", "rank_elements",
           "check_document", "document_digest", "register", "run"]

#: Where the author works, under the project; the document and the frame.
KEYFRAME_DIR = "keyframe"
DOC_NAME = "keyframe.scene.xml"
PROGRAM_NAME = "keyframe.py"
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
    "author can apply without asking you anything. The author was handed the same "
    "checklist you judge by, and ran its mechanical items itself; spend your "
    "attention on the `[review]` items:\n" + framecheck.checklist_text()
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
    """none | pending | approved | rejected | stale | failed, with what the record says.

    `stale` is an approval whose document has changed since: the digest no
    longer matches, so the look on disk is not the one that was approved.
    """
    root = Path(root)
    record = load_record(root)
    approval = record.get("approval") or {}
    state = str(approval.get("state") or "")
    out = {"state": "none", "at": "", "notes": "", "digest": "", "score": record.get("score"),
           "epoch": 0.0, "document": record.get("document", ""), "frame": record.get("frame", "")}
    if not record or state not in ("pending", "approved", "rejected", "failed"):
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
            "source": record.get("source") or "sheet", "section": record.get("section"),
            "program": record.get("program") or "", "sheet": record.get("sheet") or "",
            "record": record.get("record"), "frame_size": record.get("frame_size"),
            "look_program": record.get("look_program") or "",
            "approved_at": state["at"], "digest": state["digest"]}


def look_needs_redo(root: Path) -> bool:
    """A look taken from the film that cannot be approved: its pass failed
    (gate errors, no frame) or its document is gone. `yt run` authors it again."""
    record = load_record(root)
    if record.get("source") != "section":
        return False
    if not (Path(root) / str(record.get("document") or "")).is_file():
        return True
    return (record.get("approval") or {}).get("state") == "failed" or bool(approvable(record))


def film_carries_look(root: Path, vfx_data: dict | None = None) -> bool:
    """Whether the animation holds the approved look from the film: its
    section in vfx.json is the approved document, or a sound authoring of it
    the operator asked for under this look (`reauthored_look`). True when there
    is no such look or no animation yet: nothing then waits on `--relook`."""
    look = approved_keyframe(root)
    record = load_record(root)
    if not look or record.get("source") != "section" or record.get("section") is None:
        return True
    if vfx_data is None:
        vfx_data = read_json(Path(root) / workspace.ARTIFACTS["vfx"], warn=False)
    if not vfx_data:
        return True
    row = next((a for a in vfx_data.get("authored") or []
                if isinstance(a, dict) and a.get("n") == record["section"]), None)
    if row is None:
        return False
    return (document_digest(Path(root) / str(row.get("file") or "")) == look["digest"]
            or row.get("reauthored_look") == look["digest"])


def approvable(record: dict) -> str:
    """"" when a look from the film can be approved, else why not."""
    if record.get("source") != "section":
        return ""
    if record.get("errors"):
        return f"its gate found {len(record['errors'])} error(s): {record['errors'][0][:160]}"
    if not record.get("frame"):
        return "no proof frame was rendered"
    return ""


def _record_for_approval(root: Path) -> tuple[dict, Path]:
    record = load_record(root)
    doc = Path(root) / (record.get("document") or f"{KEYFRAME_DIR}/{DOC_NAME}")
    if not record or not doc.is_file():
        raise KeyframeError("there is no keyframe to approve or reject; run `yt keyframe` first")
    return record, doc


def record_mismatch(root: Path, record: dict) -> str:
    """Why the record does not describe the files on disk, or "". The record
    carries the digests of the document and the program as of its last round;
    a run stopped mid-round leaves files that moved on without it. A record
    from before the digests is not compared."""
    for key, path_key, what in (("document_digest", "document", "document"),
                                ("program_digest", "program", "program")):
        want, rel = record.get(key), record.get(path_key)
        if want is None or not rel:
            continue
        if document_digest(Path(root) / rel) != want:
            return f"the {what} {rel} changed after the record was written (a stopped run?)"
    return ""


def approve(root: Path) -> dict:
    """Record the operator's approval of the keyframe on disk, tied to the
    document's digest; a later change to the document makes it stale. A
    record that no longer matches the document and program is refused."""
    record, doc = _record_for_approval(root)
    mixed = record_mismatch(root, record)
    if mixed:
        raise KeyframeError(f"{mixed}; run `yt keyframe` again before approving")
    refused = approvable(record)
    if refused:
        raise KeyframeError(f"the look cannot be approved: {refused}; run `yt keyframe` again")
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
            words = visible_text(Shot.from_raw(shot))
            shots.append({"label": shot.get("label"), "visual": str(shot.get("visual", ""))[:240],
                          "super": words, "direction": shot.get("on_screen", ""),
                          "asset": shot.get("asset", "")})
            if words:
                supers.append(words)
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
                     "kind": a.get("kind") or "image", "paper": a.get("paper", ""),
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
                facts: dict, assets: list[dict], notes: list[dict],
                constraints: list[dict] | None = None) -> Path:
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
    lines += vfx.constraint_lines(constraints or [])
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
              "The program adds the tokens (tokens.xml) to the document's <styles>, and "
              "every colour references them as var(--role); the "
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
    lines += vfx.research_asset_lines(assets, root, ws.dir)
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
    lines += ["Fonts: text may use only the families the repo ships ("
              + ", ".join(fonts.families()) + "); any other family fails `yt build`.", ""]
    lines += [sdkkit.BRIEF, sdkkit.time_line(AUTHOR_TIMEOUT), "## Deliverable", "",
              f"Write `{PROGRAM_NAME}` in this directory: a Python program that builds the "
              f"keyframe with the SDK and saves `{DOC_NAME}`, a complete scene-render document "
              f"(`<project width=\"{FRAME[0]}\" height=\"{FRAME[1]}\" fps=\"30\" duration=\"1\">`, "
              "styles with the tokens, assets, materials, symbols, composition, lights, output) "
              f"holding exactly ONE `<symbol id=\"{SYMBOL_ID}\" width=\"{FRAME[0]}\" "
              f"height=\"{FRAME[1]}\" duration=\"1\">` that composes everything -- the world "
              "and the band -- instanced once in the composition from 0 to 1 s, with the "
              "<lights> declared at scene level. The program is the source: a change is made "
              "in it, never in the XML it writes, and the stage builds the program itself. "
              "Read kit/SDK-GUIDE.md and kit/examples/ first: every example rendered on this "
              "engine; examples.scene.xml shows more constructs as XML. Ask the schema "
              "(`python3 kit.py describe <element>`) before guessing an attribute. Run "
              f"`python3 kit.py build {PROGRAM_NAME}` and fix every error until it reports 0 "
              f"error(s); then `python3 sr.py frame {DOC_NAME}` renders the symbol and you "
              "Read the PNG: a black, flat, empty or wrong frame, a hero out of view or an "
              "unreadable band means the work is not done. Do not create any other file than "
              "the program and the document it saves. The stage then renders the frame at "
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

    def _stream(self, engine, prompt: str, *, resume: bool = False) -> int:
        calls = 0
        try:
            system = harness.with_feedback(AUTHOR_SYSTEM, self.root, "keyframe")
            use = bool(resume and getattr(engine, "session_id", None))
            finish = sdkkit.finish_prompt(PROGRAM_NAME, DOC_NAME, f"python3 sr.py frame {DOC_NAME}")
            for event in sdkkit.stream_round(engine, prompt, system=system, resume=use,
                                             finish=finish):
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
        # The harness builds the program itself; the document judged is its output.
        built = sdkkit.build_findings(ws.dir / PROGRAM_NAME, DOC_NAME)
        if any(f.startswith("error") for f in built):
            symbols, findings = [], built
        else:
            symbols, findings = check_document(ws)
            findings = built + findings
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

    def compose(self, chosen: dict, element_findings: list[str], picker: harness.Harness,
                checkpoint=None) -> dict:
        """Author and revise the document; `checkpoint(result)` runs after every
        gated round so a stopped run leaves a record that matches the files."""
        ws = Workspace(self.root, KEYFRAME_DIR)
        tokens = tokens_for(self.direction)
        ws.prepare(tokens=tokens, frame=FRAME)
        roles, _ = palette_roles(self.direction)
        facts = script_facts(self.script)
        brief = write_brief(ws, idea=self.idea, house_rules=self.house, direction=self.direction,
                            elements=chosen, roles=roles, tokens=tokens, facts=facts,
                            assets=asset_rows(self.dossier), notes=self.notes,
                            constraints=vfx.picture_constraints(self.script))
        inputs = _digest(chosen, self.notes, tokens, self.house_digest)
        engine = harness.ENGINE(EngineConfig(
            **sdkkit.author_config(ws.dir, project_root=self.root, programs=[PROGRAM_NAME]),
            model=self.model, max_turns_budget_usd=self.budget_usd, timeout=AUTHOR_TIMEOUT))
        reviewer = self._harness(REVIEW_ROLE)
        doc = ws.dir / DOC_NAME
        # A document from before programs were required is not reusable: the
        # program is the source, so it is authored again from the brief.
        reuse = (doc.is_file() and (ws.dir / PROGRAM_NAME).is_file() and not self.fresh
                 and self.previous.get("inputs_digest") == inputs
                 and not record_mismatch(self.root, self.previous))
        tool_calls = 0
        if reuse:
            self._say(f"  {DOC_NAME} exists for these inputs; not authored again")
        else:
            self._say(f"  authoring {DOC_NAME} in {ws.rel}/ (up to ${self.budget_usd:g}, "
                      f"bar {self.bar:g}/10, {self.rounds} revision round(s))")
            tool_calls += self._stream(engine, (
                f"Read {brief.name}, then kit/SDK-GUIDE.md and kit/examples/, and write "
                f"{PROGRAM_NAME}: the program that builds {DOC_NAME} as the brief says, one "
                f"symbol `{SYMBOL_ID}` composing the hero in its environment with the lights "
                "and materials, in the treatment, and the band with the swatches, the type "
                "sample and the element labels, and saves it. Build it with `python3 kit.py "
                f"build {PROGRAM_NAME}` and fix every error; then `python3 sr.py frame "
                f"{DOC_NAME}` and Read the frame it writes until it shows the look. When the "
                "build reports 0 error(s) and the frame shows the look, reply with one line: "
                "the program, the document and the symbol id."))
        rounds: list[dict] = []
        round_no = 0

        def snapshot(finished: bool) -> dict:
            """The result as of the rounds gated so far, with the digests of
            the files it describes."""
            last = rounds[-1]
            frame_ok = bool(last["frame"]) and not last["errors"]
            return {
                "document": f"{ws.rel}/{DOC_NAME}" if doc.is_file() else "",
                "program": f"{ws.rel}/{PROGRAM_NAME}" if (ws.dir / PROGRAM_NAME).is_file() else "",
                "provenance": sdkkit.provenance_of(doc) if doc.is_file() else None,
                "frame": f"{ws.rel}/{FRAME_NAME}" if frame_ok else "",
                "brief": f"{ws.rel}/{BRIEF_NAME}",
                "rounds": [{k: v for k, v in r.items() if k != "frame"} for r in rounds],
                "score": last["score"], "bar": self.bar, "rounds_allowed": self.rounds,
                "at_bar": last["score"] is not None and last["score"] >= self.bar and frame_ok,
                "element_findings": element_findings, "inputs_digest": inputs,
                "tool_calls": tool_calls, "reused_document": reuse, "finished": finished,
                "timed_out_usage": {"calls": self.usage.timed_out_calls,
                                    "tokens": dict(self.usage.timed_out_tokens),
                                    "cost_usd": round(self.usage.timed_out_cost_usd, 4),
                                    "cost_estimated": self.usage.timed_out_cost_estimated},
                "document_digest": document_digest(doc) if doc.is_file() else "",
                "program_digest": document_digest(ws.dir / PROGRAM_NAME),
                "engine": {"elements": picker.report(), "review": reviewer.report()},
                "cost_usd": round(self.usage.cost_usd + picker.usage.cost_usd
                                  + reviewer.usage.cost_usd, 4),
            }

        while True:
            rounds.append(self._gate(ws, reviewer, chosen, round_no))
            last = rounds[-1]
            if checkpoint:
                checkpoint(snapshot(False))
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
                      + f"Re-read {brief.name}, then fix the program {PROGRAM_NAME} -- the same "
                      f"elements, one symbol `{SYMBOL_ID}`, the hero kept -- run `python3 kit.py "
                      f"build {PROGRAM_NAME}` until it reports 0 error(s), then `python3 sr.py "
                      f"frame {DOC_NAME}` and Read the frame; iterate until it shows the look "
                      "and the findings above are answered. Reply with one line.")
            tool_calls += self._stream(engine, prompt, resume=True)
        return snapshot(True)

    def run(self) -> dict:
        picker = self._harness(ELEMENTS_ROLE)
        chosen, element_findings = self.elements(picker)
        self._say(f"  {len(chosen['elements'])} element(s): "
                  + ", ".join(f"{e['name']} ({e['source_kind']})" for e in chosen["elements"]))
        for f in element_findings:
            self._say(f"  {f}")

        def save(result: dict) -> dict:
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

        return save(self.compose(chosen, element_findings, picker, checkpoint=save))


# --------------------------------------------------------------------------- #
# the look from the film itself
# --------------------------------------------------------------------------- #

#: The lead section's proof frames side by side, beside the chosen frame.
SHEET_NAME = "frames.png"
#: The section the look comes from unless `--section` names another.
DEFAULT_SECTION = 1
#: Sections planned at once in the look pass: the plan covers the whole film,
#: and `yt vfx` reuses it.
LOOK_JOBS = 8
#: The lead section's spend cap (sessions and the gate), as `yt vfx` gives a section.
LOOK_BUDGET_USD = vfx.SECTION_BUDGET_USD
#: The look's program and document, kept where every section author may read them.
LOOK_PROGRAM = "look.py"
LOOK_DOC = "look.scene.xml"


def frame_matches(record: dict, args) -> bool:
    """Whether a look was framed at the size this run asks for (`--width`,
    `--height`); a record from before the size was kept matches."""
    size = record.get("frame_size")
    want = (getattr(args, "width", None), getattr(args, "height", None))
    return not size or None in want or tuple(size) == tuple(want)


def proof_frames(root: Path, record: dict) -> list[Path]:
    """The proof frames the gate rendered for one section, in the order the
    film plays them: world by world (the record's order), then time."""
    doc = Path(root) / str(record.get("file") or "")
    name = doc.name
    stem = name[:-len(".scene.xml")] if name.endswith(".scene.xml") else doc.stem
    order = {str(w.get("id")): i for i, w in enumerate(record.get("worlds") or [])
             if isinstance(w, dict)}

    def key(path: Path) -> tuple:
        parts = path.stem.split("__")
        symbol = parts[1] if len(parts) > 2 else ""
        try:
            at = float(parts[-1].rstrip("s"))
        except ValueError:
            at = 0.0
        return (order.get(symbol, len(order)), symbol, at, path.name)

    return sorted((doc.parent / "frames").glob(f"{stem}__*.png"), key=key)


def pick_frame(frames: list[Path]) -> Path | None:
    """The frame that shows the most: the highest spread of luminance, the
    earliest on a tie. A flat or empty proof frame never stands for the look."""
    from PIL import Image, ImageStat
    best, spread = None, -1.0
    for path in frames:
        try:
            with Image.open(path) as im:
                value = ImageStat.Stat(im.convert("L").resize((160, 90))).stddev[0]
        except OSError:
            continue
        if value > spread:
            best, spread = path, value
    return best


def contact_sheet(frames: list[Path], out: Path, *, columns: int = 4,
                  width: int = 480) -> Path | None:
    """Every proof frame of the section on one sheet, labelled with its
    symbol and time, for the operator to judge the look across the shots."""
    from PIL import Image, ImageDraw
    if not frames:
        return None
    height, strip = width * 9 // 16, 22
    rows = (len(frames) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * width, rows * (height + strip)), "black")
    draw = ImageDraw.Draw(sheet)
    for i, path in enumerate(frames):
        x, y = (i % columns) * width, (i // columns) * (height + strip)
        try:
            with Image.open(path) as im:
                sheet.paste(im.convert("RGB").resize((width, height)), (x, y + strip))
        except OSError:
            continue
        parts = path.stem.split("__")
        draw.text((x + 4, y + 4), " @ ".join(parts[1:]) if len(parts) > 1 else path.stem,
                  fill="white")
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


class SectionLook:
    """The look from the film itself. `yt vfx`'s own planner plans the film
    and authors its lead section once; the mechanical gate judges it (build,
    validation, frames, the legibility and geometry rules) and no vision
    review is paid for; its frames go in front of the operator, and once
    approved `yt vfx` carries the section forward and authors the others
    against its program. Nothing made here is thrown away."""

    def __init__(self, args, idea: Idea, project: workspace.Project, *,
                 section: int | None = None, verbose: bool = True) -> None:
        self.args = args
        self.idea = idea
        self.project = project
        self.root = Path(project.root)
        self.previous = load_record(self.root)
        # A section named once stays the look's source until another is named.
        asked = section or (self.previous.get("asked_section")
                            if self.previous.get("source") == "section" else None)
        self.section = max(1, int(asked or DEFAULT_SECTION))
        self.verbose = verbose
        self.notes = operator_notes(self.previous)
        self.usage = Usage()

    def reusable(self) -> dict | None:
        """The last look when nothing asks for a new one: a look from this
        section, pending or approved, whose files still match the record."""
        if getattr(self.args, "fresh", False):
            return None
        rec = self.previous
        if rec.get("source") != "section" or rec.get("asked_section") != self.section:
            return None
        if approval_state(self.root)["state"] not in ("pending", "approved"):
            return None
        if not (self.root / str(rec.get("document") or "")).is_file() or approvable(rec):
            return None
        if not frame_matches(rec, self.args):
            return None
        return None if record_mismatch(self.root, rec) else rec

    def run(self) -> dict:
        planner = vfx.planner_from_args(self.args, self.idea, self.project, lead=self.section,
                                        look_notes=[n["notes"] for n in self.notes])
        try:
            result = planner.run_lead()
        finally:
            _merge_usage(self.usage, planner.usage)
        rec = result["record"]
        if not rec:
            raise KeyframeError("the look pass authored nothing: no section has planned effects")
        frames = proof_frames(self.root, rec)
        out = self.root / KEYFRAME_DIR
        out.mkdir(parents=True, exist_ok=True)
        chosen = pick_frame(frames)
        frame_rel = sheet_rel = ""
        for stale in (out / FRAME_NAME, out / SHEET_NAME, out / LOOK_PROGRAM, out / LOOK_DOC):
            stale.unlink(missing_ok=True)       # never show an earlier look as this one
        if chosen is not None:
            shutil.copy2(chosen, out / FRAME_NAME)
            frame_rel = f"{KEYFRAME_DIR}/{FRAME_NAME}"
        if contact_sheet(frames, out / SHEET_NAME) is not None:
            sheet_rel = f"{KEYFRAME_DIR}/{SHEET_NAME}"
        doc = self.root / rec["file"]
        program = self.root / rec["program"] if rec.get("program") else None
        # The other sections' authors may read keyframe/, not another vfx
        # version: the look's program and document are kept here for them.
        look_program = look_doc = ""
        if program is not None and program.is_file():
            shutil.copy2(program, out / LOOK_PROGRAM)
            look_program = f"{KEYFRAME_DIR}/{LOOK_PROGRAM}"
        if doc.is_file():
            shutil.copy2(doc, out / LOOK_DOC)
            look_doc = f"{KEYFRAME_DIR}/{LOOK_DOC}"
        style = result.get("style") or {}
        findings = [f for f in rec.get("findings") or [] if not f.startswith("note")]
        record = {"idea": compact(self.idea), "stack": STACK, "house": planner.house_digest,
                  "source": "section", "section": rec["n"], "asked_section": self.section,
                  "heading": rec.get("section", ""), "version": rec.get("version"),
                  "document": rec["file"], "program": rec.get("program", ""),
                  "frame": frame_rel, "sheet": sheet_rel, "look_program": look_program,
                  "look_document": look_doc, "frame_size": list(planner.frame),
                  "frames": [str(p.relative_to(self.root)) for p in frames],
                  "chosen_frame": str(chosen.relative_to(self.root)) if chosen else "",
                  "record": rec, "findings": findings,
                  "errors": [f for f in findings if f.startswith("error")],
                  "rounds": rec.get("rounds") or [], "score": None,
                  "treatment": style.get("look", ""), "camera": style.get("camera_language", ""),
                  "hero": "", "elements": [], "operator_notes": self.notes,
                  "cost_usd": round(self.usage.cost_usd, 4), "usage": asdict(self.usage),
                  "document_digest": document_digest(doc),
                  "program_digest": document_digest(program) if program else None,
                  "approval": {"state": "pending", "notes": "", "digest": document_digest(doc),
                               "at": _now()},
                  "when": _now()}
        if not doc.is_file() or approvable(record):
            # Nothing the operator could approve: `yt run` authors it again.
            record["approval"]["state"] = "failed"
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
    if record.get("source") == "section":
        return _write_section_sheet(Path(root), record, lines)
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


def _write_section_sheet(root: Path, record: dict, lines: list[str]) -> Path:
    """keyframe.md for a look taken from the film: the chosen frame, the
    contact sheet, the section and its files, the gate's findings, the commands."""
    rec = record.get("record") or {}
    lines += [f"The look is section {record.get('section')} of the film, "
              f"**{record.get('heading', '')}**, authored once in `{record.get('version', '')}` "
              "and judged by the mechanical gate only. Approve it and `yt vfx` carries it "
              "forward and authors the other sections against it.", ""]
    if record.get("frame"):
        lines += [f"![keyframe]({record['frame']})", "",
                  f"Frame: `{record['frame']}` (from `{record.get('chosen_frame', '')}`)", ""]
    else:
        lines += ["No proof frame was rendered; see the findings below.", ""]
    if record.get("sheet"):
        lines += [f"All {len(record.get('frames') or [])} proof frames: `{record['sheet']}`", "",
                  f"![frames]({record['sheet']})", ""]
    lines += [f"Program: `{record.get('program', '')}` · document: `{record.get('document', '')}`  ",
              f"Treatment: {record.get('treatment', '')}  ",
              f"Camera: {record.get('camera', '')}  ",
              f"Cost: ${record.get('cost_usd', 0):g} (planning the film and authoring this section)",
              "", "## Worlds", ""]
    for w in rec.get("worlds") or []:
        lines.append(f"- `{w.get('id', '')}` at {w.get('start', 0)}s for {w.get('seconds', 0)}s: "
                     f"shots {', '.join(str(s) for s in w.get('shots') or [])}")
    findings = record.get("findings") or []
    lines += ["", "## The gate", ""] + ([f"- {f}" for f in findings] or ["- no finding"])
    notes = operator_notes(record)
    if notes:
        lines += ["", "## Operator's notes", ""] + [f"- ({n.get('at', '')}) {n['notes']}"
                                                   for n in notes]
    refused = approvable(record)
    if refused:
        lines += ["", f"**This look cannot be approved: {refused}.** Run `yt keyframe` again."]
    lines += ["", "## Commands", ""]
    if not refused:
        lines += [f"    yt keyframe --project {root} --approve",
                  f"    yt keyframe --project {root} --reject \"what is wrong, in your words\""]
    lines += [f"    yt keyframe --project {root} --fresh      # plan and author the section again",
              ""]
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
        help="lock the look: the film's lead section, authored once, for approval",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--section", type=int, default=None,
                   help=f"the section the look comes from (1-based; default the one named "
                        f"last, else {DEFAULT_SECTION}; the first section with planned effects "
                        "when it has none)")
    p.add_argument("--jobs", type=int, default=LOOK_JOBS,
                   help=f"sections planned at once (default {LOOK_JOBS}); the plan covers the "
                        "film and `yt vfx` reuses it")
    p.add_argument("--sheet", action="store_true",
                   help="compose a separate style frame of the film's elements instead (the "
                        "older look-lock, reviewed in rounds; its frame is not part of the film)")
    p.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS,
                   help="with --sheet: revision rounds the author gets after the first frame "
                        f"when the review is under the bar (default {DEFAULT_ROUNDS})")
    p.add_argument("--bar", type=float, default=DEFAULT_BAR,
                   help=f"with --sheet: review score (0-10) under which the frame goes back "
                        f"(default {DEFAULT_BAR:g})")
    p.add_argument("--budget-usd", type=float, default=None, dest="budget_usd",
                   help=f"spend cap for one authoring session (default {LOOK_BUDGET_USD:g}; "
                        "2 with --sheet)")
    p.add_argument("--fresh", action="store_true",
                   help="plan and author the look again (with --sheet: pick and compose "
                        "again), ignoring cached decisions")
    p.add_argument("--approve", action="store_true",
                   help="record the operator's approval of the keyframe on disk")
    p.add_argument("--reject", metavar="NOTES",
                   help="record a rejection with notes; the next run obeys them first")
    p.add_argument("--host", metavar="USER@HOST",
                   help="render the frame on this machine (default: the fast still host)")
    vfx.add_planning_args(p)
    workspace.add_inputs(p, "script", "voice", "styles")
    p.set_defaults(run=run)
    return p


def run(args) -> int:
    idea, project = workspace.resolve(args, inputs=("script", "voice", "styles"))
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
            print(f"  next: yt keyframe --project {project.root}   (your notes lead the author)")
        elif record.get("source") == "section" and not film_carries_look(project.root):
            print(f"  next: yt vfx --project {project.root} --relook   (the animation exists: "
                  f"section {record.get('section')} is carried into it and the others are "
                  "authored again against it)")
        elif record.get("source") == "section":
            print(f"  next: yt vfx --project {project.root}   (section {record.get('section')} "
                  "is carried forward; the other sections are authored against it)")
        else:
            print(f"  next: yt vfx --project {project.root}   (the author inherits this look)")
        return 0

    host = render.remote_host(args, "still")
    if not host and render.engine() is None:
        print("error: no engine reachable (RS_RENDER_HOSTS unset, no local binary); the "
              "keyframe is a rendered frame, so there is nothing to make", file=sys.stderr)
        return 1
    if not args.sheet:
        return run_section_look(args, idea, project, verbose=verbose)
    if args.budget_usd is None:
        args.budget_usd = 2.0
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


def run_section_look(args, idea: Idea, project: workspace.Project, *, verbose: bool) -> int:
    """`yt keyframe` without --sheet: the look is the film's lead section."""
    if args.budget_usd is None:
        args.budget_usd = LOOK_BUDGET_USD
    current = load_record(project.root)
    state = approval_state(project.root)["state"]
    if (state == "approved" and not args.fresh and frame_matches(current, args)
            and (args.section is None or (current.get("source") == "section"
                                          and args.section == current.get("asked_section")))):
        # An approved look is the operator's decision; only --fresh or another
        # --section replaces it, never a re-run downstream of voice or retime.
        print(f"{idea.title} — the look is approved ({current.get('source') or 'sheet'}"
              + (f", section {current.get('section')}" if current.get("source") == "section" else "")
              + f"): {project.root / (current.get('frame') or '')}")
        print("  --fresh authors a new one", file=sys.stderr)
        return 0
    look = SectionLook(args, idea, project, section=args.section, verbose=verbose)
    reused = look.reusable()
    if reused is not None:
        state = approval_state(project.root)["state"]
        print(f"{idea.title} — the look (section {reused.get('section')}, "
              f"{reused.get('version')}) is {state}: {project.root / reused.get('frame', '')}")
        if state == "pending":
            print(f"approve: yt keyframe --project {project.root} --approve")
            print(f"reject:  yt keyframe --project {project.root} --reject \"notes\"")
        print("  --fresh plans and authors it again", file=sys.stderr)
        return 0
    if verbose:
        print(f"keyframe for {STACK}: {idea.title}\n  the look is section {look.section} of "
              "the film, authored once and judged by the gate; nothing is thrown away",
              file=sys.stderr)
    try:
        record = look.run()
    except (harness.HarnessError, ClaudeError, KeyframeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"\n{idea.title} — THE LOOK: section {record['section']}, {record['heading']}")
    print(f"  {record['document']} · {len(record['frames'])} proof frame(s) · "
          f"{len(record['errors'])} error(s) · ${record['cost_usd']:g}")
    for f in record["findings"][:8]:
        print(f"  {f}")
    print(f"\n{project.root}/\n  {record_path(project.root).name}\n  keyframe.md")
    if record["frame"]:
        print(f"\nframe:  {project.root / record['frame']}")
        if record["sheet"]:
            print(f"frames: {project.root / record['sheet']}")
        print(f"approve: yt keyframe --project {project.root} --approve")
        print(f"reject:  yt keyframe --project {project.root} --reject \"notes\"")
    else:
        print("\nno proof frame was rendered; fix the errors above and run again", file=sys.stderr)
    print(f"{look.usage}", file=sys.stderr)
    return 0 if record["frame"] and not record["errors"] else 1
