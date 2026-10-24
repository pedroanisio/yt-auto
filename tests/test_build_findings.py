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
from tests.test_keyframe import STYLES
from ytstudio import build, design

#: A treatment whose palette is words only: what a plain `yt styles` writes.
HEXLESS = {"styles": [{"name": "x", "palette": "warm earth tones"}]}


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


def _beat_sections(functions=("hook", "payoff", "handoff")):
    """The fixture film with the screenplay's functions (AUTO-90): by default
    the payoff is 1.2, followed by the hand-off, so not the last shot."""
    sections = _sections()
    for shot, function in zip([s for sec in sections for s in sec.shots],
                              functions):
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
                                 "payoff_shot": "1.2", "payoff_from": "function",
                                 "payoff_is_last": False})
        last = build.story_of(SCRIPT, _beat_sections(("hook", "context", "payoff")))
        self.assertTrue(last["payoff_is_last"])

    def test_without_functions_the_last_shot_stands_for_the_payoff(self):
        story = build.story_of({"promise": "p"}, _sections())
        self.assertEqual(story["payoff_shot"], "2.1")
        self.assertEqual(story["payoff_from"], "position")
        self.assertEqual(build.story_of({}, _sections()), {})

    def prompt(self, functions=("hook", "payoff", "handoff")):
        sections = _beat_sections(functions)
        builder = build.SceneBuilder(_idea(), sections, self.root, vfx=self.vfx,
                                     verbose=False, audio=False,
                                     story=build.story_of(SCRIPT, sections))
        return build.edit_prompt(builder.brief())

    def test_the_editor_sees_the_payoff_shot_and_the_promise(self):
        prompt = self.prompt()
        for text in ("You will see why it turns.", "It turns because it must.",
                     '"payoff_shot": "1.2"', "blocking"):
            self.assertIn(text, prompt)
        self.assertIn('"function": "payoff"', prompt)

    def test_a_payoff_before_the_hand_off_is_not_given_the_tail(self):
        # tail_seconds holds after the film's last shot (2.1, the hand-off);
        # calling it the payoff's hold would put the payoff's beat after
        # the hand-off, and nothing would hold after the payoff line.
        prompt = self.prompt()
        self.assertIn("`tail_seconds` holds after the film's last shot", prompt)
        self.assertIn("is not the last shot", prompt)
        self.assertNotIn("0.5 to 1.5", prompt)
        # tail_seconds is clamped to 0..5: it cannot make a 5-20 s end screen
        self.assertNotIn("20 seconds", prompt)
        self.assertNotIn("end screen", prompt)
        _, manifest = build.SceneBuilder(
            _idea(), _beat_sections(), self.root, vfx=self.vfx, verbose=False,
            audio=False, edit=_edit(tail_seconds=1.0),
            story=build.story_of(SCRIPT, _beat_sections())).build()
        self.assertEqual(manifest["scene"]["duration"], 11.0)   # after 2.1

    def test_a_payoff_that_ends_the_film_is_given_the_tail_as_its_landing(self):
        prompt = self.prompt(("hook", "context", "payoff"))
        self.assertIn("`tail_seconds` holds after the film's last shot", prompt)
        self.assertIn("which is the payoff shot", prompt)
        self.assertIn("0.5 to 1.5", prompt)
        self.assertNotIn("is not the last shot", prompt)

    def test_an_older_film_asks_the_editor_the_same_question(self):
        builder = build.SceneBuilder(_idea(), _sections(), self.root, vfx=self.vfx,
                                     verbose=False, audio=False)
        prompt = build.edit_prompt(builder.brief())
        self.assertNotIn("payoff", prompt)
        self.assertNotIn("function", prompt)


class DesignFallback(_Project):
    """A film in design.py's house fallback palette builds, publishable as
    before, with one warning naming why (AUTO-155): the stages after it
    refuse it, not the build."""

    def design_findings(self, manifest):
        return [f for f in manifest["findings"] if f.startswith("warning: design:")]

    def test_no_styles_warns_and_stays_publishable(self):
        _, manifest = self.build(_edit(), audio=False, styles=None)
        found = self.design_findings(manifest)
        self.assertEqual(len(found), 1, manifest["findings"])
        self.assertIn("no styles.json", found[0])
        self.assertIn("yt styles --rich", found[0])
        self.assertEqual(manifest["design"]["source"], "fallback")
        self.assertEqual(manifest["design"]["fallback"], "no styles.json")
        _, styled = self.build(_edit(), audio=False, styles=STYLES)
        self.assertEqual(manifest["publishable"], styled["publishable"])

    def test_a_palette_without_hex_names_that_cause(self):
        _, manifest = self.build(_edit(), audio=False, styles=HEXLESS)
        found = self.design_findings(manifest)
        self.assertEqual(len(found), 1, manifest["findings"])
        self.assertIn("names no hex code", found[0])
        self.assertIn("names no hex code", manifest["design"]["fallback"])

    def test_an_empty_styles_list_names_that_cause(self):
        _, manifest = self.build(_edit(), audio=False, styles={"styles": []})
        found = self.design_findings(manifest)
        self.assertEqual(len(found), 1, manifest["findings"])
        self.assertIn("styles.json lists no treatment", found[0])
        self.assertEqual(manifest["design"]["fallback"], "styles.json lists no treatment")

    def test_a_styles_palette_adds_nothing(self):
        _, manifest = self.build(_edit(), audio=False, styles=STYLES)
        self.assertEqual([f for f in manifest["findings"] if "design:" in f], [])
        self.assertNotIn("fallback", manifest["design"])
        self.assertEqual(manifest["design"]["source"], "styles.json")

    def test_the_warning_is_printed_once_however_many_findings(self):
        import contextlib
        import io
        _, manifest = self.build(_edit(), audio=False, styles=None)
        manifest["findings"] = [f"warning: other {n}" for n in range(20)] + manifest["findings"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            build.print_build(manifest)
        head, sdk = out.getvalue().split("the SDK raised", 1)
        self.assertIn("warning: design:", head)          # above the cut at 12
        self.assertNotIn("warning: design:", sdk)

    def test_design_fallback_is_pure(self):
        for styles, why in ((None, "no styles.json"),
                            ({"styles": []}, "styles.json lists no treatment"),
                            ({}, "styles.json lists no treatment"),
                            (HEXLESS, "the primary treatment's palette in styles.json "
                                      "names no hex code"),
                            (STYLES, "")):
            with self.subTest(styles=styles):
                self.assertEqual(build.design_fallback(
                    styles, design.Design.from_styles(styles)), why)
