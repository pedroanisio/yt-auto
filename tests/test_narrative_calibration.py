"""AUTO-97: the story reviewer is checked before it is trusted as a gate.

judging 9 asks for the reviewer to be calibrated on human-labelled scripts of
the channel's own before it gates anything, with a length-bias audit;
judging 10 asks for known-bad scripts it must fail; judging 11 for the
majority of several runs. `yt script --review --gate` (AUTO-93) gates as if
that had been done. This is the check:

- tests/fixtures/narrative/ is the floor of the calibration set: one case
  per seeded fault the card names, each `expected.json` naming the rubric
  criterion the fault is graded on and the best verdict a correct reviewer
  may give it, and one good plan and script that expects recommend (with a
  padded copy of the same script for the length audit);
- `python -m ytstudio.narrative calibrate <dir>` runs the plan review, the
  three-sample story review and the cold read over each case and prints a
  table (fixture, expected, got, pass/fail) and the length audit;
- `yt doctor` warns when a script.json was written with `--gate` while
  work/review-log.jsonl holds fewer than 20 labelled verdicts, and `yt run`
  never passes `--gate`.

No model is called: `Screenwriter._ask` is answered from each case's own
expected review, and `harness.ENGINE` is an engine that fails the test if
anything reaches it.
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
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ytstudio import cli, doctor, harness, narrative, script, workspace  # noqa: E402

from tests.test_narrative_rubric import FIXTURES, load, problems  # noqa: E402
from tests import test_script_review as review_tests  # noqa: E402

#: The seeded faults the story review catches, each with the criterion it
#: is graded on.
SEEDED = {"list-plan": "train", "topic-train": "train", "empty-payoff": "ending",
          "and-chain": "train", "reveal-first": "reveal_setup",
          "recap-ending": "ending", "hook-enumerates": "hook",
          "flat-tension": "tension", "no-trigger": "hook", "lecture-voice": "voice"}

#: The seeded faults only the cold read catches (judging 14): the story
#: review is read on them and not judged.
COLD_READ = ("no-premise", "jargon")


def first_names(terms) -> list[str]:
    """Each seeded term by its first name: an entry is a term or the list of
    names a reader may give it."""
    return [t if isinstance(t, str) else t[0] for t in terms or []]


def cases() -> dict[str, Path]:
    return {p.name: p for p in sorted(FIXTURES.iterdir()) if p.is_dir()}


class NoEngine:
    """Fails the test the moment a decision would reach a model."""

    def __init__(self, config):
        raise AssertionError("a calibration test reached the engine")


def answered_from_fixtures(override=None):
    """A `Screenwriter._ask` that answers each case from its expected.json:
    the work directory a case is read in is named after it (`<case>` or
    `<case>-padded`). `override(case, kind)` may return another answer."""
    asked: list[tuple[str, str]] = []

    def ask(self_, name, prompt, schema):
        case = Path(self_.root).name
        base = case.removesuffix("-padded")
        expected = load(FIXTURES / base / "expected.json")
        props = schema["properties"]
        kind = ("review" if "grades" in props else "cold-read" if "premise" in props
                else "answers" if "answered" in props
                else "plan-review" if "problems" in props else "match")
        asked.append((case, name))
        if override is not None:
            got = override(case, kind)
            if got is not None:
                return copy.deepcopy(got)
        if kind == "review":
            return copy.deepcopy(expected["review"])
        if kind == "plan-review":
            return copy.deepcopy(expected["plan_review"])
        cold = expected.get("cold_read") or {}
        if kind == "cold-read":
            return {"premise": "a comet's tail and what pushes it",
                    "unexplained_terms": first_names(cold.get("terms")), "claims": []}
        if kind == "answers":
            return {"answered": []}
        return {"verdict": cold.get("match", "match"),
                "difference": "" if cold.get("match", "match") == "match" else "no question"}
    return ask, asked


class Hermetic(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name) / "work"
        patcher = mock.patch.object(harness, "ENGINE", NoEngine)
        patcher.start()
        self.addCleanup(patcher.stop)

    def calibrate(self, override=None, **kw):
        ask, asked = answered_from_fixtures(override)
        with mock.patch.object(script.Screenwriter, "_ask", ask):
            report = narrative.calibrate(FIXTURES, work=self.work, model="sonnet",
                                         reviewer_model="opus", verbose=False, **kw)
        return report, asked

    def main(self, *argv, override=None):
        ask, _ = answered_from_fixtures(override)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(script.Screenwriter, "_ask", ask), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = narrative.main(["calibrate", str(FIXTURES), "--work", str(self.work),
                                   "--quiet", *argv])
        return code, out.getvalue(), err.getvalue()


# --------------------------------------------------------------------------- #
# the set
# --------------------------------------------------------------------------- #

class TheSet(unittest.TestCase):
    def test_every_seeded_fault_and_one_good_pair(self):
        names = cases()
        self.assertEqual(sorted(names), sorted([*SEEDED, *COLD_READ, "good"]))
        for name, path in names.items():
            with self.subTest(case=name):
                expected = load(path / "expected.json")
                if name in COLD_READ:
                    self.assertIs(expected["story"], False)
                    self.assertNotIn("criterion", expected)
                    continue
                self.assertIn(expected["verdict"], narrative.VERDICTS)
                if name == "good":
                    self.assertEqual(expected["verdict"], "recommend")
                    self.assertNotIn("criterion", expected)
                    continue
                self.assertIn(expected["criterion"], narrative.KEYS)
                self.assertEqual(expected["criterion"], SEEDED[name])
                self.assertNotEqual(expected["verdict"], "recommend")

    def test_every_expected_failure_is_expressible_and_passes_its_own_check(self):
        # The review each case carries is one the schema and the rubric
        # accept, and the calibration's own judgement calls it caught.
        for name, path in cases().items():
            expected = load(path / "expected.json")
            said = expected["review"]
            with self.subTest(case=name):
                self.assertEqual(problems(said, narrative.REVIEW_SCHEMA), [])
                self.assertEqual(narrative.check_review(said), [])
                got = {"verdict": said["verdict"], "grades": said["grades"],
                       "flags": said["flags"],
                       # the round's heard trigger: not_heard is not_evidenced
                       "trigger": script.heard_trigger([said, said])}
                if "plan_review" in expected:
                    got["plan_review"] = expected["plan_review"]["verdict"]
                if "cold_read" in expected:
                    cold = expected["cold_read"]
                    got["cold_read"] = {"match": cold.get("match", "match"),
                                        "unexplained_terms": first_names(cold.get("terms"))}
                self.assertEqual(narrative.judge_case(expected, got), [])

    def test_every_flag_quotes_words_its_script_says(self):
        # AUTO-112: a case's review is read over its script and its padded
        # copy, so each quote is in both; a plan-only case has no words yet
        for name, path in cases().items():
            said = load(path / "expected.json")["review"]
            for copy_name in ("script.json", "padded.json"):
                if not (path / copy_name).is_file() and copy_name == "padded.json":
                    continue
                sections = (script.sections_of(load(path / copy_name))
                            if (path / copy_name).is_file() else [])
                w = script.Screenwriter(script.Idea(title="t", genre="g", description="d"),
                                        verbose=False, review_log=None)
                shots = w._renumber(sections)
                with self.subTest(case=name, copy=copy_name):
                    self.assertEqual(script.Screenwriter._sample_problems(
                        said, narrative.REVIEW_SCHEMA, {s.n: s.narration for s in shots},
                        {i: [s.n for s in sec.shots]
                         for i, sec in enumerate(sections, 1)}), [])
                    for f in said["flags"]:
                        if f["n"] or (f["section"] and sections):
                            self.assertTrue(f["quote"].strip(), f)

    def test_a_new_case_is_held_below_reject_only_with_its_reason(self):
        for name in ("and-chain", "no-premise", "jargon"):
            expected = load(FIXTURES / name / "expected.json")
            with self.subTest(case=name):
                self.assertTrue(expected["why"].strip())
                if name in COLD_READ:
                    # why the story review is not judged: judging 14
                    self.assertIn("judging 14", expected["judging_10"])
                    self.assertIn("cold read", expected["judging_10"])
                elif expected["verdict"] != "reject":
                    self.assertIn("judging 10", expected["judging_10"])
                    self.assertIn("recommend", expected["judging_10"])

    def test_the_cold_read_cases_say_what_the_cold_reader_must_find(self):
        self.assertEqual(load(FIXTURES / "no-premise" / "expected.json")["cold_read"],
                         {"match": "mismatch"})
        terms = first_names(load(FIXTURES / "jargon" / "expected.json")["cold_read"]["terms"])
        self.assertGreaterEqual(len(terms), 2)
        narration = " ".join(s["narration"] for sec in
                             load(FIXTURES / "jargon" / "script.json")["sections"]
                             for s in sec["shots"]).lower()
        good = " ".join(s["narration"] for sec in
                        load(FIXTURES / "good" / "script.json")["sections"]
                        for s in sec["shots"]).lower()
        for term in terms:
            self.assertIn(term.lower(), narration, term)
            self.assertNotIn(term.lower(), good, term)
        self.assertEqual(load(FIXTURES / "jargon" / "plan.json")["audience"], "lay")
        self.assertEqual(load(FIXTURES / "good" / "expected.json")["cold_read"],
                         {"match": "match"})

    def test_each_padded_script_is_its_case_longer_with_the_same_content(self):
        padded_cases = [n for n, p in cases().items() if (p / "padded.json").is_file()]
        self.assertIn("good", padded_cases)
        # A pair read at the top verdict with every grade Excellent cannot be
        # won by the longer copy, so a padded case below recommend is what
        # gives the audit room to see a bias.
        self.assertTrue([n for n in padded_cases
                         if load(FIXTURES / n / "expected.json").get("verdict") == "consider"],
                        padded_cases)
        for name in padded_cases:
            with self.subTest(case=name):
                shorter = load(FIXTURES / name / "script.json")
                padded = load(FIXTURES / name / "padded.json")
                self.assertEqual(padded["idea"], shorter["idea"])
                self.assertEqual([s["heading"] for s in padded["sections"]],
                                 [s["heading"] for s in shorter["sections"]])
                short = [s for sec in shorter["sections"] for s in sec["shots"]]
                long = [s for sec in padded["sections"] for s in sec["shots"]]
                self.assertEqual([s["n"] for s in long], [s["n"] for s in short])
                for a, b in zip(short, long):
                    # the same figures said, and the same picture
                    self.assertEqual(script.figures_in(b["narration"]),
                                     script.figures_in(a["narration"]))
                    self.assertEqual(b["visual"], a["visual"])
                words = lambda shots: sum(len(s["narration"].split()) for s in shots)  # noqa: E731
                self.assertGreaterEqual(words(long), 1.5 * words(short))
        # the recap is still a recap at its longer length: the fault is kept
        last = load(FIXTURES / "recap-ending" / "padded.json")["sections"][-1]["shots"][-1]
        self.assertIn("recap", last["narration"].lower())


# --------------------------------------------------------------------------- #
# judging one case, and the length audit
# --------------------------------------------------------------------------- #

def got_for(name: str, **change) -> dict:
    said = load(FIXTURES / name / "expected.json")["review"]
    out = {"verdict": said["verdict"], "grades": dict(said["grades"]),
           "flags": list(said["flags"])}
    out.update(change)
    return out


class JudgeCase(unittest.TestCase):
    def test_a_cold_read_case_does_not_judge_the_story_review(self):
        # The rubric has no criterion for a premise the narration never
        # voices or for jargon; the stage leaves the story verdict alone and
        # files the cold read's mismatch itself. Whatever the story review
        # says here, only the cold read is held to the case.
        for name in COLD_READ:
            expected = load(FIXTURES / name / "expected.json")
            cold = expected["cold_read"]
            read = {"match": cold.get("match", "match"),
                    "unexplained_terms": first_names(cold.get("terms"))}
            with self.subTest(case=name):
                for story in (got_for("good"), {"verdict": "not_evidenced"},
                              got_for("good", verdict="consider")):
                    self.assertEqual(narrative.judge_case(expected,
                                                          dict(story, cold_read=read)), [])
        expected = load(FIXTURES / "no-premise" / "expected.json")
        failed = narrative.judge_case(expected, dict(got_for("good"),
                                                     cold_read={"match": "match"}))
        self.assertEqual(len(failed), 1, failed)
        self.assertIn("cold read", failed[0])

    def test_a_reader_may_name_a_term_in_its_own_words(self):
        # An exact phrase would fail a reader for a correct reading: the
        # case lists the names a reader may give each term.
        jargon = load(FIXTURES / "jargon" / "expected.json")
        terms = jargon["cold_read"]["terms"]
        self.assertTrue(any(isinstance(t, list) and len(t) > 1 for t in terms), terms)
        said = ["Plasma", "IMF", "the magnetic field between the planets",
                "corpuscular radiation (what is it?)"]
        self.assertEqual(narrative.judge_case(jargon, {"cold_read": {
            "match": "match", "unexplained_terms": said}}), [])
        failed = narrative.judge_case(jargon, {"cold_read": {
            "match": "match", "unexplained_terms": ["plasma"]}})
        self.assertEqual(len(failed), 1, failed)
        self.assertIn("interplanetary magnetic field", failed[0])
        self.assertIn("corpuscular radiation", failed[0])

    def test_a_recommend_on_a_seeded_case_fails_the_reviewer(self):
        for name in SEEDED:
            expected = load(FIXTURES / name / "expected.json")
            with self.subTest(case=name):
                failed = narrative.judge_case(expected, got_for(name, verdict="recommend"))
                self.assertTrue(failed)
                self.assertIn("recommend", failed[0])

    def test_a_verdict_better_than_the_case_allows_fails(self):
        expected = load(FIXTURES / "topic-train" / "expected.json")       # reject
        failed = narrative.judge_case(expected, got_for("topic-train", verdict="consider"))
        self.assertTrue(any("consider" in line and "reject" in line for line in failed), failed)

    def test_the_named_criterion_must_be_graded_down_and_flagged(self):
        expected = load(FIXTURES / "hook-enumerates" / "expected.json")
        grades = dict(got_for("hook-enumerates")["grades"], hook="Good")
        failed = narrative.judge_case(expected, got_for("hook-enumerates", grades=grades))
        self.assertTrue(any("hook" in line and "Good" in line for line in failed), failed)
        failed = narrative.judge_case(expected, got_for("hook-enumerates", flags=[]))
        self.assertTrue(any("flag" in line for line in failed), failed)

    def test_the_good_case_must_be_recommended(self):
        expected = load(FIXTURES / "good" / "expected.json")
        self.assertEqual(narrative.judge_case(expected, dict(
            got_for("good"), plan_review="accept",
            cold_read={"match": "match", "unexplained_terms": []})), [])
        failed = narrative.judge_case(expected, dict(got_for("good"), verdict="consider",
                                                     plan_review="accept"))
        self.assertTrue(failed)

    def test_the_plan_review_must_send_a_bad_plan_back_and_accept_the_good(self):
        expected = load(FIXTURES / "list-plan" / "expected.json")
        failed = narrative.judge_case(expected, got_for("list-plan", plan_review="accept"))
        self.assertTrue(any("plan review" in line for line in failed), failed)
        self.assertEqual(narrative.judge_case(expected, got_for("list-plan",
                                                                plan_review="revise")), [])
        good = load(FIXTURES / "good" / "expected.json")
        failed = narrative.judge_case(good, got_for("good", plan_review="revise",
                                                    cold_read={"match": "match"}))
        self.assertTrue(any("plan review" in line for line in failed), failed)

    def test_the_cold_read_must_find_what_the_case_hides(self):
        expected = load(FIXTURES / "no-premise" / "expected.json")
        failed = narrative.judge_case(expected, got_for("no-premise",
                                                        cold_read={"match": "match"}))
        self.assertTrue(any("cold read" in line for line in failed), failed)
        jargon = load(FIXTURES / "jargon" / "expected.json")
        terms = first_names(jargon["cold_read"]["terms"])
        failed = narrative.judge_case(jargon, got_for("jargon", cold_read={
            "match": "match", "unexplained_terms": terms[:1]}))
        self.assertTrue(any(terms[1] in line for line in failed), failed)

    def test_no_verdict_is_a_failure_never_a_pass(self):
        for name in ("topic-train", "good"):
            expected = load(FIXTURES / name / "expected.json")
            with self.subTest(case=name):
                self.assertTrue(narrative.judge_case(expected, {"verdict": "not_evidenced"}))


def rated(verdict: str, **grades) -> dict:
    g = {k: "Good" for k in narrative.KEYS}
    g.update(grades)
    return {"verdict": verdict, "grades": g}


class TensionAndTrigger(unittest.TestCase):
    """AUTO-136: flat stakes and an opening with no curiosity trigger."""

    def test_the_two_cases_load_with_a_script_and_their_criterion(self):
        names = cases()
        for name, key in (("flat-tension", "tension"), ("no-trigger", "hook")):
            with self.subTest(case=name):
                for part in ("plan.json", "script.json", "expected.json"):
                    self.assertTrue((names[name] / part).is_file(), part)
                expected = load(names[name] / "expected.json")
                self.assertEqual(expected["criterion"], key)
                self.assertIn(key, narrative.KEYS)
                self.assertEqual(expected["verdict"], "consider")
        self.assertEqual(load(names["no-trigger"] / "expected.json")["trigger"], "none")
        self.assertNotIn("trigger", load(names["flat-tension"] / "expected.json"))

    def test_judge_case_fails_a_reviewer_that_misses_either_fault(self):
        flat = load(FIXTURES / "flat-tension" / "expected.json")
        quiet = load(FIXTURES / "no-trigger" / "expected.json")
        self.assertEqual(narrative.judge_case(flat, got_for("flat-tension")), [])
        self.assertEqual(narrative.judge_case(quiet, got_for("no-trigger", trigger="none")),
                         [])
        for expected, name in ((flat, "flat-tension"), (quiet, "no-trigger")):
            with self.subTest(case=name):
                self.assertTrue(narrative.judge_case(
                    expected, got_for(name, verdict="recommend", trigger="none")))
        grades = dict(got_for("flat-tension")["grades"], tension="Good")
        failed = narrative.judge_case(flat, got_for("flat-tension", grades=grades))
        self.assertTrue(any("tension graded Good" in line for line in failed), failed)
        for trigger in ("question", "not_evidenced", None):
            with self.subTest(trigger=trigger):
                failed = narrative.judge_case(quiet, got_for("no-trigger", trigger=trigger))
                self.assertEqual(len(failed), 1, failed)
                self.assertIn("trigger", failed[0])
                self.assertIn("none", failed[0])


class LectureVoice(unittest.TestCase):
    """AUTO-138: the good film read as a lecture, graded on voice."""

    def test_judge_case_fails_a_reviewer_that_hears_no_lecture(self):
        expected = load(FIXTURES / "lecture-voice" / "expected.json")
        self.assertEqual(narrative.judge_case(expected, got_for("lecture-voice")), [])
        failed = narrative.judge_case(expected, got_for("lecture-voice", verdict="recommend"))
        self.assertTrue(any("recommend" in line for line in failed), failed)
        for grade in ("Excellent", "Good"):
            with self.subTest(grade=grade):
                grades = dict(got_for("lecture-voice")["grades"], voice=grade)
                failed = narrative.judge_case(expected, got_for("lecture-voice",
                                                                grades=grades))
                self.assertTrue(any(f"voice graded {grade}" in line for line in failed),
                                failed)
        unflagged = [f for f in got_for("lecture-voice")["flags"] if f["criterion"] != "voice"]
        failed = narrative.judge_case(expected, got_for("lecture-voice", flags=unflagged))
        self.assertTrue(any("no flag on voice" in line for line in failed), failed)

    def test_its_voice_flags_name_shots_and_quote_them(self):
        said = load(FIXTURES / "lecture-voice" / "expected.json")["review"]
        voiced = [f for f in said["flags"] if f["criterion"] == "voice"]
        self.assertTrue(voiced)
        for f in voiced:
            self.assertGreater(f["n"], 0, f)
            self.assertTrue(f["quote"].strip(), f)
        self.assertEqual(said["verdict"], "consider")


class LengthAudit(unittest.TestCase):
    def test_the_good_pair_at_the_ceiling_carries_voice_excellent(self):
        good = load(FIXTURES / "good" / "expected.json")["review"]
        top = {"verdict": "recommend", "grades": {k: "Excellent" for k in good["grades"]}}
        self.assertEqual(sorted(top["grades"]), sorted(narrative.KEYS))
        self.assertTrue(narrative.at_ceiling(top, top))
        self.assertEqual(good["grades"]["voice"], "Excellent")

    def test_who_wins_a_pair(self):
        self.assertEqual(narrative.pair_winner(rated("consider"), rated("recommend")), "longer")
        self.assertEqual(narrative.pair_winner(rated("recommend"), rated("consider")), "shorter")
        self.assertEqual(narrative.pair_winner(rated("consider"), rated("consider")), "tie")
        # the same verdict: the grades decide, each criterion a level up or down
        self.assertEqual(narrative.pair_winner(rated("consider"),
                                               rated("consider", hook="Excellent")), "longer")
        self.assertEqual(narrative.pair_winner(rated("consider", hook="Excellent"),
                                               rated("consider", stakes="Excellent")), "tie")

    def test_the_audit_fails_when_the_longer_wins_more_than_60_percent(self):
        win = (rated("consider"), rated("recommend"))
        tie = (rated("consider"), rated("consider"))
        self.assertTrue(narrative.length_audit([win, win, tie, tie, tie])["passed"])
        three_of_five = narrative.length_audit([win, win, win, tie, tie])
        self.assertTrue(three_of_five["passed"])                      # 60% is the limit
        self.assertEqual((three_of_five["pairs"], three_of_five["longer"]), (5, 3))
        self.assertFalse(narrative.length_audit([win, win, tie])["passed"])
        self.assertEqual(narrative.LENGTH_BIAS_LIMIT, 0.6)

    def test_no_pair_is_no_audit(self):
        audit = narrative.length_audit([])
        self.assertFalse(audit["passed"])
        self.assertEqual(audit["pairs"], 0)

    def test_a_pair_at_the_ceiling_is_counted_and_an_audit_all_at_it_sees_nothing(self):
        top = rated("recommend", **{k: "Excellent" for k in narrative.KEYS})
        self.assertTrue(narrative.at_ceiling(top, top))
        # AUTO-138: the ceiling is every row Excellent, voice too
        self.assertEqual(len(top["grades"]), 9)
        self.assertFalse(narrative.at_ceiling(top, dict(top, grades=dict(top["grades"],
                                                                         voice="Good"))))
        self.assertFalse(narrative.at_ceiling(top, rated("recommend")))
        self.assertFalse(narrative.at_ceiling(rated("consider"), rated("consider")))
        tie = (rated("consider"), rated("consider"))
        audit = narrative.length_audit([(top, top), (top, top), tie])
        self.assertEqual(audit["ceiling"], 2)
        self.assertTrue(audit["passed"])
        blind = narrative.length_audit([(top, top), (top, top), (top, top)])
        self.assertEqual(blind["ceiling"], 3)
        self.assertFalse(blind["passed"])
        lines = narrative.calibration_lines({"cases": [], "audit": blind})
        line = next(line for line in lines if line.startswith("length audit:"))
        self.assertIn("fail", line)
        self.assertIn("ceiling", line)
        lines = narrative.calibration_lines({"cases": [], "audit": audit})
        line = next(line for line in lines if line.startswith("length audit:"))
        self.assertIn("2 at the ceiling", line)


# --------------------------------------------------------------------------- #
# the calibrate entry point, over the fake
# --------------------------------------------------------------------------- #

class Calibrate(Hermetic):
    def test_a_reviewer_that_says_what_each_case_expects_passes_every_row(self):
        report, asked = self.calibrate()
        rows = {r["name"]: r for r in report["cases"]}
        self.assertEqual(sorted(rows), sorted([*SEEDED, *COLD_READ, "good"]))
        for name, row in rows.items():
            with self.subTest(case=name):
                self.assertTrue(row["passed"], row)
                self.assertEqual(row["expected"],
                                 load(FIXTURES / name / "expected.json").get("verdict"))
                self.assertIs(row["story"], name not in COLD_READ)
        self.assertTrue(report["audit"]["passed"])
        self.assertGreaterEqual(report["audit"]["pairs"], 3)
        self.assertTrue(report["passed"])

    def test_the_rounds_heard_trigger_is_judged_on_no_trigger(self):
        report, _ = self.calibrate()
        rows = {r["name"]: r for r in report["cases"]}
        self.assertEqual(rows["no-trigger"]["trigger"], "none")
        self.assertTrue(rows["no-trigger"]["passed"], rows["no-trigger"])
        self.assertEqual(rows["good"]["trigger"], "unexpected_observation")
        self.assertEqual(rows["topic-train"]["trigger"], "not_evidenced")    # not_heard

        def opens_on_a_question(case, kind):
            if kind == "review" and case == "no-trigger":
                said = copy.deepcopy(load(FIXTURES / case / "expected.json")["review"])
                said["first_minute"]["trigger"] = "question"
                return said
            return None
        report, _ = self.calibrate(opens_on_a_question)
        row = next(r for r in report["cases"] if r["name"] == "no-trigger")
        self.assertFalse(row["passed"])
        self.assertTrue(any("trigger" in f for f in row["failures"]), row["failures"])

    def test_it_asks_the_plan_review_the_three_samples_and_the_cold_read(self):
        report, asked = self.calibrate()
        by_case: dict[str, list[str]] = {}
        for case, name in asked:
            by_case.setdefault(case, []).append(name)
        good = by_case["good"]
        for name in ("plan-review", "review-r1-s1", "review-r1-s2", "review-r1-s3",
                     "cold-read", "cold-read-match"):
            self.assertIn(name, good)
        self.assertIn("review-r1-s1", by_case["good-padded"])
        # a plan-only case has no narration for a cold reader
        self.assertIn("plan-review", by_case["topic-train"])
        self.assertNotIn("cold-read", by_case["topic-train"])
        # nothing is revised: calibration reads, it never rewrites
        every = [n for _, n in asked]
        self.assertFalse([n for n in every if n.startswith(("revise", "redraft", "plan-2",
                                                            "write-"))], every)

    def test_a_lenient_reviewer_fails_every_seeded_row_and_the_report(self):
        def lenient(case, kind):
            if kind == "review":
                return {**load(FIXTURES / "good" / "expected.json")["review"]}
            if kind == "plan-review":
                return load(FIXTURES / "good" / "expected.json")["plan_review"]
            return None
        report, _ = self.calibrate(lenient)
        rows = {r["name"]: r for r in report["cases"]}
        for name in SEEDED:
            with self.subTest(case=name):
                self.assertFalse(rows[name]["passed"])
                self.assertEqual(rows[name]["got"], "recommend")
        self.assertFalse(report["passed"])

    def test_a_cold_read_case_is_passed_or_failed_by_its_cold_reader(self):
        def recommends(case, kind):
            if kind == "review":
                return load(FIXTURES / "good" / "expected.json")["review"]
            return None
        report, _ = self.calibrate(recommends)
        rows = {r["name"]: r for r in report["cases"]}
        for name in COLD_READ:
            with self.subTest(case=name):
                self.assertEqual(rows[name]["got"], "recommend")
                self.assertTrue(rows[name]["passed"], rows[name])

        def blind(case, kind):
            if kind == "cold-read":
                return {"premise": "a comet's tail and what pushes it",
                        "unexplained_terms": [], "claims": []}
            if kind == "match":
                return {"verdict": "match", "difference": ""}
            return None
        report, _ = self.calibrate(blind)
        rows = {r["name"]: r for r in report["cases"]}
        for name in COLD_READ:
            with self.subTest(case=name, reader="blind"):
                self.assertFalse(rows[name]["passed"])
                self.assertTrue(all("cold read" in f for f in rows[name]["failures"]),
                                rows[name]["failures"])
        lines = narrative.calibration_lines(report)
        row = next(line for line in lines if line.split() and line.split()[0] == "jargon")
        self.assertIn("cold read", row)

    def test_a_reviewer_won_by_length_fails_the_audit(self):
        def longer_wins(case, kind):
            if kind == "review" and case in ("good", "recap-ending"):
                said = copy.deepcopy(load(FIXTURES / case / "expected.json")["review"])
                said["grades"]["open_loops"] = "Fair"
                said["verdict"] = "consider"
                said["flags"].append({"n": 0, "section": 2, "criterion": "open_loops",
                                      "problem": "p", "severity": "important", "fix": "f",
                                      # in section 2 of the script and of its padded copy
                                      "quote": "a grain of dust"})
                return said
            return None
        report, _ = self.calibrate(longer_wins)
        self.assertFalse(report["audit"]["passed"])
        self.assertEqual(report["audit"]["longer"], report["audit"]["pairs"])
        self.assertFalse(report["passed"])

    def test_the_consider_level_case_is_padded_and_paired_too(self):
        report, asked = self.calibrate()
        self.assertIn("recap-ending-padded", {case for case, _ in asked})
        rows = {r["name"]: r for r in report["cases"]}
        self.assertEqual(rows["recap-ending"]["padded"], "consider")
        self.assertEqual(report["audit"]["pairs"], 6)
        self.assertEqual(report["audit"]["ceiling"], 0)

    def test_the_fixtures_are_never_logged_as_channel_scripts(self):
        self.calibrate()
        self.assertEqual(list(self.work.rglob(script.REVIEW_LOG)), [])

    def test_a_spent_budget_leaves_the_rest_unrun_and_the_report_unpassed(self):
        ask, _ = answered_from_fixtures()

        def costly(self_, name, prompt, schema):
            self_.usage.cost_usd += 1.0
            return ask(self_, name, prompt, schema)
        with mock.patch.object(script.Screenwriter, "_ask", costly):
            report = narrative.calibrate(FIXTURES, work=self.work, model="sonnet",
                                         reviewer_model="opus", verbose=False,
                                         budget_usd=3.0)
        unrun = [r for r in report["cases"] if not r["ran"]]
        self.assertTrue(unrun)
        for row in unrun:
            self.assertFalse(row["passed"])
            self.assertIn("budget", row["failures"][0])
        self.assertFalse(report["passed"])

    def test_the_reviewer_is_never_the_writer_s_family(self):
        with self.assertRaises(ValueError):
            narrative.calibrate(FIXTURES, work=self.work, model="opus",
                                reviewer_model="opus", verbose=False)


class Entry(Hermetic):
    def test_the_table_lists_every_fixture_with_pass_or_fail_and_the_audit(self):
        code, out, err = self.main()
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        header = next(line for line in lines if line.startswith("fixture"))
        for column in ("expected", "got", "result"):
            self.assertIn(column, header)
        for name in [*SEEDED, *COLD_READ, "good"]:
            row = next((line for line in lines if line.split() and line.split()[0] == name),
                       None)
            self.assertIsNotNone(row, name)
            self.assertIn("pass", row.split())
        audit = next(line for line in lines if line.startswith("length audit:"))
        self.assertIn("pass", audit)
        self.assertIn("60%", audit)
        self.assertTrue(any(line.startswith("--gate") for line in lines), out)

    def test_a_failing_reviewer_exits_1_and_says_which_rows_failed(self):
        def lenient(case, kind):
            if kind == "review":
                return load(FIXTURES / "good" / "expected.json")["review"]
            return None
        code, out, err = self.main(override=lenient)
        self.assertEqual(code, 1)
        row = next(line for line in out.splitlines() if line.startswith("recap-ending"))
        self.assertIn("fail", row.split())

    def test_the_gate_line_counts_the_labelled_verdicts(self):
        self.work.mkdir(parents=True, exist_ok=True)
        rows = [{"slug": f"film-{i}", "verdict": "consider",
                 "label": "consider" if i < 5 else None} for i in range(8)]
        (self.work / script.REVIEW_LOG).write_text(
            "".join(json.dumps(r) + "\n" for r in rows))
        code, out, _ = self.main()
        gate = next(line for line in out.splitlines() if line.startswith("--gate"))
        self.assertIn("5 labelled", gate)
        self.assertIn("20", gate)
        self.assertIn("not yet", gate)

    def test_the_gate_line_says_how_often_the_reviewer_agreed_with_the_labels(self):
        # Counting labels is not calibrating against them (judging 9): the
        # line reports the agreement, with no threshold.
        self.work.mkdir(parents=True, exist_ok=True)
        rows = [{"verdict": "recommend", "label": "reject" if i < 15 else "recommend"}
                for i in range(20)]
        (self.work / script.REVIEW_LOG).write_text(
            "".join(json.dumps(r) + "\n" for r in rows))
        code, out, _ = self.main()
        self.assertEqual(code, 0)
        gate = next(line for line in out.splitlines() if line.startswith("--gate"))
        self.assertIn("may be turned on", gate)
        self.assertIn("agreed with 5 of 20 labels", gate)
        report = json.loads((self.work / "calibration" / "calibration.json").read_text())
        self.assertEqual((report["agreed"], report["labelled"]), (5, 20))

    def test_the_report_on_disk_has_a_stable_shape(self):
        code, _, _ = self.main()
        self.assertEqual(code, 0)
        path = self.work / "calibration" / "calibration.json"
        self.assertTrue(path.is_file())
        report = json.loads(path.read_text())
        self.assertEqual(set(report), {"directory", "model", "reviewer_model", "cases",
                                       "audit", "passed", "verdicts", "labelled", "agreed",
                                       "cost_usd"})
        self.assertEqual(set(report["audit"]), {"pairs", "longer", "shorter", "ties",
                                                "ceiling", "passed"})
        base = {"name", "criterion", "story", "expected", "got", "plan_review",
                "cold_read", "ran", "passed", "failures", "grades", "samples", "trigger"}
        for row in report["cases"]:
            with self.subTest(case=row["name"]):
                padded = (FIXTURES / row["name"] / "padded.json").is_file()
                self.assertEqual(set(row), base | ({"padded"} if padded else set()))

    def test_a_missing_directory_exits_2(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = narrative.main(["calibrate", str(self.work / "nowhere"),
                                   "--work", str(self.work), "--quiet"])
        self.assertEqual(code, 2)
        self.assertIn("no calibration cases", err.getvalue())

    def test_a_reviewer_in_the_writer_s_family_exits_2(self):
        code, out, err = self.main("--model", "opus", "--reviewer-model", "opus")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertTrue(err.strip())

    def test_the_module_runs_as_a_program(self):
        source = (ROOT / "ytstudio" / "narrative.py").read_text(encoding="utf-8")
        self.assertIn('if __name__ == "__main__":', source)

    def test_calibrate_is_no_stage_of_yt(self):
        # A module entry point, so the stage table and the every-command
        # check are untouched.
        parser = cli.build_parser()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args(["calibrate"])


# --------------------------------------------------------------------------- #
# the review log, doctor and yt run
# --------------------------------------------------------------------------- #

class PlanOnly(Hermetic):
    """Four cases ship a plan and no script: the story review reads the plan
    as the film, and a flag can only sit on shot 0."""

    def writer(self) -> script.Screenwriter:
        return script.Screenwriter(script.Idea(title="t", genre="g", description="d"),
                                   verbose=False, jobs=1, reviewer_model="opus",
                                   review_log=None, root=self.work / "list-plan")

    def test_the_prompt_says_no_section_is_drafted_and_flags_go_on_shot_0(self):
        plan = load(FIXTURES / "list-plan" / "plan.json")
        prompt = self.writer()._story_prompt([], plan)
        flat = " ".join(prompt.split())
        self.assertIn("No section is drafted yet", flat)
        self.assertIn("review the plan above as the film it would make", flat)
        self.assertIn("shot 0", flat[flat.index("No section is drafted yet"):])
        self.assertNotIn("Section 1:", prompt)

    def test_a_flag_on_a_shot_the_plan_does_not_have_unsettles_the_sample(self):
        said = load(FIXTURES / "list-plan" / "expected.json")["review"]
        stray = copy.deepcopy(said)
        stray["flags"][0]["n"] = 1

        def ask(self_, name, prompt, schema):
            return copy.deepcopy(stray if name.endswith(("-s1", "-s1-again")) else said)
        with mock.patch.object(script.Screenwriter, "_ask", ask):
            rnd = self.writer().review_round([], load(FIXTURES / "list-plan" / "plan.json"), 1)
        first = rnd["samples"][0]
        self.assertFalse(first["valid"], first)
        self.assertTrue(first.get("again"), first)
        self.assertTrue(all(s["valid"] for s in rnd["samples"][1:]))
        self.assertEqual(rnd["verdict"], said["verdict"])


class ReviewLog(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name)

    def test_a_label_round_trips(self):
        log = self.work / script.REVIEW_LOG
        w = script.Screenwriter(script.Idea(title="t", genre="g", description="d"),
                                verbose=False, jobs=1, reviewer_model="opus",
                                review_log=log, root=self.work / "film")
        w._log_verdict({"round": 1, "grades": {k: "Good" for k in narrative.KEYS},
                        "verdict": "consider", "samples": [{"name": "review-r1-s1"}]})
        rows = narrative.read_review_log(log)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["label"])
        self.assertEqual(narrative.labelled(rows), 0)
        # a person reads the script and labels the row
        rows[0]["label"] = "reject"
        log.write_text(json.dumps(rows[0]) + "\n")
        again = narrative.read_review_log(log)
        self.assertEqual(again, rows)
        self.assertEqual(narrative.labelled(again), 1)

    def test_only_a_verdict_is_a_label(self):
        rows = [{"label": "reject"}, {"label": "recommend"}, {"label": None},
                {"label": "maybe"}, {"label": ""}, {}]
        self.assertEqual(narrative.labelled(rows), 2)

    def test_a_label_in_the_corpus_s_words_or_capitals_is_counted(self):
        # Coverage says Pass, Consider, Recommend (rules 10 and 12); Pass is
        # what the rubric calls reject.
        rows = [{"label": "Pass"}, {"label": "Consider"}, {"label": " RECOMMEND "},
                {"label": "reject"}]
        self.assertEqual([narrative.label_of(r) for r in rows],
                         ["reject", "consider", "recommend", "reject"])
        self.assertEqual(narrative.labelled(rows), 4)
        self.assertIsNone(narrative.label_of({"label": "maybe"}))
        self.assertIsNone(narrative.label_of({"label": 3}))

    def test_agreement_is_the_labels_the_reviewer_s_verdict_matches(self):
        rows = [{"verdict": "reject", "label": "Pass"},
                {"verdict": "consider", "label": "consider"},
                {"verdict": "recommend", "label": "consider"},
                {"verdict": "consider", "label": None},
                {"verdict": "consider", "label": "maybe"}]
        self.assertEqual(narrative.agreement(rows), (2, 3))
        self.assertEqual(narrative.unreadable_labels(rows), 1)

    def test_a_missing_or_broken_log_reads_as_what_it_holds(self):
        self.assertEqual(narrative.read_review_log(self.work / "none.jsonl"), [])
        broken = self.work / script.REVIEW_LOG
        broken.write_text('{"label": "consider"}\nnot json\n[1]\n')
        self.assertEqual(narrative.read_review_log(broken), [{"label": "consider"}])


class Doctor(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name) / "work"
        self.root = self.work / "film"
        self.root.mkdir(parents=True)
        (self.root / "idea.json").write_text(json.dumps(
            {"title": "Why the bridge fell", "genre": "g", "description": "d"}))

    def write(self, *, gate: bool, labelled: int, unlabelled: int = 3):
        record = {"target_seconds": 10, "findings": [], "review": {"verdict": "recommend"},
                  "sections": [{"heading": "S1", "shots": [
                      {"label": "1.1", "narration": "x", "seconds": 10}]}]}
        if gate:
            record["gate"] = True
        (self.root / "script.json").write_text(json.dumps(record))
        rows = ([{"verdict": "consider", "label": "consider"}] * labelled
                + [{"verdict": "consider", "label": None}] * unlabelled)
        (self.work / script.REVIEW_LOG).write_text("".join(json.dumps(r) + "\n" for r in rows))

    def warned(self) -> list:
        report = doctor.check_project(workspace.Project(self.root), self.work)
        return [f for f in report.findings if "gate on" in f.message]

    def test_a_gated_script_below_20_labelled_verdicts_is_warned(self):
        self.write(gate=True, labelled=19)
        found = self.warned()
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0].level, "warn")
        self.assertEqual(found[0].area, "script")
        self.assertIn("gate on with 19 labelled verdicts (20 needed)", found[0].message)
        self.assertIn("calibrate", found[0].fix)
        # --gate stops voice only on a reject or not_evidenced; this script
        # was recommended, so the warn says what is true of every gated one
        self.assertNotIn("stopped", found[0].message)
        self.assertIn("gated voice on a story review not yet calibrated", found[0].message)

    def test_not_at_20_and_not_without_the_gate(self):
        self.write(gate=True, labelled=20)
        self.assertEqual(self.warned(), [])
        self.write(gate=False, labelled=0)
        self.assertEqual(self.warned(), [])

    def test_the_log_is_read_from_the_project_s_work_directory_by_default(self):
        self.write(gate=True, labelled=2)
        report = doctor.check_project(workspace.Project(self.root))
        found = [f for f in report.findings if "gate on" in f.message]
        self.assertEqual(len(found), 1)
        self.assertIn("2 labelled", found[0].message)

    def test_the_environment_note_counts_only_verdict_labels(self):
        self.write(gate=False, labelled=4)
        with (self.work / script.REVIEW_LOG).open("a") as fh:
            fh.write(json.dumps({"verdict": "consider", "label": "maybe"}) + "\n")
        report = doctor.Report("environment")
        doctor.review_log(report, self.work)
        self.assertEqual(report.metrics["review_log"],
                         {"verdicts": 8, "labelled": 4, "agreed": 4, "unreadable": 1})
        note = next(f for f in report.findings if f.area == "review log")
        self.assertIn("the reviewer agreed with 4", note.message)
        self.assertIn("1 labelled with a word that is not a verdict", note.message)


class GateRecorded(unittest.TestCase):
    """script.json says when it was written under --gate, which doctor reads."""
    setUp = review_tests.Command.setUp
    run_stage = review_tests.Command.run_stage

    def test_gate_is_recorded_and_its_absence_too(self):
        code, _, said = self.run_stage("--review", "--gate")
        self.assertEqual(code, 0, said)
        self.assertIs(json.loads((self.film / "script.json").read_text())["gate"], True)
        code, _, said = self.run_stage("--review")
        self.assertNotIn("gate", json.loads((self.film / "script.json").read_text()))


class RunNeverGates(unittest.TestCase):
    def test_run_extras_never_passes_gate(self):
        for quick in (False, True):
            args = SimpleNamespace(quick=quick, minutes=10, reviewer_model="opus",
                                   review_budget_usd=2.0)
            extras = cli.run_extras(args, redo={"all"})
            with self.subTest(quick=quick):
                self.assertNotIn("--gate", [f for flags in extras.values() for f in flags])


# --------------------------------------------------------------------------- #
# the docs
# --------------------------------------------------------------------------- #

class Docs(unittest.TestCase):
    def test_the_manual_states_when_gate_may_be_turned_on(self):
        skill = (ROOT / "ytstudio" / "SKILL.md").read_text(encoding="utf-8")
        flat = " ".join(skill.split())
        self.assertIn("python -m ytstudio.narrative calibrate", flat)
        self.assertIn("the seeded-bad fixtures all fail, the good one passes, the length "
                      "audit passes, and work/review-log.jsonl holds at least 20 rows with "
                      "a human `label`", flat)
        self.assertIn("gate on with N labelled verdicts (20 needed)", flat)

    def test_the_corpus_readme_notes_the_condition_and_the_cost(self):
        readme = (ROOT / "docs" / "narrative" / "README.md").read_text(encoding="utf-8")
        item = readme[readme.index("3. **The narrative rubric.**"):
                      readme.index("4. **The evidence loop.**")]
        item = " ".join(item.split())
        self.assertIn("python -m ytstudio.narrative calibrate", item)
        self.assertIn("20", item)
        self.assertIn("length", item)
        self.assertRegex(item, r"about \$\d+")

    def test_gate_when_names_the_calibration(self):
        self.assertIn("calibrate", script.GATE_WHEN)
        self.assertIn(str(narrative.GATE_LABELLED), script.GATE_WHEN)


if __name__ == "__main__":
    unittest.main()
