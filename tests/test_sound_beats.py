"""Music beat grids, measured not asked for (AUTO-140).

The render host prints an RMS envelope of each music and stinger file in the
same ssh as its levels; `sound.beat_grid` turns it into a tempo, a phase and
a downbeat in stdlib Python, or None for a bed with no steady beat. Nothing
here reaches a host, ffmpeg or a model: the envelopes are synthetic and the
ssh is a canned reply.
"""

import os
import random
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from tests.hermetic import fake_remote
from tests.test_sound_fetch import FetchCase, cue, row
from tests.test_sound_levels import EBUR, lv
from tests.test_sound_synth import SynthCase
from ytstudio import render, sound

os.environ.setdefault("YT_NO_REMOTE", "1")

HOP = sound.BEAT_HOP_S


def clicks(bpm=120.0, first=0.12, seconds=20.0, accent=1, floor=-60.0):
    """An RMS envelope (dB per hop) of a click every beat, every 4th louder,
    the first loud one at beat `accent`."""
    frames = int(seconds / HOP)
    env = [floor] * frames
    period = 60.0 / bpm / HOP
    k = 0
    while True:
        f = int(round(first / HOP + k * period))
        if f >= frames:
            break
        peak = 0.0 if k % 4 == accent else -10.0
        for d, drop in enumerate((0.0, 15.0, 30.0)):
            if f + d < frames:
                env[f + d] = max(env[f + d], peak - drop)
        k += 1
    return env


class Grid(unittest.TestCase):
    def test_a_120_bpm_click_with_an_accent_every_fourth_beat(self):
        grid = sound.beat_grid(clicks(), HOP)
        self.assertIsNotNone(grid)
        self.assertAlmostEqual(grid["bpm"], 120.0, delta=1.0)
        self.assertAlmostEqual(grid["period"], 0.5, delta=0.005)
        self.assertAlmostEqual(grid["first_beat"], 0.12, delta=0.011)
        self.assertAlmostEqual(grid["downbeat_first"], 0.62, delta=0.011)
        self.assertGreaterEqual(grid["confidence"], sound.BEAT_CONFIDENCE)
        self.assertEqual(set(grid), {"bpm", "period", "first_beat", "downbeat_first",
                                     "confidence"})

    def test_another_tempo_and_phase(self):
        grid = sound.beat_grid(clicks(bpm=96.0, first=0.3, accent=2), HOP)
        self.assertAlmostEqual(grid["bpm"], 96.0, delta=1.0)
        self.assertAlmostEqual(grid["first_beat"], 0.3, delta=0.011)
        self.assertAlmostEqual(grid["downbeat_first"], 0.3 + 2 * 0.625, delta=0.02)

    def test_flat_or_noise_has_no_steady_beat(self):
        self.assertIsNone(sound.beat_grid([-30.0] * 2000, HOP))
        rnd = random.Random(1)
        self.assertIsNone(sound.beat_grid([rnd.uniform(-40, -20) for _ in range(2000)], HOP))
        self.assertIsNone(sound.beat_grid([], HOP))
        self.assertIsNone(sound.beat_grid(clicks(seconds=1.0), HOP))

    def test_the_constants_are_the_cards_starting_values(self):
        self.assertEqual(sound.BEAT_BPM, (60, 180))
        self.assertEqual(sound.BEAT_KINDS, ("music", "stinger"))


class Script(unittest.TestCase):
    def test_only_the_files_asked_for_get_a_beat_block(self):
        script = sound.measure_script("/r/x", ["0.mp3", "1.mp3"], "/ff", beat={0})
        self.assertIn('echo "@@BEAT 0"', script)
        self.assertIn('echo "@@BEATEND 0"', script)
        self.assertNotIn("@@BEAT 1", script)
        self.assertIn("astats", script)
        self.assertIn("RMS_level", script)

    def test_an_sfx_only_batch_has_no_beat_block(self):
        script = sound.measure_script("/r/x", ["0.mp3", "1.mp3"], "/ff")
        self.assertNotIn("@@BEAT", script)
        self.assertEqual(script, sound.measure_script("/r/x", ["0.mp3", "1.mp3"], "/ff",
                                                      beat=frozenset()))


class Remote(unittest.TestCase):
    def setUp(self):
        fake_remote(self, RS_RENDER_HOST="gpu")
        render._HOSTS.clear()
        render._HOSTS["gpu"] = render.Host(addr="gpu", engine="/e/scene-render",
                                           ffprobe="/opt/af/bin/ffprobe")
        patch = mock.patch.object(render, "host_health", return_value=render.Health(alive=True))
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(render._HOSTS.clear)
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.paths = []
        for n in range(2):
            p = Path(self._tmp.name) / f"{n}.mp3"
            p.write_bytes(b"ID3" + bytes([n]))
            self.paths.append(p)

    def measure(self, out):
        done = subprocess.CompletedProcess([], 0, out.encode(), b"")
        with mock.patch("ytstudio.render.subprocess.run", return_value=done) as run:
            got = sound.measure_remote(self.paths, beat={0})
        return got, run.call_args.args[0][-1]

    def test_the_envelope_comes_back_in_the_same_ssh(self):
        out = (f"@@FILE 0\n{EBUR}\n@@END 0 0\n@@BEAT 0\n-20.5 -inf -18.25 \n@@BEATEND 0\n"
               f"@@FILE 1\n{EBUR}\n@@END 1 0\n")
        got, script = self.measure(out)
        self.assertIn("@@BEAT 0", script)
        self.assertNotIn("@@BEAT 1", script)
        self.assertEqual(got[0].envelope, [-20.5, sound.BEAT_FLOOR_DB, -18.25])
        self.assertEqual(got[1].envelope, [])
        self.assertEqual(got[0].lufs, -27.5)

    def test_a_missing_beat_block_still_yields_the_levels(self):
        out = f"@@FILE 0\n{EBUR}\n@@END 0 0\n@@FILE 1\n{EBUR}\n@@END 1 0\n"
        got, _ = self.measure(out)
        self.assertEqual(got[0].lufs, -27.5)
        self.assertEqual(got[0].envelope, [])


def beaten(**kw):
    level = lv(**kw)
    level.envelope = clicks()
    return level


class Fetched(FetchCase):
    def fetch(self, data, envelope=True):
        self.asked = []

        def measure(paths, beat=frozenset()):
            self.asked.append((sorted(Path(p).name for p in paths), sorted(beat)))
            return [beaten() if (n in beat and envelope) else lv()
                    for n, p in enumerate(paths)]
        sound.fetch(self.root, data, token="t", say=self.hush, measure=measure)
        return {c["cue_id"]: c for c in data["cues"]}

    def test_a_music_cue_gets_a_beat_grid_and_an_effect_none(self):
        self.catalogue["bed"] = [row(1, "Bed.wav", 20)]
        self.catalogue["hit"] = [row(2, "Hit.wav", 1)]
        cues = self.fetch(self.data(
            cue("music", "M1", "Bed", 20, query="bed", level="-30 dBFS"),
            cue("sfx", "S1", "Hit", 1, query="hit", level="-20 dBFS")))
        [(names, beat)] = self.asked
        self.assertEqual(len(names), 2)
        self.assertEqual(len(beat), 1)
        self.assertAlmostEqual(cues["M1"]["beat_grid"]["bpm"], 120.0, delta=1.0)
        self.assertNotIn("beat_grid", cues["S1"])
        self.assertIn("beat_grid", sound.FETCHED_FIELDS)
        self.assertNotIn("beat_grid", sound.HEARD_FIELDS)

    def test_a_refetch_clears_the_grid(self):
        self.catalogue["bed"] = [row(1, "Bed.wav", 20)]
        data = self.data(cue("music", "M1", "Bed", 20, query="bed", level="-30 dBFS"))
        self.fetch(data)
        self.assertIn("beat_grid", data["cues"][0])
        (self.root / data["cues"][0]["file"]).unlink()
        cues = self.fetch(data, envelope=False)
        self.assertNotIn("beat_grid", cues["M1"])

    def test_a_meter_that_knows_nothing_of_beats_is_still_called(self):
        self.catalogue["bed"] = [row(1, "Bed.wav", 20)]
        data = self.data(cue("music", "M1", "Bed", 20, query="bed", level="-30 dBFS"))
        sound.fetch(self.root, data, token="t", say=self.hush,
                    measure=lambda paths: [lv() for _ in paths])
        self.assertIn("file", data["cues"][0])
        self.assertNotIn("beat_grid", data["cues"][0])


class Synthesised(SynthCase):
    def test_a_synthesised_music_cue_gets_a_beat_grid(self):
        self.data["cues"][2]["kind"] = "music"
        author, _ = self.author_writing()
        asked = []

        def measure(paths, beat=frozenset()):
            asked.append(sorted(beat))
            return [beaten(lufs=-20, peak=-10, floor=-80) if n in beat
                    else lv(lufs=-20, peak=-10, floor=-80) for n, _ in enumerate(paths)]
        sound.realise(self.root, self.data, author=author, measure=measure,
                      say=lambda m: None)
        cues = {c["cue_id"]: c for c in self.data["cues"]}
        self.assertEqual(asked, [[1]])
        self.assertAlmostEqual(cues["C3"]["beat_grid"]["bpm"], 120.0, delta=1.0)
        self.assertNotIn("beat_grid", cues["C1"])


if __name__ == "__main__":
    unittest.main()
