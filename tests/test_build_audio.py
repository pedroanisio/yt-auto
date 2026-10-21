"""The build places the sound design, not only the narration, and one
symbol that carries several consecutive shots is included once.

Every document built here is read back through the sr_core SDK and checked,
so an attribute the schema does not have fails the test rather than the
engine.
"""

import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from xml.etree import ElementTree

from ytstudio import build
from ytstudio.core import Idea
from ytstudio.scene_sdk import sdk
from ytstudio.script import Section, Shot


def _idea():
    return Idea.from_raw({"title": "A film", "genre": "explainer",
                          "description": "a premise"})


def _sections():
    return [Section(heading="Open", shots=[
                Shot(label="1.1", n=1, visual="a thing appears", narration="Hello there",
                     seconds=4, at=0),
                Shot(label="1.2", n=2, visual="it turns", narration="and it turns",
                     seconds=3, at=4)]),
            Section(heading="Close", shots=[
                Shot(label="2.1", n=3, visual="the end card", narration="goodbye",
                     on_screen="THE END", seconds=3, at=7)])]


def _wav(path: Path, seconds: float, rate: int = 8000) -> None:
    """A quiet sine, long enough to be a cue."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(
            struct.pack("<h", int(3000 * math.sin(2 * math.pi * 220 * n / rate)))
            for n in range(frames)))


def _checked(data: bytes) -> ElementTree.Element:
    """The document, parsed, after the SDK found nothing wrong with it."""
    errors = [str(f) for f in sdk().read(data).check() if str(f).startswith("error")]
    assert not errors, errors
    return ElementTree.fromstring(data)


class _Project(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "vfx").mkdir()
        (self.root / "vfx" / "open.scene.xml").write_text("<scene/>")

    def tearDown(self):
        self._tmp.cleanup()

    def builder(self, **kw):
        kw.setdefault("verbose", False)
        return build.SceneBuilder(_idea(), _sections(), self.root, **kw)


class SpanMerge(_Project):
    def vfx(self, *symbols):
        return {"scene": {"by_label": {
            label: {"file": "vfx/open.scene.xml", "symbol": symbol}
            for label, symbol in zip(("1.1", "1.2", "2.1"), symbols)}}}

    def test_consecutive_shots_on_one_symbol_share_one_include(self):
        data, manifest = self.builder(vfx=self.vfx("fx-a", "fx-a", "fx-b"),
                                      audio=False).build()
        root = _checked(data)
        includes = root.findall(".//include")
        self.assertEqual([(i.get("id"), i.get("start"), i.get("end"), i.get("symbol"))
                          for i in includes],
                         [("shot-1.1", "0", "7", "fx-a"),
                          ("shot-2.1", "7", "10.5", "fx-b")])
        shots = {s["label"]: s for s in manifest["shots"]}
        self.assertEqual(shots["1.1"]["include"], "shot-1.1")
        self.assertEqual(shots["1.2"]["include"], "shot-1.1")
        self.assertEqual(shots["2.1"]["include"], "shot-2.1")
        self.assertEqual(shots["1.2"]["asset"], "effect")
        # the markers are still one per shot
        beats = [m for m in root.iter("marker") if m.get("kind") == "beat"]
        self.assertEqual([m.get("label") for m in beats], ["1.1", "1.2", "2.1"])
        self.assertEqual(manifest["includes"], 2)

    def test_a_symbol_reused_later_is_a_separate_include(self):
        data, manifest = self.builder(vfx=self.vfx("fx-a", "fx-b", "fx-a"),
                                      audio=False).build()
        root = _checked(data)
        self.assertEqual([i.get("id") for i in root.findall(".//include")],
                         ["shot-1.1", "shot-1.2", "shot-2.1"])
        self.assertEqual(manifest["includes"], 3)

    def test_an_extended_gap_does_not_break_the_run(self):
        vfx = {"scene": {"by_label": {
            "1.1": {"file": "vfx/open.scene.xml", "symbol": "fx-a"},
            "2.1": {"file": "vfx/open.scene.xml", "symbol": "fx-a"}}}}
        edit = {"coverage": [{"label": "1.2", "action": "extend_previous",
                              "reason": "same motif"}],
                "captions_off": [], "tail_seconds": 1.0, "notes": "", "blocking": []}
        data, manifest = self.builder(vfx=vfx, edit=edit, audio=False).build()
        root = _checked(data)
        includes = root.findall(".//include")
        self.assertEqual(len(includes), 1)
        self.assertEqual((includes[0].get("start"), includes[0].get("end")), ("0", "11"))
        shots = {s["label"]: s for s in manifest["shots"]}
        self.assertEqual({s["include"] for s in shots.values()}, {"shot-1.1"})
        self.assertEqual(manifest["placeholders"], [])


def _sound(cues):
    return {"runtime": 10, "cues": cues}


class AudioFromSound(_Project):
    def setUp(self):
        super().setUp()
        # every shot drawn, so only the sound can decide `publishable`
        self.vfx = {"scene": {"by_label": {
            label: {"file": "vfx/open.scene.xml", "symbol": "fx-all"}
            for label in ("1.1", "1.2", "2.1")}}}
        _wav(self.root / "sound" / "001-pad.wav", 12)
        _wav(self.root / "sound" / "002-tick.wav", 1)
        (self.root / "voice").mkdir()
        _wav(self.root / "voice" / "1-1.wav", 4)
        self.voice = {"lang": "en", "takes": [
            {"label": "1.1", "at": 0, "seconds": 4, "file": "1-1.wav",
             "text": "Hello there"}]}

    def builder(self, **kw):
        kw.setdefault("vfx", self.vfx)
        return super().builder(**kw)

    def cues(self):
        return [
            {"cue_id": "M1", "kind": "music", "at": 2, "seconds": 6,
             "level": "-30 dBFS peak, 6-9 dB below any Foley event",
             "description": "a cello fifth", "file": "sound/001-pad.wav",
             "licence": "CC0", "credit": "someone", "fade_in": 1.5, "fade_out": 2},
            {"cue_id": "S1", "kind": "sfx", "at": 1, "seconds": 1,
             "level": "peak -22 dBFS", "description": "a paper slip"},
            {"cue_id": "A1", "kind": "ambience", "at": 0, "seconds": 10,
             "level": "-50 dBFS RMS", "description": "room tone",
             "file": "sound/none.wav"},
            {"cue_id": "X1", "kind": "stinger", "at": 7, "seconds": 3,
             "level": "-18 dBFS", "gain_db": 4, "description": "a tick",
             "file": "sound/002-tick.wav", "loop": True},
            {"cue_id": "Q1", "kind": "silence", "at": 5, "seconds": 2,
             "level": "", "description": "a held breath"},
        ]

    def test_cues_with_files_become_tracks_on_a_bus_per_role(self):
        data, manifest = self.builder(voice=self.voice,
                                      sound=_sound(self.cues())).build()
        root = _checked(data)
        mix = root.find("audioMix")
        self.assertIsNotNone(mix)
        tracks = {t.get("id"): t for t in mix.findall("audioTrack")}
        buses = {b.get("id"): b for b in mix.findall("bus")}
        self.assertEqual(set(buses), {"narration", "music", "effects"})
        music = tracks["cue-M1"]
        self.assertEqual(music.get("bus"), "music")
        self.assertEqual(music.get("role"), "music")
        self.assertEqual(music.get("start"), "2")
        self.assertEqual(music.get("clipOut"), "6")
        self.assertEqual(music.get("fadeIn"), "1.5")
        self.assertEqual(music.get("fadeOut"), "2")
        self.assertEqual(tracks["cue-X1"].get("bus"), "effects")
        self.assertEqual(tracks["cue-X1"].get("role"), "effects")
        self.assertEqual(tracks["vo-track-1.1"].get("bus"), "narration")
        # music and effects both duck under the narration
        self.assertEqual(buses["music"].get("duckUnder"), "narration")
        self.assertEqual(buses["effects"].get("duckUnder"), "narration")
        # the asset carries its provenance, like an image does
        asset = root.find("assets/audio[@id='cue-asset-M1']")
        self.assertEqual(asset.get("src"), "sound/001-pad.wav")
        self.assertEqual(asset.get("license"), "CC0")
        self.assertEqual(asset.get("credit"), "someone")
        # the report
        self.assertEqual(manifest["audio_tracks"], 3)
        self.assertEqual(manifest["buses"], {"narration": 1, "music": 1, "effects": 1})
        self.assertEqual([m["cue"] for m in manifest["audio_missing"]], ["S1", "A1"])
        self.assertIn("paper slip", manifest["audio_missing"][0]["description"])
        self.assertTrue(manifest["publishable"])
        self.assertEqual(manifest["scene"]["normalize"], "integrated")
        self.assertEqual(mix.find("master").get("normalize"), "integrated")
        self.assertEqual(mix.find("master").get("limiter"), "true")

    def test_gain_is_staged_across_bus_and_track_within_the_schemas_facets(self):
        cues = [{"cue_id": "S1", "kind": "sfx", "at": 0, "seconds": 2,
                 "level": "", "gain_db": 36, "description": "a quiet library",
                 "file": "sound/002-tick.wav"},
                {"cue_id": "S2", "kind": "sfx", "at": 3, "seconds": 2,
                 "level": "-6 dBFS", "gain_db": 30, "description": "another",
                 "file": "sound/002-tick.wav"}]
        data, _ = self.builder(sound=_sound(cues)).build()
        root = _checked(data)
        mix = root.find("audioMix")
        lo, hi = build.facets_of("audioTrack", "gain")
        self.assertEqual((lo, hi), (-120.0, 24.0))
        bus = float(mix.find("bus[@id='effects']").get("gain"))
        self.assertLessEqual(bus, hi)
        for cue_id, total in (("S1", 36.0), ("S2", 24.0)):
            track = float(mix.find(f"audioTrack[@id='cue-{cue_id}']").get("gain", 0))
            self.assertLessEqual(track, hi)
            self.assertGreaterEqual(track, lo)
            self.assertAlmostEqual(bus + track, total, places=3)

    def test_a_looping_cue_repeats_to_fill_its_window(self):
        data, _ = self.builder(sound=_sound([self.cues()[3]])).build()
        root = _checked(data)
        track = root.find("audioMix/audioTrack[@id='cue-X1']")
        # a one-second file over a three-second cue: two extra plays
        self.assertEqual(track.get("loop"), "2")
        self.assertIsNone(track.get("clipOut"))

    def test_silence_is_a_window_on_the_score_and_effects_never_the_master(self):
        # measured on the Euler draft: a master window muted the narration
        # with the music, 30 s of digital silence over spoken lines
        data, manifest = self.builder(sound=_sound(self.cues())).build()
        root = _checked(data)
        mix = root.find("audioMix")
        self.assertIsNone(mix.find("audioTrack[@id='cue-Q1']"))
        self.assertIsNone(mix.find("master/animate[@property='volume']"))
        gated = {b.get("id") for b in mix.findall("bus") if b.find("animate[@property='volume']") is not None}
        self.assertEqual(gated, {b.get("id") for b in mix.findall("bus")} & {"music", "effects"})
        self.assertTrue(gated)
        for bus in ("narration", "ambience"):
            node = mix.find(f"bus[@id='{bus}']")
            if node is not None:
                self.assertIsNone(node.find("animate[@property='volume']"), bus)
        animate = mix.find(f"bus[@id='{sorted(gated)[0]}']/animate[@property='volume']")
        keys = [(float(k.get("time")), k.get("value")) for k in animate.findall("key")]
        self.assertEqual(keys[0][1], "1")
        self.assertIn((5.0, "0"), keys)
        self.assertEqual(keys[-1], (7.0, "1"))
        self.assertEqual(manifest["silence_windows"], 1)

    def test_a_film_without_narration_is_scored_and_nothing_ducks(self):
        data, manifest = self.builder(sound=_sound(self.cues())).build()
        root = _checked(data)
        mix = root.find("audioMix")
        self.assertEqual({b.get("id") for b in mix.findall("bus")}, {"music", "effects"})
        self.assertIsNone(mix.find("bus[@id='music']").get("duckUnder"))
        self.assertEqual(manifest["audio_tracks"], 2)
        self.assertTrue(manifest["publishable"])

    def test_a_sound_design_nothing_could_place_blocks_publication(self):
        cues = [c for c in self.cues() if c["cue_id"] in ("S1", "A1", "Q1")]
        data, manifest = self.builder(sound=_sound(cues)).build()
        _checked(data)
        self.assertFalse(manifest["publishable"])
        self.assertEqual([m["cue"] for m in manifest["audio_missing"]], ["S1", "A1"])
        self.assertTrue(any("sound design" in f for f in manifest["findings"]))

    def test_no_sound_design_is_not_a_block(self):
        _, manifest = self.builder(voice=self.voice).build()
        self.assertTrue(manifest["publishable"])
        self.assertEqual(manifest["audio_missing"], [])
        self.assertEqual(manifest["buses"], {"narration": 1})

    def test_no_audio_leaves_the_mix_out(self):
        data, manifest = self.builder(voice=self.voice, sound=_sound(self.cues()),
                                      audio=False).build()
        root = _checked(data)
        self.assertIsNone(root.find("audioMix"))
        self.assertEqual(manifest["audio_tracks"], 0)


class TransientStaging(_Project):
    """The limiter runs after normalisation, so transients far above the bed
    cost the programme its loudness. Short effects are shifted, as a group,
    to sit within the headroom the master's own targets leave."""

    def setUp(self):
        super().setUp()
        self.vfx = {"scene": {"by_label": {
            label: {"file": "vfx/open.scene.xml", "symbol": "fx-all"}
            for label in ("1.1", "1.2", "2.1")}}}
        _wav(self.root / "sound" / "bed.wav", 10)
        _wav(self.root / "sound" / "hit.wav", 1)

    def cue(self, cue_id, kind, at, seconds, level, file="sound/hit.wav"):
        return {"cue_id": cue_id, "kind": kind, "at": at, "seconds": seconds,
                "level": level, "description": cue_id, "file": file}

    def gains(self, data):
        mix = _checked(data).find("audioMix")
        bus = {b.get("id"): float(b.get("gain", 0)) for b in mix.findall("bus")}
        return {t.get("id")[4:]: round(bus[t.get("bus")] + float(t.get("gain", 0)), 3)
                for t in mix.findall("audioTrack")}

    def test_the_rule_is_derived_from_the_masters_targets(self):
        self.assertEqual(build.transient_headroom(-14.0, -1.0), 13.0 - build.DEFAULT_CREST_DB)
        shift, bed = build.transient_shift(
            [("music", 9, -30.0), ("ambience", 10, -50.0), ("sfx", 8, -34.0),
             ("sfx", 1, -12.0), ("sfx", 1, -22.0)], headroom=7.0)
        self.assertEqual((shift, bed), (11.0, -30.0))

    def test_transients_are_shifted_as_a_group_to_the_headroom(self):
        cues = [self.cue("A1", "ambience", 0, 10, "-50 dBFS RMS", "sound/bed.wav"),
                self.cue("M1", "music", 0, 9, "-30 dBFS peak", "sound/bed.wav"),
                self.cue("S1", "sfx", 1, 8, "-34 dBFS", "sound/bed.wav"),
                self.cue("S2", "sfx", 2, 1, "-12 dBFS peak"),
                self.cue("S3", "sfx", 4, 1, "-22 dBFS peak")]
        data, manifest = self.builder(vfx=self.vfx, sound=_sound(cues)).build()
        gains = self.gains(data)
        headroom = build.transient_headroom(-14.0, -1.0)
        # the beds and the long effect keep the sheet's level
        self.assertEqual(gains["A1"], -50.0)
        self.assertEqual(gains["M1"], -30.0)
        self.assertEqual(gains["S1"], -34.0)
        # the loudest transient peaks `headroom` above the loudest bed, and
        # the quieter one keeps its distance from it
        self.assertEqual(gains["S2"], -30.0 + headroom)
        self.assertEqual(gains["S3"], -40.0 + headroom)
        staging = manifest["audio_staging"]
        self.assertEqual(staging["bed_db"], -30.0)
        self.assertEqual(staging["headroom_db"], headroom)
        self.assertEqual(staging["shift_db"], 18.0 - headroom)
        self.assertEqual(staging["shifted"], ["S2", "S3"])
        self.assertTrue(any("headroom" in f for f in manifest["findings"]))

    def test_transients_already_within_the_headroom_are_left_alone(self):
        cues = [self.cue("M1", "music", 0, 9, "-30 dBFS", "sound/bed.wav"),
                self.cue("S1", "sfx", 2, 1, "-28 dBFS peak")]
        data, manifest = self.builder(vfx=self.vfx, sound=_sound(cues)).build()
        self.assertEqual(self.gains(data)["S1"], -28.0)
        self.assertEqual(manifest["audio_staging"]["shift_db"], 0.0)
        self.assertFalse(any("headroom" in f for f in manifest["findings"]))

    def test_without_a_bed_nothing_is_shifted_and_the_risk_is_named(self):
        cues = [self.cue("S1", "sfx", 2, 1, "-12 dBFS peak"),
                self.cue("S2", "sfx", 4, 1, "-30 dBFS peak")]
        data, manifest = self.builder(vfx=self.vfx, sound=_sound(cues)).build()
        self.assertEqual(self.gains(data)["S1"], -12.0)
        self.assertIsNone(manifest["audio_staging"]["bed_db"])
        self.assertTrue(any("no bed" in f for f in manifest["findings"]))


class LevelParsing(unittest.TestCase):
    def test_the_first_absolute_level_wins_over_relative_notes(self):
        self.assertEqual(build.level_db("-30 dBFS peak, 6-9 dB below any Foley"), -30.0)
        self.assertEqual(build.level_db("about -42 dBFS peak, 8 dB under"), -42.0)
        self.assertEqual(build.level_db("+2 dB 2-5 kHz on the bed, bed about -50 dBFS"),
                         -50.0)
        self.assertEqual(build.level_db("-36 to -30 dBFS"), -36.0)
        self.assertEqual(build.level_db("about -34 LUFS short-term"), -34.0)
        self.assertEqual(build.level_db("-20 dB"), -20.0)
        self.assertIsNone(build.level_db("quiet, under the voice"))
        self.assertIsNone(build.level_db(""))


if __name__ == "__main__":
    unittest.main()


class MeasuredCrest(unittest.TestCase):
    """The bed's crest is this film's measurement, not a constant fitted to
    another film: a generic start, corrected by the last mix's shortfall."""

    def write(self, root, crest, measured, *, render_newer=True):
        import json, os, time
        (root / "build.json").write_text(json.dumps(
            {"audio_staging": {"crest_db": crest}}), encoding="utf-8")
        (root / "render.json").write_text(json.dumps(
            {"result": {"outputs": [{"loudness": measured}]}}), encoding="utf-8")
        now = time.time()
        built, rendered = (now - 10, now) if render_newer else (now, now - 10)
        os.utime(root / "build.json", (built, built))
        os.utime(root / "render.json", (rendered, rendered))

    def test_with_no_measurement_the_generic_start_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            crest, source = build.measured_crest(Path(tmp), target=-14.0)
        self.assertEqual((crest, source), (build.DEFAULT_CREST_DB, "default"))

    def test_a_shortfall_raises_the_crest_by_that_much(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write(Path(tmp), 6.0, -14.9)
            crest, source = build.measured_crest(Path(tmp), target=-14.0)
        self.assertAlmostEqual(crest, 6.9, places=3)
        self.assertEqual(source, "measured")

    def test_a_mix_on_target_keeps_the_crest(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write(Path(tmp), 7.5, -14.2)
            self.assertEqual(build.measured_crest(Path(tmp), target=-14.0)[0], 7.5)

    def test_a_render_older_than_the_build_is_not_evidence_for_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write(Path(tmp), 6.0, -17.0, render_newer=False)
            self.assertEqual(build.measured_crest(Path(tmp), target=-14.0),
                             (build.DEFAULT_CREST_DB, "default"))

    def test_the_crest_never_eats_the_whole_ceiling(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write(Path(tmp), 6.0, -40.0)
            crest = build.measured_crest(Path(tmp), target=-14.0, true_peak=-1.0)[0]
        self.assertLessEqual(crest, 13.0 - build.MIN_TRANSIENT_ROOM_DB)

    def test_headroom_takes_the_crest_it_is_given(self):
        self.assertEqual(build.transient_headroom(-14.0, -1.0, crest=9.0), 4.0)
