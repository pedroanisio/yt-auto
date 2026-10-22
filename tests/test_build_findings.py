"""A number the model wrote badly is replaced, and the build says so.

The editor's tail and the sound designer's fades, gain and level used to fall
back to a default with nothing recorded (invariant 3: no silent default).
Each replacement is one finding in the manifest (build.json), naming the
field, the cue or the film, and the value used.
"""

import re
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from tests.test_build_audio import _idea, _sections, _wav
from ytstudio import build


class _Project(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "vfx").mkdir()
        (self.root / "vfx" / "open.scene.xml").write_text("<scene/>")
        _wav(self.root / "sound" / "pad.wav", 12)
        self.vfx = {"scene": {"by_label": {
            label: {"file": "vfx/open.scene.xml", "symbol": "fx-all"}
            for label in ("1.1", "1.2", "2.1")}}}

    def tearDown(self):
        self._tmp.cleanup()

    def build(self, edit=None, cues=None, **kw):
        sound = {"runtime": 10, "cues": cues} if cues is not None else None
        return build.SceneBuilder(_idea(), _sections(), self.root, vfx=self.vfx,
                                  edit=edit, sound=sound, verbose=False,
                                  **kw).build()


def _cue(**over):
    row = {"cue_id": "M1", "kind": "music", "at": 0, "seconds": 6,
           "level": "-30 dBFS", "description": "a pad", "file": "sound/pad.wav"}
    row.update(over)
    return row


def _edit(**over):
    row = {"coverage": [], "captions_off": [], "tail_seconds": 0.5, "notes": "",
           "blocking": []}
    row.update(over)
    return row


class EditorTail(_Project):
    def test_a_tail_that_is_not_a_number_is_a_finding_naming_the_value_used(self):
        _, manifest = self.build(_edit(tail_seconds="a beat"), audio=False)
        found = [f for f in manifest["findings"] if "tail_seconds" in f]
        self.assertEqual(len(found), 1, manifest["findings"])
        self.assertIn("'a beat'", found[0])
        self.assertIn("0.5", found[0])
        self.assertTrue(found[0].startswith("warning"))
        self.assertEqual(manifest["scene"]["duration"], 10.5)

    def test_a_tail_outside_the_range_is_a_finding_naming_the_clamp(self):
        _, manifest = self.build(_edit(tail_seconds=40), audio=False)
        found = [f for f in manifest["findings"] if "tail_seconds" in f]
        self.assertEqual(len(found), 1, manifest["findings"])
        self.assertIn("5", found[0])

    def test_a_good_tail_is_silent(self):
        _, manifest = self.build(_edit(tail_seconds=1.5), audio=False)
        self.assertFalse([f for f in manifest["findings"] if "tail_seconds" in f])


class SoundNumbers(_Project):
    def one(self, field, value):
        # the bad number's own finding; a bed left with no fade out is
        # another (AUTO-84), pinned in test_sound_fades
        _, manifest = self.build(_edit(), [_cue(**{field: value})])
        return [f for f in manifest["findings"]
                if "M1" in f and field in f and "not a number" in f]

    def test_a_fade_in_that_is_not_a_number(self):
        found = self.one("fade_in", "slow")
        self.assertEqual(len(found), 1)
        self.assertIn("'slow'", found[0])

    def test_a_fade_out_that_is_not_a_number(self):
        self.assertEqual(len(self.one("fade_out", "slow")), 1)

    def test_a_gain_that_is_not_a_number(self):
        found = self.one("gain_db", "a little louder")
        self.assertEqual(len(found), 1)
        self.assertIn("'a little louder'", found[0])

    def test_numbers_that_parse_make_no_finding(self):
        _, manifest = self.build(_edit(), [_cue(fade_in=1, fade_out="2", gain_db=3)])
        self.assertFalse([f for f in manifest["findings"] if "M1" in f])

    def test_a_level_in_words_only_is_a_finding_naming_the_cue(self):
        _, manifest = self.build(_edit(), [_cue(level="quiet, under the voice")])
        found = [f for f in manifest["findings"] if "M1" in f and "level" in f]
        self.assertEqual(len(found), 1, manifest["findings"])
        self.assertIn("quiet, under the voice", found[0])

    def test_a_level_with_a_figure_or_none_is_silent(self):
        _, manifest = self.build(_edit(), [
            _cue(level="-20 dBFS"), _cue(cue_id="S1", kind="sfx", level="", file="")])
        self.assertFalse([f for f in manifest["findings"] if "level" in f])


class PlaceholderCard(_Project):
    def test_the_card_is_drawn_with_the_designs_tokens(self):
        self.vfx = {"scene": {"by_label": {}}}
        data, manifest = self.build(_edit(), audio=False)
        self.assertEqual(manifest["placeholders"], ["1.1", "1.2", "2.1"])
        self.assertIsNotNone(ElementTree.fromstring(data).find(".//text"))

    def test_no_literal_colour_in_the_card_code(self):
        source = Path(build.__file__).read_text()
        start = source.index("An honest placeholder")
        card = source[start:start + 900]
        self.assertIsNone(re.search(r"#[0-9A-Fa-f]{6}", card))


def _beat_sections():
    """The fixture film with the screenplay's functions (AUTO-90): the payoff
    is 1.2, not the last shot."""
    sections = _sections()
    for shot, function in zip([s for sec in sections for s in sec.shots],
                              ("hook", "payoff", "handoff")):
        shot.function = function
    return sections


SCRIPT = {"promise": "You will see why it turns.",
          "ending": {"payoff_line": "It turns because it must.", "callback": "c",
                     "next_film": "n"}}


class Story(_Project):
    """The shot rows carry each shot's function, and the editor is told which
    shot pays the promise, so the hold after it is the editor's call and a
    missing payoff picture is named in `blocking` (structure 33, 35)."""

    def test_the_shot_rows_carry_the_function(self):
        _, manifest = build.SceneBuilder(_idea(), _beat_sections(), self.root, vfx=self.vfx,
                                         edit=_edit(), verbose=False, audio=False).build()
        self.assertEqual([s["function"] for s in manifest["shots"]],
                         ["hook", "payoff", "handoff"])

    def test_the_story_names_the_payoff_shot_by_function(self):
        story = build.story_of(SCRIPT, _beat_sections())
        self.assertEqual(story, {"promise": "You will see why it turns.",
                                 "payoff_line": "It turns because it must.",
                                 "payoff_shot": "1.2", "payoff_from": "function"})

    def test_without_functions_the_last_shot_stands_for_the_payoff(self):
        story = build.story_of({"promise": "p"}, _sections())
        self.assertEqual(story["payoff_shot"], "2.1")
        self.assertEqual(story["payoff_from"], "position")
        self.assertEqual(build.story_of({}, _sections()), {})

    def test_the_editor_sees_the_payoff_shot_and_the_promise(self):
        builder = build.SceneBuilder(_idea(), _beat_sections(), self.root, vfx=self.vfx,
                                     verbose=False, audio=False,
                                     story=build.story_of(SCRIPT, _beat_sections()))
        prompt = build.edit_prompt(builder.brief())
        for text in ("You will see why it turns.", "It turns because it must.",
                     '"payoff_shot": "1.2"', "0.5", "1.5", "blocking", "end screen"):
            self.assertIn(text, prompt)
        self.assertIn('"function": "payoff"', prompt)

    def test_an_older_film_asks_the_editor_the_same_question(self):
        builder = build.SceneBuilder(_idea(), _sections(), self.root, vfx=self.vfx,
                                     verbose=False, audio=False)
        prompt = build.edit_prompt(builder.brief())
        self.assertNotIn("payoff", prompt)
        self.assertNotIn("function", prompt)
