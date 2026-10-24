"""The build places the sound design, not only the narration, and one
symbol that carries several consecutive shots is included once.

Every document built here is read back through the sr_core SDK and checked,
so an attribute the schema does not have fails the test rather than the
engine.
"""

import contextlib
import io
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


# --------------------------------------------------------------------------- #
# cue anchors (AUTO-140): a cue follows the spoken word it was spotted on
# --------------------------------------------------------------------------- #

def _w(*pairs):
    return [{"text": t, "start": s, "end": round(s + 0.3, 3)} for t, s in pairs]


def _take(label, at, seconds, text, words, file=None):
    return {"label": label, "at": at, "seconds": seconds, "text": text,
            "file": file or f"{label.replace('.', '-')}.wav", "words": words}


def _voice_v1():
    return {"lang": "en", "takes": [
        _take("1.1", 0, 4, "Hello there", _w(("Hello", 0.0), ("there", 0.5))),
        _take("1.2", 4, 3, "and it turns", _w(("and", 0.0), ("it", 0.4), ("turns", 0.9))),
        _take("2.1", 7, 3, "the end of the line",
              _w(("the", 0.0), ("end", 0.4), ("of", 0.8), ("the", 1.1), ("line", 1.5)))]}


class _Voiced(_Project):
    """Three recorded shots, every one drawn, and the files the cues name."""

    def setUp(self):
        super().setUp()
        self.vfx = {"scene": {"by_label": {
            label: {"file": "vfx/open.scene.xml", "symbol": "fx-all"}
            for label in ("1.1", "1.2", "2.1")}}}
        _wav(self.root / "sound" / "hit.wav", 1)
        _wav(self.root / "sound" / "bed.wav", 14)
        for name in ("1-1", "1-2", "2-1"):
            _wav(self.root / "voice" / f"{name}.wav", 1)

    def sheet(self, *cues, voice=None, sections=None):
        """The cues as a fresh `yt sound` writes them: anchored on voice v1."""
        from ytstudio import sound
        voice = voice or _voice_v1()
        cues = [dict(c) for c in cues]
        sound.anchor_cues(cues, build.timeline(sections or _sections(), voice), voice)
        return {"runtime": 10, "cues": cues}

    def hit(self, cue_id, at, seconds=1, **kw):
        return {"cue_id": cue_id, "kind": "sfx", "at": at, "seconds": seconds,
                "level": "-20 dBFS", "description": cue_id, "file": "sound/hit.wav", **kw}

    def build_with(self, voice, sound, sections=None, verbose=False):
        builder = build.SceneBuilder(_idea(), sections or _sections(), self.root,
                                     vfx=self.vfx, voice=voice, sound=sound,
                                     verbose=verbose)
        return builder.build()

    def rows(self, manifest):
        return {r["cue"]: r for r in manifest["cue_anchors"]}

    def start(self, data, cue_id):
        return _checked(data).find(f"audioMix/audioTrack[@id='cue-{cue_id}']").get("start")


class Anchors(_Voiced):
    def test_a_cue_follows_its_word_when_an_earlier_take_is_redone_longer(self):
        sheet = self.sheet(self.hit("S1", 5))
        self.assertEqual(sheet["cues"][0]["anchor"]["text"], "turns")
        self.assertEqual(sheet["cues"][0]["anchor"]["word"], 2)
        voice = _voice_v1()
        # 1.1 re-recorded 2.4 s longer: 1.2 and everything after start later
        voice["takes"][0].update(seconds=6.4, words=_w(("Hello", 0.0), ("there", 2.9)))
        voice["takes"][1]["at"] = 6.4
        voice["takes"][2]["at"] = 9.4
        data, manifest = self.build_with(voice, sheet)
        row = self.rows(manifest)["S1"]
        self.assertEqual((row["sheet_at"], row["placed_at"], row["status"]),
                         (5.0, 7.4, "moved"))
        self.assertEqual(row["anchor"], sheet["cues"][0]["anchor"])
        self.assertEqual(self.start(data, "S1"), "7.4")

    def test_an_unchanged_voice_keeps_every_cue_where_the_sheet_put_it(self):
        sheet = self.sheet(self.hit("S1", 5))
        data, manifest = self.build_with(_voice_v1(), sheet)
        self.assertEqual(self.rows(manifest)["S1"]["status"], "kept")
        self.assertEqual(self.start(data, "S1"), "5")

    def test_a_different_take_with_identical_timing_is_moved_not_kept(self):
        sheet = self.sheet(self.hit("S1", 5))
        voice = _voice_v1()
        voice["takes"][1]["sha256"] = "f" * 64
        _, manifest = self.build_with(voice, sheet)
        row = self.rows(manifest)["S1"]
        self.assertEqual((row["placed_at"], row["status"]), (5.0, "moved"))

    def test_the_anchor_word_at_another_index_is_found_by_its_text(self):
        sheet = self.sheet(self.hit("S1", 5))
        voice = _voice_v1()
        voice["takes"][1].update(text="so and it turns", words=_w(
            ("so", 0.0), ("and", 0.3), ("it", 0.6), ("Turns,", 1.1)))
        data, manifest = self.build_with(voice, sheet)
        row = self.rows(manifest)["S1"]
        self.assertEqual((row["placed_at"], row["status"]), (5.2, "moved"))
        self.assertEqual(self.start(data, "S1"), "5.2")

    def test_a_repeated_word_is_lost_as_ambiguous_never_guessed(self):
        sheet = self.sheet(self.hit("X1", 7))
        self.assertEqual((sheet["cues"][0]["anchor"]["word"],
                          sheet["cues"][0]["anchor"]["text"]), (0, "the"))
        voice = _voice_v1()
        voice["takes"][2].update(text="so the end of the line", words=_w(
            ("so", 0.0), ("the", 0.3), ("end", 0.6), ("of", 0.9), ("the", 1.2),
            ("line", 1.6)))
        data, manifest = self.build_with(voice, sheet)
        row = self.rows(manifest)["X1"]
        self.assertEqual((row["placed_at"], row["status"]), (7.0, "lost"))
        self.assertEqual(self.start(data, "X1"), "7")
        warned = [f for f in manifest["findings"] if "X1" in f and "anchor" in f]
        self.assertTrue(warned and warned[0].startswith("warning:"), manifest["findings"])
        self.assertIn("ambiguous", warned[0])
        self.assertIn("7s", warned[0])

    def test_a_removed_shot_is_lost_and_the_cue_keeps_its_sheet_time(self):
        sheet = self.sheet(self.hit("S1", 5))
        sections = _sections()
        sections[0].shots = sections[0].shots[:1]
        voice = _voice_v1()
        del voice["takes"][1]
        _, manifest = self.build_with(voice, sheet, sections=sections)
        row = self.rows(manifest)["S1"]
        self.assertEqual((row["placed_at"], row["status"]), (5.0, "lost"))
        self.assertTrue(any(f.startswith("warning:") and "S1" in f and "1.2" in f
                            for f in manifest["findings"]), manifest["findings"])

    def test_an_overlap_made_by_re_anchoring_is_a_build_warning(self):
        # S1 on 1.2's "turns", S2 on 2.1's first "the": apart on the sheet
        sheet = self.sheet(self.hit("S1", 5), self.hit("S2", 7),
                           {"cue_id": "A1", "kind": "ambience", "at": 0, "seconds": 10,
                            "level": "-50 dBFS", "description": "room", "fade_out": 1,
                            "file": "sound/bed.wav"})
        voice = _voice_v1()
        voice["takes"][1].update(at=6.4, seconds=0.6)   # 1.2 redone, later
        _, manifest = self.build_with(voice, sheet)
        rows = self.rows(manifest)
        self.assertEqual((rows["S1"]["placed_at"], rows["S2"]["placed_at"]), (7.4, 7.0))
        overlap = [f for f in manifest["findings"] if "rule:overlap" in f]
        self.assertEqual(len(overlap), 1, manifest["findings"])
        self.assertTrue(overlap[0].startswith("warning:"))
        self.assertIn("S1", overlap[0])
        self.assertFalse(any("rule:no-bed" in f for f in manifest["findings"]))

    def test_a_sheet_without_anchors_places_as_before_and_says_so_once(self):
        plain = {"runtime": 10, "cues": [self.hit("S1", 5)]}
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            data, manifest = self.build_with(_voice_v1(), plain, verbose=True)
        self.assertNotIn("cue_anchors", manifest)
        self.assertEqual(self.start(data, "S1"), "5")
        line = ("cue sheet has no anchors: cues will not follow a redone voice "
                "(re-spot with yt sound --fresh)")
        self.assertEqual(err.getvalue().count(line), 1)
        # the same cues anchored and kept build the same document
        anchored, _ = self.build_with(_voice_v1(), self.sheet(self.hit("S1", 5)))
        self.assertEqual(data, anchored)

    def test_the_summary_names_the_moved_and_the_lost(self):
        sheet = self.sheet(self.hit("S1", 5), self.hit("X1", 7))
        voice = _voice_v1()
        voice["takes"][1]["at"] = 4.5
        voice["takes"][2].update(words=_w(("so", 0.0), ("the", 0.3), ("the", 1.2)))
        _, manifest = self.build_with(voice, sheet)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            build.print_build(manifest)
        self.assertIn("cue anchors: 1 moved (S1), 1 lost (X1)", out.getvalue())


# --------------------------------------------------------------------------- #
# beat_sync (AUTO-140): how close the cuts land to the music, measured only
# --------------------------------------------------------------------------- #

GRID = {"bpm": 120.0, "period": 0.5, "first_beat": 0.1, "downbeat_first": 0.6,
        "confidence": 0.9}


def _shot(label, at, seconds=1.0, function=""):
    return build.Placed(label=label, at=at, seconds=seconds, function=function,
                        narration=f"line of {label}")


def _row(cue_id="M1", start=2.0, length=20.0, loops=0, clip_out=10.0, grid=GRID):
    return {"cue_id": cue_id, "start": start, "length": length, "loops": loops,
            "clip_out": clip_out, "beat_grid": grid}


class BeatSync(unittest.TestCase):
    def test_cuts_at_known_offsets_from_a_known_grid(self):
        # music 2..12 s, beats at 2.1 + k/2, downbeats at 2.6 + 2k
        placed = [_shot("1", 0, 3.15), _shot("2", 3.15, 1.35), _shot("3", 4.5, 2.12),
                  _shot("4", 6.62, 6.38, function="reveal"), _shot("5", 13.0)]
        got = build.beat_sync(placed, [_row()])
        self.assertEqual(got["cuts_in_music"], 3)
        self.assertEqual(got["cuts_outside_music"], 1)
        self.assertAlmostEqual(got["on_beat_share"], 0.67, places=2)
        self.assertEqual(got["median_abs_offset_ms"], 50)
        self.assertAlmostEqual(got["downbeat_share"], 0.33, places=2)
        self.assertEqual(got["reveals"], [{"label": "4", "at": 6.62,
                                           "downbeat_offset_ms": 20, "cue": "M1"}])
        self.assertEqual(got["unmeasured"], [])
        self.assertLessEqual(build.BEAT_TOLERANCE_S, 0.1)

    def test_offsets_are_signed_cut_minus_beat(self):
        placed = [_shot("1", 0, 4.5), _shot("2", 4.5)]
        got = build.beat_sync(placed, [_row()])
        self.assertEqual(got["cuts"], [{"at": 4.5, "cue": "M1", "beat_offset_ms": -100,
                                        "downbeat_offset_ms": -100}])

    def test_a_looped_row_restarts_its_grid_at_each_play(self):
        # a 1.3 s file played three times from 0: beats 0.1 0.6 1.1 | 1.4 1.9 2.4 | 2.7 ...
        row = _row(start=0.0, length=1.3, loops=2, clip_out=None)
        got = build.beat_sync([_shot("1", 0, 1.4), _shot("2", 1.4, 2.6), _shot("3", 4.0)],
                              [row])
        self.assertEqual(got["cuts_in_music"], 1)
        self.assertEqual(got["cuts"][0]["beat_offset_ms"], 0)
        self.assertEqual(got["cuts_outside_music"], 1)          # 4.0 is past 3.9

    def test_the_grid_ends_at_clip_out(self):
        row = _row(start=0.0, length=20.0, clip_out=1.0)
        got = build.beat_sync([_shot("1", 0, 1.1), _shot("2", 1.1)], [row])
        self.assertEqual((got["cuts_in_music"], got["cuts_outside_music"]), (0, 1))

    def test_cuts_under_unmeasured_music_are_listed_not_scored(self):
        placed = [_shot("1", 0, 3.0), _shot("2", 3.0, 3.0), _shot("3", 6.0)]
        got = build.beat_sync(placed, [_row(grid=None)])
        self.assertEqual(got["unmeasured"], ["M1"])
        self.assertEqual((got["cuts_in_music"], got["cuts_outside_music"]), (0, 0))
        self.assertIsNone(got["on_beat_share"])
        self.assertIsNone(got["median_abs_offset_ms"])


class BeatSyncBuilt(_Voiced):
    def sounding(self, grid=GRID):
        _wav(self.root / "sound" / "bed.wav", 14)
        cue = {"cue_id": "M1", "kind": "music", "at": 2, "seconds": 8,
               "level": "-30 dBFS", "description": "a pulse", "fade_out": 1,
               "file": "sound/bed.wav"}
        if grid:
            cue["beat_grid"] = grid
        return {"runtime": 10, "cues": [cue, self.hit("S1", 5)]}

    def test_the_mix_reports_its_music_rows(self):
        builder = build.SceneBuilder(_idea(), _sections(), self.root, vfx=self.vfx,
                                     voice=_voice_v1(), sound=self.sounding(), verbose=False)
        seen = {}
        mix = builder._mix
        builder._mix = lambda *a: seen.setdefault("report", mix(*a))
        builder.build()
        rows = seen["report"]["music_rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cue_id"], "M1")
        self.assertEqual((rows[0]["start"], rows[0]["loops"], rows[0]["clip_out"]),
                         (2.0, 0, 8.0))
        self.assertAlmostEqual(rows[0]["length"], 14.0, places=2)
        self.assertEqual(rows[0]["beat_grid"], GRID)

    def test_the_manifest_carries_beat_sync_and_markers_beats_is_still_the_shot_count(self):
        _, manifest = self.build_with(_voice_v1(), self.sounding())
        sync = manifest["beat_sync"]
        # the cuts at 4 and 7 s: beats at 2.1 + k/2, so 0.1 s before one each
        self.assertEqual(sync["cuts_in_music"], 2)
        self.assertEqual(sync["median_abs_offset_ms"], 100)
        self.assertEqual(manifest["markers"]["beats"], 3)
        self.assertTrue(manifest["publishable"])

    def test_without_a_grid_the_music_is_unmeasured_and_nothing_fails(self):
        _, manifest = self.build_with(_voice_v1(), self.sounding(grid=None))
        self.assertEqual(manifest["beat_sync"]["unmeasured"], ["M1"])

    def test_a_film_with_no_music_has_no_beat_sync(self):
        _, manifest = self.build_with(_voice_v1(), {"runtime": 10, "cues": [self.hit("S1", 5)]})
        self.assertNotIn("beat_sync", manifest)
