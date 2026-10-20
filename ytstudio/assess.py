"""Can this pipeline produce the video you describe? Ask, and prove it with a frame.

    yt assess "a photoreal 3D water droplet falling into still water, 30 s, no narration"
    yt assess --title monty                 # the project's own treatment and script
    yt assess "..." --no-proof --budget-usd 1

A claude_engine agent investigates rather than opines: it works in a
workspace holding `sr.py` (ask the schema, check a document), the schema
cheat-sheet generated from `describe()`, the engine's own capability manifest
(what it renders, approximates or only reports), the render host's GPU and
memory, and the constructs already proven on this engine. It answers with a
structured verdict -- feasible, partly, or not; how; with which constructs;
what the engine cannot do; what it would cost in render time -- and writes a
proof document of a few seconds exercising the decisive constructs. The
harness validates the proof with the SDK and the engine, renders one frame of
it on the render host, and keeps the frame beside the verdict.

The intelligence is the model's; this module owns only the workspace, the
checks, the render and the files.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from claude_engine import ClaudeEngine, ClaudeError, EngineConfig
from . import render, sdkkit, workspace
from .core import Idea, _key, read_json, slugify, write_json
from .vfx import MISSION, STACK, Workspace

#: The stack, as every brief downstream of the assessment must name it. The
#: styles stage once recommended "a Blender camera"; nothing in the loop
#: could take one.
STACK_LINE = (f"The production stack is {STACK}: the only renderer is scene-render; "
              "there is no Blender, After Effects or Manim in the loop. What the "
              "schema cannot express and the engine cannot render is not produced.")

ASSESS_DIR = "assess"
AGENT_TOOLS = ("Read", "Glob", "Grep", "Write", "Edit", "Bash")

SYSTEM = (
    "You assess whether a described video can be produced on scene-render and "
    "sr-core, by investigation, not opinion. You ask the schema "
    "(`python3 kit.py describe <element>`, `python3 kit.py enum <element> "
    "<attribute>`, `python3 kit.py elements`), you read kit/ENGINE.md and engine.json "
    "for constructs the engine only approximates, ignores per renderer or reports, you "
    "read the render host's resources, and you prove the decisive constructs by "
    "writing a short Python program with the sr-core SDK (srkit) that builds a proof "
    "document, building it with `python3 kit.py build` until it reports 0 errors. You "
    "distinguish what the schema can express from what the engine renders, and you say "
    "plainly what it cannot do."
)

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "feasible": {"type": "string", "enum": ["yes", "partly", "no"]},
        "summary": {"type": "string"},
        "approach": {"type": "array", "items": {"type": "string"}},
        "constructs": {"type": "array", "items": {"type": "string"}},
        "engine_limits": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "render_estimate": {"type": "string"},
        "pipeline": {"type": "array", "items": {"type": "string"}},
        "missing": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["feasible", "summary", "approach", "constructs", "engine_limits",
                 "risks", "render_estimate", "pipeline", "missing"],
}


def _content_words(text: str) -> set[str]:
    return {_key(w) for w in re.findall(r"\w+", text or "") if len(w) > 2}


def _about_this(record: dict, idea: Idea) -> bool:
    """Whether an assessment on disk describes this idea: its title verbatim
    (how `yt assess --title` records it), or a premise that mostly overlaps."""
    description = str(record.get("description") or "")
    if not description:
        return False
    if _key(description) in (_key(idea.title), _key(idea.description or "")):
        return True
    mine, theirs = _content_words(idea.description or ""), _content_words(description)
    if not mine or not theirs:
        return False
    return len(mine & theirs) / min(len(mine), len(theirs)) >= 0.6


def find_verdict(project_root: Path | str, *, work: Path | str | None = None,
                 idea: Idea | None = None) -> tuple[dict, Path] | None:
    """The latest assessment of this video: the project's own `assess.json`,
    else the newest one under `<work>/.assess/` that is about this idea."""
    root = Path(project_root)
    own = root / "assess.json"
    record = read_json(own, warn=False) if own.is_file() else None
    if record and record.get("verdict"):
        return record, own
    pool = Path(work) / ".assess" if work else root.parent / ".assess"
    if idea is None or not pool.is_dir():
        return None
    slugs = {slugify(idea.title, 40), slugify(idea.description or "", 40)}
    found: list[tuple[float, dict, Path]] = []
    for path in sorted(pool.glob("*/assess.json")):
        record = read_json(path, warn=False)
        if not record or not record.get("verdict"):
            continue
        if path.parent.name in slugs or _about_this(record, idea):
            found.append((path.stat().st_mtime, record, path))
    if not found:
        return None
    _, record, path = max(found, key=lambda row: row[0])
    return record, path


def stack_brief(project_root: Path | str, *, work: Path | str | None = None,
                idea: Idea | None = None) -> str:
    """What the stack makes of this video, for the briefs of later stages:
    the verdict's constructs (renders), engine limits (approximates), what is
    missing (cannot do) and the render estimate. Empty when nothing has been
    assessed."""
    found = find_verdict(project_root, work=work, idea=idea)
    if not found:
        return ""
    record, path = found
    verdict = record.get("verdict") or {}
    lines = [STACK_LINE, "",
             f"The assessment (`yt assess`, {path.name} of "
             f"{path.parent.name}) found the piece {verdict.get('feasible', '?')} "
             f"feasible: {verdict.get('summary', '')}".strip()]
    for title, key, cap in (
            ("What the engine renders for it (constructs it proved)", "constructs", 16),
            ("What it only approximates (engine limits)", "engine_limits", 8),
            ("What it cannot do -- nothing in the pipeline supplies this", "missing", 8),
            ("Risks it named", "risks", 6)):
        rows = [str(r) for r in (verdict.get(key) or []) if r][:cap]
        if rows:
            lines += ["", f"{title}:"] + [f"- {r}" for r in rows]
    if verdict.get("render_estimate"):
        lines += ["", f"Render estimate: {verdict['render_estimate']}"]
    return "\n".join(lines)


def engine_facts(host: str) -> dict:
    """What the engine says about itself, and what the host has to run it."""
    facts: dict = {"host": host or "local"}
    binary = render.engine()
    if binary is not None and not host:
        try:
            out = subprocess.run([str(binary), "capabilities"], capture_output=True,
                                 text=True, timeout=30).stdout
            facts["capabilities"] = json.loads(out[out.index("{"):])
        except Exception as exc:
            facts["capabilities_error"] = str(exc)[:200]
        return facts
    if not host:
        return facts
    binary_expr = os.environ.get("RS_RENDER_REMOTE_BIN") or (
        "$(command -v scene-render || echo $HOME/src/scene-render/target/release/scene-render)")
    script = (f"B={binary_expr}; $B capabilities; echo '=====GPUS'; $B gpus; "
              "echo '=====MEM'; free -g | sed -n 2p; nproc; echo '=====DISK'; df -h /tmp | tail -1")
    try:
        proc = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, script],
                              capture_output=True, text=True, timeout=60)
        caps, _, rest = proc.stdout.partition("=====GPUS")
        gpus, _, rest = rest.partition("=====MEM")
        mem, _, disk = rest.partition("=====DISK")
        facts["capabilities"] = json.loads(caps[caps.index("{"):]) if "{" in caps else {}
        facts["gpus"] = gpus.strip()
        facts["memory_and_cores"] = mem.strip()
        facts["disk"] = disk.strip()
    except Exception as exc:
        facts["capabilities_error"] = str(exc)[:200]
    return facts


def prepare(ws: Workspace, *, description: str, facts: dict, project_context: str) -> Path:
    ws.prepare(tokens={"background": "#101418", "ink": "#F2F5F7", "accent": "#3DD6FF",
                       "muted": "#8E9BB0"}, frame=(1920, 1080))
    write_json(ws.dir / "engine.json", facts)
    lines = [f"# Can we produce this?", "", description, ""]
    if project_context:
        lines += ["## What the project already says", "", project_context, ""]
    lines += ["## Mission", "", MISSION, "",
              "## What is in this directory", "",
              "- `kit.py` -- `elements`, `describe <el>`, `enum <el> <attr>`, `build <program>`",
              "- `srkit.py` -- what a program imports: `from srkit import S, FRAME, save`",
              "- `kit/SDK-GUIDE.md`, `kit/ENGINE.md`, `kit/examples/` -- the SDK's guide, what the "
              "engine does per renderer, programs that rendered on this engine",
              "- `schema.md` -- a cheat-sheet generated from the schema; describe anything not in it",
              "- `examples.scene.xml` -- more constructs, as XML, that rendered on this engine",
              "- `engine.json` -- the engine's capability manifest (entries marked approximate or "
              "reported are rendered differently from what the document says, or only logged), "
              "the render host's GPUs, memory, cores and disk", "",
              "## Deliverables", "",
              "1. `proof.py`: a Python program with the SDK that saves `proof.scene.xml`, a "
              "complete scene-render document of 2-4 seconds, 1920x1080, that exercises the "
              "constructs the verdict depends on (for a 3D request that is an object3D with a "
              "real material, lights, a camera and its renderer; for a simulation, the "
              "simulating element). Run `python3 kit.py build proof.py` and fix every error "
              "until it reports 0 error(s); the stage builds the program itself.",
              "2. `assessment.json` with exactly these keys: feasible (yes|partly|no), summary, "
              "approach[] (the shots and how each is built), constructs[] (schema elements and "
              "attributes used, e.g. `object3D@primitive=sphere`, `material@transmission`), "
              "engine_limits[] (from engine.json: what will be approximate or unsupported for "
              "this request), risks[], render_estimate (frames x renderer x host, as a time), "
              "pipeline[] (the `yt` commands to run, in order), missing[] (what no stage can "
              "provide today).",
              "", "Investigate first; write both files; then reply with one line."]
    path = ws.dir / "brief.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def investigate(ws: Workspace, *, model: str, budget_usd: float, verbose: bool) -> dict:
    engine = ClaudeEngine(EngineConfig(
        tools=AGENT_TOOLS, model=model, permission_mode="dontAsk", cwd=ws.dir,
        max_turns_budget_usd=budget_usd, timeout=1500.0))
    texts: list[str] = []
    calls = 0
    try:
        for event in engine.stream("Read brief.md and do what it says.", system=SYSTEM):
            if event.subtype == "tool_use" and event.tool_input:
                calls += 1
                what = event.tool_input.get("command") or event.tool_input.get("file_path") or ""
                if verbose:
                    print(f"    {event.tool_name}: {str(what)[:100]}", file=sys.stderr)
            if event.type == "assistant" and event.text:
                texts.append(event.text)
    except ClaudeError as exc:
        print(f"warning: the investigation ended early: {exc}", file=sys.stderr)
    verdict = read_json(ws.dir / "assessment.json", warn=False)
    if not verdict or not all(k in verdict for k in VERDICT_SCHEMA["required"]):
        # The agent reasoned but did not file the form; one structured pass
        # over its own words fills it, with nothing added.
        if verbose:
            print("  assessment.json missing or incomplete; extracting the verdict",
                  file=sys.stderr)
        extractor = ClaudeEngine(EngineConfig(tools=(), model=model))
        try:
            verdict = extractor.ask_json(
                "From this investigation, fill the verdict exactly as the schema asks, "
                "adding nothing that is not in the text. If a field has no support in "
                "the text, say so in it.\n\n" + "\n\n".join(texts)[-12000:],
                VERDICT_SCHEMA, system=SYSTEM)
        except ClaudeError as exc:
            verdict = {"feasible": "no", "summary": f"no verdict could be produced: {exc}",
                       "approach": [], "constructs": [], "engine_limits": [], "risks": [],
                       "render_estimate": "", "pipeline": [], "missing": ["an assessment"]}
        engine.usage.cost_usd += extractor.usage.cost_usd
    verdict["tool_calls"] = calls
    verdict["cost_usd"] = round(engine.usage.cost_usd, 4)
    return verdict


def prove(ws: Workspace, host: str, *, verbose: bool) -> dict:
    """Validate the proof document and render one frame of it on the engine."""
    out = {"file": "", "valid": False, "errors": [], "frame": ""}
    path = ws.dir / "proof.scene.xml"
    # The harness builds the proof program itself; the document is its output.
    built = sdkkit.build_findings(ws.dir / "proof.py", "proof.scene.xml")
    if any(f.startswith("error") for f in built) or not path.is_file():
        out["errors"] = [f for f in built if f.startswith("error")] or [
            "the agent wrote no proof.scene.xml"]
        if (ws.dir / "proof.py").is_file():
            out["file"] = str(ws.dir / "proof.py")   # written, but it did not build
        return out
    out["file"] = str(path)
    symbols, findings = ws.validate("proof.scene.xml")
    findings = built + findings
    out["errors"] = [f for f in findings if f.startswith("error")]
    out["valid"] = not out["errors"]
    if not out["valid"]:
        return out
    cmd = ["scene-render", "render", str(path), "-t", "1.5", "-o", str(ws.dir / "proof.png")]
    binary = render.engine()
    try:
        if host:
            proc = render._run_remote(cmd, ws.dir, host, verbose=verbose)
        elif binary is not None:
            cmd[0] = str(binary)
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        else:
            out["errors"] = ["no engine reachable to render the proof"]
            return out
    except subprocess.CalledProcessError as exc:
        out["errors"] = [f"render host unreachable: {exc.stderr.strip()[:200]}"]
        return out
    frame = ws.dir / "proof.png"
    if proc.returncode == 0 and frame.is_file():
        out["frame"] = str(frame)
    else:
        out["errors"] = [f"the engine did not render the proof: "
                         f"{(proc.stderr or proc.stdout).strip()[-300:]}"]
    return out


def write_report(root: Path, description: str, verdict: dict, proof: dict, facts: dict) -> Path:
    md = [f"# Can we produce it? — {verdict.get('feasible', '?').upper()}", "",
          f"*{description}*", "", verdict.get("summary", ""), "",
          f"Engine: {facts.get('host')} · "
          f"{(facts.get('capabilities') or {}).get('engine', {}).get('name', 'scene-render')} "
          f"{(facts.get('capabilities') or {}).get('engine', {}).get('version', '')} · "
          f"schema {(facts.get('capabilities') or {}).get('schema', '')}", ""]
    if proof.get("frame"):
        md += ["## Proof", "", f"`proof.scene.xml` validated by the SDK and the engine; one frame "
               f"rendered on the host: `{Path(proof['frame']).name}`", ""]
    elif proof.get("file"):
        md += ["## Proof", "", "`proof.scene.xml` was written but did not pass:", ""]
        md += [f"- {e[:200]}" for e in proof.get("errors", [])] + [""]
    for title, key in (("Approach", "approach"), ("Constructs", "constructs"),
                       ("Engine limits", "engine_limits"), ("Risks", "risks"),
                       ("Pipeline", "pipeline"), ("Missing", "missing")):
        rows = verdict.get(key) or []
        if rows:
            md += [f"## {title}", ""] + [f"- {r}" for r in rows] + [""]
    md += [f"**Render estimate.** {verdict.get('render_estimate', '')}", "",
           f"*{verdict.get('tool_calls', 0)} tool calls · ${verdict.get('cost_usd', 0)}*", ""]
    path = root / "assess.md"
    path.write_text("\n".join(md), encoding="utf-8")
    write_json(root / "assess.json", {"description": description, "verdict": verdict,
                                      "proof": proof, "engine": facts})
    return path


def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "assess", parents=list(parents),
        help="ask whether a described video can be produced, and prove it with a frame",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("description", nargs="*", help="the video, in a sentence or two")
    # --project comes from the shared `work` parent; --title is this stage's own.
    p.add_argument("--title", help="a project on disk whose treatment and script to assess")
    p.add_argument("--budget-usd", type=float, default=2.0, dest="budget_usd",
                   help="spend cap for the investigation (default 2)")
    workspace.add_inputs(p, "styles", "script")
    p.add_argument("--no-proof", action="store_true", dest="no_proof",
                   help="verdict only; do not render the proof frame")
    p.set_defaults(run=run)
    return p


def run(args) -> int:
    description = " ".join(args.description).strip()
    project = None
    context = ""
    given = {n: workspace.override(args, n) for n in ("styles", "script")}
    if args.project or args.title or any(given.values()):
        if args.project or args.title:
            hits = ([workspace.Project(Path(args.project))] if args.project
                    else workspace.matches(workspace.work_dir(args), args.title))
            if not hits:
                print(f"error: no project matches {args.title!r}", file=sys.stderr)
                return 1
            project = hits[0]
        else:
            # Standalone: anchored to the file handed in, nothing planted in work/.
            anchor = Path(given["script"] or given["styles"])
            project = workspace.Project(anchor.parent, standalone=True)
        idea = project.load_idea()
        styles = read_json(project.source("styles", given["styles"]), warn=False) or {}
        script = read_json(project.source("script", given["script"]), warn=False) or {}
        bits = []
        if idea:
            bits.append(f"Title: {idea.title}\nPremise: {idea.description}")
        for st in styles.get("styles", [])[:3]:
            bits.append(f"Treatment: {st.get('name')} (fit {st.get('fit')}/10): {st.get('shows_well', '')}")
        if script.get("sections"):
            shots = [sh for s in script["sections"] for sh in s.get("shots", [])]
            bits.append(f"Screenplay: {len(shots)} shots; e.g. " + " | ".join(
                sh.get("visual", "")[:120] for sh in shots[:4]))
        context = "\n\n".join(bits)
        if not description:
            description = (idea.title if idea else project.root.name)
    if not description:
        print("error: describe the video, or name a project", file=sys.stderr)
        return 1

    root = project.root if project else workspace.work_dir(args) / ".assess" / slugify(description, 40)
    root.mkdir(parents=True, exist_ok=True)
    ws = Workspace(root, ASSESS_DIR)
    host = render.remote_host(args)
    verbose = not args.quiet
    if verbose:
        print(f"assessing: {description[:90]}\n  -> {ws.dir}", file=sys.stderr)
    facts = engine_facts(host)
    prepare(ws, description=description, facts=facts, project_context=context)
    verdict = investigate(ws, model=args.model, budget_usd=args.budget_usd, verbose=verbose)
    proof = {"file": "", "valid": False, "errors": [], "frame": ""}
    if not args.no_proof:
        if verbose:
            print("  proving: validating proof.scene.xml and rendering one frame", file=sys.stderr)
        proof = prove(ws, host, verbose=verbose)
    report = write_report(root, description, verdict, proof, facts)

    print(f"\n{verdict.get('feasible', '?').upper()} — {verdict.get('summary', '')}")
    for key in ("approach", "engine_limits", "risks", "missing"):
        for row in verdict.get(key) or []:
            print(f"  {key[:8]:<8} {row}")
    print(f"  render   {verdict.get('render_estimate', '')}")
    if proof.get("frame"):
        print(f"  proof    rendered: {proof['frame']}")
    elif proof.get("errors"):
        print(f"  proof    failed: {proof['errors'][0][:160]}")
    print(f"\nwrote {report}", file=sys.stderr)
    return 0 if verdict.get("feasible") in ("yes", "partly") else 1
