"""Route QA's findings back to the stages that own them, and run the loop.

`yt qa` names an owner for every finding, and `docs/production.md` §8 says
what to do with them: trace each to the file that owns it, fix it there,
re-render, compare, keep only a measured improvement. This stage is that
loop as code:

    yt improve --title X --dry-run          # route the findings, write the briefs, run nothing
    yt improve --title X                    # up to 3 passes, $15, until READY TO PUBLISH
    yt improve --title X --max-passes 1 --budget-usd 12 --quality preview

1. **Evidence.** The latest QA pass (or `--from-pass`), scored again first
   when it is older than `video.mp4` or `scene.xml`; the gate's blocking
   rows and `yt doctor`'s fails and warnings for the project beside it.
2. **Routing.** Each finding goes to exactly one stage of
   `workspace.STAGES`, or to `upstream-fact` (a claim the research or the
   screenplay got wrong: production fixes pictures, not facts) or
   `external` (a policy or an approval outside this repo). The mechanical
   kinds (`MECHANICAL_KINDS`) go to their stated owner without asking; the
   prose ones, whose `owner` is free text, are routed by one harness
   decision under `ROUTE_SCHEMA`, which also merges duplicates and writes
   each stage an actionable brief: what must change, how the next pass
   will check it.
3. **Feedback.** `<project>/feedback/<stage>.md` and a JSON twin, with the
   findings verbatim and the pass they came from. The harness puts a
   stage's open notes into every decision and inspection it makes
   (`harness.feedback_for`), as revision notes that outrank the house
   standards; a pass that consumed them moves them to
   `feedback/closed/<pass>/`.
4. **Re-run.** The earliest routed stage with the flags that force a new
   decision (`--fresh`; vfx opens a new `vfx/vNN/`), every stage downstream
   of it up to render (research and the screenplay only when routed to
   them; a stage the film never ran is left out), the gate, then `yt qa`
   for a new pass -- all through `runner`, a callable taking argv.
5. **Keep or revert** (§8.8, §9). Against the best pass so far: kept when
   the overall improves, no category drops by more than `TOLERANCE` and no
   new blocking mechanical finding appears; otherwise the snapshot taken
   before the pass (`improve/pass-NN/snapshot/`) is restored. vfx versions
   are kept anyway, so reverting vfx restores `vfx.json`'s active version.
6. **Stop** at READY TO PUBLISH (or `--target` with nothing blocking), at
   `--max-passes`, when the next pass's estimate exceeds what is left of
   `--budget-usd`, when a defect survives `SURVIVOR_PASSES` passes that
   were briefed on it (the approach is wrong, not the parameters), or when
   everything left needs a human.
7. **Report.** `improve.json` (every pass: the routing, the briefs, the
   stages and argv, the scores before and after, keep or revert and why,
   the cost, the stop) and `improve.md` in the shape of §11.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from . import house, qa, workspace
from .core import read_json, write_json
from .harness import FEEDBACK_DIR, Harness, HarnessError, brief

#: The stages a finding can be routed to, in pipeline order.
PIPELINE = tuple(stage for stage, _ in workspace.STAGES)
ROUTABLE = PIPELINE + (() if "keyframe" in PIPELINE else ("keyframe",))
UPSTREAM, EXTERNAL = "upstream-fact", "external"
HUMAN = (UPSTREAM, EXTERNAL)
ROUTES = ROUTABLE + HUMAN

#: Measured by `yt qa` before anyone looks; routed to their stated owner.
MECHANICAL_KINDS = ("penetration", "reads-merged", "flat", "clipped", "frozen", "jump",
                    "off_camera")

#: Expensive, and they change the film's facts: never re-run as a side effect.
EXPENSIVE = ("research", "script")

#: A category may drop this much against the best pass and still be kept.
TOLERANCE = 0.5
#: Briefed and still there after this many passes: the approach is wrong.
SURVIVOR_PASSES = 3

IMPROVE_DIR = "improve"
REPORT_JSON, REPORT_MD = "improve.json", "improve.md"

#: What a pass is guessed to cost before one has been measured.
VFX_PASS_USD = 10.0
DEFAULT_USD = {"research": 5.0, "styles": 2.0, "script": 3.0, "props": 0.0,
               "keyframe": 5.0, "voice": 2.0, "retime": 0.5, "vfx": VFX_PASS_USD,
               "sound": 0.5, "build": 0.1, "render": 0.0, "gate": 0.1, "qa": 0.5}

#: The flags that make a routed stage decide again, and the ones a stage
#: downstream of it runs with (reusing what its inputs did not change).
FORCE = {"research": ["--fresh"], "styles": ["--rich"], "script": ["--polish", "--verify"],
         "props": ["--fresh"], "keyframe": ["--fresh"], "voice": ["--fresh"],
         "retime": ["--fresh"], "vfx": ["--fresh"],
         "sound": ["--fetch", "--review", "--fresh"], "build": ["--fresh"], "render": []}
REUSE = {"styles": ["--rich"], "script": ["--polish", "--verify"], "sound": ["--fetch"]}

#: What each stage owns, so the router can place a finding whose owner is prose.
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
}

#: The files each stage writes, snapshotted before a pass re-runs it.
SNAPSHOT = {
    "research": ["dossier.json", "README.md", "claims.csv", "sources.csv"],
    "styles": ["styles.json", "styles.md"],
    "script": ["script.json", "script.md", "narration.txt", "shotlist.csv"],
    "props": ["props.json"],
    "keyframe": ["keyframe.json", "keyframe.md", "keyframe"],
    "voice": ["voice.json", "narration.vtt", "narration.srt", "voice"],
    "retime": ["retime.json", "script.json"],
    "vfx": ["vfx.json", "vfx.md", "vfx.csv"],
    "sound": ["sound.json", "sound.md", "cues.csv"],
    "build": ["scene.xml", "build.json"],
    "render": ["video.mp4", "render.json"],
    "gate": ["gate.json"],
    "qa": ["qa.json"],
}

#: The manifest whose cost a stage reports, and where it keeps its decisions.
MANIFEST = {"research": "dossier.json", "styles": "styles.json", "script": "script.json",
            "props": "props.json", "keyframe": "keyframe.json", "voice": "voice.json",
            "retime": "retime.json", "vfx": "vfx.json", "sound": "sound.json",
            "build": "build.json", "render": "render.json", "gate": "gate.json"}

ROLE = (
    "You are the producer running the improvement loop of docs/production.md §8. "
    "QA has judged the rendered film and filed findings; each must reach the one "
    "pipeline stage whose source owns the fix, because a fix applied anywhere else "
    "is undone by the next build. You route, you merge what is the same defect seen "
    "twice, and you write each stage a revision brief its own model can act on "
    "without having seen the film: concrete changes in that stage's terms, and the "
    "observable check the next QA pass will apply. You never soften a finding."
)

ROUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "routes": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "finding": {"type": "string", "description": "the finding's id, F1..."},
                "stage": {"type": "string", "enum": list(ROUTES)},
                "duplicate_of": {"type": "string",
                                 "description": "the id of the earlier finding this one "
                                                "repeats, or empty"},
                "why": {"type": "string"}},
            "required": ["finding", "stage", "why"]}},
        "briefs": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "stage": {"type": "string", "enum": list(ROUTABLE)},
                "must_change": {"type": "array", "items": {"type": "string"}},
                "check": {"type": "string",
                          "description": "how the next QA pass will tell it is fixed"}},
            "required": ["stage", "must_change", "check"]}},
    },
    "required": ["routes", "briefs"],
}

SAME_SCHEMA = {
    "type": "object",
    "properties": {"matches": {"type": "array", "items": {
        "type": "object",
        "properties": {"finding": {"type": "string"}, "defect": {"type": "string"},
                       "why": {"type": "string"}},
        "required": ["finding", "defect"]}}},
    "required": ["matches"],
}


# --------------------------------------------------------------------------- #
# evidence
# --------------------------------------------------------------------------- #

_TICKED = re.compile(r"`([^`]+)`")


def fingerprint(finding: dict) -> str | None:
    """A mechanical finding's identity across passes: kind, shot and the
    objects it names. A prose finding has none; the model judges it."""
    kind = finding.get("kind")
    if kind not in MECHANICAL_KINDS:
        return None
    names = sorted({n.rsplit("/", 1)[-1] for n in _TICKED.findall(str(finding.get("problem", "")))})
    return f"{kind}|{finding.get('shot', '')}|{'+'.join(names)}"


def collect(card: dict, *, gate: dict | None = None,
            doctor: Iterable[tuple] = ()) -> list[dict]:
    """Every finding of one pass, numbered F1..: the mechanical ones, the
    reviewer's, the gate's blocking rows, and doctor's fails and warnings
    for a stage."""
    rows: list[dict] = []

    def add(source: str, f: dict, **over) -> None:
        row = {"source": source, "kind": f.get("kind"), "shot": f.get("shot"),
               "timestamp": f.get("timestamp"), "severity": f.get("severity", "minor"),
               "owner": str(f.get("owner", "")), "problem": str(f.get("problem", "")),
               "why": str(f.get("why", "")), "root_cause": str(f.get("root_cause", "")),
               "fix": str(f.get("fix", ""))}
        row.update(over)
        row["fingerprint"] = fingerprint(row) if source == "mechanical" else None
        rows.append(row)

    for f in card.get("mechanical") or []:
        add("mechanical", f)
    for f in card.get("findings") or []:
        add("qa", f, kind=None)
    if gate and gate.get("exit"):
        for b in (gate.get("triage") or {}).get("blocking") or []:
            add("gate", {"owner": b.get("owner", ""), "problem": b.get("what", ""),
                         "fix": b.get("fix", ""), "severity": "blocking"})
    for level, area, message, fix in doctor:
        if level in ("fail", "warn") and area in ROUTABLE:
            add("doctor", {"owner": area, "problem": message, "fix": fix,
                           "severity": "blocking" if level == "fail" else "minor"})
    for n, row in enumerate(rows, 1):
        row["id"] = f"F{n}"
    return rows


def doctor_rows(project: workspace.Project) -> list[tuple]:
    """`yt doctor`'s findings for one project, as (level, area, message, fix)."""
    from . import doctor
    try:
        report = doctor.check_project(project)
    except Exception as exc:       # noqa: BLE001 - doctor is evidence, not a gate
        return [("note", "improve", f"doctor did not run: {exc}", "")]
    return [(f.level, f.area, f.message, f.fix) for f in report.findings]


# --------------------------------------------------------------------------- #
# routing
# --------------------------------------------------------------------------- #

def _shown(f: dict) -> dict:
    return {k: f.get(k) for k in ("id", "source", "kind", "shot", "timestamp", "severity",
                                  "owner", "problem", "why", "root_cause", "fix")
            if f.get(k) not in (None, "")}


def _route_prompt(findings: list[dict], fixed: dict[str, str], context: dict | None) -> str:
    rows = []
    for f in findings:
        row = _shown(f)
        if f["id"] in fixed:
            row["routed"] = fixed[f["id"]]
        rows.append(row)
    return "\n\n".join([
        "Route every finding of this QA pass to the one stage that owns its fix, merge "
        "the duplicates, and write one revision brief per stage that receives any.",
        "Rules:\n"
        "- `stage` is exactly one of the pipeline stages below, or `upstream-fact` when "
        "the finding is a wrong or unsupported claim in the research or the screenplay "
        "(production fixes pictures, not facts), or `external` when it is a policy, a "
        "licence or an approval outside this repo (for example a delivery requirement "
        "that does not fit this film, or a step waiting on the operator). Those two "
        "reach a human; nothing loops on them.\n"
        "- Routing to `research` or `script` re-researches the film or rewrites its "
        "screenplay, which is expensive and changes its facts. Route there only when the "
        "picture or the sound cannot be fixed without a different screenplay. A weak, "
        "unsourced or low-confidence claim is `upstream-fact`, never `research`.\n"
        "- A finding whose fix is the operator's approval or sign-off (approving a "
        "keyframe, waiving a requirement, choosing between options) is `external`: "
        "running the stage again does not approve anything. The project's state below "
        "says what already exists and what is waiting on the operator; a finding that "
        "asks for something already made and awaiting approval is `external`.\n"
        "- The `owner` written by QA is free text and may name a tool, a discipline or a "
        "stage; route by what must change in which stage's source, not by the word.\n"
        "- A finding marked `routed` was measured mechanically and is already routed; "
        "do not route it elsewhere, but do write its brief and mark later findings that "
        "repeat it as `duplicate_of` it.\n"
        "- `duplicate_of` names the earliest finding describing the same defect, or is "
        "empty.\n"
        "- A brief's `must_change` lists concrete changes in that stage's own terms "
        "(what to author, key, light, place, fetch, spot or mix differently), each "
        "traceable to a finding; `check` says what the next QA pass must no longer show "
        "and what it must show instead. The findings themselves are attached to the "
        "brief verbatim by the tooling; do not copy them.",
        brief("Pipeline stages and what each owns",
              {s: DUTIES.get(s, "") for s in ROUTABLE}),
        brief("The pass", context or {}),
        brief("Findings", rows),
    ])


def _unrouted(answer: dict, findings: list[dict], fixed: dict[str, str]) -> list[str]:
    ids = {f["id"] for f in findings}
    got = {}
    for r in answer.get("routes") or []:
        if isinstance(r, dict):
            got[str(r.get("finding", ""))] = r
    missing = []
    for f in findings:
        if f["id"] in fixed:
            continue
        r = got.get(f["id"])
        if r is None:
            missing.append(f["id"])
            continue
        dup = str(r.get("duplicate_of") or "")
        if r.get("stage") not in ROUTES and not (dup in ids and dup != f["id"]):
            missing.append(f["id"])
    return missing


def route(h: Harness, findings: list[dict], *, name: str,
          context: dict | None = None) -> dict:
    """Each finding to one stage; the briefs per stage; what needs a human."""
    out: dict = {"routes": {}, "stages": {}, "human": []}
    if not findings:
        return out
    fixed = {f["id"]: f["owner"] for f in findings
             if f.get("kind") in MECHANICAL_KINDS and f.get("owner") in ROUTABLE}
    prompt = _route_prompt(findings, fixed, context)
    answer = h.decide(name, prompt, ROUTE_SCHEMA)
    missing = _unrouted(answer, findings, fixed)
    if missing:
        answer = h.decide(f"{name}-retry", prompt + "\n\nAn earlier answer left "
                          f"{', '.join(missing)} unrouted or routed to no known stage. "
                          "Route every finding.", ROUTE_SCHEMA)
        missing = _unrouted(answer, findings, fixed)
        if missing:
            raise HarnessError(f"[{h.stage}] {name}: the model routed no stage for "
                               f"{', '.join(missing)}")
    by_id = {f["id"]: f for f in findings}
    model = {str(r.get("finding", "")): r for r in answer.get("routes") or []
             if isinstance(r, dict)}
    routes = out["routes"]
    for f in findings:
        r = model.get(f["id"], {})
        dup = str(r.get("duplicate_of") or "")
        dup = dup if dup in by_id and dup != f["id"] else ""
        if f["id"] in fixed:
            routes[f["id"]] = {"stage": fixed[f["id"]], "by": "mechanical",
                               "why": f"measured {f['kind']}, owned by {fixed[f['id']]}",
                               "duplicate_of": dup}
        else:
            routes[f["id"]] = {"stage": str(r.get("stage", "")), "by": "model",
                               "why": str(r.get("why", "")), "duplicate_of": dup}
    # A duplicate follows its primary, unless it is measured and owned elsewhere.
    for fid, r in routes.items():
        seen, primary = {fid}, r["duplicate_of"]
        while primary and routes.get(primary, {}).get("duplicate_of") and primary not in seen:
            seen.add(primary)
            primary = routes[primary]["duplicate_of"]
        if not primary:
            continue
        stage = routes[primary]["stage"]
        if r["by"] == "mechanical" and stage != r["stage"]:
            r["duplicate_of"] = ""
            continue
        r["stage"] = stage
    briefs = {}
    for b in answer.get("briefs") or []:
        if isinstance(b, dict) and b.get("stage") in ROUTABLE and b["stage"] not in briefs:
            briefs[b["stage"]] = b
    for f in findings:
        stage = routes[f["id"]]["stage"]
        if stage in HUMAN:
            out["human"].append(dict(f, route=stage, why_routed=routes[f["id"]]["why"]))
            continue
        entry = out["stages"].setdefault(stage, {"findings": [], "must_change": [],
                                                 "check": ""})
        entry["findings"].append(f["id"])
    for stage, entry in out["stages"].items():
        b = briefs.get(stage)
        if b and [m for m in b.get("must_change") or [] if str(m).strip()]:
            entry["must_change"] = [str(m) for m in b["must_change"] if str(m).strip()]
            entry["check"] = str(b.get("check", ""))
        else:
            fixes = []
            for fid in entry["findings"]:
                fix = by_id[fid].get("fix")
                if fix and fix not in fixes:
                    fixes.append(fix)
            entry["must_change"] = fixes
        if not entry["check"]:
            entry["check"] = ("the next QA pass no longer reports these findings, and "
                              "no category drops")
    return out


# --------------------------------------------------------------------------- #
# the feedback channel
# --------------------------------------------------------------------------- #

def _open_notes(root: Path) -> list[Path]:
    base = root / FEEDBACK_DIR
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_file() and p.suffix in (".md", ".json"))


def feedback_md(stage: str, entry: dict, findings: list[dict], *, from_pass: str,
                card: dict, improve_pass: str, survived: dict | None = None,
                attempts: list[str] | None = None) -> str:
    stamp = qa._stamp
    lines = [f"# Revision notes for `{stage}`, from qa/{from_pass}", "",
             f"Written by `yt improve` for its {improve_pass}. QA judged {from_pass} "
             f"{card.get('verdict', '?')} at an overall of {card.get('overall', '?')}. "
             "These findings were seen in the rendered film and trace to this stage's "
             "source; the next QA pass checks them.", "",
             "## Findings, verbatim", ""]
    for f in findings:
        where = f"{stamp(f['timestamp'])} " if f.get("timestamp") is not None else ""
        shot = f"shot {f['shot']} " if f.get("shot") else ""
        what = f"{f['source']}" + (f" {f['kind']}" if f.get("kind") else "")
        lines.append(f"- **{f['id']}** {where}{shot}[{f.get('severity', '?')}] ({what}, "
                     f"QA owner: {f.get('owner') or '?'}): {f['problem']}")
        for label, key in (("why", "why"), ("root cause", "root_cause"), ("fix", "fix")):
            if f.get(key):
                lines.append(f"  - {label}: {f[key]}")
        if f.get("duplicate_of"):
            lines.append(f"  - the same defect as {f['duplicate_of']}")
        if survived and survived.get(f["id"]):
            n = survived[f["id"]]
            lines.append(f"  - briefed and still there after {n} pass{'es' if n != 1 else ''}"
                         " of this loop: change the approach, not the parameters")
    lines += ["", "## What must change", ""]
    lines += [f"{n}. {m}" for n, m in enumerate(entry.get("must_change") or [], 1)] or ["-"]
    lines += ["", "## How the next pass will check it", "", entry.get("check") or "-", ""]
    if attempts:
        lines += ["## Earlier attempts from the same evidence, reverted", "",
                  *[f"- {a}" for a in attempts], ""]
    return "\n".join(lines)


def write_feedback(root: Path, routing: dict, findings: list[dict], *, from_pass: str,
                   card: dict, improve_pass: str, survived: dict | None = None,
                   attempts: dict[str, list[str]] | None = None) -> list[str]:
    """One open brief per routed stage, `.md` for the model and `.json` for
    code; an open note for a stage this routing does not reach is removed."""
    root = Path(root)
    base = root / FEEDBACK_DIR
    stages = routing.get("stages") or {}
    for path in _open_notes(root):
        if path.stem not in stages:
            path.unlink()
    by_id = {f["id"]: f for f in findings}
    written = []
    for stage, entry in stages.items():
        rows = [dict(by_id[fid], duplicate_of=routing["routes"][fid].get("duplicate_of", ""))
                for fid in entry["findings"]]
        base.mkdir(parents=True, exist_ok=True)
        tried = (attempts or {}).get(stage) or []
        (base / f"{stage}.md").write_text(
            feedback_md(stage, entry, rows, from_pass=from_pass, card=card,
                        improve_pass=improve_pass, survived=survived, attempts=tried),
            encoding="utf-8")
        write_json(base / f"{stage}.json", {
            "stage": stage, "from_pass": from_pass, "improve_pass": improve_pass,
            "written": time.strftime("%Y-%m-%dT%H:%M:%S"), "findings": rows,
            "must_change": entry.get("must_change", []), "check": entry.get("check", ""),
            "attempts": tried})
        written.append(stage)
    return written


def close_feedback(root: Path, name: str) -> list[str]:
    """Move every open note under `feedback/closed/<name>/`: consumed."""
    moved = []
    for path in _open_notes(Path(root)):
        dest = Path(root) / FEEDBACK_DIR / "closed" / name
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest / path.name))
        moved.append(path.name)
    return moved


# --------------------------------------------------------------------------- #
# the plan
# --------------------------------------------------------------------------- #

def plan(routed: Iterable[str], root: Path) -> list[str]:
    """The earliest routed stage, then every stage downstream of it up to
    render. Research and the screenplay run only when routed to; a stage
    whose artifact the film never had (a film with no narration has no
    voice) is left out."""
    routed = {s for s in routed if s in PIPELINE}
    if not routed:
        return []
    project = workspace.Project(Path(root))
    artifacts = dict(workspace.STAGES)
    first = min(PIPELINE.index(s) for s in routed)
    out = []
    for stage in PIPELINE[first:]:
        if stage in routed or stage == "render":
            out.append(stage)
        elif stage in EXPENSIVE or not project.has(artifacts[stage]):
            continue
        else:
            out.append(stage)
    return out


def stage_argvs(stages: list[str], routed: Iterable[str], base: list[str], *,
                quality: str, root: Path) -> list[list[str]]:
    from .cli import stage_argv
    routed = set(routed)
    sound_data = read_json(Path(root) / "sound.json", warn=False) or {}
    out = []
    for stage in stages:
        flags = list((FORCE if stage in routed else REUSE).get(stage, []))
        if stage == "script":
            seconds = (read_json(Path(root) / "script.json", warn=False) or {}).get(
                "target_seconds")
            if seconds:
                flags = ["--minutes", f"{float(seconds) / 60:g}"] + flags
        if stage == "sound" and sound_data.get("synth"):
            flags.append("--synth")
        if stage == "render":
            flags += ["--quality", quality]
        out.append(stage_argv(stage, base, {stage: flags}))
    return out


def estimate(stages: list[str], last_cost: float | None) -> float:
    """The last pass's cost, or the default for a pass of these stages."""
    if last_cost:
        return round(float(last_cost), 2)
    if "vfx" in stages:
        # A vfx pass is guessed whole, its downstream included; what runs
        # before it is added on top.
        upstream = stages[:stages.index("vfx")]
        return round(VFX_PASS_USD + sum(DEFAULT_USD.get(s, 0.5) for s in upstream), 2)
    return round(sum(DEFAULT_USD.get(s, 0.5) for s in stages)
                 + DEFAULT_USD["gate"] + DEFAULT_USD["qa"], 2)


# --------------------------------------------------------------------------- #
# snapshots
# --------------------------------------------------------------------------- #

def snapshot(root: Path, stages: Iterable[str], dest: Path) -> dict:
    """Copy what these stages write into `dest`, noting what did not exist."""
    root, dest = Path(root), Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    files: dict[str, bool] = {}
    for stage in stages:
        for rel in SNAPSHOT.get(stage, []):
            if rel in files:
                continue
            src = root / rel
            files[rel] = src.exists()
            if src.is_dir():
                shutil.copytree(src, dest / rel, copy_function=shutil.copy2,
                                dirs_exist_ok=True)
            elif src.is_file():
                (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest / rel)
    write_json(dest / "snapshot.json", {"files": files})
    return files


def restore(root: Path, dest: Path) -> list[str]:
    """Put back what `snapshot` copied; remove what did not exist then."""
    root, dest = Path(root), Path(dest)
    files = (read_json(dest / "snapshot.json", warn=False) or {}).get("files") or {}
    for rel, existed in files.items():
        target = root / rel
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        if existed:
            src = dest / rel
            if src.is_dir():
                shutil.copytree(src, target, copy_function=shutil.copy2)
            elif src.is_file():
                shutil.copy2(src, target)
    return sorted(files)


# --------------------------------------------------------------------------- #
# cost
# --------------------------------------------------------------------------- #

def _cost_in(data: Any) -> float | None:
    if not isinstance(data, dict):
        return None
    for path in (("usage", "cost_usd"), ("cost_usd",), ("engine", "cost_usd")):
        value: Any = data
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if isinstance(value, (int, float)):
            extra = (data.get("synth") or {}).get("cost_usd") if isinstance(
                data.get("synth"), dict) else 0
            return float(value) + float(extra or 0)
    return None


def _stamps(root: Path) -> dict[str, int]:
    """mtime_ns of every manifest and decision, to tell what a pass wrote."""
    out = {}
    for rel in MANIFEST.values():
        p = root / rel
        if p.is_file():
            out[rel] = p.stat().st_mtime_ns
    base = root / "decisions"
    if base.is_dir():
        for p in base.glob("*/*.json"):
            out[str(p.relative_to(root))] = p.stat().st_mtime_ns
    return out


def stage_cost(root: Path, stage: str, before: dict[str, int]) -> float:
    """What a stage reported spending, if it wrote its manifest during the
    pass: the manifest's own account, else its new decisions' `cost_usd`."""
    root = Path(root)
    rel = MANIFEST.get(stage)
    if rel and (root / rel).is_file() and before.get(rel) != (root / rel).stat().st_mtime_ns:
        cost = _cost_in(read_json(root / rel, warn=False))
        if cost is not None:
            return round(cost, 4)
    total = 0.0
    base = root / "decisions" / stage
    if base.is_dir():
        for p in base.glob("*.json"):
            key = str(p.relative_to(root))
            if before.get(key) != p.stat().st_mtime_ns:
                total += float((read_json(p, warn=False) or {}).get("cost_usd") or 0)
    return round(total, 4)


# --------------------------------------------------------------------------- #
# comparing passes
# --------------------------------------------------------------------------- #

def blocking_mechanical(card: dict) -> set[str]:
    return {fingerprint(f) or f"{f.get('kind')}|{f.get('shot')}"
            for f in card.get("mechanical") or [] if f.get("severity") == "blocking"}


def judge(best: dict, new: dict, *, tolerance: float = TOLERANCE) -> dict:
    """Keep only a measured improvement: the overall up, no category down by
    more than the tolerance, no new blocking mechanical finding."""
    diff = qa.compare(best, new)
    reasons = []
    if diff["overall_delta"] <= 0:
        reasons.append(f"overall {new.get('overall')} did not improve on "
                       f"{best.get('overall')} ({diff['overall_delta']:+.2f})")
    for key, delta in diff["deltas"].items():
        if delta < -tolerance - 1e-9:
            reasons.append(f"{key} dropped {delta:+.1f}, more than the {tolerance:g} "
                           "tolerance")
    for fp in sorted(blocking_mechanical(new) - blocking_mechanical(best)):
        reasons.append(f"new blocking mechanical finding: {fp}")
    return {"keep": not reasons, "reasons": reasons, "deltas": diff["deltas"],
            "overall_delta": diff["overall_delta"], "against": best.get("pass")}


def reached(card: dict, target: float) -> bool:
    if card.get("verdict") == qa.READY:
        return True
    try:
        return float(card.get("overall", 0)) >= target and not card.get("blocking")
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #

def cli_runner(argv: list[str]) -> int:
    """Production: one `yt` stage in this process, as `yt run` does."""
    from . import cli
    args = cli.build_parser().parse_args(argv)
    return int(args.run(args) or 0)


def _summary(card: dict | None) -> dict | None:
    if not card:
        return None
    return {"pass": card.get("pass"), "overall": card.get("overall"),
            "verdict": card.get("verdict"), "scores": card.get("scores"),
            "blocking": len(card.get("blocking") or [])}


class Improver:
    """One project's improvement loop."""

    def __init__(self, project: workspace.Project, idea, *, model: str = "sonnet",
                 effort: str | None = None, fallback_model: str | None = None,
                 max_passes: int = 3, budget_usd: float = 15.0, target: float = 9.0,
                 dry_run: bool = False, from_pass: str | None = None,
                 quality: str = "final", quiet: bool = False,
                 runner: Callable[[list[str]], int] | None = None,
                 doctor: Callable[[workspace.Project], list[tuple]] | None = None,
                 house_rules: str = "", house_digest: dict | None = None,
                 say: Callable[[str], None] | None = None) -> None:
        self.project = project
        self.root = Path(project.root)
        self.idea = idea
        self.model = model
        self.effort = effort
        self.fallback_model = fallback_model
        self.max_passes = max(0, int(max_passes))
        self.budget = float(budget_usd)
        self.target = float(target)
        self.dry_run = dry_run
        self.from_pass = from_pass
        self.quality = quality
        self.quiet = quiet
        self.runner = runner or cli_runner
        self.doctor = doctor or doctor_rows
        self.say = say or print
        self.h = Harness(self.root, "improve", model=model, effort=effort,
                         fallback_model=fallback_model, role=ROLE, house=house_rules,
                         house_digest=house_digest or {}, verbose=not quiet)
        self.spent_stages = 0.0
        self.defects: dict[str, dict] = {}
        self.assigned: dict[str, dict[str, str]] = {}
        self.findings: dict[str, list[dict]] = {}
        self.states: dict[str, list[dict]] = {}

    # -- plumbing ----------------------------------------------------------

    def _log(self, msg: str) -> None:
        if not self.quiet:
            print(msg, file=sys.stderr)

    @property
    def base(self) -> list[str]:
        argv = ["--project", str(self.root), "--model", self.model]
        if self.effort:
            argv += ["--effort", self.effort]
        if self.fallback_model:
            argv += ["--fallback-model", self.fallback_model]
        if self.quiet:
            argv.append("--quiet")
        return argv

    def _run(self, argv: list[str]) -> int:
        self._log(f"\n== yt {' '.join(argv)}")
        try:
            return int(self.runner(argv) or 0)
        except SystemExit as exc:
            return exc.code if isinstance(exc.code, int) else 1
        except Exception as exc:       # noqa: BLE001 - a stage's crash fails the pass
            self._log(f"{argv[0]} raised {type(exc).__name__}: {exc}")
            return 1

    @property
    def spent(self) -> float:
        return round(self.spent_stages + float(self.h.report()["cost_usd"]), 4)

    def _card(self, pdir: Path | None) -> dict | None:
        if pdir is None:
            return None
        card = read_json(pdir / "scorecard.json", warn=False)
        if isinstance(card, dict):
            card.setdefault("pass", pdir.name)
            return card
        return None

    def _qa(self) -> dict | None:
        before = qa.latest_pass(self.root)
        self._run(["qa", *self.base])
        after = qa.latest_pass(self.root)
        if after is None or after == before:
            return None
        card = self._card(after)
        if card:
            self.spent_stages += float((card.get("engine") or {}).get("cost_usd") or 0)
        return card

    def _stale(self, pdir: Path) -> bool:
        card = pdir / "scorecard.json"
        if not card.is_file():
            return True
        newest = max((p.stat().st_mtime for p in (self.root / "video.mp4",
                                                  self.root / "scene.xml") if p.exists()),
                     default=0)
        return card.stat().st_mtime < newest

    def _evidence(self) -> dict | None:
        if self.from_pass:
            card = self._card(self.root / qa.QA_DIR / self.from_pass)
            if card is None:
                self.say(f"error: qa/{self.from_pass} has no scorecard.json")
            return card
        latest = qa.latest_pass(self.root)
        earlier = read_json(self.root / REPORT_JSON, warn=False) or {}
        reverted = {p.get("qa_pass") for p in earlier.get("passes") or []
                    if p.get("decision") == "revert"}
        if latest is not None and latest.name in reverted and earlier.get("best"):
            # The latest pass judged a video that was put back; the best kept one stands.
            best = self.root / qa.QA_DIR / str(earlier["best"].get("pass"))
            if best.is_dir():
                latest = best
        if latest is not None and not self._stale(latest):
            return self._card(latest)
        if self.dry_run:
            if latest is None:
                self.say("error: no QA pass to route, and a dry run renders nothing; "
                         "run `yt qa` first")
                return None
            self.say(f"note: {latest.name} is older than the video it should judge; a "
                     "dry run routes it anyway")
            return self._card(latest)
        self._log("the latest QA pass is missing or older than the video; scoring first")
        return self._qa()

    # -- defects across passes ---------------------------------------------

    def _new_defect(self, f: dict, pass_name: str) -> str:
        did = f"D{len(self.defects) + 1}"
        self.defects[did] = {"id": did, "fingerprint": f.get("fingerprint"),
                             "text": f["problem"][:400], "timestamp": f.get("timestamp"),
                             "first": pass_name, "survived": 0}
        return did

    def _assign(self, pass_name: str, findings: list[dict], briefed: set[str]) -> None:
        """Which defect each finding of this pass is: a mechanical one by its
        fingerprint, a prose one by the model's judgement against the
        defects the pass before was briefed on."""
        by_fp = {d["fingerprint"]: did for did, d in self.defects.items() if d["fingerprint"]}
        out: dict[str, str] = {}
        prose = []
        for f in findings:
            if f.get("fingerprint"):
                out[f["id"]] = by_fp.get(f["fingerprint"]) or self._new_defect(f, pass_name)
                by_fp[f["fingerprint"]] = out[f["id"]]
            else:
                prose.append(f)
        candidates = [{"id": did, "text": self.defects[did]["text"],
                       "timestamp": self.defects[did].get("timestamp")}
                      for did in sorted(briefed) if not self.defects[did]["fingerprint"]]
        if prose and candidates:
            answer = self.h.decide(f"same-{pass_name}", "\n\n".join([
                "Each defect below was briefed to a stage before this QA pass. For each "
                "new finding, say which defect it is the same problem as -- the same "
                "thing visibly wrong at about the same place, however differently it is "
                "worded -- or leave it out when it is a different problem. A defect "
                "matched here survived the pass.",
                brief("Defects briefed before the pass", candidates),
                brief("Findings of the new pass", [{"id": f["id"], "text": f["problem"],
                                                    "timestamp": f.get("timestamp")}
                                                   for f in prose])]), SAME_SCHEMA)
            known = {c["id"] for c in candidates}
            for m in answer.get("matches") or []:
                if (isinstance(m, dict) and m.get("defect") in known
                        and m.get("finding") in {f["id"] for f in prose}):
                    out.setdefault(str(m["finding"]), str(m["defect"]))
        for f in prose:
            if f["id"] not in out:
                out[f["id"]] = self._new_defect(f, pass_name)
        self.assigned[pass_name] = out
        self.findings[pass_name] = findings

    def _evidence_findings(self, card: dict) -> list[dict]:
        gate = read_json(self.root / "gate.json", warn=False)
        rows = list(self.doctor(self.project))
        # Doctor's notes are not findings, but they say what already exists and
        # what waits on the operator, which is what tells a stage from a human.
        self.states[str(card.get("pass"))] = [
            {"area": area, "note": message, "command": fix}
            for level, area, message, fix in rows if level == "note"]
        return collect(card, gate=gate, doctor=rows)

    # -- one pass -------------------------------------------------------------

    def _next_name(self) -> str:
        base = self.root / IMPROVE_DIR
        taken = [int(m.group(1)) for p in (base.iterdir() if base.is_dir() else [])
                 if (m := re.match(r"^pass-(\d+)$", p.name))]
        return f"pass-{(max(taken) + 1 if taken else 1):02d}"

    def run(self) -> dict:
        started = time.strftime("%Y-%m-%dT%H:%M:%S")
        record: dict = {"idea": {"title": self.idea.title}, "project": str(self.root),
                        "started": started, "dry_run": self.dry_run,
                        "max_passes": self.max_passes, "budget_usd": self.budget,
                        "target": self.target, "quality": self.quality, "passes": [],
                        "human": [], "start": None, "best": None, "spent_usd": 0.0,
                        "stop": None}
        card = self._evidence()
        if card is None:
            record["stop"] = {"reason": "no-evidence",
                              "detail": "no QA pass could be read or scored"}
            return self._finish(record)
        record["start"] = _summary(card)
        best = evidence = card
        attempts: dict[str, list[str]] = {}
        last_cost: float | None = None
        findings = self._evidence_findings(card)
        self._assign(card["pass"], findings, set())

        while True:
            if len(record["passes"]) >= self.max_passes and not self.dry_run:
                record["stop"] = {"reason": "max-passes",
                                  "detail": f"{self.max_passes} pass(es) run"}
                break
            findings = self.findings.get(evidence["pass"]) or self._evidence_findings(evidence)
            if evidence["pass"] not in self.assigned:
                self._assign(evidence["pass"], findings, set())
            routing = route(self.h, findings, name=f"route-{evidence['pass']}", context={
                "title": self.idea.title, "pass": evidence["pass"],
                "verdict": evidence.get("verdict"), "overall": evidence.get("overall"),
                "scores": evidence.get("scores"), "bar": evidence.get("bar"),
                "project_state": self.states.get(evidence["pass"], [])})
            seen = {h["problem"] for h in record["human"]}
            record["human"] += [h for h in routing["human"] if h["problem"] not in seen]
            stages = plan(routing["stages"], self.root)
            argvs = stage_argvs(stages, routing["stages"], self.base, quality=self.quality,
                                root=self.root)
            guess = estimate(stages, last_cost)
            name = self._next_name()
            assigned = self.assigned.get(evidence["pass"], {})
            survived = {fid: self.defects[did]["survived"] for fid, did in assigned.items()
                        if self.defects[did]["survived"]}

            if not routing["stages"]:
                record["stop"] = {"reason": "human" if routing["human"] else "nothing",
                                  "detail": "every finding left needs a human decision"
                                  if routing["human"] else "no finding to route"}
                break
            if self.dry_run:
                write_feedback(self.root, routing, findings, from_pass=evidence["pass"],
                               card=evidence, improve_pass=name, survived=survived)
                record["plan"] = {"from_pass": evidence["pass"],
                                  "routed": self._routed(routing, findings),
                                  "routes": routing["routes"], "stages": stages,
                                  "argv": argvs + [["gate", *self.base], ["qa", *self.base]],
                                  "estimate_usd": guess}
                record["stop"] = {"reason": "dry-run", "detail": "routed and briefed; "
                                  "nothing run"}
                self._print_plan(record, findings)
                break
            remaining = self.budget - self.spent
            if guess > remaining:
                record["stop"] = {"reason": "budget", "detail":
                                  f"the next pass is estimated at ${guess:.2f} and "
                                  f"${remaining:.2f} of ${self.budget:.2f} is left"}
                break

            write_feedback(self.root, routing, findings, from_pass=evidence["pass"],
                           card=evidence, improve_pass=name, survived=survived,
                           attempts=attempts)
            pdir = self.root / IMPROVE_DIR / name
            snap = pdir / "snapshot"
            snapshot(self.root, stages + ["gate", "qa"], snap)
            before = _stamps(self.root)
            entry = {"name": name, "from_pass": evidence["pass"],
                     "routed": self._routed(routing, findings), "routes": routing["routes"],
                     "stages": stages, "argv": [], "exit": {}, "estimate_usd": guess,
                     "overall_before": best.get("overall"), "scores_before": best.get("scores"),
                     "best_before": best.get("pass")}
            record["passes"].append(entry)
            self._log(f"\n[improve] {name}: {', '.join(stages)} from {evidence['pass']} "
                      f"(estimated ${guess:.2f})")
            failed = None
            for stage, argv in zip(stages, argvs):
                entry["argv"].append(argv)
                code = self._run(argv)
                entry["exit"][stage] = code
                if code:
                    failed = f"{stage} exited {code}"
                    break
            new = None
            if failed is None:
                entry["argv"].append(["gate", *self.base])
                entry["exit"]["gate"] = self._run(["gate", *self.base])
                entry["argv"].append(["qa", *self.base])
                new = self._qa()
                if new is None:
                    failed = "qa filed no new pass"
            cost = sum(stage_cost(self.root, s, before) for s in stages + ["gate"])
            self.spent_stages += cost
            cost += float(((new or {}).get("engine") or {}).get("cost_usd") or 0)
            entry["cost_usd"] = round(cost, 4)
            last_cost = cost or None

            if failed:
                restore(self.root, snap)
                entry.update(decision="failed", reasons=[failed])
                record["stop"] = {"reason": "stage-failed", "detail": f"{failed}; the "
                                  "snapshot was restored and the notes left open"}
                self._save_pass(pdir, entry)
                break

            verdict = judge(best, new)
            entry.update(qa_pass=new["pass"], overall_after=new.get("overall"),
                         scores_after=new.get("scores"), verdict_after=new.get("verdict"),
                         deltas=verdict["deltas"], overall_delta=verdict["overall_delta"],
                         reasons=verdict["reasons"],
                         decision="keep" if verdict["keep"] else "revert")
            briefed = {assigned[fid] for e in routing["stages"].values()
                       for fid in e["findings"] if fid in assigned}
            new_findings = self._evidence_findings(new)
            self._assign(new["pass"], new_findings, briefed)
            present = set(self.assigned[new["pass"]].values())
            for did in briefed:
                d = self.defects[did]
                d["survived"] = d["survived"] + 1 if did in present else 0
            entry["survivors"] = {did: self.defects[did]["survived"] for did in sorted(briefed)
                                  if self.defects[did]["survived"]}
            entry["fixed"] = sorted(did for did in briefed if did not in present)
            entry["briefed_defects"] = {fid: assigned[fid] for e in routing["stages"].values()
                                        for fid in e["findings"] if fid in assigned}
            if verdict["keep"]:
                best = evidence = new
                attempts = {}
                self._log(f"[improve] {name}: kept ({new.get('overall')} against "
                          f"{entry['overall_before']})")
            else:
                restore(self.root, snap)
                for stage in routing["stages"]:
                    attempts.setdefault(stage, []).append(
                        f"{name} ({new['pass']}, overall {new.get('overall')}): "
                        + "; ".join(verdict["reasons"]))
                evidence = best
                self._log(f"[improve] {name}: reverted ({'; '.join(verdict['reasons'])})")
            close_feedback(self.root, name)
            self._save_pass(pdir, entry)
            record["best"] = _summary(best)
            record["spent_usd"] = self.spent
            self._write(record)

            if verdict["keep"] and reached(new, self.target):
                record["stop"] = {"reason": "ready", "detail":
                                  f"{new['pass']}: {new.get('verdict')} at {new.get('overall')}"}
                break
            worst = [d for d in self.defects.values() if d["survived"] >= SURVIVOR_PASSES]
            if worst:
                named = "; ".join(f"{d['id']} {d['fingerprint'] or d['text'][:160]} "
                                  f"(briefed {d['survived']} times)" for d in worst)
                record["stop"] = {"reason": "survivor", "detail":
                                  f"survived {SURVIVOR_PASSES} briefed passes, so the "
                                  f"approach is wrong, not the parameters: {named}"}
                break

        record["best"] = _summary(best)
        record["defects"] = self.defects
        return self._finish(record)

    # -- the record -----------------------------------------------------------

    def _routed(self, routing: dict, findings: list[dict]) -> dict:
        by_id = {f["id"]: f for f in findings}
        return {stage: {"findings": [{k: by_id[fid].get(k) for k in
                                      ("id", "source", "kind", "shot", "timestamp",
                                       "severity", "problem", "root_cause")}
                                     for fid in e["findings"]],
                        "must_change": e["must_change"], "check": e["check"]}
                for stage, e in routing["stages"].items()}

    def _save_pass(self, pdir: Path, entry: dict) -> None:
        pdir.mkdir(parents=True, exist_ok=True)
        write_json(pdir / "pass.json", entry)

    def _write(self, record: dict) -> None:
        if self.dry_run:
            return
        write_json(self.root / REPORT_JSON, record)

    def _finish(self, record: dict) -> dict:
        record["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        record["spent_usd"] = self.spent
        record["engine"] = self.h.report()
        if self.dry_run:
            (self.root / IMPROVE_DIR).mkdir(parents=True, exist_ok=True)
            write_json(self.root / IMPROVE_DIR / "plan.json", record)
            return record
        self._write(record)
        if record.get("start"):
            (self.root / REPORT_MD).write_text(report_md(record, self.root), encoding="utf-8")
        self._print_summary(record)
        return record

    def _print_plan(self, record: dict, findings: list[dict]) -> None:
        p = record["plan"]
        self.say(f"{self.idea.title}: routing qa/{p['from_pass']} "
                 f"({record['start']['verdict']} at {record['start']['overall']})")
        self.say("\n  finding -> stage")
        for f in findings:
            r = p["routes"][f["id"]]
            dup = f" (= {r['duplicate_of']})" if r.get("duplicate_of") else ""
            self.say(f"    {f['id']:<4} {qa._stamp(f.get('timestamp')) if f.get('timestamp') is not None else '':<10}"
                     f" {f['source']:<10} -> {r['stage']:<13}{dup} {f['problem'][:70]}")
        for stage, e in p["routed"].items():
            self.say(f"\n  brief for {stage} ({len(e['findings'])} finding(s)), "
                     f"feedback/{stage}.md:")
            for m in e["must_change"]:
                self.say(f"    - {m}")
            self.say(f"    check: {e['check']}")
        if record["human"]:
            self.say("\n  needs a human decision:")
            for h in record["human"]:
                self.say(f"    [{h['route']}] {h['problem'][:110]}")
        self.say(f"\n  would run: {' -> '.join(p['stages'] + ['gate', 'qa'])}")
        for argv in p["argv"]:
            self.say(f"    yt {' '.join(argv)}")
        self.say(f"  estimated cost: ${p['estimate_usd']:.2f} (routing so far "
                 f"${self.spent:.2f})")

    def _print_summary(self, record: dict) -> None:
        start, best = record["start"] or {}, record["best"] or record["start"] or {}
        self.say(f"\n{self.idea.title} -- improve: {record['stop']['reason']} "
                 f"({record['stop']['detail']})")
        self.say(f"  start {start.get('pass')} {start.get('overall')}  ->  best "
                 f"{best.get('pass')} {best.get('overall')} {best.get('verdict')}")
        for p in record["passes"]:
            self.say(f"  {p['name']}: {', '.join(p['stages'])} -> {p.get('qa_pass', '-')} "
                     f"{p.get('overall_before')} -> {p.get('overall_after', '-')} "
                     f"{p.get('decision')} (${p.get('cost_usd', 0):.2f})")
        if record["human"]:
            self.say(f"  {len(record['human'])} finding(s) need a human decision; see "
                     f"{REPORT_MD}")
        self.say(f"  spent ${record['spent_usd']:.2f} of ${record['budget_usd']:.2f}")


# --------------------------------------------------------------------------- #
# the report, in the shape of docs/production.md §11
# --------------------------------------------------------------------------- #

def report_md(record: dict, root: Path) -> str:
    stamp = qa._stamp
    root = Path(root)
    start = record.get("start") or {}
    best = record.get("best") or start
    best_card = read_json(root / qa.QA_DIR / str(best.get("pass")) / "scorecard.json",
                          warn=False) or {}
    lines = [f"# Improvement report: {record['idea']['title']}", "",
             f"## 1. Verdict", "", f"**{best.get('verdict', '?')}** -- best pass "
             f"{best.get('pass')} at an overall of {best.get('overall')} (started from "
             f"{start.get('pass')} at {start.get('overall')}). Stopped: "
             f"{record['stop']['reason']} -- {record['stop']['detail']}.", "",
             "## 2. Scorecard", "", f"| Category | {start.get('pass')} | {best.get('pass')} | Δ |",
             "|---|---|---|---|"]
    for key in qa.CATEGORIES:
        a = (start.get("scores") or {}).get(key)
        b = (best.get("scores") or {}).get(key)
        try:
            d = f"{float(b) - float(a):+.1f}"
        except (TypeError, ValueError):
            d = ""
        lines.append(f"| {key} | {a} | {b} | {d} |")
    lines.append(f"| **overall** | {start.get('overall')} | {best.get('overall')} | |")
    lines += ["", "## 3. Changes implemented", ""]
    if not record["passes"]:
        lines.append("None: no pass ran.")
    for p in record["passes"]:
        label = {"keep": "kept", "revert": "reverted"}.get(p.get("decision"),
                                                           str(p.get("decision")))
        head = (f"### {p['name']}: {', '.join(p['stages'])} from {p['from_pass']} -> "
                f"{p.get('qa_pass', '-')} -- **{label}**")
        lines += [head, "", f"Overall {p.get('overall_before')} -> {p.get('overall_after', '-')}, "
                  f"cost ${p.get('cost_usd', 0):.2f}."
                  + (f" Why not kept: {'; '.join(p.get('reasons') or [])}."
                     if p.get("decision") != "keep" else ""), ""]
        fixed = set(p.get("fixed") or [])
        defect_of = p.get("briefed_defects") or {}
        for stage, e in (p.get("routed") or {}).items():
            correction = "; ".join(e.get("must_change") or []) or "-"
            for f in e.get("findings") or []:
                did = defect_of.get(f["id"])
                if p.get("decision") == "keep" and did in fixed:
                    result = f"verified: not reported by {p.get('qa_pass')}"
                elif did in fixed:
                    result = f"gone in {p.get('qa_pass')}, but the pass was reverted"
                else:
                    result = f"still reported by {p.get('qa_pass', 'the next pass')}"
                when = stamp(f.get("timestamp")) if f.get("timestamp") is not None else "-"
                shot = f" shot {f['shot']}" if f.get("shot") else ""
                kind = f" [{f['kind']}]" if f.get("kind") else ""
                lines.append(f"- {when}{shot}{kind} → {f.get('problem', '')} → root cause: "
                             f"{f.get('root_cause') or '-'} → correction ({stage}): "
                             f"{correction} → {result}")
        lines.append("")
    lines += ["## 4. Remaining issues", "", "### Blocking", ""]
    remaining = collect(best_card) if best_card else []
    blocking = [f for f in remaining if f.get("severity") == "blocking"]
    minor = [f for f in remaining if f.get("severity") != "blocking"]
    for group in (blocking,):
        lines += [f"- {stamp(f.get('timestamp'))} [{f['source']}"
                  + (f" {f['kind']}" if f.get("kind") else "") + f"] {f['problem']} "
                  f"(owner {f.get('owner') or '?'})" for f in group] or ["None."]
    lines += ["", "### Minor / acceptable", ""]
    lines += [f"- {stamp(f.get('timestamp'))} [{f['source']}] {f['problem']} "
              f"(owner {f.get('owner') or '?'})" for f in minor] or ["None."]
    if record.get("human"):
        lines += ["", "### Needs a human decision (not looped)", ""]
        lines += [f"- [{h['route']}] {h['problem']} -- {h.get('why_routed', '')}"
                  for h in record["human"]]
    build = read_json(root / "build.json", warn=False) or {}
    scene = build.get("scene") or {}
    video = root / "video.mp4"
    lines += ["", "## 5. Final artifact", "",
              f"- `{video}`" + (f", {video.stat().st_size / 1e6:.1f} MB" if video.is_file() else
                                " (missing)"),
              f"- runtime {scene.get('duration', '?')} s, {scene.get('width', '?')}x"
              f"{scene.get('height', '?')} at {scene.get('fps', '?')} fps, rendered at "
              f"--quality {record.get('quality')}",
              f"- audio: {build.get('audio_tracks', 0) or 0} track(s)"
              + (f" on buses {', '.join(map(str, build.get('buses') or []))}"
                 if build.get("buses") else ""), "",
              "## 6. Publication rationale", ""]
    if best.get("verdict") == qa.READY:
        lines.append(f"{best.get('pass')} meets the bar of docs/production.md §2.")
    else:
        lines.append(f"NOT READY. {best.get('pass')} misses the bar:")
        lines += [f"- {r}" for r in best_card.get("bar") or []]
        if best_card.get("rationale"):
            lines += ["", str(best_card["rationale"])]
    lines += ["", f"Spent ${record.get('spent_usd', 0):.2f} of ${record.get('budget_usd', 0):.2f}.", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "improve", parents=list(parents),
        help="route QA's findings to the stages that own them, re-run, keep what improves",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--max-passes", type=int, default=3, dest="max_passes",
                   help="improvement passes at most (default 3)")
    p.add_argument("--budget-usd", type=float, default=15.0, dest="budget_usd",
                   help="spend cap for the whole loop, stages included (default 15)")
    p.add_argument("--target", type=float, default=9.0,
                   help="stop when a kept pass reaches this overall with nothing "
                        "blocking, or READY TO PUBLISH (default 9.0)")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="route the findings and write the feedback, run no stage, "
                        "print the plan and its estimated cost")
    p.add_argument("--from-pass", metavar="PASS", dest="from_pass",
                   help="the QA pass to start from (default the latest)")
    p.add_argument("--quality", choices=["draft", "preview", "final"], default="final",
                   help="render quality of each pass's encode (default final)")
    p.set_defaults(run=run)
    return p


def run(args) -> int:
    idea, project = workspace.resolve(args)
    rules, digest = house.for_args(args, project, "improve")
    improver = Improver(project, idea, model=args.model,
                        effort=getattr(args, "effort", None),
                        fallback_model=getattr(args, "fallback_model", None),
                        max_passes=args.max_passes, budget_usd=args.budget_usd,
                        target=args.target, dry_run=args.dry_run, from_pass=args.from_pass,
                        quality=args.quality, quiet=args.quiet, house_rules=rules,
                        house_digest=digest)
    try:
        record = improver.run()
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0 if record["stop"]["reason"] in ("ready", "dry-run") else 1
