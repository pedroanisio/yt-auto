"""`yt sound --fetch`: each fetched file is brought to its cue's design level.

Freesound and the level meter are faked: no network, no ssh, no ffmpeg. What
the tests pin: the measuring seam takes a whole batch (one ssh per batch, on
a render host, never here); beds are lifted by loudness and short effects by
peak, each by the cue's own kind; the lift is bounded by the track's gain cap
alone; a source whose noise floor would sit too near the narration after the
lift is refused with the floor and the gain named, and the next-ranked hit
for the same query is tried before the cue is left unfilled.
"""

import io
import os
import subprocess
import tarfile
import unittest
from pathlib import Path
from unittest import mock

from tests.hermetic import fake_remote
from tests.test_sound_fetch import FetchCase, cue, row
from tests.test_sound_synth import SynthCase
from ytstudio import build, render, sound
from ytstudio.sound import Levels

os.environ.setdefault("YT_NO_REMOTE", "1")      # the suite never reaches a render host


def lv(lufs=-30.0, peak=-10.0, floor=-45.0) -> Levels:
    return Levels(lufs=lufs, true_peak=peak, floor=floor)


def track_cap() -> float:
    return build.facets_of("audioTrack", "gain")[1]


class Seam(FetchCase):
    """fetch with a fake meter that records each batch it is asked for."""

    def run_fetch(self, measured, *cues, narration="", **kw):
        """`measured`: file name -> Levels (or None); a missing name is a quiet clean file."""
        self.batches: list[list[str]] = []

        def measure(paths):
            self.batches.append([Path(p).name for p in paths])
            return [measured.get(Path(p).name, lv()) if isinstance(measured, dict)
                    else measured for p in paths]
        data = self.data(*cues)
        if narration:
            data["palette"] = {"mix": {"narration": narration}}
        self.said: list[str] = []
        sound.fetch(self.root, data, token="t", say=self.said.append,
                    measure=kw.get("measure", measure))
        return data


class Lift(Seam):
    def test_a_bed_is_lifted_by_loudness(self):
        self.catalogue["hum"] = [row(1, "Hum.wav", 5)]
        data = self.run_fetch(lv(lufs=-20, peak=-20, floor=-22),
                              cue("ambience", "A1", "Hum", 5, query="hum",
                                  level="-10 LUFS"), narration="-14 LUFS")
        c = data["cues"][0]
        self.assertEqual(c["lift_by"], "loudness")
        self.assertEqual(c["gain_db"], 20.0)          # build adds level (-10): total +10
        self.assertEqual(c["source_lufs"], -20.0)

    def test_a_short_effect_is_lifted_by_peak(self):
        self.catalogue["hit"] = [row(1, "Hit.wav", 1)]
        data = self.run_fetch(lv(lufs=-40, peak=-22, floor=-80),
                              cue("sfx", "S1", "Hit", 1, query="hit",
                                  level="-20 dBFS"), narration="-14 LUFS")
        c = data["cues"][0]
        self.assertEqual(c["lift_by"], "peak")
        self.assertEqual(c["gain_db"], 22.0)
        self.assertEqual(c["source_true_peak_dbfs"], -22.0)

    def test_the_kind_picks_the_reference(self):
        self.assertEqual(sound.reference_of("music"), "loudness")
        self.assertEqual(sound.reference_of("ambience"), "loudness")
        for kind in ("sfx", "stinger", "transition"):
            self.assertEqual(sound.reference_of(kind), "peak")

    def test_the_reach_is_the_track_cap_alone(self):
        self.assertEqual(sound.gain_reach(), track_cap())
        self.assertLess(sound.gain_reach(), track_cap()
                        + build.facets_of("bus", "gain")[1])

    def test_a_lift_beyond_the_track_cap_is_refused_with_the_level(self):
        self.catalogue["x"] = [row(3, "X.wav", 5)]
        data = self.run_fetch(lv(peak=-30.0, floor=-90),
                              cue("sfx", "S1", "X", 5, query="x", level="+10 dBFS"),
                              narration="-14 LUFS")
        self.assertNotIn("file", data["cues"][0])
        reason = data["unfilled"][0]["reason"]
        self.assertIn("-30.0", reason)
        self.assertIn(f"{track_cap():+.0f}", reason)
        self.assertEqual(list((self.root / "sound").glob("*.mp3")), [])

    def test_the_net_gain_not_gain_db_alone_is_held_to_the_track(self):
        # Real run, Euler film: beds designed at -48 LUFS. The build applies
        # level + gain_db, so the track carries design - reference.
        for name, lufs, net in (("room-tone empty church", -51.7, 3.7),
                                ("room-tone quiet 01", -42.8, -5.2),
                                ("classroom_ambiance", -42.8, -5.2),
                                ("Indoor rain", -35.2, -12.8)):
            with self.subTest(name):
                self.assertGreater(-lufs, track_cap())      # gain_db alone is past the cap
                got, why = sound.level_to_design(
                    {"kind": "ambience",
                     "level": "About −48 LUFS-S, roughly 33 dB under the voice"},
                    lv(lufs=lufs, floor=lufs - 3), -16.0)
                self.assertEqual(why, "")
                self.assertEqual(got["gain_db"], round(-lufs, 2))
                self.assertAlmostEqual(-48.0 - lufs, net, places=1)

    def test_a_net_need_beyond_the_track_cap_is_refused(self):
        # Real "MUSCStr_Cello Bowed": -36.7 LUFS for a -9 design needs +27.7.
        got, why = sound.level_to_design(
            {"kind": "music", "level": "-9 LUFS"}, lv(lufs=-36.7, floor=-60), -16.0)
        self.assertIsNone(got)
        self.assertIn("+27.7", why)
        self.assertIn(f"{track_cap():+.0f}", why)

    def test_a_net_cut_beyond_the_track_minimum_is_refused(self):
        low = build.facets_of("audioTrack", "gain")[0]
        got, why = sound.level_to_design(
            {"kind": "music", "level": "-9 LUFS"}, lv(lufs=-9.0 - low + 3.0, floor=-90), -16.0)
        self.assertIsNone(got)
        self.assertIn(f"{low:+.0f}", why)

    def test_a_silent_source_is_refused(self):
        self.catalogue["x"] = [row(3, "X.wav", 5)]
        data = self.run_fetch(lv(lufs=float("-inf"), peak=float("-inf"),
                                 floor=float("-inf")),
                              cue("sfx", "S1", "X", 5, query="x"))
        self.assertIn("silent", data["unfilled"][0]["reason"])

    def test_a_cue_with_no_stated_level_is_measured_against_zero(self):
        self.catalogue["x"] = [row(3, "X.wav", 5)]
        data = self.run_fetch(lv(peak=-20.0, floor=-90),
                              cue("sfx", "S1", "X", 5, query="x"))
        self.assertEqual(data["cues"][0]["gain_db"], 20.0)


class Noise(Seam):
    def sfx(self, level="-4 dBFS"):
        return cue("sfx", "S1", "Hit", 1, query="hit", level=level)

    def test_a_noisy_source_is_refused_naming_floor_and_gain(self):
        self.catalogue["hit"] = [row(1, "Hit.wav", 1)]
        # peak -24 lifted to -4 is +20 dB: the floor at -44 becomes -24, above
        # the ceiling.
        data = self.run_fetch(lv(peak=-24, floor=-44), self.sfx(),
                              narration="-16 LUFS")
        self.assertNotIn("file", data["cues"][0])
        reason = data["unfilled"][0]["reason"]
        self.assertIn("-44.0", reason)          # the measured floor
        self.assertIn("+20.0", reason)          # the gain
        self.assertIn("noise", reason)

    def test_a_clean_source_with_the_same_gain_is_kept(self):
        self.catalogue["hit"] = [row(1, "Hit.wav", 1)]
        data = self.run_fetch(lv(peak=-24, floor=-90), self.sfx(),
                              narration="-16 LUFS")
        self.assertIn("file", data["cues"][0])
        self.assertEqual(data["cues"][0]["gain_db"], 24.0)

    def test_the_ceiling_is_the_narration_less_the_margin(self):
        self.assertEqual(sound.noise_ceiling(-16.0), -16.0 - sound.NOISE_BELOW_NARRATION_DB)
        self.catalogue["hit"] = [row(1, "Hit.wav", 1)]
        edge = sound.noise_ceiling(-16.0) - 20.0          # after a +20 dB lift: the ceiling
        ok = self.run_fetch(lv(peak=-24, floor=edge), self.sfx(), narration="-16 LUFS")
        self.assertIn("file", ok["cues"][0])
        over = self.run_fetch(lv(peak=-24, floor=edge + 0.1), self.sfx(),
                              narration="-16 LUFS")
        self.assertNotIn("file", over["cues"][0])

    def test_without_a_stated_narration_the_programme_target_is_used(self):
        self.assertEqual(sound.narration_level({}), (sound.TARGET_LUFS, "programme target"))
        self.assertEqual(sound.narration_level({"palette": {"mix": {"narration": "-18 LUFS"}}})[0],
                         -18.0)
        self.assertEqual(sound.narration_level({"palette": {"mix": {"narration": "warm"}}})[0],
                         sound.TARGET_LUFS)

    def test_a_bed_is_judged_against_its_own_design_level(self):
        # A steady bed is its own floor; the sheet owns where it sits.
        self.catalogue["hum"] = [row(1, "Hum.wav", 5)]
        data = self.run_fetch(lv(lufs=-22, peak=-30, floor=-23),
                              cue("ambience", "A1", "Hum", 5, query="hum",
                                  level="-20 LUFS"), narration="-16 LUFS")
        self.assertIn("file", data["cues"][0])


class NextRanked(Seam):
    def two_hits(self):
        self.catalogue["hit"] = [row(1, "Noisy.wav", 1), row(2, "Clean.wav", 1)]
        for r, rating in zip(self.catalogue["hit"], (5.0, 3.0)):
            r["avg_rating"], r["num_ratings"] = rating, 10

    def meter(self):
        # The first-ranked file is noisy, the second clean.
        def measure(paths):
            self.batches.append([Path(p).name for p in paths])
            return [lv(peak=-24, floor=-42) if "1-hq" in Path(p).read_bytes().decode("latin1")
                    else lv(peak=-24, floor=-90) for p in paths]
        return measure

    def test_a_refused_hit_gives_way_to_the_next_ranked(self):
        self.two_hits()
        self.batches = []
        data = self.run_fetch({}, cue("sfx", "S1", "Hit", 1, query="hit",
                                      level="-4 dBFS"), narration="-16 LUFS",
                              measure=self.meter())
        c = data["cues"][0]
        self.assertEqual(c["freesound_id"], 2)
        self.assertEqual(data["unfilled"], [])
        self.assertEqual(self.searches, ["hit"])          # no new query
        self.assertEqual(len(self.downloads), 2)
        self.assertEqual(len(list((self.root / "sound").glob("*.mp3"))), 1)
        self.assertEqual(data["fetch"]["refused"][0]["freesound_id"], 1)

    def test_every_hit_refused_leaves_the_cue_unfilled_within_a_bound(self):
        self.catalogue["hit"] = [row(i, f"N{i}.wav", 1) for i in range(1, 9)]
        data = self.run_fetch(lv(peak=-24, floor=-42),
                              cue("sfx", "S1", "Hit", 1, query="hit", level="-4 dBFS"),
                              narration="-16 LUFS")
        self.assertNotIn("file", data["cues"][0])
        self.assertEqual(len(self.downloads), sound.FETCH_TRIES)
        self.assertEqual(len(self.searches), 1)
        self.assertIn(f"{sound.FETCH_TRIES}", data["unfilled"][0]["reason"])
        self.assertEqual(list((self.root / "sound").glob("*.mp3")), [])

    def test_a_batch_is_one_call_to_the_meter(self):
        self.catalogue["a"] = [row(1, "A.wav", 1)]
        self.catalogue["b"] = [row(2, "B.wav", 1)]
        self.run_fetch(lv(),
                       cue("sfx", "S1", "A", 1, query="a", level="-20 dBFS"),
                       cue("sfx", "S2", "B", 1, query="b", level="-20 dBFS"))
        self.assertEqual(len(self.batches), 1)
        self.assertEqual(len(self.batches[0]), 2)


class Unmeasured(Seam):
    def test_no_meter_is_said_loudly_and_the_file_is_kept_unlevelled(self):
        self.catalogue["x"] = [row(3, "X.wav", 5)]

        def nowhere(paths):
            raise sound.MeasureError("no render host configured")
        data = self.run_fetch({}, cue("sfx", "S1", "X", 5, query="x"), measure=nowhere)
        c = data["cues"][0]
        self.assertIn("file", c)
        self.assertNotIn("gain_db", c)
        summary = data["fetch"]
        self.assertEqual(summary["unmeasured"], ["S1"])
        self.assertIn("no render host configured", summary["measure_error"])
        self.assertTrue(any("NOT LEVELLED" in m for m in self.said), self.said)

    def test_the_default_meter_runs_nothing_under_no_remote(self):
        self.catalogue["x"] = [row(3, "X.wav", 5)]
        self.assertTrue(os.environ.get("YT_NO_REMOTE"))
        with mock.patch("subprocess.run") as run:
            data = self.data(cue("sfx", "S1", "X", 5, query="x"))
            said: list[str] = []
            summary = sound.fetch(self.root, data, token="t", say=said.append)
        run.assert_not_called()
        self.assertEqual(summary["unmeasured"], ["S1"])
        self.assertIn("YT_NO_REMOTE", summary["measure_error"])
        self.assertTrue(any("NOT LEVELLED" in m for m in said))


EBUR = """\
[Parsed_ebur128_0 @ 0x1] t: 0.1  TARGET:-23 LUFS    M: -120.7 S: -120.7     I: -70.0 LUFS     LRA:   0.0 LU
[Parsed_ebur128_0 @ 0x1] t: 0.2  TARGET:-23 LUFS    M: -120.7 S: -120.7     I: -70.0 LUFS     LRA:   0.0 LU
[Parsed_ebur128_0 @ 0x1] t: 0.3  TARGET:-23 LUFS    M: -120.7 S: -120.7     I: -70.0 LUFS     LRA:   0.0 LU
""" + "".join(
    f"[Parsed_ebur128_0 @ 0x1] t: {0.5 + n / 10:.1f}  TARGET:-23 LUFS    "
    f"M: {m:.1f} S: -30.0     I: -30.0 LUFS     LRA:   0.0 LU\n"
    for n, m in enumerate([-60.0, -50.0] + [-20.0] * 18)) + """\
[Parsed_ebur128_0 @ 0x1] Summary:

  Integrated loudness:
    I:         -27.5 LUFS
    Threshold: -37.5 LUFS

  Loudness range:
    LRA:         3.0 LU

  True peak:
    Peak:       -6.5 dBFS
"""


class Parse(unittest.TestCase):
    def test_loudness_and_true_peak_come_from_the_summary(self):
        got = sound.parse_ebur128(EBUR)
        self.assertEqual(got.lufs, -27.5)
        self.assertEqual(got.true_peak, -6.5)

    def test_the_floor_is_the_quietest_tenth_of_the_400ms_windows(self):
        # 20 full windows after the first 0.4 s (the partial ones do not count):
        # the quietest 10% are the two at -60 and -50; the floor is the level
        # that tenth reaches, -50.
        got = sound.parse_ebur128(EBUR)
        self.assertEqual(got.floor, -50.0)

    def test_a_file_shorter_than_one_window_has_its_loudness_as_floor(self):
        lines = EBUR.splitlines()
        tail = lines[next(n for n, l in enumerate(lines) if "Summary:" in l):]
        got = sound.parse_ebur128("\n".join(lines[:3] + tail))
        self.assertEqual(got.floor, -27.5)

    def test_text_without_a_summary_is_not_a_measurement(self):
        self.assertIsNone(sound.parse_ebur128("Error opening input file"))


class Minus(unittest.TestCase):
    def test_the_typographic_minus_keeps_its_sign(self):
        for text, want in (
                ("About −48 LUFS-S, roughly 33 dB under the voice", -48.0),
                ("Peaks about −24 dBFS", -24.0),
                ("about −22 LUFS-S", -22.0),
                ("about -48 LUFS-S", -48.0),
                ("−36 to −30 dBFS", -36.0),
                ("−36–−30 dBFS", -36.0),
                ("–36 dBFS", -36.0),
                ("about −20 dB", -20.0)):
            with self.subTest(text):
                self.assertEqual(build.level_db(text), want)

    def test_a_range_dash_between_figures_is_not_a_sign(self):
        self.assertIsNone(build.level_db("6–9 dB below"))


class Floor(unittest.TestCase):
    def test_the_host_is_asked_for_the_frame_lines(self):
        # framelog=verbose prints frames at verbose; the default log level hides them.
        script = sound.measure_script("/tmp/x", ["0.wav"])
        self.assertTrue("-loglevel verbose" in script or "framelog=info" in script, script)

    def test_frame_lines_as_ffmpeg_prints_them_give_a_floor_under_the_loudness(self):
        lines = "".join(
            f"[Parsed_ebur128_0 @ 0x5581] t: {n / 10:.6g}      TARGET:-23 LUFS    "
            f"M:{m:.1f} S:-42.0     I: -42.8 LUFS       LRA:   0.0 LU  "
            "FTPK: -30.0 -30.0 dBFS  TPK: -30.0 -30.0 dBFS\n"
            for n, m in zip(range(1, 31), [-70.0] * 3 + [-50.0] * 3 + [-42.0] * 24))
        got = sound.parse_ebur128(lines + EBUR[EBUR.index("[Parsed_ebur128_0 @ 0x1] Summary:"):])
        self.assertEqual(got.floor, -50.0)
        self.assertNotEqual(got.floor, got.lufs)


class SynthLevels(SynthCase):
    """A synthesised file is a file in the mix: measured like a fetched one."""

    def realise(self, meter, **per_cue):
        author, _ = self.author_writing(**per_cue)
        self.batches: list[list[str]] = []

        def measure(paths):
            self.batches.append([Path(p).name for p in paths])
            return [meter(Path(p).name) for p in paths]
        return sound.realise(self.root, self.data, author=author, measure=measure,
                             say=lambda m: None)

    def test_a_synth_cue_is_measured_in_one_batch_and_levelled(self):
        summary = self.realise(lambda name: lv(lufs=-20, peak=-10, floor=-80))
        self.assertEqual(len(self.batches), 1)
        self.assertEqual(len(self.batches[0]), 2)
        cues = {c["cue_id"]: c for c in self.data["cues"]}
        self.assertEqual(cues["C1"]["lift_by"], "peak")
        self.assertEqual(cues["C1"]["gain_db"], 10.0)
        self.assertEqual(cues["C3"]["lift_by"], "loudness")
        self.assertEqual(cues["C3"]["gain_db"], 20.0)
        self.assertEqual(summary["levelled"], 2)

    def test_a_synth_that_misses_its_level_is_refused_like_a_fetched_file(self):
        self.realise(lambda name: lv(lufs=-60, peak=-40, floor=-90))
        cues = {c["cue_id"]: c for c in self.data["cues"]}
        for cid in ("C1", "C3"):
            self.assertNotIn("file", cues[cid])
        reasons = {u["cue_id"]: u["reason"] for u in self.data["unfilled"]}
        self.assertIn("-40.0", reasons["C1"])
        self.assertIn("-60.0", reasons["C3"])

    def test_a_synth_nobody_could_measure_is_kept_and_said(self):
        def nowhere(paths):
            raise sound.MeasureError("no render host configured")
        author, _ = self.author_writing()
        summary = sound.realise(self.root, self.data, author=author, measure=nowhere,
                                say=lambda m: None)
        cues = {c["cue_id"]: c for c in self.data["cues"]}
        self.assertIn("file", cues["C1"])
        self.assertNotIn("gain_db", cues["C1"])
        self.assertIn("no render host configured", summary["measure_error"])


def untar(blob: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers()}


class Remote(unittest.TestCase):
    def setUp(self):
        fake_remote(self, RS_RENDER_HOST="gpu")
        render._HOSTS.clear()
        render._HOSTS["gpu"] = render.Host(addr="gpu", engine="/e/scene-render",
                                           ffprobe="/opt/af/bin/ffprobe")
        patch = mock.patch.object(render, "ssh_alive", return_value=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(render._HOSTS.clear)
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.paths = []
        for n in range(3):
            p = Path(self._tmp.name) / f"{n}.mp3"
            p.write_bytes(b"ID3" + bytes([n]))
            self.paths.append(p)

    def reply(self, files):
        out = ""
        for n in range(files):
            out += f"@@FILE {n}\n{EBUR}\n@@END {n} 0\n"
        return subprocess.CompletedProcess([], 0, out.encode(), b"")

    def test_a_batch_is_one_ssh_with_every_file_on_stdin(self):
        with mock.patch("subprocess.run", return_value=self.reply(3)) as run:
            got = sound.measure_remote(self.paths)
        self.assertEqual(run.call_count, 1)
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "ssh")
        self.assertIn("gpu", argv)
        script = argv[-1]
        self.assertIn("ebur128", script)
        self.assertIn("/opt/af/bin/ffmpeg", script)        # beside the host's ffprobe
        self.assertIn(render.REMOTE_ROOT, script)          # a job-scoped scratch dir
        sent = untar(run.call_args.kwargs["input"])
        self.assertEqual(len(sent), 3)
        self.assertEqual(len(got), 3)
        self.assertEqual(got[0].lufs, -27.5)
        self.assertEqual(got[0].host, "gpu")

    def test_a_file_the_host_could_not_read_is_unmeasured_the_rest_are_not(self):
        out = (f"@@FILE 0\n{EBUR}\n@@END 0 0\n@@FILE 1\nInvalid data\n@@END 1 1\n"
               f"@@FILE 2\n{EBUR}\n@@END 2 0\n")
        done = subprocess.CompletedProcess([], 0, out.encode(), b"")
        with mock.patch("ytstudio.render.subprocess.run", return_value=done):
            got = sound.measure_remote(self.paths)
        self.assertIsNone(got[1])
        self.assertIsNotNone(got[0])
        self.assertIsNotNone(got[2])

    def test_no_ffmpeg_on_the_host_is_an_error_naming_it(self):
        done = subprocess.CompletedProcess([], 3, b"__NO_FFMPEG__\n", b"")
        with mock.patch("ytstudio.render.subprocess.run", return_value=done):
            with self.assertRaises(sound.MeasureError) as ctx:
                sound.measure_remote(self.paths)
        self.assertIn("ffmpeg", str(ctx.exception))
        self.assertIn("gpu", str(ctx.exception))

    def test_an_unreachable_host_is_an_error(self):
        with mock.patch("ytstudio.render.subprocess.run",
                        side_effect=subprocess.TimeoutExpired("ssh", 1)):
            with self.assertRaises(sound.MeasureError):
                sound.measure_remote(self.paths)

    def test_no_host_is_an_error_that_says_so(self):
        with mock.patch.dict(os.environ, {"RS_RENDER_HOST": "", "RS_RENDER_HOSTS": ""}), \
                mock.patch("ytstudio.render.subprocess.run") as run:
            with self.assertRaises(sound.MeasureError) as ctx:
                sound.measure_remote(self.paths)
        run.assert_not_called()
        self.assertIn("no render host", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
