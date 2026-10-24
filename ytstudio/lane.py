"""A yt video as a govern lane: the process, and every stage's exit check.

govern (super-tools) runs any process through checked stages: it holds the
owner's gates for their own words, runs long jobs without a model waiting on
them and keeps one ledger of tokens and machine time. yt knows what a video's
stages are and what proves each one done. This module is the seam between
them, and holds no judgement of its own:

    python3 ytstudio/lane.py process --write     # .govern/processes/yt-video.json
    python3 ytstudio/lane.py check DIR STAGE     # exit 0 when STAGE is done

govern runs a lane's commands in the lane's folder, so the checks name this
file by its path under the repo (`{repo}/ytstudio/lane.py`) and it makes the
repo importable itself, as the `yt` launcher does.

`process()` is generated from `workspace.STAGES`, so the govern process and
the pipeline cannot drift; tests/test_lane.py fails, naming the command
above, when the file on disk is not what it generates. A stage's check
passes when its artifact exists and `yt doctor` reports no fail owned by it
(`FINDING_STAGE` names the areas filed under another name); `render` also needs
every render job report to have rendered and `vfx` every section reviewed
without an error; warnings never block. `keyframe` also needs the operator's approval of the look, and `deliver`
passes only on doctor's delivery evidence (a clean final render, the gate
deliverable, QA at READY TO PUBLISH, and a current upload set from
`yt package` judged ready).

A lane's folder is `.govern/projects/<slug>`; its video is `work/<slug>`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    # Run by path: the repo, not this folder, is what must be importable.
    _here = str(Path(__file__).resolve().parent)
    sys.path[:] = [p for p in sys.path if p not in (_here, "")]
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import doctor, keyframe, render, workspace  # noqa: E402
from ytstudio.core import read_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROCESS_NAME = "yt-video"
PROCESS_FILE = ROOT / ".govern" / "processes" / f"{PROCESS_NAME}.json"
DELIVER = "deliver"

#: What done means beyond "the artifact exists and doctor finds no fail".
EXTRA_EXIT = {
    "keyframe": "the operator approved the look (yt keyframe --approve)",
    "retime": "the screenplay carries the measured narration timings",
}

#: Doctor areas that are not a stage's name, to the stage that owns them.
FINDING_STAGE = {"render-jobs": "render", "captions": "voice", "art": "build"}


def stages() -> list[str]:
    return [stage for stage, _ in workspace.STAGES] + [DELIVER]


def process() -> dict:
    """The govern process for a yt video, from the pipeline's own stages."""
    rows = []
    for stage, artifact in workspace.STAGES:
        exit_ = (f"{workspace.ARTIFACTS[artifact]} written and `yt doctor` reports no "
                 f"fail for {stage}")
        if stage in EXTRA_EXIT:
            exit_ += f"; {EXTRA_EXIT[stage]}"
        rows.append({"name": stage, "exit": exit_, "check": stage, "requires": "check"})
    rows.append({"name": DELIVER, "requires": "check", "check": DELIVER,
                 "exit": "a clean final render of the whole programme, the delivery "
                         "gate deliverable and a QA pass at READY TO PUBLISH, both "
                         "after that render, and the upload set `yt package` wrote "
                         "for that scene and video, judged ready"})
    commands = {name: ["{python}", "{repo}/ytstudio/lane.py", "check", "{dir}", name]
                for name in stages()}
    return {
        "version": 1,
        "name": PROCESS_NAME,
        "description": "a video made with the yt pipeline, idea to delivered film; "
                       "every stage's check is yt's own evidence for it",
        "manual": "../../ytstudio/SKILL.md",
        "setup": [{"commands": commands}],
        "stages": rows,
        "gates": [
            {"name": "script", "stage": "script",
             "message": "the owner has not approved the screenplay; nothing is "
                        "recorded or animated before they do"},
            {"name": "final", "stage": DELIVER, "needs": ["script"],
             "message": "the owner has not approved the final cut"},
        ],
    }


def project_for(lane_dir: str | Path, *, work: str | Path | None = None) -> workspace.Project:
    """The yt project a lane drives: work/<the lane folder's name>."""
    slug = Path(lane_dir).name
    base = Path(work) if work else ROOT / workspace.DEFAULT_WORK
    return workspace.Project(base / slug)


def _render_blocks(project: workspace.Project) -> list[str]:
    """Why the render stage is open: a job report in the project root that did
    not render (the statuses doctor lists under render-jobs), or a video no encode report backs."""
    root = project.root
    encode = root / "render.json"
    encoded_at = encode.stat().st_mtime if encode.is_file() else 0.0
    lines = []
    # The film's own jobs only; the authors' stills in .cache/reports belong to vfx.
    for path in sorted(root.glob("render*.json")):
        rep = read_json(path, warn=False)
        status = rep.get("status") if isinstance(rep, dict) else None
        if status not in render.STATUSES or status == "rendered":
            continue
        if path.parent == root and path != encode and path.stat().st_mtime < encoded_at:
            continue  # superseded by a later encode
        note = str(rep.get("message") or "")[:100]
        lines.append(f"fail: {rep.get('job') or path.stem} {status}"
                     + (f" -- {note}" if note else "")
                     + f" [{path.relative_to(root).as_posix()}] -> read the report, "
                       "then re-run the job")
    if project.has("video") and not encode.is_file():
        lines.append("fail: video.mp4 has no render.json to say how it was encoded")
    return lines


def _vfx_blocks(project: workspace.Project) -> list[str]:
    """Why the vfx stage is open: a section whose record carries an error, or
    that no review scored."""
    vfx = read_json(project.path("vfx"), warn=False) if project.has("vfx") else None
    lines = []
    for record in (vfx or {}).get("authored") or []:
        if not isinstance(record, dict):
            continue
        n = record.get("n", "?")
        errors = [f for f in record.get("findings") or [] if str(f).startswith("error")]
        if errors:
            lines.append(f"fail: section {n}: {errors[0][:200]} -> yt vfx --sections {n}")
        elif not record.get("review"):
            lines.append(f"fail: section {n} has no review -> yt vfx --sections {n}")
    return lines


#: Stages whose evidence doctor only warns about, read from the artifacts.
STAGE_BLOCKS = {"render": _render_blocks, "vfx": _vfx_blocks}


def check(lane_dir: str | Path, stage: str, *, work: str | Path | None = None
          ) -> tuple[bool, list[str]]:
    """(done, why) for one stage of the lane's video."""
    if stage not in stages():
        raise ValueError(f"{stage!r} is not a stage of {PROCESS_NAME}: {', '.join(stages())}")
    project = project_for(lane_dir, work=work)
    if not project.root.is_dir():
        return False, [f"no video at {project.root}"]
    report = doctor.check_project(project)
    if stage == DELIVER:
        delivery = report.metrics.get("delivery")
        if delivery is None:
            return False, ["nothing is rendered yet"]
        lines = [f"missing: {m}" for m in delivery["missing"]]
        return bool(delivery["ready"]), lines or ["READY TO PUBLISH"]
    artifact = dict(workspace.STAGES)[stage]
    lines: list[str] = []
    if not project.has(artifact):
        lines.append(f"{workspace.ARTIFACTS[artifact]} is not written yet")
    fails = [f for f in report.findings if f.level == "fail"
             and FINDING_STAGE.get(f.area, f.area) == stage]
    lines += [f"fail: {f.message}" + (f" -> {f.fix}" if f.fix else "") for f in fails]
    if stage in STAGE_BLOCKS:
        lines += STAGE_BLOCKS[stage](project)
    if stage == "keyframe" and project.has(artifact):
        state = keyframe.approval_state(project.root)["state"]
        if state != "approved":
            lines.append(f"the look is {state}, not approved")
    return not lines, lines or [f"{stage} done"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python3 ytstudio/lane.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("process", help="print the govern process (or --write it)")
    pp.add_argument("--write", action="store_true", help=f"write {PROCESS_FILE}")
    pc = sub.add_parser("check", help="exit 0 when the stage is done")
    pc.add_argument("lane_dir")
    pc.add_argument("stage")
    pc.add_argument("--work", help="where videos are kept (default ./work)")
    args = p.parse_args(argv)
    if args.cmd == "process":
        text = json.dumps(process(), indent=2, ensure_ascii=False) + "\n"
        if args.write:
            PROCESS_FILE.parent.mkdir(parents=True, exist_ok=True)
            PROCESS_FILE.write_text(text, encoding="utf-8")
            print(f"wrote {PROCESS_FILE}", file=sys.stderr)
        else:
            print(text, end="")
        return 0
    try:
        ok, lines = check(args.lane_dir, args.stage, work=args.work)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
