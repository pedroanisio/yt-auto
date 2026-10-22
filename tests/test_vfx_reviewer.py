"""AUTO-79: `yt vfx` and `yt keyframe` take `--reviewer-model` and
`--review-budget-usd`, spelled as `yt probe` spells them, and the reviewer
runs as its own model under its own cap; the review reserve is that cap, each
round's review cost is on the record, and the defaults afford the rounds the
estimate prints. The model is never called: the engines are fakes."""

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ytstudio import cli, harness, keyframe, vfx, workspace
from ytstudio.core import Idea
from tests import test_vfx_budget as budget
from tests import test_vfx_world as world


class Flags(unittest.TestCase):
    def parse(self, *argv):
        return cli.build_parser().parse_args(list(argv))

    def test_vfx_and_keyframe_spell_the_reviewer_flags_as_probe_does(self):
        for stage in ("vfx", "keyframe", "probe"):
            ns = self.parse(stage, "--reviewer-model", "haiku", "--review-budget-usd", "1.5")
            self.assertEqual((ns.reviewer_model, ns.review_budget_usd), ("haiku", 1.5), stage)

    def test_the_reviewer_is_not_the_author_by_default(self):
        ns = self.parse("vfx")
        self.assertEqual(ns.reviewer_model, vfx.REVIEWER_MODEL)
        self.assertNotEqual(ns.reviewer_model, ns.model)
        self.assertEqual(ns.review_budget_usd, vfx.REVIEW_USD)

    def test_the_defaults_afford_the_rounds_they_print(self):
        ns = self.parse("vfx")
        e = vfx.estimate_spend(1, ns.revisions, ns.budget_usd, ns.budget_usd,
                               review_usd=ns.review_budget_usd)
        self.assertEqual(e["rounds"], 1 + ns.revisions)
        self.assertLessEqual(e["per_section_usd"], ns.budget_usd)
        self.assertNotIn("stops", vfx.estimate_line(e))
        self.assertEqual(vfx.SECTION_BUDGET_USD,
                         (1 + vfx.DEFAULT_ROUNDS) * (vfx.AUTHOR_SESSION_USD + vfx.REVIEW_USD))
        self.assertEqual(keyframe.LOOK_BUDGET_USD, vfx.SECTION_BUDGET_USD)

    def test_the_estimate_prices_the_review_at_its_cap(self):
        e = vfx.estimate_spend(2, 1, 20, 40, review_usd=3.0)
        self.assertAlmostEqual(e["per_section_usd"], 2 * (vfx.AUTHOR_SESSION_USD + 3.0))
        self.assertIn("$3", vfx.estimate_line(e))

    def test_the_manual_names_the_reviewer_flags(self):
        text = (Path(__file__).resolve().parent.parent / "ytstudio" / "SKILL.md").read_text()
        table = text.split("One word per concept", 1)[1].split("\n## ", 1)[0]
        self.assertIn("`--reviewer-model`", table)
        self.assertIn("`--review-budget-usd`", table)


class Recording(budget.Costed):
    """Costed, and it records the reviewer's model and cap."""

    reviewers: list = []

    def stream(self, prompt, system=None, resume=False):
        if "Write" not in tuple(self.config.tools or ()):
            Recording.reviewers.append((self.config.model, self.config.max_turns_budget_usd))
        yield from super().stream(prompt, system=system, resume=resume)


class Plumbing(budget.Planned):
    engine = Recording

    def setUp(self):
        super().setUp()
        Recording.reviewers = []

    def test_the_reviewer_runs_as_its_own_model_under_its_own_cap(self):
        self.reviews(5, 9)
        self.author(self.planner(budget_usd=40, stage_budget_usd=400, reviewer_model="opus",
                                 review_budget_usd=1.5))
        self.assertEqual(Recording.reviewers, [("opus", 1.5), ("opus", 1.5)])
        authors = [c for c in world.FakeEngine.stream_calls if "Write" in c["tools"]]
        self.assertTrue(authors)

    def test_the_reserve_is_the_review_cap(self):
        self.reviews(6, 6.5, 6.5)
        # round 0: 3 + 1 of 7; the revision gets 7 - 4 - 2 (the cap of the review it owes) = 1
        self.author(self.planner(budget_usd=7, stage_budget_usd=70, review_budget_usd=2.0))
        self.assertEqual(budget.Costed.caps, [4.0, 1.0])

    def test_each_rounds_review_cost_is_on_the_record(self):
        self.reviews(5, 9)
        doc = self.author(self.planner(budget_usd=40, stage_budget_usd=400))[0]
        self.assertEqual([r["review_usd"] for r in doc["rounds"]], [1.0, 1.0])
        self.assertAlmostEqual(doc["budget"]["review_usd"], 2.0)
        self.assertEqual(doc["reviewer"], {"model": vfx.REVIEWER_MODEL,
                                           "budget_usd": vfx.REVIEW_USD})

    def reviews(self, *scores):
        world.FakeEngine.reviews = [{"frames": [], "score": s, "summary": "s"} for s in scores]


class FromArgs(unittest.TestCase):
    def test_planner_from_args_hands_the_flags_to_the_planner(self):
        made: dict = {}

        class Planner(vfx.VfxPlanner):
            def __init__(self, *a, **kw):
                made.update(kw)
                super().__init__(*a, **kw)
        with tempfile.TemporaryDirectory() as tmp:
            args = cli.build_parser().parse_args(
                ["vfx", "--project", tmp, "--reviewer-model", "haiku", "--review-budget-usd",
                 "0.5", "--quiet"])
            idea = Idea(title="T", genre="g", description="d")
            with patch.object(vfx, "VfxPlanner", Planner), \
                    patch.object(vfx.render, "engine", lambda: None), \
                    patch.object(vfx.render, "remote_host", lambda *a, **k: ""):
                vfx.planner_from_args(args, idea, workspace.Project(Path(tmp)))
        self.assertEqual((made["reviewer_model"], made["review_budget_usd"]), ("haiku", 0.5))


class Keyframe(unittest.TestCase):
    def test_the_sheets_reviewer_is_its_own_model_under_its_own_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = workspace.Project(Path(tmp))
            idea = Idea(title="T", genre="g", description="d")
            stage = keyframe.KeyframeStage(project, idea, model="sonnet", reviewer_model="opus",
                                           review_budget_usd=1.0, verbose=False)
            reviewer = stage._reviewer()
            self.assertEqual((reviewer.model, reviewer.budget_usd), ("opus", 1.0))
            self.assertEqual(stage._harness(keyframe.ELEMENTS_ROLE).model, "sonnet")
            default = keyframe.KeyframeStage(project, idea, model="sonnet", verbose=False)
            self.assertEqual(default._reviewer().model, vfx.REVIEWER_MODEL)

    def test_run_hands_the_sheet_stage_the_reviewer_flags(self):
        made: dict = {}

        class Stage:
            def __init__(self, *a, **kw):
                made.update(kw)

            def run(self):
                raise harness.HarnessError("stop here")
        with tempfile.TemporaryDirectory() as tmp:
            args = cli.build_parser().parse_args(
                ["keyframe", "--project", tmp, "--sheet", "--reviewer-model", "haiku",
                 "--review-budget-usd", "0.5", "--quiet"])
            with patch.object(keyframe, "KeyframeStage", Stage), \
                    patch.object(keyframe.render, "engine", lambda: None), \
                    patch.object(keyframe.render, "remote_host", lambda *a, **k: "root@fast"):
                keyframe.run(args)
        self.assertEqual((made["reviewer_model"], made["review_budget_usd"]), ("haiku", 0.5))


if __name__ == "__main__":
    unittest.main()
