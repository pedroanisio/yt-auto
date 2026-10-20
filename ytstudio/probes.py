"""The realism probe suite: what this system can make look real, measured.

Fixed, neutral briefs -- one per class of material, light or motion a
photoreal film depends on -- are each composed by an agent coding with the
sr-core SDK through the kit (`sdkkit`), the way a film's worlds are composed.
The harness then builds the program itself (twice, checked, validated by the
engine), renders frames on the render host, and hands them to a different
model that scores their realism and names, for every defect, what caused it:
the authoring (the engine offered a way and the program did not take it), the
engine (it cannot render what was asked), or an asset (no adequate model,
texture or HDRI was at hand). So one run says where the ceiling is and whose
it is, and the next run is a comparison.

    python3 -m ytstudio.probes                         # every probe
    python3 -m ytstudio.probes --only metal,glass      # some of them
    python3 -m ytstudio.probes --list                  # what each one asks
    python3 -m ytstudio.probes --fetch-props           # CC0 HDRIs, models, textures

A run lives in `work/.probes/<run>/`: one workspace per probe (the program,
the document, the frames, the review), `summary.json` and `summary.md`, and
the review decisions under `decisions/probe/`. `work/.probes/latest.json`
names the last complete run; its rows are what `sdkkit.install` writes into
ENGINE.md as measured on this engine. The briefs name no film and choose no
look: a probe states what must read as real, never how to get there.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from claude_engine import ClaudeError, EngineConfig
from . import harness, sdkkit
from .core import read_json, write_json

PROBES_DIR = ".probes"
PROGRAM = "compose.py"
DOCUMENT = "probe.scene.xml"
DURATION = 3.0
FRAME = (1920, 1080)
CAUSES = ("authoring", "engine", "asset", "unknown")
AUTHOR_TIMEOUT = 1800.0
#: A reviewer reads three full-size frames, the program and ENGINE.md; 900 s was
#: measured too short for a path-traced probe.
REVIEW_TIMEOUT = 1800.0

#: Render failures that are the infrastructure's, not the program's.
INFRA = ("No such file or directory", "render host unreachable", "no engine reachable",
         "Connection timed out", "Connection refused", "ssh: connect to host",
         "No route to host", "timed out during banner exchange", "port 22 timed out",
         "Could not resolve hostname")


class InfrastructureError(RuntimeError):
    """The frames could not be rendered for a reason no author caused."""


@dataclass(frozen=True)
class Probe:
    id: str
    what: str            # the class it measures, in a few words
    brief: str           # what must read as real; never how
    times: tuple[float, ...] = (0.75, 1.5, 2.25)


PROBES: tuple[Probe, ...] = (
    Probe("metal", "polished and brushed metal",
          "A polished metal object and a brushed metal object rest on a matte surface. "
          "The polished one must mirror its surroundings with the sharpness and falloff "
          "of real metal; the brushed one must stretch its highlight along its grain. "
          "Both must sit on the surface, with the contact shadow that proves it."),
    Probe("coated", "coated and painted surfaces",
          "Glossy coated objects -- a lacquer or clear coat over a coloured base -- under a "
          "soft key. The coat must reflect sharply while the colour beneath stays diffuse, "
          "and the reflection must fall off toward the edges as a real coat does."),
    Probe("glass", "glass and liquid",
          "A clear glass vessel partly filled with liquid, lit from behind. What is behind "
          "it must bend through the glass and the liquid, the edges must darken and "
          "brighten the way thick glass does, and the liquid line must read."),
    Probe("translucent", "light through thin organic material",
          "A thin translucent organic material -- something like a leaf, wax or skin -- lit "
          "from behind so light passes through its thin parts and glows warmer there, "
          "while its thick parts stay opaque."),
    Probe("terrain", "natural ground and distance",
          "Natural ground receding to a horizon under a low sun: near detail that holds "
          "up close, long shadows, and atmospheric depth that separates near from far."),
    Probe("emissive", "light-emitting elements",
          "Thin glowing elements in a dim space. They must light the surfaces near them -- "
          "a falloff on the ground and on nearby objects -- not merely look bright."),
    Probe("volume", "light in a medium",
          "A beam of light crossing haze in a dark space, with objects inside it that cut "
          "visible shadows into the beam."),
    Probe("macro", "macro scale and depth of field",
          "A small object filmed at macro distance: very shallow depth of field, bokeh in "
          "the background, and surface micro-detail that holds where it is in focus."),
    Probe("detail", "dense repeated detail",
          "A surface covered by thousands of small repeated elements, seen close and then "
          "far as the camera moves. The detail must hold without shimmering, and the "
          "repetition must not read as a tiled pattern."),
    Probe("motion", "motion and the camera",
          "A fast-moving object and a moving camera. The motion blur must read as "
          "captured by a real camera's shutter, and the move must have weight."),
)

BY_ID = {p.id: p for p in PROBES}

REVIEW_ROLE = (
    "You judge whether rendered frames read as real. Score photorealism 0-10 for the "
    "class the brief probes: 10 cannot be told from a photograph or from the best CG "
    "on a top-tier channel; 8 reads as high-end CG at a glance; 6 is competent CG that "
    "is obviously rendered; 4 is basic 3D (primitives, flat light, no contact); 2 is "
    "broken; 0 is nothing to judge. Judge only what the frames show, not what the "
    "program intended. For every defect name the frame, what is visible, and its "
    "cause: `authoring` when the engine offers a way (kit/ENGINE.md says what it "
    "approximates or ignores; compose.py shows what was done) and the program did not "
    "take it; `engine` when the engine cannot render it as asked -- cite the line of "
    "ENGINE.md or the visible artefact; `asset` when no adequate model, texture or "
    "environment was available; `unknown` when you cannot tell. Give the fix in the "
    "program's terms."
)

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number"},
        "seen": {"type": "string"},
        "findings": {"type": "array", "items": {
            "type": "object",
            "properties": {"frame": {"type": "string"}, "problem": {"type": "string"},
                           "cause": {"type": "string", "enum": list(CAUSES)},
                           "fix": {"type": "string"}},
            "required": ["frame", "problem", "cause", "fix"]}},
        "summary": {"type": "string"},
    },
    "required": ["score", "seen", "findings", "summary"],
}

#: Neutral phrases for a shared, CC0 props folder the probes may draw on: a few
#: environments of each kind of light, and generic models and surfaces.
PROPS_PHRASES = {"hdri": ("studio", "overcast", "sunset", "interior"),
                 "model": ("rock", "plant", "bottle"),
                 "texture": ("metal", "concrete", "fabric")}


def model_family(name: str) -> str:
    """`opus`, `claude-opus-5-5` and `opus[1m]` are one model family."""
    low = (name or "").lower()
    return next((f for f in ("fable", "opus", "sonnet", "haiku") if f in low), low)


def work_dir(root: Path | None = None) -> Path:
    return Path(root) if root else Path(__file__).resolve().parent.parent / "work"


def probes_dir(root: Path | None = None) -> Path:
    return work_dir(root) / PROBES_DIR


# --------------------------------------------------------------------------- #
# one probe
# --------------------------------------------------------------------------- #

def brief_text(probe: Probe, *, props: list[dict]) -> str:
    """brief.md: the probe, the kit, the deliverable. No look, no value."""
    lines = [f"# Probe: {probe.what}", "", probe.brief, "",
             f"Make it a {DURATION:g}-second, {FRAME[0]}x{FRAME[1]} scene with a slow, "
             "motivated camera move. Everything else -- renderer, light, materials, "
             "composition -- is yours to choose for the picture to read as real.", "",
             sdkkit.BRIEF, "## Deliverable", "",
             f"1. `{PROGRAM}`: the program. It saves exactly one document, `{DOCUMENT}`.",
             f"2. `python3 kit.py build {PROGRAM}` reports 0 errors and a deterministic "
             "document.",
             "3. You rendered frames across the scene, Read every one, and changed the "
             "program until each frame reads as real for the class above.", "",
             "When you are done, end with one paragraph: what you chose and why, and what "
             "you could not make real and why (the engine, an asset, or time)."]
    if props:
        lines += ["", "## Props (CC0, under `props/`; listed in `props.json`)", ""]
        for item in props:
            lines.append(f"- {item.get('kind')}: `{item.get('file')}` -- "
                         f"{str(item.get('about', ''))[:120]}")
        lines += ["", "Use them where the picture needs them; nothing obliges you to."]
    return "\n".join(lines) + "\n"


def prepare(ws: Path, probe: Probe, *, props_dir: Path | None, host: str,
            measured: list[dict] | None) -> list[dict]:
    """The probe's workspace: the kit, the props link, brief.md."""
    ws.mkdir(parents=True, exist_ok=True)
    sdkkit.install(ws, frame=FRAME, host=host, measured=measured)
    items: list[dict] = []
    if props_dir and (props_dir / "props.json").is_file():
        manifest = read_json(props_dir / "props.json", warn=False) or {}
        items = list(manifest.get("items") or [])
        # Hard links, not a symlink: the files must sit below the workspace for the
        # frame job to ship them under the paths the document names, and a link
        # costs no disk.
        target = ws / "props"
        if not target.exists():
            try:
                shutil.copytree(props_dir / "props", target, copy_function=os.link)
            except OSError:
                shutil.copytree(props_dir / "props", target, dirs_exist_ok=True)
        write_json(ws / "props.json", manifest)
    (ws / "brief.md").write_text(brief_text(probe, props=items), encoding="utf-8")
    return items


REVISE = (
    "The realism review of your frames scored {score}/10 against a bar of {bar}. Its "
    "findings, verbatim, each with the frame, what it saw, the cause it named and a fix:"
    "\n\n{findings}\n\nRe-read brief.md, change the program to answer every finding the "
    "authoring can answer, build it, render frames and Read them again. Where a finding "
    "names the engine, work around it if the engine offers a way, else say so at the end.")


def author(ws: Path, probe: Probe, *, model: str, effort: str | None, budget_usd: float,
           verbose: bool, prompt: str = "Read brief.md and do what it asks.",
           resume: str | None = None) -> dict:
    """One agent session that composes the probe in its workspace; a revision
    resumes the same session, so the brief and its own choices stay in view."""
    engine = harness.ENGINE(EngineConfig(
        tools=sdkkit.AUTHOR_TOOLS, model=model, effort=effort, permission_mode="dontAsk",
        cwd=ws, max_turns_budget_usd=budget_usd, timeout=AUTHOR_TIMEOUT))
    calls, texts, error = 0, [], ""
    try:
        for event in engine.stream(prompt, system=sdkkit.AUTHOR_SYSTEM,
                                   **({"resume": resume} if resume else {})):
            if event.subtype == "tool_use":
                calls += 1
                if verbose:
                    what = (event.tool_input or {}).get("command") or \
                        (event.tool_input or {}).get("file_path") or ""
                    print(f"    [{probe.id}] {event.tool_name}: {str(what)[:90]}",
                          file=sys.stderr)
            if event.type == "assistant" and event.text:
                texts.append(event.text)
    except ClaudeError as exc:
        error = str(exc)
    usage = engine.usage
    return {"tool_calls": calls, "cost_usd": round(usage.cost_usd, 4), "error": error,
            "timed_out_usage": {"calls": usage.timed_out_calls,
                                "tokens": dict(usage.timed_out_tokens),
                                "cost_usd": round(usage.timed_out_cost_usd, 4),
                                "cost_estimated": usage.timed_out_cost_estimated},
            "closing": texts[-1].strip()[-2000:] if texts else "",
            "model": model, "session_id": getattr(engine, "session_id", None) or resume}


def verify(ws: Path, probe: Probe, *, host: str) -> dict:
    """The harness's own build and frames: never the agent's word for it."""
    program = ws / PROGRAM
    if not program.is_file():
        return {"ok": False, "error": f"no {PROGRAM} in the workspace", "frames": []}
    built = sdkkit.build(program, host=host)
    out = {"ok": built["ok"], "error": built.get("error", ""), "frames": [],
           "findings": [f for d in built.get("documents", []) for f in d["errors"]],
           "frame_findings": []}
    doc = ws / DOCUMENT
    if built.get("error") or not doc.is_file():
        out["ok"] = False
        out["error"] = out["error"] or f"the program did not save {DOCUMENT}"
        return out
    review = ws / "review"
    if review.exists():
        shutil.rmtree(review)
    review.mkdir()
    for t in probe.times:
        png, findings = sdkkit.frame(doc, t, host=host, out_dir=review)
        out["findings"] += findings
        out["frame_findings"] += findings
        if png:
            out["frames"].append(png.name)
    for name in ("brief.md", PROGRAM):
        shutil.copy2(ws / name, review / name)
    shutil.copy2(ws / sdkkit.KIT_DIR / "ENGINE.md", review / "ENGINE.md")
    return out


def review(run_root: Path, ws: Path, probe: Probe, *, model: str, effort: str | None,
           budget_usd: float, verbose: bool, name: str | None = None) -> tuple[dict, dict]:
    """The realism review, through the harness: (verdict, accounting)."""
    h = harness.Harness(run_root, "probe", model=model, effort=effort, role=REVIEW_ROLE,
                        verbose=verbose, budget_usd=budget_usd, timeout=REVIEW_TIMEOUT)
    prompt = (f"The probe: {probe.what}. Read brief.md (what must read as real), then "
              f"every PNG here ({', '.join(sorted(p.name for p in (ws / 'review').glob('*.png')))}), "
              "then compose.py and ENGINE.md to attribute each defect to its cause. "
              "Score the frames as they are.")
    verdict = h.inspect(name or probe.id, prompt, REVIEW_SCHEMA, cwd=ws / "review")
    return verdict, h.report()


REGRESSED = (
    "Your last revision scored {last}/10, below round {best_round}'s {best}/10, so the "
    "program has been put back to round {best_round}'s (keep only a measured improvement). "
    "Round {best_round}'s findings, verbatim:\n\n{findings}\n\nAnswer them another way "
    "than the change that lowered the score; build, render, Read the frames again.")


def _causes(findings: list[dict]) -> dict:
    out = {c: 0 for c in CAUSES}
    for f in findings:
        out[f.get("cause") if f.get("cause") in CAUSES else "unknown"] += 1
    return out


def run_probe(run_root: Path, probe: Probe, *, args, props_dir: Path | None,
              measured: list[dict] | None) -> dict:
    """Author, build, frame and review one probe; while the score is under the
    bar and rounds remain, the review's findings go back to the same author
    session verbatim and the probe is built, framed and reviewed again. Each
    round's evidence is kept under rounds/rNN/; the probe's result is its best
    round, and a revision that scored lower is put back before the next one."""
    ws = run_root / probe.id
    started = time.monotonic()
    row: dict = {"probe": probe.id, "what": probe.what, "score": 0.0, "summary": "",
                 "causes": {c: 0 for c in CAUSES}, "rounds": []}
    authors: list[dict] = []
    reviews_cost = 0.0
    best: dict | None = None
    try:
        prepare(ws, probe, props_dir=props_dir, host=args.host, measured=measured)
        authors.append(author(ws, probe, model=args.model, effort=args.effort,
                              budget_usd=args.budget_usd, verbose=not args.quiet))
        for n in range(max(0, args.rounds) + 1):
            built = verify(ws, probe, host=args.host)
            row["build"] = {k: built[k] for k in ("ok", "error", "frames")}
            row["build_findings"] = built.get("findings", [])[:20]
            costs = [f.split(sdkkit.RENDER_NOTE, 1)[1].strip() for f in built.get("frame_findings", [])
                     if sdkkit.RENDER_NOTE in f]
            if costs:
                row["render"] = costs[-1]
            # Only the render's own messages can blame the infrastructure: a
            # program that names a missing file fails its build, never here.
            rendered = built.get("frame_findings", [])
            if not built["frames"] and any(m in f for f in rendered for m in INFRA):
                raise InfrastructureError("the frames could not be rendered: " + "; ".join(
                    f for f in rendered if any(m in f for m in INFRA))[:400])
            if not built["frames"]:
                # Nothing to look at is a measured outcome, recorded as such.
                row["rounds"].append({"round": n, "score": 0.0, "built": False,
                                      "summary": "no frame to judge: "
                                      + (built["error"] or "nothing rendered")})
                if best is None:
                    break
                current = row["rounds"][-1]
            else:
                verdict, report = review(run_root, ws, probe, model=args.reviewer_model,
                                         effort=args.effort,
                                         budget_usd=args.review_budget_usd,
                                         verbose=not args.quiet, name=f"{probe.id}-r{n}")
                reviews_cost += report["cost_usd"]
                findings = verdict.get("findings", [])
                current = {"round": n, "score": float(verdict.get("score") or 0),
                           "summary": str(verdict.get("summary", "")),
                           "findings": findings, "causes": _causes(findings)}
                row["rounds"].append({k: current[k] for k in ("round", "score", "causes")})
                kept = ws / "rounds" / f"r{n:02d}"
                if kept.exists():
                    shutil.rmtree(kept)
                shutil.copytree(ws / "review", kept)
                if best is None or current["score"] > best["score"]:
                    best = current
            if (best and best["score"] >= args.bar) or n == args.rounds:
                break
            if current is best or current["score"] >= best["score"]:
                # an improvement, or a tie: the newer program stands
                prompt = REVISE.format(score=current["score"], bar=args.bar,
                                       findings=json.dumps(current["findings"],
                                                           ensure_ascii=False, indent=1))
            else:
                shutil.copy2(ws / "rounds" / f"r{best['round']:02d}" / PROGRAM, ws / PROGRAM)
                prompt = REGRESSED.format(last=current["score"], best=best["score"],
                                          best_round=best["round"],
                                          findings=json.dumps(best["findings"],
                                                              ensure_ascii=False, indent=1))
            authors.append(author(ws, probe, model=args.model, effort=args.effort,
                                  budget_usd=args.budget_usd, verbose=not args.quiet,
                                  prompt=prompt, resume=authors[-1].get("session_id")))
    except Exception as exc:          # one probe's failure is that probe's row
        row["error"] = f"{type(exc).__name__}: {exc}"
    # What was measured stands even when a later round failed; the error keeps
    # the run from counting as complete.
    if best is not None:
        row.update(score=best["score"], summary=best["summary"],
                   findings=best["findings"], causes=best["causes"],
                   best_round=best["round"])
    elif row["rounds"]:
        row["summary"] = row["rounds"][-1]["summary"]
        row["causes"] = {c: int(c == "authoring") for c in CAUSES}
    if row.get("error"):
        row["summary"] = row["summary"] or f"the probe did not complete: {row['error']}"
    row["review"] = {"model": args.reviewer_model, "cost_usd": round(reviews_cost, 4)}
    if authors:
        row["author"] = {"model": args.model, "sessions": len(authors),
                         "tool_calls": sum(a["tool_calls"] for a in authors),
                         "cost_usd": round(sum(a["cost_usd"] for a in authors), 4),
                         "error": next((a["error"] for a in authors if a["error"]), ""),
                         "closing": authors[-1]["closing"]}
    row["seconds"] = round(time.monotonic() - started, 1)
    write_json(ws / "probe.json", row)
    return row


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #

def latest(root: Path | None = None) -> dict | None:
    """The last complete run's summary, or None."""
    pointer = read_json(probes_dir(root) / "latest.json", warn=False)
    if not isinstance(pointer, dict):
        return None
    return read_json(probes_dir(root) / pointer.get("run", "") / "summary.json", warn=False)


def latest_measured(root: Path | None = None) -> list[dict]:
    """The last run's rows as ENGINE.md shows them: probe, score, summary."""
    summary = latest(root) or {}
    return [{"probe": r["probe"], "score": r["score"], "summary": r.get("summary", ""),
             "render": r.get("render", "")}
            for r in summary.get("rows", [])]


def summary_text(summary: dict) -> str:
    lines = [f"# Realism probes · {summary['run']}", "",
             f"Author {summary['model']} · reviewer {summary['reviewer_model']} · "
             f"engine {summary.get('engine', '?')} · ${summary['cost_usd']:.2f}", "",
             "| Probe | Score | Rounds | Δ | Authoring | Engine | Asset | Summary |",
             "|---|---|---|---|---|---|---|---|"]
    for r in summary["rows"]:
        delta = r.get("delta")
        d = "" if delta is None else f"{delta:+.1f}"
        c = r["causes"]
        rounds = " → ".join(f"{x['score']:g}" for x in r.get("rounds", []))
        lines.append(f"| {r['probe']} | {r['score']:.1f} | {rounds} | {d} | {c['authoring']} | "
                     f"{c['engine']} | {c['asset']} | {r['summary'][:140]} |")
    lines += ["", f"Mean {summary['mean']:.2f}. A cause column counts the reviewer's "
              "defects by what caused them: the authoring could have done it, the engine "
              "cannot, or no adequate asset was at hand."]
    return "\n".join(lines) + "\n"


def run(args) -> int:
    selected = list(PROBES)
    if args.only:
        wanted = [w.strip() for w in args.only.split(",") if w.strip()]
        unknown = [w for w in wanted if w not in BY_ID]
        if unknown:
            print(f"error: unknown probe(s) {', '.join(unknown)}; "
                  f"have {', '.join(BY_ID)}", file=sys.stderr)
            return 2
        selected = [BY_ID[w] for w in wanted]
    if model_family(args.reviewer_model) == model_family(args.model):
        print("error: the reviewer must be a different model from the author: a model "
              "does not grade its own work", file=sys.stderr)
        return 2
    base = probes_dir(args.work)
    name = args.name or time.strftime("r-%Y%m%d-%H%M%S")
    run_root = base / name
    done: dict[str, dict] = {}
    if args.resume:
        if not args.name or not run_root.is_dir():
            print("error: --resume continues a run named by --name that exists",
                  file=sys.stderr)
            return 2
        for probe in selected:
            row = read_json(run_root / probe.id / "probe.json", warn=False)
            if isinstance(row, dict) and not row.get("error"):
                done[probe.id] = row
            elif (run_root / probe.id).exists():
                shutil.rmtree(run_root / probe.id)     # an unfinished probe starts again
    elif args.name and run_root.is_dir() and any(run_root.iterdir()):
        print(f"error: {run_root} exists; pass --resume to continue it", file=sys.stderr)
        return 2
    run_root.mkdir(parents=True, exist_ok=True)
    # A props folder holds props.json and props/: a project root, or the
    # shared set `--fetch-props` writes into work/.probes/.
    props_dir = Path(args.props) if args.props else base
    if not (props_dir / "props.json").is_file():
        props_dir = None
    previous = latest(args.work)
    measured = latest_measured(args.work) or None
    print(f"probes: {len(selected)} -> {run_root}" +
          (f" · props {props_dir}" if props_dir else " · no props"), file=sys.stderr)
    todo = [p for p in selected if p.id not in done]
    if done:
        print(f"probes: resuming; kept {', '.join(done)}", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        fresh = dict(zip([p.id for p in todo],
                         pool.map(lambda p: run_probe(run_root, p, args=args,
                                                      props_dir=props_dir, measured=measured),
                                  todo)))
    rows = [done.get(p.id) or fresh[p.id] for p in selected]
    before = {r["probe"]: r["score"] for r in (previous or {}).get("rows", [])}
    for r in rows:
        if r["probe"] in before:
            r["delta"] = round(r["score"] - before[r["probe"]], 2)
    engine = sdkkit.engine_manifest(args.host).get("manifest", {}).get("engine", {})
    cost = sum((r.get("author") or {}).get("cost_usd", 0) +
               (r.get("review") or {}).get("cost_usd", 0) for r in rows)
    summary = {"run": name, "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "model": args.model, "reviewer_model": args.reviewer_model,
               "engine": f"{engine.get('name', '?')} {engine.get('version', '?')}",
               "cost_usd": round(cost, 4),
               "mean": round(sum(r["score"] for r in rows) / len(rows), 2) if rows else 0.0,
               "rows": rows}
    write_json(run_root / "summary.json", summary)
    (run_root / "summary.md").write_text(summary_text(summary), encoding="utf-8")
    if len(selected) == len(PROBES) and not any(r.get("error") for r in rows):
        write_json(base / "latest.json", {"run": name, "when": summary["when"]})
    print(summary_text(summary))
    return 0 if not any(r.get("error") for r in rows) else 1


def fetch_props(args) -> int:
    """Fill work/.probes/props with the neutral CC0 set the probes may use."""
    from . import props
    root = probes_dir(args.work)
    root.mkdir(parents=True, exist_ok=True)
    catalogue = props.Catalogue(root / props.CACHE_DIR)
    items: list[dict] = []
    for kind, phrases in PROPS_PHRASES.items():
        items += props.gather(root, list(phrases), kinds=(kind,), limit=1,
                              catalogue=catalogue, verbose=not args.quiet)
    (root / "props").mkdir(exist_ok=True)
    phrases = [p for ps in PROPS_PHRASES.values() for p in ps]
    write_json(root / "props.json", {"source": "Poly Haven (https://polyhaven.com), "
                                     "everything CC0", "phrases": phrases,
                                     "resolution": props.RESOLUTION, "count": len(items),
                                     "items": items})
    print(f"{len(items)} prop(s) in {root / 'props'}")
    return 0 if items else 1


def add_arguments(p: argparse.ArgumentParser, *, shared: bool = True) -> None:
    """The probe's flags; `shared` adds the ones `yt` gives every stage
    (--model, --effort, --quiet, --work) for running the module on its own."""
    if shared:
        p.add_argument("--model", default="sonnet", help="the author (default sonnet)")
        p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
        p.add_argument("--work", help="the work directory (default ./work)")
        p.add_argument("--quiet", action="store_true")
    p.add_argument("--only", help="comma-separated probe ids (default: all)")
    p.add_argument("--list", action="store_true", help="list the probes and stop")
    p.add_argument("--fetch-props", action="store_true",
                   help="fill work/.probes/props with a neutral CC0 set, then stop")
    p.add_argument("--reviewer-model", default="opus",
                   help="the realism reviewer; must differ from the author (default opus)")
    p.add_argument("--rounds", type=int, default=2,
                   help="revisions after the first review while under the bar (default 2)")
    p.add_argument("--bar", type=float, default=8.0,
                   help="the realism score that ends the revisions (default 8)")
    p.add_argument("--budget-usd", type=float, default=3.0, help="per author session")
    p.add_argument("--review-budget-usd", type=float, default=1.0, help="per review")
    p.add_argument("--jobs", type=int, default=2, help="probes composed at once")
    p.add_argument("--props", help="a folder holding props.json and props/ (a project root "
                   "works); default work/.probes, which --fetch-props fills")
    p.add_argument("--host", default="", help="render host (default: the still host)")
    p.add_argument("--name", help="the run's directory name (default r-<time>)")
    p.add_argument("--resume", action="store_true",
                   help="continue the run named by --name: finished probes keep their rows, "
                        "unfinished ones start again")


def dispatch(args) -> int:
    if args.list:
        for probe in PROBES:
            print(f"{probe.id:<12} {probe.what}\n             {probe.brief}\n")
        return 0
    if args.fetch_props:
        return fetch_props(args)
    return run(args)


def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "probe", parents=list(parents),
        help="measure what the system can make look real, per class of material and light",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_arguments(p, shared=False)
    p.set_defaults(run=dispatch)
    return p


def main(argv: list[str] | None = None) -> int:
    from . import env
    env.load(str(sdkkit.AUTOMATION_ROOT / ".env"))
    harness.isolate_sessions()
    p = argparse.ArgumentParser(prog="python3 -m ytstudio.probes", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    add_arguments(p)
    return dispatch(p.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
