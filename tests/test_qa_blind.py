"""qa.py: the scorecard is scored blind, and the comparison with the previous
pass is computed afterwards (AUTO-106).

Each pass used to be given the previous pass's scores, overall and up to 20
findings under "Previous pass, for comparison", and the reviewer anchored to
them: three passes in a row matched on every category and one rationale said
"close to pass-07 with no clear regression". Now the scoring samples' prompt
names no previous pass, score, overall or finding; `qa.compare` (the Delta
column of findings.md) is computed in code from the two scorecards once the
new one exists; and the previous findings, when they are wanted as a
checklist (`--recheck`), are put to a decision of their own after the
scoring, `<pass>-recheck`, whose answers are filed beside the scores and
change none of them. No model, no network, no host: a fake engine answers, a
fake renderer draws (the fixture of tests/test_qa_side_by_side.py).
"""

import json
import re
import unittest
from pathlib import Path

from claude_engine import ClaudeError, Event
from ytstudio import cli, harness, qa, spend
from tests import test_qa_motion as motion
from tests.test_qa_side_by_side import COMPARE, STORY, Pass, ids_in

#: The previous pass's finding, worded so it cannot appear by accident.
OLD = "the comet's tail points at the sun in shot 1.1"
#: The previous pass's scores: a number no other part of the brief carries.
OLD_SCORE = 6.35
#: What marks the recheck's prompt apart from a scoring sample's.
RECHECK = "earlier cut"


def finding(problem, t=0.5):
    return {"timestamp": t, "frame": "", "problem": problem, "why": "it reads wrong",
            "root_cause": "the rig", "owner": "vfx", "fix": "turn the tail",
            "severity": "minor"}


def sample(score, findings=()):
    return {"scores": {k: score for k in qa.CATEGORIES}, "overall": score,
            "verdict": qa.NOT_READY, "findings": list(findings), "blocking": [],
            "not_evidenced": [], "rationale": "read it", "story": STORY}


class Blind(Pass):
    """pass-01 scores every category OLD_SCORE and files OLD in every
    sample; pass-02 scores 7.0 and files nothing."""

    def setUp(self):
        super().setUp()
        seen = self.seen
        seen["rechecks"] = []
        #: The recheck's answer: a callable of its prompt, or an exception.
        self.recheck = recheck = [lambda prompt: {"findings": [
            {"id": i, "status": "stands", "why": "the tail still points at the sun",
             # an answer that tries to score: nothing reads it
             "scores": {k: 1.0 for k in qa.CATEGORIES}}
            for i in dict.fromkeys(re.findall(r'"id": "(P\d+)"', prompt))],
            "scores": {k: 1.0 for k in qa.CATEGORIES}, "overall": 1.0}]
        compare = self.compare

        class Reader(motion.FakeEngine):
            def ask_json(self_, prompt, schema, system=None):
                # The merge decision: every sample's first finding is one defect.
                if "groups" in schema.get("properties", {}):
                    ids = sorted(set(re.findall(r'"id": "(s\d+\.F1)"', prompt)))
                    return {"groups": [{"findings": ids}] if ids else []}
                return {}

            def stream(self_, prompt, system=None):
                if COMPARE in prompt:
                    seen["compares"].append(prompt)
                    answer = compare[0](prompt)
                elif RECHECK in prompt:
                    seen["rechecks"].append({"prompt": prompt, "cwd": self_.config.cwd})
                    answer = recheck[0]
                    if isinstance(answer, Exception):
                        raise answer
                    answer = answer(prompt)
                else:
                    seen["prompts"].append(prompt)
                    first = prompt.startswith("Review pass-01")
                    answer = (sample(OLD_SCORE, [finding(OLD)]) if first else sample(7.0))
                yield Event(type="assistant", text="```json\n" + json.dumps(answer) + "\n```")
                yield Event(type="result", raw={})

        harness.ENGINE = Reader

    def second(self):
        """pass-02's scoring prompts."""
        return [p for p in self.seen["prompts"] if p.startswith("Review pass-02")]


class ScoredBlind(Blind):
    def test_pass_01_files_the_finding_the_next_pass_must_not_see(self):
        self.two_passes()
        old = self.card("pass-01")
        self.assertEqual([f["problem"] for f in old["findings"]], [OLD])
        self.assertEqual(old["scores"]["visual_quality"], OLD_SCORE)

    def test_the_scoring_prompt_carries_no_previous_score_overall_or_finding(self):
        self.two_passes()
        prompts = self.second()
        self.assertEqual(len(prompts), qa.QA_SAMPLES)
        for prompt in prompts:
            for word in ("Previous pass", "pass-01", OLD, str(OLD_SCORE), "for comparison"):
                self.assertNotIn(word, prompt)

    def test_the_scoring_prompt_is_the_same_whether_or_not_an_earlier_pass_exists(self):
        self.run_qa()
        first = [p.replace("pass-01", "pass-NN") for p in self.seen["prompts"]]
        (self.root / "qa" / "pass-01").rename(self.root / "qa" / "pass-00")
        self.seen["prompts"].clear()
        self.run_qa(fresh=True)    # pass-01 again, now with pass-00 scored before it
        self.assertEqual(self.card("pass-00")["findings"][0]["problem"], OLD)
        again = [p.replace("pass-01", "pass-NN") for p in self.seen["prompts"]]
        self.assertEqual(again, first)

    def test_with_recheck_the_scoring_prompt_is_still_blind(self):
        self.two_passes(recheck=True)
        for prompt in self.second():
            self.assertNotIn(OLD, prompt)
            self.assertNotIn(RECHECK, prompt)


class Delta(Blind):
    def test_the_delta_is_computed_from_the_two_scorecards(self):
        self.two_passes()
        card = self.card()
        self.assertEqual(card["comparison"]["against"], "pass-01")
        for key in qa.CATEGORIES:
            if card["scores"][key] is not None:
                self.assertAlmostEqual(card["comparison"]["deltas"][key],
                                       round(7.0 - OLD_SCORE, 2), msg=key)

    def test_findings_md_still_writes_the_delta_table(self):
        self.two_passes()
        text = (self.root / "qa" / "pass-02" / "findings.md").read_text()
        self.assertIn("| Category | Score | Spread | Δ vs previous |", text)
        self.assertIn("| visual_quality | 7.0 |", text)
        self.assertIn(f"{7.0 - OLD_SCORE:+.1f} |", text)


class Recheck(Blind):
    def test_without_the_flag_no_recheck_is_asked(self):
        self.two_passes()
        self.assertEqual(self.seen["rechecks"], [])
        self.assertNotIn("recheck", self.card())
        names = [d["name"] for d in self.card()["engine"]["decisions"]]
        self.assertFalse([n for n in names if n.endswith(qa.RECHECK_SUFFIX)])

    def test_a_standing_previous_finding_is_reported_by_its_own_decision(self):
        self.two_passes(recheck=True)
        self.assertEqual(len(self.seen["rechecks"]), 1)
        asked = self.seen["rechecks"][0]
        self.assertIn(OLD, asked["prompt"])
        self.assertEqual(Path(asked["cwd"]).name, "pass-02")   # it reads this pass's frames
        card = self.card()
        record = card["recheck"]
        self.assertEqual(record["status"], "done")
        self.assertEqual(record["against"], "pass-01")
        self.assertEqual(record["decision"], qa.recheck_name("pass-02"))
        self.assertEqual([(f["problem"], f["status"]) for f in record["findings"]],
                         [(OLD, "stands")])
        self.assertEqual(record["tally"], {"stands": 1, "gone": 0, "cannot_tell": 0})

    def test_it_is_asked_after_the_scoring_samples(self):
        self.two_passes(recheck=True)
        names = [d["name"] for d in self.card()["engine"]["decisions"]]
        self.assertEqual(names[:qa.QA_SAMPLES],
                         [f"pass-02-scorecard-s{k}" for k in range(1, qa.QA_SAMPLES + 1)])
        self.assertIn(qa.recheck_name("pass-02"), names)
        self.assertGreater(names.index(qa.recheck_name("pass-02")),
                           names.index(f"pass-02-scorecard-s{qa.QA_SAMPLES}"))

    def test_its_prompt_carries_no_score_and_its_answer_changes_none(self):
        self.two_passes(recheck=True)
        prompt = self.seen["rechecks"][0]["prompt"]
        for word in (str(OLD_SCORE), '"scores"', '"overall"', '"verdict"'):
            self.assertNotIn(word, prompt)
        card = self.card()
        # The recheck answered 1.0 everywhere it could; the medians stand.
        self.assertTrue(all(v == 7.0 for v in card["scores"].values() if v is not None))
        self.assertEqual(card["overall"], 7.0)
        self.assertEqual(card["comparison"]["deltas"]["visual_quality"],
                         round(7.0 - OLD_SCORE, 2))
        self.assertEqual(card["findings"], [])
        qa_json = json.loads((self.root / "qa.json").read_text())
        self.assertEqual(qa_json["overall"], 7.0)
        self.assertEqual(qa_json["recheck"]["stands"], 1)

    def test_the_scores_are_the_same_with_and_without_the_recheck(self):
        self.two_passes(recheck=True)
        with_it = self.card()
        self.run_qa(previous="pass-01", fresh=True)     # pass-03, the same cut
        without = self.card("pass-03")
        for key in ("scores", "overall", "spread", "verdict", "findings"):
            self.assertEqual(with_it[key], without[key], key)

    def test_findings_md_lists_the_recheck(self):
        self.two_passes(recheck=True)
        text = (self.root / "qa" / "pass-02" / "findings.md").read_text()
        self.assertIn("## Previous findings, rechecked", text)
        self.assertIn(f"stands -- {OLD}", text)

    def test_an_unanswered_finding_is_recorded_unanswered(self):
        self.recheck[0] = lambda prompt: {"findings": []}
        self.two_passes(recheck=True)
        row = self.card()["recheck"]["findings"][0]
        self.assertIsNone(row["status"])
        self.assertTrue(row["unanswered"])

    def test_a_failed_recheck_is_billed_and_leaves_the_scores(self):
        self.recheck[0] = ClaudeError("the engine fell over")
        self.two_passes(recheck=True)
        card = self.card()
        self.assertEqual(card["recheck"]["status"], "failed")
        self.assertIn("fell over", card["recheck"]["why"])
        self.assertEqual(card["overall"], 7.0)
        rows = [d for d in card["engine"]["decisions"]
                if d["name"] == qa.recheck_name("pass-02")]
        self.assertEqual([r["kind"] for r in rows], ["failed"])

    def test_with_no_earlier_pass_nothing_is_rechecked(self):
        self.run_qa(recheck=True)
        self.assertEqual(self.seen["rechecks"], [])
        record = self.card("pass-01")["recheck"]
        self.assertEqual(record["status"], "skipped")
        self.assertIn("no earlier scored pass", record["why"])

    def test_an_earlier_pass_with_no_findings_is_not_rechecked(self):
        self.run_qa()
        (self.root / "scene.xml").write_text("<scene cut='2'/>")
        self.run_qa()                                    # pass-02 files nothing
        (self.root / "scene.xml").write_text("<scene cut='3'/>")
        self.run_qa(recheck=True)                        # pass-03 against pass-02
        self.assertEqual(self.seen["rechecks"], [])
        record = self.card("pass-03")["recheck"]
        self.assertEqual(record["status"], "skipped")
        self.assertIn("no findings", record["why"])

    def test_at_most_recheck_max_findings_are_asked_and_the_rest_counted(self):
        many = [finding(f"defect number {n}", t=0.5) for n in range(qa.RECHECK_MAX + 3)]
        card = {"pass": "pass-01", "findings": many}
        rows, unchecked = qa.recheck_rows(card)
        self.assertEqual(len(rows), qa.RECHECK_MAX)
        self.assertEqual(unchecked, 3)
        self.assertEqual(rows[0]["id"], "P01")


class Flag(unittest.TestCase):
    def test_the_flag_is_on_yt_qa_and_off_by_default(self):
        parser = cli.build_parser()
        self.assertTrue(parser.parse_args(["qa", "--recheck"]).recheck)
        self.assertFalse(parser.parse_args(["qa"]).recheck)

    def test_the_estimate_adds_the_recheck_only_when_it_is_asked(self):
        self.assertEqual(spend.flag_increments(None, "qa", ["--recheck"]),
                         [("--recheck", spend.RECHECK_USD)])
        self.assertEqual(spend.flag_increments(None, "qa", []), [])

    def test_a_past_recheck_is_priced_once_not_per_sample(self):
        # Two samples asked: they are scaled to QA_SAMPLES, the recheck is not.
        card = {"usage": {"cost_usd": 1.1}, "scoring": {"asked": 2},
                "engine": {"decisions": [
                    {"name": "pass-02-scorecard-s1", "cost_usd": 0.5},
                    {"name": "pass-02-scorecard-s2", "cost_usd": 0.5},
                    {"name": "pass-02-recheck", "cost_usd": 0.1}]}}
        self.assertEqual(qa.pass_usd(card), round(1.0 / 2 * qa.QA_SAMPLES + 0.1, 2))


if __name__ == "__main__":
    unittest.main()
