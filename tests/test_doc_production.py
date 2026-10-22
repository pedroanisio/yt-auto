"""docs/production.md states the publication bar the build actually applies."""

import re
import tempfile
import unittest
from pathlib import Path

from ytstudio import build
from tests.test_production import _idea, _sections

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "production.md"
BUILD = ROOT / "ytstudio" / "build.py"


def _section(title):
    text = DOC.read_text()
    start = text.index(title)
    end = text.find("\n## ", start + 1)
    return text[start:end if end > 0 else None]


class ProductionDoc(unittest.TestCase):
    def test_the_emitted_list_names_every_build_json_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "vfx").mkdir()
            builder = build.SceneBuilder(_idea(), _sections(), root, vfx={},
                                         verbose=False, audio=False)
            _, manifest = builder.build()
        # keys the stage's main() adds after build(): manifest["key"] = ...
        added = set(re.findall(r'manifest\["(\w+)"\]\s*=', BUILD.read_text()))
        keys = set(manifest) | added
        self.assertTrue({"look", "look_check", "look_declined"} <= keys)
        listed = set(re.findall(r"`(\w+)`", _section("## 5. What production emits")))
        self.assertEqual(sorted(keys - listed), [])

    def test_the_colour_pipeline_is_named_among_the_emitted_elements(self):
        text = _section("## 5. What production emits")
        for word in ("colorManagement", "ACEScg", "tone mapping", "exposure", "CDL"):
            self.assertIn(word.lower(), text.lower())

    def test_visible_words_are_on_screen_text_and_on_screen_is_direction(self):
        doc = DOC.read_text()
        self.assertIn("`on_screen_text`", doc)
        self.assertNotIn("`on_screen` (the super)", doc)
        self.assertNotIn("Every `on_screen` super", doc)

    def test_narrative_clarity_names_the_story_the_corpus_asks_for(self):
        row = next(line for line in _section("## 2. The publication bar").splitlines()
                   if "Narrative clarity" in line)
        for word in ("hook", "stakes", "escalation", "payoff", "visual support"):
            self.assertIn(word, row)

    def test_the_story_criteria_are_house_priors_with_rule_numbers(self):
        text = _section("## 6. Craft criteria")
        self.assertIn("**Story.**", text)
        story = " ".join(text[text.index("**Story.**"):].split("\n\n")[0].split())
        self.assertIn("prior", story)
        self.assertIn("docs/narrative/", story)
        for rule in ("judging 13", "attention 8", "structure 19", "structure 7",
                     "structure 27"):
            self.assertIn(rule, story)

    def test_the_narrative_beats_are_the_plans_beat_fields(self):
        self.assertIn("`sections[].beats`", _section("## 7. Evidence sampling"))

    def test_narrative_clarity_is_not_judged_from_audience_data_yet(self):
        text = _section("## 12. What this harness cannot yet evidence")
        self.assertIn("retention", text)
        self.assertIn("screenplay", text)

    def test_captions_are_a_sidecar_unless_burned(self):
        text = _section("### The frame, in bands")
        self.assertIn("sidecar", text)
        self.assertIn("--burn-captions", text)
        self.assertNotIn("Burned-in captions live in the bottom band", text)


if __name__ == "__main__":
    unittest.main()
