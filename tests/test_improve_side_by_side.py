"""improve.py: a pass is kept by the side-by-side tally first and the score
delta second (AUTO-108).

`yt qa` files, beside the absolute scores, a tally of the moments where the
new cut was better, worse or the same as the previous kept cut
(`side_by_side`). Two scores inside the samples' spread cannot say whether
a revision helped; the tally can. So `improve.judge` reads the tally
first: more better than worse keeps a pass whose overall moved inside the
noise, more worse reverts it, a tie leaves the decision to the score delta
as before. The score delta is read second: an overall drop outside the
noise, a category regression or a new blocking mechanical finding still
reverts. Improve tells QA which pass is the kept cut (`--previous`), and
the estimate prices the comparison. No model, no network: fake engines and
the fake runner of test_improve.
"""

import json
import unittest
from pathlib import Path

from ytstudio import improve, qa, spend
from ytstudio.core import write_json
from tests.test_improve import FakeRunner, ImproveCase, card as improve_card


# --------------------------------------------------------------------------- #
# improve reads the tally first
# --------------------------------------------------------------------------- #

def scored(overall, *, spread=0.5, side=None, name="pass-02", **kw):
    c = dict(improve_card(overall, **kw), spread={"overall": spread}, **{"pass": name})
    if side is not None:
        c["side_by_side"] = side
    return c


def side(better=0, worse=0, same=0, against="pass-01", source="stills"):
    entries = ([{"category": "visual_quality", "result": "better", "source": source}] * better
               + [{"category": "visual_quality", "result": "worse", "source": source}] * worse
               + [{"category": "visual_quality", "result": "same", "source": source}] * same)
    return {"against": against, "status": "done", "entries": entries,
            "tally": qa.tally(entries)}


class Judge(unittest.TestCase):
    best = dict(scored(7.0, name="pass-01"))

    def test_more_better_than_worse_keeps_a_pass_inside_the_noise(self):
        new = scored(7.2, side=side(better=5, worse=1))
        self.assertFalse(improve.judge(self.best, scored(7.2))["keep"])   # AUTO-107 alone
        got = improve.judge(self.best, new)
        self.assertTrue(got["keep"], got["reasons"])
        self.assertEqual(got["side_by_side"]["better"], 5)
        self.assertEqual(got["side_by_side"]["read"], "first")

    def test_more_worse_than_better_reverts_a_pass_whose_score_rose(self):
        got = improve.judge(self.best, scored(8.5, side=side(better=1, worse=4)))
        self.assertFalse(got["keep"])
        self.assertTrue(any("side by side" in r for r in got["reasons"]), got["reasons"])

    def test_a_tie_leaves_the_score_delta_to_decide(self):
        self.assertTrue(improve.judge(self.best, scored(8.0, side=side(2, 2, 3)))["keep"])
        self.assertFalse(improve.judge(self.best, scored(7.2, side=side(2, 2, 3)))["keep"])
        self.assertFalse(improve.judge(self.best, scored(7.2, side=side(same=6)))["keep"])

    def test_a_tally_against_another_pass_is_not_read(self):
        got = improve.judge(self.best, scored(7.2, side=side(better=5, against="pass-07")))
        self.assertFalse(got["keep"])
        self.assertIsNone(got.get("side_by_side"))

    def test_an_overall_drop_outside_the_noise_still_reverts(self):
        got = improve.judge(self.best, scored(6.0, spread=0.2, side=side(better=5)))
        self.assertFalse(got["keep"])

    def test_a_category_drop_and_a_new_blocking_mechanical_finding_still_revert(self):
        drop = scored(7.2, side=side(better=5), scores={"cinematography": 5.0})
        self.assertFalse(improve.judge(self.best, drop)["keep"])
        from tests.test_improve import mech
        blocked = scored(7.2, side=side(better=5), mechanical=[mech()])
        self.assertFalse(improve.judge(self.best, blocked)["keep"])

    def test_a_video_tally_is_read_the_same_way(self):
        got = improve.judge(self.best, scored(7.2, side=side(better=3, source="video")))
        self.assertTrue(got["keep"], got["reasons"])

    def test_a_pass_that_is_not_evidenced_is_never_kept_by_its_tally(self):
        new = dict(scored(7.2, side=side(better=5)), verdict=qa.NOT_EVIDENCED)
        self.assertFalse(improve.judge(self.best, new)["keep"])


class Loop(ImproveCase):
    def test_qa_is_told_the_best_pass_and_the_argv_recorded_is_the_one_run(self):
        from tests.test_improve import mech
        self.start_pass(improve_card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [improve_card(4.0, mechanical=[mech()]),
                                        improve_card(4.5, mechanical=[mech()])])
        record = self.improver(runner, max_passes=2).run()
        qa_runs = [c for c in runner.calls if c[0] == "qa"]
        self.assertEqual(len(qa_runs), 2)
        for argv in qa_runs:      # both reverted: the best stays pass-01
            self.assertEqual(argv[argv.index("--previous") + 1], "pass-01")
        recorded = [a for p in record["passes"] for a in p["argv"] if a[0] == "qa"]
        self.assertEqual(recorded, qa_runs)

    def test_a_kept_pass_becomes_the_one_compared_with(self):
        from tests.test_improve import mech
        self.start_pass(improve_card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [improve_card(6.0, mechanical=[mech()]),
                                        improve_card(6.5, mechanical=[mech()])])
        self.improver(runner, max_passes=2).run()
        qa_runs = [c for c in runner.calls if c[0] == "qa"]
        self.assertEqual([a[a.index("--previous") + 1] for a in qa_runs],
                         ["pass-01", "pass-02"])

    def test_a_pass_kept_by_its_tally_says_so_in_the_record_and_the_report(self):
        from tests.test_improve import mech
        self.start_pass(dict(improve_card(5.0, mechanical=[mech()]), spread={"overall": 0.5}))
        new = dict(improve_card(5.2, mechanical=[mech()]), spread={"overall": 0.5},
                   side_by_side=side(better=4, worse=1))
        record = self.improver(FakeRunner(self.root, [new]), max_passes=1).run()
        entry = record["passes"][0]
        self.assertEqual(entry["decision"], "keep")
        self.assertEqual(entry["side_by_side"]["better"], 4)
        text = (self.root / "improve.md").read_text()
        self.assertIn("Side by side against pass-01: better 4, worse 1, same 0", text)

    def test_the_dry_run_plan_names_the_previous_pass(self):
        from tests.test_improve import mech
        self.start_pass(improve_card(5.0, mechanical=[mech()]))
        plan = self.improver(FakeRunner(self.root, []), dry_run=True).run()["plan"]
        self.assertEqual(plan["argv"][-1][-2:], ["--previous", "pass-01"])


class Estimate(ImproveCase):
    def qa_row(self, argv=("qa", "--previous", "pass-01")):
        rows = improve.stage_estimates(self.root, ["sound"], extras={"qa": list(argv)})
        return next(r for r in rows if r["stage"] == "qa")

    def test_the_estimate_includes_the_comparison(self):
        row = self.qa_row()
        self.assertAlmostEqual(row["usd"], spend.DEFAULT_USD["qa"] + spend.COMPARE_PASS_USD)
        self.assertIn("--previous", row["source"])
        plain = self.qa_row(("qa",))
        self.assertAlmostEqual(plain["usd"], spend.DEFAULT_USD["qa"])

    def test_a_measured_comparison_is_not_added_twice_nor_multiplied(self):
        decisions = [{"name": f"pass-01-scorecard-s{k}", "cost_usd": 0.5} for k in (1, 2, 3)]
        decisions.append({"name": "pass-01-compare-pass", "cost_usd": 0.3})
        self.start_pass(dict(improve_card(7.0), usage=spend.record(1.8, "m", 4),
                             scoring={"asked": 3, "valid": 3},
                             engine={"cost_usd": 1.8, "decisions": decisions}))
        row = self.qa_row()
        self.assertEqual(row["basis"], "measured")
        self.assertAlmostEqual(row["usd"], 1.8)
        self.assertNotIn("--previous", row["source"])
        self.assertAlmostEqual(qa.pass_usd(self.card()), 1.8)

    def card(self):
        return json.loads((self.root / "qa" / "pass-01" / "scorecard.json").read_text())

    def test_the_dry_run_prices_the_comparison(self):
        from tests.test_improve import mech
        write_json(self.root / "sound.json", {"cues": [], "usage": {"cost_usd": 0.5}})
        self.start_pass(dict(improve_card(5.0, mechanical=[mech(owner="sound", kind="flat")]),
                             usage=spend.record(0.6, "m", 1)))
        plan = self.improver(FakeRunner(self.root, []), dry_run=True).run()["plan"]
        by = {r["stage"]: r for r in plan["estimates"]}
        self.assertAlmostEqual(by["qa"]["usd"], round(1.8 + spend.COMPARE_PASS_USD, 2))


if __name__ == "__main__":
    unittest.main()
