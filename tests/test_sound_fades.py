"""A cue carries `fade_in` and `fade_out` in seconds (AUTO-84): the schema
asks for them, the sheet keeps them, a revision may change them, and the mix
applies them. A file longer than its window with no fade out is cut hard
mid-sound, and the build says so.
"""

from __future__ import annotations

import csv
import io
import sys
import tempfile
import unittest
from dataclasses import asdict, fields
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import build, sound  # noqa: E402
from ytstudio.core import Idea  # noqa: E402
from ytstudio.script import Section, Shot  # noqa: E402


def raw(**over):
    row = {"kind": "music", "name": "bed", "at": 0, "seconds": 30, "purpose": "p",
           "description": "d", "source": "catalogue", "search_description": "s",
           "synth_spec": "x", "level": "-30 dBFS"}
    row.update(over)
    return row


class Schema(unittest.TestCase):
    def test_the_spotting_schema_asks_for_fades_in_seconds(self):
        props = sound.CUES_SCHEMA["properties"]["cues"]["items"]["properties"]
        for key in ("fade_in", "fade_out"):
            self.assertEqual(props[key]["type"], "number")
        names = {f.name for f in fields(sound.Cue)}
        self.assertTrue({"fade_in", "fade_out"} <= names)

    def test_a_cue_keeps_its_fades_as_seconds(self):
        c = sound.Cue.from_raw(raw(fade_in="1.5", fade_out=2))
        self.assertEqual((c.fade_in, c.fade_out), (1.5, 2.0))
        self.assertEqual((asdict(c)["fade_in"], asdict(c)["fade_out"]), (1.5, 2.0))

    def test_a_fade_that_is_not_a_time_is_no_fade(self):
        c = sound.Cue.from_raw(raw(fade_in="slow", fade_out=-3))
        self.assertEqual((c.fade_in, c.fade_out), (0.0, 0.0))

    def test_a_cue_without_fades_has_none(self):
        self.assertEqual((sound.Cue.from_raw(raw()).fade_in,
                          sound.Cue.from_raw(raw()).fade_out), (0.0, 0.0))

    def test_the_revision_schema_carries_the_fades(self):
        for key in ("fade_in", "fade_out"):
            self.assertEqual(sound.REVISE_SCHEMA["properties"][key]["type"], "number")


class Prompt(unittest.TestCase):
    def test_the_spotting_prompt_asks_for_fades_beside_entry_and_exit(self):
        d = sound.SoundDesigner(Idea(title="T", description="d", genre="g"),
                                runtime=60, verbose=False)
        asked = {}

        def ask(name, prompt, schema):
            asked[name] = prompt
            return {"cues": []}
        d._ask = ask
        section = Section(heading="Open", shots=[
            Shot(label="1.1", n=1, visual="v", narration="n", seconds=10, at=0)])
        d.spot_section(section, index=1, total=1, placed=[])
        d.spot_whole()
        for prompt in asked.values():
            self.assertIn("fade_in", prompt)
            self.assertIn("fade_out", prompt)
            self.assertIn("music and ambience", prompt)


class Revise(unittest.TestCase):
    def test_a_revision_may_change_a_fade(self):
        d = sound.SoundDesigner(Idea(title="T", description="d", genre="g"),
                                runtime=60, verbose=False)
        d._ask = lambda name, prompt, schema: {"action": "fix", "fade_out": 3, "why": "tail"}
        cues = sound.number_cues([sound.Cue.from_raw(raw(fade_out=1))])
        out = d.revise(cues, {"cue": "M1", "problem": "cut hard", "severity": "important",
                              "fix": "fade"})
        self.assertEqual(cues[0].fade_out, 3.0)
        self.assertEqual(out["applied"]["before"]["fade_out"], 1.0)
        self.assertEqual(out["applied"]["after"]["fade_out"], 3.0)

    def test_the_model_sees_the_cues_fades_and_timing_before_it_answers(self):
        d = sound.SoundDesigner(Idea(title="T", description="d", genre="g"),
                                runtime=60, verbose=False)
        prompts = []

        def ask(name, prompt, schema):
            prompts.append(prompt)
            return {"action": "keep", "why": "fine"}
        d._ask = ask
        cues = sound.number_cues([sound.Cue.from_raw(raw(at=5, seconds=20,
                                                         fade_in=1.5, fade_out=4))])
        d.revise(cues, {"cue": "M1", "problem": "p", "severity": "important", "fix": "f"})
        now = prompts[0].split("Its fields now: ")[1].split("\n")[0]
        for text in ("'fade_in': 1.5", "'fade_out': 4.0", "'at': 5", "'seconds': 20"):
            self.assertIn(text, now)

    def test_a_fade_longer_than_the_cue_is_held_to_it(self):
        d = sound.SoundDesigner(Idea(title="T", description="d", genre="g"),
                                runtime=60, verbose=False)
        d._ask = lambda name, prompt, schema: {"action": "fix", "fade_in": 90, "why": "slow"}
        cues = sound.number_cues([sound.Cue.from_raw(raw(seconds=30))])
        d.revise(cues, {"cue": "M1", "problem": "p", "severity": "important", "fix": "f"})
        self.assertEqual(cues[0].fade_in, 30.0)


class Rule(unittest.TestCase):
    """A bed with no fade is a rule finding: the field is required for music
    and ambience, and a missing one is the model's to choose, not a default."""

    def test_a_bed_with_no_fade_is_flagged(self):
        cues = sound.number_cues([sound.Cue.from_raw(raw()),
                                  sound.Cue.from_raw(raw(kind="ambience", at=30))])
        flags = [f for f in sound.rule_findings(cues, 60) if f["rule"] == "rule:fade"]
        self.assertEqual([f["cue"] for f in flags], ["M1", "A1"])
        self.assertIn("fade", flags[0]["fix"])

    def test_a_bed_with_fades_and_an_effect_without_are_fine(self):
        cues = sound.number_cues([sound.Cue.from_raw(raw(fade_in=1, fade_out=2)),
                                  sound.Cue.from_raw(raw(kind="sfx", at=5, seconds=1))])
        self.assertEqual([f for f in sound.rule_findings(cues, 60) if f["rule"] == "rule:fade"],
                         [])


class Output(unittest.TestCase):
    def test_the_sheet_and_the_csv_show_the_fades(self):
        idea = Idea(title="T", description="d", genre="g")
        cues = sound.number_cues([sound.Cue.from_raw(raw(fade_in=1.5, fade_out=2))])
        data = {"runtime": 60, "palette": {}, "coverage": {}, "cues": [asdict(c) for c in cues]}
        with tempfile.TemporaryDirectory() as tmp:
            sound.write_md(f"{tmp}/s.md", idea, data, cues)
            md = Path(f"{tmp}/s.md").read_text()
            sound.write_csv(f"{tmp}/c.csv", cues)
            row = next(csv.DictReader(open(f"{tmp}/c.csv", encoding="utf-8")))
        self.assertIn("1.5", md)
        self.assertIn("Fade", md)
        self.assertEqual((row["fade_in"], row["fade_out"]), ("1.5", "2.0"))
        out = io.StringIO()
        sys.stdout, saved = out, sys.stdout
        try:
            sound.print_sound(idea, data, cues)
        finally:
            sys.stdout = saved
        self.assertIn("fade: 1.5s in, 2s out", out.getvalue())


class Mix(unittest.TestCase):
    def setUp(self):
        from tests.test_build_audio import _idea, _sections, _wav
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "vfx").mkdir()
        (self.root / "vfx" / "open.scene.xml").write_text("<scene/>")
        _wav(self.root / "sound" / "long.wav", 12)
        _wav(self.root / "sound" / "short.wav", 2)
        self.vfx = {"scene": {"by_label": {
            label: {"file": "vfx/open.scene.xml", "symbol": "fx-all"}
            for label in ("1.1", "1.2", "2.1")}}}
        self.idea, self.sections = _idea(), _sections()

    def tearDown(self):
        self._tmp.cleanup()

    def build(self, *cues):
        data = {"runtime": 10, "cues": list(cues)}
        out, manifest = build.SceneBuilder(self.idea, self.sections, self.root,
                                           vfx=self.vfx, sound=data, verbose=False).build()
        from tests.test_build_audio import _checked
        return _checked(out).find("audioMix"), manifest

    def hard_cut(self, manifest):
        return [f for f in manifest["findings"] if "cut" in f and "fade" in f]

    def test_the_mix_takes_its_fades_from_the_cue(self):
        cue = asdict(sound.Cue.from_raw(raw(seconds=6, fade_in=1.5, fade_out=2)))
        cue.update(cue_id="M1", file="sound/long.wav")
        mix, manifest = self.build(cue)
        track = mix.find("audioTrack[@id='cue-M1']")
        self.assertEqual((track.get("fadeIn"), track.get("fadeOut")), ("1.5", "2"))
        self.assertEqual(self.hard_cut(manifest), [])

    def test_a_file_cut_short_with_no_fade_out_is_a_warning_naming_the_cue(self):
        cue = asdict(sound.Cue.from_raw(raw(seconds=6)))
        cue.update(cue_id="M1", file="sound/long.wav")
        _, manifest = self.build(cue)
        found = self.hard_cut(manifest)
        self.assertEqual(len(found), 1, manifest["findings"])
        self.assertTrue(found[0].startswith("warning:"))
        self.assertIn("M1", found[0])
        self.assertIn("12", found[0])

    def test_a_file_that_fits_its_window_needs_no_fade(self):
        cue = asdict(sound.Cue.from_raw(raw(kind="sfx", seconds=4)))
        cue.update(cue_id="S1", file="sound/short.wav")
        _, manifest = self.build(cue)
        self.assertEqual(self.hard_cut(manifest), [])

    def test_a_looping_cue_is_not_cut(self):
        cue = asdict(sound.Cue.from_raw(raw(kind="ambience", seconds=6)))
        cue.update(cue_id="A1", file="sound/short.wav", loop=True)
        _, manifest = self.build(cue)
        self.assertEqual(self.hard_cut(manifest), [])


if __name__ == "__main__":
    unittest.main()
