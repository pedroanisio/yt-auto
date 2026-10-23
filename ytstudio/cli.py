"""yt -- take a YouTube video from an idea to a rendered cut.

Eleven stages, each reading what the ones before it wrote (the order `yt run` walks):

    yt research  turn one idea into a sourced dossier, with licensed footage
    yt styles    rank the visual treatments that can carry its message
    yt script    write the full AV screenplay, shot by shot
    yt props     fetch CC0 3D props (models, HDRIs, textures) for a video from Poly Haven
    yt voice     record the narration, measure it, caption it
    yt retime    write the measured narration timings back into the script
    yt keyframe  lock the look: the film's lead section, authored once, for approval
    yt vfx       plan the animation and have the model author it for scene-render
    yt sound     spot the effects, ambience, score and mix against it
    yt build     compile all of it into a scene-render document
    yt render    hand the document to the engine

Around them, before and after:

    yt ideas     invent and rank video ideas, deduplicated against a store
    yt pilot     cut a short pilot at full fidelity, for approval
    yt gate      put the render through the delivery gate
    yt review    write the PDF review sheet, and have the model read it
    yt qa        score the rendered film against the publication bar, per pass
    yt improve   route QA's findings to the stages that own them, re-run, keep what improves
    yt retention read a published film's audience curve back onto its shots, for improve

    yt run       all of the above, in order, for one idea
    yt ls        which videos you have, and how far each one got
    yt doctor    check the toolchain and every video, and say what to do next
    yt assess    ask whether a described video can be produced, and prove it with a frame
    yt assets    search open catalogues for one shot's footage
    yt probe     measure what the system can make look real, per class of material and light

Everything for one video lives in `work/<slug>/`, so stages chain without
passing paths -- and every stage also runs on its own, handed files
(`yt pilot --scene cut.xml`, `yt build --script s.json --out cut.xml`):

    yt ideas --niche "semiconductor manufacturing" --count 20
    yt research --title lithography
    yt styles --title lithography --rich
    yt script --title lithography --minutes 12 --polish --verify
    yt sound --title lithography --review

`--title` matches a project already on disk, an idea in `work/ideas.json`, or
is taken as the title itself. Pass `--project DIR` to point at one directly,
`--work DIR` to keep videos somewhere other than `./work`, and `-` to read one
idea as JSON on stdin. Every stage writes its own files into the project and
also prints to stdout, so `--json`/`--md`/`--csv` are only for sending a copy
somewhere else.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import traceback

import claude_engine

from .core import read_json
from . import (assess, assets, build, doctor, env, flags, gate, harness, house, ideas, improve,
               inhibit, keyframe, pilot, probes, props, qa, render, research, retention, retime, review,
               reviewers, script, sound, spend, styles, vfx, voice, workspace)

#: The stages `yt run` walks, in order.
PIPELINE = tuple(stage for stage, _ in workspace.STAGES)


def _flags(*specs) -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    for args, kwargs in specs:
        parent.add_argument(*args, **kwargs)
    return parent


def _parents() -> dict[str, argparse.ArgumentParser]:
    """The flags shared across stages, defined once."""
    return {
        "quiet": _flags(
            (("--quiet",), dict(action="store_true",
                                help="no progress on stderr")),
            (("--env-file",), dict(dest="env_file", metavar="FILE",
                                   help="API keys to load (default: "
                                        "$YT_ENV_FILE, ./_tmp/do_not_delete.env, "
                                        "./.env, ~/.config/pals-notes/.env)")),
            (("--house",), dict(metavar="FILE",
                                help="the channel's standards (default: "
                                     "<work>/house.md and the project's own)")),
            (("--no-house",), dict(action="store_true", dest="no_house",
                                   help="ignore the standards for this run"))),
        "model": _flags(
            (("--model",), dict(default="sonnet",
                                help="model: fable | opus | sonnet | haiku | a "
                                     "full id (default sonnet)")),
            (("--effort",), dict(choices=["low", "medium", "high", "xhigh", "max"],
                                 help="reasoning effort for every call this run "
                                      "makes (default: $CLAUDE_ENGINE_EFFORT, "
                                      "then the claude CLI's own setting)")),
            (("--fallback-model",), dict(dest="fallback_model", metavar="MODEL",
                                         help="model(s) to try when --model is "
                                              "overloaded; comma-separated, in "
                                              "order ($CLAUDE_ENGINE_FALLBACK_MODEL)"))),
        "work": _flags(
            (("--work",), dict(default=workspace.DEFAULT_WORK, metavar="DIR",
                               help="where videos are kept "
                                    f"(default ./{workspace.DEFAULT_WORK})")),
            (("--project",), dict(metavar="DIR",
                                  help="one video's directory, instead of "
                                       "--work plus a title"))),
        "idea": _flags(
            (("stdin",), dict(nargs="?", choices=["-"], default=None,
                              help="read one idea (or a list) as JSON on stdin")),
            (("--from",), dict(dest="source", metavar="FILE",
                               help="ideas.json to pick from "
                                    "(default <work>/ideas.json)")),
            (("--index",), dict(type=int,
                                help="1-based idea to pick (default 1)")),
            (("--title",), dict(help="a project on disk, an idea title, or a "
                                     "substring of either")),
            (("--genre",), dict(help="genre, when passing an idea by hand")),
            (("--description",), dict(help="premise, when passing an idea by hand"))),
    }


# --------------------------------------------------------------------------- #
# the whole pipeline
# --------------------------------------------------------------------------- #

def _register_run(sub, parents: list) -> None:
    p = sub.add_parser(
        "run", parents=parents, help="run every stage for one idea, in order",
        description="Research, direct, write and score one video in one go. A "
                    "stage whose output is already in the project is skipped "
                    "unless named in --redo.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--minutes", type=float, default=10.0,
                   help="target runtime for the script (default 10)")
    p.add_argument("--skip", default="", metavar="STAGES",
                   help=f"comma-separated stages to leave out: {', '.join(PIPELINE)}")
    p.add_argument("--redo", default="", metavar="STAGES",
                   help="comma-separated stages to run again even though their "
                        "output exists, or 'all'")
    p.add_argument("--quick", action="store_true",
                   help="skip the quality passes (deliberation, polish, "
                        "fact-check, listen-back) for a cheaper first draft")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="print the stages this run would walk, each with its "
                        "estimated cost (measured, like or assumed) and the "
                        "total, and run nothing (the project is planted, as "
                        "for any stage)")
    flags.budget(p, default=None, metavar="USD",
                 help="spend cap for the whole run: a stage whose estimate plus "
                      "what the run has spent does not fit is not started "
                      f"(exit {retime.UNSETTLED}), and vfx gets the remainder as "
                      "--stage-budget-usd (default: no cap)")
    flags.reviewer_model(p, default=None,
                         help="the model that reviews the script's plan, keyframe and "
                              "vfx; never the author's own (default "
                              f"{vfx.REVIEWER_MODEL}; --quick drops the plan review). "
                              "`pool` or sdk:<id>, a reviewer from another maker on the "
                              "gateway engine, reaches the script's review only")
    flags.review_budget(p, default=None,
                        help=f"what one review may cost (default {vfx.REVIEW_USD:g})")
    p.set_defaults(run=_run_pipeline)

#: Stages that call no model: `yt run` hands them the project and the quiet
#: flag, not the model flags (props asks a model and takes them like the rest).
NO_MODEL = ("render",)
MODEL_FLAGS = ("--model", "--effort", "--fallback-model")


def run_extras(args, *, redo: set) -> dict[str, list[str]]:
    """The flags `yt run` adds to each stage: the quality passes unless
    --quick, a fresh research when it is redone, and the fetches that turn a
    design into files (props, the sound cues). The plan review (script
    --review) is a quality pass, so --quick drops it with the others."""
    quick = getattr(args, "quick", False)
    # The reviewer flags go to the stages that review; unset, each keeps its default.
    # A pool reviewer (`pool`, `sdk:<id>`, AUTO-125) reads text only, so it goes
    # to the script alone and keyframe and vfx keep their default reviewer.
    named: list[str] = []
    if getattr(args, "reviewer_model", None):
        named = ["--reviewer-model", str(args.reviewer_model)]
    cap: list[str] = []
    if getattr(args, "review_budget_usd", None) is not None:
        cap = ["--review-budget-usd", f"{args.review_budget_usd:g}"]
    script_reviewer = named + cap
    reviewer = ([] if reviewers.is_gateway(getattr(args, "reviewer_model", None))
                else named) + cap
    return {
        # --redo means run again, which for research means ignoring its
        # cached stages too, or the "rerun" would just replay them.
        "research": ["--fresh"] if redo & {"research", "all"} and not quick else [],
        "styles": ["--rich"] + ([] if quick else ["--deliberate", "critique"]),
        "script": ["--minutes", str(args.minutes)]
                  + ([] if quick else ["--polish", "--verify", "--review"] + script_reviewer),
        "props": [],
        # The look is locked once per set of inputs; a redo means plan and
        # author the lead section again, not replay the cached one.
        "keyframe": (["--fresh"] if redo & {"keyframe", "all"} else []) + reviewer,
        "voice": [],
        # The screenplay takes the narration's measured lengths before
        # anything is authored against it.
        "retime": [],
        # vfx caches its own stages by name, so a redo without --fresh would
        # replay the earlier authoring against the clock it was asked to fix.
        "vfx": (["--fresh"] if redo & {"vfx", "all"} else []) + reviewer,
        # A cue Freesound cannot fill is synthesised, so the sheet never
        # leaves the stage with holes the build would play as silence.
        "sound": ["--fetch", "--synth"] + ([] if quick else ["--review"]),
        "build": [],
        "render": ["--quality", "draft"] if quick else [],
    }


def stage_argv(stage: str, base: list[str], extra: dict[str, list[str]]) -> list[str]:
    """The argv for one stage of `yt run`: the shared flags plus its own, less
    the model flags for a stage that calls no model."""
    if stage in NO_MODEL:
        kept, skip = [], False
        for n, flag in enumerate(base):
            if skip:
                skip = False
            elif flag in MODEL_FLAGS:
                skip = True
            else:
                kept.append(flag)
        return [stage, *kept, *extra.get(stage, [])]
    return [stage, *base, *extra.get(stage, [])]


def settle_retime(code: int, project: workspace.Project, run_stage, *,
                  say=lambda msg: print(msg, file=sys.stderr)) -> int:
    """Act on retime's verdict before anything is planned on the clock.

    `rerecord` is plumbing: the takes are recorded again and the screenplay
    reconciled once more. `rewrite`, or a read sent back twice, is the
    editor's work, so the run stops with retime's code and the hint printed.
    """
    if code != retime.UNSETTLED:
        return code
    back = retime.unsettled(read_json(project.path("retime"), warn=False) or {})
    if back.get("rewrite") or not back.get("rerecord"):
        labels = ",".join(back.get("rewrite") or [])
        say(f"the editor sent {labels or 'a line'} back to the screenplay: run `yt script "
            f"--revise retime --project {project.root}`, then `yt run --redo voice,retime` "
            "(a take is cached by its text, so voice records only the changed lines)")
        return code
    labels = ",".join(back["rerecord"])
    say(f"\n== voice: recording {labels} again, as the editor asked")
    code = run_stage("voice", ["--redo", labels])
    if code:
        return code
    say("\n== retime: reconciling the new read")
    code = run_stage("retime", [])
    if code == retime.UNSETTLED:
        say("the read was sent back twice; settle it by hand (yt voice --redo, "
            "yt script --revise retime) and run again")
    return code


def look_gate(stage: str, project: workspace.Project, skip: set, redo: set = frozenset(),
              *, say=lambda msg: print(msg, file=sys.stderr)) -> str:
    """What the look's approval means for `stage` in `yt run`:

    - "again" for the keyframe when it was rejected, changed after approval,
      or its pass left nothing approvable: it is authored again (notes first);
    - "stop" before vfx while a look awaits the operator: nothing downstream
      is paid for against a look that may change;
    - "relook" for vfx when an approved look from the film is not in the
      animation that exists: its section is carried in and the others are
      authored again against it (`yt vfx --relook`), and build and render follow;
    - "" otherwise."""
    if "keyframe" in skip or stage not in ("keyframe", "vfx"):
        return ""
    state = keyframe.approval_state(project.root)["state"]
    if stage == "keyframe":
        return ("again" if state in ("rejected", "stale")
                or keyframe.look_needs_redo(project.root) else "")
    if state == "pending":
        start = PIPELINE.index("vfx")
        rest = (list(PIPELINE[start:]) if redo & {"keyframe", "all"}
                else [s for s in PIPELINE[start:] if s in redo])
        again = f" --redo {','.join(rest)}" if rest else ""
        say(f"\n== vfx: waiting for the look's approval. Look at "
            f"{project.root / 'keyframe.md'}, then\n"
            f"  yt keyframe --project {project.root} --approve   (or --reject \"notes\")\n"
            f"  and `yt run --project {project.root}{again}` to continue; --skip keyframe "
            "runs without a look")
        return "stop"
    if (state == "approved" and project.has("vfx")
            and keyframe.load_record(project.root).get("source") == "section"
            and not keyframe.film_carries_look(project.root)):
        return "relook"
    return ""


class Beside(threading.Thread):
    """One stage run beside the walk, on its own thread. `code` is what it
    exited once joined; a crash is printed and counts as a failure, so the
    walk never waits on a stage that is gone. A daemon: a Ctrl-C ends the
    run at once, and the stage with the process, instead of the interpreter
    waiting on the thread while it spends on."""

    def __init__(self, stage: str, run) -> None:
        super().__init__(name=f"yt {stage}", daemon=True)
        self.stage, self._run, self.code = stage, run, 1

    def run(self) -> None:
        try:
            self.code = int(self._run() or 0)
        except SystemExit as exc:  # argparse's refusal would otherwise vanish with the thread
            self.code = int(exc.code) if isinstance(exc.code, int) and exc.code else 1
        except BaseException:  # noqa: BLE001 -- reported here, not lost with the thread
            traceback.print_exc()
            self.code = 1

    def join_for(self, say) -> int:
        if self.is_alive():
            say(f"\n== {self.stage}: waiting for it to finish")
        self.join()
        return self.code


def stage_state(stage: str, project: workspace.Project, skip: set, redo: set, *,
                say=lambda msg: print(msg, file=sys.stderr)) -> str:
    """What `yt run` does at a stage's slot, from the project as it stands:
    "skip", "stop" (the look awaits the operator), "done" (its output exists
    and it was not named in --redo) or "run"."""
    if stage in skip:
        return "skip"
    look = look_gate(stage, project, skip, redo, say=say)
    if look == "stop":
        return "stop"
    if (project.has(dict(workspace.STAGES)[stage]) and "all" not in redo
            and stage not in redo and look not in ("again", "relook")):
        return "done"
    return "run"


def walk_order(skip: set = frozenset()) -> list[str]:
    """The stages in the order `yt run` reaches them: PIPELINE with sound
    moved to where it starts, beside the keyframe, less the ones skipped.
    The plan and the cap both price this order, since an assumed vfx folds
    in what follows it: sound priced after vfx would count at nothing."""
    order = [s for s in PIPELINE if s != "sound"]
    order.insert(order.index("keyframe"), "sound")
    return [s for s in order if s not in skip]


def run_plan(project: workspace.Project, skip: set, redo: set, *,
             say=lambda msg: print(msg, file=sys.stderr)) -> tuple[list[str], str]:
    """The stages `yt run` would walk now, in order, sound where it starts
    (beside the keyframe), and why the walk would stop early, or "". A
    keyframe that runs leaves a look awaiting approval, so the plan ends
    there as the walk does."""
    plan: list[str] = []
    for stage in walk_order():
        state = stage_state(stage, project, skip, redo, say=say)
        if state == "stop":
            return plan, f"stops before {stage}: the look awaits the operator"
        if state == "run":
            plan.append(stage)
            if stage == "keyframe":
                return plan, ("stops before vfx: the look will await the operator "
                              "(yt keyframe --approve, then yt run again)")
    return plan, ""


def print_plan(plan: list[str], rows: list[dict], stop: str) -> None:
    """One row per stage the run would walk, the total, and where it stops."""
    priced = {r["stage"]: r for r in rows}
    print(f"yt run would walk {len(plan)} stage(s); nothing run:")
    for stage in plan:
        r = priced.get(stage) or {"usd": 0.0, "basis": "assumed", "source": "no estimate"}
        beside = "  (beside the keyframe and vfx)" if stage == "sound" else ""
        print(f"  {stage:<10} {'$' + format(r['usd'], '.2f'):>8}  {r['basis']:<9} "
              f"{r['source']}{beside}")
    print(f"  {'total':<10} {'$' + format(spend.total(priced[s] for s in plan if s in priced), '.2f'):>8}")
    if stop:
        print(f"  {stop}")


def _run_pipeline(args) -> int:
    split = lambda s: [p.strip() for p in s.split(",") if p.strip()]  # noqa: E731
    skip, redo = set(split(args.skip)), set(split(args.redo))
    if unknown := (skip | redo) - set(PIPELINE) - {"all"}:
        print(f"error: unknown stage(s) {', '.join(sorted(unknown))}; pick from "
              f"{', '.join(PIPELINE)}", file=sys.stderr)
        return 1
    # keyframe and vfx review, and the script's plan review unless --quick: a
    # model never grades its own work, so a run that would reach them is
    # refused here, before any stage spends.
    reviews = ({"keyframe", "vfx"} | (set() if args.quick else {"script"})) - skip
    asked = getattr(args, "reviewer_model", None)
    if reviewers.is_gateway(asked):
        # A pool reviewer (AUTO-125) reads the script only; keyframe and vfx
        # keep vfx.REVIEWER_MODEL, checked by family as before. It is
        # resolved once here and handed to the script as its gateway id.
        if "script" not in reviews:
            print(f"error: --reviewer-model {asked} reviews the script only, and this run "
                  f"makes no script review ({'--quick' if args.quick else '--skip script'}); "
                  "pass a claude or codex model, or leave the flag out", file=sys.stderr)
            return 2
        inspected = sorted(reviews & {"keyframe", "vfx"})
        if inspected and (own := flags.own_reviewer(args.model, vfx.REVIEWER_MODEL)):
            print(f"{own}\n  --reviewer-model {asked} reaches the script's review only; "
                  f"{' and '.join(inspected)} keep their default reviewer, "
                  f"{vfx.REVIEWER_MODEL}: skip them (--skip keyframe,vfx) or pass another "
                  "--model", file=sys.stderr)
            return 2
        try:
            pooled = reviewers.resolve(args.model, asked)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        args.reviewer_model = reviewers.PREFIX + pooled.model
    elif reviews and (own := flags.own_reviewer(args.model, asked or vfx.REVIEWER_MODEL)):
        print(own, file=sys.stderr)
        return 2

    idea, project = workspace.resolve(args)
    parser = build_parser()
    base = ["--project", str(project.root), "--model", args.model]
    if args.effort:
        base += ["--effort", args.effort]
    if args.fallback_model:
        base += ["--fallback-model", args.fallback_model]
    if args.quiet:
        base.append("--quiet")

    extra = run_extras(args, redo=redo)
    artifacts = dict(workspace.STAGES)
    say = lambda msg: print(msg, file=sys.stderr)  # noqa: E731
    say(f"{idea.title}\n  -> {project.root}")

    if args.dry_run:
        plan, stop = run_plan(project, skip, redo, say=say)
        print_plan(plan, spend.stage_estimates(project.root, plan, extras=extra), stop)
        return 0

    def run_stage(stage: str, argv_extra: dict[str, list[str]]) -> int:
        stage_args = parser.parse_args(stage_argv(stage, base, argv_extra))
        return stage_args.run(stage_args)

    # The cap: a stage is started only when its estimate fits beside what the
    # stages that ran have reported. Sound counts at its estimate while it
    # runs beside, at its report once joined. Priced in walk order, as the
    # plan is, so a stage is folded into the vfx default only when it follows it.
    budget = args.budget_usd
    priced: dict[str, dict] = {}
    if budget is not None:
        priced = {r["stage"]: r for r in spend.stage_estimates(project.root, walk_order(skip),
                                                               extras=extra)}
    ran: set[str] = set()
    committed, over = 0.0, False

    def paid(stage: str) -> float:
        rel = spend.MANIFEST.get(stage)
        return (spend.cost_in(read_json(project.root / rel, warn=False)) or 0.0) if rel else 0.0

    def refused(stage: str) -> bool:
        nonlocal over
        if budget is None:
            return False
        spent = sum(paid(s) for s in ran) + committed
        row = priced.get(stage) or {"usd": 0.0, "basis": "assumed"}
        if spent + row["usd"] <= budget:
            return False
        say(f"\n== {stage}: estimated at ${row['usd']:.2f} ({row['basis']}) and "
            f"${spent:.2f} of the ${budget:.2f} budget is spent; it does not fit, "
            f"stopping here (a higher --budget-usd, or `yt {stage}` by hand)")
        over = True
        return True

    def run_capped(stage: str, argv: list[str]) -> int:
        # The re-record retime asks for is a stage like any other under the cap.
        return retime.UNSETTLED if refused(stage) else run_stage(stage, {stage: argv})

    # Only the build reads the cue sheet, so the sound stage runs beside the
    # keyframe and vfx on its own thread (its own harness, its own files) and
    # is joined before the build; a walk that stops early still waits for it.
    beside: Beside | None = None
    code = 0
    for stage in PIPELINE:
        if stage == "keyframe" and stage_state("sound", project, skip, redo) == "run":
            if refused("sound"):
                code = retime.UNSETTLED
                break
            committed = (priced.get("sound") or {}).get("usd", 0.0)
            say("\n== sound: beside the keyframe and vfx")
            beside = Beside("sound", lambda: run_stage("sound", extra))
            beside.start()
        if stage == "sound" and beside is not None:
            code = beside.join_for(say)
            committed, ran = 0.0, ran | {"sound"}
            if code:
                break
            continue
        state = stage_state(stage, project, skip, redo)
        if state == "skip":
            say(f"\n== {stage}: skipped")
            continue
        if state == "stop":
            break
        if state == "done":
            say(f"\n== {stage}: already done ({artifacts[stage]}); --redo to run it again")
            continue
        if refused(stage):
            code = retime.UNSETTLED
            break
        argv_extra = dict(extra)
        if stage == "vfx" and "keyframe" not in skip and keyframe.approved_keyframe(
                project.root) and keyframe.load_record(project.root).get("source") == "section":
            # The approved section is never thrown away by a --fresh replan:
            # the first animation carries it, and an existing one takes it with
            # --relook (the others authored again on the plan it was made under;
            # `--redo keyframe` is what replans).
            argv_extra["vfx"] = ["--relook"] if project.has("vfx") else []
            if project.has("vfx"):
                print("\n== vfx: the approved look goes into the animation: its section is "
                      "carried and the others are authored again against it", file=sys.stderr)
                redo = redo | {"build", "render"}
        if stage == "vfx" and budget is not None:
            # What is left of the cap is the stage's own, every section together.
            left = budget - sum(paid(s) for s in ran) - committed
            argv_extra["vfx"] = [*argv_extra.get("vfx", []), "--stage-budget-usd", f"{left:.2f}"]
        say(f"\n== {stage}")
        code = run_stage(stage, argv_extra)
        ran.add(stage)  # its report counts before anything it asks for is priced
        if stage == "retime":
            code = settle_retime(code, project, run_capped)
        if code:
            break
    if code and not over:
        say(f"\n{stage} exited {code}; stopping here")
    if beside is not None and "sound" not in ran:
        # The walk stopped before the sound slot; what sound did still counts,
        # whether the thread is still at it or already failed and gone.
        sound_code = beside.join_for(say)
        if sound_code:
            say(f"\nsound exited {sound_code}")
            code = code or sound_code
    return code



# --------------------------------------------------------------------------- #
# what have I got
# --------------------------------------------------------------------------- #

def _register_ls(sub, parents: list) -> None:
    p = sub.add_parser(
        "ls", parents=parents, help="list videos and how far each one got",
        description="One line per video in the work directory, newest first.")
    p.add_argument("--files", action="store_true",
                   help="also list every file in each project")
    p.set_defaults(run=_list_projects)


def _list_projects(args) -> int:
    work = workspace.work_dir(args)
    found = workspace.projects(work)
    ideas_file = work / workspace.IDEAS_FILE
    if ideas_file.exists():
        try:
            rows = json.loads(ideas_file.read_text(encoding="utf-8"))
            print(f"{len(rows)} idea(s) in {ideas_file}")
        except (OSError, ValueError):
            pass
    if not found:
        print(f"no videos in {work}/ yet. Start with:\n"
              '  yt ideas --niche "your topic"\n'
              "  yt research --index 1")
        return 0

    width = max(len(p.root.name) for p in found)
    for project in found:
        done = project.done()
        marks = "  ".join(("+ " if stage in done else "- ") + stage
                          for stage in PIPELINE)
        print(f"{project.root.name:<{width}}   {marks}")
        if args.files:
            for file in sorted(project.root.iterdir()):
                print(f"    {file.name}" + ("/" if file.is_dir() else ""))
    print(f"\n{len(found)} video(s) in {work}/")
    return 0


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    shared = _parents()
    quiet, model, work, idea = (shared["quiet"], shared["model"],
                                shared["work"], shared["idea"])
    parser = argparse.ArgumentParser(
        prog="yt", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="stage", metavar="<stage>", required=True)

    ideas.register(sub, [model, quiet, work])
    research.register(sub, [idea, model, quiet, work])
    styles.register(sub, [idea, model, quiet, work])
    script.register(sub, [idea, model, quiet, work])
    voice.register(sub, [idea, model, quiet, work])
    vfx.register(sub, [idea, model, quiet, work])
    sound.register(sub, [idea, model, quiet, work])
    build.register(sub, [idea, model, quiet, work])
    render.register(sub, [idea, quiet, work])
    retime.register(sub, [idea, model, quiet, work])
    pilot.register(sub, [idea, model, quiet, work])
    gate.register(sub, [idea, model, quiet, work])
    review.register(sub, [idea, model, quiet, work])
    qa.register(sub, [idea, model, quiet, work])
    improve.register(sub, [idea, model, quiet, work])
    retention.register(sub, [idea, quiet, work])
    doctor.register(sub, [quiet, work])
    assess.register(sub, [model, quiet, work])
    props.register(sub, [model, quiet, work])
    keyframe.register(sub, [idea, model, quiet, work])
    probes.register(sub, [model, quiet, work])
    _register_run(sub, [idea, model, quiet, work])
    _register_ls(sub, [work, quiet])
    assets.register(sub, [quiet])
    return parser


#: Stages that never wait on a model or a render host need no inhibitor.
LOCAL_ONLY = frozenset({"ls", "assets", "props", "retention"})


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Effort and fallback apply to every engine a stage builds, including the
    # ones that bypass the harness, so they are process-wide defaults rather
    # than one more argument threaded through each stage.
    claude_engine.set_defaults(effort=getattr(args, "effort", None),
                               fallback_model=getattr(args, "fallback_model", None))
    # No other session on the machine may talk to a model mid-stage.
    harness.isolate_sessions()
    for name, path in env.load(getattr(args, "env_file", None)):
        if not getattr(args, "quiet", False):
            # The name, never the value.
            print(f"env: {name} from {path}", file=sys.stderr)
    try:
        if args.stage in LOCAL_ONLY:
            return args.run(args)
        with inhibit.held(f"yt {args.stage}"):
            return args.run(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
