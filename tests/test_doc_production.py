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
        for words in ("hook", "stakes", "open questions closed", "escalation",
                      "payoff on the promise", "visual support"):
            self.assertIn(words, row)

    def test_narrative_clarity_and_the_screenplays_duty_name_the_same_story(self):
        # AUTO-99: QA scores the story under row 9 and places a story finding
        # with script by DUTIES; the two say the same thing or a finding has
        # no owner.
        from ytstudio import qa
        row = next(line for line in _section("## 2. The publication bar").splitlines()
                   if "Narrative clarity" in line)
        duty = qa.DUTIES["script"]
        self.assertTrue(qa.STORY_DUTY)
        for term in qa.STORY_DUTY:
            self.assertIn(term, row)
            self.assertIn(term, duty)

    def test_listen_role_from_production_doc(self):
        # AUTO-126: the listener judges rows 6-8 of the bar, word for word;
        # no checklist of its own lives in code.
        from ytstudio import listen
        rows = {}
        for line in _section("## 2. The publication bar").splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 3 and cells[0] in ("6", "7", "8"):
                rows[cells[1]] = cells[2]
        self.assertEqual({name: words for name, words in listen.LISTEN_CRITERIA.values()},
                         rows)
        self.assertEqual(list(listen.LISTEN_CRITERIA), ["voiceover", "sound_design",
                                                        "music_score"])
        for name, words in rows.items():
            self.assertIn(f"{name}: {words}", listen.LISTEN_ROLE)

    def test_the_story_criteria_are_house_priors_with_rule_numbers(self):
        text = _section("## 6. Craft criteria")
        self.assertIn("**Story.**", text)
        story = " ".join(text[text.index("**Story.**"):].split("\n\n")[0].split())
        self.assertIn("prior", story)
        self.assertIn("docs/narrative/", story)
        for rule in ("judging 13", "attention 8", "structure 19", "structure 7",
                     "structure 27"):
            self.assertIn(rule, story)

    def test_the_story_criteria_keep_the_corpus_strength(self):
        # Each sentence says no more than the rule it cites: attention 8 is
        # "whenever possible" with a twist exemption, structure 19's check
        # accepts an answer deferred to a named later section, and structure
        # 27 allows a one-line pointer to the next film (so §7's call to
        # action is not a defect).
        text = _section("## 6. Craft criteria")
        story = " ".join(text[text.index("**Story.**"):].split("\n\n")[0].split())
        sentence = {rule: next(s for s in story.split(". ") if f"({rule}" in s)
                    for rule in ("attention 8", "structure 19", "structure 27")}
        self.assertIn("twist", sentence["attention 8"])
        self.assertIn("where the film can", sentence["attention 8"].lower())
        self.assertIn("later section it names", sentence["structure 19"])
        self.assertNotIn("first line", sentence["structure 19"])
        self.assertIn("one-line pointer to the next film", sentence["structure 27"])
        self.assertNotIn("Nothing follows the payoff", story)

    def test_the_narrative_beats_are_the_plans_beat_fields(self):
        self.assertIn("`sections[].beats`", _section("## 7. Evidence sampling"))

    def test_narrative_clarity_is_not_judged_from_audience_data_yet(self):
        text = _section("## 12. What this harness cannot yet evidence")
        self.assertIn("retention", text)
        self.assertIn("screenplay", text)

    def test_the_voice_json_row_names_the_delivery_fields(self):
        row = next(l for l in DOC.read_text().splitlines() if l.startswith("| `voice.json`"))
        for key in ("`spoken`", "`spoken_chars`", "`delivery`", "`draft:", "`voice-draft.json`",
                    "`unmatched`", "`recorded_only`"):
            self.assertIn(key, row, key)

    def test_the_dossier_row_names_the_human_stories(self):
        # AUTO-149
        row = next(l for l in DOC.read_text().splitlines() if l.startswith("| `dossier.json`"))
        self.assertIn("`human_stories`", row)
        for key in ("`who`", "`what_happened`", "`consequence`", "`source`", "`claim`"):
            self.assertIn(key, row.split("`human_stories`", 1)[1], key)

    def test_the_dossier_row_names_the_parallels(self):
        # AUTO-151
        row = next(l for l in DOC.read_text().splitlines() if l.startswith("| `dossier.json`"))
        self.assertIn("`parallels`", row)
        self.assertIn("`parallels_why`", row)
        for key in ("`event`", "`year`", "`how_it_maps`", "`where_it_breaks`",
                    "`source_url`"):
            self.assertIn(key, row.split("`parallels`", 1)[1], key)
        self.assertIn("--fresh", row.split("`parallels`", 1)[1])

    def test_the_script_row_names_the_headline_asset(self):
        # AUTO-149
        row = next(l for l in DOC.read_text().splitlines() if l.startswith("| `script.json`"))
        self.assertIn("`asset`", row)
        self.assertIn('"headline [Nk]"', row)
        self.assertIn("`source:headline`", row)

    def test_captions_are_a_sidecar_unless_burned(self):
        text = _section("### The frame, in bands")
        self.assertIn("sidecar", text)
        self.assertIn("--burn-captions", text)
        self.assertNotIn("Burned-in captions live in the bottom band", text)


if __name__ == "__main__":
    unittest.main()
