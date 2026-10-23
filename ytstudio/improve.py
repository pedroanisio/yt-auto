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
   `feedback/closed/<pass>/`. A story finding is the screenplay's even
   though script is expensive; its brief names the narrative rubric key
   each one bears on (`rule`), and one that says the film lacks what the
   plan declares (`PLAN_CLAIMS`) is annotated "the plan declared ..." and
   routed as before, since the reviewer watched the film and the plan is
   the writer's own report.
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
   A pass whose stages all ran but whose QA filed nothing (a render host
   down) is `unjudged`: its outputs and snapshot stay, and the next run
   scores and judges it before anything else.
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

from . import flags, house, narrative, qa, spend, workspace
from .core import read_json, write_json
from .harness import FEEDBACK_DIR, Harness, HarnessError, brief
from .retention import current as measured_retention
# A stage's price is one thing, for `yt improve` and `yt run` alike: it lives
# in spend and is read from here by the names it always had.
from .spend import (DEFAULT_USD, MANIFEST, VFX_PASS_USD,  # noqa: F401
                    _like, _qa_cost, _reported, stage_estimates, total)

#: The stages a finding can be routed to, in pipeline order.
PIPELINE = tuple(stage for stage, _ in workspace.STAGES)
ROUTABLE = PIPELINE
UPSTREAM, EXTERNAL = "upstream-fact", "external"
HUMAN = (UPSTREAM, EXTERNAL)
ROUTES = ROUTABLE + HUMAN

#: Measured by `yt qa` before anyone looks; routed to their stated owner.
MECHANICAL_KINDS = ("penetration", "reads-merged", "flat", "clipped", "frozen", "jump",
                    "off_camera", "proof-mismatch")

#: Expensive, and they change the film's facts: never re-run as a side effect.
EXPENSIVE = ("research", "script")

#: A category may drop this much against the best pass and still be kept.
TOLERANCE = 0.5
#: Briefed and still there after this many passes: the approach is wrong.
SURVIVOR_PASSES = 3

IMPROVE_DIR = "improve"
QA_FAILED = "qa filed no new scored pass"
#: Stages whose own cache replays an attempt: vfx keeps every planning and
#: authoring step by name, so run without --fresh it re-materialises the
#: last run's version instead of authoring a new one.
REPLAYS = ("vfx",)
REPORT_JSON, REPORT_MD = "improve.json", "improve.md"

#: The flags that make a routed stage decide again, and the ones a stage
#: downstream of it runs with (reusing what its inputs did not change).
FORCE = {"research": ["--fresh"], "styles": ["--rich"], "script": ["--polish", "--verify", "--review"],
         "props": ["--fresh"], "keyframe": ["--fresh"], "voice": ["--fresh"],
         "retime": ["--fresh"], "vfx": ["--fresh"],
         "sound": ["--fetch", "--review", "--fresh"], "build": ["--fresh"], "render": []}
REUSE = {"styles": ["--rich"], "script": ["--polish", "--verify", "--review"],
         "sound": ["--fetch"]}

#: What each stage owns, so the router can place a finding whose owner is prose.
DUTIES = {stage: qa.DUTIES[stage] for stage in ROUTABLE}

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
                          "description": "how the next QA pass will tell it is fixed"},
                # The rubric key each story finding bears on (AUTO-99), so the
                # screenplay's brief says which criterion to rewrite against.
                "rules": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "finding": {"type": "string", "description": "the finding's id"},
                        "rule": {"type": "string", "enum": list(narrative.KEYS),
                                 "description": "the narrative rubric key it bears on"}},
                    "required": ["finding", "rule"]}}},
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
    objects it names; a retention finding's, its shot and tag (the same dip
    read twice is one defect). A prose finding has none; the model judges it."""
    if finding.get("source") == "retention":
        return f"retention|{finding.get('shot') or ''}|{finding.get('tag') or ''}"
    kind = finding.get("kind")
    if kind not in MECHANICAL_KINDS:
        return None
    names = sorted({n.rsplit("/", 1)[-1] for n in _TICKED.findall(str(finding.get("problem", "")))})
    return f"{kind}|{finding.get('shot', '')}|{'+'.join(names)}"


def _measured_only_by_an_audience(defect: dict) -> bool:
    """A retention defect: only a new publish and `yt retention` can say
    whether it is gone, never a QA pass (AUTO-101)."""
    return str(defect.get("fingerprint") or "").startswith("retention|")


def collect(card: dict, *, gate: dict | None = None,
            doctor: Iterable[tuple] = (), retention: Iterable[dict] = ()) -> list[dict]:
    """Every finding of one pass, numbered F1..: the mechanical ones, the
    reviewer's, the gate's blocking rows, doctor's fails and warnings for a
    stage, and the published film's retention findings (`yt retention`,
    AUTO-101), each with its tag, the corpus rule it applies (`corpus_rule`,
    kept apart from the rubric key a brief names as `rule`) and its
    hypothesis."""
    rows: list[dict] = []

    def add(source: str, f: dict, **over) -> None:
        row = {"source": source, "kind": f.get("kind"), "shot": f.get("shot"),
               "timestamp": f.get("timestamp"), "severity": f.get("severity", "minor"),
               "owner": str(f.get("owner", "")), "problem": str(f.get("problem", "")),
               "why": str(f.get("why", "")), "root_cause": str(f.get("root_cause", "")),
               "fix": str(f.get("fix", ""))}
        row.update(over)
        row["fingerprint"] = (fingerprint(row) if source in ("mechanical", "retention")
                              else None)
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
    for f in retention or ():
        if isinstance(f, dict):
            add("retention", f, kind=None, tag=f.get("tag"), corpus_rule=f.get("rule"),
                hypothesis=f.get("hypothesis"), section=f.get("section"),
                heading=f.get("heading"), delta_vs_typical=f.get("delta_vs_typical"),
                sanity_bound=list(f.get("sanity_bound") or []))
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
# claims the manifests can check
# --------------------------------------------------------------------------- #

#: A finding that states a checkable fact about the build: the pattern of the
#: claim, the build.json field that settles it, and when that field
#: contradicts it. A reviewer reading frames can misreport what the mix or
#: the timeline holds; a brief built on that sends a stage to fix nothing.
CLAIMS = (
    (re.compile(r"\bno audio\b|\baudio coverage\b[^.]*?\b0\s*/|"
                r"\bno (?:sound|audio) (?:tracks?|nodes?)\b", re.I),
     "audio_tracks", lambda n: n > 0),
    (re.compile(r"\bno narration\b|\bnarration (?:is )?missing\b", re.I),
     "narration_tracks", lambda n: n > 0),
    (re.compile(r"(?<!no )\bplaceholders?\b", re.I), "placeholders", lambda n: n == 0),
)


def _count(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, (list, tuple, dict, set)):
        return len(value)
    return 0


def contradicted_by(finding: dict, build: dict | None) -> str | None:
    """`build.json: <field> = <value>` when the build manifest contradicts a
    claim the finding makes, else None."""
    if not isinstance(build, dict):
        return None
    text = str(finding.get("problem", ""))
    for pattern, field, contradicts in CLAIMS:
        if field in build and pattern.search(text) and contradicts(_count(build[field])):
            value = build[field]
            shown = value if isinstance(value, (int, float)) else f"{_count(value)} item(s)"
            return f"build.json: {field} = {shown}"
    return None


#: A story finding that says the film lacks what the plan declares (AUTO-99):
#: the pattern of the claim and the script.json field the plan holds it in.
#: Unlike CLAIMS this routes nothing away: the reviewer watched the film and
#: the plan is the writer's own report, so a declared payoff the film did not
#: deliver is still a finding. It is annotated, so the writer reads that the
#: line was there and did not land.
PLAN_CLAIMS = (
    # A payoff "missing its picture" is a picture defect, not a missing payoff.
    (re.compile(r"\bno (?:clear |real )?payoff\b|"
                r"\bpayoff (?:line )?(?:is )?(?:missing|absent)\b"
                r"(?! (?:its|a|an|the) (?:picture|image|visual|shot|frame))|"
                r"\bnever pays? off\b|\bpromise is (?:never|not) paid\b", re.I),
     "ending.payoff_line"),
    (re.compile(r"\bno (?:clear |real )?stakes\b|\bstakes (?:are )?(?:missing|absent)\b|"
                r"\bnothing (?:is )?at (?:risk|stake)\b", re.I), "stakes"),
    # Only a claim that a question is left without its answer: a finding that
    # merely names the open question (closed too early, repeated) is not one.
    (re.compile(r"\bno (?:clear )?(?:central )?question\b|"
                r"\b(?:questions?|Q\d+) (?:is|are) (?:left |still )?(?:open|unanswered)\b|"
                r"\b(?:questions?|Q\d+) (?:is|are) (?:never|not) (?:answered|closed)\b|"
                r"\bleaves? (?:\w+ ){0,3}questions? (?:open|unanswered)\b", re.I),
     "questions"),
    (re.compile(r"\bno re-?hooks?\b", re.I), "sections[].rehook"),
    (re.compile(r"\bno (?:clear )?hook\b", re.I), "sections[].shots[function=hook]"),
    (re.compile(r"\bno (?:clear )?promise\b", re.I), "promise"),
)


def _declared(plan: dict, field: str) -> str:
    """What the plan holds under `field`, in a few words, or "" for nothing."""
    if field == "ending.payoff_line":
        ending = plan.get("ending") if isinstance(plan.get("ending"), dict) else {}
        return " ".join(str(ending.get("payoff_line") or "").split())
    if field == "questions":
        return "; ".join(f"{q.get('id', '?')} opened in section {q.get('opened_in', '?')}, "
                         + (f"closed in section {q.get('closed_in')}" if q.get("closed_in")
                            else "left open")
                         for q in plan.get("questions") or [] if isinstance(q, dict))
    if field == "sections[].shots[function=hook]":
        return "; ".join(f"shot {shot.get('label', '?')}: "
                         + " ".join(str(shot.get("narration") or "").split())
                         for sec in plan.get("sections") or [] if isinstance(sec, dict)
                         for shot in sec.get("shots") or []
                         if isinstance(shot, dict) and shot.get("function") == "hook")
    if field == "sections[].rehook":
        return ", ".join(f"section {n} re-opens {s['rehook']}"
                         for n, s in enumerate(plan.get("sections") or [], 1)
                         if isinstance(s, dict) and str(s.get("rehook") or "").strip())
    return " ".join(str(plan.get(field) or "").split())


def plan_declared(finding: dict, plan: dict | None) -> str | None:
    """`the plan declared <field>: "..."` when a finding says the film lacks
    something script.json's plan declares, else None."""
    if not isinstance(plan, dict):
        return None
    text = str(finding.get("problem", ""))
    for pattern, field in PLAN_CLAIMS:
        if pattern.search(text):
            held = _declared(plan, field)
            if held:
                return f"the plan declared {field}: \"{held[:240]}\""
    return None


# --------------------------------------------------------------------------- #
# routing
# --------------------------------------------------------------------------- #

def _shown(f: dict) -> dict:
    return {k: f.get(k) for k in ("id", "source", "kind", "shot", "timestamp", "severity",
                                  "owner", "problem", "why", "root_cause", "fix", "tag",
                                  "corpus_rule", "hypothesis", "section")
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
        "- A finding on the story itself (its hook, stakes, re-hooks, open questions, "
        "payoff, or a section that leaves the viewer nothing) is owned by `script`: "
        "route it there even though it is expensive, since no picture or sound fixes "
        "a story the screenplay does not tell. In that brief's `rules`, name for each "
        "such finding the key of the narrative rubric below it bears on; a finding "
        "that is not about the story gets no rule.\n"
        "- A finding from `retention` is the published film's measured audience curve "
        "(a dip, a late top moment, the Intro, the tail, a spike after the payoff), "
        "placed on the shot that was on screen at that moment. Route it to `script` by "
        "its `corpus_rule`, unless its problem names a picture at that timestamp, in "
        "which case to the stage that owns that picture. Its `hypothesis` is a rubric "
        "key guessed from its `tag`, not a cause: name a rule for it only if it bears "
        "on one.\n"
        "- A finding whose fix is the operator's approval or sign-off (approving a "
        "keyframe, waiving a requirement, choosing between options) is `external`: "
        "running the stage again does not approve anything. The project's state below "
        "says what already exists and what is waiting on the operator; a finding that "
        "asks for something already made and awaiting approval is `external`.\n"
        "- The `owner` written by QA is one of the stages, `upstream-fact` or `external`; "
        "an older scorecard may hold free text naming a tool or a discipline, which you "
        "route by what must change in which stage's source, not by the word.\n"
        "- A finding marked `routed` was measured, or its owner was one of the routes; "
        "it is already routed, so do not route it elsewhere, but do write its brief and "
        "mark later findings that repeat it as `duplicate_of` it.\n"
        "- `duplicate_of` names the earliest finding describing the same defect, or is "
        "empty.\n"
        "- A brief's `must_change` lists concrete changes in that stage's own terms "
        "(what to author, key, light, place, fetch, spot or mix differently), each "
        "traceable to a finding; `check` says what the next QA pass must no longer show "
        "and what it must show instead. The findings themselves are attached to the "
        "brief verbatim by the tooling; do not copy them.",
        brief("Pipeline stages and what each owns",
              {s: DUTIES.get(s, "") for s in ROUTABLE}),
        "The narrative rubric, by key (for a brief's `rules`):\n"
        + narrative.rubric_text("writer"),
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
    out: dict = {"routes": {}, "stages": {}, "human": [], "contradicted": []}
    build = read_json(Path(h.root) / "build.json", warn=False)
    kept = []
    for f in findings:
        why = None if f.get("source") == "mechanical" else contradicted_by(f, build)
        if why:
            out["contradicted"].append(dict(f, contradicted_by=why))
        else:
            kept.append(f)
    findings = kept
    if not findings:
        return out
    # A lookup, not a decision: a measured finding goes to its stated stage, and a
    # reviewer's finding to its owner when QA named one of the routes (qa.OWNERS).
    fixed = {f["id"]: f["owner"] for f in findings
             if (f.get("kind") in MECHANICAL_KINDS and f.get("owner") in ROUTABLE)
             or (f.get("source") == "qa" and f.get("owner") in ROUTES)}
    looked_up = {f["id"] for f in findings if f.get("source") == "qa"}
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
            by = "owner" if f["id"] in looked_up else "mechanical"
            routes[f["id"]] = {"stage": fixed[f["id"]], "by": by,
                               "why": (f"QA named {fixed[f['id']]} as the owner"
                                       if by == "owner" else
                                       f"measured {f['kind']}, owned by {fixed[f['id']]}"),
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
        if r["by"] in ("mechanical", "owner") and stage != r["stage"]:
            r["duplicate_of"] = ""
            continue
        r["stage"] = stage
    briefs = {}
    for b in answer.get("briefs") or []:
        if isinstance(b, dict) and b.get("stage") in ROUTABLE and b["stage"] not in briefs:
            briefs[b["stage"]] = b
    # The rubric key a story finding bears on, as the brief named it. Only a
    # brief's own findings take a key: a row naming a finding routed to
    # another stage is the label of a finding that is not about the story,
    # or not this stage's, and is dropped. The schema's enum is asked again
    # once by the harness and then let through (invariant 3), so a word the
    # rubric still does not have is kept apart under `rule_outside`,
    # recorded rather than passed off as a criterion.
    for stage, b in briefs.items():
        for row in b.get("rules") or []:
            if not isinstance(row, dict) or str(row.get("finding", "")) not in routes:
                continue
            if routes[str(row["finding"])]["stage"] != stage:
                continue
            rule = str(row.get("rule") or "").strip()
            if rule in narrative.KEYS:
                routes[str(row["finding"])]["rule"] = rule
            elif rule:
                routes[str(row["finding"])]["rule_outside"] = rule
    # What the plan declared against a finding that says the film lacks it:
    # an annotation for the writer, decided after the routing and never
    # shown to it, so it changes no route.
    plan_doc = read_json(Path(h.root) / "script.json", warn=False)
    for f in findings:
        declared = plan_declared(f, plan_doc)
        if declared:
            routes[f["id"]]["plan_declared"] = declared
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
        rule = f" [rubric: {f['rule']}]" if f.get("rule") else ""
        if f.get("source") == "retention" and f.get("corpus_rule"):
            rule += (f" [retention: {f['corpus_rule']}, {f.get('tag') or '?'}; "
                     f"hypothesis: {f.get('hypothesis') or '?'}]")
        lines.append(f"- **{f['id']}** {where}{shot}[{f.get('severity', '?')}] ({what}, "
                     f"QA owner: {f.get('owner') or '?'}){rule}: {f['problem']}")
        for label, key in (("why", "why"), ("root cause", "root_cause"), ("fix", "fix")):
            if f.get(key):
                lines.append(f"  - {label}: {f[key]}")
        if f.get("sanity_bound"):
            lines.append("  - sanity_bound (a prior, not yet the channel's measure): "
                         + "; ".join(str(b) for b in f["sanity_bound"]))
        if f.get("plan_declared"):
            lines.append(f"  - {f['plan_declared']}; QA watched the film and still found "
                         "this, so what the plan declares did not land on screen")
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
        rows = [dict(by_id[fid], duplicate_of=routing["routes"][fid].get("duplicate_of", ""),
                     **{k: routing["routes"][fid][k]
                        for k in ("rule", "rule_outside", "plan_declared")
                        if routing["routes"][fid].get(k)})
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


def by_shot(findings: Iterable[dict] | None, survived: dict | None = None) -> bool:
    """Does every finding name the shot it is about? Then the screenplay is
    sent those lines (`yt script --revise feedback`), not re-run from its
    plan: judging 3 sends a dip back to the beat that dipped.

    Not when one of them was briefed before and is still there (`survived`,
    finding id -> briefed passes): the line revision did not fix it, maybe
    because it is about a shot's hold or a beat no single line carries, and
    the brief says to change the approach, so the screenplay runs whole."""
    rows = list(findings or [])
    if any((survived or {}).get(f.get("id")) for f in rows):
        return False
    return bool(rows) and all(str(f.get("shot") or "").strip() for f in rows)


def stage_argvs(stages: list[str], routed: Iterable[str], base: list[str], *,
                quality: str, root: Path,
                sections: Iterable[int] | None = None,
                script_findings: Iterable[dict] | None = None,
                survived: dict | None = None) -> list[list[str]]:
    """Each stage's argv. A routed vfx pass names `sections` (the ones the
    findings fall in) instead of re-authoring the whole film with --fresh; a
    routed script pass whose `script_findings` all name a shot, none of them
    `survived` from an earlier briefed pass, revises those lines from the
    feedback's `.json` twin instead of the whole screenplay (by_shot)."""
    from .cli import stage_argv
    routed = set(routed)
    sound_data = read_json(Path(root) / "sound.json", warn=False) or {}
    out = []
    for stage in stages:
        flags = list((FORCE if stage in routed else REUSE).get(stage, []))
        if stage == "vfx" and stage in routed and sections:
            flags = ["--sections", ",".join(str(n) for n in sorted(set(sections)))]
        if stage == "script" and stage in routed and by_shot(script_findings, survived):
            out.append(stage_argv(stage, base, {stage: ["--revise", "feedback"]}))
            continue
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


def _replay(argv: list[str]) -> list[str]:
    """The argv of a stage run again from its own cache: no --fresh, and no
    --sections with its value."""
    out, skip = [], False
    for x in argv:
        if skip:
            skip = False
        elif x == "--sections":
            skip = True
        elif x != "--fresh":
            out.append(x)
    return out


def routed_sections(root: Path, findings: Iterable[dict]) -> list[int] | None:
    """The vfx sections the findings fall in, by shot label then by timestamp
    against each section's worlds, or None when the whole film is to be
    authored again: a finding no section holds (a film-wide one), no
    authoring record, or every section hit."""
    authored = [r for r in (read_json(Path(root) / "vfx.json", warn=False) or {}).get(
        "authored") or [] if isinstance(r, dict) and isinstance(r.get("n"), int)]
    findings = list(findings)
    if not authored or not findings:
        return None
    hit: set[int] = set()
    for f in findings:
        found = None
        shot, t = f.get("shot"), f.get("timestamp")
        for rec in authored:
            if shot and any(str(shot) in [str(x) for x in w.get("shots") or []]
                            for w in rec.get("worlds") or []):
                found = rec["n"]
                break
        if found is None and isinstance(t, (int, float)):
            for rec in authored:
                if any(float(w.get("start", 0)) <= t
                       < float(w.get("start", 0)) + float(w.get("seconds", 0))
                       for w in rec.get("worlds") or []):
                    found = rec["n"]
                    break
        if found is None:
            return None
        hit.add(found)
    return None if hit >= {r["n"] for r in authored} else sorted(hit)


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
    return spend.cost_in(data)


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


def spread(card: dict, key: str) -> float:
    """The samples' spread (max - min) a scorecard recorded for `key`, a
    category or `overall` (AUTO-107); 0 for a card scored once."""
    try:
        return max(0.0, float((card.get("spread") or {}).get(key) or 0))
    except (TypeError, ValueError, AttributeError):
        return 0.0


def judge(best: dict, new: dict, *, tolerance: float = TOLERANCE) -> dict:
    """Keep only a measured improvement: the overall up, no category down by
    more than the tolerance, no new blocking mechanical finding. A change
    smaller than the samples' spread (the larger of the two passes',
    AUTO-107) is no change: a gain inside it is not kept, and a drop inside
    it is no regression. A pass QA could not score (`not_evidenced`) is
    never kept."""
    if new.get("verdict") == qa.NOT_EVIDENCED:
        return {"keep": False, "deltas": {}, "overall_delta": 0.0,
                "against": best.get("pass"),
                "reasons": [f"{new.get('pass')} is not evidenced: fewer than two of its "
                            "scoring samples answered, so nothing is measured"]}
    diff = qa.compare(best, new)
    reasons = []
    noise = max(spread(best, "overall"), spread(new, "overall"))
    if diff["overall_delta"] <= 0:
        reasons.append(f"overall {new.get('overall')} did not improve on "
                       f"{best.get('overall')} ({diff['overall_delta']:+.2f})")
    elif diff["overall_delta"] < noise - 1e-9:
        reasons.append(f"overall {diff['overall_delta']:+.2f} is inside the samples' "
                       f"spread of {noise:.2f}: no change")
    for key, delta in diff["deltas"].items():
        inside = -delta < max(spread(best, key), spread(new, key)) - 1e-9
        if delta < -tolerance - 1e-9 and not inside:
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
                 resume: bool = False, rejudge: bool = False,
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
        self.resume = resume
        self.rejudge = rejudge
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
            self.spent_stages += _cost_in(card) or 0.0
        if card and card.get("verdict") == qa.NOT_EVIDENCED:
            # Fewer than two scoring samples answered (AUTO-107): what the
            # pass made stays, unjudged, and the next run scores it again.
            self._log(f"[improve] {after.name} is not evidenced: fewer than two of its "
                      "scoring samples answered")
            return None
        return card

    def _stale(self, pdir: Path) -> bool:
        """No scorecard, one older than the video it should judge, or one QA
        could not score (`not_evidenced`): the pass is scored again first."""
        card = pdir / "scorecard.json"
        if not card.is_file():
            return True
        if (read_json(card, warn=False) or {}).get("verdict") == qa.NOT_EVIDENCED:
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

    def _resume(self) -> dict | None:
        """Judge the last pass of an earlier run whose QA failed: score it
        now if no pass has scored it, keep it or restore its snapshot."""
        earlier = read_json(self.root / REPORT_JSON, warn=False) or {}
        passes = earlier.get("passes") or []
        if not passes or passes[-1].get("decision") != "unjudged":
            return None
        entry = dict(passes[-1], resumed=True)
        before = self._card(self.root / qa.QA_DIR / str(entry.get("best_before")))
        if before is None:
            return None
        latest = qa.latest_pass(self.root)
        new = None
        if (latest is not None and latest.name != before.get("pass")
                and not self._stale(latest)
                and int(latest.name.split("-")[1]) > int(str(before["pass"]).split("-")[1])):
            new = self._card(latest)
        if new is None:
            self._log(f"[improve] {entry['name']} was never judged; scoring it first")
            new = self._qa()
        if new is None:
            self.say(f"error: {entry['name']} still has no QA pass; the render host may "
                     "be down")
            return None
        self.spent_stages += float(earlier.get("spent_usd") or 0)
        return self._settle(entry, before, new)

    def _gate(self, entry: dict, pdir: Path, stamps: dict[str, int]) -> None:
        """Run the gate on the new video. A gate that wrote nothing (it can
        refuse to start, e.g. with no captions to check) leaves the old
        video's gate.json, whose blocking rows QA would read as this
        video's; it is set aside, so the pass is judged on its own evidence.
        A revert puts it back from the snapshot."""
        entry["argv"].append(["gate", *self.base])
        entry["exit"]["gate"] = self._run(["gate", *self.base])
        gate = self.root / "gate.json"
        if gate.is_file() and stamps.get("gate.json") == gate.stat().st_mtime_ns:
            pdir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(gate), str(pdir / "stale-gate.json"))
            entry["gate"] = ("did not write a gate.json for this video; the earlier one "
                             f"was set aside as {IMPROVE_DIR}/{pdir.name}/stale-gate.json")
            self._log(f"[improve] {entry['gate']}")

    def _settle(self, entry: dict, before: dict, new: dict) -> dict:
        """Keep or revert a pass judged outside the loop, close its notes."""
        verdict = judge(before, new)
        entry.update(qa_pass=new["pass"], overall_after=new.get("overall"),
                     scores_after=new.get("scores"), verdict_after=new.get("verdict"),
                     deltas=verdict["deltas"], overall_delta=verdict["overall_delta"],
                     reasons=verdict["reasons"],
                     decision="keep" if verdict["keep"] else "revert")
        if not verdict["keep"]:
            pdir = self.root / IMPROVE_DIR / entry["name"]
            snapshot(self.root, list(entry.get("stages") or []) + ["gate", "qa"],
                     pdir / "attempt")
            entry["attempt"] = str(pdir / "attempt")
            restore(self.root, Path(entry.get("snapshot") or pdir / "snapshot"))
        close_feedback(self.root, entry["name"])
        self._save_pass(self.root / IMPROVE_DIR / entry["name"], entry)
        self._log(f"[improve] {entry['name']}: judged, "
                  f"{'kept' if verdict['keep'] else 'reverted'}")
        return {"entry": entry, "start": before, "best": new if verdict["keep"] else before}

    def _rejudge(self) -> dict | None:
        """`--rejudge`: judge the last pass again on clean evidence, when its
        judgement could not be trusted (a stale input, a host that failed).
        The project as it stands is snapshotted, the attempt's files are put
        back over it, the video is rendered again from the attempt's scene,
        the gate and QA run (an old gate.json is set aside if the gate
        writes none), and the same rule keeps the pass or restores the
        snapshot. Nothing is authored or decided again upstream."""
        earlier = read_json(self.root / REPORT_JSON, warn=False) or {}
        passes = earlier.get("passes") or []
        last = passes[-1] if passes else {}
        name = last.get("name", "")
        pdir = self.root / IMPROVE_DIR / name
        attempt = pdir / "attempt"
        before = self._card(self.root / qa.QA_DIR / str(last.get("best_before")))
        if (last.get("decision") not in ("revert", "unjudged", "keep")
                or not (attempt / "snapshot.json").is_file() or before is None):
            self.say(f"error: --rejudge needs a {REPORT_JSON} whose last pass was judged "
                     f"and kept its files under {IMPROVE_DIR}/<pass>/attempt/")
            return None
        self.spent_stages += float(earlier.get("spent_usd") or 0)
        stages = list(last.get("stages") or [])
        render = stage_argvs(["render"], set(), self.base, quality=self.quality,
                             root=self.root)[0]
        guess = estimate(["render"], None)
        if guess > self.budget - self.spent:
            self.say(f"error: re-judging {name} is estimated at ${guess:.2f}; "
                     f"${self.budget - self.spent:.2f} is left")
            return None
        fresh = pdir / "snapshot-rejudge"
        snapshot(self.root, stages + ["gate", "qa"], fresh)
        restore(self.root, attempt)
        entry = dict(last, rejudged=True, exit={}, argv=[], snapshot=str(fresh),
                     previous_judgement={k: last.get(k) for k in (
                         "decision", "qa_pass", "overall_after", "scores_after", "reasons",
                         "gate")})
        self._log(f"[improve] re-judging {name}: rendering its attempt again")
        stamps = _stamps(self.root)
        entry["argv"].append(render)
        code = entry["exit"]["render"] = self._run(render)
        if code:
            restore(self.root, fresh)
            entry.update(decision="failed", reasons=[f"render exited {code}"])
            self._save_pass(pdir, entry)
            return {"entry": entry, "start": before, "best": before,
                    "stop": {"reason": "stage-failed", "detail": f"render exited {code}; "
                             "the state before the re-judge was restored"}}
        self._gate(entry, pdir, stamps)
        entry["argv"].append(["qa", *self.base])
        new = self._qa()
        cost = stage_cost(self.root, "gate", stamps) + (_cost_in(new) or 0.0)
        self.spent_stages += stage_cost(self.root, "gate", stamps)
        entry["cost_usd"] = round(float(last.get("cost_usd") or 0) + cost, 4)
        if new is None:
            entry.update(decision="unjudged", reasons=[QA_FAILED])
            self._save_pass(pdir, entry)
            return {"entry": entry, "start": before, "best": before,
                    "stop": {"reason": "qa-failed", "detail": f"{QA_FAILED}; run "
                             "`yt improve --rejudge` again"}}
        return self._settle(entry, before, new)

    def _rerun_failed(self) -> dict | None:
        """`--resume`: run the last pass of an earlier run again from the stage
        that failed, after the cause was fixed. The attempt's own files are
        put back when they were kept; otherwise the stages before the failure
        run again, a stage that replays from its own cache (`REPLAYS`)
        without --fresh, so nothing expensive is authored twice. The earlier
        spend counts against the budget."""
        earlier = read_json(self.root / REPORT_JSON, warn=False) or {}
        passes = earlier.get("passes") or []
        if not passes or passes[-1].get("decision") != "failed":
            self.say(f"error: --resume needs a {REPORT_JSON} whose last pass failed")
            return None
        last = passes[-1]
        stages = list(last.get("stages") or [])
        failed_at = next((st for st in stages if (last.get("exit") or {}).get(st)), None)
        before = self._card(self.root / qa.QA_DIR / str(last.get("best_before")))
        if failed_at is None or before is None:
            self.say("error: the failed pass names no failing stage or no pass it started from")
            return None
        self.spent_stages += float(earlier.get("spent_usd") or 0)
        name = last["name"]
        pdir = self.root / IMPROVE_DIR / name
        argvs = stage_argvs(stages, set(last.get("routed") or {}), self.base,
                            quality=self.quality, root=self.root,
                            sections=last.get("sections"),
                            script_findings=((last.get("routed") or {}).get("script")
                                             or {}).get("findings"),
                            survived=last.get("survived"))
        start = stages.index(failed_at)
        attempt = pdir / "attempt"
        if (attempt / "snapshot.json").is_file():
            restore(self.root, attempt)
            steps = list(zip(stages, argvs))[start:]
        else:
            steps = [(st, _replay(argv) if n < start and st in REPLAYS else argv)
                     for n, (st, argv) in enumerate(zip(stages, argvs))]
        paid = [st for st, argv in steps if not (
            st in REPLAYS and "--fresh" not in argv and "--sections" not in argv)]
        guess = estimate(paid, None)
        if paid:
            guess = total(stage_estimates(self.root, paid, last.get("sections"),
                                          extras=dict(steps)))
        remaining = self.budget - self.spent
        if guess > remaining:
            self.say(f"error: resuming {name} is estimated at ${guess:.2f} and "
                     f"${remaining:.2f} of ${self.budget:.2f} is left")
            return None
        entry = dict(last, resumed=True, exit={}, argv=[], previous_attempt=last,
                     estimate_usd=guess)
        self._log(f"[improve] resuming {name} from {steps[0][0]} ({failed_at} failed before)")
        stamps = _stamps(self.root)
        failed = None
        for stage, argv in steps:
            entry["argv"].append(argv)
            code = self._run(argv)
            entry["exit"][stage] = code
            if code:
                failed = f"{stage} exited {code}"
                break
        new = None
        if failed is None:
            self._gate(entry, pdir, stamps)
            entry["argv"].append(["qa", *self.base])
            new = self._qa()
        cost = sum(stage_cost(self.root, st, stamps) for st, _ in steps) + stage_cost(
            self.root, "gate", stamps)
        self.spent_stages += cost
        cost += _cost_in(new) or 0.0
        entry["cost_usd"] = round(float(last.get("cost_usd") or 0) + cost, 4)
        snap = pdir / "snapshot"
        if failed:
            snapshot(self.root, stages + ["gate", "qa"], attempt)
            restore(self.root, snap)
            entry.update(decision="failed", reasons=[failed], attempt=str(attempt))
            self._save_pass(pdir, entry)
            return {"entry": entry, "start": before, "best": before,
                    "stop": {"reason": "stage-failed", "detail": f"{failed} again; the "
                             "snapshot was restored and the notes left open"}}
        if new is None:
            entry.update(decision="unjudged", reasons=[QA_FAILED], snapshot=str(snap))
            self._save_pass(pdir, entry)
            return {"entry": entry, "start": before, "best": before,
                    "stop": {"reason": "qa-failed", "detail": f"{QA_FAILED}; run "
                             "`yt improve` again to judge it"}}
        return self._settle(entry, before, new)

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
        # The published film's curve, while the film is still the cut it measured.
        return collect(card, gate=gate, doctor=rows, retention=measured_retention(self.root))

    # -- one pass -------------------------------------------------------------

    def _next_name(self) -> str:
        base = self.root / IMPROVE_DIR
        taken = [int(m.group(1)) for p in (base.iterdir() if base.is_dir() else [])
                 if (m := re.match(r"^pass-(\d+)$", p.name))]
        return f"pass-{(max(taken) + 1 if taken else 1):02d}"

    def _carry(self, record: dict, resumed: dict) -> None:
        """Continue the earlier run's record instead of starting one: its passes
        (the one being resumed or re-judged replaced by its new entry), its
        defects with their survivor counts, the findings it left to a human and
        the start it measured from. --max-passes, the survivor stop and
        improve.md then speak for the whole history."""
        earlier = read_json(self.root / REPORT_JSON, warn=False) or {}
        entry = resumed["entry"]
        record["passes"] = [p for p in earlier.get("passes") or []
                            if isinstance(p, dict) and p.get("name") != entry.get("name")
                            ] + [entry]
        for did, d in (earlier.get("defects") or {}).items():
            self.defects.setdefault(did, d)
        record["human"] = list(earlier.get("human") or [])
        record["contradicted"] = list(earlier.get("contradicted") or [])
        record["start"] = earlier.get("start") or _summary(resumed["start"])

    def run(self) -> dict:
        started = time.strftime("%Y-%m-%dT%H:%M:%S")
        record: dict = {"idea": {"title": self.idea.title}, "project": str(self.root),
                        "started": started, "dry_run": self.dry_run,
                        "max_passes": self.max_passes, "budget_usd": self.budget,
                        "target": self.target, "quality": self.quality, "passes": [],
                        "human": [], "contradicted": [], "start": None, "best": None,
                        "spent_usd": 0.0,
                        "stop": None}
        if self.resume or self.rejudge:
            resumed = self._rejudge() if self.rejudge else self._rerun_failed()
            if resumed is None:
                record["stop"] = {"reason": "resume-failed",
                                  "detail": "nothing to resume; see the error above"}
                return record
            self._carry(record, resumed)
            if resumed.get("stop"):
                record.update(best=_summary(resumed["best"]), stop=resumed["stop"])
                record["defects"] = self.defects
                return self._finish(record)
        else:
            resumed = None if self.dry_run else self._resume()
            if resumed:
                self._carry(record, resumed)
        card = resumed["best"] if resumed else self._evidence()
        if card is None:
            record["stop"] = {"reason": "no-evidence",
                              "detail": "no QA pass could be read or scored"}
            return self._finish(record)
        record["start"] = record["start"] or _summary(resumed["start"] if resumed else card)
        best = evidence = card
        attempts: dict[str, list[str]] = {}
        last_cost: float | None = None
        findings = self._evidence_findings(card)
        self._assign(card["pass"], findings, set(
            (resumed["entry"].get("briefed_defects") or {}).values()) if resumed else set())
        if resumed:
            record["best"] = _summary(best)
            last_cost = resumed["entry"].get("cost_usd") or None

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
            seen = {c["problem"] for c in record["contradicted"]}
            record["contradicted"] += [c for c in routing["contradicted"]
                                       if c["problem"] not in seen]
            stages = plan(routing["stages"], self.root)
            by_id = {f["id"]: f for f in findings}
            sections = routed_sections(self.root, [
                by_id[fid] for fid in (routing["stages"].get("vfx") or {}).get("findings", [])
                if fid in by_id]) if "vfx" in routing["stages"] else None
            assigned = self.assigned.get(evidence["pass"], {})
            survived = {fid: self.defects[did]["survived"] for fid, did in assigned.items()
                        if self.defects[did]["survived"]}
            argvs = stage_argvs(stages, routing["stages"], self.base, quality=self.quality,
                                root=self.root, sections=sections, script_findings=[
                                    by_id[fid] for fid in (routing["stages"].get("script")
                                                           or {}).get("findings", [])
                                    if fid in by_id], survived=survived)
            rows = stage_estimates(self.root, stages, sections,
                                   extras=dict(zip(stages, argvs)))
            guess = total(rows)
            if last_cost and not any(r["basis"] != "assumed" for r in rows):
                guess = estimate(stages, last_cost)
            name = self._next_name()

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
                                  "sections": sections,
                                  "argv": argvs + [["gate", *self.base], ["qa", *self.base]],
                                  "estimates": rows, "estimate_usd": guess}
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
                     "stages": stages, "sections": sections, "argv": [], "exit": {},
                     "survived": survived, "estimate_usd": guess, "estimates": rows,
                     "overall_before": best.get("overall"), "scores_before": best.get("scores"),
                     "best_before": best.get("pass")}
            record["passes"].append(entry)
            self._log(f"\n[improve] {name}: {', '.join(stages)} from {evidence['pass']} "
                      f"(estimated ${guess:.2f})")
            failed = None
            rejected = ""
            for stage, argv in zip(stages, argvs):
                entry["argv"].append(argv)
                code = self._run(argv)
                entry["exit"][stage] = code
                if code:
                    failed = f"{stage} exited {code}"
                    break
                rejected = story_rejected(self.root, stage, argv)
                if rejected:
                    # AUTO-93: the screenplay the pass wrote was read and
                    # rejected; voicing and authoring it would spend the
                    # pass on a story the reviewer would not voice.
                    failed = rejected
                    break
            new = None
            if failed is None:
                self._gate(entry, pdir, before)
                entry["argv"].append(["qa", *self.base])
                new = self._qa()
                if new is None:
                    failed = QA_FAILED
            cost = sum(stage_cost(self.root, s, before) for s in stages + ["gate"])
            self.spent_stages += cost
            cost += _cost_in(new) or 0.0
            entry["cost_usd"] = round(cost, 4)
            last_cost = cost or None

            if failed == QA_FAILED:
                # Every stage ran; only the judging failed (a render host
                # down, say). The pass is paid for: keep its outputs and the
                # snapshot, and let the next run score and judge it.
                entry.update(decision="unjudged", reasons=[failed], snapshot=str(snap))
                record["stop"] = {"reason": "qa-failed", "detail": f"{failed}; the "
                                  "pass's outputs are in place and its snapshot is kept: "
                                  "run `yt improve` again (it scores and judges this pass "
                                  "first) or `yt qa`"}
                self._save_pass(pdir, entry)
                break
            if failed:
                # What the attempt wrote is kept beside the snapshot, for the
                # diagnosis; the project goes back to the best pass.
                snapshot(self.root, stages + ["gate", "qa"], pdir / "attempt")
                restore(self.root, snap)
                entry.update(decision="stopped" if rejected else "failed", reasons=[failed],
                             attempt=str(pdir / "attempt"))
                record["stop"] = {"reason": "story-review" if rejected else "stage-failed",
                                  "detail": f"{failed}; the "
                                  "snapshot was restored (the attempt's files are in "
                                  f"{IMPROVE_DIR}/{name}/attempt/) and the notes left open"}
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
            # A dip the audience showed is gone from the new pass only because
            # its curve describes the published cut, not this one: nobody
            # measured it again, so it is neither fixed nor survived.
            unmeasured = {did for did in briefed if did not in present
                          and _measured_only_by_an_audience(self.defects[did])}
            for did in briefed - unmeasured:
                d = self.defects[did]
                d["survived"] = d["survived"] + 1 if did in present else 0
            entry["survivors"] = {did: self.defects[did]["survived"] for did in sorted(briefed)
                                  if self.defects[did]["survived"]}
            entry["fixed"] = sorted(did for did in briefed - unmeasured if did not in present)
            entry["unmeasured"] = sorted(unmeasured)
            entry["briefed_defects"] = {fid: assigned[fid] for e in routing["stages"].values()
                                        for fid in e["findings"] if fid in assigned}
            if verdict["keep"]:
                best = evidence = new
                attempts = {}
                self._log(f"[improve] {name}: kept ({new.get('overall')} against "
                          f"{entry['overall_before']})")
            else:
                snapshot(self.root, stages + ["gate", "qa"], pdir / "attempt")
                entry["attempt"] = str(pdir / "attempt")
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
        routes = routing.get("routes") or {}
        return {stage: {"findings": [{**{k: by_id[fid].get(k) for k in
                                         ("id", "source", "kind", "shot", "timestamp",
                                          "severity", "problem", "root_cause", "tag",
                                          "corpus_rule")},
                                      **{k: routes[fid][k] for k in ("rule", "plan_declared")
                                         if (routes.get(fid) or {}).get(k)}}
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
        self.say("\n  estimate per stage (from the project's own reports where it has them):")
        for r in p.get("estimates") or []:
            self.say(f"    {r['stage']:<10} {'$' + format(r['usd'], '.2f'):>8}  "
                     f"{r['basis']:<9} {r['source']}")
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

def story_rejected(root: Path, stage: str, argv: list[str]) -> str:
    """Why a pass stops after its script ran with `--review`: the story
    review rejected the screenplay it wrote (AUTO-93). The stage exits 0 on
    a reject unless it was given --gate, which the loop does not pass; the
    loop reads the verdict instead, so a pass is not spent voicing and
    authoring a story its reviewer would not voice. Empty otherwise."""
    if stage != "script" or "--review" not in argv:
        return ""
    review = (read_json(Path(root) / "script.json", warn=False) or {}).get("review")
    if not isinstance(review, dict) or review.get("verdict") != "reject":
        return ""
    weak = ", ".join(f"{k} {v}" for k, v in (review.get("grades") or {}).items()
                     if v in ("Poor", "Fair"))
    return (f"the script's story review: reject ({weak or 'see script.md'}); the "
            "screenplay this pass wrote was not voiced")


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
        unmeasured = set(p.get("unmeasured") or [])
        defect_of = p.get("briefed_defects") or {}
        after_fps = None
        if "fixed" not in p and p.get("qa_pass"):
            # A pass judged outside the loop tracked no defects: measure the
            # mechanical ones against the new pass's own mechanical findings.
            after = read_json(root / qa.QA_DIR / str(p["qa_pass"]) / "scorecard.json",
                              warn=False) or {}
            after_fps = {fingerprint(m) for m in after.get("mechanical") or []}
        for stage, e in (p.get("routed") or {}).items():
            correction = "; ".join(e.get("must_change") or []) or "-"
            for f in e.get("findings") or []:
                did = defect_of.get(f["id"])
                audience = f.get("source") == "retention"
                fp = fingerprint(f) if after_fps is not None and not audience else None
                if fp is not None:
                    did = f["id"]
                    if fp not in after_fps:
                        fixed.add(did)
                if audience and (did in unmeasured or after_fps is not None):
                    result = (f"not re-measured: {p.get('qa_pass')} scored a cut no audience "
                              "has watched; run `yt retention` once the new cut is published")
                elif after_fps is not None and fp is None:
                    result = (f"not re-checked one by one ({p.get('qa_pass')} was judged "
                              "after a resume); see its findings")
                elif p.get("decision") == "keep" and did in fixed:
                    result = f"verified: not reported by {p.get('qa_pass')}"
                elif did in fixed:
                    result = f"gone in {p.get('qa_pass')}, but the pass was reverted"
                else:
                    result = f"still reported by {p.get('qa_pass', 'the next pass')}"
                when = stamp(f.get("timestamp")) if f.get("timestamp") is not None else "-"
                shot = f" shot {f['shot']}" if f.get("shot") else ""
                kind = f" [{f['kind']}]" if f.get("kind") else ""
                kind += f" [rubric: {f['rule']}]" if f.get("rule") else ""
                if audience and f.get("corpus_rule"):
                    kind += f" [retention: {f['corpus_rule']}, {f.get('tag') or '?'}]"
                kind += f" ({f['plan_declared']})" if f.get("plan_declared") else ""
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
    if record.get("contradicted"):
        lines += ["", "### Contradicted by the manifests (not routed)", ""]
        lines += [f"- {stamp(c.get('timestamp'))} [{c.get('source')}] {c['problem']} -- "
                  f"contradicted by {c['contradicted_by']}" for c in record["contradicted"]]
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
    p.add_argument("--rejudge", action="store_true",
                   help="judge the last pass again on clean evidence: put its kept "
                        "files back, render, gate and score again, keep or revert")
    p.add_argument("--resume", action="store_true",
                   help="run the last failed pass again from the stage that failed, "
                        "once its cause is fixed, replaying what came before it")
    p.set_defaults(run=run)
    return p


def run(args) -> int:
    # The stages the loop reruns review with vfx.REVIEWER_MODEL (script's
    # plan with --review, keyframe, vfx): a model never grades its own work,
    # so an author of that family is refused here, before any stage spends,
    # as `yt run` refuses it, not partway through a pass.
    from .vfx import REVIEWER_MODEL
    if own := flags.own_reviewer(args.model, REVIEWER_MODEL):
        print(own, file=sys.stderr)
        return 2
    idea, project = workspace.resolve(args)
    rules, digest = house.for_args(args, project, "improve")
    improver = Improver(project, idea, model=args.model,
                        effort=getattr(args, "effort", None),
                        fallback_model=getattr(args, "fallback_model", None),
                        max_passes=args.max_passes, budget_usd=args.budget_usd,
                        target=args.target, dry_run=args.dry_run, from_pass=args.from_pass,
                        resume=args.resume, rejudge=args.rejudge,
                        quality=args.quality, quiet=args.quiet, house_rules=rules,
                        house_digest=digest)
    try:
        record = improver.run()
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0 if record["stop"]["reason"] in ("ready", "dry-run") else 1
