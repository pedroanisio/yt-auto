"""Sections are authored apart; the film is watched together.

Each section's author and reviewer judge one section's frames, so a film can
pass every section review and still change its hero, its light or its grade
at every section boundary -- the droplet film did, three times. After the
sections are authored, one reviewer looks at a frame from each side by side
and says whether they read as one film, naming the section that breaks it.
And every agent the stage starts, authors and reviewers alike, reports into
the planner's one account, so vfx.json's `usage` is the stage's whole bill.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import test_vfx_world as world  # noqa: E402

from ytstudio import doctor, harness, vfx, workspace  # noqa: E402
from ytstudio.core import Idea, write_json  # noqa: E402
from ytstudio.research import Dossier  # noqa: E402

LATER = dict(world.SECTION, heading="Later",
             shots=[dict(s, at=s["at"] + 30) for s in world.SECTION["shots"]])

BREAK = {"one_film": False, "score": 4, "summary": "two films",
         "breaks": [{"sections": ["Open", "Later"], "owner_section": "Later",
                     "problem": "the hero is twice the size in Later",
                     "fix": "scale the hero to the size it has in Open",
                     "severity": "major"}]}


class ContinuityCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._engine = harness.ENGINE
        harness.ENGINE = world.FakeEngine
        world.FakeEngine.stream_calls, world.FakeEngine.json_calls = [], []
        world.FakeEngine.reviews, world.FakeEngine.doc = [], world.WORLD_DOC
        world.FakeEngine.time_out_on = 0
        idea = Idea(title="T", genre="g", description="d")
        bundle = {"idea": idea, "outline": {}, "claims": [], "assets": [], "premise": ""}
        self.ws = vfx.Workspace(self.root)
        self.sections = [world.SECTION, LATER]
        self.planner = vfx.VfxPlanner(
            bundle, Dossier(self.root, idea, at=self.root), self.ws, model="sonnet",
            sections=self.sections, revisions=0, author_bar=0.0, verbose=False)
        self.groups = [{"section": s["heading"], "seconds": 15, "effects": world.EFFECTS,
                        "cuts": []} for s in self.sections]

    def tearDown(self):
        harness.ENGINE = self._engine
        self._tmp.cleanup()

    def author(self, sections=None, groups=None):
        still = lambda xml, at, **kw: (world.varied_png(), "rendered locally")  # noqa: E731
        with patch.object(vfx.render, "engine_validate", lambda data, **kw: []), \
                patch.object(vfx.render, "engine_render_still", still), \
                patch.object(vfx, "encode_findings", lambda *a, **k: []):
            return self.planner.author({"sections": sections or self.sections},
                                       groups or self.groups, world.STYLE,
                                       {"ink": "#FFFFFF"})


class OneAccount(ContinuityCase):
    def test_every_author_and_reviewer_cost_reaches_the_planners_account(self):
        authored = self.author()
        self.assertEqual(len(authored), 2)
        spent = sum(d["cost_usd"] for d in authored)
        self.assertGreater(spent, 0)
        self.assertAlmostEqual(self.planner.usage.cost_usd, spent, places=4)
        agents = len(world.FakeEngine.stream_calls)
        self.assertEqual(self.planner.usage.calls, agents)

    def test_a_section_that_raises_after_its_session_still_reports_its_cost(self):
        # A reviewer's inspection raised something other than a HarnessError
        # (a FileNotFoundError from the shared tree, as section threads racing
        # each other's record writes once produced): the section's record is
        # the failure, but its author session was paid for, so the planner's
        # account and the record both carry it.
        real = harness._tree_digest
        raised = []

        def once(root):
            if not raised:
                raised.append(root)
                raise FileNotFoundError(root / "rounds" / "01-open" / ".record.json.1.tmp")
            return real(root)
        with patch.object(harness, "_tree_digest", once):
            authored = self.author()
        failed = [r for r in authored
                  if any(f.startswith("error: the authoring") for f in r["findings"])]
        self.assertEqual(len(failed), 1)
        self.assertIn("FileNotFoundError", failed[0]["findings"][0])
        self.assertAlmostEqual(failed[0]["cost_usd"], 0.02, places=4)   # its author session
        spent = sum(d["cost_usd"] for d in authored)
        self.assertAlmostEqual(self.planner.usage.cost_usd, spent, places=4)
        self.assertEqual(self.planner.usage.calls, len(world.FakeEngine.stream_calls))


    def test_a_revision_does_not_count_the_first_round_twice(self):
        self.planner.revisions, self.planner.author_bar = 1, 7.0
        low = {"frames": [], "score": 5, "summary": "blockout"}
        high = {"frames": [], "score": 8, "summary": "reads"}
        world.FakeEngine.reviews = [low, high]
        authored = self.author(sections=[world.SECTION], groups=self.groups[:1])
        self.assertEqual(authored[0]["revisions"], 1)
        self.assertAlmostEqual(self.planner.usage.cost_usd, authored[0]["cost_usd"], places=4)


class Continuity(ContinuityCase):
    def test_one_frame_per_section_is_shown_side_by_side(self):
        authored = self.author()
        world.FakeEngine.reviews = [dict(BREAK)]
        before = len(world.FakeEngine.stream_calls)
        result = self.planner.continuity(authored)
        calls = world.FakeEngine.stream_calls[before:]
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertNotIn("Write", call["tools"])
        self.assertEqual(Path(call["cwd"]), self.root / "vfx" / "v01")
        for stem in ("01-open", "02-later"):
            self.assertIn(f"frames/{stem}__", call["prompt"])
        self.assertEqual([f["section"] for f in result["frames"]], ["Open", "Later"])
        self.assertFalse(result["one_film"])
        self.assertEqual(result["breaks"][0]["owner_section"], "Later")

    def test_its_cost_joins_the_account(self):
        authored = self.author()
        spent = self.planner.usage.cost_usd
        world.FakeEngine.reviews = [dict(BREAK)]
        self.planner.continuity(authored)
        self.assertGreater(self.planner.usage.cost_usd, spent)

    def test_a_single_section_has_nothing_to_compare(self):
        authored = self.author(sections=[world.SECTION], groups=self.groups[:1])
        before = len(world.FakeEngine.stream_calls)
        result = self.planner.continuity(authored)
        self.assertEqual(len(world.FakeEngine.stream_calls), before)
        self.assertIn("skipped", result)
        self.assertEqual(result["breaks"], [])

    def test_the_run_records_continuity_in_vfx_json(self):
        authored = self.author()
        world.FakeEngine.reviews = [dict(BREAK)]
        stubs = {"outline": {"sections": self.sections}, "style": world.STYLE,
                 "effects": self.groups, "author": authored,
                 "scene_map": {}, "reach": {}, "plan": {}}
        with patch.multiple(self.planner, **{k: (lambda *a, v=v, **kw: v)
                                             for k, v in stubs.items()}):
            out = self.planner.run()
        self.assertFalse(out["continuity"]["one_film"])
        self.assertIn("usage", out)


class DoctorReadsContinuity(unittest.TestCase):
    def test_a_film_that_does_not_read_as_one_is_a_warning_naming_the_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "a-film"
            root.mkdir()
            write_json(root / "idea.json", {"title": "A film", "genre": "g",
                                            "description": "d"})
            write_json(root / "vfx.json", {"totals": {}, "plan": {}, "continuity": BREAK})
            report = doctor.check_project(workspace.Project(root))
            warns = [f for f in report.findings
                     if f.area == "vfx" and f.level == "warn" and "one film" in f.message]
            self.assertEqual(len(warns), 1)
            self.assertIn("Later", warns[0].message)
            self.assertIn("twice the size", warns[0].message)


if __name__ == "__main__":
    unittest.main()
