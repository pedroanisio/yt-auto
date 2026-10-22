"""Cut a pilot: the finished video, shorter, at full fidelity.

A pilot is not a preview. It renders through the same composition, the same
materials, the same colour pipeline and the same audio graph as the finished
film -- it is simply shorter, so an operator can approve the thing or send it
back before paying for the whole render.

    yt pilot --title monty                      # 60 seconds
    yt pilot --title monty --seconds 30,60,180  # three cuts, nested
    yt pilot --title monty --render             # and hand them to the engine

Shortness is the easy part; coverage is the point. The first 30 seconds of a
video exercise almost none of it, so the selection is a coverage problem solved
in Python: every visual treatment, every effect type, every cue kind, every
motif, the supers, the hook and the payoff are the universe, and the cut is the
cheapest set of shots that touches as much of it as the budget allows. What it
could *not* fit is written down, because that is what an operator must not
approve blind.

The cuts nest: the 30 is a subset of the 60, which is a subset of the 180, so
comparing them shows growth rather than a different edit.

Each run of adjacent shots becomes one `<segment>` of a new `<output>` on a
copy of the scene, so the engine rebuilds the audio in output time -- stems
mapped, ducking and master normalisation applied once -- and remaps the
captions with it. Between runs a visible transition marks the join, so a jump
does not read as a continuity error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from . import house, spend, workspace
from .harness import Harness, HarnessError, brief
from .core import _key, read_json, timecode
from .build import duplicate_animations, shots_from_scene
from .scene_sdk import enum_of, sdk
from .script import visible_text
from .script import load_script

#: Preferred join, when the schema has it.
JOIN = ("dip-to-color", "crossfade", "cut")

ROLE = (
    "You are the editor cutting pilots of this film: nested shorter edits at "
    "full fidelity, so an operator can judge the opening, the strongest "
    "visuals, the payoff and the voice before paying for the whole render. A "
    "pilot is not a summary; it is evidence. Prefer a few long runs of "
    "adjacent shots over many fragments, keep the hook and the payoff, and "
    "choose the shots whose pictures prove the treatment -- the ones a "
    "coverage count cannot rank. Each cut must sit inside the next larger "
    "one, so the three pilots are one edit growing."
)

CUT_SCHEMA = {
    "type": "object",
    "properties": {
        "cuts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "budget": {"type": "integer"},
                    "shots": {"type": "array", "items": {"type": "string"},
                              "description": "shot labels, in film order"},
                    "why": {"type": "string"},
                },
                "required": ["budget", "shots", "why"],
            },
        },
        "transition": {"type": "string",
                       "description": "the join between runs, from the list given"},
        "notes": {"type": "string"},
    },
    "required": ["cuts", "transition", "notes"],
}


def select_prompt(beats: list[Beat], budgets: list[int], proposal: dict[int, dict],
                  joins: list[str], violations: list[str] | None = None) -> str:
    rows = [{"label": b.label, "section": b.section, "at": round(b.at, 2),
             "seconds": round(b.seconds, 2), "narration": b.text, "visual": b.visual,
             "traits": sorted(b.traits)} for b in beats]
    parts = [
        f"Choose the shots of each pilot cut, for budgets {budgets} seconds. "
        "Return one entry per budget with the shot labels in film order. The "
        "cuts must be nested (every shot of a smaller cut appears in each "
        "larger one) and each must fit its budget (whole shots only). Pick "
        "`transition` from the joins available.",
        brief("Shots, in order", rows),
        brief("A coverage-maximising proposal, for reference (not binding)",
              {str(k): {"shots": v["shots"], "does_not_prove": v["does_not_prove"]}
               for k, v in proposal.items()}),
        brief("Joins available", joins),
    ]
    if violations:
        parts.append(brief("Your previous selection was invalid; fix these",
                           violations))
    return "\n\n".join(parts)



@dataclass
class Beat:
    """One shot, with everything about the film it is evidence of."""
    label: str
    at: float
    seconds: float
    section: str
    traits: set[str] = field(default_factory=set)
    order: int = 0          # when the sweep picked it; lower is more important
    text: str = ""          # the narration, so a reader can judge the cut
    visual: str = ""

    @property
    def out(self) -> float:
        return self.at + self.seconds


def traits_of(shot, placed: dict, *, section: str, cues: list[dict],
              fx: set[str], first: bool, last: bool) -> set[str]:
    traits = {f"section:{_key(section)[:24]}"}
    if getattr(shot, "style", ""):
        traits.add(f"style:{_key(shot.style)[:24]}")
    traits.add(f"asset:{placed.get('asset', 'placeholder')}")
    traits.add("voice" if (shot.narration or "").strip() else "silent")
    if visible_text(shot).strip():
        traits.add("super")
    if getattr(shot, "source", "") and shot.source.upper() != "UNSOURCED":
        traits.add("cited")
    start, end = placed.get("at", 0.0), placed.get("at", 0.0) + placed.get("seconds", 0.0)
    for cue in cues:
        if cue["out"] > start and cue["at"] < end:
            traits.add(f"cue:{cue['kind']}")
            if cue.get("motif"):
                traits.add(f"motif:{_key(cue['motif'])[:20]}")
    traits |= {f"fx:{_key(t)[:20]}" for t in fx}
    # The screenplay names its beats (AUTO-90); `first` and `last` are the
    # positional stand-ins `gather` passes when it does not.
    function = getattr(shot, "function", "")
    if function in STORY_TRAITS:
        traits.add(function)
    if first:
        traits.add("hook")
    if last:
        traits.add("payoff")
    return traits


#: The screenplay functions a beat carries as a trait of the same name.
STORY_TRAITS = ("hook", "rehook", "payoff")


def gather(project, overrides: dict | None = None) -> tuple[list[Beat], dict]:
    """Every shot, with its traits, from whatever stages have run.

    With a screenplay and a build manifest the traits are rich; with only the
    document, the shots are its beat markers and the traits are what a
    document can say about itself (section, hook, payoff).
    """
    overrides = overrides or {}
    src = lambda name: project.source(name, overrides.get(name))  # noqa: E731
    scene_file = src("scene")
    if not scene_file:
        raise RuntimeError("a pilot needs a scene document; run `yt build` first "
                           "or pass --scene")
    script_file = src("script")
    build = read_json(src("build"), warn=False)
    if not build or not script_file:
        document = Path(scene_file).read_bytes()
        rows = shots_from_scene(document)
        if not rows:
            raise RuntimeError("the document has no markers to cut by, and there "
                               "is no build manifest or screenplay beside it")
        background = ""
        try:
            background = ElementTree.fromstring(document).find("project").get("background", "")
        except (ElementTree.ParseError, AttributeError):
            pass
        beats = [Beat(label=r["label"], at=float(r["at"]), seconds=float(r["seconds"]),
                      section=r.get("section", ""),
                      traits={f"section:{_key(r.get('section', ''))[:24]}", "document"}
                      | ({"hook"} if n == 0 else set())
                      | ({"payoff"} if n == len(rows) - 1 else set()))
                 for n, r in enumerate(rows)]
        return beats, {"scene": {"background": background}, "shots": rows,
                       "from": "document"}
    sections, _ = load_script(script_file)
    placed = {row["label"]: row for row in build.get("shots", [])}
    sound = read_json(src("sound"), warn=False) or {}
    cues = [{"at": float(c.get("at", 0)), "out": float(c.get("at", 0)) + float(c.get("seconds", 0)),
             "kind": c.get("kind", "sfx"), "motif": c.get("motif", "")}
            for c in sound.get("cues", [])]
    vfx = read_json(src("vfx"), warn=False) or {}
    fx_by_section = {}
    for row in vfx.get("sections", []):
        key = _key(str(row.get("section", "")))
        fx_by_section[key] = {e.get("type", "") for e in row.get("effects", [])
                              if e.get("type")}

    flat = [(section, shot) for section in sections for shot in section.shots]
    # The opening is always a hook: a pilot that starts mid-film says nothing
    # about it. The payoff is the shot the screenplay marks so; one written
    # before shots had a function (or marking none) is ended by its last
    # shot, and the record says the cut stood on position.
    marked = {getattr(shot, "function", "") for _, shot in flat}
    notes = []
    if flat and "payoff" not in marked:
        notes.append(f"note: the screenplay marks no payoff shot; the last shot, "
                     f"{flat[-1][1].label}, stands for it")
    beats = []
    for n, (section, shot) in enumerate(flat):
        row = placed.get(shot.label)
        if not row:
            continue
        beats.append(Beat(
            label=shot.label, at=float(row["at"]), seconds=float(row["seconds"]),
            section=section.heading,
            text=" ".join((shot.narration or "").split())[:240],
            visual=(shot.visual or "")[:240],
            traits=traits_of(shot, row, section=section.heading, cues=cues,
                             fx=fx_by_section.get(_key(section.heading), set()),
                             first=(n == 0),
                             last=(n == len(flat) - 1 and "payoff" not in marked))))
    if not beats:
        raise RuntimeError("the built scene has no shots to choose from")
    if notes:
        build = {**build, "beat_notes": notes}
    return sorted(beats, key=lambda b: b.at), build


# --------------------------------------------------------------------------- #
# the selection
# --------------------------------------------------------------------------- #

def rank(beats: list[Beat]) -> list[Beat]:
    """Order the shots by what each one adds that nothing chosen yet proves.

    Greedy maximum coverage per second, with a bonus for a shot next to one
    already taken: two long runs read as an edit, eight stray shots read as a
    trailer. The hook and the payoff are taken first, because a pilot that
    opens in the middle tells an operator nothing about the opening.
    """
    must = [b for b in beats if "hook" in b.traits or "payoff" in b.traits]
    chosen: list[Beat] = []
    covered: set[str] = set()
    for beat in must:
        chosen.append(beat)
        covered |= beat.traits

    pool = [b for b in beats if b not in chosen]
    while pool:
        best, best_score = None, 0.0
        for beat in pool:
            fresh = beat.traits - covered
            if not fresh:
                continue
            score = len(fresh) / max(1.0, beat.seconds)
            if any(abs(beat.at - c.out) < 1.0 or abs(c.at - beat.out) < 1.0
                   for c in chosen):
                score *= 1.25
            if score > best_score:
                best, best_score = beat, score
        if best is None:
            break
        chosen.append(best)
        covered |= best.traits
        pool.remove(best)

    # Whatever is left fills a budget that still has room, nearest first.
    chosen += sorted(pool, key=lambda b: b.at)
    for n, beat in enumerate(chosen):
        beat.order = n
    return chosen


def runs_of(picked: list[Beat]) -> list[list[Beat]]:
    """Adjacent shots become one segment; a gap starts a new one."""
    out: list[list[Beat]] = []
    for beat in sorted(picked, key=lambda b: b.at):
        if out and abs(beat.at - out[-1][-1].out) < 1.0:
            out[-1].append(beat)
        else:
            out.append([beat])
    return out


def cut(beats: list[Beat], ranked: list[Beat], budget: float) -> dict:
    """The cut for one budget, and an honest account of what it misses.

    The cut is the longest *prefix* of the ranking that fits, never a
    cherry-pick: skipping a shot that does not fit and taking a cheaper one
    further down makes a small cut that the next size up does not contain, and
    then the three pilots are three different edits instead of one growing.
    So a shot too long for the budget stops the cut, and the slack is
    reported rather than filled.
    """
    picked, spent = [], 0.0
    for beat in ranked:
        if picked and spent + beat.seconds > budget:
            break
        picked.append(beat)
        spent += beat.seconds
    return account(beats, picked, budget)


def cut_from(beats: list[Beat], labels: list[str], budget: float) -> dict:
    """The cut the editor chose, by shot label, in the same shape as `cut`."""
    by_label = {b.label: b for b in beats}
    picked = [by_label[label] for label in labels if label in by_label]
    return account(beats, picked, budget)


def validate(beats: list[Beat], chosen: dict[int, list[str]],
             budgets: list[int], *, slack: float = 0.05) -> list[str]:
    """What is wrong with a selection, in words the editor can act on.

    A shot must exist; a cut may run over its budget by `slack` plus one
    second (a shot is not cut mid-word to fit); and each cut must sit inside
    the next larger one, so the pilots are one growing edit.
    """
    known = {b.label: b for b in beats}
    out = []
    for budget in budgets:
        labels = list(chosen.get(budget) or [])
        if not labels:
            out.append(f"the {budget}s cut has no shots")
            continue
        unknown = [l for l in labels if l not in known]
        if unknown:
            out.append(f"the {budget}s cut names shots that do not exist: "
                       f"{', '.join(unknown)}")
        spent = sum(known[l].seconds for l in labels if l in known)
        if spent > budget * (1 + slack) + 1.0:
            out.append(f"the {budget}s cut runs {spent:.1f}s, over its budget")
    for small, large in zip(budgets, budgets[1:]):
        missing = set(chosen.get(small) or []) - set(chosen.get(large) or [])
        if missing:
            out.append(f"the {small}s cut is not inside the {large}s cut: "
                       f"{', '.join(sorted(missing))} would be dropped as it grows")
    return out


def account(beats: list[Beat], picked: list[Beat], budget: float) -> dict:
    """One cut, and an honest account of what it misses."""
    spent = sum(b.seconds for b in picked)
    universe = set().union(*(b.traits for b in beats))
    covered = set().union(*(b.traits for b in picked)) if picked else set()
    runs = runs_of(picked)
    return {
        "seconds": round(spent, 2),
        "budget": budget,
        "slack": round(budget - spent, 2),
        "shots": [b.label for b in sorted(picked, key=lambda b: b.at)],
        "runs": [{"from": run[0].label, "to": run[-1].label,
                  "at": round(run[0].at, 3), "out": round(run[-1].out, 3),
                  "seconds": round(run[-1].out - run[0].at, 3),
                  "section": run[0].section} for run in runs],
        "cuts": max(0, len(runs) - 1),
        "proves": sorted(covered),
        "does_not_prove": sorted(universe - covered),
        "coverage": round(100 * len(covered) / max(1, len(universe))),
    }


# --------------------------------------------------------------------------- #
# the document
# --------------------------------------------------------------------------- #

def add_outputs(scene, cuts: dict[int, dict], *, codec: str, join: str,
                join_seconds: float, background: str) -> list[str]:
    """One `<output>` of `<segment>`s per budget, on a copy of the scene."""
    names = []
    for budget, plan in sorted(cuts.items()):
        name = f"pilot-{budget}s"
        output = scene.add_output(path=f"{name}.mp4", codec=codec)
        output.id = name
        output.joinFade = 0.05
        for n, run in enumerate(plan["runs"]):
            segment = output.add_segment()
            segment.id = f"{name}-{n + 1}"
            segment.from_ = run["at"]
            segment.to = run["out"]
            if n < len(plan["runs"]) - 1 and join != "cut":
                transition = segment.add_transition(type=join)
                transition.duration = join_seconds
                transition.alignment = "center"
                if join == "dip-to-color" and background:
                    transition.color = background
        names.append(name)
    return names


def print_pilot(title: str, cuts: dict[int, dict]) -> None:
    print(f"\n{title} — PILOTS")
    for budget, plan in sorted(cuts.items()):
        slack = f", {plan['slack']:.1f}s slack" if plan.get("slack", 0) > 1 else ""
        print(f"\n  {budget}s  ->  {plan['seconds']:.1f}s actual{slack} · "
              f"{len(plan['shots'])} shots in {len(plan['runs'])} run(s) · "
              f"{plan['cuts']} join(s) · covers {plan['coverage']}%")
        for run in plan["runs"]:
            print(f"    {timecode(int(run['at']))}–{timecode(int(run['out']))}  "
                  f"{run['from']}→{run['to']}  ({run['seconds']:.1f}s)  "
                  f"{run['section'][:42]}")
        missed = plan["does_not_prove"]
        if missed:
            shown = ", ".join(missed[:10])
            more = f" (+{len(missed) - 10})" if len(missed) > 10 else ""
            print(f"    does NOT prove: {shown}{more}")


def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "pilot", parents=list(parents),
        help="cut a short pilot at full fidelity for approval",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seconds", default="60", metavar="LIST",
                   help="comma-separated budgets (default 60)")
    p.add_argument("--codec", default="h264", help="codec for the pilot outputs")
    p.add_argument("--transition", default="",
                   help=f"join between runs (default: the first of "
                        f"{', '.join(JOIN)} the schema has)")
    p.add_argument("--transition-seconds", type=float, default=0.4,
                   dest="transition_seconds", help="length of that join")
    p.add_argument("--out", dest="pilot_out", metavar="FILE",
                   help="where to write the pilot scene (default pilot.scene.xml)")
    p.add_argument("--render", action="store_true",
                   help="render the pilots right away with the engine")
    p.add_argument("--fresh", action="store_true",
                   help="ask the editor again even if the inputs did not change")
    workspace.add_inputs(p, "scene", "build", "script", "sound", "vfx")
    p.set_defaults(run=run)
    return p


def run(args) -> int:
    idea, project = workspace.resolve(args, inputs=("scene",))
    overrides = {name: workspace.override(args, name)
                 for name in ("scene", "build", "script", "sound", "vfx")}
    scene_file = project.source("scene", overrides["scene"])
    if not scene_file:
        print("error: no scene; run `yt build` first, or pass --scene FILE",
              file=sys.stderr)
        return 1
    try:
        budgets = sorted({int(float(s)) for s in args.seconds.split(",") if s.strip()})
    except ValueError:
        print(f"error: --seconds {args.seconds!r} is not a list of numbers",
              file=sys.stderr)
        return 1
    if not budgets:
        print("error: --seconds is empty", file=sys.stderr)
        return 1

    try:
        beats, build = gather(project, overrides)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    runtime = sum(b.seconds for b in beats)
    too_long = [b for b in budgets if b >= runtime]
    if too_long and not args.quiet:
        print(f"note: {', '.join(f'{b}s' for b in too_long)} is as long as the "
              f"film ({timecode(round(runtime))}); those cuts are the whole thing",
              file=sys.stderr)

    # The coverage sweep is the proposal and the yardstick; the cut is the
    # editor's, validated against the budget and the nesting rule.
    ranked = rank(beats)
    proposal = {budget: cut(beats, ranked, float(budget)) for budget in budgets}
    joins = [j for j in enum_of("transition", "type")] or list(JOIN)
    rules, digest = house.for_args(args, project, "pilot")
    engine = Harness(project.root, "pilot", model=args.model, role=ROLE,
                     house=rules, house_digest=digest,
                     fresh=getattr(args, "fresh", False), verbose=not args.quiet)
    findings: list[str] = list(build.get("beat_notes") or [])
    chosen: dict[int, list[str]] = {}
    verdict: dict = {}
    violations: list[str] = []
    try:
        for attempt in ("select", "select-2"):
            verdict = engine.decide(attempt, select_prompt(
                beats, budgets, proposal, joins, violations), CUT_SCHEMA)
            chosen = {int(c.get("budget", 0)): [str(l) for l in c.get("shots", [])]
                      for c in verdict.get("cuts", []) if isinstance(c, dict)}
            violations = validate(beats, chosen, budgets)
            if not violations:
                break
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if violations:
        findings.append("warning: the editor's selection did not hold ("
                        + "; ".join(violations) + "); the coverage cut was used")
        cuts = proposal
        selection = "coverage"
    else:
        order = {b.label: b.at for b in beats}
        cuts = {budget: cut_from(beats, sorted(chosen[budget], key=order.get),
                                 float(budget)) for budget in budgets}
        for c in verdict.get("cuts", []):
            if isinstance(c, dict) and int(c.get("budget", 0)) in cuts:
                cuts[int(c["budget"])]["why"] = str(c.get("why", ""))
        selection = "editor"

    join = args.transition or (verdict.get("transition")
                               if verdict.get("transition") in joins else None) \
        or next((j for j in JOIN if j in joins), "cut")
    S = sdk()
    try:
        scene = S.read(Path(scene_file).read_bytes())
    except Exception as exc:
        print(f"error: could not read {scene_file}: {exc}", file=sys.stderr)
        return 1
    background = build.get("scene", {}).get("background", "")
    # <segment> arrived with SREP 13 in schema 1.2, and `yt build` writes the
    # lowest version its content needs -- 1.1 for a plain cut. Raise it here,
    # where the newer element is actually used.
    for version in ("auto", "1.2"):
        try:
            scene.version = version
            break
        except Exception:
            continue
    try:
        names = add_outputs(scene, cuts, codec=args.codec, join=join,
                            join_seconds=args.transition_seconds,
                            background=background)
    except Exception as exc:
        print(f"error: the pilot outputs did not fit the schema: {exc}",
              file=sys.stderr)
        return 1

    # Serialise once: the checks read the bytes that will be written, not a
    # second serialisation of the same tree.
    document = scene.to_bytes(layout="pretty")
    findings += [str(f) for f in scene.check()] + duplicate_animations(document)
    print_pilot(idea.title, cuts)
    if verdict.get("notes"):
        print(f"\n  editor's notes: {verdict['notes'][:300]}")
    if findings:
        print(f"\n  the SDK raised {len(findings)} finding(s):")
        for finding in findings[:10]:
            print(f"    {finding}")
    errors = [f for f in findings if f.startswith("error")]
    if errors:
        print(f"\nerror: {len(errors)} check error(s); nothing written",
              file=sys.stderr)
        return 1

    target = Path(args.pilot_out or (
        Path(scene_file).with_name("pilot.scene.xml") if project.standalone
        else project.root / "pilot.scene.xml"))
    target.write_bytes(document)
    manifest = {"idea": {"title": idea.title}, "scene": str(target),
                "join": join, "runtime": round(runtime, 2),
                "outputs": names,
                # Same shape and strings as build.json, so anything reading
                # findings there reads them here without a second format.
                "findings": findings,
                "selection": selection, "notes": verdict.get("notes", ""),
                "engine": engine.report(),
                "usage": spend.from_report(engine.report()),
                "cuts": {str(k): v for k, v in cuts.items()}}
    report = target.with_name("pilot.json")
    report.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    print(f"wrote {target}", file=sys.stderr)
    print(f"wrote {report}", file=sys.stderr)

    if args.render:
        from . import render as render_stage
        for name in names:
            code = render_stage.run(argparse.Namespace(
                **vars(args) | {"scene": str(target), "video_out": None,
                                "quality": None, "start": None, "end": None,
                                "still": None, "frames": None, "strict": False,
                                "parallel": None, "validate": False,
                                "output": name}))
            if code:
                return code
    else:
        print(f"\nrender with: yt render --project {project.root} "
              f"--scene {target.name} --output {names[0]}", file=sys.stderr)
    return 0
