"""The gate's tail check on the standard library and ffmpeg (AUTO-58).

The port is compared with pnkit's tools/tail_check.py on signals written here; those
comparisons skip, with the reason, where numpy, scipy or pnkit are absent. Tests that
decode or encode with ffmpeg skip without it. Nothing here needs a network or a host.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import math
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
import struct
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "ytstudio" / "audiotools"
PNKIT = Path.home() / "src" / "pnkit"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TOOLS))

import sr_audio as lib  # noqa: E402
from ytstudio import gate  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
SR = 24000
VOICE_END = 1.0


def original():
    """pnkit's tail_check module, or the reason it cannot be had."""
    path = PNKIT / "tools" / "tail_check.py"
    if not path.is_file():
        raise unittest.SkipTest(f"pnkit's tail_check.py is not at {path}")
    try:
        import numpy  # noqa: F401
        import scipy  # noqa: F401
    except ImportError as exc:
        raise unittest.SkipTest(f"numpy/scipy are absent: {exc}")
    sys.path.insert(0, str(PNKIT))
    try:
        spec = importlib.util.spec_from_file_location("pnkit_tail_check", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except ImportError as exc:
        raise unittest.SkipTest(f"pnkit's tail_check cannot be imported: {exc}")
    finally:
        sys.path.remove(str(PNKIT))
    return module


# --------------------------------------------------------------------------- #
# signals
# --------------------------------------------------------------------------- #

def voice(seconds: float = VOICE_END, seed: int = 3) -> list[float]:
    rng = random.Random(seed)
    return [0.1 * rng.uniform(-1, 1) for _ in range(int(seconds * SR))]


def sine(seconds: float, hz: float, amp: float, decay: float = 0.0) -> list[float]:
    return [amp * math.exp(-decay * n / SR) * math.sin(2 * math.pi * hz * n / SR)
            for n in range(int(seconds * SR))]


def sweep(seconds: float, f0: float, f1: float, amp: float, decay: float = 0.0) -> list[float]:
    """exponential chirp from f0 to f1"""
    k = math.log(f1 / f0) / seconds
    out = []
    for n in range(int(seconds * SR)):
        t = n / SR
        phase = 2 * math.pi * f0 * (math.exp(k * t) - 1) / k
        out.append(amp * math.exp(-decay * t) * math.sin(phase))
    return out


def room(seconds: float, db: float = -70.0, seed: int = 5) -> list[float]:
    rng = random.Random(seed)
    a = 10 ** (db / 20)
    return [a * rng.uniform(-1, 1) for _ in range(int(seconds * SR))]


def add(*parts: list[float]) -> list[float]:
    n = max(len(p) for p in parts)
    return [sum(p[i] for p in parts if i < len(p)) for i in range(n)]


def film(tail: list[float]) -> list[float]:
    return voice() + tail


HELD = film(add(sine(3.0, 1000, 0.3), room(3.0)))
GLIDE = film(add(sweep(3.0, 600, 2400, 0.3), room(3.0)))
DECAY = film(add([s * math.exp(-4.0 * n / SR) for n, s in enumerate(room(3.0, db=-12.0, seed=9))], room(3.0)))
KNOCK = film(add([0.0] * 2400, sine(3.0, 180, 0.4, decay=5.0), room(3.0, db=-90.0)))
LASER = film(add(sweep(1.0, 3000, 500, 0.3) * 3, room(3.0)))

SIGNALS = {"held": HELD, "glide": GLIDE, "decay": DECAY, "knock": KNOCK, "laser": LASER}


def write_wav(path: Path, samples: list[float]) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<h", max(-32768, min(32767, round(x * 32767))))
                               for x in samples))
    return path


def encode(src: Path, dst: Path) -> Path:
    subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-y", "-i", str(src), "-c:a", "aac",
                    "-b:a", "128k", str(dst)], check=True)
    return dst


# --------------------------------------------------------------------------- #
# the arithmetic
# --------------------------------------------------------------------------- #

class Fft(unittest.TestCase):
    def test_matches_the_definition(self):
        rng = random.Random(1)
        x = [complex(rng.uniform(-1, 1), rng.uniform(-1, 1)) for _ in range(16)]
        want = [sum(v * complex(math.cos(-2 * math.pi * k * n / 16), math.sin(-2 * math.pi * k * n / 16))
                    for n, v in enumerate(x)) for k in range(16)]
        got = lib.fft(x)
        for a, b in zip(got, want):
            self.assertAlmostEqual(abs(a - b), 0, places=9)

    def test_a_tone_lands_in_its_bin(self):
        n = 4096
        frame = [math.sin(2 * math.pi * 100 * i / n) for i in range(n)]
        power = lib.frame_powers([frame], [1.0] * n, 1.0)[0]
        self.assertEqual(max(range(len(power)), key=power.__getitem__), 100)
        self.assertEqual(len(power), n // 2 + 1)

    def test_two_real_frames_per_transform_equal_two_separate_ones(self):
        rng = random.Random(2)
        a = [rng.uniform(-1, 1) for _ in range(64)]
        b = [rng.uniform(-1, 1) for _ in range(64)]
        both = lib.frame_powers([a, b], [1.0] * 64, 1.0)
        one = [lib.frame_powers([f], [1.0] * 64, 1.0)[0] for f in (a, b)]
        for got, want in zip(both, one):
            for g, w in zip(got, want):
                self.assertAlmostEqual(g, w, places=9)


class Rules(unittest.TestCase):
    def measure(self, x):
        return lib.tail_measure(x, VOICE_END)

    def verdict(self, x):
        return lib.tail_report(self.measure(x), [], [], "t")[0]

    def test_a_held_partial_is_caught(self):
        m = self.measure(HELD)
        self.assertGreater(m["held"], lib.TAIL_HELD_MAX)
        self.assertFalse(self.verdict(HELD))

    def test_a_steady_glide_is_caught(self):
        m = self.measure(GLIDE)
        self.assertTrue(m["glide"])
        self.assertFalse(self.verdict(GLIDE))

    def test_a_laser_like_sweep_is_caught(self):
        self.assertFalse(self.verdict(LASER))

    def test_a_clean_decay_passes(self):
        self.assertTrue(self.verdict(DECAY), self.measure(DECAY))

    def test_a_soft_knock_passes(self):
        self.assertTrue(self.verdict(KNOCK), self.measure(KNOCK))

    def test_a_tail_that_does_not_fall_away_is_caught_by_the_decay_rule(self):
        steady = film(room(3.0, db=-30.0))
        m = self.measure(steady)
        self.assertFalse(m["last"] < m["before"] - lib.TAIL_DECAY_DB)

    def test_the_line_has_the_original_shape(self):
        ok, line = lib.tail_report(self.measure(KNOCK), None, [], "film.mp4")
        self.assertTrue(line.startswith("film.mp4 tail 3.0 s: cues after the last word not checked (no --scene); "
                                        "longest held pure partial "), line)
        self.assertTrue(line.endswith("-> PASS" if ok else "-> CHECK"))
        self.assertIn("energy above 1.5 kHz", line)
        self.assertIn("glide no", line)

    def test_a_tail_past_the_end_cannot_be_measured(self):
        with self.assertRaises(lib.ToolError):
            lib.tail_measure(voice(0.5), 5.0)


class AgainstPnkit(unittest.TestCase):
    """the same verdict, and figures within a small tolerance, as pnkit's tail_check"""

    @classmethod
    def setUpClass(cls):
        cls.orig = original()

    def compare(self, x, name):
        import numpy as np
        want = self.orig.measure(np.array(x, dtype=np.float32), VOICE_END)
        got = lib.tail_measure(x, VOICE_END)
        self.assertEqual(got["glide"], want["glide"], name)
        self.assertAlmostEqual(got["tail"], want["tail"], delta=0.01, msg=name)
        self.assertAlmostEqual(got["held"], want["held"], delta=0.05, msg=name)
        self.assertAlmostEqual(got["share"], want["share"], delta=0.03, msg=name)
        self.assertAlmostEqual(got["high"], want["high"], delta=0.02, msg=name)
        self.assertAlmostEqual(got["last"], want["last"], delta=0.5, msg=name)
        self.assertAlmostEqual(got["before"], want["before"], delta=0.5, msg=name)
        ok, _ = lib.tail_report(got, [], [], "t")
        want_ok, _ = self.orig.report(want, None, [], "t")
        self.assertEqual(ok, want_ok, f"{name}: {got} vs {want}")

    def test_each_synthetic_signal(self):
        for name, x in SIGNALS.items():
            with self.subTest(name):
                self.compare(x, name)

    def test_the_constants_are_the_originals(self):
        self.assertEqual((lib.TAIL_RATE, lib.TAIL_HELD_MAX, lib.TAIL_SHARE_MAX, lib.TAIL_DECAY_DB),
                         (self.orig.SR, self.orig.HELD_MAX, self.orig.SHARE_MAX, self.orig.DECAY_DB))

    @unittest.skipUnless(FFMPEG, "ffmpeg is not installed")
    def test_real_encoded_files_decode_and_measure_alike(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            for name, x in SIGNALS.items():
                with self.subTest(name):
                    wav = write_wav(Path(tmp) / f"{name}.wav", x)
                    media = encode(wav, Path(tmp) / f"{name}.m4a")
                    want = self.orig.measure(self.orig.load(str(media)), VOICE_END)
                    got = lib.tail_measure(lib.decode_pcm(media), VOICE_END)
                    self.assertEqual(got["glide"], want["glide"])
                    self.assertAlmostEqual(got["held"], want["held"], delta=0.05)
                    self.assertAlmostEqual(got["share"], want["share"], delta=0.03)
                    self.assertAlmostEqual(got["last"], want["last"], delta=0.5)
                    self.assertEqual(lib.tail_report(got, [], [], "t")[0],
                                     self.orig.report(want, None, [], "t")[0])


# --------------------------------------------------------------------------- #
# rule 1, from the scene
# --------------------------------------------------------------------------- #

def scene(*tracks: str) -> str:
    return ('<scene><assets><audio id="a-hit" src="fx/hit.wav" duration="1"/>'
            '<audio id="a-zap" src="fx/zap.wav" duration="3"/>'
            '<audio id="a-bed" src="m/bed.wav" duration="30"/></assets><audioMix>'
            + "".join(tracks) + "</audioMix></scene>")


HIT_TRACK = '<audioTrack id="cue-C1" asset="a-hit" start="{}" bus="effects" role="effects"/>'
ZAP_TRACK = '<audioTrack id="cue-C2" asset="a-zap" start="{}" bus="effects" role="effects"/>'
BED_TRACK = '<audioTrack id="cue-C3" asset="a-bed" start="0" bus="music" role="music"/>'


def thud(seconds=0.8):
    return sine(seconds, 160, 0.4, decay=12.0)


class StruckOnset(unittest.TestCase):
    def test_a_short_attack_that_decays_is_struck(self):
        self.assertEqual(lib.struck_onset(thud(), SR), (True, ""))

    def test_a_swell_is_not(self):
        swell = [s * min(1.0, n / (0.6 * SR)) for n, s in enumerate(sine(1.5, 300, 0.4))]
        ok, why = lib.struck_onset(swell, SR)
        self.assertFalse(ok)
        self.assertIn("attack", why)

    def test_leading_silence_is_not_attack(self):
        self.assertEqual(lib.struck_onset([0.0] * int(0.6 * SR) + thud(), SR), (True, ""))

    def test_a_swell_after_silence_is_still_slow(self):
        swell = [s * min(1.0, n / (0.6 * SR)) for n, s in enumerate(sine(1.5, 300, 0.4))]
        ok, why = lib.struck_onset([0.0] * int(0.6 * SR) + swell, SR)
        self.assertFalse(ok)
        self.assertIn("attack", why)

    def test_a_sustained_tone_is_not(self):
        ok, why = lib.struck_onset(sine(1.5, 300, 0.4), SR)
        self.assertFalse(ok)
        self.assertIn("decay", why)

    def test_a_fade_out_lets_a_cut_sound_decay(self):
        self.assertTrue(lib.struck_onset(sine(1.5, 300, 0.4), SR, fade_out=0.5)[0])

    def test_a_slow_fade_in_is_a_slow_attack(self):
        self.assertFalse(lib.struck_onset(thud(), SR, fade_in=0.5)[0])


class SceneEffects(unittest.TestCase):
    def check(self, xml, sounds, voice_end=10.0):
        """(late, bad) with the files replaced by `sounds` (asset file name -> samples)"""
        def decode(path, rate=lib.TAIL_RATE):
            return sounds[Path(path).name]
        with mock.patch.object(lib, "decode_pcm", decode):
            return lib.scene_tail_effects(xml, Path("/p"), voice_end)

    def sounds(self):
        return {"hit.wav": thud(), "zap.wav": sweep(3.0, 3000, 500, 0.4), "bed.wav": sine(30, 200, 0.1)}

    def test_only_effects_that_start_in_the_ending_are_judged(self):
        late, bad = self.check(scene(HIT_TRACK.format(2), HIT_TRACK.format(10.0), BED_TRACK), self.sounds())
        self.assertEqual([(t, n) for t, n in late], [(10.0, "cue-C1")])
        self.assertEqual(bad, [])

    def test_the_lead_before_the_last_word_counts(self):
        late, _ = self.check(scene(HIT_TRACK.format(9.6)), self.sounds())
        self.assertEqual(len(late), 1)
        late, _ = self.check(scene(HIT_TRACK.format(9.4)), self.sounds())
        self.assertEqual(late, [])

    def test_a_sweep_in_the_ending_is_refused_whatever_it_is_called(self):
        late, bad = self.check(scene(ZAP_TRACK.format(10.5)), self.sounds())
        self.assertEqual([n for _, n, *_ in bad], ["cue-C2"])
        self.assertEqual(len(late), 1)

    def test_a_looped_effect_is_refused(self):
        track = '<audioTrack id="cue-C1" asset="a-hit" start="10" loop="3" bus="effects" role="effects"/>'
        _, bad = self.check(scene(track), self.sounds())
        self.assertEqual(len(bad), 1)

    def test_a_muted_track_and_the_score_are_not_effects(self):
        muted = '<audioTrack id="cue-C1" asset="a-zap" start="10" mute="true" bus="effects" role="effects"/>'
        late, _ = self.check(scene(muted, BED_TRACK), self.sounds())
        self.assertEqual(late, [])

    def test_a_file_that_cannot_be_read_is_refused_not_passed(self):
        def decode(path, rate=lib.TAIL_RATE):
            raise lib.ToolError("no such file")
        with mock.patch.object(lib, "decode_pcm", decode):
            _, bad = lib.scene_tail_effects(scene(HIT_TRACK.format(10)), Path("/p"), 10.0)
        self.assertEqual(len(bad), 1)

    def test_the_clip_window_is_what_is_judged(self):
        # the sustained tone is cut by clipOut while still loud: it does not decay
        track = '<audioTrack id="cue-C1" asset="a-hit" start="10" clipOut="0.5" bus="effects" role="effects"/>'
        _, bad = self.check(scene(track), {"hit.wav": sine(2.0, 300, 0.4)})
        self.assertEqual(len(bad), 1)


# --------------------------------------------------------------------------- #
# the tool
# --------------------------------------------------------------------------- #

class Tool(unittest.TestCase):
    def run_tool(self, argv, samples, files=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(lib, "decode_pcm", lambda path, rate=lib.TAIL_RATE: samples), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = lib.tail_check_main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_exit_0_and_one_line_on_a_pass(self):
        code, out, _ = self.run_tool(["film.mp4", "--voice-end", "1.0"], KNOCK)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertTrue(out.startswith("film.mp4 tail "), out)

    def test_exit_1_on_a_check(self):
        code, out, _ = self.run_tool(["/x/film.mp4", "--voice-end", "1.0", "--label", "L"], HELD)
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("L tail "), out)
        self.assertTrue(out.strip().endswith("CHECK"))

    def test_out_is_written_and_voice_end_comes_from_timings(self):
        with tempfile.TemporaryDirectory() as tmp:
            timings = Path(tmp) / "t.json"
            timings.write_text('{"duration": 0.4}')
            target = Path(tmp) / "deep" / "tail.txt"
            code, out, _ = self.run_tool(["f.mp4", "--timings", str(timings), "--out", str(target)], KNOCK)
            self.assertEqual(target.read_text(), out)
            self.assertIn(code, (0, 1))

    def test_a_scene_effect_that_is_refused_makes_it_a_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            sc = Path(tmp) / "scene.xml"
            sc.write_text(scene(ZAP_TRACK.format(1.2)))
            sounds = {"zap.wav": sweep(3.0, 3000, 500, 0.4)}
            with mock.patch.object(lib, "decode_pcm", lambda path, rate=lib.TAIL_RATE:
                                   sounds.get(Path(path).name, KNOCK)):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = lib.tail_check_main(["f.mp4", "--voice-end", "1.0", "--scene", str(sc)])
            self.assertEqual(code, 1)
            self.assertIn("NOT ALLOWED", out.getvalue())
            self.assertIn("cue-C2@1.2", out.getvalue())

    def test_a_tool_error_is_exit_2(self):
        with mock.patch.object(lib, "decode_pcm", side_effect=lib.ToolError("ffmpeg failed")):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = lib.tail_check_main(["f.mp4", "--voice-end", "1.0"])
        self.assertEqual(code, 2)
        self.assertIn("tail_check", err.getvalue())

    def test_the_script_is_there_and_runs_without_numpy(self):
        script = TOOLS / "tail_check.py"
        self.assertTrue(script.is_file())
        text = script.read_text()
        self.assertNotIn("numpy", text)
        self.assertNotIn("scipy", text)

    @unittest.skipUnless(FFMPEG, "ffmpeg is not installed")
    def test_the_script_end_to_end_on_an_encoded_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = encode(write_wav(Path(tmp) / "k.wav", KNOCK), Path(tmp) / "k.m4a")
            proc = subprocess.run([sys.executable, "-I", str(TOOLS / "tail_check.py"), str(media),
                                   "--voice-end", "1.0"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("-> PASS", proc.stdout)


# --------------------------------------------------------------------------- #
# the gate ships it
# --------------------------------------------------------------------------- #

FOUND = {
    "scene": "/w/m/scene.xml", "video": "/w/m/video.mp4", "encode report": "/w/m/render.json",
    "captions": "/w/m/n.vtt", "engine": "/e/scene-render", "gate": "/g/production_gate.py",
    "tools dir": "/src/pnkit/tools", "voice end": 3.0, "project": Path("/w/m"),
}


class GateShipsIt(unittest.TestCase):
    def test_the_bundle_has_the_tool(self):
        self.assertTrue((gate.bundled_tools() / "tail_check.py").is_file())

    def test_local_command_passes_the_bundled_tool_even_when_the_tools_dir_has_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "tail_check.py").write_text("")
            cmd = gate.local_command(FOUND | {"tools dir": tmp}, [], frames=False,
                                     loudness_target=-14.0, max_illegal=None)
        self.assertEqual(cmd[cmd.index("--tail-check") + 1], str(gate.bundled_tools() / "tail_check.py"))

    def test_local_command_passes_it_when_the_tools_dir_has_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = gate.local_command(FOUND | {"tools dir": tmp}, [], frames=False,
                                     loudness_target=-14.0, max_illegal=None)
        self.assertEqual(cmd[cmd.index("--tail-check") + 1], str(gate.bundled_tools() / "tail_check.py"))

    def test_remote_plan_copies_the_tool_over_the_tools_dir_without_touching_the_others(self):
        plan = gate.plan_remote(FOUND, [], Path("/w/m"), "root@h", tag="t")
        self.assertIn("--tail-check", plan.command)
        self.assertEqual(plan.command[plan.command.index("--tail-check") + 1],
                         f"{plan.root}/tools/audio/tail_check.py")
        mine = [a for a in plan.syncs if a[-2] == str(gate.bundled_tools()) + "/"]
        self.assertEqual(len(mine), 1)
        argv = mine[0]
        self.assertEqual(argv[-1], f"root@h:{plan.root}/tools/audio/")
        self.assertIn("--include=tail_check.py", argv)
        self.assertIn("--include=sr_audio.py", argv)
        self.assertIn("--exclude=*", argv)
        self.assertNotIn("--delete", argv)
        self.assertLess(argv.index("--include=tail_check.py"), argv.index("--exclude=*"))

    def test_remote_plan_does_not_ship_the_bundle_twice(self):
        plan = gate.plan_remote(FOUND | {"audio tools": str(gate.bundled_tools())}, [], Path("/w/m"),
                                "root@h", tag="t")
        mine = [a for a in plan.syncs if a[-2] == str(gate.bundled_tools()) + "/"]
        self.assertEqual(len(mine), 1)


if __name__ == "__main__":
    unittest.main()
