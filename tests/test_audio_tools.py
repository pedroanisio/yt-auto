"""The gate's three audio tools: sr-margin, sr-audiocheck, sr-sibilance.

Parsing is tested on captured ffmpeg text, the isolation of the narration on
scene XML, the arithmetic on numbers, and the real tools on synthetic WAVs
written here with the standard library. Tests that need ffmpeg skip without
it; none needs the engine, a network or a host.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "ytstudio" / "audiotools"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TOOLS))

import sr_audio as lib  # noqa: E402
from ytstudio import doctor, gate  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
RATE = 16000

EBUR128 = """\
[Parsed_ebur128_0 @ 0x5581] t: 0.1        TARGET:-23 LUFS    M:-120.7 S:-120.7     I: -70.0 LUFS       LRA:   0.0 LU  FTPK: -inf -inf dBFS  TPK: -inf -inf dBFS
[Parsed_ebur128_0 @ 0x5581] t: 0.2        TARGET:-23 LUFS    M: -31.2 S:-120.7     I: -31.2 LUFS       LRA:   0.0 LU  FTPK: -12.0 -12.1 dBFS  TPK: -12.0 -12.1 dBFS
[Parsed_ebur128_0 @ 0x5581] t: 3.4        TARGET:-23 LUFS    M: -22.5 S: -24.0     I: -25.1 LUFS       LRA:   2.1 LU  FTPK:  -1.0  -1.2 dBFS  TPK:  -1.0  -1.2 dBFS
[Parsed_ebur128_0 @ 0x5581] Summary:

  Integrated loudness:
    I:         -25.1 LUFS
    Threshold: -35.3 LUFS

  Loudness range:
    LRA:         2.1 LU
    Threshold: -45.3 LUFS
    LRA low:   -26.0 LUFS
    LRA high:  -23.9 LUFS

  True peak:
    Peak:       -0.4 dBFS
"""

ASTATS = """\
[Parsed_astats_1 @ 0x55] Channel: 1
[Parsed_astats_1 @ 0x55] DC offset: 0.000012
[Parsed_astats_1 @ 0x55] Peak level dB: -6.020600
[Parsed_astats_1 @ 0x55] Channel: 2
[Parsed_astats_1 @ 0x55] DC offset: -0.031000
[Parsed_astats_1 @ 0x55] Peak level dB: -6.020600
[Parsed_astats_1 @ 0x55] Overall
[Parsed_astats_1 @ 0x55] DC offset: 0.015506
"""

SILENCE = """\
[silencedetect @ 0x55] silence_start: 0
[silencedetect @ 0x55] silence_end: 0.5 | silence_duration: 0.5
[silencedetect @ 0x55] silence_start: 3.5
[silencedetect @ 0x55] silence_end: 6.25 | silence_duration: 2.75
[silencedetect @ 0x55] silence_start: 9.75
"""

AMETADATA = """\
frame:0    pts:0       pts_time:0
lavfi.astats.Overall.RMS_level=-20.5
frame:1    pts:800     pts_time:0.05
lavfi.astats.Overall.RMS_level=-inf
frame:2    pts:1600    pts_time:0.1
lavfi.astats.Overall.RMS_level=-18.25
"""

SCENE = """\
<scene xmlns:x="urn:x">
  <assets>
    <audio id="a-vo1" src="voice/one.wav" duration="4.0"/>
    <audio id="a-vo2" src="voice/two.wav" duration="6.0"/>
    <audio id="a-bed" src="music/bed.wav" duration="30"/>
    <audio id="a-hit" src="fx/hit.wav" duration="1"/>
  </assets>
  <audioMix>
    <audioTrack id="vo1" asset="a-vo1" start="1.0" bus="narration" role="voiceover"/>
    <audioTrack id="vo2" asset="a-vo2" start="8.0" clipIn="1.0" clipOut="5.0" speed="2" bus="narration"/>
    <audioTrack id="bed" asset="a-bed" start="0" bus="music" role="music"/>
    <audioTrack id="hit" asset="a-hit" start="3"/>
    <bus id="narration"/>
    <bus id="music" duckUnder="narration"/>
    <master normalize="integrated" loudness="-14" limiter="true"/>
  </audioMix>
</scene>
"""


def write_wav(path: Path, samples: list[float], rate: int = RATE) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(
            struct.pack("<h", max(-32768, min(32767, round(x * 32767)))) for x in samples))
    return path


def tone(seconds: float, hz: float, level_db: float, rate: int = RATE) -> list[float]:
    a = 10 ** (level_db / 20)
    return [a * math.sin(2 * math.pi * hz * n / rate) for n in range(int(seconds * rate))]


def noise(seconds: float, level_db: float, rate: int = RATE, seed: int = 1) -> list[float]:
    rng = random.Random(seed)
    a = 10 ** (level_db / 20) * math.sqrt(3)  # uniform: rms = a / sqrt(3)
    return [a * rng.uniform(-1, 1) for _ in range(int(seconds * rate))]


class Parsing(unittest.TestCase):
    def test_momentary_and_short_term_frames(self):
        frames = lib.parse_ebur128(EBUR128)
        self.assertEqual([f.t for f in frames], [0.1, 0.2, 3.4])
        self.assertEqual(frames[1].m, -31.2)
        self.assertEqual(frames[2].s, -24.0)

    def test_tight_columns_parse_too(self):
        line = "[Parsed_ebur128_0 @ 0x1] t: 12.3 TARGET:-23 LUFS    M:-20.1 S:-21.7     I:-22.0 LUFS"
        (f,) = lib.parse_ebur128(line)
        self.assertEqual((f.t, f.m, f.s), (12.3, -20.1, -21.7))

    def test_silent_frames_floor_at_the_scale_bottom(self):
        frames = lib.parse_ebur128(EBUR128)
        self.assertEqual(frames[0].m, lib.FLOOR_LUFS)
        self.assertEqual(frames[0].s, lib.FLOOR_LUFS)

    def test_the_summary(self):
        s = lib.parse_ebur128_summary(EBUR128)
        self.assertEqual(s["integrated"], -25.1)
        self.assertEqual(s["true_peak"], -0.4)
        self.assertEqual(s["lra"], 2.1)

    def test_a_missing_summary_is_none_not_a_guess(self):
        self.assertIsNone(lib.parse_ebur128_summary("nothing here")["true_peak"])

    def test_dc_offsets_per_channel_without_the_overall_row(self):
        self.assertEqual(lib.parse_dc_offsets(ASTATS), [0.000012, -0.031])

    def test_silences_close_at_the_end_when_the_file_ends_in_one(self):
        self.assertEqual(lib.parse_silences(SILENCE, 12.0),
                         [(0.0, 0.5), (3.5, 6.25), (9.75, 12.0)])

    def test_ametadata_values(self):
        rows = lib.parse_ametadata(AMETADATA, "lavfi.astats.Overall.RMS_level")
        self.assertEqual(rows, [(0.0, -20.5), (0.05, lib.FLOOR_DB), (0.1, -18.25)])


class Scene(unittest.TestCase):
    def test_narration_is_the_voice_role_or_the_narration_bus(self):
        tracks = lib.narration_tracks(SCENE, Path("/p"))
        self.assertEqual([t.id for t in tracks], ["vo1", "vo2"])

    def test_spans_follow_start_clip_and_speed(self):
        one, two = lib.narration_tracks(SCENE, Path("/p"))
        self.assertEqual((one.start, one.end), (1.0, 5.0))
        self.assertEqual((two.start, two.end), (8.0, 10.0))  # (5 - 1) s at 2x
        self.assertEqual(one.path, Path("/p/voice/one.wav"))

    def test_isolation_keeps_the_levels_and_mutes_the_other_side(self):
        voice, rest = lib.isolate(SCENE)
        v, r = ET.fromstring(voice), ET.fromstring(rest)
        for root in (v, r):
            master = root.find("audioMix/master")
            self.assertEqual(master.get("normalize"), "none")
            self.assertEqual(master.get("limiter"), "false")
            nulls = [b for b in root.iter("bus") if b.get("mute") == "true"]
            self.assertEqual(len(nulls), 1)
        null = [b for b in v.iter("bus") if b.get("mute") == "true"][0].get("id")
        tracks = {t.get("id"): t for t in v.iter("audioTrack")}
        self.assertEqual(tracks["vo1"].get("bus"), "narration")
        for other in ("bed", "hit"):
            self.assertEqual(tracks[other].get("bus"), null)
        # the rest: the narration bus feeds the muted bus, so what ducks under
        # it still senses it; the other tracks are untouched
        null = [b for b in r.iter("bus") if b.get("mute") == "true"][0].get("id")
        buses = {b.get("id"): b for b in r.iter("bus")}
        self.assertEqual(buses["narration"].get("output"), null)
        rtracks = {t.get("id"): t for t in r.iter("audioTrack")}
        self.assertEqual(rtracks["bed"].get("bus"), "music")
        self.assertEqual(buses["music"].get("duckUnder"), "narration")

    def test_a_narration_track_without_a_bus_goes_to_the_muted_bus(self):
        text = ('<scene><assets><audio id="a" src="v.wav" duration="2"/></assets><audioMix>'
                '<audioTrack id="t" asset="a" role="voiceover"/></audioMix></scene>')
        _, rest = lib.isolate(text)
        r = ET.fromstring(rest)
        null = [b for b in r.iter("bus") if b.get("mute") == "true"][0].get("id")
        self.assertEqual(next(r.iter("audioTrack")).get("bus"), null)

    def test_no_audio_mix_is_nothing_to_measure(self):
        self.assertEqual(lib.narration_tracks("<scene><assets/></scene>", Path("/p")), [])


class Margin(unittest.TestCase):
    def frames(self, level, seconds=10.0):
        return [lib.Frame(round(0.1 * (n + 1), 1), level, level) for n in range(int(seconds * 10))]

    def test_a_steady_margin_is_the_difference(self):
        rows = lib.span_margins(self.frames(-20), self.frames(-34), [("line", 1.0, 9.0)])
        self.assertAlmostEqual(rows[0]["margin"], 14.0, places=1)
        self.assertAlmostEqual(rows[0]["worst"], 14.0, places=1)

    def test_a_bed_that_swells_mid_line_shows_in_the_worst_window_only(self):
        rest = self.frames(-34)
        rest = [lib.Frame(f.t, -24.0 if 4.0 < f.t <= 7.5 else f.m, f.s) for f in rest]
        row = lib.span_margins(self.frames(-20), rest, [("line", 1.0, 9.5)])[0]
        self.assertAlmostEqual(row["worst"], 4.0, places=1)
        self.assertGreater(row["margin"], 4.0)

    def test_pauses_in_the_voice_do_not_count_against_the_line(self):
        voice = [lib.Frame(f.t, -20.0 if f.t < 3 or f.t > 5 else lib.FLOOR_LUFS, 0)
                 for f in self.frames(-20)]
        row = lib.span_margins(voice, self.frames(-32), [("line", 0.5, 9.5)])[0]
        self.assertAlmostEqual(row["margin"], 12.0, places=1)

    def test_a_silent_rest_is_capped_not_infinite(self):
        row = lib.span_margins(self.frames(-20), self.frames(lib.FLOOR_LUFS), [("l", 1, 9)])[0]
        self.assertTrue(math.isfinite(row["margin"]))

    def test_findings_name_the_line_and_use_the_floor(self):
        rows = [{"label": "vo1", "start": 1.0, "end": 5.0, "margin": 10.9, "worst": 10.9},
                {"label": "vo2", "start": 8.0, "end": 9.0, "margin": 15.0, "worst": 9.0}]
        out = lib.margin_findings(rows, floor=lib.MARGIN_FLOOR_DB)
        self.assertEqual(lib.MARGIN_FLOOR_DB, 11.0)
        self.assertEqual(len(out), 2)
        self.assertIn("vo1", out[0])
        self.assertIn("worst 3 s", out[1])
        self.assertEqual(lib.margin_findings(rows[:1], floor=10.0), [])


class AudioCheck(unittest.TestCase):
    def test_a_true_peak_over_the_ceiling_is_a_finding(self):
        self.assertEqual(lib.TRUE_PEAK_CEILING_DB, -1.0)
        out = lib.audio_findings(true_peak=-0.4, dc=[0.0], silences=[], duration=10.0, spans=[],
                                 short_term=[])
        self.assertEqual(len(out), 1)
        self.assertIn("-0.4", out[0])
        self.assertEqual(lib.audio_findings(true_peak=-1.0, dc=[0.0], silences=[], duration=10.0,
                                            spans=[], short_term=[]), [])

    def test_dc_offset(self):
        out = lib.audio_findings(true_peak=-3, dc=[0.0, -0.031], silences=[], duration=10.0,
                                 spans=[], short_term=[])
        self.assertEqual(len(out), 1)
        self.assertIn("DC offset", out[0])

    def test_silence_inside_the_programme_but_not_at_head_or_tail(self):
        sil = [(0.0, 3.0), (4.0, 7.0), (9.0, 10.0)]
        out = lib.audio_findings(true_peak=-3, dc=[0.0], silences=sil, duration=10.0, spans=[],
                                 short_term=[])
        self.assertEqual(len(out), 1)
        self.assertIn("4.00", out[0])

    def test_a_short_gap_is_not_a_finding(self):
        out = lib.audio_findings(true_peak=-3, dc=[0.0], silences=[(4.0, 5.0)], duration=10.0,
                                 spans=[], short_term=[])
        self.assertEqual(out, [])

    def test_loudness_swing_is_measured_inside_narration_only(self):
        frames = [lib.Frame(round(0.1 * (n + 1), 1), -50.0 if n < 100 else -22.0,
                            -50.0 if n < 100 else -22.0) for n in range(200)]
        swing = lib.spread_in_spans(frames, [(11.0, 19.0)])
        self.assertLess(swing, 1.0)
        wide = [lib.Frame(f.t, -40.0 if f.t < 15 else -18.0, 0) for f in frames]
        wide = [lib.Frame(f.t, -40.0 if f.t < 15 else -18.0, -40.0 if f.t < 15 else -18.0)
                for f in frames]
        self.assertGreater(lib.spread_in_spans(wide, [(1.0, 19.0)]), 15.0)

    def test_a_swing_over_the_limit_is_a_finding(self):
        frames = [lib.Frame(round(0.1 * (n + 1), 1), 0, -40.0 if n < 100 else -18.0)
                  for n in range(200)]
        out = lib.audio_findings(true_peak=-3, dc=[0.0], silences=[], duration=20.0,
                                 spans=[(1.0, 19.0)], short_term=frames)
        self.assertEqual(len(out), 1)
        self.assertIn("loudness", out[0])


class Sibilance(unittest.TestCase):
    def test_a_burst_over_the_takes_level_is_flagged_when_sustained(self):
        band = [-30.0] * 40 + [-8.0] * 4 + [-30.0] * 40
        rows = [(0.05 * i, v) for i, v in enumerate(band)]
        out = lib.sibilant_bursts(rows, overall_db=-15.0, limit_db=lib.SIBILANCE_LIMIT_DB)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0][0], 2.0, places=2)
        self.assertGreater(out[0][2], 5.0)

    def test_a_single_window_is_a_click_not_sibilance(self):
        band = [-30.0] * 40 + [-8.0] + [-30.0] * 40
        rows = [(0.05 * i, v) for i, v in enumerate(band)]
        self.assertEqual(lib.sibilant_bursts(rows, overall_db=-15.0, limit_db=0.0), [])

    def test_silence_in_the_band_never_flags(self):
        rows = [(0.05 * i, lib.FLOOR_DB) for i in range(40)]
        self.assertEqual(lib.sibilant_bursts(rows, overall_db=-15.0, limit_db=0.0), [])

    def test_takes_are_the_narration_of_the_projects_scene(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "scene.xml").write_text(SCENE, encoding="utf-8")
            takes = lib.project_takes(Path(d))
            self.assertEqual([t.name for t in takes], ["one.wav", "two.wav"])

    def test_without_a_scene_the_voice_directory_is_the_takes(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "voice").mkdir()
            for n in ("b.wav", "a.wav", "notes.txt"):
                (Path(d) / "voice" / n).write_bytes(b"")
            self.assertEqual([t.name for t in lib.project_takes(Path(d))], ["a.wav", "b.wav"])


@unittest.skipUnless(FFMPEG, "ffmpeg is not installed here; the controller runs the real tools on a render host")
class RealFfmpeg(unittest.TestCase):
    """The measurements against ffmpeg's own filters, on files written here."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_a_tone_of_known_level_has_the_loudness_it_should(self):
        # BS.1770: L = -0.691 + 10 log10(K-weighted mean square). A sine of
        # amplitude A has mean square A^2 / 2 (-3.01 dB), and the K filter
        # adds about +0.69 dB at 1 kHz, cancelling the -0.691 offset: a 1 kHz
        # sine at -20 dBFS peak reads about -23.0 LUFS (as ffmpeg measures).
        wav = write_wav(self.dir / "t.wav", tone(5, 1000, -20))
        frames = lib.momentary(wav)
        self.assertGreater(len(frames), 30)
        expected = -20 - 3.01
        self.assertAlmostEqual(frames[-1].m, expected, delta=0.7)

    def test_audiocheck_flags_a_clipped_file_and_passes_a_clean_one(self):
        clean = write_wav(self.dir / "clean.wav", noise(4, -24))
        clipped = write_wav(self.dir / "clip.wav", [max(-1, min(1, x * 30)) for x in noise(4, -20)])
        self.assertEqual(lib.audiocheck_file(clean, spans=[]), [])
        found = lib.audiocheck_file(clipped, spans=[])
        self.assertTrue(any("true peak" in f for f in found), found)

    def test_audiocheck_finds_a_silent_gap(self):
        samples = noise(3, -24) + [0.0] * (RATE * 3) + noise(3, -24, seed=2)
        found = lib.audiocheck_file(write_wav(self.dir / "gap.wav", samples), spans=[])
        self.assertTrue(any("silence" in f for f in found), found)

    def test_sibilance_flags_a_hissy_take_and_not_a_dull_one(self):
        rng = random.Random(3)
        # "speech": a low voiced tone; the hissy one adds 0.4 s bursts of broadband noise
        voice = tone(4, 200, -22)
        dull = write_wav(self.dir / "dull.wav", voice)
        hiss = list(voice)
        for start in (1.0, 2.5):
            for n in range(int(0.4 * RATE)):
                hiss[int(start * RATE) + n] += 0.5 * rng.uniform(-1, 1)
        hissy = write_wav(self.dir / "hissy.wav", hiss)
        self.assertEqual(lib.sibilance_findings(dull), [])
        self.assertTrue(lib.sibilance_findings(hissy))


class Runner(unittest.TestCase):
    def test_ffmpeg_is_the_one_in_sr_ffmpeg_or_on_the_path(self):
        with mock.patch.dict(os.environ, {"SR_FFMPEG": "/opt/ff"}):
            self.assertEqual(lib.ffmpeg_bin(), "/opt/ff")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(lib.ffmpeg_bin(), "ffmpeg")

    def test_the_encode_call_writes_an_audio_only_mix(self):
        seen = []

        def fake(cmd, **kw):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch.object(subprocess, "run", fake):
            lib.render_mix("/bin/engine", Path("/t/copy.xml"), Path("/t/out.wav"))
        self.assertEqual(seen[0][:3], ["/bin/engine", "encode", "/t/copy.xml"])
        self.assertIn("-o", seen[0])
        self.assertEqual(seen[0][seen[0].index("-o") + 1], "/t/out.wav")

    def test_a_failed_engine_is_a_tool_error_with_its_words(self):
        def fake(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, "", "boom: no such bus")

        with mock.patch.object(subprocess, "run", fake):
            with self.assertRaises(lib.ToolError) as ctx:
                lib.render_mix("/bin/engine", Path("/t/c.xml"), Path("/t/o.wav"))
        self.assertIn("no such bus", str(ctx.exception))

    def test_the_isolated_scenes_live_beside_symlinks_never_in_the_project(self):
        with tempfile.TemporaryDirectory() as d:
            project = Path(d) / "p"
            (project / "voice").mkdir(parents=True)
            (project / "scene.xml").write_text(SCENE, encoding="utf-8")
            before = sorted(p.name for p in project.rglob("*"))
            with lib.isolated_copies(project / "scene.xml") as (voice, rest):
                self.assertTrue(voice.is_file() and rest.is_file())
                self.assertNotEqual(voice.parent, project)
                self.assertTrue((voice.parent / "voice").exists())
            self.assertEqual(sorted(p.name for p in project.rglob("*")), before)


class Entrypoints(unittest.TestCase):
    def test_each_tool_is_a_python3_script(self):
        for name in gate.AUDIO_TOOLS:
            path = TOOLS / name
            with self.subTest(tool=name):
                self.assertTrue(path.is_file())
                self.assertEqual(path.read_text(encoding="utf-8").splitlines()[0],
                                 "#!/usr/bin/env python3")

    def test_the_gate_makes_the_tools_executable_whatever_the_checkout_did_to_the_mode(self):
        # production_gate.py runs <tools-dir>/<name> directly; a copy through a
        # zip or a filesystem without mode bits must not stop it
        with tempfile.TemporaryDirectory() as d:
            for name in gate.AUDIO_TOOLS:
                shutil.copy(TOOLS / name, Path(d) / name)
                (Path(d) / name).chmod(0o644)
            gate.ensure_executable(Path(d))
            for name in gate.AUDIO_TOOLS:
                self.assertTrue((Path(d) / name).stat().st_mode & stat.S_IXUSR, name)

    def test_without_the_engine_variable_margin_says_so_and_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "scene.xml").write_text(SCENE, encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k != "SR"}
            proc = subprocess.run([sys.executable, str(TOOLS / "sr-margin"), d,
                                   "--scene", str(Path(d) / "scene.xml")],
                                  capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("SR", proc.stderr)

    def test_a_wordless_scene_has_nothing_to_measure_and_passes(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "scene.xml").write_text("<scene><assets/></scene>", encoding="utf-8")
            env = dict(os.environ, SR="/nonexistent")
            proc = subprocess.run([sys.executable, str(TOOLS / "sr-margin"), d,
                                   "--scene", str(Path(d) / "scene.xml")],
                                  capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("no narration", proc.stdout)


class Shipping(unittest.TestCase):
    def tools_dir(self, names=()):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        for n in names:
            (Path(d.name) / n).write_text("#!/bin/sh\n")
        return Path(d.name)

    def test_the_bundled_dir_has_all_three(self):
        self.assertEqual(gate.AUDIO_TOOLS, ("sr-audiocheck", "sr-margin", "sr-sibilance"))
        self.assertEqual(sorted(gate.audio_tool_paths(None)), sorted(gate.AUDIO_TOOLS))
        for path in gate.audio_tool_paths(None).values():
            self.assertTrue(path)

    def test_a_tools_dir_that_has_them_wins_and_nothing_extra_is_shipped(self):
        d = self.tools_dir(gate.AUDIO_TOOLS)
        self.assertIsNone(gate.audio_tools_to_ship(d))
        self.assertEqual(gate.audio_tool_paths(d)["sr-margin"], str(d / "sr-margin"))

    def test_a_tools_dir_that_lacks_one_gets_the_bundle(self):
        d = self.tools_dir(("sr-margin", "sr-audiocheck"))
        self.assertEqual(gate.audio_tools_to_ship(d), gate.bundled_tools())

    def test_remote_plan_ships_the_bundle_after_the_tools_dir_into_the_same_place(self):
        found = {"scene": "/w/m/scene.xml", "video": "/w/m/video.mp4",
                 "encode report": "/w/m/render.json", "captions": "/w/m/n.vtt",
                 "engine": "/e/scene-render", "gate": "/g/production_gate.py",
                 "tools dir": "/src/pnkit/tools", "voice end": 3.0,
                 "audio tools": str(gate.bundled_tools())}
        plan = gate.plan_remote(found, [], Path("/w/m"), "root@h", tag="t")
        dests = [a[-1] for a in plan.syncs]
        sources = [a[-2] for a in plan.syncs]
        audio = f"root@h:{plan.root}/tools/audio/"
        self.assertEqual(dests.count(audio), 2)
        self.assertLess(sources.index("/src/pnkit/tools/"),
                        sources.index(str(gate.bundled_tools()) + "/"))
        for argv in plan.syncs:
            self.assertNotIn("--delete", argv)

    def test_local_command_points_at_the_bundle_for_the_tail_check_too(self):
        d = self.tools_dir(("tail_check.py",))
        found = {"scene": "/s.xml", "video": "/v.mp4", "encode report": "/r.json",
                 "captions": "/c.vtt", "engine": "/e", "gate": "/g.py", "tools dir": str(d),
                 "voice end": 1.0, "project": Path("/p"), "audio tools": str(gate.bundled_tools())}
        cmd = gate.local_command(found, [], frames=False, loudness_target=-14.0, max_illegal=None)
        self.assertEqual(cmd[cmd.index("--tools-dir") + 1], str(gate.bundled_tools()))
        # the pnkit one needs numpy, which the hosts lack: the bundled port is used
        self.assertEqual(cmd[cmd.index("--tail-check") + 1],
                         str(gate.bundled_tools() / "tail_check.py"))

    def test_preflight_inputs_name_each_missing_tool(self):
        with mock.patch.object(gate, "bundled_tools", return_value=Path("/nonexistent")):
            with mock.patch.object(gate, "tools_dir", return_value=self.tools_dir(("sr-margin",))):
                found, missing = gate.inputs(_project(), _args())
        self.assertIn("sr-audiocheck", missing)
        self.assertIn("sr-sibilance", missing)
        self.assertNotIn("sr-margin", missing)
        self.assertEqual(found["sr-audiocheck"], "")

    def test_the_bundled_tools_satisfy_a_tools_dir_without_them(self):
        with mock.patch.object(gate, "tools_dir", return_value=self.tools_dir(("tail_check.py",))):
            found, missing = gate.inputs(_project(), _args())
        for name in gate.AUDIO_TOOLS:
            self.assertNotIn(name, missing)
        self.assertEqual(found["audio tools"], str(gate.bundled_tools()))

    def test_doctor_names_each_missing_tool(self):
        with mock.patch.object(gate, "bundled_tools", return_value=Path("/nonexistent")):
            with mock.patch.object(gate, "tools_dir", return_value=None):
                report = doctor.check_environment(Path(tempfile.gettempdir()))
        rows = [r for r in report.findings if r.area == "audio tools"]
        text = " ".join(f"{r.message} {r.fix}" for r in rows)
        self.assertTrue(rows, text)
        for name in gate.AUDIO_TOOLS:
            self.assertIn(name, text)

    def test_doctor_is_quiet_about_tools_that_are_there(self):
        report = doctor.check_environment(Path(tempfile.gettempdir()))
        rows = [r for r in report.findings if r.area == "audio tools"]
        self.assertEqual([r.level for r in rows], ["ok"])


def _project():
    root = Path(tempfile.mkdtemp())
    return gate.workspace.Project(root)


def _args():
    return argparse.Namespace(captions=[], voice=None, voice_end=None, video=None,
                              scene=None, render=None, tools_dir=None, build=None)


if __name__ == "__main__":
    unittest.main()
