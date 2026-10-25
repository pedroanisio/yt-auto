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

The reader is `--reviewer-model` (default opus), never the author's family:
the author is build.json's engine model (else `--model`), and the author's
own family, or the gateway reviewer pool (`pool`, `sdk:<id>`, whose engine
cannot open the PDF), exits 2 before the sheet is rendered or anything paid.

Each issue has a kind. A `credit` issue is something the sheet shows that
was taken from someone else with nothing on screen to say whose: a chart
redrawn from a paper, a logo, quoted press text, a placeholder credit. No
manifest row exists for those, so the reader is the only check that sees
them. A credit issue names its asset (asked once more when it does not, then
an error), and it blocks whatever its severity or the verdict: review.json's
`credit_issues` lists them, with `scene_sha256` of the scene it read, and
`yt review` exits 1 while there is one. Every other kind is the editor's
mark-up and changes no exit code. `yt qa` and `yt doctor` read
`credit_issues` (`current_credits`) only while scene.xml is still that
scene, and route them to a human (external): only someone with the source
can say what the credit is.

review.json is always the project's (`--out` moves only the PDF), since it
is the file QA and doctor read. A read that fails (the form, or a credit
that still names no asset after the one retry) voids the review.json that
was there: its digest is dropped and `failed` says why, so no earlier "no
credits" answer stands for a sheet the last reader could not clear.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from . import flags, house, reviewers, spend, workspace
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
    "matters, which stage fixes it.\n\n"
    "Every issue has a kind. Its kind is `credit` when the sheet shows "
    "something taken from someone else with no credit on screen: (a) a chart, "
    "diagram or figure redrawn from a paper or a named third-party source, "
    "with no \"after X\" or \"source: X\" on screen; (b) a logo, trademark or "
    "product mark that was typeset or modelled; (c) quoted press or web text, "
    "or anything styled as a screenshot, with no visible attribution; (d) a "
    "credit that is on screen but is a placeholder (\"TBD\", \"credit\", "
    "\"source\", \"Lorem\", \"[credit]\"). A credit issue names in `asset` the "
    "asset id, file or on-screen item that needs the credit. Something listed "
    "in the build's credits is credited (the end-credits card prints it), and "
    "a headline card with a visible \"outlet, date\" line is attributed; never "
    "report as missing a credit the credits rows already hold, nor a row the "
    "build's credit_errors already name."
)

#: The kinds an issue on the sheet may have. Only `credit` acts: it is the
#: one the reader alone can see (no file or manifest row exists for a figure
#: redrawn from a paper), and a licence breach is not minor.
KINDS = ("credit", "band", "legibility", "placeholder", "continuity", "other")
CREDIT = "credit"
#: current_credits' note when review.json read another scene than scene.xml.
STALE_REVIEW = "not_evidenced: review.json reviewed another scene; run `yt review` again"
#: ... when review.json was written before the digest existed (AUTO-160):
#: whether it read this scene cannot be told, not that it read another.
OLD_REVIEW = "not_evidenced: review.json predates the scene digest; run `yt review` again"
#: ... when the last read failed, so no answer speaks for the scene.
FAILED_REVIEW = "not_evidenced: the last `yt review` read failed; run `yt review` again"

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
                                                            "fix": {"type": "string"},
                                                            "kind": {"type": "string",
                                                                     "enum": list(KINDS)},
                                                            "asset": {"type": "string",
                                                                      "description": "credit only: the asset id, file, or on-screen item that needs the credit"}},
                                                        "required": ["what", "severity", "owner", "fix",
                                                                     "kind"]}}},
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
    # The reviewer by default: a claude CLI model of another family than
    # the default sonnet author, as for every stage that reviews; never the
    # gateway. Imported here, as build and research do, because qa and
    # doctor import this module and have no other use for vfx.
    from .vfx import REVIEWER_MODEL
    flags.reviewer_model(p, default=REVIEWER_MODEL,
                         help="the model that reads the sheet, never the author's own "
                              f"family (default {REVIEWER_MODEL}; a claude or codex model: "
                              "`pool` and sdk:<id> exit 2)")
    workspace.add_inputs(p, "build", "script")
    p.set_defaults(run=run)
    return p


def credit_issues(critique: dict) -> list[dict]:
    """Every issue of kind `credit` in the reader's form, with the heading
    of its section. Each one blocks, whatever its severity and whatever the
    verdict: a licence breach is not minor."""
    found = []
    for sec in (critique or {}).get("sections") or []:
        for row in (sec or {}).get("issues") or []:
            if isinstance(row, dict) and row.get("kind") == CREDIT:
                found.append({"heading": str(sec.get("heading", "")),
                              "what": str(row.get("what", "")),
                              "asset": str(row.get("asset") or "").strip(),
                              "severity": str(row.get("severity", "")),
                              "fix": str(row.get("fix", ""))})
    return found


def unnamed_credits(critique: dict) -> list[str]:
    """The headings of the credit issues that name no asset. The asset is
    what makes the finding actionable, and what keeps it one defect across
    QA passes (improve.fingerprint reads the backticked name)."""
    named: list[str] = []
    for row in credit_issues(critique):
        if not row["asset"] and row["heading"] not in named:
            named.append(row["heading"])
    return named


def scene_sha256(scene: str | Path) -> str:
    """sha256 of the scene document's bytes, as qa.scene_digest records
    the scene QA judged; computed here so that qa may import this module."""
    return hashlib.sha256(Path(scene).read_bytes()).hexdigest()


def current_credits(root: Path, scene: str | Path | None) -> tuple[list[dict], str]:
    """review.json's credit issues while they speak for `scene`, and a note.

    No review.json, or no scene: nothing, and no note. One whose last read
    failed: nothing, and FAILED_REVIEW. One with no `scene_sha256` (written
    before AUTO-160): nothing, and OLD_REVIEW. One of another scene's
    bytes: nothing, and STALE_REVIEW. QA and doctor both ask here, so they
    never disagree on what is current."""
    record = read_json(Path(root) / "review.json", warn=False)
    if not isinstance(record, dict) or not scene or not Path(scene).is_file():
        return [], ""
    if record.get("failed"):
        return [], FAILED_REVIEW
    seen = record.get("scene_sha256")
    if not seen:
        return [], OLD_REVIEW
    if seen != scene_sha256(scene):
        return [], STALE_REVIEW
    return [row for row in record.get("credit_issues") or [] if isinstance(row, dict)], ""


def void(path: Path, why: str) -> None:
    """Mark the review.json at `path` as no answer: the read that should
    have replaced it failed. Its digest and credits go, so current_credits
    gives FAILED_REVIEW; the rest (the earlier critique, engine, usage) is
    kept for the record. With no review.json there is nothing to void."""
    record = read_json(path, warn=False)
    if not isinstance(record, dict):
        return
    record.pop("scene_sha256", None)
    record.pop("credit_issues", None)
    write_json(path, record | {"failed": why})


def run(args) -> int:
    # The gateway reviewer pool answers text decisions only; this reader
    # opens the PDF and the frames through an agent.
    if refused := reviewers.agent_refusal("review", args.reviewer_model):
        print(refused, file=sys.stderr)
        return 2
    idea, project = workspace.resolve(args, inputs=("scene",))
    # A model never grades its own work. The author is the model that built
    # the scene (build.json's engine), else --model; the check is made
    # before sr-review runs or anything is paid.
    build = read_json(project.source("build", workspace.override(args, "build")),
                      warn=False) or {}
    engine = build.get("engine") if isinstance(build.get("engine"), dict) else {}
    if own := flags.own_reviewer(str(engine.get("model") or args.model), args.reviewer_model):
        print(own, file=sys.stderr)
        return 2
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
    # QA and doctor read the project's review.json, so it is written there
    # whatever --out says about the PDF.
    review_out = project.root / "review.json"
    # The bytes the sheet was made from: QA and doctor read its credits
    # only while scene.xml is still this document (current_credits).
    seen = scene_sha256(scene)

    # Now read it: the sheet exists to be marked up, and the first reader is
    # the model, with the PDF and the frames in front of it.
    script = read_json(project.source("script", workspace.override(args, "script")),
                       warn=False) or {}
    rules, digest = house.for_args(args, project, "review")
    reader = Harness(project.root, "review", model=args.reviewer_model, role=ROLE,
                     house=rules, house_digest=digest,
                     fresh=getattr(args, "fresh", False), verbose=not args.quiet)
    frames_dir = project.root / "review-frames"
    sections = [{"heading": sec.get("heading", ""), "purpose": sec.get("purpose", ""),
                 "shots": [{"label": sh.get("label"), "visual": str(sh.get("visual", ""))[:160],
                            "on_screen": sh.get("on_screen", "")}
                           for sh in sec.get("shots", [])]}
                for sec in script.get("sections", [])]
    manifest = {k: build.get(k) for k in ("editorial", "placeholders", "findings",
                                          "publishable", "design")}
    # What the end-credits card already credits and what build already
    # refused (AUTO-147), so the reader files only what neither holds; a
    # build from before them passes nothing for them.
    manifest |= {k: build[k] for k in ("credits", "credit_errors") if k in build}
    # The reader works in the project: a sheet written elsewhere is named
    # by its full path, the default one as before (same question, same cache).
    shown = out.name if out.resolve().parent == project.root.resolve() else str(out)
    prompt = "\n\n".join([
        f"Read `{shown}` (the review sheet) and, if the directory "
        f"`{frames_dir.name}/` exists, every frame in it. Critique each "
        "section against the screenplay below and file the form.",
        brief("Screenplay, per section", sections),
        brief("Build manifest (editorial decisions, placeholders, findings, credits)",
              manifest),
    ])
    try:
        critique = reader.inspect("sheet", prompt, REVIEW_SCHEMA, cwd=project.root)
        # A credit issue that names nothing can be neither credited nor
        # tracked across passes: asked once more, then an error (invariant
        # 3), never filled in.
        dropped: list[str] = []
        if unnamed := unnamed_credits(critique):
            critique = reader.inspect(
                "sheet-retry", prompt + "\n\nYour form filed credit issues in "
                f"{', '.join(unnamed)} with no `asset`: name the asset id, file or "
                "on-screen item for each.", REVIEW_SCHEMA, cwd=project.root)
            if unnamed_credits(critique):
                why = "[review] sheet: a credit issue names no asset"
                print(f"error: {why}", file=sys.stderr)
                void(review_out, why)
                return 1
            # A retry that answers by dropping the issue clears the block;
            # it is said, and recorded, so the drop can be audited.
            kept = {row["heading"] for row in credit_issues(critique)}
            dropped = [h for h in unnamed if h not in kept]
            for heading in dropped:
                print(f"note: [review] the retry filed no credit issue in {heading}, "
                      "which the first form flagged with no asset", file=sys.stderr)
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        void(review_out, str(exc))
        return 1
    credits = credit_issues(critique)
    print(f"\n  reader's verdict: {critique['verdict']}")
    for sec in critique.get("sections", []):
        issues = sec.get("issues", [])
        print(f"  {sec.get('heading', '')[:40]:<40} {len(issues)} issue(s)")
        for row in issues[:4]:
            print(f"      [{row.get('severity', '?'):<8}] {row.get('kind', '?')}: "
                  f"{row.get('what', '')[:70]} -> {row.get('owner', '?')}: "
                  f"{row.get('fix', '')[:50]}")
    print(f"  {critique.get('overall', '')[:400]}")
    if credits and critique.get("verdict") == "approve":
        print(f"note: the reader approved the sheet but filed {len(credits)} credit "
              "issue(s); they block", file=sys.stderr)
    write_json(review_out, {
        "idea": {"title": idea.title}, "pdf": str(out),
        "frames": sorted(p.name for p in frames_dir.glob("*.png")) if frames_dir.is_dir() else [],
        "scene_sha256": seen, "critique": critique, "credit_issues": credits,
        "credits_dropped_on_retry": dropped,
        "engine": reader.report(), "usage": spend.from_report(reader.report())})
    print(f"wrote {review_out}", file=sys.stderr)
    # Only a credit acts: every other kind is the editor's mark-up.
    for row in credits:
        print(f"error: review: missing credit in {row['heading']}: {row['what']} "
              f"(`{row['asset']}`)", file=sys.stderr)
    return 1 if credits else 0
