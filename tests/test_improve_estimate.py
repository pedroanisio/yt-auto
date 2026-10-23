"""improve.py: a pass is estimated from the stages' own measured costs.

The dry run used to guess one default per stage (about $17 for a pass whose
vfx alone had cost $59 and $91) and a vfx pass always re-authored the whole
film. The estimate now reads each stage's cost from its own report -- this
project's, else a like project's -- says which stages were measured and which
assumed, and a vfx pass names only the sections QA's findings fall in.
No model, no network: FakeEngine and a fake runner, as in test_improve.py.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from test_improve import FakeRunner, ImproveCase, card, mech  # noqa: E402
from ytstudio import improve  # noqa: E402
from ytstudio.core import write_json  # noqa: E402


def authored(n, start, seconds, shots, cost):
    return {"n": n, "section": f"s{n}", "cost_usd": cost,
            "worlds": [{"id": f"w{n}", "start": start, "seconds": seconds, "shots": shots,
                        "phases": []}]}


def write_vfx(root, total, rows):
    write_json(root / "vfx.json", {"authoring": {"version": "v01", "versions": ["v01"]},
                                   "authored": rows, "usage": {"cost_usd": total}})


class SectionsOf(ImproveCase):
    def setUp(self):
        super().setUp()
        write_vfx(self.root, 30.0, [authored(1, 0, 10, ["1.1", "1.2"], 10.0),
                                    authored(2, 10, 10, ["2.1", "1.4"], 12.0),
                                    authored(3, 20, 10, ["3.1"], 8.0)])

    def f(self, **kw):
        return dict({"id": "F1", "shot": None, "timestamp": None}, **kw)

    def test_a_shot_label_places_a_finding(self):
        self.assertEqual(improve.routed_sections(self.root, [self.f(shot="3.1")]), [3])

    def test_a_timestamp_places_a_finding_when_the_shot_is_unknown(self):
        got = improve.routed_sections(self.root, [self.f(timestamp=12.5),
                                                  self.f(shot="zz", timestamp=1.0)])
        self.assertEqual(got, [1, 2])

    def test_one_finding_that_cannot_be_placed_sends_the_whole_film(self):
        self.assertIsNone(improve.routed_sections(self.root, [self.f(shot="3.1"), self.f()]))
        self.assertIsNone(improve.routed_sections(self.root, [self.f(timestamp=99.0)]))

    def test_every_section_routed_is_the_whole_film(self):
        rows = [self.f(shot="1.1"), self.f(shot="2.1"), self.f(shot="3.1")]
        self.assertIsNone(improve.routed_sections(self.root, rows))

    def test_no_authoring_record_means_the_whole_film(self):
        write_json(self.root / "vfx.json", {"authoring": {"version": "v01"}})
        self.assertIsNone(improve.routed_sections(self.root, [self.f(shot="3.1")]))


class Argvs(ImproveCase):
    base = ["--project", "p"]

    def test_vfx_names_the_routed_sections_not_fresh(self):
        argv = improve.stage_argvs(["vfx", "sound", "build", "render"], {"vfx"}, self.base,
                                   quality="final", root=self.root, sections=[2, 3])
        self.assertEqual(argv[0][argv[0].index("--sections") + 1], "2,3")
        self.assertNotIn("--fresh", argv[0])
        self.assertNotIn("--sections", argv[1])

    def test_without_sections_vfx_is_fresh_as_before(self):
        argv = improve.stage_argvs(["vfx"], {"vfx"}, self.base, quality="final",
                                   root=self.root)[0]
        self.assertIn("--fresh", argv)
        self.assertNotIn("--sections", argv)


class Rows(ImproveCase):
    def rows(self, stages, sections=None):
        return {r["stage"]: r for r in improve.stage_estimates(self.root, stages, sections)}

    def test_a_stage_with_no_report_is_assumed_at_the_default(self):
        write_json(self.root / "sound.json", {"cues": []})
        r = self.rows(["sound"])
        self.assertEqual(r["sound"]["basis"], "assumed")
        self.assertEqual(r["sound"]["usd"], improve.DEFAULT_USD["sound"])

    def test_a_stage_with_a_report_is_measured(self):
        write_json(self.root / "sound.json", {"cues": [], "usage": {"cost_usd": 0.75}})
        r = self.rows(["sound"])["sound"]
        self.assertEqual((r["usd"], r["basis"]), (0.75, "measured"))

    def test_vfx_is_measured_from_its_own_report_not_the_default(self):
        write_vfx(self.root, 91.22, [authored(1, 0, 10, ["1.1"], 91.22)])
        r = self.rows(["vfx"])["vfx"]
        self.assertEqual((r["usd"], r["basis"]), (91.22, "measured"))
        self.assertGreater(r["usd"], improve.VFX_PASS_USD)

    def test_vfx_by_sections_sums_those_sections(self):
        write_vfx(self.root, 30.0, [authored(1, 0, 10, ["1.1"], 10.0),
                                    authored(2, 10, 10, ["2.1"], 12.0),
                                    authored(3, 20, 10, ["3.1"], 8.0)])
        r = self.rows(["vfx"], [2, 3])["vfx"]
        self.assertEqual((r["usd"], r["basis"]), (20.0, "measured"))

    def test_a_section_never_authored_makes_the_row_assumed(self):
        write_vfx(self.root, 10.0, [authored(1, 0, 10, ["1.1"], 10.0)])
        self.assertEqual(self.rows(["vfx"], [1, 2])["vfx"]["basis"], "assumed")

    def test_a_like_project_stands_in_for_a_stage_this_one_never_ran(self):
        like = self.work / "other-film"
        like.mkdir()
        write_json(like / "keyframe.json", {"cost_usd": 7.5})
        r = self.rows(["keyframe"])["keyframe"]
        self.assertEqual((r["usd"], r["basis"]), (7.5, "like"))
        self.assertIn("other-film", r["source"])

    def test_own_report_beats_a_like_project(self):
        write_json(self.root / "keyframe.json", {"cost_usd": 3.0})
        like = self.work / "other-film"
        like.mkdir()
        write_json(like / "keyframe.json", {"cost_usd": 7.5})
        self.assertEqual(self.rows(["keyframe"])["keyframe"]["usd"], 3.0)

    def test_a_zero_on_a_costly_stage_is_a_cache_replay_not_a_measurement(self):
        write_json(self.root / "keyframe.json", {"cost_usd": 0.0})
        self.assertEqual(self.rows(["keyframe"])["keyframe"]["basis"], "assumed")

    def test_the_total_is_the_rows_plus_gate_and_qa(self):
        write_vfx(self.root, 59.53, [authored(1, 0, 10, ["1.1"], 59.53)])
        rows = improve.stage_estimates(self.root, ["vfx", "sound", "build", "render"], None)
        self.assertEqual([r["stage"] for r in rows],
                         ["vfx", "sound", "build", "render", "gate", "qa"])
        self.assertGreater(improve.total(rows), 59.53)


class Loop(ImproveCase):
    def test_dry_run_prints_a_line_per_stage_saying_measured_or_assumed(self):
        write_vfx(self.root, 59.53, [authored(1, 0, 20, ["1.1", "1.4"], 40.0),
                                     authored(2, 20, 20, ["2.1"], 19.53)])
        self.start_pass(card(5.0, mechanical=[mech(shot="1.4")]))
        out = []
        record = self.improver(FakeRunner(self.root, []), dry_run=True, say=out.append).run()
        text = "\n".join(out)
        plan = record["plan"]
        self.assertEqual(plan["sections"], [1])
        self.assertIn("--sections 1", text)
        self.assertNotIn("--fresh", plan["argv"][0])
        by = {r["stage"]: r for r in plan["estimates"]}
        self.assertEqual((by["vfx"]["usd"], by["vfx"]["basis"]), (40.0, "measured"))
        self.assertEqual(by["render"]["basis"], "assumed")
        self.assertRegex(text, r"vfx\s+\$40\.00\s+measured")
        self.assertRegex(text, r"render\s+\$[\d.]+\s+assumed")
        self.assertAlmostEqual(plan["estimate_usd"], improve.total(plan["estimates"]), 2)

    def test_the_budget_stops_before_a_pass_measured_over_it(self):
        write_vfx(self.root, 59.53, [authored(1, 0, 20, ["1.4"], 59.53),
                                     authored(2, 20, 20, ["2.1"], 0.01)])
        self.start_pass(card(5.0, mechanical=[mech(shot="1.4")]))
        runner = FakeRunner(self.root, [card(6.0)])
        record = self.improver(runner, budget_usd=15.0).run()
        self.assertEqual(runner.calls, [])
        self.assertEqual(record["stop"]["reason"], "budget")
        # the measured vfx, not the $10 default; QA's side-by-side comparison
        # with the kept pass is priced too (AUTO-108)
        self.assertIn("$61", record["stop"]["detail"])

    def test_a_pass_runs_vfx_only_for_the_routed_sections(self):
        write_vfx(self.root, 20.0, [authored(1, 0, 20, ["1.4"], 10.0),
                                    authored(2, 20, 20, ["2.1"], 10.0)])
        self.start_pass(card(5.0, mechanical=[mech(shot="1.4")]))
        runner = FakeRunner(self.root, [card(6.0)], costs={"vfx": 5.0})
        record = self.improver(runner, budget_usd=100.0, max_passes=1).run()
        vfx_argv = next(c for c in runner.calls if c[0] == "vfx")
        self.assertEqual(vfx_argv[vfx_argv.index("--sections") + 1], "1")
        self.assertNotIn("--fresh", vfx_argv)
        self.assertEqual(record["passes"][0]["sections"], [1])


if __name__ == "__main__":
    unittest.main()
