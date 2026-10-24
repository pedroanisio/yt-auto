"""AUTO-93: the story is reviewed before voice is paid for.

The first story judgement used to be `yt qa` on the rendered film. With
`yt script --review` the screenplay is now read twice after it is drafted
and polished, before the fact-check and before any take is recorded:

- three samples of the story review (`review-r1-s1..s3`), each with the
  rubric's levels in its own order (judging 8), graded absolutely
  (judging 7); the grade per criterion and the verdict are the median of
  the valid samples (judging 11), and fewer than two valid samples is
  `not_evidenced`, never a silent pass;
- a cold reader (`cold-read`) that sees the narration only, no research,
  plan or house (judging 14), whose restated premise is matched against the
  train (`cold-read-match`).

A consider's shot flags go to `revise-story-N`, a section flag to
`redraft-NN`, a plan flag is left unresolved with the command that fixes
it, and the revision is read again with `previous` (judging 12). A reject
is an `error:` finding; it stops voice only under `--gate` (judging 9), and
every verdict is appended to work/review-log.jsonl for the calibration set.

No model is called: `Screenwriter._ask` is a fake keyed on the schema's
top-level keys ('grades', 'premise', 'fixes', 'verdict'), or `harness.ENGINE`
is a fake engine.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_engine import ClaudeError  # noqa: E402
from ytstudio import cli, doctor, harness, narrative, script, workspace  # noqa: E402

from tests.test_harness_decisions import FakeEngine  # noqa: E402
from tests.test_script_plan import PLAN, idea, shots_for  # noqa: E402


def kind(schema):
    """What a schema asks for, by its top-level keys."""
    props = schema["properties"]
    if "grades" in props:
        return "review"
    if "premise" in props:
        return "cold-read"
    if "answered" in props:
        return "answers"
    if "verdict" in props and "problems" in props:
        return "plan-review"
    if "questions" in props:
        return "plan" if "logline" in props else "plan-fields"
    if "shots" in props:
        return "shots"
    for name in ("fixes", "flags", "edits"):
        if name in props:
            return name
    if "verdict" in props:
        return "match"
    if "items" in props:
        return "quiz"
    if "answers" in props:
        return "quiz-answers"
    return ""


def sample(verdict="recommend", grades=None, flags=(), previous=None, trigger=None):
    g = {k: "Good" for k in narrative.KEYS}
    g.update(grades or {})
    out = {"logline": "a bridge that should have stood", "synopsis": "it falls; why",
           "grades": g, "verdict": verdict, "flags": [dict(f) for f in flags],
           "first_minute": {"promise_match": "yes", "question_and_stakes": "why it fell"}}
    if trigger is not None:
        # AUTO-136: the curiosity trigger the sample heard in the opening
        out["first_minute"].update(trigger=trigger, gap="" if trigger == "not_heard"
                                   else "why the bridge fell")
    if previous:
        out["previous"] = {"verdict": previous, "findings": []}
    return out


def line_of(n):
    """What the default fake writes on shot n: two shots a section, `line s.i`."""
    return f"line {(n + 1) // 2}.{2 - n % 2}"


def flag(n=1, section=1, criterion="hook", severity="important",
         problem="the hook lists its conclusions", fix="open on the empty river",
         quote=None, conflicts_with=None):
    """A story-review flag. It quotes the default fake's own words (AUTO-112):
    shot n's line, a section's first line, nothing for the whole script."""
    if quote is None:
        quote = line_of(n) if n else f"line {section}.1" if section else ""
    out = {"n": n, "section": section, "criterion": criterion, "problem": problem,
           "severity": severity, "fix": fix, "quote": quote}
    if conflicts_with is not None:
        out["conflicts_with"] = dict(conflicts_with)
    return out


#: A shot as the story prompt lists it: `n. [label time seconds] narration`.
_LISTED = re.compile(r"^(\d+)\. \[[^\]]*\] (.*)$", re.M)


def requoted(prompt, flags):
    """`flags` with each shot flag quoting its shot's line as `prompt` shows
    it now: a later round reads the lines the revision changed."""
    said = {int(m.group(1)): m.group(2).split("  [")[0] for m in _LISTED.finditer(prompt)}
    return [dict(f, quote=said.get(f["n"], f["quote"])) if f["n"] else dict(f)
            for f in flags]


COLD = {"premise": "A bridge fell because of one bolt nobody could see.",
        "unexplained_terms": ["gusset"],
        "claims": [{"claim": "one bolt held four lanes", "why_care": "you drive on one"}]}
ANSWERS = {"answered": [{"question_id": "Q1", "shot_index": 5}]}

#: A keyed viewer quiz (AUTO-115): four items, two from the plan's
#: questions and two from the research. Its wording is its own, never a
#: plan question's text, so a test can tell the plan's words from the quiz's.
QUIZ = {"items": [
    {"id": "K1", "question": "What held up the four lanes?",
     "options": ["a cable", "one bolt", "a pier", "the deck"], "answer": "B", "from": "Q1"},
    {"id": "K2", "question": "What kept the flaw from the inspectors?",
     "options": ["rust", "paint", "water", "darkness"], "answer": "B", "from": "Q2"},
    {"id": "K3", "question": "When does the twin bridge open?",
     "options": ["in a year", "in a week", "in a month", "never"], "answer": "C",
     "from": "QUIZ-FROM-CLAIM-ONE"},
    {"id": "K4", "question": "How old was the bridge?",
     "options": ["10 years", "40 years", "80 years", "120 years"], "answer": "B",
     "from": "QUIZ-FROM-CLAIM-TWO"},
]}
#: Before the narration: one right (K4), one wrong, two unsure.
BEFORE = {"answers": [{"id": "K1", "choice": "unsure"}, {"id": "K2", "choice": "A"},
                      {"id": "K3", "choice": "unsure"}, {"id": "K4", "choice": "B"}]}
#: After it: three right, K3 wrong (A for C).
AFTER = {"answers": [{"id": "K1", "choice": "B"}, {"id": "K2", "choice": "B"},
                     {"id": "K3", "choice": "A"}, {"id": "K4", "choice": "B"}]}

ACCEPT = {"verdict": "accept", "train_ok": True, "reorder_survives": False,
          "sections_advancing_none": [], "payoff_concrete": True,
          "questions_unanswered": [], "problems": []}


def writer(**kw):
    kw.setdefault("research", "")
    kw.setdefault("reviewer_model", "opus")
    return script.Screenwriter(idea(), verbose=False, jobs=1, minutes=1, **kw)


def faked(w, answers=None):
    """Answer by the schema; `answers` maps a decision name to its answer (or
    to a callable of the prompt and schema). Every call is kept in order."""
    calls = []
    answers = answers or {}

    def ask(name, prompt, schema):
        which = kind(schema)
        calls.append({"name": name, "prompt": prompt, "kind": which, "schema": schema})
        if name in answers:
            got = answers[name]
            got = got(prompt, schema) if callable(got) else got
            if isinstance(got, Exception):
                raise got
            return copy.deepcopy(got)
        if which == "plan-review":
            return copy.deepcopy(ACCEPT)
        if which == "plan":
            return copy.deepcopy(PLAN)
        if which == "shots":
            return shots_for(int(name.split("-")[1]), ("context", "payoff"))
        if which == "review":
            return sample("recommend")
        if which == "cold-read":
            return copy.deepcopy(COLD)
        if which == "answers":
            return copy.deepcopy(ANSWERS)
        if which == "match":
            return {"verdict": "match", "difference": ""}
        if which == "fixes":
            return {"fixes": []}
        return {}
    w._ask = ask
    return calls


def names(calls):
    return [c["name"] for c in calls]


def consider_then(second, *, flags=(flag(),), first=None):
    """Round 1's three samples answer consider with `flags`; round 2's answer
    `second` (a verdict and the previous one: ("recommend", "better"))."""
    verdict, previous = second
    out = {f"review-r1-s{k}": first or sample("consider", flags=flags) for k in (1, 2, 3)}
    later = flags if verdict == "consider" else ()
    out.update({f"review-r2-s{k}": (lambda prompt, schema: sample(
        verdict, previous=previous, flags=requoted(prompt, later)))
                for k in (1, 2, 3)})
    return out


# --------------------------------------------------------------------------- #
# the samples
# --------------------------------------------------------------------------- #

class Samples(unittest.TestCase):
    def test_three_samples_are_asked_under_distinct_names_with_permuted_levels(self):
        w = writer()
        calls = faked(w)
        data = w.run(review=True)
        reviews = [c for c in calls if c["kind"] == "review"]
        self.assertEqual([c["name"] for c in reviews],
                         ["review-r1-s1", "review-r1-s2", "review-r1-s3"])
        orders = [tuple(c["schema"]["properties"]["grades"]["properties"]["train"]["enum"])
                  for c in reviews]
        self.assertEqual(orders, [narrative.grade_levels(k) for k in (1, 2, 3)])
        self.assertEqual(len(set(o[0] for o in orders)), 3)
        # the same script, the same plan, the same rubric rows in each
        bodies = [c["prompt"] for c in reviews]
        for body in bodies:
            self.assertIn("line 1.1", body)
            self.assertIn(PLAN["train"], body)
        record = data["review"]
        self.assertEqual(record["verdict"], "recommend")
        self.assertEqual(len(record["samples"]), 3)
        for key in ("grades", "verdict", "samples", "flags", "revisions", "redrafts",
                    "kept", "unresolved", "cold_read", "previous_verdicts"):
            self.assertIn(key, record, key)

    def test_the_review_runs_after_the_polish_and_before_the_fact_check(self):
        w = writer(research="R")
        calls = faked(w, {"verify": {"flags": [], "verdict": "fine"}})
        w.run(review=True, do_polish=True, do_verify=True)
        order = names(calls)
        self.assertLess(order.index("polish"), order.index("review-r1-s1"))
        self.assertLess(order.index("review-r1-s3"), order.index("verify"))
        self.assertLess(order.index("cold-read"), order.index("verify"))

    def test_without_review_no_story_review_is_asked(self):
        w = writer()
        calls = faked(w)
        data = w.run()
        self.assertFalse([n for n in names(calls) if n.startswith(("review-", "cold-read"))])
        self.assertNotIn("review", data)

    def test_the_grade_is_the_median_of_mixed_and_of_three_different_levels(self):
        median = narrative.median
        self.assertEqual(median(["Good", "Poor", "Good"], narrative.LEVELS), "Good")
        self.assertEqual(median(["Excellent", "Fair", "Poor"], narrative.LEVELS), "Fair")
        self.assertEqual(median(["Poor", "Excellent", "Good"], narrative.LEVELS), "Good")
        verdicts = narrative.VERDICTS
        self.assertEqual(median(["reject", "recommend", "consider"], verdicts), "consider")
        self.assertEqual(median(["reject", "reject", "recommend"], verdicts), "reject")
        w = writer()
        faked(w, {"review-r1-s1": sample("recommend", {"train": "Excellent", "hook": "Good"}),
                  "review-r1-s2": sample("consider", {"train": "Fair", "hook": "Good"},
                                         flags=[flag(severity="minor")]),
                  "review-r1-s3": sample("consider", {"train": "Poor", "hook": "Fair"},
                                         flags=[flag(severity="minor")])})
        record = w.run(review=True, fix=False)["review"]
        self.assertEqual(record["grades"]["train"], "Fair")
        self.assertEqual(record["grades"]["hook"], "Good")
        self.assertEqual(record["verdict"], "consider")

    def test_two_valid_samples_that_disagree_are_a_consider(self):
        # judging 11: a borderline result is returned as consider
        self.assertEqual(narrative.median(["reject", "recommend"], narrative.VERDICTS,
                                          borderline="consider"), "consider")
        self.assertEqual(narrative.median(["recommend", "recommend"], narrative.VERDICTS,
                                          borderline="consider"), "recommend")
        # an even count of grades takes the worse of the middle two
        self.assertEqual(narrative.median(["Good", "Poor"], narrative.LEVELS), "Poor")

    def test_a_sample_that_breaks_its_rubric_is_asked_again_once(self):
        w = writer()
        bad = sample("recommend", {"stakes": "Poor"})
        calls = faked(w, {"review-r1-s2": bad,
                          "review-r1-s2-again": sample("consider", {"stakes": "Poor"},
                                                       flags=[flag(severity="minor")])})
        record = w.run(review=True, fix=False)["review"]
        order = names(calls)
        self.assertEqual(order.count("review-r1-s2-again"), 1)
        again = next(c for c in calls if c["name"] == "review-r1-s2-again")
        self.assertIn("stakes", again["prompt"])
        self.assertIn("Poor", again["prompt"])
        second = next(s for s in record["samples"] if s["name"] == "review-r1-s2")
        self.assertTrue(second["again"])
        self.assertEqual(second["verdict"], "consider")

    def test_a_sample_still_breaking_its_rubric_after_the_again_is_not_valid(self):
        w = writer()
        bad = sample("recommend", {"stakes": "Poor"})
        faked(w, {"review-r1-s2": bad, "review-r1-s2-again": bad,
                  "review-r1-s3": bad, "review-r1-s3-again": bad})
        data = w.run(review=True, fix=False)
        self.assertEqual(data["review"]["verdict"], "not_evidenced")

    def test_a_verdict_off_the_list_is_not_a_valid_sample(self):
        w = writer()
        off = dict(sample("recommend"), verdict="maybe")
        faked(w, {"review-r1-s1": off, "review-r1-s1-again": off})
        record = w.run(review=True, fix=False)["review"]
        first = next(s for s in record["samples"] if s["name"] == "review-r1-s1")
        self.assertFalse(first["valid"])
        self.assertEqual(record["verdict"], "recommend")      # the two others decide


# --------------------------------------------------------------------------- #
# the opening's trigger and the tension row (AUTO-136)
# --------------------------------------------------------------------------- #

def planned(trigger):
    plan = copy.deepcopy(PLAN)
    plan["hook"]["trigger"] = trigger
    return plan


def heard(*triggers, **grades):
    """The samples of round 1, each hearing its trigger."""
    return {f"review-r1-s{k}": sample("recommend", grades, trigger=trigger)
            for k, trigger in enumerate(triggers, 1)}


def redrafted(prompt, schema):
    return {"shots": [{"visual": "v", "narration": "a redrafted line", "seconds": 5,
                       "asset": "make", "style": "s", "function": "context", "shows": "s"}]}


class Trigger(unittest.TestCase):
    def round_of(self, answers):
        """One round over the plan alone (no line drafted, so not_heard is
        an honest answer)."""
        w = writer()
        faked(w, answers)
        return w.review_round([], PLAN, 1)

    def test_the_round_hears_the_label_most_valid_samples_give(self):
        self.assertEqual(self.round_of(heard("question", "question", "paradox"))["trigger"],
                         "question")

    def test_a_tie_too_few_samples_or_not_heard_is_not_evidenced(self):
        tie = heard("question", "paradox")
        tie["review-r1-s3"] = tie["review-r1-s3-again"] = sample("recommend",
                                                                 {"stakes": "Poor"})
        self.assertEqual(self.round_of(tie)["trigger"], "not_evidenced")
        self.assertEqual(self.round_of(heard("not_heard", "not_heard", "question"))["trigger"],
                         "not_evidenced")
        one = heard("question")
        for k in (2, 3):
            one[f"review-r1-s{k}"] = one[f"review-r1-s{k}-again"] = sample(
                "recommend", {"stakes": "Poor"}, trigger="question")
        self.assertEqual(self.round_of(one)["trigger"], "not_evidenced")
        # samples that name no trigger say nothing about it
        self.assertEqual(self.round_of({})["trigger"], "not_evidenced")

    def test_not_heard_over_narrated_shots_is_asked_again(self):
        w = writer()
        calls = faked(w, {"review-r1-s1": sample(trigger="not_heard"),
                          "review-r1-s1-again": sample(trigger="question")})
        record = w.run(review=True, fix=False)["review"]
        again = next(c for c in calls if c["name"] == "review-r1-s1-again")
        self.assertIn("not_heard", again["prompt"])
        first = next(s for s in record["samples"] if s["name"] == "review-r1-s1")
        self.assertTrue(first["again"])
        self.assertTrue(first["valid"])

    def test_a_heard_none_files_one_hook_trigger_match_flag(self):
        w = writer()
        faked(w, heard("none", "none", "question", hook="Fair"))
        data = w.run(review=True)
        record = data["review"]
        self.assertEqual(record["trigger"], {"planned": "unexpected_observation",
                                             "heard": "none"})
        filed = [f for f in record["unresolved"] if f.get("by") == "hook-trigger-match"]
        self.assertEqual(len(filed), 1, record["unresolved"])
        self.assertEqual({k: filed[0][k] for k in ("n", "section", "criterion", "severity",
                                                     "problem", "fix", "command")},
                         {"n": 0, "section": 0, "criterion": "hook", "severity": "important",
                          "problem": "the first three spoken sentences open no curiosity "
                                     "trigger",
                          "fix": "open on the planned trigger: unexpected_observation",
                          "command": script.REPLAN_FIX})
        said = [f for f in data["findings"] if "open no curiosity trigger" in f]
        self.assertEqual(len(said), 1, data["findings"])
        self.assertTrue(said[0].startswith("warning: story review:"), said[0])
        self.assertIn(script.REPLAN_FIX, said[0])
        # a heard none is the flag, not a difference between two triggers
        self.assertFalse([f for f in data["findings"] if "the opening was planned on" in f],
                         data["findings"])
        lines = script.story_review_lines(record)
        self.assertIn("hook trigger: planned unexpected_observation, heard none", lines)
        self.assertFalse([line for line in lines if "(differs)" in line], lines)
        self.assertIs(script.trigger_differs(record["trigger"]), False)

    def test_a_heard_none_with_the_samples_own_hook_flag_is_still_one_warning(self):
        # The samples also raise a whole-script hook flag (merge_flags keeps
        # one per criterion), here on a different fault: the opening lists
        # its conclusions. One REPLAN_FIX warning stands for the opening and
        # carries that flag's problem and fix, so the fault is not lost.
        whole = flag(n=0, section=0, criterion="hook",
                     problem="the opening lists the conclusions", fix="hold them back",
                     quote="")
        other = flag(n=0, section=0, criterion="train", problem="the train names a topic",
                     fix="name the obstacle", quote="")
        w = writer()
        faked(w, {f"review-r1-s{k}": sample("consider", {"hook": "Fair"},
                                            flags=[whole, other], trigger=t)
                  for k, t in enumerate(("none", "none", "question"), 1)})
        data = w.run(review=True)
        record = data["review"]
        self.assertEqual(record["trigger"]["heard"], "none")
        hooks = [f for f in record["unresolved"] if f.get("criterion") == "hook"]
        self.assertEqual([f.get("by") for f in hooks], ["hook-trigger-match"],
                         record["unresolved"])
        self.assertEqual(hooks[0]["problem"],
                         "the first three spoken sentences open no curiosity trigger; "
                         "the reviewers also said: the opening lists the conclusions")
        self.assertEqual(hooks[0]["fix"], "open on the planned trigger: "
                                          "unexpected_observation; hold them back")
        # a whole-script flag on another criterion still stands
        self.assertIn("the train names a topic",
                      [f["problem"] for f in record["unresolved"]])
        said = [f for f in data["findings"] if f.startswith("warning: story review:")
                and "hook" in f]
        self.assertEqual(len(said), 1, data["findings"])

    def test_a_first_minute_that_is_not_an_object_is_not_evidenced(self):
        # a drifted answer keeps first_minute as a string: no trigger, no crash
        drifted = [{"first_minute": "question"}, {"first_minute": ["paradox"]},
                   {"first_minute": {"trigger": "question"}}]
        self.assertEqual(script.heard_trigger(drifted), "not_evidenced")
        self.assertEqual(script.heard_trigger(drifted + [{"first_minute":
                                                          {"trigger": "question"}}]),
                         "question")

    def test_a_heard_trigger_other_than_the_planned_files_nothing(self):
        w = writer()
        faked(w, {"plan": planned("question"), **heard("paradox", "paradox", "question")})
        data = w.run(review=True)
        record = data["review"]
        self.assertEqual(record["trigger"], {"planned": "question", "heard": "paradox"})
        notes = [f for f in data["findings"] if f.startswith(
            "note: story review: the opening was planned on question and heard on paradox")]
        self.assertEqual(len(notes), 1, data["findings"])
        self.assertEqual(record["unresolved"], [])
        lines = script.story_review_lines(record)
        self.assertIn("hook trigger: planned question, heard paradox (differs)", lines)
        self.assertIn("| tension | Good |", lines)

    def test_not_evidenced_files_nothing_and_shows_no_difference(self):
        w = writer()
        faked(w, heard("question", "paradox", "unresolved_outcome"))
        data = w.run(review=True)
        record = data["review"]
        self.assertEqual(record["trigger"]["heard"], "not_evidenced")
        self.assertFalse([f for f in data["findings"] if "the opening was planned on" in f],
                         data["findings"])
        self.assertEqual(record["unresolved"], [])
        lines = script.story_review_lines(record)
        self.assertIn("hook trigger: planned unexpected_observation, heard not_evidenced",
                      lines)
        self.assertFalse([line for line in lines if "(differs)" in line])

    def test_a_drifted_plan_trigger_is_recorded_as_none(self):
        w = writer()
        faked(w, {"plan": planned("teaser"), **heard("none", "none", "none", hook="Fair")})
        record = w.run(review=True)["review"]
        self.assertEqual(record["trigger"], {"planned": None, "heard": "none"})
        filed = [f for f in record["unresolved"] if f.get("by") == "hook-trigger-match"]
        self.assertEqual(len(filed), 1)
        self.assertNotIn("None", filed[0]["fix"])


class TensionRow(unittest.TestCase):
    def test_a_fair_tension_is_a_consider_and_its_section_flag_is_redrafted(self):
        # The samples say consider themselves: no code turns a tension grade
        # into a verdict. That tension is not a reject ground is the rubric's
        # text (test_narrative_rubric), read by the reviewer; this test pins
        # only where a tension flag goes.
        w = writer()
        flat = flag(n=0, section=2, criterion="tension",
                    problem="section 2 risks nothing the opening did not",
                    fix="make the twin's opening date the new cost")
        first = sample("consider", {"tension": "Fair"}, flags=[flat])
        answers = consider_then(("recommend", "better"), flags=[flat], first=first)
        answers["redraft-02"] = redrafted
        calls = faked(w, answers)
        data = w.run(review=True)
        self.assertEqual(data["review"]["rounds"][0]["verdict"], "consider")
        self.assertEqual(data["review"]["rounds"][0]["grades"]["tension"], "Fair")
        prompt = next(c for c in calls if c["name"] == "redraft-02")["prompt"]
        self.assertIn("section 2 risks nothing the opening did not", prompt)
        self.assertIn("tension", prompt)

    def test_a_tension_shot_flag_reaches_revise_story(self):
        w = writer()
        flat = flag(n=3, section=2, criterion="tension",
                    problem="this line restates the risk", fix="raise the cost")
        first = sample("consider", {"tension": "Fair"}, flags=[flat])
        answers = consider_then(("recommend", "better"), flags=[flat], first=first)
        calls = faked(w, answers)
        w.run(review=True)
        prompt = next(c for c in calls if c["name"] == "revise-story-1")["prompt"]
        self.assertIn("this line restates the risk", prompt)


class DevicesRow(unittest.TestCase):
    """AUTO-135: the three samples grade devices, and the story reviewer
    reads the plan's devices; script.md's table shows the row."""

    def test_the_samples_grade_devices_and_the_table_shows_it(self):
        from tests.test_script_plan import ANALOGY, with_devices
        w = writer()
        poor = flag(n=3, section=2, criterion="devices",
                    problem="the analogy maps the paint onto the steel's strength",
                    fix="map the paint onto what it hides")
        said = sample("consider", {"devices": "Poor"}, flags=[poor])
        calls = faked(w, {"plan": with_devices(**{"2": [ANALOGY]}),
                          **{f"review-r1-s{k}": said for k in (1, 2, 3)}})
        data = w.run(review=True, fix=False)
        story = next(c for c in calls if c["name"] == "review-r1-s1")
        self.assertIn("devices", story["schema"]["properties"]["grades"]["required"])
        self.assertIn(ANALOGY["maps"], story["prompt"])
        record = data["review"]
        self.assertEqual(record["grades"]["devices"], "Poor")
        # a Poor devices alone is the samples' consider, never turned into a reject
        self.assertEqual(record["verdict"], "consider")
        self.assertIn("| devices | Poor |", script.story_review_lines(record))


# --------------------------------------------------------------------------- #
# failed samples, through the harness
# --------------------------------------------------------------------------- #

class Engine(FakeEngine):
    """Answers by the schema; a review sample whose grade order is one of
    `fail_seeds` raises, as a reviewer that timed out does."""
    seen: list = []
    fail_seeds: tuple = ()
    by_model = True

    def __init__(self, config):
        super().__init__(config)
        if Engine.by_model:
            self.usage.by_model = {config.model: {"cost_usd": 0.01, "calls": 1}}

    def ask_json(self, prompt, schema, system=None):
        which = kind(schema)
        Engine.seen.append({"kind": which, "model": self.config.model, "prompt": prompt,
                            "system": system, "schema": schema})
        if which == "review":
            order = tuple(schema["properties"]["grades"]["properties"]["train"]["enum"])
            if any(order == narrative.grade_levels(s) for s in Engine.fail_seeds):
                raise ClaudeError("the reviewer timed out")
            # through the harness the schema holds: the first minute names
            # the trigger it heard (AUTO-136)
            return sample("recommend", trigger="unexpected_observation")
        if which == "cold-read":
            return copy.deepcopy(COLD)
        if which == "answers":
            return copy.deepcopy(ANSWERS)
        if which == "match":
            return {"verdict": "match", "difference": ""}
        if which == "plan-review":
            return copy.deepcopy(ACCEPT)
        if which == "plan":
            return copy.deepcopy(PLAN)
        if which == "shots":
            out = shots_for(1, ("context", "payoff"))
            for shot in out["shots"]:
                shot["source"] = "S1"       # a sourced draft cites each line
            return out
        if which == "fixes":
            return {"fixes": []}
        if which == "flags":
            return {"flags": [], "verdict": "fine"}
        if which == "edits":
            return {"edits": []}
        if which == "quiz":
            return copy.deepcopy(QUIZ)
        if which == "quiz-answers":
            return copy.deepcopy(BEFORE if script.QUIZ_BEFORE in prompt else AFTER)
        return {}


class EngineCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._engine = harness.ENGINE
        harness.ENGINE = Engine
        self.addCleanup(setattr, harness, "ENGINE", self._engine)
        Engine.seen, Engine.fail_seeds, Engine.by_model = [], (), True

    def writer(self, **kw):
        kw.setdefault("research", "")
        return script.Screenwriter(idea(), root=self.root, verbose=False, minutes=1, jobs=1,
                                   model="sonnet", reviewer_model="opus", **kw)


class Failed(EngineCase):
    def test_one_failed_sample_is_a_failed_row_and_two_valid_samples_decide(self):
        Engine.fail_seeds = (2,)
        w = self.writer()
        data = w.run(review=True)
        rows = {d["name"]: d for d in data["engine"]["decisions"]}
        self.assertEqual(rows["review-r1-s2"]["kind"], "failed")
        self.assertEqual(rows["review-r1-s1"]["kind"], "decide")
        record = data["review"]
        self.assertEqual(record["verdict"], "recommend")
        failed = next(s for s in record["samples"] if s["name"] == "review-r1-s2")
        self.assertFalse(failed["valid"])
        self.assertIn("timed out", failed["failed"])
        self.assertFalse((self.root / "decisions" / "script" / "review-r1-s2.json").exists())

    def test_one_valid_sample_is_not_evidenced_and_never_passed(self):
        Engine.fail_seeds = (2, 3)
        data = self.writer().run(review=True)
        self.assertEqual(data["review"]["verdict"], "not_evidenced")
        self.assertTrue(any(f.startswith("warning: story review: not_evidenced")
                            for f in data["findings"]), data["findings"])

    def test_the_samples_are_the_reviewers_and_its_share_is_in_the_usage(self):
        data = self.writer().run(review=True)
        reviews = [c for c in Engine.seen if c["kind"] in ("review", "cold-read", "match")]
        self.assertTrue(reviews)
        self.assertEqual({c["model"] for c in reviews}, {"opus"})
        # the story review's role, not the plan's
        self.assertIn(narrative.LONGER, reviews[0]["system"])
        self.assertIn("recommend", reviews[0]["system"])
        self.assertIn("opus", data["usage"]["by_model"])
        self.assertIn("sonnet", data["usage"]["by_model"])
        for name in ("review-r1-s1", "cold-read", "cold-read-match"):
            self.assertTrue((self.root / "decisions" / "script" / f"{name}.json").is_file(),
                            name)


class ColdReader(EngineCase):
    def test_the_cold_reader_sees_the_narration_and_no_research_plan_or_house(self):
        (self.root / "feedback").mkdir()
        (self.root / "feedback" / "script.md").write_text("FEEDBACK-ONLY-TEXT")
        brief = script.Idea(title="Why the bridge fell", genre="explainer",
                            description="IDEA-PREMISE-TEXT", hook="IDEA-HOOK-TEXT",
                            question="IDEA-QUESTION-TEXT", stakes="IDEA-STAKES-TEXT",
                            promise="IDEA-PROMISE-TEXT")
        w = script.Screenwriter(brief, root=self.root, verbose=False, minutes=1, jobs=1,
                                model="sonnet", reviewer_model="opus",
                                house="HOUSE-ONLY-TEXT", research="RESEARCH-ONLY-TEXT")
        data = w.run(review=True)
        cold = [c for c in Engine.seen if c["kind"] == "cold-read"]
        self.assertEqual(len(cold), 1)
        seen = cold[0]["prompt"] + "\n" + cold[0]["system"]
        self.assertIn("line 1.1", cold[0]["prompt"])
        private = ("HOUSE-ONLY-TEXT", "RESEARCH-ONLY-TEXT", "FEEDBACK-ONLY-TEXT",
                   "IDEA-PREMISE-TEXT", "IDEA-HOOK-TEXT", "IDEA-QUESTION-TEXT",
                   "IDEA-STAKES-TEXT", "IDEA-PROMISE-TEXT",
                   PLAN["train"], PLAN["stakes"], PLAN["logline"], PLAN["promise"],
                   PLAN["ending"]["payoff_line"], "The paint", "paint hides steel",
                   "v1.1", "shows 1.1")
        for text in private:
            self.assertNotIn(text, seen, text)
        # the reader restates the premise before it is shown the plan's
        # questions: they would prime the premise the match then reads
        for q in PLAN["questions"]:
            self.assertNotIn(q["text"], seen, q["text"])
        # a second reading on the same cold reader maps the questions to the
        # shots that answer them, listed without where the plan closes them
        answers = [c for c in Engine.seen if c["kind"] == "answers"]
        self.assertEqual(len(answers), 1)
        self.assertIn("What brought the bridge down?", answers[0]["prompt"])
        self.assertIn("line 1.1", answers[0]["prompt"])
        self.assertNotIn("closed_in", answers[0]["prompt"])
        self.assertEqual(answers[0]["system"], cold[0]["system"])
        for text in private:
            self.assertNotIn(text, answers[0]["prompt"] + answers[0]["system"], text)
        self.assertNotIn(COLD["premise"], answers[0]["prompt"])
        # the story reviewers keep the house
        story = next(c for c in Engine.seen if c["kind"] == "review")
        self.assertIn("HOUSE-ONLY-TEXT", story["system"])
        record = data["review"]["cold_read"]
        self.assertEqual(record["premise"], COLD["premise"])
        self.assertEqual(record["unexplained_terms"], ["gusset"])
        self.assertEqual(record["answered"], ANSWERS["answered"])
        self.assertEqual(record["match"], "match")
        self.assertEqual(record["unanswered"], ["Q2"])
        for name in ("cold-read", "cold-read-answers", "cold-read-match"):
            self.assertTrue((self.root / "decisions" / "script" / f"{name}.json").is_file(),
                            name)

    def test_a_premise_that_misses_the_train_is_a_plan_flag_left_unresolved(self):
        w = writer()
        calls = faked(w, {"cold-read-match": {"verdict": "mismatch",
                                              "difference": "the reader heard a story "
                                                            "about rust"}})
        record = w.run(review=True)["review"]
        match = next(c for c in calls if c["name"] == "cold-read-match")
        self.assertIn(PLAN["train"], match["prompt"])
        self.assertIn(COLD["premise"], match["prompt"])
        unresolved = [f for f in record["unresolved"] if f["criterion"] == "train"]
        self.assertEqual(len(unresolved), 1, record["unresolved"])
        self.assertEqual((unresolved[0]["n"], unresolved[0]["section"]), (0, 0))
        self.assertEqual(unresolved[0]["severity"], "important")
        self.assertIn("yt script --fresh --review", unresolved[0]["command"])
        self.assertIn("rust", unresolved[0]["problem"])


# --------------------------------------------------------------------------- #
# what a consider sends back
# --------------------------------------------------------------------------- #

class Revision(unittest.TestCase):
    def test_a_consider_shot_flag_reaches_revise_story_and_the_rereview_carries_previous(self):
        w = writer()
        answers = consider_then(("recommend", "better"))
        answers["revise-story-1"] = {"fixes": [{"n": 1, "action": "fix",
                                                "narration": "The river is empty now.",
                                                "why": "opens on the picture"}]}
        calls = faked(w, answers)
        data = w.run(review=True)
        order = names(calls)
        self.assertIn("revise-story-1", order)
        self.assertLess(order.index("revise-story-1"), order.index("review-r2-s1"))
        revise = next(c for c in calls if c["name"] == "revise-story-1")
        self.assertIn("the story review", revise["prompt"])
        self.assertIn("the hook lists its conclusions", revise["prompt"])
        self.assertIn("may restructure the line, never add a fact", revise["prompt"])
        self.assertNotIn("flagged by the fact-check", revise["prompt"])
        again = next(c for c in calls if c["name"] == "review-r2-s1")
        self.assertIn("previous", again["schema"]["required"])
        self.assertIn("the hook lists its conclusions", again["prompt"])
        self.assertIn("The river is empty now.", again["prompt"])
        record = data["review"]
        self.assertEqual(record["verdict"], "recommend")
        self.assertEqual(record["previous_verdicts"], ["better"])
        self.assertEqual(record["revisions"][0]["after"], "The river is empty now.")
        shot = data["sections"][0]["shots"][0]
        self.assertEqual(shot["narration"], "The river is empty now.")

    def test_a_revision_judged_no_better_is_put_back(self):
        for judged in ("same", "worse"):
            w = writer()
            answers = consider_then(("consider", judged))
            answers["revise-story-1"] = {"fixes": [{"n": 1, "action": "fix",
                                                    "narration": "A rewrite.", "why": "w"}]}
            calls = faked(w, answers)
            data = w.run(review=True)
            record = data["review"]
            self.assertEqual(record["previous_verdicts"], [judged], judged)
            self.assertEqual(data["sections"][0]["shots"][0]["narration"], "line 1.1", judged)
            self.assertEqual(record["verdict"], "consider", judged)
            self.assertNotIn("revise-story-2", names(calls), judged)
            self.assertTrue(record["reverted"], judged)

    def test_the_rounds_bound_the_rereviews(self):
        w = writer(rounds=1)
        answers = consider_then(("consider", "better"))
        answers["revise-story-1"] = {"fixes": [{"n": 1, "action": "fix",
                                                "narration": "A rewrite.", "why": "w"}]}
        calls = faked(w, answers)
        w.run(review=True)
        self.assertNotIn("revise-story-2", names(calls))
        self.assertNotIn("review-r3-s1", names(calls))

    def test_a_section_flag_is_redrafted_between_how_the_one_before_ends_and_the_next_opens(self):
        w = writer()
        section_flag = flag(n=0, section=2, criterion="open_loops",
                            problem="section 2 opens Q2 and drops it",
                            fix="end section 2 on the inspection log")
        answers = consider_then(("recommend", "better"), flags=[section_flag])
        # the section after has three shots, so its opening and its close differ
        answers["write-03"] = shots_for(3, ("context", "context", "payoff"))

        def redraft(prompt, schema):
            return {"shots": [{"visual": "v", "narration": "a redrafted line", "seconds": 5,
                               "asset": "make", "style": "s", "function": "context",
                               "shows": "s"}]}
        answers["redraft-02"] = redraft
        calls = faked(w, answers)
        data = w.run(review=True)
        order = names(calls)
        self.assertIn("redraft-02", order)
        self.assertNotIn("revise-story-1", order)
        prompt = next(c for c in calls if c["name"] == "redraft-02")["prompt"]
        self.assertIn("section 2 opens Q2 and drops it", prompt)
        self.assertIn("end section 2 on the inspection log", prompt)
        self.assertIn("line 1.2", prompt)      # the section before ends on it
        self.assertIn("line 3.1", prompt)      # the section after opens on it
        self.assertNotIn("line 3.3", prompt)   # and its close is not this one's business
        self.assertIn("opens with these shots", prompt)
        record = data["review"]
        self.assertEqual(record["redrafts"][0]["section"], 2)
        self.assertEqual([s["narration"] for s in data["sections"][1]["shots"]],
                         ["a redrafted line"])
        labels = [s["label"] for sec in data["sections"] for s in sec["shots"]]
        self.assertEqual(labels, ["1.1", "1.2", "2.1", "3.1", "3.2", "3.3"])
        self.assertEqual([s["n"] for sec in data["sections"] for s in sec["shots"]],
                         [1, 2, 3, 4, 5, 6])

    def test_a_flag_on_a_shot_the_script_does_not_have_is_asked_again_not_redrafted(self):
        # one sample's off-by-one shot number is a broken answer, not a
        # reason to write a whole section again or to plan the film again
        for stray in (flag(n=99, section=2), flag(n=99, section=0), flag(n=0, section=9)):
            w = writer()
            answers = {"review-r1-s1": sample("consider", flags=[stray]),
                       "review-r1-s1-again": sample("recommend")}
            calls = faked(w, answers)
            record = w.run(review=True)["review"]
            order = names(calls)
            self.assertIn("review-r1-s1-again", order, stray)
            again = next(c for c in calls if c["name"] == "review-r1-s1-again")
            self.assertIn(str(stray["n"] or stray["section"]), again["prompt"], stray)
            self.assertFalse([n for n in order if n.startswith(("redraft-", "revise-story"))],
                             stray)
            self.assertEqual(record["unresolved"], [], stray)
            self.assertEqual(record["verdict"], "recommend", stray)
        # still stray after the again: the sample is not valid
        w = writer()
        stray = sample("consider", flags=[flag(n=99, section=2)])
        faked(w, {"review-r1-s1": stray, "review-r1-s1-again": stray})
        record = w.run(review=True)["review"]
        first = next(s for s in record["samples"] if s["name"] == "review-r1-s1")
        self.assertFalse(first["valid"])
        self.assertTrue(any("99" in p for p in first["unsettled"]), first)

    def test_the_story_rewrite_is_what_the_fact_check_reads(self):
        w = writer(research="R")
        answers = consider_then(("recommend", "better"))
        answers["revise-story-1"] = {"fixes": [{"n": 1, "action": "fix",
                                                "narration": "The river is empty now.",
                                                "why": "opens on the picture"}]}
        answers["verify"] = {"flags": [], "verdict": "fine"}
        calls = faked(w, answers)
        w.run(review=True, do_verify=True)
        verify = next(c for c in calls if c["name"] == "verify")
        self.assertIn("The river is empty now.", verify["prompt"])
        self.assertNotRegex(verify["prompt"], r"\] line 1\.1(\n|$)")

    def test_the_cold_reader_reads_the_draft_that_stands(self):
        w = writer()
        answers = consider_then(("recommend", "better"))
        answers["revise-story-1"] = {"fixes": [{"n": 1, "action": "fix",
                                                "narration": "The river is empty now.",
                                                "why": "opens on the picture"}]}
        answers["cold-read-match"] = {"verdict": "mismatch", "difference": "rust"}
        answers["cold-read-match-r2"] = {"verdict": "match", "difference": ""}
        calls = faked(w, answers)
        record = w.run(review=True)["review"]
        order = names(calls)
        for name in ("cold-read-r2", "cold-read-answers-r2", "cold-read-match-r2"):
            self.assertIn(name, order)
        reread = next(c for c in calls if c["name"] == "cold-read-r2")
        self.assertIn("The river is empty now.", reread["prompt"])
        self.assertEqual(record["cold_read"]["round"], 2)
        self.assertEqual(record["cold_read"]["match"], "match")
        self.assertEqual([f for f in record["unresolved"] if f["criterion"] == "train"], [])
        # a revision put back leaves the first reading standing
        w = writer()
        answers = consider_then(("consider", "same"))
        answers["revise-story-1"] = {"fixes": [{"n": 1, "action": "fix",
                                                "narration": "A rewrite.", "why": "w"}]}
        calls = faked(w, answers)
        record = w.run(review=True)["review"]
        self.assertNotIn("cold-read-r2", names(calls))
        self.assertEqual(record["cold_read"]["round"], 1)

    def test_a_reverted_rounds_keeps_and_superseded_plan_flags_are_not_the_records(self):
        plan_flag = flag(n=0, section=0, criterion="train",
                         problem="the train names a topic", fix="name the obstacle")
        shot_flags = [flag(), flag(n=2, problem="the second line restates the first")]
        keep_and_fix = {"fixes": [{"n": 1, "action": "fix", "narration": "A rewrite.",
                                   "why": "w"},
                                  {"n": 2, "action": "keep", "why": "it is right as written"}]}
        # put back: the keep spoke of a round that no longer stands
        w = writer()
        answers = consider_then(("consider", "same"), flags=shot_flags)
        answers["revise-story-1"] = keep_and_fix
        faked(w, answers)
        record = w.run(review=True)["review"]
        self.assertTrue(record["reverted"])
        self.assertEqual(record["kept"], [])
        self.assertEqual(record["rounds"][-1]["kept"][0]["why"], "it is right as written")
        # stands: the keep is the record's
        w = writer()
        answers = consider_then(("recommend", "better"), flags=shot_flags)
        answers["revise-story-1"] = keep_and_fix
        faked(w, answers)
        record = w.run(review=True)["review"]
        self.assertEqual([k["why"] for k in record["kept"]], ["it is right as written"])
        # a plan flag the better draft's review no longer raises is not unresolved
        w = writer()
        answers = consider_then(("recommend", "better"), flags=[flag(), plan_flag])
        answers["revise-story-1"] = {"fixes": [{"n": 1, "action": "fix",
                                                "narration": "A rewrite.", "why": "w"}]}
        faked(w, answers)
        record = w.run(review=True)["review"]
        self.assertEqual(record["verdict"], "recommend")
        self.assertEqual(record["unresolved"], [])

    def test_a_plan_flag_is_unresolved_with_the_named_fix_and_nothing_replans(self):
        w = writer()
        plan_flag = flag(n=0, section=0, criterion="train",
                         problem="the train names a topic", fix="name the obstacle")
        calls = faked(w, consider_then(("recommend", "better"), flags=[plan_flag]))
        data = w.run(review=True)
        order = names(calls)
        self.assertEqual(order.count("plan"), 1)
        self.assertFalse([n for n in order if n.startswith(("revise-story", "redraft-"))])
        self.assertNotIn("review-r2-s1", order)
        record = data["review"]
        self.assertEqual(record["unresolved"][0]["problem"], "the train names a topic")
        self.assertIn("yt script --fresh --review", record["unresolved"][0]["command"])
        self.assertTrue(any("story review" in f and "--fresh --review" in f
                            for f in data["findings"]), data["findings"])

    def test_report_only_reports_and_revises_nothing(self):
        w = writer()
        calls = faked(w, consider_then(("recommend", "better")))
        record = w.run(review=True, fix=False)["review"]
        self.assertEqual(record["verdict"], "consider")
        self.assertFalse([n for n in names(calls)
                          if n.startswith(("revise-story", "redraft-", "review-r2"))])
        self.assertEqual(record["flags"][0]["problem"], "the hook lists its conclusions")

    def test_a_reject_is_an_error_finding_and_is_not_revised(self):
        w = writer()
        reject = sample("reject", {"train": "Poor"},
                        flags=[flag(severity="blocker", criterion="train")])
        calls = faked(w, {f"review-r1-s{k}": reject for k in (1, 2, 3)})
        data = w.run(review=True)
        self.assertIn("error: story review: reject", "\n".join(data["findings"]))
        self.assertFalse([n for n in names(calls) if n.startswith(("revise-story", "redraft-"))])


# --------------------------------------------------------------------------- #
# the command
# --------------------------------------------------------------------------- #

class QuestionLedger(unittest.TestCase):
    """AUTO-96: the cold reader's answers measure the question ledger."""

    def marked(self, n, **marks):
        return dict(shots_for(n, ("context", "payoff")), **marks)

    def test_a_question_the_writer_answered_and_the_reader_did_not_hear_goes_to_revise_ledger_2(self):
        w = writer()
        calls = faked(w, {
            "write-01": self.marked(1, opens=[{"id": "Q1", "opens_at": 1}]),
            "write-02": self.marked(2, opens=[{"id": "Q2", "opens_at": 1}]),
            "write-03": self.marked(3, closes=[{"id": "Q1", "closes_at": 1},
                                               {"id": "Q2", "closes_at": 2}])})
        data = w.run(review=True)
        order = names(calls)
        self.assertNotIn("revise-ledger", order)       # the writers kept the ledger
        self.assertLess(order.index("cold-read-answers"), order.index("revise-ledger-2"))
        prompt = next(c["prompt"] for c in calls if c["name"] == "revise-ledger-2")
        self.assertIn("rule:unanswered-by-reader", prompt)
        self.assertIn("Q2", prompt)
        self.assertNotIn("Q1 (", prompt.split("flagged by", 1)[1])
        lines = [f for f in data["findings"] if "rule:unanswered-by-reader" in f]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("warning: rule:unanswered-by-reader: shot 3.2: "),
                        lines[0])
        self.assertEqual([(q["id"], q["opened_at"], q["closed_at"]) for q in data["questions"]],
                         [("Q1", "1.1", "3.1"), ("Q2", "2.1", "3.2")])

    def test_a_note_from_a_redraft_put_back_is_not_in_the_findings(self):
        section_flag = flag(n=0, section=2, criterion="open_loops",
                            problem="section 2 opens Q2 and drops it",
                            fix="end section 2 on the inspection log")
        for judged, noted in (("worse", False), ("better", True)):
            with self.subTest(judged=judged):
                w = writer()
                answers = consider_then(("consider" if judged == "worse" else "recommend",
                                         judged), flags=[section_flag])
                answers["redraft-02"] = dict(shots_for(2, ("context",)),
                                             opens=[{"id": "Q2", "opens_at": 7}])
                faked(w, answers)
                data = w.run(review=True)
                self.assertEqual(data["review"]["reverted"], not noted)
                notes = [f for f in data["findings"] if "opens_at 7" in f]
                self.assertEqual(bool(notes), noted, data["findings"])

    def test_report_only_records_the_readers_gap_and_revises_nothing(self):
        w = writer()
        calls = faked(w, {"write-03": self.marked(3, closes=[{"id": "Q1", "closes_at": 1},
                                                             {"id": "Q2", "closes_at": 2}])})
        data = w.run(review=True, fix=False)
        self.assertFalse([n for n in names(calls) if n.startswith("revise-ledger")])
        self.assertTrue([f for f in data["findings"]
                         if "rule:unanswered-by-reader" in f and "--report-only" in f])


#: A cold read that needed one term the narration never explains.
COLD_TERMS = {"premise": "The Sun is made of plasma.", "unexplained_terms": ["plasma"],
              "claims": [], "answered": []}


def termed(lines, audience="lay", questions=False):
    """A plan and its sections, one shot a section, numbered as run() does.
    A plan written before AUTO-90 has no `questions`, and its terms are
    still measured."""
    plan = {"sections": [{"heading": f"S{i}", "seconds": 5} for i in range(1, len(lines) + 1)]}
    if audience is not None:
        plan["audience"] = audience
    if questions:
        plan["questions"] = []
    sections = script.sections_of({"sections": [
        {"heading": f"S{i}", "shots": [{"visual": "v", "narration": line, "seconds": 5,
                                        "asset": "make", "style": "s",
                                        "function": "context", "shows": "s"}]}
        for i, line in enumerate(lines, 1)]})
    shots = writer()._renumber(sections)
    return plan, sections, shots


PLASMA = ["The Sun is hot.", "Plasma is what the Sun is made of.", "The plasma streams out."]


def term_rules(flags):
    return [f for f in flags if f["rule"] == "rule:unexplained-term"]


class UnexplainedTerms(unittest.TestCase):
    """AUTO-138: a term the cold reader needed and was never told is a flag
    for revise-ledger-2 on a lay plan, and a warning on any other."""

    def test_a_term_the_narration_says_is_an_important_flag_on_its_first_shot(self):
        plan, sections, _ = termed(PLASMA)
        self.assertNotIn("questions", plan)
        flags = script.term_findings(plan, sections, COLD_TERMS)
        self.assertEqual(len(flags), 1, flags)
        f = flags[0]
        self.assertEqual((f["rule"], f["n"], f["term"], f["section"]),
                         ("rule:unexplained-term", 2, "plasma", 2))
        self.assertEqual((f["tier"], f["severity"]), ("important", "important"))
        self.assertIn("plasma", f["problem"])
        self.assertTrue(f["problem"].startswith("rule:unexplained-term: shot 2.1: "),
                        f["problem"])
        required = script.CHECK_SCHEMA["properties"]["flags"]["items"]["required"]
        for key in required:
            self.assertIn(key, f)
        # a plan with an empty ledger is measured the same
        plan, sections, _ = termed(PLASMA, questions=True)
        self.assertEqual(len(script.term_findings(plan, sections, COLD_TERMS)), 1)

    def test_on_any_other_audience_the_flag_is_minor_and_never_sent(self):
        # enthusiasts keep the field's common terms undefined (AUDIENCE_BRIEF),
        # and the cold reader knows none of them; a plan with no audience was
        # given no brief to define its terms
        for audience in ("enthusiast", "expert", None):
            with self.subTest(audience=audience):
                plan, sections, shots = termed(PLASMA, audience=audience)
                flags = script.term_findings(plan, sections, COLD_TERMS)
                self.assertEqual([(f["tier"], f["severity"]) for f in flags],
                                 [("minor", "minor")])
                w = writer()

                def ask(name, prompt, schema):
                    raise AssertionError(f"{name} was asked")
                w._ask = ask
                done = w.revise_ledger(sections, shots, plan, cold_read=COLD_TERMS,
                                       name="revise-ledger-2", figures=False)
                self.assertEqual(len(term_rules(done["flags"])), 1)
                lines = w.ledger_lines(plan, sections, COLD_TERMS)
                self.assertEqual(len(lines), 1, lines)
                self.assertTrue(lines[0].startswith("warning: rule:unexplained-term: "),
                                lines[0])

    def test_no_flag_for_an_unsaid_term_a_failed_read_or_no_read(self):
        plan, sections, _ = termed(PLASMA)
        for cold in (None, "plasma", {}, {"unexplained_terms": "plasma"},
                     dict(COLD_TERMS, failed="timed out"),
                     dict(COLD_TERMS, unexplained_terms=["gusset"]),
                     # a whole word only: "plasm" is inside "plasma"
                     dict(COLD_TERMS, unexplained_terms=["plasm"])):
            with self.subTest(cold=cold):
                self.assertEqual(script.term_findings(plan, sections, cold), [])

    def test_terms_that_fold_to_one_give_one_flag_and_case_does_not_matter(self):
        plan, sections, _ = termed(PLASMA)
        flags = script.term_findings(
            plan, sections, dict(COLD_TERMS, unexplained_terms=["PLASMA", "plasma", " Plasma "]))
        self.assertEqual(len(flags), 1, flags)
        self.assertEqual(flags[0]["n"], 2)

    def test_the_flag_keeps_the_readers_spelling_and_the_ledger_its_fold(self):
        plan, sections, _ = termed(PLASMA)
        flags = script.term_findings(
            plan, sections, dict(COLD_TERMS, unexplained_terms=["Plasma", "PLASMA"]))
        self.assertEqual([f["term"] for f in flags], ["Plasma"])
        self.assertIn("\"Plasma\"", flags[0]["problem"])
        self.assertEqual(script._ledger_key(flags[0]), ("rule:unexplained-term", "plasma"))

    def test_the_ledger_says_term_flags_apart_from_question_flags(self):
        plan, sections, shots = termed(PLASMA)
        w = writer()
        said = []
        w._say = said.append
        w._ask = lambda name, prompt, schema: {"fixes": []}
        w.revise_ledger(sections, shots, plan, cold_read=COLD_TERMS,
                        name="revise-ledger-2", figures=False)
        self.assertIn("[ledger] 1 term flag(s)", said)
        self.assertFalse([m for m in said if "question flag" in m], said)

    def test_the_ledger_keys_a_term_flag_on_its_term(self):
        self.assertEqual(script._ledger_key({"rule": "rule:unexplained-term", "term": "plasma",
                                             "section": 2}),
                         ("rule:unexplained-term", "plasma"))
        self.assertEqual(script._ledger_key({"rule": "rule:unexplained-term", "term": "Plasma",
                                             "section": 2}),
                         ("rule:unexplained-term", "plasma"))
        self.assertEqual(script._ledger_key({"rule": "rule:open-loop", "question": "Q1",
                                             "section": 3}), ("rule:open-loop", "Q1"))
        self.assertEqual(script._ledger_key({"rule": "rule:raises-nothing", "section": 3}),
                         ("rule:raises-nothing", 3))

    def test_the_bound_cites_attention_24_and_judging_14(self):
        bound = script.BOUNDS["unexplained_term"]
        self.assertEqual(bound["value"], 0)
        self.assertEqual(bound["corpus"], ("attention 24", "judging 14"))
        self.assertEqual(bound["strength"], "peer-reviewed")
        rule = next(r for r in script.LEDGER_RULES if r.id == "rule:unexplained-term")
        self.assertEqual((rule.tier, rule.langs, rule.bound),
                         ("important", (), "unexplained_term"))

    def fixing(self, answer):
        w = writer()
        asked = []

        def ask(name, prompt, schema):
            asked.append((name, prompt))
            return copy.deepcopy(answer)
        w._ask = ask
        return w, asked

    def test_a_changed_line_that_still_says_the_term_is_answered(self):
        plan, sections, shots = termed(PLASMA)
        w, asked = self.fixing({"fixes": [{
            "n": 2, "action": "fix", "why": "defined where it is first said",
            "narration": "Plasma, gas so hot its atoms come apart, is what the Sun is made of."}]})
        done = w.revise_ledger(sections, shots, plan, cold_read=COLD_TERMS,
                               name="revise-ledger-2", figures=False)
        self.assertEqual([n for n, _ in asked], ["revise-ledger-2"])
        self.assertIn("rule:unexplained-term", asked[0][1])
        self.assertIn("plasma", asked[0][1])
        # the cold read is not repeated: the defined line still says the word
        self.assertEqual(len(script.term_findings(plan, sections, COLD_TERMS)), 1)
        self.assertEqual(term_rules(done["flags"]), [])
        self.assertEqual(w.ledger_lines(plan, sections, COLD_TERMS), [])
        # a section written again in that line's place is measured afresh
        _, fresh, _ = termed(PLASMA)
        sections[1].shots = fresh[1].shots
        self.assertEqual([f["n"] for f in term_rules(w.ledger_flags(plan, sections,
                                                                    COLD_TERMS))], [2])

    def test_a_line_kept_with_a_reason_stays_a_warning_with_it(self):
        plan, sections, shots = termed(PLASMA)
        w, asked = self.fixing({"fixes": [{"n": 2, "action": "keep",
                                           "why": "the picture labels it"}]})
        w.revise_ledger(sections, shots, plan, cold_read=COLD_TERMS,
                        name="revise-ledger-2", figures=False)
        lines = w.ledger_lines(plan, sections, COLD_TERMS)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("warning: rule:unexplained-term: shot 2.1: "))
        self.assertIn("kept: the picture labels it", lines[0])
        # and it is not asked about again
        w.revise_ledger(sections, shots, plan, cold_read=COLD_TERMS,
                        name="revise-ledger-3", figures=False)
        self.assertEqual([n for n, _ in asked], ["revise-ledger-2"])

    def test_the_stage_sends_term_and_reader_flags_in_one_revise_ledger_2(self):
        def marked(n, **marks):
            return dict(shots_for(n, ("context", "payoff")), **marks)
        two = marked(2, opens=[{"id": "Q2", "opens_at": 1}])
        two["shots"][0]["narration"] = "The gusset plate hid the flaw."
        w = writer()
        calls = faked(w, {
            "write-01": marked(1, opens=[{"id": "Q1", "opens_at": 1}]),
            "write-02": two,
            "write-03": marked(3, closes=[{"id": "Q1", "closes_at": 1},
                                          {"id": "Q2", "closes_at": 2}])})
        data = w.run(review=True)
        order = names(calls)
        self.assertNotIn("revise-ledger", order)          # no cold read before the review
        self.assertEqual(order.count("revise-ledger-2"), 1)
        prompt = next(c["prompt"] for c in calls if c["name"] == "revise-ledger-2")
        flagged = prompt.split("flagged by", 1)[1]
        self.assertIn("rule:unanswered-by-reader", flagged)
        self.assertIn("rule:unexplained-term", flagged)
        self.assertIn("gusset", flagged)
        # the writer changed nothing: the term stands as a warning, and
        # script.md still lists it under the cold read's terms
        lines = [f for f in data["findings"] if "rule:unexplained-term" in f]
        self.assertEqual(len(lines), 1, data["findings"])
        self.assertTrue(lines[0].startswith("warning: rule:unexplained-term: shot 2.1: "),
                        lines[0])
        self.assertIn("Unexplained terms: gusset.",
                      "\n".join(script.story_review_lines(data["review"])))

    def test_no_revise_ledger_2_when_nothing_is_flagged(self):
        def marked(n, **marks):
            return dict(shots_for(n, ("context", "payoff")), **marks)
        both = {"answered": [{"question_id": "Q1", "shot_index": 5},
                             {"question_id": "Q2", "shot_index": 6}]}
        balanced = {
            "write-01": marked(1, opens=[{"id": "Q1", "opens_at": 1}]),
            "write-02": marked(2, opens=[{"id": "Q2", "opens_at": 1}]),
            "write-03": marked(3, closes=[{"id": "Q1", "closes_at": 1},
                                          {"id": "Q2", "closes_at": 2}]),
            "cold-read-answers": both}
        w = writer()
        calls = faked(w, balanced)       # the reader's "gusset" is never said
        data = w.run(review=True)
        self.assertFalse([n for n in names(calls) if n.startswith("revise-ledger")])
        self.assertFalse([f for f in data["findings"] if "rule:unexplained-term" in f])
        # said on an enthusiast plan: a warning, and still no decision
        said = copy.deepcopy(balanced)
        said["write-02"]["shots"][0]["narration"] = "The gusset plate hid the flaw."
        said["plan"] = dict(PLAN, audience="enthusiast")
        w = writer()
        calls = faked(w, said)
        data = w.run(review=True)
        self.assertFalse([n for n in names(calls) if n.startswith("revise-ledger")])
        lines = [f for f in data["findings"] if "rule:unexplained-term" in f]
        self.assertEqual(len(lines), 1, data["findings"])
        self.assertTrue(lines[0].startswith("warning: rule:unexplained-term: "), lines[0])


class VoiceRow(unittest.TestCase):
    def test_a_voice_flag_on_a_shot_reaches_revise_story_like_any_other(self):
        # AUTO-138: the quote is the "confused here" mark direction 24 asks for
        w = writer()
        lecture = flag(n=3, section=2, criterion="voice",
                       problem="this line reads as a lecture", fix="ask it as a question")
        first = sample("consider", {"voice": "Fair"}, flags=[lecture])
        answers = consider_then(("recommend", "better"), flags=[lecture], first=first)
        calls = faked(w, answers)
        data = w.run(review=True)
        self.assertEqual(data["review"]["rounds"][0]["grades"]["voice"], "Fair")
        self.assertEqual(data["review"]["rounds"][0]["verdict"], "consider")
        prompt = next(c for c in calls if c["name"] == "revise-story-1")["prompt"]
        self.assertIn("this line reads as a lecture", prompt)
        self.assertIn(line_of(3), prompt)
        self.assertEqual(data["review"]["unresolved"], [])

    def test_a_voice_flag_on_shot_0_is_asked_again_never_replanned(self):
        w = writer()
        stray = flag(n=0, section=2, criterion="voice", problem="section 2 lectures")
        calls = faked(w, {"review-r1-s1": sample("consider", {"voice": "Fair"}, flags=[stray]),
                          "review-r1-s1-again": sample("recommend")})
        record = w.run(review=True)["review"]
        order = names(calls)
        self.assertIn("review-r1-s1-again", order)
        again = next(c for c in calls if c["name"] == "review-r1-s1-again")
        self.assertIn("shot 0", again["prompt"])
        self.assertFalse([n for n in order if n.startswith(("redraft-", "revise-story"))])
        self.assertEqual(record["unresolved"], [])


class Command(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.work = self.base / "work"
        self.work.mkdir()
        self.film = self.work / "why-the-bridge-fell"
        self.film.mkdir()
        (self.film / "idea.json").write_text(
            '{"title": "Why the bridge fell", "genre": "g", "description": "d"}')

    def run_stage(self, *flags, answers=None):
        answers = answers or {}
        asked = []

        def ask(self_, name, prompt, schema):
            which = kind(schema)
            asked.append(name)
            if name in answers:
                got = answers[name]
                return copy.deepcopy(got(prompt, schema) if callable(got) else got)
            if which == "plan-review":
                return copy.deepcopy(ACCEPT)
            if which == "plan":
                return copy.deepcopy(PLAN)
            if which == "shots":
                return shots_for(int(name.split("-")[1]), ("context", "payoff"))
            if which == "review":
                return sample("recommend")
            if which == "cold-read":
                return copy.deepcopy(COLD)
            if which == "answers":
                return copy.deepcopy(ANSWERS)
            if which == "match":
                return {"verdict": "match", "difference": ""}
            if which == "fixes":
                return {"fixes": []}
            return {}
        args = cli.build_parser().parse_args(
            ["script", "--project", str(self.film), "--work", str(self.work), "--quiet",
             "--model", "sonnet", *flags])
        with mock.patch.object(script.Screenwriter, "_ask", ask), \
                contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = script.run(args)
        return code, asked, out.getvalue() + err.getvalue()

    def rejected(self):
        reject = sample("reject", {"train": "Poor"},
                        flags=[flag(severity="blocker", criterion="train")])
        return {f"review-r1-s{k}": reject for k in (1, 2, 3)}

    def test_the_flags_parse(self):
        ns = cli.build_parser().parse_args(["script"])
        self.assertFalse(ns.gate)
        self.assertFalse(ns.fresh)
        ns = cli.build_parser().parse_args(["script", "--review", "--gate", "--fresh"])
        self.assertTrue(ns.gate and ns.fresh)

    def test_a_reject_without_gate_writes_the_narration_exits_0_and_leaves_an_error(self):
        code, asked, said = self.run_stage("--review", answers=self.rejected())
        self.assertEqual(code, 0, said)
        self.assertTrue((self.film / "narration.txt").is_file())
        record = json.loads((self.film / "script.json").read_text())
        self.assertEqual(record["review"]["verdict"], "reject")
        self.assertIn("error: story review: reject", "\n".join(record["findings"]))
        self.assertIn("story review: reject", said)
        md = (self.film / "script.md").read_text()
        self.assertIn("## Story review", md)
        self.assertIn("reject", md[md.index("## Story review"):])

    def test_a_reject_under_gate_leaves_the_narration_unwritten_and_exits_1(self):
        code, asked, said = self.run_stage("--review", "--gate", answers=self.rejected())
        self.assertEqual(code, 1, said)
        self.assertFalse((self.film / "narration.txt").exists())
        self.assertTrue((self.film / "script.json").is_file())
        self.assertIn("--gate", said)

    def test_not_evidenced_exits_1_under_gate_and_0_with_a_warning_otherwise(self):
        # one valid sample of three
        for flags, code_wanted in ((("--review",), 0), (("--review", "--gate"), 1)):
            with mock.patch.object(script.Screenwriter, "story_sample",
                                   _failing_sample(("review-r1-s2", "review-r1-s3"))):
                code, asked, said = self.run_stage(*flags)
            self.assertEqual(code, code_wanted, (flags, said))
            record = json.loads((self.film / "script.json").read_text())
            self.assertEqual(record["review"]["verdict"], "not_evidenced")
            self.assertIn("not_evidenced", said)

    def test_report_only_with_gate_reports_only(self):
        code, asked, said = self.run_stage("--review", "--gate", "--report-only",
                                           answers=self.rejected())
        self.assertEqual(code, 0, said)
        self.assertTrue((self.film / "narration.txt").is_file())

    def test_every_verdict_is_appended_to_the_review_log(self):
        answers = consider_then(("recommend", "better"))
        answers["revise-story-1"] = {"fixes": [{"n": 1, "action": "fix",
                                                "narration": "A rewrite.", "why": "w"}]}
        self.run_stage("--review", answers=answers)
        log = self.work / script.REVIEW_LOG
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual([r["round"] for r in rows], [1, 2])
        self.assertEqual([r["verdict"] for r in rows], ["consider", "recommend"])
        for row in rows:
            self.assertEqual(row["slug"], "why-the-bridge-fell")
            self.assertIsNone(row["label"])
            self.assertEqual(row["model"], "opus")
            self.assertEqual(len(row["samples"]), 3)
            self.assertEqual(set(row["grades"]), set(narrative.KEYS))
        self.run_stage("--review", answers=self.rejected())
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(rows[-1]["verdict"], "reject")
        self.assertEqual(len(rows), 3)

    def test_gate_without_review_is_refused_before_anything_is_asked(self):
        # --gate reads the story review; with no review to read, or on
        # --revise, which never reads the story again, it would gate on a
        # verdict about lines that are no longer there
        for flags_ in (("--gate",), ("--revise", "1.1", "--why", "w", "--gate"),
                       ("--revise", "1.1", "--why", "w", "--review", "--gate")):
            code, asked, said = self.run_stage(*flags_)
            self.assertEqual(code, 2, (flags_, said))
            self.assertEqual(asked, [], flags_)
            self.assertIn("--gate", said)
            self.assertEqual(len(said.strip().splitlines()), 1, said)

    def test_fresh_asks_every_decision_again(self):
        seen = {}
        real = script.Screenwriter.__init__

        def init(self_, *a, **kw):
            real(self_, *a, **kw)
            seen["fresh"] = [h.fresh for h in (self_.harness, self_._drafter)]
        with mock.patch.object(script.Screenwriter, "__init__", init):
            self.run_stage("--fresh")
        self.assertEqual(seen["fresh"], [True, True])


# --------------------------------------------------------------------------- #
# the flagged words, quoted exactly (AUTO-112)
# --------------------------------------------------------------------------- #

class Quotes(unittest.TestCase):
    """A flag quotes the words it is about, and a contradiction names both
    lines. A quote the script does not say is a broken answer: asked again
    once, then not valid, never trimmed or matched on the model's behalf."""

    SHOTS = {1: "line 1.1", 2: "line 1.2", 3: "line 2.1", 4: "line 2.2"}
    SECTIONS = {1: [1, 2], 2: [3, 4]}

    def problems(self, *flags, shots=None, sections=None):
        review = sample("consider", flags=flags)
        return script.Screenwriter._sample_problems(
            review, narrative.REVIEW_SCHEMA,
            self.SHOTS if shots is None else shots,
            self.SECTIONS if sections is None else sections)

    def test_a_quote_not_in_the_shot_is_asked_again_naming_the_shot_and_the_words(self):
        w = writer()
        made_up = flag(quote="the river was never empty")
        calls = faked(w, {"review-r1-s1": sample("consider", flags=[made_up]),
                          "review-r1-s1-again": sample("recommend")})
        record = w.run(review=True, fix=False)["review"]
        order = names(calls)
        self.assertEqual(order.count("review-r1-s1-again"), 1)
        again = next(c for c in calls if c["name"] == "review-r1-s1-again")["prompt"]
        said = again.split("Your previous answer", 1)[1]
        self.assertIn("shot 1", said)
        self.assertIn('"the river was never empty"', said)
        first = next(s for s in record["samples"] if s["name"] == "review-r1-s1")
        self.assertTrue(first["again"] and first["valid"], first)
        self.assertEqual(record["verdict"], "recommend")

    def test_a_sample_still_misquoting_is_not_valid_and_its_flags_never_reach_revise(self):
        w = writer()
        made_up = sample("consider", flags=[flag(quote="invented words",
                                                 problem="a problem nobody else saw")])
        honest = sample("consider", flags=[flag()])
        answers = {"review-r1-s1": made_up, "review-r1-s1-again": made_up,
                   "review-r1-s2": honest, "review-r1-s3": honest,
                   "revise-story-1": {"fixes": [{"n": 1, "action": "fix",
                                                 "narration": "The river is empty now.",
                                                 "why": "w"}]}}
        answers.update({f"review-r2-s{k}": sample("recommend", previous="better")
                        for k in (1, 2, 3)})
        calls = faked(w, answers)
        record = w.run(review=True)["review"]
        self.assertIn("review-r1-s1-again", names(calls))
        revise = next(c for c in calls if c["name"] == "revise-story-1")["prompt"]
        self.assertNotIn("invented words", revise)
        self.assertNotIn("a problem nobody else saw", revise)
        self.assertEqual(record["rounds"][0]["verdict"], "consider")   # two valid decide
        # the round as recorded: the sample is not valid, and says why
        w = writer()
        faked(w, answers)
        record = w.run(review=True, fix=False)["review"]
        first = next(s for s in record["samples"] if s["name"] == "review-r1-s1")
        self.assertFalse(first["valid"])
        self.assertTrue(any("invented words" in p and "shot 1" in p
                            for p in first["unsettled"]), first)
        self.assertNotIn("a problem nobody else saw",
                         [f["problem"] for f in record["flags"]])

    def test_whitespace_and_curly_quote_marks_are_the_only_difference_allowed(self):
        shots = {1: 'He said "stop"  there,\nand it\'s late.'}
        for quote in ('He said “stop” there, and it’s late.',
                      "He  said \"stop\"\tthere,",
                      "it’s late."):
            with self.subTest(quote=quote):
                self.assertEqual(self.problems(flag(quote=quote), shots=shots,
                                               sections={1: [1]}), [])
        for quote in ("he said \"stop\" there", "He said stop there", "and it's late!",
                      "said \"stop\" there and"):
            with self.subTest(quote=quote):
                found = self.problems(flag(quote=quote), shots=shots, sections={1: [1]})
                self.assertEqual(len(found), 1, found)
                self.assertIn("shot 1", found[0])

    def test_a_shot_flag_must_quote_something(self):
        found = self.problems(flag(quote=""))
        self.assertEqual(len(found), 1, found)
        self.assertIn("shot 1", found[0])
        # a silent shot has no words to quote
        self.assertEqual(self.problems(flag(quote=""), shots={1: ""}, sections={1: [1]}), [])

    def test_a_place_with_no_narration_takes_only_an_empty_quote(self):
        # a silent shot, a section of silent shots, or a plan read before any
        # line is drafted says no words, so any quote there is made up
        silent = {1: "", 2: "line 1.2", 3: script.SILENT, 4: "  "}
        for f, where in ((flag(n=1, quote="totally invented"), "shot 1"),
                         (flag(n=3, quote=script.SILENT), "shot 3"),
                         (flag(n=0, section=2, quote="line 1.2"), "section 2")):
            with self.subTest(where=where):
                found = self.problems(f, shots=silent, sections={1: [1, 2], 2: [3, 4]})
                self.assertEqual(len(found), 1, found)
                self.assertIn(where, found[0])
                self.assertIn("no narration", found[0])
                self.assertIn("leave quote empty", found[0])
        found = self.problems(flag(n=0, section=2, quote="made up"), shots={}, sections={})
        self.assertEqual(len(found), 1, found)
        # through the stage: a writer whose first shot is silent
        w = writer()
        silent_first = shots_for(1, ("context", "payoff"))
        silent_first["shots"][0]["narration"] = ""
        calls = faked(w, {"write-01": silent_first,
                          "review-r1-s1": sample("consider", flags=[
                              flag(n=1, quote="the river was never empty")]),
                          "review-r1-s1-again": sample("recommend")})
        record = w.run(review=True, fix=False)["review"]
        self.assertIn("review-r1-s1-again", names(calls))
        again = next(c for c in calls if c["name"] == "review-r1-s1-again")["prompt"]
        said = again.split("Your previous answer", 1)[1]
        self.assertIn("shot 1", said)
        self.assertIn("no narration", said)
        self.assertNotIn("the river was never empty",
                         [f.get("quote") for f in record["flags"]])

    def test_a_quote_is_whole_words_never_a_cut_word(self):
        self.assertTrue(script.said_in("line 1.1", "line 1.1"))
        self.assertTrue(script.said_in("the river", "And the river rose."))
        self.assertTrue(script.said_in("rose.", "And the river rose."))
        for quote in ("ine 1.1", "a", "the riv", "nd the"):
            with self.subTest(quote=quote):
                self.assertFalse(script.said_in(quote, "And the river rose. abandon"))

    def test_a_section_flags_quote_comes_from_that_sections_lines(self):
        self.assertEqual(self.problems(flag(n=0, section=2, quote="line 2.2")), [])
        found = self.problems(flag(n=0, section=2, quote="line 1.1"))
        self.assertEqual(len(found), 1, found)
        self.assertIn("section 2", found[0])
        self.assertIn('"line 1.1"', found[0])
        self.assertEqual(len(self.problems(flag(n=0, section=2, quote=""))), 1)
        # through the stage: asked again, as any broken answer is
        w = writer()
        stray = flag(n=0, section=2, quote="line 1.1", criterion="open_loops")
        calls = faked(w, {"review-r1-s2": sample("consider", flags=[stray]),
                          "review-r1-s2-again": sample("recommend")})
        w.run(review=True, fix=False)
        self.assertIn("review-r1-s2-again", names(calls))

    def test_a_whole_script_flag_quotes_nothing_and_is_valid(self):
        whole = flag(n=0, section=0, criterion="train", quote="")
        self.assertEqual(self.problems(whole), [])
        w = writer()
        calls = faked(w, {f"review-r1-s{k}": sample("consider", flags=[whole])
                          for k in (1, 2, 3)})
        record = w.run(review=True, fix=False)["review"]
        self.assertFalse([n for n in names(calls)
                          if n.startswith("review-") and n.endswith("-again")])
        self.assertEqual(record["flags"][0]["criterion"], "train")

    def test_a_plan_read_before_any_line_is_drafted_has_nothing_to_quote(self):
        plan_flag = flag(n=0, section=2, criterion="train", quote="")
        self.assertEqual(self.problems(plan_flag, shots={}, sections={}), [])

    def test_conflicts_with_must_name_a_shot_that_says_its_words(self):
        good = flag(n=3, quote="line 2.1", criterion="honesty",
                    conflicts_with={"n": 1, "quote": "line 1.1"})
        self.assertEqual(self.problems(good), [])
        for other, words in (({"n": 99, "quote": "line 1.1"}, "99"),
                             ({"n": 1, "quote": "line 2.2"}, '"line 2.2"'),
                             ({"n": 1, "quote": ""}, "shot 1")):
            with self.subTest(other=other):
                found = self.problems(dict(good, conflicts_with=other))
                self.assertEqual(len(found), 1, found)
                self.assertIn(words, found[0])
        w = writer()
        broken = dict(good, conflicts_with={"n": 99, "quote": "line 1.1"})
        calls = faked(w, {"review-r1-s3": sample("consider", flags=[broken]),
                          "review-r1-s3-again": sample("recommend")})
        w.run(review=True, fix=False)
        self.assertIn("review-r1-s3-again", names(calls))

    def test_conflicts_with_of_shot_0_and_no_words_means_none(self):
        # some engines fill an optional object with its zero value; a shot 0
        # with no words is no contradiction, as story_flag_line reads it
        empty = flag(n=3, quote="line 2.1", conflicts_with={"n": 0, "quote": ""})
        self.assertEqual(self.problems(empty), [])
        self.assertNotIn("contradicts", script.story_flag_line(empty))
        found = self.problems(dict(empty, conflicts_with={"n": 0, "quote": "line 1.1"}))
        self.assertEqual(len(found), 1, found)

    def test_merge_keeps_the_most_severe_quote_and_lists_every_distinct_one(self):
        minor = flag(severity="minor", quote="line")
        severe = flag(severity="blocker", quote="line 1.1",
                      conflicts_with={"n": 3, "quote": "line 2.1"})
        again = flag(severity="minor", quote="line")
        merged = script.merge_flags([{"flags": [minor]}, {"flags": [severe]},
                                     {"flags": [again]}])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["quote"], "line 1.1")
        self.assertEqual(merged[0]["conflicts_with"], {"n": 3, "quote": "line 2.1"})
        self.assertEqual(merged[0]["quotes"], ["line", "line 1.1"])
        self.assertEqual(merged[0]["samples"], 3)

    def test_merge_reads_quotes_alike_as_the_check_does(self):
        merged = script.merge_flags([{"flags": [flag(quote="it’s  late")]},
                                     {"flags": [flag(quote="it's late")]}])
        self.assertEqual(merged[0]["quotes"], ["it’s  late"])

    def test_the_redraft_and_the_unresolved_warning_carry_the_quote(self):
        w = writer()
        section_flag = flag(n=0, section=2, criterion="open_loops", quote="line 2.1",
                            problem="section 2 opens Q2 and drops it")
        answers = consider_then(("recommend", "better"), flags=[section_flag])
        calls = faked(w, answers)
        w.run(review=True)
        prompt = next(c for c in calls if c["name"] == "redraft-02")["prompt"]
        self.assertIn('section 2 "line 2.1"', prompt)
        # a whole-script flag that quotes a line keeps it where it is unresolved
        w = writer()
        whole = flag(n=0, section=0, criterion="train", quote="line 2.2",
                     problem="the train is never heard")
        faked(w, {f"review-r1-s{k}": sample("consider", flags=[whole]) for k in (1, 2, 3)})
        record = w.run(review=True, fix=False)["review"]
        self.assertIn('the whole script "line 2.2"',
                      script.story_flag_line(record["unresolved"][0]))
        warning = [f for f in w.story_findings(record) if "the train is never heard" in f]
        self.assertTrue(warning and '"line 2.2"' in warning[0], warning)

    def test_the_flag_line_shows_the_quote_and_the_line_it_contradicts(self):
        line = script.story_flag_line(flag(n=3, quote="line 2.1", criterion="honesty",
                                           conflicts_with={"n": 1, "quote": "line 1.1"}))
        self.assertIn('shot 3 "line 2.1"', line)
        self.assertIn('(contradicts shot 1: "line 1.1")', line)
        self.assertIn("[honesty, important]", line)
        plain = script.story_flag_line(flag(n=0, section=0, quote=""))
        self.assertTrue(plain.startswith("the whole script ["), plain)
        self.assertNotIn('""', plain)
        # script.md's "## Story review" reads it
        record = {"verdict": "consider", "samples": [],
                  "flags": [flag(n=3, quote="line 2.1",
                                 conflicts_with={"n": 1, "quote": "line 1.1"})]}
        md = "\n".join(script.story_review_lines(record))
        self.assertIn('"line 2.1" (contradicts shot 1: "line 1.1")', md)

    def test_revise_story_shows_each_flags_quote_and_the_conflicting_line(self):
        w = writer()
        flags = [flag(n=3, quote="line 2.1", criterion="honesty",
                      problem="it says the river rose",
                      conflicts_with={"n": 1, "quote": "line 1.1"}),
                 flag(n=2, quote="line 1.2", problem="the second line restates the first")]
        answers = consider_then(("recommend", "better"), flags=flags)
        calls = faked(w, answers)
        w.run(review=True)
        prompt = next(c for c in calls if c["name"] == "revise-story-1")["prompt"]
        problems = prompt.split("flagged by the story review", 1)[1]
        self.assertIn('"line 2.1"', problems)
        self.assertIn('contradicts shot 1: "line 1.1"', problems)
        self.assertIn('"line 1.2"', problems)
        self.assertIn('3. important: "line 2.1" (contradicts shot 1: "line 1.1"): '
                      'it says the river rose (suggested: open on the empty river)', problems)

    def test_another_revise_reads_its_flags_as_before(self):
        # only the story review's flags carry quotes into the prompt; the
        # fact-check's problem lines are what they were
        w = writer()
        calls = faked(w)
        shots = [script.Shot(narration=f"line 1.{i}", n=i, label=f"1.{i}", seconds=5)
                 for i in (1, 2)]
        w.revise(shots, [dict(flag(quote="line 1.1"), problem="p", fix="f")], name="revise")
        prompt = calls[-1]["prompt"]
        self.assertIn("\n1. important: p (suggested: f)\n", prompt)
        self.assertNotIn('"line 1.1"', prompt)


class ReviewLog(unittest.TestCase):
    def rnd(self):
        return {"round": 1, "grades": {}, "verdict": "consider",
                "samples": [{"name": "review-r1-s1", "again": False},
                            {"name": "review-r1-s2", "again": True},
                            {"name": "review-r1-s3", "again": False}]}

    def logged(self, cached: dict) -> int:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / script.REVIEW_LOG
            w = writer(review_log=log)
            w.harness.decisions[:] = [mock.Mock(cached=c) for c in cached.values()]
            for d, name in zip(w.harness.decisions, cached):
                d.name = name
            w._log_verdict(self.rnd())
            return len(log.read_text().splitlines()) if log.exists() else 0

    def test_a_round_is_replayed_only_when_every_decision_it_used_was_cached(self):
        every = {"review-r1-s1": True, "review-r1-s2": True, "review-r1-s2-again": True,
                 "review-r1-s3": True}
        self.assertEqual(self.logged(every), 0)
        # the sample was served, its again asked afresh: a new verdict
        self.assertEqual(self.logged(dict(every, **{"review-r1-s2-again": False})), 1)
        # a cached again does not stand for its sample asked afresh
        self.assertEqual(self.logged(dict(every, **{"review-r1-s2": False})), 1)
        self.assertEqual(self.logged(dict(every, **{"review-r1-s1": False,
                                                    "review-r1-s1-again": True})), 1)


def _failing_sample(failing):
    real = script.Screenwriter.story_sample

    def story_sample(self_, name, *a, **kw):
        if name in failing:
            return {"name": name, "valid": False, "failed": "the reviewer timed out",
                    "again": False}
        return real(self_, name, *a, **kw)
    return story_sample


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #

class Doctor(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name) / "work"
        self.root = self.work / "film"
        self.root.mkdir(parents=True)
        (self.root / "idea.json").write_text(json.dumps(
            {"title": "Why the bridge fell", "genre": "g", "description": "d"}))

    def write(self, findings, review):
        (self.root / "script.json").write_text(json.dumps({
            "target_seconds": 10, "findings": findings, "review": review, "sections": [
                {"heading": "S1", "shots": [{"label": "1.1", "narration": "x",
                                             "seconds": 10}]}]}))

    def test_a_reject_fails_under_script_with_the_fixing_command(self):
        self.write(["error: story review: reject (train Poor)"],
                   {"verdict": "reject", "grades": {"train": "Poor"}})
        report = doctor.check_project(workspace.Project(self.root)).findings
        fails = [f for f in report if f.area == "script" and f.level == "fail"]
        self.assertEqual(len(fails), 1, fails)
        self.assertIn("story review: reject", fails[0].message)
        self.assertIn("yt script --project", fails[0].fix)
        self.assertIn("--review", fails[0].fix)
        self.assertIn("Story review", fails[0].fix)

    def test_the_build_does_not_carry_a_reject_to_qa(self):
        # without --gate the reject is evidence for the operator, not a
        # blocker qa would lift into the delivery verdict
        from ytstudio import build
        found = build.script_errors({"findings": [
            "error: story review: reject (train Poor)", "error: rule:x: too long"]})
        self.assertEqual(found, ["error: script: rule:x: too long"])

    def test_the_review_log_is_named_as_evidence_to_copy_out(self):
        (self.work / script.REVIEW_LOG).write_text(
            json.dumps({"slug": "film", "verdict": "consider", "label": None}) + "\n"
            + json.dumps({"slug": "film", "verdict": "reject", "label": "reject"}) + "\n")
        report = doctor.Report("environment")
        doctor.review_log(report, self.work)
        notes = [f for f in report.findings if f.area == "review log"]
        self.assertEqual(len(notes), 1, report.findings)
        self.assertIn("2 verdict", notes[0].message)
        self.assertIn("1 labelled", notes[0].message)
        self.assertIn("copy", notes[0].fix + notes[0].message)


# --------------------------------------------------------------------------- #
# the viewer quiz, a measure beside the cold reader (AUTO-115)
# --------------------------------------------------------------------------- #

def quizzed(answers=None, *, quiz=QUIZ, before=BEFORE, after=AFTER):
    """`answers` with the viewer quiz's three decisions answered."""
    out = {"quiz": quiz, "quiz-before": before, "quiz-after": after}
    out.update(answers or {})
    return out


def failing(*_):
    raise ClaudeError("the reader timed out")


#: A narration line as the default fake writes it: `line 2.1`.
_NARRATION = re.compile(r"\bline \d\.\d\b")


class ViewerQuiz(unittest.TestCase):
    def test_the_quiz_is_written_from_the_plan_and_research_never_the_narration(self):
        plan = copy.deepcopy(PLAN)
        plan["questions"].append({"id": "Q3", "text": "LEFT-OPEN-QUESTION?", "opened_in": 3,
                                  "closed_in": 0, "left_open_why": "the next film"})
        w = writer(research="RESEARCH-ONLY-TEXT")
        calls = faked(w, quizzed({"plan": plan}))
        w.run(review=True)
        authors = [c for c in calls if c["name"] == "quiz"]
        self.assertEqual(len(authors), 1, names(calls))
        prompt = authors[0]["prompt"]
        for text in ("What brought the bridge down?", "Why did no inspection see it?",
                     "RESEARCH-ONLY-TEXT", PLAN["train"], PLAN["misconception"]):
            self.assertIn(text, prompt, text)
        # a question left open on purpose has no answer to key
        self.assertNotIn("LEFT-OPEN-QUESTION?", prompt)
        # the narration is what the quiz measures: written from it, it would
        # test the words against themselves
        self.assertIsNone(_NARRATION.search(prompt), prompt)
        self.assertEqual(authors[0]["schema"], script.QUIZ_SCHEMA)

    def test_the_quiz_taker_is_never_shown_the_key_or_where_an_item_came_from(self):
        rekeyed = copy.deepcopy(QUIZ)
        for item, key in zip(rekeyed["items"], "DCBA"):
            item["answer"], item["from"] = key, "ANOTHER-SOURCE"
        prompts = []
        for quiz in (QUIZ, rekeyed):
            w = writer(research="RESEARCH-ONLY-TEXT")
            calls = faked(w, quizzed(quiz=quiz))
            w.run(review=True)
            prompts.append({c["name"]: c["prompt"] for c in calls
                            if c["name"] in ("quiz-before", "quiz-after")})
        self.assertEqual(set(prompts[0]), {"quiz-before", "quiz-after"})
        # the same items keyed otherwise ask the reader exactly the same
        self.assertEqual(prompts[0], prompts[1])
        for prompt in prompts[0].values():
            self.assertIn("What held up the four lanes?", prompt)
            self.assertIn("one bolt", prompt)
            self.assertNotIn("QUIZ-FROM-CLAIM", prompt)

    def test_the_answers_are_scored_against_the_key_and_the_gain_recorded(self):
        w = writer()
        faked(w, quizzed())
        record = w.run(review=True)["review"]
        quiz = record["cold_read"]["quiz"]
        self.assertEqual(quiz["items"], QUIZ["items"])
        self.assertEqual((quiz["before"], quiz["after"], quiz["gain"], quiz["round"]),
                         (1, 3, 2, 1))
        self.assertEqual(quiz["missed_after"], [
            {"id": "K3", "question": "When does the twin bridge open?", "chose": "A",
             "key": "C", "from": "QUIZ-FROM-CLAIM-ONE"}])
        self.assertNotIn("quiz_failed", record["cold_read"])

    def test_an_item_left_unanswered_or_answered_off_the_letters_is_missed(self):
        after = {"answers": [{"id": " k1 ", "choice": "b"}, {"id": "K2", "choice": "E"},
                             {"id": "K4", "choice": "unsure"}, {"id": "K9", "choice": "C"}]}
        w = writer()
        faked(w, quizzed(after=after))
        quiz = w.run(review=True)["review"]["cold_read"]["quiz"]
        # a lower-case letter is the letter, and an id read as its item's
        # whatever its case or spaces; nothing else counts
        self.assertEqual(quiz["after"], 1)
        self.assertEqual([(m["id"], m["chose"]) for m in quiz["missed_after"]],
                         [("K2", "E"), ("K3", ""), ("K4", "unsure")])

    def test_a_bad_item_set_is_asked_again_once_then_recorded_as_failed(self):
        bad_sets = []
        dup = copy.deepcopy(QUIZ)
        dup["items"][0]["options"] = ["one bolt", "one bolt", "a pier", "the deck"]
        bad_sets.append(dup)
        same_id = copy.deepcopy(QUIZ)
        same_id["items"][1]["id"] = "K1"
        bad_sets.append(same_id)
        empty = copy.deepcopy(QUIZ)
        empty["items"][2]["options"][3] = " "
        bad_sets.append(empty)
        bad_sets.append({"items": QUIZ["items"][:3]})
        three = copy.deepcopy(QUIZ)
        three["items"][3]["options"] = three["items"][3]["options"][:3]
        bad_sets.append(three)
        keyless = copy.deepcopy(QUIZ)
        keyless["items"][0]["answer"] = "E"
        bad_sets.append(keyless)
        for bad in bad_sets:
            w = writer()
            calls = faked(w, quizzed({"quiz-again": QUIZ}, quiz=bad))
            record = w.run(review=True)["review"]
            self.assertEqual(names(calls).count("quiz-again"), 1, bad)
            again = next(c for c in calls if c["name"] == "quiz-again")
            self.assertEqual(again["schema"], script.QUIZ_SCHEMA)
            self.assertEqual(record["cold_read"]["quiz"]["items"], QUIZ["items"], bad)
        # a second bad set: no quiz, no exception, the review stands
        w = writer()
        calls = faked(w, quizzed({"quiz-again": dup}, quiz=dup))
        record = w.run(review=True)["review"]
        self.assertEqual(len(record["cold_read"]["quiz_failed"]), 1)
        self.assertTrue(record["cold_read"]["quiz_failed"][0].startswith("quiz:"))
        self.assertNotIn("quiz", record["cold_read"])
        self.assertFalse({"quiz-before", "quiz-after"} & set(names(calls)))
        self.assertEqual(record["verdict"], "recommend")
        self.assertEqual(record["cold_read"]["premise"], COLD["premise"])
        # a failed author call is the same
        w = writer()
        calls = faked(w, quizzed({"quiz": failing}))
        record = w.run(review=True)["review"]
        self.assertEqual(len(record["cold_read"]["quiz_failed"]), 1)
        self.assertIn("timed out", record["cold_read"]["quiz_failed"][0])
        self.assertNotIn("quiz-again", names(calls))

    def test_a_failed_quiz_before_still_scores_the_after(self):
        w = writer()
        faked(w, quizzed({"quiz-before": failing}))
        cold = w.run(review=True)["review"]["cold_read"]
        self.assertEqual([f.split(":")[0] for f in cold["quiz_failed"]], ["quiz-before"])
        self.assertEqual((cold["quiz"]["before"], cold["quiz"]["after"], cold["quiz"]["gain"]),
                         (None, 3, None))

    def test_both_quiz_calls_failing_are_both_recorded_and_the_before_kept_by_a_revision(self):
        w = writer()
        faked(w, quizzed({"quiz-before": failing, "quiz-after": failing}))
        cold = w.run(review=True)["review"]["cold_read"]
        self.assertEqual([f.split(":")[0] for f in cold["quiz_failed"]],
                         ["quiz-before", "quiz-after"])
        # a revision that stands: the before failed for good, the first
        # after is replaced by the second, which is scored
        w = writer()
        answers = consider_then(("recommend", "better"))
        answers["revise-story-1"] = {"fixes": [{"n": 1, "action": "fix",
                                                "narration": "The river is empty now.",
                                                "why": "opens on the picture"}]}
        right = {"answers": [{"id": i["id"], "choice": i["answer"]} for i in QUIZ["items"]]}
        faked(w, quizzed(dict(answers, **{"quiz-before": failing, "quiz-after": failing,
                                          "quiz-after-r2": right})))
        cold = w.run(review=True)["review"]["cold_read"]
        self.assertEqual([f.split(":")[0] for f in cold["quiz_failed"]], ["quiz-before"])
        self.assertIn("timed out", cold["quiz_failed"][0])
        self.assertEqual((cold["quiz"]["before"], cold["quiz"]["after"], cold["quiz"]["gain"],
                          cold["quiz"]["round"]), (None, 4, None, 2))

    def test_a_question_closed_in_no_readable_section_is_left_out_of_the_quiz(self):
        # a hand-edited or older plan: a bad `closed_in` leaves that question
        # out of the author's prompt, and the review and the quiz stand
        plan = copy.deepcopy(PLAN)
        for n, closed in enumerate(("s3", "2.1", None, [2], True), 1):
            plan["questions"].append({"id": f"QX{n}", "text": f"ODD-QUESTION-{n}?",
                                      "opened_in": 1, "closed_in": closed})
        w = writer()
        calls = faked(w, quizzed({"plan": plan}))
        record = w.run(review=True)["review"]
        prompt = next(c for c in calls if c["name"] == "quiz")["prompt"]
        self.assertIn("What brought the bridge down?", prompt)
        self.assertNotIn("ODD-QUESTION", prompt)
        self.assertEqual(record["cold_read"]["quiz"]["after"], 3)
        self.assertNotIn("quiz_failed", record["cold_read"])

    def test_a_revision_that_stands_is_quizzed_again_on_the_same_items(self):
        w = writer()
        answers = consider_then(("recommend", "better"))
        answers["revise-story-1"] = {"fixes": [{"n": 1, "action": "fix",
                                                "narration": "The river is empty now.",
                                                "why": "opens on the picture"}]}
        right = {"answers": [{"id": i["id"], "choice": i["answer"]} for i in QUIZ["items"]]}
        calls = faked(w, quizzed(dict(answers, **{"quiz-after-r2": right})))
        record = w.run(review=True)["review"]
        order = names(calls)
        for name, times in (("quiz", 1), ("quiz-before", 1), ("quiz-after", 1),
                            ("quiz-after-r2", 1)):
            self.assertEqual(order.count(name), times, (name, order))
        self.assertLess(order.index("cold-read-r2"), order.index("quiz-after-r2"))
        reread = next(c for c in calls if c["name"] == "quiz-after-r2")
        self.assertIn("The river is empty now.", reread["prompt"])
        quiz = record["cold_read"]["quiz"]
        self.assertEqual(record["cold_read"]["round"], 2)
        self.assertEqual(quiz["items"], QUIZ["items"])
        self.assertEqual((quiz["before"], quiz["after"], quiz["gain"], quiz["round"]),
                         (1, 4, 3, 2))
        self.assertEqual(quiz["missed_after"], [])
        # a revision put back is not quizzed again
        w = writer()
        answers = consider_then(("consider", "same"))
        answers["revise-story-1"] = {"fixes": [{"n": 1, "action": "fix",
                                                "narration": "A rewrite.", "why": "w"}]}
        calls = faked(w, quizzed(answers))
        record = w.run(review=True)["review"]
        self.assertNotIn("quiz-after-r2", names(calls))
        self.assertEqual(record["cold_read"]["quiz"]["round"], 1)

    def test_the_cold_reads_own_questions_are_asked_as_before(self):
        # the quiz shares the cold read's layout of the narration; the cold
        # read's prompts, and so its cached decisions, are what they were
        w = writer()
        calls = faked(w, quizzed())
        w.run(review=True)
        heard = ("The narration, shot by shot, each with its number:\n\n"
                 "1. line 1.1\n2. line 1.2\n3. line 2.1\n4. line 2.2\n5. line 3.1\n"
                 "6. line 3.2")
        read = next(c for c in calls if c["name"] == "cold-read")
        self.assertEqual(read["prompt"], heard + (
            "\n\nRestate the film's premise in your own words, list the terms a viewer "
            "needs and is never told, and for each major claim say why a viewer should "
            "care about it as the narration puts it."))
        after = next(c for c in calls if c["name"] == "quiz-after")
        self.assertTrue(after["prompt"].startswith(heard + "\n\n"), after["prompt"])

    def test_without_review_no_quiz_is_asked(self):
        w = writer()
        calls = faked(w, quizzed())
        data = w.run()
        self.assertFalse([n for n in names(calls) if n.startswith("quiz")])
        # and so no quiz warning (AUTO-162)
        self.assertFalse([f for f in data["findings"] if "viewer quiz" in f])

    def test_the_quiz_is_routed_to_its_own_reader_and_the_takers_to_the_cold_one(self):
        for name in ("quiz", "quiz-again"):
            self.assertTrue(script._QUIZ_NAME.match(name), name)
        for name in ("quiz-before", "quiz-after", "quiz-after-r2", "cold-read",
                     "cold-read-answers-r3"):
            self.assertTrue(script._COLD_NAME.match(name), name)
            self.assertFalse(script._QUIZ_NAME.match(name), name)


class ViewerQuizReader(EngineCase):
    def test_the_quiz_taker_is_the_cold_reader_and_knows_only_the_words(self):
        (self.root / "feedback").mkdir()
        (self.root / "feedback" / "script.md").write_text("FEEDBACK-ONLY-TEXT")
        w = script.Screenwriter(idea(), root=self.root, verbose=False, minutes=1, jobs=1,
                                model="sonnet", reviewer_model="opus",
                                house="HOUSE-ONLY-TEXT", research="RESEARCH-ONLY-TEXT")
        data = w.run(review=True)
        cold = next(c for c in Engine.seen if c["kind"] == "cold-read")
        author = [c for c in Engine.seen if c["kind"] == "quiz"]
        taken = [c for c in Engine.seen if c["kind"] == "quiz-answers"]
        self.assertEqual(len(author), 1)
        self.assertEqual(len(taken), 2)
        before, after = taken
        self.assertIn(script.QUIZ_BEFORE, before["prompt"])
        self.assertNotIn(script.QUIZ_BEFORE, after["prompt"])
        self.assertEqual({c["model"] for c in author + taken}, {"opus"})
        # one reader: the cold reader's system, and no house or notes in it
        for c in taken:
            self.assertEqual(c["system"], cold["system"])
        self.assertNotIn("line 1.1", before["prompt"])
        self.assertIn("line 1.1", after["prompt"])
        private = ("HOUSE-ONLY-TEXT", "RESEARCH-ONLY-TEXT", "FEEDBACK-ONLY-TEXT",
                   PLAN["train"], PLAN["misconception"], PLAN["stakes"], PLAN["logline"],
                   "QUIZ-FROM-CLAIM", *(q["text"] for q in PLAN["questions"]))
        for c in taken:
            for text in private:
                self.assertNotIn(text, c["prompt"] + c["system"], text)
        # the author has its own role, and is never the cold reader; it is
        # shown the research and no house or notes
        self.assertIn(script.QUIZ_ROLE, author[0]["system"])
        self.assertIn("RESEARCH-ONLY-TEXT", author[0]["prompt"])
        for text in ("HOUSE-ONLY-TEXT", "FEEDBACK-ONLY-TEXT"):
            self.assertNotIn(text, author[0]["system"] + author[0]["prompt"], text)
        self.assertNotEqual(author[0]["system"], cold["system"])
        quiz = data["review"]["cold_read"]["quiz"]
        self.assertEqual((quiz["before"], quiz["after"], quiz["gain"]), (1, 3, 2))
        for name in ("quiz", "quiz-before", "quiz-after"):
            self.assertTrue((self.root / "decisions" / "script" / f"{name}.json").is_file(),
                            name)


#: Eight items, every one keyed A, for the gain-0 and gain-8 runs.
QUIZ8 = {"items": [{"id": f"K{i}", "question": f"Question {i}?",
                    "options": [f"right {i}", f"wrong {i}a", f"wrong {i}b", f"wrong {i}c"],
                    "answer": "A", "from": "Q1"} for i in range(1, 9)]}
ALL_RIGHT = {"answers": [{"id": f"K{i}", "choice": "A"} for i in range(1, 9)]}
ALL_UNSURE = {"answers": [{"id": f"K{i}", "choice": "unsure"} for i in range(1, 9)]}


class ViewerQuizCommand(unittest.TestCase):
    """The quiz through `yt script`: it is in script.json and script.md, and
    no verdict, flag, gate or exit code moves with it."""
    setUp = Command.setUp
    run_stage = Command.run_stage
    rejected = Command.rejected

    def outcome(self, *flags, answers):
        for path in self.film.iterdir():
            if path.name != "idea.json":
                (shutil.rmtree if path.is_dir() else Path.unlink)(path)
        code, asked, said = self.run_stage(*flags, answers=answers)
        record = json.loads((self.film / "script.json").read_text())
        narration = self.film / "narration.txt"
        review = record["review"]
        return {"code": code, "asked": asked, "verdict": review["verdict"],
                "flags": review["flags"],
                "unresolved": review["unresolved"], "findings": record["findings"],
                "narration": narration.read_text() if narration.exists() else None,
                "quiz": review["cold_read"].get("quiz"),
                "quiz_failed": review["cold_read"].get("quiz_failed"),
                "md": (self.film / "script.md").read_text()}

    def test_a_failed_quiz_after_changes_no_verdict_flag_or_narration(self):
        for base in ({}, consider_then(("consider", "same"))):
            fine = self.outcome("--review", answers=quizzed(base))
            failed = self.outcome("--review", answers=quizzed(dict(base, **{
                "quiz-after": failing})))
            self.assertIsNone(fine["quiz_failed"])
            self.assertEqual([f.split(":")[0] for f in failed["quiz_failed"]],
                             ["quiz-after"])
            self.assertIsNone(failed["quiz"]["after"])
            for key in ("code", "verdict", "flags", "unresolved", "findings", "narration"):
                self.assertEqual(fine[key], failed[key], key)

    def test_the_gain_moves_no_verdict_and_no_gate(self):
        # The gain-0 run is the ceiling case: a reader right on all eight
        # before and after has nothing missed, so no warning is raised
        # (AUTO-162) and the findings stay equal.
        for flags, answers, code in ((("--review",), {}, 0),
                                     (("--review", "--gate"), {}, 0),
                                     (("--review", "--gate"), self.rejected(), 1)):
            none = self.outcome(*flags, answers=quizzed(answers, quiz=QUIZ8,
                                                        before=ALL_RIGHT, after=ALL_RIGHT))
            eight = self.outcome(*flags, answers=quizzed(answers, quiz=QUIZ8,
                                                         before=ALL_UNSURE, after=ALL_RIGHT))
            self.assertEqual((none["quiz"]["gain"], eight["quiz"]["gain"]), (0, 8))
            self.assertEqual(none["code"], code, flags)
            for key in ("code", "verdict", "flags", "unresolved", "findings", "narration"):
                self.assertEqual(none[key], eight[key], (flags, key))

    def test_a_low_gain_is_one_warning_and_moves_nothing_else(self):
        # AUTO-162: five right after a reader that knew all eight before is
        # a gain of -3 with three facts untaught; the same film with a gain
        # of 8 differs from it by that one warning line and nothing else
        low_after = {"answers": [{"id": f"K{i}", "choice": "A" if i <= 5 else "unsure"}
                                 for i in range(1, 9)]}
        for flags, answers, code in ((("--review",), {}, 0),
                                     (("--review", "--gate"), {}, 0),
                                     (("--review", "--gate"), self.rejected(), 1)):
            low = self.outcome(*flags, answers=quizzed(answers, quiz=QUIZ8,
                                                       before=ALL_RIGHT, after=low_after))
            high = self.outcome(*flags, answers=quizzed(answers, quiz=QUIZ8,
                                                        before=ALL_UNSURE, after=ALL_RIGHT))
            self.assertEqual((low["quiz"]["gain"], high["quiz"]["gain"]), (-3, 8))
            self.assertEqual((low["code"], high["code"]), (code, code), flags)
            warned = [f for f in low["findings"]
                      if f.startswith("warning: story review: viewer quiz")]
            self.assertEqual(len(warned), 1, low["findings"])
            for text in ("gain -3 under 1", '"Question 6?" = "right 6"',
                         '"Question 8?" = "right 8"'):
                self.assertIn(text, warned[0], text)
            self.assertEqual([f for f in low["findings"] if f not in warned],
                             high["findings"], flags)
            self.assertFalse([f for f in high["findings"] if "viewer quiz" in f])
            for key in ("code", "verdict", "flags", "unresolved", "narration"):
                self.assertEqual(low[key], high[key], (flags, key))
            # no call is added: the sections are drafted on a pool, so the
            # order of their calls is the pool's
            self.assertEqual(sorted(low["asked"]), sorted(high["asked"]), flags)
            # the same line is in script.md's Findings, before voice
            findings = low["md"][low["md"].index("## Findings"):]
            self.assertIn("- `warning` " + warned[0].split(": ", 1)[1], findings)
            self.assertNotIn("viewer quiz gain", high["md"])

    def test_the_low_gain_never_reaches_a_revise_prompt(self):
        # what the quiz missed is a warning for the operator, never a flag:
        # no revise-story, redraft or revise-ledger prompt is shown it
        low_after = {"answers": [{"id": "K4", "choice": "B"}]}
        missed = [i["question"] for i in QUIZ["items"][:3]]
        for second in (("consider", "same"), ("recommend", "better")):
            answers = consider_then(second)
            answers["revise-story-1"] = {"fixes": [{"n": 1, "action": "fix",
                                                    "narration": "The river is empty now.",
                                                    "why": "opens on the picture"}]}
            # when the revision stands, round 1 taught everything and the
            # re-ask taught nothing, so the one warning can only come from
            # the re-asked quiz
            stands = second[1] == "better"
            w = writer()
            calls = faked(w, quizzed(dict(answers, **{"quiz-after-r2": low_after}),
                                     after=ALL_KEYED if stands else low_after))
            data = w.run(review=True)
            self.assertEqual(data["review"]["cold_read"]["quiz"]["gain"], 0, second)
            warned = [f for f in data["findings"] if "viewer quiz" in f]
            self.assertEqual(len(warned), 1, data["findings"])
            self.assertEqual("round 2" in warned[0], stands, warned)
            revises = [c for c in calls
                       if c["name"].startswith(("revise-story", "revise-ledger", "redraft"))]
            self.assertIn("revise-story-1", names(revises))
            for c in revises:
                for text in ("viewer quiz", "Viewer quiz", *missed):
                    self.assertNotIn(text, c["prompt"], (c["name"], text))
            self.assertFalse([f for f in data["review"]["unresolved"]
                              if "quiz" in json.dumps(f)])
            # every other decision is asked word for word as with a high
            # gain, so its cache key is what it was: the warning adds no call
            w = writer()
            high = faked(w, quizzed(dict(answers, **{"quiz-after-r2": ALL_KEYED}),
                                    before={"answers": []}, after=ALL_KEYED))
            w.run(review=True)
            self.assertEqual([(c["name"], c["prompt"]) for c in calls
                              if not c["name"].startswith("quiz")],
                             [(c["name"], c["prompt"]) for c in high
                              if not c["name"].startswith("quiz")], second)

    def test_a_reasked_quiz_that_teaches_silences_round_one(self):
        # the reverse: round 1 taught nothing, the revision that stands
        # teaches every fact, and the warning goes with the replaced draft
        low_after = {"answers": [{"id": "K4", "choice": "B"}]}
        answers = consider_then(("recommend", "better"))
        answers["revise-story-1"] = {"fixes": [{"n": 1, "action": "fix",
                                                "narration": "The river is empty now.",
                                                "why": "opens on the picture"}]}
        w = writer()
        faked(w, quizzed(dict(answers, **{"quiz-after-r2": ALL_KEYED}), after=low_after))
        data = w.run(review=True)
        quiz = data["review"]["cold_read"]["quiz"]
        self.assertEqual((quiz["round"], quiz["missed_after"]), (2, []), quiz)
        self.assertFalse([f for f in data["findings"] if "viewer quiz" in f],
                         data["findings"])

    def test_script_md_shows_the_quiz_as_a_measure(self):
        self.run_stage("--review", answers=quizzed())
        md = (self.film / "script.md").read_text()
        story = md[md.index("## Story review"):]
        line = next(l for l in story.splitlines() if l.startswith("Viewer quiz"))
        self.assertIn("1 of 4", line)
        self.assertIn("3 of 4", line)
        self.assertIn("gain 2", line)
        self.assertIn("measure", line)
        self.assertIn("gates nothing", line)
        self.assertIn("prior", line)
        self.assertIn("K3", story)
        self.assertIn("When does the twin bridge open?", story)
        # a failed quiz says so, and the review stands
        self.run_stage("--review", answers=quizzed({"quiz": failing}))
        md = (self.film / "script.md").read_text()
        self.assertIn("Viewer quiz failed", md)


# --------------------------------------------------------------------------- #
# a low quiz gain is one warning (AUTO-162)
# --------------------------------------------------------------------------- #

def scored(before, after, *, quiz=QUIZ, round_no=1):
    """A cold read's record holding `quiz` taken with `before` and `after`,
    scored as viewer_quiz scores them."""
    items = copy.deepcopy(quiz["items"])
    b = script.quiz_score(items, before)[0]
    a, missed = script.quiz_score(items, after)
    return {"quiz": {"items": items, "before": b, "after": a, "gain": a - b,
                     "missed_after": missed, "round": round_no}}


#: Two right before (K1, K4), two right after (K1, K2): gain 0 with K3
#: and K4 missed after hearing.
TWO_BEFORE = {"answers": [{"id": "K1", "choice": "B"}, {"id": "K2", "choice": "unsure"},
                          {"id": "K3", "choice": "unsure"}, {"id": "K4", "choice": "B"}]}
TWO_AFTER = {"answers": [{"id": "K1", "choice": "B"}, {"id": "K2", "choice": "B"},
                         {"id": "K3", "choice": "A"}, {"id": "K4", "choice": "unsure"}]}
ALL_KEYED = {"answers": [{"id": i["id"], "choice": i["answer"]} for i in QUIZ["items"]]}


class QuizFindings(unittest.TestCase):
    def test_a_gain_under_the_floor_with_missed_facts_warns_with_the_keyed_text(self):
        cold = scored(TWO_BEFORE, TWO_AFTER)
        self.assertEqual((cold["quiz"]["before"], cold["quiz"]["after"]), (2, 2))
        lines = script.quiz_findings(cold)
        self.assertEqual(len(lines), 1, lines)
        line = lines[0]
        self.assertTrue(line.startswith(
            f"warning: story review: viewer quiz gain 0 under {script.QUIZ_GAIN_FLOOR}"),
            line)
        self.assertIn("(2 of 4 before, 2 after)", line)
        for text in ("K3", "When does the twin bridge open?", '"in a month"',
                     "QUIZ-FROM-CLAIM-ONE", "K4", '"40 years"', "QUIZ-FROM-CLAIM-TWO",
                     '`yt script --review --notes "teach: '):
            self.assertIn(text, line, text)
        # the gain and the first fact come first: doctor clips the text
        # after `warning: ` at 160 characters
        self.assertLess(line.index('"in a month"') + len('"in a month"'),
                        160 + len("warning: "), line)
        self.assertNotIn("round", line)

    def test_the_floor_is_one_question(self):
        self.assertEqual(script.QUIZ_GAIN_FLOOR, 1)

    def test_a_gain_at_or_over_the_floor_is_silent(self):
        cold = scored(BEFORE, AFTER)
        self.assertEqual(cold["quiz"]["gain"], 2)
        self.assertTrue(cold["quiz"]["missed_after"])
        self.assertEqual(script.quiz_findings(cold), [])
        # exactly the floor: one question more taught than known is enough
        one_after = copy.deepcopy(BEFORE)
        one_after["answers"][0]["choice"] = "B"
        cold = scored(BEFORE, one_after)
        self.assertEqual(cold["quiz"]["gain"], script.QUIZ_GAIN_FLOOR)
        self.assertTrue(cold["quiz"]["missed_after"])
        self.assertEqual(script.quiz_findings(cold), [])

    def test_a_ceiling_score_is_silent(self):
        # a reader that knew it all before has nothing left to be taught
        cold = scored(ALL_KEYED, ALL_KEYED)
        self.assertEqual((cold["quiz"]["before"], cold["quiz"]["after"],
                          cold["quiz"]["gain"], cold["quiz"]["missed_after"]), (4, 4, 0, []))
        self.assertEqual(script.quiz_findings(cold), [])

    def test_a_negative_gain_warns(self):
        before = copy.deepcopy(ALL_KEYED)
        before["answers"][2]["choice"] = "unsure"           # 3 of 4
        after = {"answers": [{"id": "K1", "choice": "B"}]}  # 1 of 4
        cold = scored(before, after)
        self.assertEqual(cold["quiz"]["gain"], -2)
        [line] = script.quiz_findings(cold)
        self.assertTrue(line.startswith("warning: story review: viewer quiz gain -2 under 1 "
                                        "(3 of 4 before, 1 after)"), line)
        for text in ('"paint"', '"in a month"', '"40 years"'):
            self.assertIn(text, line, text)

    def test_a_failed_before_or_after_is_silent(self):
        cold = scored(TWO_BEFORE, TWO_AFTER)
        for key in ("before", "after"):
            failed = copy.deepcopy(cold)
            failed["quiz"].update({key: None, "gain": None})
            failed["quiz_failed"] = [f"quiz-{key}: the reader timed out"]
            self.assertEqual(script.quiz_findings(failed), [], key)

    def test_no_quiz_is_silent(self):
        for cold in ({}, {"quiz_failed": ["quiz: the author timed out"]}, None, "x",
                     {"quiz": {"items": [], "gain": -1, "missed_after": [{"id": "K1"}]}}):
            self.assertEqual(script.quiz_findings(cold), [], cold)

    def test_an_unresolvable_key_falls_back_to_the_letter(self):
        cold = scored(TWO_BEFORE, TWO_AFTER)
        cold["quiz"]["missed_after"][0]["key"] = "E"
        cold["quiz"]["missed_after"][1]["id"] = "K9"
        [line] = script.quiz_findings(cold)
        self.assertIn("keyed E", line)
        self.assertIn("keyed B", line)
        self.assertNotIn('"in a month"', line)
        self.assertNotIn('"40 years"', line)
        # an item with too few options, or none at all, never raises
        broken = copy.deepcopy(cold)
        broken["quiz"]["missed_after"][0]["key"] = "D"
        broken["quiz"]["items"][2]["options"] = ["only one"]
        self.assertIn("keyed D", script.quiz_findings(broken)[0])
        broken["quiz"]["items"][2]["options"] = None
        broken["quiz"]["missed_after"].append("not a dict")
        self.assertIn("keyed D", script.quiz_findings(broken)[0])

    def test_the_from_part_appears_only_when_named(self):
        cold = scored(TWO_BEFORE, TWO_AFTER)
        for m in cold["quiz"]["missed_after"]:
            m["from"] = ""
        [line] = script.quiz_findings(cold)
        self.assertNotIn("(from", line)

    def test_a_revision_that_stands_reads_the_reasked_quiz(self):
        cold = scored(TWO_BEFORE, TWO_AFTER, round_no=2)
        [line] = script.quiz_findings(cold)
        self.assertIn("(2 of 4 before, 2 after, round 2)", line)

    def test_story_findings_carries_it_after_the_cold_read_lines(self):
        w = writer()
        cold = dict(scored(TWO_BEFORE, TWO_AFTER), failed="the reader timed out")
        out = w.story_findings({"verdict": "recommend", "grades": {}, "unresolved": [],
                                "cold_read": cold})
        self.assertEqual(len(out), 2, out)
        self.assertTrue(out[0].startswith("warning: story review: the cold read failed"))
        self.assertEqual(out[1:], script.quiz_findings(cold))
        self.assertEqual(w.story_findings({"verdict": "recommend", "grades": {}}), [])


if __name__ == "__main__":
    unittest.main()
