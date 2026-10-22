"""The sound designer decides the master's normalise mode and the mix numbers
(AUTO-85): the palette carries them, sound.json carries `normalize`, the fetch
reads `narration_lufs` before the prose, and `yt build` takes the sheet's mode
over its flag's default. An explicit flag is the operator's override.
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import build, sound  # noqa: E402
from ytstudio.core import Idea  # noqa: E402


MIX = {"narration": "-16 LUFS, dry", "music_bed": "8 dB under", "effects": "-6 dBTP",
       "master": "-14 LUFS, -1 dBTP", "narration_lufs": -16,
       "bed_below_narration_db": 8, "effects_peak_dbtp": -6,
       "normalize": {"mode": "dynamic",
                     "reason": "voice-led with designed silences; integrated "
                               "leaves the quiet stretches 3 dB under"}}


def palette(**over):
    return {"approach": "a", "instrumentation": "i", "sfx_palette": "s",
            "ambience": "b", "avoid": "v", "silence": "q", "motifs": [],
            "mix": dict(MIX, **over)}


def designer():
    return sound.SoundDesigner(Idea(title="T", description="d", genre="g"),
                               runtime=60, verbose=False)


def cues():
    return sound.number_cues([
        sound.Cue(kind="music", name="bed", at=0, seconds=30, purpose="p",
                  level="-30 dBFS", fade_in=1, fade_out=2)])


class Schema(unittest.TestCase):
    def mix(self):
        return sound.PALETTE_SCHEMA["properties"]["mix"]

    def test_the_mix_asks_for_the_mode_with_its_reason(self):
        normalize = self.mix()["properties"]["normalize"]
        self.assertEqual(normalize["properties"]["mode"]["enum"],
                         ["integrated", "dynamic", "none"])
        self.assertEqual(normalize["properties"]["reason"]["type"], "string")
        self.assertEqual(sorted(normalize["required"]), ["mode", "reason"])
        self.assertIn("normalize", self.mix()["required"])

    def test_the_mix_numbers_are_numbers(self):
        for key in ("narration_lufs", "bed_below_narration_db", "effects_peak_dbtp"):
            self.assertEqual(self.mix()["properties"][key]["type"], "number")
            self.assertIn(key, self.mix()["required"])

    def test_the_prose_fields_stay(self):
        for key in ("narration", "music_bed", "effects", "master"):
            self.assertIn(key, self.mix()["required"])


class Prompt(unittest.TestCase):
    def test_the_palette_prompt_states_the_trade_off_and_asks_for_the_mode(self):
        d = designer()
        asked = {}

        def ask(name, prompt, schema):
            asked[name] = prompt
            return palette()
        d._ask = ask
        d.plan()
        text = asked["palette"]
        for word in ("integrated", "dynamic", "LRA", "`normalize`", "reason",
                     "narration_lufs", "bed_below_narration_db", "effects_peak_dbtp"):
            self.assertIn(word, text)

    def test_the_spotting_recap_carries_the_numbers(self):
        d = designer()
        d.palette = palette()
        recap = d._palette_recap()
        self.assertIn("-16", recap)
        self.assertIn("dynamic", recap)


class Record(unittest.TestCase):
    def run_with(self, pal):
        d = designer()
        d.plan = lambda: pal
        d.spot_whole = cues
        return d.run()

    def test_sound_json_carries_the_designers_normalize(self):
        data = self.run_with(palette())
        self.assertEqual(data["normalize"], MIX["normalize"])

    def test_a_palette_without_the_decision_writes_none(self):
        # an older palette, or a fake: the build then falls to its own default
        data = self.run_with({"mix": {"narration": "-16 LUFS"}})
        self.assertNotIn("normalize", data)


class NarrationLevel(unittest.TestCase):
    def test_the_number_is_read_before_the_prose(self):
        data = {"palette": {"mix": {"narration": "-18 LUFS", "narration_lufs": -16}}}
        self.assertEqual(sound.narration_level(data), (-16.0, "the sheet's narration_lufs"))

    def test_the_prose_is_the_fallback(self):
        self.assertEqual(sound.narration_level({"palette": {"mix": {"narration": "-18 LUFS"}}}),
                         (-18.0, "the sheet's narration level"))
        self.assertEqual(sound.narration_level({"palette": {"mix": {"narration_lufs": "loud",
                                                                    "narration": "-18 LUFS"}}})[0],
                         -18.0)
        self.assertEqual(sound.narration_level({}), (sound.TARGET_LUFS, "programme target"))


class Output(unittest.TestCase):
    def test_the_sheet_and_the_markdown_show_the_mode(self):
        idea = Idea(title="T", description="d", genre="g")
        c = cues()
        data = {"runtime": 60, "palette": palette(), "coverage": {},
                "normalize": MIX["normalize"], "cues": [asdict(x) for x in c]}
        with tempfile.TemporaryDirectory() as tmp:
            sound.write_md(f"{tmp}/s.md", idea, data, c)
            md = Path(f"{tmp}/s.md").read_text()
        self.assertIn("dynamic", md)
        self.assertIn("designed silences", md)
        out = io.StringIO()
        sys.stdout, saved = out, sys.stdout
        try:
            sound.print_sound(idea, data, c)
        finally:
            sys.stdout = saved
        self.assertIn("normalize: dynamic", out.getvalue())
        self.assertIn("designed silences", out.getvalue())


class Build(unittest.TestCase):
    """The sheet's mode over the flag's default; an explicit flag overrides."""

    def setUp(self):
        from tests.test_build_audio import _idea, _sections, _wav
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "vfx").mkdir()
        (self.root / "vfx" / "open.scene.xml").write_text("<scene/>")
        _wav(self.root / "sound" / "pad.wav", 12)
        _wav(self.root / "sound" / "hit.wav", 1)
        self.vfx = {"scene": {"by_label": {
            label: {"file": "vfx/open.scene.xml", "symbol": "fx-all"}
            for label in ("1.1", "1.2", "2.1")}}}
        self.idea, self.sections = _idea(), _sections()

    def tearDown(self):
        self._tmp.cleanup()

    def sheet(self, normalize=None):
        d = designer()
        d.plan = lambda: palette(normalize=normalize) if normalize else palette()
        d.spot_whole = cues
        data = d.run()
        data["cues"][0].update(file="sound/pad.wav", seconds=6)
        data["cues"].append({"cue_id": "S1", "kind": "sfx", "at": 1, "seconds": 1,
                             "level": "-12 dBFS peak", "file": "sound/hit.wav"})
        return data

    def build(self, data, **kw):
        from tests.test_build_audio import _checked
        out, manifest = build.SceneBuilder(self.idea, self.sections, self.root,
                                           vfx=self.vfx, sound=data, verbose=False,
                                           **kw).build()
        return _checked(out).find("audioMix/master"), manifest

    def test_the_sheets_mode_is_read_over_the_flags_default(self):
        master, manifest = self.build(self.sheet())
        self.assertEqual(master.get("normalize"), "dynamic")
        self.assertEqual(manifest["scene"]["normalize"], "dynamic")
        self.assertEqual(manifest["scene"]["normalize_from"], "sound.json")
        self.assertFalse(any("normalize=dynamic asked with no reason" in f
                             for f in manifest["findings"]))

    def test_an_explicit_flag_overrides_the_sheet(self):
        master, manifest = self.build(self.sheet(), normalize="integrated")
        self.assertEqual(master.get("normalize"), "integrated")
        self.assertEqual(manifest["scene"]["normalize_from"], "the --normalize flag")

    def test_no_sheet_decision_and_no_flag_is_integrated(self):
        data = self.sheet()
        del data["normalize"]
        master, manifest = self.build(data)
        self.assertEqual(master.get("normalize"), "integrated")
        self.assertEqual(manifest["scene"]["normalize_from"], "default")

    def test_the_sheets_none_turns_the_transient_staging_off_too(self):
        master, manifest = self.build(self.sheet(
            normalize={"mode": "none", "reason": "a reference mix for the ear"}))
        self.assertEqual(master.get("normalize"), "none")
        self.assertIsNone(master.get("loudness"))
        self.assertIsNone(manifest["audio_staging"]["bed_db"])

    def test_the_cli_flag_has_no_default_of_its_own(self):
        import argparse
        p = argparse.ArgumentParser()
        sub = p.add_subparsers()
        build.register(sub)
        self.assertIsNone(p.parse_args(["build"]).normalize)


if __name__ == "__main__":
    unittest.main()
