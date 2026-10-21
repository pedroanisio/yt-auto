"""Write the narration's real durations back into the screenplay.

`yt script` budgets each shot from a words-per-minute estimate; `yt voice`
measures what the read actually takes. Until the screenplay carries the
measured numbers, every later stage -- the scene, the pilot's segments, the
animation hours -- is planning against a guess.

    yt retime --title monty --dry-run     # show the changes, write nothing
    yt retime --title monty               # apply them, keeping script.json.bak

Only shots that were recorded change, and only the ones the editor accepts: a
read that ran long may be right, or it may be a take to record again, or a line
to rewrite -- the model decides per shot, against the house standards, and the
screenplay takes the measured length only where the answer was `accept`. A
picture-only shot keeps its planned length: nobody measured it, and its silence
is a decision.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from . import house, workspace
from .build import stale_takes
from .core import read_json, timecode, write_json
from .harness import Harness, HarnessError, brief
from .script import load_script

ACTIONS = ("accept", "rerecord", "rewrite", "keep_planned")

#: Exit code when the editor sent a shot back (re-record or rewrite): the
#: clock is not settled, so nothing downstream should be planned on it yet.
UNSETTLED = 3


def unsettled(report: dict) -> dict[str, list[str]]:
    """The shots the editor sent back, by what has to happen to them."""
    out: dict[str, list[str]] = {}
    for d in (report or {}).get("decisions", []):
        if isinstance(d, dict) and d.get("action") in ("rerecord", "rewrite") and d.get("label"):
            out.setdefault(str(d["action"]), []).append(str(d["label"]))
    return out

ROLE = (
    "You reconcile a screenplay with its recorded narration. For each shot "
    "whose read differs from the plan you decide: `accept` the measured length "
    "(the read is right and the picture adapts), `rerecord` (the read is too "
    "fast, too slow, or mis-stressed for the house pacing; name why), "
    "`rewrite` (the line itself is too long or too short for its shot; the "
    "screenplay stage owns that), or `keep_planned` (the planned length should "
    "stand and the picture will absorb the difference). Reading time for "
    "supers and the shot's visual weight matter more than hitting the estimate."
)

RETIME_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "action": {"type": "string", "enum": list(ACTIONS)},
                    "note": {"type": "string"},
                },
                "required": ["label", "action", "note"],
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["decisions", "notes"],
}


def plan(script: dict, voice: dict) -> tuple[list[dict], dict]:
    """The per-shot changes, and the totals they add up to."""
    takes = {t["label"]: t for t in voice.get("takes", [])}
    changes, before, after = [], 0, 0
    for section in script.get("sections", []):
        for shot in section.get("shots", []):
            planned = int(shot.get("seconds", 0) or 0)
            before += planned
            take = takes.get(shot.get("label"))
            if not take:
                after += planned
                continue
            exact = round(float(take["seconds"]), 2)
            measured = max(1, int(round(exact)))
            after += measured
            if measured != planned:
                # `measured` and `take` make a new recording a new question:
                # two reads that round to the same second are not the same read.
                changes.append({"label": shot["label"], "from": planned,
                                "to": measured, "delta": measured - planned,
                                "measured": exact, "take": str(take.get("file") or ""),
                                "section": section.get("heading", "")})
    return changes, {"before": before, "after": after,
                     "delta": after - before, "shots": len(changes)}


def reconcile_prompt(*, rows: list[dict], stale: list[dict], totals: dict,
                     earlier: dict | None) -> str:
    """The question to the editor. With an earlier round on disk, the
    editor is told what it asked for then and what the new read measures,
    so a shot recorded again at its request is judged as such."""
    parts = [
        "For each shot below, whose recorded read differs from its planned "
        "length, decide the action. Every shot listed must get one decision.",
        brief("Shots that drifted (from = planned s, to = measured s; `measured` "
              "is the exact length of the current take)", rows),
    ]
    if earlier and earlier.get("decisions"):
        was = {c.get("label"): c.get("measured") for c in earlier.get("changes", [])
               if isinstance(c, dict)}
        history = [{"label": d.get("label"), "asked": d.get("action"),
                    "note": str(d.get("note", ""))[:300],
                    "measured_then": was.get(d.get("label"))}
                   for d in earlier["decisions"] if isinstance(d, dict)]
        parts.append(brief(
            "The earlier round: what you asked for each shot, and what its read "
            "measured then. A shot listed above with a different take was recorded "
            "again at the editor's request; judge the new read on its own terms, "
            "and do not send it back for the same reason twice", history))
    parts += [brief("Stale takes (recorded text no longer matches the screenplay)",
                    [r["label"] for r in stale]),
              brief("Totals", totals)]
    return "\n\n".join(parts)


def apply(script: dict, voice: dict, accepted: set[str] | None = None) -> dict:
    """Rewrite durations and starts in place, in shot order.

    With `accepted`, only those shots take their measured length; the rest
    keep the plan and the starts are re-laid around them.
    """
    takes = {t["label"]: t for t in voice.get("takes", [])}
    cursor = 0
    for section in script.get("sections", []):
        total = 0
        for shot in section.get("shots", []):
            take = takes.get(shot.get("label"))
            if take and (accepted is None or shot.get("label") in accepted):
                shot["seconds"] = max(1, int(round(float(take["seconds"]))))
            shot["at"] = cursor
            cursor += int(shot.get("seconds", 0) or 0)
            total += int(shot.get("seconds", 0) or 0)
        section["seconds"] = total
    script["retimed_seconds"] = cursor
    return script


def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "retime", parents=list(parents),
        help="write the measured narration durations into the screenplay",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fresh", action="store_true",
                   help="ask the editor again even if the inputs did not change")
    workspace.add_inputs(p, "script", "voice")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="print the changes and write nothing")
    p.set_defaults(run=run)
    return p


def run(args) -> int:
    idea, project = workspace.resolve(args, inputs=("script", "voice"))
    script_file = project.source("script", workspace.override(args, "script"))
    voice_file = project.source("voice", workspace.override(args, "voice"))
    if not script_file:
        print("error: no screenplay; run `yt script` first, or pass --script FILE",
              file=sys.stderr)
        return 1
    if not voice_file:
        print("error: no narration recorded; run `yt voice` first, or pass "
              "--voice FILE", file=sys.stderr)
        return 1
    script, voice = read_json(script_file), read_json(voice_file)
    if not script or not voice:
        print("error: could not read the screenplay or the narration",
              file=sys.stderr)
        return 1

    sections, _ = load_script(script_file)
    stale = stale_takes(sections, voice)
    if stale:
        print(f"warning: {len(stale)} take(s) recorded with text that no longer "
              "matches the screenplay. Retiming against an old recording "
              "anchors the new words to durations nobody spoke: "
              + ", ".join(r["label"] for r in stale), file=sys.stderr)
        print("         re-record first: yt voice --redo "
              + ",".join(r["label"] for r in stale), file=sys.stderr)

    changes, totals = plan(script, voice)
    print(f"\n{idea.title} — RETIME")
    print(f"{timecode(totals['before'])} planned -> "
          f"{timecode(totals['after'])} measured "
          f"({totals['delta']:+d}s across {totals['shots']} shot(s))")
    section = None
    for row in changes:
        if row["section"] != section:
            section = row["section"]
            print(f"\n  {section}")
        print(f"    {row['label']:<6} {row['from']:>3}s -> {row['to']:>3}s  "
              f"{row['delta']:+d}s")
    if not changes:
        print("\n  nothing to change: " + (
            "a wordless film has no narration to measure" if voice.get("wordless")
            else "the screenplay already matches the narration"))
        return 0

    # Which of these the screenplay should take is the editor's call.
    rules, digest = house.for_args(args, project, "retime")
    engine = Harness(project.root, "retime", model=args.model, role=ROLE,
                     house=rules, house_digest=digest,
                     fresh=getattr(args, "fresh", False), verbose=not args.quiet)
    shots = {s.label: s for sec in sections for s in sec.shots}
    rows = [row | {"narration": shots[row["label"]].narration if row["label"] in shots else "",
                   "on_screen": shots[row["label"]].on_screen if row["label"] in shots else "",
                   "visual": (shots[row["label"]].visual if row["label"] in shots else "")[:200]}
            for row in changes]
    try:
        earlier = read_json(project.root / "retime.json", warn=False)
        verdict = engine.decide("reconcile", reconcile_prompt(
            rows=rows, stale=stale, totals=totals,
            earlier=earlier if isinstance(earlier, dict) else None), RETIME_SCHEMA)
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    decided = {d["label"]: d for d in verdict.get("decisions", [])
               if isinstance(d, dict) and d.get("label")}
    for row in changes:
        if row["label"] not in decided:
            decided[row["label"]] = {"label": row["label"], "action": "keep_planned",
                                     "note": "the editor gave no decision"}
    accepted = {l for l, d in decided.items() if d["action"] == "accept"}
    print("\n  the editor's decisions:")
    for row in changes:
        d = decided[row["label"]]
        print(f"    {row['label']:<6} {d['action']:<13} {d.get('note', '')[:70]}")
    if verdict.get("notes"):
        print(f"\n  notes: {verdict['notes'][:300]}")
    for action, hint in (("rerecord", "yt voice --redo"), ("rewrite", "yt script (revise)")):
        labels = [l for l, d in decided.items() if d["action"] == action]
        if labels:
            print(f"  {action}: {hint} {','.join(labels)}")
    report = {"idea": {"title": idea.title}, "changes": changes, "totals": totals,
              "decisions": list(decided.values()), "accepted": sorted(accepted),
              "notes": verdict.get("notes", ""), "stale": stale,
              "applied": not args.dry_run, "engine": engine.report()}
    if args.dry_run:
        print("\n(--dry-run: the screenplay was not written)", file=sys.stderr)
        return 0

    backup = Path(script_file).with_suffix(".json.bak")
    shutil.copy2(script_file, backup)
    write_json(script_file, apply(script, voice, accepted))
    write_json(project.root / "retime.json", report)
    print(f"wrote {script_file} ({len(accepted)} of {len(changes)} shot(s) retimed)",
          file=sys.stderr)
    print(f"wrote {project.root / 'retime.json'}", file=sys.stderr)
    print(f"the original is kept at {backup}", file=sys.stderr)
    print("run `yt build` again so the scene picks up the new timings",
          file=sys.stderr)
    if unsettled(report):
        # The clock is not settled: `yt run` records again or stops here.
        return UNSETTLED
    return 0
