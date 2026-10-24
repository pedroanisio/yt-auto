"""AUTO-158: casting hears the narrator's temperament the plan chose.

The plan names a `persona` (the narrator's temperament for this film and
why); script.json carries it at the top level, and `yt voice` already loads
script.json. The casting brief's Film block is built by `voice.film_brief`,
which adds the persona when the plan names a temperament and is today's
dict when it does not, so the casting decision's cache key does not move
for a screenplay without one.

No model is called: the director is test_wordless's FakeEngine, the voice
engine the stub that records silence.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import harness, voice  # noqa: E402
from ytstudio.core import Idea  # noqa: E402

from tests import test_wordless  # noqa: E402

IDEA = Idea(title="T", genre="g", description="d")
TODAY = {"title": "T", "premise": "d", "genre": "g", "treatment": ""}

#: sha256 of the casting prompt for CastingRun's screenplay with no persona,
#: taken on develop 29d523a before AUTO-158: the casting decision is asked
#: exactly what it was.
CASTING_BEFORE_AUTO_158 = "2bdc1187ab316e7613b90ea205275bbdd7a57427961647fd082d3050cbb71da9"


class FilmBrief(unittest.TestCase):
    def test_the_film_brief_carries_the_plans_persona(self):
        brief = voice.film_brief(IDEA, {}, {"persona": {"temperament": "wry",
                                                        "why": "a comic history"}})
        self.assertEqual(brief["persona"], "wry (a comic history)")
        self.assertEqual({k: v for k, v in brief.items() if k != "persona"}, TODAY)

    def test_a_persona_with_no_reason_is_its_temperament(self):
        brief = voice.film_brief(IDEA, {}, {"persona": {"temperament": " wry ", "why": ""}})
        self.assertEqual(brief["persona"], "wry")

    def test_without_persona_the_film_brief_is_todays(self):
        for script in ({}, {"persona": {"temperament": ""}},
                       {"persona": {"temperament": "  ", "why": "w"}},
                       {"persona": "wry"}, {"persona": None}):
            with self.subTest(script=script):
                brief = voice.film_brief(IDEA, {}, script)
                self.assertEqual(brief, TODAY)
                self.assertEqual(list(brief), ["title", "premise", "genre", "treatment"])

    def test_the_treatment_is_the_first_style_as_before(self):
        styles = {"styles": [{"name": "ink"}, {"name": "clay"}]}
        self.assertEqual(voice.film_brief(IDEA, styles, {})["treatment"], "ink")
        self.assertEqual(voice.film_brief(IDEA, {"styles": "x"}, {})["treatment"], "")
        self.assertEqual(voice.film_brief(IDEA, {"styles": []}, {})["treatment"], "")


class CastingRun(unittest.TestCase):
    """voice.run on a project, the director a fake that records prompts."""

    def setUp(self):
        saved = harness.ENGINE
        self.addCleanup(setattr, harness, "ENGINE", saved)
        harness.ENGINE = test_wordless.FakeEngine
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "idea.json").write_text(json.dumps(
            {"title": "T", "genre": "g", "description": "d"}), encoding="utf-8")

    def casting_prompt(self, **top):
        test_wordless.FakeEngine.prompts = []
        test_wordless.FakeEngine.answers = [
            {"voice": "", "speed": 1.0, "why": "w"},
            {"verdict": "ok", "retakes": [], "notes": "n"}]
        script = {**top, "sections": [{"heading": "One", "shots": [
            {"n": i, "label": f"1.{i}", "visual": "v",
             "narration": f"Line number {i}.", "seconds": 3} for i in (1, 2)]}]}
        (self.root / "script.json").write_text(json.dumps(script), encoding="utf-8")
        args = test_wordless.WordlessRun.args(self)
        args.engine, args.fresh, args.quiet = "stub", True, True
        self.assertEqual(voice.run(args), 0)
        return test_wordless.FakeEngine.prompts[0]

    @staticmethod
    def film_block(prompt):
        return prompt.split("Film:\n", 1)[1].split("\n\n", 1)[0]

    def test_casting_is_asked_with_the_persona(self):
        prompt = self.casting_prompt(persona={"temperament": "wry", "why": "a comic history"})
        self.assertIn('"persona": "wry (a comic history)"', self.film_block(prompt))

    def test_without_persona_the_casting_prompt_is_todays(self):
        prompt = self.casting_prompt()
        self.assertNotIn("persona", prompt)
        self.assertEqual(hashlib.sha256(prompt.encode()).hexdigest(),
                         CASTING_BEFORE_AUTO_158)
        self.assertEqual(self.casting_prompt(persona={"temperament": "", "why": ""}), prompt)


if __name__ == "__main__":
    unittest.main()
