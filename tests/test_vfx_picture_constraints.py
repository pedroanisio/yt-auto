"""AUTO-98: the story's binding flags reach the people who draw the film.

The fact-check already binds the picture (`vfx.picture_constraints` reads
script.json `check`). The story review's flags (`review.flags`, the
coverage-shaped review of narrative.REVIEW_SCHEMA) and the picture-check's
standing flags (`picture.flags`, `yt script --review`) ride the same path:
a blocker or important one reaches the vfx planner, each section's author
brief and reviewer, and the keyframe brief, under `## Story constraints`,
the whole-film ones everywhere and a shot's in the section that holds it.
A minor one binds nothing. The model is never called and no host is used.
"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ytstudio import keyframe, vfx
from ytstudio.core import Idea, write_json
from ytstudio.research import Dossier

from tests.test_keyframe import SCRIPT as KEYFRAME_SCRIPT, ProjectCase
from tests.test_vfx_delta_review import Case as AuthoringCase, review
from tests.test_vfx_world import EFFECTS, SECTION, STYLE

SCRIPT = {"sections": [
    {"heading": "Open", "shots": [{"n": 1, "label": "1.1"}, {"n": 2, "label": "1.2"},
                                  {"n": 3, "label": "1.3"}, {"n": 4, "label": "1.4"}]},
    {"heading": "Two", "shots": [{"n": 5, "label": "2.1"}]}],
    "check": {"verdict": "ok", "flags": [
        {"n": 5, "severity": "blocker", "problem": "shot 2.1 labels a model wrongly",
         "fix": "drop the label"}]},
    "review": {"verdict": "consider", "flags": [
        {"n": 0, "section": 0, "criterion": "stakes", "severity": "important",
         "problem": "the stakes are never shown happening to anyone",
         "fix": "show the people on the bridge"},
        {"n": 2, "section": 1, "criterion": "reveal_setup", "severity": "minor",
         "problem": "a cosmetic quibble", "fix": "-"}]},
    "picture": {"flags": [
        {"n": 3, "severity": "important", "kind": "picture:unshown-claim",
         "problem": "picture:unshown-claim: shot 1.3: the glide is said to lose lift; "
                    "nothing shows the lift", "fix": "make the picture demonstrate it"},
        {"n": 5, "severity": "important", "kind": "picture:bare-number",
         "problem": "picture:bare-number: shot 2.1: 40,000 tonnes, bare",
         "fix": "pair the figure with a comparison"}]}}


class Rows(unittest.TestCase):
    def test_review_and_picture_flags_bind_beside_the_fact_check(self):
        rows = vfx.picture_constraints(SCRIPT)
        got = [(r["source"], r["label"], r["severity"]) for r in rows]
        self.assertEqual(got, [("check", "2.1", "blocker"), ("review", "", "important"),
                               ("picture", "1.3", "important"),
                               ("picture", "2.1", "important")])
        self.assertNotIn("a cosmetic quibble", str(rows))        # minor binds nothing

    def test_the_story_flags_are_their_own_block(self):
        rows = vfx.picture_constraints(SCRIPT)
        one = "\n".join(vfx.constraint_lines(rows, {"1.1", "1.2", "1.3", "1.4"}))
        two = "\n".join(vfx.constraint_lines(rows, {"2.1"}))
        self.assertIn("## Story constraints", one)
        self.assertNotIn("## Fact-check constraints", one)        # 2.1's only
        self.assertIn("whole film: the stakes are never shown happening to anyone", one)
        self.assertIn("REQUIRED: show the people on the bridge", one)
        self.assertIn("shot 1.3: picture:unshown-claim", one)
        self.assertNotIn("40,000 tonnes", one)
        self.assertIn("## Fact-check constraints", two)
        self.assertIn("## Story constraints", two)
        self.assertLess(two.index("## Fact-check constraints"), two.index("## Story constraints"))
        self.assertIn("40,000 tonnes", two.split("## Story constraints")[1])
        self.assertNotIn("labels a model", two.split("## Story constraints")[1])

    def test_a_script_without_them_reads_as_before(self):
        plain = {"sections": SCRIPT["sections"], "check": SCRIPT["check"]}
        rows = vfx.picture_constraints(plain)
        self.assertEqual([r["source"] for r in rows], ["check"])
        self.assertNotIn("## Story constraints", "\n".join(vfx.constraint_lines(rows)))
        self.assertEqual(vfx.picture_constraints({"sections": [], "review": "x",
                                                  "picture": None}), [])


class SectionBrief(unittest.TestCase):
    def test_the_authors_brief_carries_its_story_constraints(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = vfx.Workspace(Path(tmp), version="v01")
            path = ws.brief(1, idea=Idea(title="T", genre="g", description="d"),
                            house_rules="", style=STYLE, tokens={"ink": "#FFFFFF"},
                            frame=(1920, 1080), section=SECTION, effects=EFFECTS, words={},
                            assets=[], worlds=vfx.world_symbols(SECTION, EFFECTS),
                            constraints=vfx.picture_constraints(SCRIPT))
            text = path.read_text(encoding="utf-8")
        self.assertIn("## Story constraints", text)
        self.assertIn("the stakes are never shown happening to anyone", text)
        self.assertIn("nothing shows the lift", text)
        self.assertNotIn("40,000 tonnes", text)                   # section two's


class Planner(unittest.TestCase):
    def test_the_planners_section_prompt_carries_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            idea = Idea(title="T", genre="g", description="d")
            bundle = {"idea": idea, "outline": {}, "claims": [], "assets": [], "premise": "",
                      "picture_check": vfx.picture_constraints(SCRIPT)}
            planner = vfx.VfxPlanner(bundle, Dossier(root, idea, at=root), vfx.Workspace(root),
                                     model="sonnet", verbose=False)
            seen = []

            def decide(name, prompt, schema):
                seen.append(prompt)
                return {"effects": [], "cuts": []}
            with patch.object(planner, "_decide", decide), \
                    patch.object(planner, "vocabulary", lambda: ""):
                planner._section_effects(SECTION, STYLE)
        self.assertIn("## Story constraints", seen[0])
        self.assertIn("nothing shows the lift", seen[0])


class Reviewer(AuthoringCase):
    def test_the_section_reviewer_and_author_are_held_to_them(self):
        harness_reviews = [review(8)]
        from tests.test_vfx_world import FakeEngine
        FakeEngine.reviews = harness_reviews
        planner = self.planner(revisions=0)
        planner.bundle["picture_check"] = vfx.picture_constraints(SCRIPT)
        self.author(planner)
        [first] = self.reviews()
        self.assertIn("## Story constraints", first["prompt"])
        self.assertIn("nothing shows the lift", first["prompt"])
        brief = (self.root / "vfx" / "v01" / "brief-01.md").read_text(encoding="utf-8")
        self.assertIn("## Story constraints", brief)


class Keyframe(ProjectCase):
    def test_the_keyframe_brief_carries_the_story_constraints(self):
        data = copy.deepcopy(KEYFRAME_SCRIPT)
        data["review"] = {"verdict": "consider", "flags": [
            {"n": 0, "section": 0, "criterion": "ending", "severity": "important",
             "problem": "the plane never lands where the story ends",
             "fix": "show the landing"}]}
        write_json(self.root / "script.json", data)
        self.run_stage()
        brief = (self.root / "keyframe" / keyframe.BRIEF_NAME).read_text()
        self.assertIn("## Story constraints", brief)
        self.assertIn("the plane never lands where the story ends", brief)
        self.assertIn("show the landing", brief)


if __name__ == "__main__":
    unittest.main()
