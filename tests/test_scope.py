"""What a stage is told its job is scoped to.

Research adapts its plan to the genre: a wordless cinematic piece needs
reference, look and the physics of what is on screen, not a fact-check of
claims nobody will speak. Styles is told what the production stack is -- one
renderer, scene-render -- and what the assessment found it can render,
approximate or not do, so a treatment is judged against what can be made.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_engine import Usage  # noqa: E402
from ytstudio import assess, harness, research, styles  # noqa: E402
from ytstudio.core import Idea, slugify  # noqa: E402


def idea(genre="explainer", description="How a thing works, step by step.", **kw):
    return Idea(title="A film", genre=genre, description=description, **kw)


EXPLAINER = idea()
CINEMATIC = idea("cinematic", "A single 15-second shot: a thing crosses a sunlit "
                 "room and settles. No narration, sound design only.")


class Mapping(unittest.TestCase):
    def test_an_explainer_keeps_the_full_scope(self):
        scope = research.scope_for(EXPLAINER)
        self.assertEqual(scope["name"], "explainer")
        self.assertTrue(scope["spoken"])
        self.assertTrue(scope["misconceptions"])
        self.assertEqual(scope["depth"], research.DEFAULT_DEPTH)

    def test_a_cinematic_genre_is_light(self):
        scope = research.scope_for(CINEMATIC)
        self.assertEqual(scope["name"], "cinematic")
        self.assertFalse(scope["spoken"])
        self.assertFalse(scope["misconceptions"])
        self.assertLess(scope["depth"], research.DEFAULT_DEPTH)

    def test_the_premise_alone_can_make_it_wordless(self):
        for text in ("a wordless short", "no narration, music only",
                     "a music video for the track", "an ambient loop for a lobby",
                     "no voice-over; captions only"):
            self.assertEqual(research.scope_for(idea("short", text))["name"],
                             "cinematic", text)

    def test_the_genre_alone_can_make_it_light(self):
        for genre in ("music video", "ambient", "cinematic short", "mood film"):
            self.assertEqual(research.scope_for(idea(genre))["name"], "cinematic", genre)

    def test_a_narrated_documentary_stays_full(self):
        doc = idea("documentary", "A narrated history of the thing, with interviews.")
        self.assertEqual(research.scope_for(doc)["name"], "explainer")
        essay = idea("video essay", "Why the cinematic look of 90s films is back.")
        self.assertEqual(research.scope_for(essay)["name"], "explainer")


class FakeEngine:
    calls: list[dict] = []

    def __init__(self, config):
        self.config = config
        self.usage = Usage()

    def ask_json(self, prompt, schema, system=None):
        FakeEngine.calls.append({"prompt": prompt, "schema": schema, "system": system})
        return {"premise": "p", "questions": [], "gaps": [], "verdict": "v",
                "claims": [], "sources": [], "shots": [], "topic_searches": [],
                "working_title": "t", "runtime": "60 s", "sections": [],
                "misconceptions": [], "identifiable_case": {"who": "", "source": ""},
                "human_stories": []}


class Plan(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._engine = research.ClaudeEngine, harness.ENGINE
        research.ClaudeEngine = harness.ENGINE = FakeEngine
        FakeEngine.calls = []

    def tearDown(self):
        research.ClaudeEngine, harness.ENGINE = self._engine
        self._tmp.cleanup()

    def researcher(self, which, **kw):
        # Both fixtures share a title, so each researcher gets its own dossier
        # directory and never serves the other's cached stage.
        at = self.root / slugify(which.title) / slugify(which.genre)
        dossier = research.Dossier(self.root, which, at=at)
        return research.Researcher(which, dossier, verbose=False, **kw)

    def prompt(self):
        return FakeEngine.calls[-1]["prompt"]

    def test_the_explainer_plan_is_unchanged(self):
        self.researcher(EXPLAINER).questions()
        text = self.prompt()
        self.assertIn(f"Give exactly {research.DEFAULT_DEPTH} questions", text)
        self.assertIn("premise survives contact with the facts", text)

    def test_the_cinematic_plan_asks_for_reference_look_and_physics(self):
        r = self.researcher(CINEMATIC)
        r.questions()
        text = self.prompt()
        self.assertIn(f"Give exactly {r.depth} questions", text)
        self.assertLess(r.depth, research.DEFAULT_DEPTH)
        for word in ("reference", "look", "physics", "on screen"):
            self.assertIn(word, text)
        self.assertNotIn("premise survives contact with the facts", text)
        self.assertNotIn("misconception", text.lower())

    def test_an_explicit_depth_wins_over_the_scope(self):
        r = self.researcher(CINEMATIC, depth=7)
        r.questions()
        self.assertIn("Give exactly 7 questions", self.prompt())

    def test_cinematic_gaps_do_not_block_on_unsourced_numbers(self):
        self.researcher(CINEMATIC).gaps({"claims": []}, {"sources": []})
        text = self.prompt()
        self.assertIn("no spoken claim", text)
        self.assertIn("not blockers", text)
        self.assertNotIn("misconceptions the video should address", text)
        self.assertIn("empty", text)          # the misconceptions list stays empty

    def test_explainer_gaps_still_ask_for_misconceptions(self):
        self.researcher(EXPLAINER).gaps({"claims": []}, {"sources": []})
        self.assertIn("misconceptions the video should address", self.prompt())

    def test_cinematic_claims_are_fewer_and_about_what_is_on_screen(self):
        self.researcher(CINEMATIC).claims([])
        text = self.prompt()
        self.assertIn("on screen", text)
        self.assertNotIn("Extract every factual claim worth putting on camera", text)
        self.researcher(EXPLAINER).claims([])
        self.assertIn("Extract every factual claim worth putting on camera", self.prompt())

    def test_the_scope_is_recorded_in_the_dossier(self):
        data = self.researcher(CINEMATIC).run()
        self.assertEqual(data["scope"], "cinematic")


VERDICT = {
    "feasible": "partly",
    "summary": "A lit 3D room and a camera that follows the subject can be expressed.",
    "approach": ["one continuous shot"],
    "constructs": ["object3D@primitive=extrude", "camera@depthOfField", "light@type=directional"],
    "engine_limits": ["volumetric fog is approximated by layered planes"],
    "risks": ["the subject reads as a blockout"],
    "render_estimate": "450 frames x pathtrace on the GPU host: about 40 minutes",
    "pipeline": ["yt vfx", "yt build"],
    "missing": ["a mesh asset for the subject; no stage can supply one"],
}


class Stack(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name)
        self.root = self.work / "a-film"
        self.root.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, where: Path, description: str, verdict=VERDICT):
        where.mkdir(parents=True, exist_ok=True)
        (where / "assess.json").write_text(json.dumps(
            {"description": description, "verdict": verdict, "proof": {}, "engine": {}}))

    def test_the_brief_says_what_renders_approximates_cannot_and_the_estimate(self):
        self.write(self.root, "A film")
        text = assess.stack_brief(self.root)
        self.assertIn("scene-render", text)
        self.assertIn("object3D@primitive=extrude", text)
        self.assertIn("volumetric fog is approximated", text)
        self.assertIn("a mesh asset for the subject", text)
        self.assertIn("about 40 minutes", text)
        self.assertIn("partly", text)

    def test_no_verdict_anywhere_is_an_empty_brief(self):
        self.assertEqual(assess.stack_brief(self.root, work=self.work, idea=CINEMATIC), "")

    def test_the_work_assess_directory_is_searched_by_the_ideas_description(self):
        self.write(self.work / ".assess" / slugify(CINEMATIC.description, 40),
                   CINEMATIC.description)
        text = assess.stack_brief(self.root, work=self.work, idea=CINEMATIC)
        self.assertIn("a mesh asset for the subject", text)

    def test_another_videos_verdict_is_not_borrowed(self):
        self.write(self.work / ".assess" / "something-else",
                   "A photoreal droplet falling into still water")
        self.assertEqual(assess.stack_brief(self.root, work=self.work, idea=CINEMATIC), "")

    def test_the_projects_own_verdict_outranks_the_work_one(self):
        self.write(self.work / ".assess" / slugify(CINEMATIC.description, 40),
                   CINEMATIC.description, VERDICT | {"render_estimate": "older"})
        time.sleep(0.01)
        self.write(self.root, CINEMATIC.description)
        self.assertIn("about 40 minutes", assess.stack_brief(self.root, work=self.work,
                                                             idea=CINEMATIC))

    def test_the_styles_brief_names_the_stack_and_carries_the_verdict(self):
        self.write(self.root, "A film")
        stack = assess.stack_brief(self.root)
        prompt = styles.build_prompt(CINEMATIC, count=3, pool=["- 3d animation: x"],
                                     rich=True, constraints=[], context="", stack=stack)
        self.assertIn("the only renderer is scene-render", prompt)
        self.assertIn("a mesh asset for the subject", prompt)
        self.assertIn("volumetric fog is approximated", prompt)
        self.assertIn("about 40 minutes", prompt)
        # The rich fields ask for tools: they must be the stack's, not Blender's.
        self.assertIn("scene-render", prompt.split("For each style give")[1])

    def test_the_system_prompt_names_the_stack(self):
        self.assertIn("the only renderer is scene-render", styles.SYSTEM)
        self.assertIn("no Blender, After Effects or Manim", styles.SYSTEM)
        self.assertIn("no Blender, After Effects or Manim", assess.STACK_LINE)

    def test_the_director_preamble_carries_the_stack_to_every_call(self):
        director = styles.Director(CINEMATIC, stack="STACK-LINE-HERE", verbose=False)
        self.assertIn("STACK-LINE-HERE", director._preamble())


if __name__ == "__main__":
    unittest.main()
