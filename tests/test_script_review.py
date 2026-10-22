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
    return ""


def sample(verdict="recommend", grades=None, flags=(), previous=None):
    g = {k: "Good" for k in narrative.KEYS}
    g.update(grades or {})
    out = {"logline": "a bridge that should have stood", "synopsis": "it falls; why",
           "grades": g, "verdict": verdict, "flags": [dict(f) for f in flags],
           "first_minute": {"promise_match": "yes", "question_and_stakes": "why it fell"}}
    if previous:
        out["previous"] = {"verdict": previous, "findings": []}
    return out


def flag(n=1, section=1, criterion="hook", severity="important",
         problem="the hook lists its conclusions", fix="open on the empty river"):
    return {"n": n, "section": section, "criterion": criterion, "problem": problem,
            "severity": severity, "fix": fix}


COLD = {"premise": "A bridge fell because of one bolt nobody could see.",
        "unexplained_terms": ["gusset"],
        "claims": [{"claim": "one bolt held four lanes", "why_care": "you drive on one"}]}
ANSWERS = {"answered": [{"question_id": "Q1", "shot_index": 5}]}

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
    out.update({f"review-r2-s{k}": sample(verdict, previous=previous,
                                          flags=flags if verdict == "consider" else ())
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
            return sample("recommend")
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
                return copy.deepcopy(answers[name])
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

    def test_fresh_asks_every_decision_again(self):
        seen = {}
        real = script.Screenwriter.__init__

        def init(self_, *a, **kw):
            real(self_, *a, **kw)
            seen["fresh"] = [h.fresh for h in (self_.harness, self_._drafter)]
        with mock.patch.object(script.Screenwriter, "__init__", init):
            self.run_stage("--fresh")
        self.assertEqual(seen["fresh"], [True, True])


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


if __name__ == "__main__":
    unittest.main()
