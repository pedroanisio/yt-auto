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
                     "severity": "major"}],
         "monotony": []}

#: AUTO-143: a run of sections that never leaves one set, named by the reviewer.
MONOTONY = [{"sections": ["Open", "Later"], "owner_section": "Later",
             "problem": "both sections sit on the same grey desk at the same scale",
             "fix": "cut Later to the real 1953 lab, archival footage"}]


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


class ReviewerRecord(ContinuityCase):
    """AUTO-153: the continuity verdict says who gave it, so QA can tell a
    reviewer of another family (whose blocking break blocks) from the
    author's own model or an unrecorded one (whose never does)."""

    def test_continuity_is_reviewed_by_the_reviewer_model(self):
        authored = self.author()
        world.FakeEngine.reviews = [dict(BREAK)]
        made = []
        real = harness.Harness

        def record(*args, **kwargs):
            made.append(kwargs)
            return real(*args, **kwargs)
        with patch.object(vfx.harness, "Harness", record):
            self.planner.continuity(authored)
        self.assertEqual([m["model"] for m in made], [self.planner.reviewer_model])
        self.assertNotEqual(made[0]["model"], self.planner.model)

    def test_continuity_records_reviewer_author_and_engine(self):
        expect = {"model": self.planner.reviewer_model, "author": "sonnet",
                  "authors": ["sonnet"], "engine": harness.ENGINE_NAME}
        authored = self.author()
        world.FakeEngine.reviews = [dict(BREAK)]
        self.assertEqual(self.planner.continuity(authored)["reviewer"], expect)
        # an answer without monotony fails its form: the error form says who too
        self.planner.fresh = True
        world.FakeEngine.reviews = [{k: v for k, v in BREAK.items() if k != "monotony"}]
        failed = self.planner.continuity(authored)
        self.assertIn("error", failed)
        self.assertEqual(failed["reviewer"], expect)
        skipped = self.planner.continuity(authored[:1])
        self.assertIn("skipped", skipped)
        self.assertEqual(skipped["reviewer"], expect)

    def test_authored_record_keeps_its_bar(self):
        self.planner.author_bar = 6.5
        first = self.author()
        self.assertEqual([r["bar"] for r in first], [6.5, 6.5])
        # carried into a --sections run, a record keeps the bar it was reviewed against
        idea = Idea(title="T", genre="g", description="d")
        bundle = {"idea": idea, "outline": {}, "claims": [], "assets": [], "premise": ""}
        self.planner = vfx.VfxPlanner(
            bundle, Dossier(self.root, idea, at=self.root), self.ws, model="sonnet",
            sections=self.sections, revisions=0, author_bar=9.0, verbose=False,
            sections_only={2})
        again = {r["n"]: r for r in self.author()}
        self.assertEqual(again[1]["carried_from"], "v01")
        self.assertEqual(again[1]["bar"], 6.5)
        self.assertEqual(again[2]["bar"], 9.0)


    def test_authored_record_names_its_author_and_a_carry_keeps_it(self):
        self.planner.model, self.planner.reviewer_model = "opus", "sonnet"
        self.assertEqual([r["author"] for r in self.author()], ["opus", "opus"])
        idea = Idea(title="T", genre="g", description="d")
        bundle = {"idea": idea, "outline": {}, "claims": [], "assets": [], "premise": ""}
        self.planner = vfx.VfxPlanner(
            bundle, Dossier(self.root, idea, at=self.root), self.ws, model="sonnet",
            reviewer_model="opus", sections=self.sections, revisions=0, author_bar=0.0,
            verbose=False, sections_only={2})
        again = {r["n"]: r for r in self.author()}
        self.assertEqual((again[1]["author"], again[2]["author"]), ("opus", "sonnet"))

    def test_a_carried_section_by_the_reviewers_family_is_not_judged_apart(self):
        # v01: opus authors, sonnet reviews. v02 re-authors section 2 on sonnet
        # with opus reviewing: section 1 is still opus's own work, so opus's
        # verdict on the film is not one from another family.
        self.planner.model, self.planner.reviewer_model = "opus", "sonnet"
        self.author()
        idea = Idea(title="T", genre="g", description="d")
        bundle = {"idea": idea, "outline": {}, "claims": [], "assets": [], "premise": ""}
        self.planner = vfx.VfxPlanner(
            bundle, Dossier(self.root, idea, at=self.root), self.ws, model="sonnet",
            reviewer_model="opus", sections=self.sections, revisions=0, author_bar=0.0,
            verbose=False, sections_only={2})
        authored = self.author()
        world.FakeEngine.reviews = [dict(BREAK)]
        result = self.planner.continuity(authored)
        self.assertEqual(result["reviewer"]["authors"], ["opus", "sonnet"])
        self.assertFalse(vfx.judged_apart(result))

    def test_a_section_with_no_recorded_author_is_not_judged_apart(self):
        authored = self.author()
        authored[0].pop("author")       # a record written before authors were kept
        world.FakeEngine.reviews = [dict(BREAK)]
        result = self.planner.continuity(authored)
        self.assertEqual(result["reviewer"]["authors"], ["sonnet", None])
        self.assertFalse(vfx.judged_apart(result))
        authored[0]["author"] = "sonnet"
        self.planner.fresh = True
        world.FakeEngine.reviews = [dict(BREAK)]
        self.assertTrue(vfx.judged_apart(self.planner.continuity(authored)))

    def test_the_engine_named_is_the_one_the_reviewer_ran_on(self):
        authored = self.author()
        world.FakeEngine.reviews = [dict(BREAK)]
        with patch.object(vfx.harness, "ENGINE_NAME", "codex"):
            self.assertEqual(self.planner.continuity(authored)["reviewer"]["engine"], "codex")


class OwnerSection(unittest.TestCase):
    """The continuity review names a section by its heading (the frames it
    judged are labelled so); the join to its number is shared by QA and
    doctor."""

    AUTHORED = [{"n": 1, "section": "Open"}, {"n": 2, "section": "The Pressure  Builds"},
                {"n": 3, "section": "Twice"}, {"n": 4, "section": "twice"}]

    def test_owner_section_joins_by_heading_exact_then_folded(self):
        self.assertEqual(vfx.owner_section_n("Open", self.AUTHORED), 1)
        self.assertEqual(vfx.owner_section_n(" the pressure builds\n", self.AUTHORED), 2)
        # an exact match wins over a folded one that is ambiguous
        self.assertEqual(vfx.owner_section_n("Twice", self.AUTHORED), 3)
        self.assertEqual(vfx.owner_section_n("twice", self.AUTHORED), 4)

    def test_owner_section_with_no_match_gives_none(self):
        self.assertIsNone(vfx.owner_section_n("Elsewhere", self.AUTHORED))
        self.assertIsNone(vfx.owner_section_n("TWICE", self.AUTHORED))     # ambiguous
        self.assertIsNone(vfx.owner_section_n("Open", [{"n": 1, "section": "Open"},
                                                       {"n": 5, "section": "Open"}]))
        self.assertIsNone(vfx.owner_section_n("", self.AUTHORED))
        self.assertIsNone(vfx.owner_section_n("Open", []))
        self.assertIsNone(vfx.owner_section_n("Open", [{"section": "Open"}, "x"]))


class Monotony(ContinuityCase):
    """AUTO-143: the continuity review also names a film that stays in one
    place, sees one frame per world, and is the reviewer's model, never the
    author's."""

    def two_worlds(self):
        self.planner.jobs = 1                  # one author at a time: docs in order
        world.FakeEngine.docs = [world.TWO_WORLD_DOC,
                                 world.WORLD_DOC.replace("world-open", "world-later")]
        groups = [world.SETTINGS_GROUP,
                  dict(self.groups[1], setting="a storm over the North Sea")]
        return self.author(groups=groups)

    def test_a_section_with_two_worlds_shows_a_frame_of_each_with_its_setting(self):
        authored = self.two_worlds()
        world.FakeEngine.reviews = [dict(BREAK)]
        result = self.planner.continuity(authored)
        rows = [(f["section"], f["symbol"], f["setting"]) for f in result["frames"]]
        self.assertEqual(rows, [
            ("Open", "world-open", "1953 Cavendish lab, archival footage"),
            ("Open", "world-open-2", "inside one cell membrane, molecular scale"),
            ("Later", "world-later", "a storm over the North Sea")])
        for f in result["frames"]:
            self.assertIn(f"__{f['symbol']}__", f["frame"])
        prompt = world.FakeEngine.stream_calls[-1]["prompt"]
        self.assertIn("inside one cell membrane, molecular scale", prompt)

    def test_a_section_shows_two_worlds_at_most(self):
        authored = self.two_worlds()
        three = dict(authored[0], worlds=authored[0]["worlds"] + [
            dict(authored[0]["worlds"][1], id="world-open-3", setting="a third")])
        frames = Path(self.root / three["file"]).parent / "frames"
        stem = Path(three["file"]).name[:-len(".scene.xml")]
        (frames / f"{stem}__world-open-3__1.5s.png").write_bytes(world.varied_png())
        world.FakeEngine.reviews = [dict(BREAK)]
        result = self.planner.continuity([three, authored[1]])
        self.assertEqual([f["symbol"] for f in result["frames"]],
                         ["world-open", "world-open-2", "world-later"])

    def test_the_review_runs_on_the_reviewer_model(self):
        authored = self.author()
        world.FakeEngine.reviews = [dict(BREAK)]
        before = len(world.FakeEngine.stream_calls)
        self.planner.continuity(authored)
        call = world.FakeEngine.stream_calls[before]
        self.assertEqual(call["model"], self.planner.reviewer_model)
        self.assertNotEqual(call["model"], self.planner.model)

    def test_the_review_is_capped_as_a_section_review_is(self):
        authored = self.author()
        world.FakeEngine.reviews = [dict(BREAK)]
        made = []
        real = harness.Harness

        def record(*args, **kwargs):
            made.append(kwargs)
            return real(*args, **kwargs)
        self.planner.review_budget_usd = 0.75
        with patch.object(vfx.harness, "Harness", record):
            self.planner.continuity(authored)
        self.assertEqual(len(made), 1)
        self.assertEqual(made[0]["budget_usd"], 0.75)

    def test_the_schema_requires_monotony_and_the_role_asks_for_it(self):
        self.assertIn("monotony", vfx.CONTINUITY_SCHEMA["required"])
        item = vfx.CONTINUITY_SCHEMA["properties"]["monotony"]["items"]
        self.assertEqual(sorted(item["required"]),
                         ["fix", "owner_section", "problem", "sections"])
        self.assertIn("a change of setting the plan lists is not a break",
                      vfx.CONTINUITY_ROLE)
        self.assertIn("stays in one set, scale and look", vfx.CONTINUITY_ROLE)

    def test_an_answer_without_monotony_is_asked_again_then_fails(self):
        authored = self.author()
        world.FakeEngine.reviews = [{k: v for k, v in BREAK.items() if k != "monotony"}]
        asked = len(world.FakeEngine.json_calls)
        result = self.planner.continuity(authored)
        self.assertEqual(len(world.FakeEngine.json_calls), asked + 1)   # the form pass
        self.assertIn("error", result)
        self.assertIn("monotony", result["error"])

    def test_monotony_reaches_vfx_json(self):
        authored = self.author()
        world.FakeEngine.reviews = [dict(BREAK, monotony=MONOTONY)]
        stubs = {"outline": {"sections": self.sections}, "style": world.STYLE,
                 "effects": self.groups, "author": authored,
                 "scene_map": {}, "reach": {}, "plan": {}}
        with patch.multiple(self.planner, **{k: (lambda *a, v=v, **kw: v)
                                             for k, v in stubs.items()}):
            out = self.planner.run()
        self.assertEqual(out["continuity"]["monotony"], MONOTONY)


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
                     if f.area == "vfx" and f.level == "warn" and "continuity" in f.message]
            self.assertEqual(len(warns), 1)
            self.assertIn("Later", warns[0].message)
            self.assertIn("twice the size", warns[0].message)

    def report(self, continuity, authored=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "a-film"
            root.mkdir()
            write_json(root / "idea.json", {"title": "A film", "genre": "g",
                                            "description": "d"})
            write_json(root / "vfx.json", {"totals": {}, "plan": {},
                                           "continuity": continuity,
                                           **({"authored": authored} if authored else {})})
            self.root = str(root)
            return doctor.check_project(workspace.Project(root))

    # -- AUTO-153: one row per serious break's section, routed to it ------------

    AUTHORED = [{"n": 1, "section": "Open", "score": 8, "bar": 7, "review": {}},
                {"n": 2, "section": "Later", "score": 8, "bar": 7, "review": {}}]
    REVIEWER = {"model": "opus", "author": "sonnet", "engine": "claude"}

    def breaks(self, *rows, reviewer=REVIEWER, one_film=False):
        out = {"one_film": one_film, "score": 4, "summary": "two films", "monotony": [],
               "breaks": [{"sections": ["Open", "Later"], "owner_section": owner,
                           "problem": problem, "fix": "fix it", "severity": severity}
                          for owner, problem, severity in rows]}
        return dict(out, reviewer=reviewer) if reviewer is not None else out

    def continuity_rows(self, report):
        return [f for f in report.findings if f.area == "vfx"
                and (f.message.startswith("continuity") or "one film" in f.message)]

    def test_each_serious_break_is_one_warn_with_its_sections_fix(self):
        report = self.report(self.breaks(("Later", "the hero doubles", "blocking"),
                                         ("Open", "the grade turns green", "major"),
                                         ("Open", "a tick mark moves", "minor")),
                             self.AUTHORED)
        rows = self.continuity_rows(report)
        self.assertEqual([(r.level, r.message, r.fix) for r in rows], [
            ("warn", "continuity: section 2 (Later): the hero doubles [blocking; reviewer opus]",
             f"yt vfx --project {self.root} --sections 2"),
            ("warn", "continuity: section 1 (Open): the grade turns green [major; reviewer opus]",
             f"yt vfx --project {self.root} --sections 1")])

    def test_two_breaks_owned_by_one_section_are_one_row(self):
        report = self.report(self.breaks(("Later", "the hero doubles", "major"),
                                         ("  later ", "the light flips", "blocking")),
                             self.AUTHORED)
        rows = self.continuity_rows(report)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].message, "continuity: section 2 (Later): the hero doubles; "
                         "the light flips [blocking; reviewer opus]")
        self.assertTrue(rows[0].fix.endswith("--sections 2"))

    def test_unmatched_owner_gives_a_plain_vfx_fix(self):
        report = self.report(self.breaks(("Elsewhere", "the hero doubles", "major")),
                             self.AUTHORED)
        rows = self.continuity_rows(report)
        self.assertEqual([(r.message, r.fix) for r in rows], [
            ("continuity: Elsewhere: the hero doubles [major; reviewer opus]",
             f"yt vfx --project {self.root}")])

    def test_unmatched_owners_spelled_two_ways_are_one_row(self):
        # the same fold the join uses: one owner retyped is still one section
        report = self.report(self.breaks(("Elsewhere", "the hero doubles", "major"),
                                         (" elsewhere", "the light flips", "major")),
                             self.AUTHORED)
        rows = self.continuity_rows(report)
        self.assertEqual([r.message for r in rows], [
            "continuity: Elsewhere: the hero doubles; the light flips [major; reviewer opus]"])

    def test_one_film_false_with_no_serious_break_is_one_plain_warn(self):
        report = self.report(self.breaks(("Later", "a tick mark moves", "minor")),
                             self.AUTHORED)
        rows = self.continuity_rows(report)
        self.assertEqual(len(rows), 1)
        self.assertIn("do not read as one film (4/10)", rows[0].message)
        self.assertIn("a tick mark moves", rows[0].message)
        self.assertEqual(rows[0].fix, f"yt vfx --project {self.root}")

    def test_a_carried_author_of_the_reviewers_family_says_why_qa_does_not_block(self):
        # the reviewer is of another family than this run's author, but a
        # carried section's author is unrecorded or of the reviewer's own
        # family: the row says so, and that re-authoring it lets QA block again
        note = (" (not blocking QA: a section's author is not recorded or shares the "
                "reviewer's family; re-author it or run `yt vfx --fresh`)")
        for authors in (["sonnet", None], ["opus", "sonnet"]):
            with self.subTest(authors=authors):
                reviewer = dict(self.REVIEWER, authors=authors)
                report = self.report(self.breaks(("Later", "the hero doubles", "blocking"),
                                                 reviewer=reviewer), self.AUTHORED)
                rows = self.continuity_rows(report)
                self.assertEqual(len(rows), 1)
                self.assertTrue(rows[0].message.endswith("]" + note), rows[0].message)

    def test_an_authoring_that_is_not_a_record_does_not_stop_the_check(self):
        # a hand-edited vfx.json: the bar falls back to 7
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "a-film"
            root.mkdir()
            write_json(root / "idea.json", {"title": "A film", "genre": "g",
                                            "description": "d"})
            write_json(root / "vfx.json", {"totals": {}, "plan": {}, "authoring": "7.5",
                                           "authored": [{"n": 1, "section": "Open",
                                                         "score": 6, "review": {}}]})
            report = doctor.check_project(workspace.Project(root))
            self.assertTrue([f for f in report.findings if f.area == "vfx"
                             and "under its bar 7 " in f.message])

    def test_same_family_blocking_break_says_it_does_not_block_qa(self):
        note = " (not blocking QA: no reviewer of another family recorded)"
        for reviewer in ({"model": "opus[1m]", "author": "opus", "engine": "claude"}, None):
            with self.subTest(reviewer=reviewer):
                report = self.report(self.breaks(("Later", "the hero doubles", "blocking"),
                                                 reviewer=reviewer), self.AUTHORED)
                rows = self.continuity_rows(report)
                self.assertEqual(len(rows), 1)
                self.assertTrue(rows[0].message.endswith("]" + note), rows[0].message)
        # a reviewer of another family: QA blocks, so the row says nothing of it
        report = self.report(self.breaks(("Later", "the hero doubles", "blocking")),
                             self.AUTHORED)
        self.assertNotIn("not blocking QA", self.continuity_rows(report)[0].message)
        # a major break never blocks QA, so it never says it does not
        report = self.report(self.breaks(("Later", "the hero doubles", "major"),
                                         reviewer=None), self.AUTHORED)
        row = self.continuity_rows(report)[0]
        self.assertNotIn("not blocking QA", row.message)
        self.assertIn("reviewer not recorded", row.message)

    def test_skipped_and_error_continuity_are_a_note(self):
        for key, text in (("skipped", "fewer than two sections have proof frames"),
                          ("error", "the reviewer timed out")):
            with self.subTest(key=key):
                report = self.report({key: text, "frames": [], "breaks": []}, self.AUTHORED)
                rows = [f for f in report.findings if f.area == "vfx"
                        and "continuity" in f.message]
                self.assertEqual([(r.level, r.message) for r in rows],
                                 [("note", f"continuity was not judged: {text}")])

    def test_no_continuity_row_is_a_fail(self):
        cases = [self.breaks(("Later", "x", "blocking")),
                 self.breaks(("Later", "x", "blocking"), reviewer=None),
                 self.breaks(("Elsewhere", "x", "major")),
                 self.breaks(("Later", "x", "minor")),
                 {"error": "boom", "breaks": []}, {"skipped": "one", "breaks": []}]
        for continuity in cases:
            with self.subTest(continuity=continuity):
                report = self.report(continuity, self.AUTHORED)
                self.assertFalse([f for f in report.findings
                                  if f.area == "vfx" and f.level == "fail"])

    def test_a_film_that_stays_in_one_setting_is_one_warning_naming_the_sections(self):
        one = {"one_film": True, "score": 8, "breaks": [], "summary": "one film",
               "monotony": MONOTONY}
        report = self.report(one)
        warns = [f for f in report.findings
                 if f.area == "vfx" and f.level == "warn" and "one setting" in f.message]
        self.assertEqual(len(warns), 1)
        self.assertIn("sections Open to Later", warns[0].message)
        self.assertIn("same grey desk", warns[0].message)
        self.assertIn("archival footage", warns[0].fix)
        self.assertFalse([f for f in report.findings if f.level == "fail"
                          and f.area == "vfx"])

    def test_at_most_three_monotony_runs_are_reported(self):
        runs = [dict(MONOTONY[0], owner_section=f"S{n}", problem=f"run {n}")
                for n in range(4)]
        report = self.report({"one_film": True, "score": 8, "breaks": [],
                              "summary": "one film", "monotony": runs})
        warns = [f for f in report.findings
                 if f.area == "vfx" and f.level == "warn" and "one setting" in f.message]
        self.assertEqual([w.message.endswith(f"run {n}") for n, w in enumerate(warns)],
                         [True, True, True])

    def test_a_continuity_from_before_monotony_gives_no_warning(self):
        old = {"one_film": True, "score": 8, "breaks": [], "summary": "one film"}
        report = self.report(old)
        self.assertFalse([f for f in report.findings if f.area == "vfx" and (
            f.level == "fail" or "setting" in f.message or "one film" in f.message)])


if __name__ == "__main__":
    unittest.main()
