"""Make the review sheet: a PDF of the scene for a human to mark up.

sr-core ships `sr-review`, which reads a scene document and writes a PDF with a
frame, the captions and the markers of each section, the open to-dos, and the
sources and credits. Everything it wants is already in the document `yt build`
writes -- that is why the builder carries the licence and the credit line into
each image asset.

    yt review --title monty                 # the sheet, from the document alone
    yt review --title monty --engine rs     # with a real frame per section

Without an engine it lays out what the document says; with one it renders a
still for each section first.

The sheet is then read, not just written: the model opens the PDF and the
frames and critiques each section -- what a marker-up reviewer would circle --
and `review.json` carries that beside the PDF. The full scorecard over the
rendered video is `yt qa`; this is the composition read from its own document.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import house, workspace
from . import render as render_stage
from .core import read_json, write_json
from .harness import Harness, HarnessError, brief
from .scene_sdk import sdk

ROLE = (
    "You review a scene-render composition from its review sheet: a PDF with "
    "a frame per section, the captions and markers, the to-dos, sources and "
    "credits, plus the rendered frames when there are any. You read like the "
    "editor who will mark the sheet up: per section, what the frame shows "
    "against what the screenplay asked, legibility and band discipline "
    "(supers top, stage middle, captions bottom), continuity of treatment "
    "between sections, missing credits, and anything that reads as a "
    "placeholder or a slideshow. Be specific: section, what you see, why it "
    "matters, which stage fixes it."
)

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "sections": {"type": "array",
                     "items": {"type": "object",
                               "properties": {
                                   "heading": {"type": "string"},
                                   "reads": {"type": "string",
                                             "description": "what the frame shows"},
                                   "issues": {"type": "array",
                                              "items": {"type": "object",
                                                        "properties": {
                                                            "what": {"type": "string"},
                                                            "severity": {"type": "string",
                                                                         "enum": ["blocking", "minor"]},
                                                            "owner": {"type": "string"},
                                                            "fix": {"type": "string"}},
                                                        "required": ["what", "severity", "owner", "fix"]}}},
                               "required": ["heading", "reads", "issues"]}},
        "overall": {"type": "string"},
        "verdict": {"type": "string", "enum": ["approve", "send back"]},
    },
    "required": ["sections", "overall", "verdict"],
}


def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "review", parents=list(parents),
        help="write the PDF review sheet for the scene",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scene", metavar="FILE",
                   help="the document (default: the project's scene.xml)")
    p.add_argument("--out", dest="pdf_out", metavar="FILE",
                   help="where to write the PDF (default review.pdf)")
    p.add_argument("--engine", nargs="?", const="rs", default=None,
                   help="render a frame per section with this engine (rs, py, …)")
    p.add_argument("--at", choices=["middle", "start"], default="middle",
                   help="where in each section the frame comes from")
    p.add_argument("--captions", metavar="LANG",
                   help="caption track to show, by language prefix")
    p.add_argument("--fresh", action="store_true",
                   help="read the sheet again even if it did not change")
    workspace.add_inputs(p, "build", "script")
    p.set_defaults(run=run)
    return p


def run(args) -> int:
    idea, project = workspace.resolve(args, inputs=("scene",))
    scene = project.source("scene", args.scene)
    if not scene:
        print("error: no scene; run `yt build` first, or pass --scene FILE",
              file=sys.stderr)
        return 1

    sdk()  # puts sr-core on the path, installed or from the checkout
    try:
        from sr_core.review.cli import main as review_main
    except ImportError as exc:
        print(f"error: sr-review was not found ({exc})", file=sys.stderr)
        return 1

    out = Path(args.pdf_out or (project.root / "review.pdf"))
    argv = [scene, "-o", str(out), "--at", args.at]
    if args.captions:
        argv += ["--captions", args.captions]
    if args.engine:
        if render_stage.engine() is None and not args.quiet:
            print("warning: no engine binary found; frames may be left blank",
                  file=sys.stderr)
        argv += ["--engine", args.engine,
                 "--frames", str(project.root / "review-frames")]
    if not args.quiet:
        print(f"sr-review {' '.join(argv)}", file=sys.stderr)

    try:
        code = review_main(argv)
    except SystemExit as exc:          # argparse inside the tool
        code = int(exc.code or 0)
    except Exception as exc:
        print(f"error: sr-review failed: {exc}", file=sys.stderr)
        return 1
    if code:
        return int(code)
    print(f"\n{idea.title} — review sheet\nwrote {out}")

    # Now read it: the sheet exists to be marked up, and the first reader is
    # the model, with the PDF and the frames in front of it.
    build = read_json(project.source("build", workspace.override(args, "build")),
                      warn=False) or {}
    script = read_json(project.source("script", workspace.override(args, "script")),
                       warn=False) or {}
    rules, digest = house.for_args(args, project, "review")
    reader = Harness(project.root, "review", model=args.model, role=ROLE,
                     house=rules, house_digest=digest,
                     fresh=getattr(args, "fresh", False), verbose=not args.quiet)
    frames_dir = project.root / "review-frames"
    sections = [{"heading": sec.get("heading", ""), "purpose": sec.get("purpose", ""),
                 "shots": [{"label": sh.get("label"), "visual": str(sh.get("visual", ""))[:160],
                            "on_screen": sh.get("on_screen", "")}
                           for sh in sec.get("shots", [])]}
                for sec in script.get("sections", [])]
    try:
        critique = reader.inspect("sheet", "\n\n".join([
            f"Read `{out.name}` (the review sheet) and, if the directory "
            f"`{frames_dir.name}/` exists, every frame in it. Critique each "
            "section against the screenplay below and file the form.",
            brief("Screenplay, per section", sections),
            brief("Build manifest (editorial decisions, placeholders, findings)",
                  {k: build.get(k) for k in ("editorial", "placeholders", "findings",
                                             "publishable", "design")}),
        ]), REVIEW_SCHEMA, cwd=project.root)
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"\n  reader's verdict: {critique['verdict']}")
    for sec in critique.get("sections", []):
        issues = sec.get("issues", [])
        print(f"  {sec.get('heading', '')[:40]:<40} {len(issues)} issue(s)")
        for row in issues[:4]:
            print(f"      [{row.get('severity', '?'):<8}] {row.get('what', '')[:70]} "
                  f"-> {row.get('owner', '?')}: {row.get('fix', '')[:50]}")
    print(f"  {critique.get('overall', '')[:400]}")
    review_out = out.with_name("review.json")
    write_json(review_out, {
        "idea": {"title": idea.title}, "pdf": str(out),
        "frames": sorted(p.name for p in frames_dir.glob("*.png")) if frames_dir.is_dir() else [],
        "critique": critique, "engine": reader.report()})
    print(f"wrote {review_out}", file=sys.stderr)
    return 0
