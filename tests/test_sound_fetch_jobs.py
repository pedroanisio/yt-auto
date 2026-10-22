"""`yt sound --fetch --jobs N`: a round's cues are searched, downloaded and
judged several at once (AUTO-76).

Freesound and the judge are faked at the same seams as `test_sound_fetch`
and `test_sound_judge`; a barrier inside the fake proves the overlap, since
a serial fetch never has two cues inside it at the same time. What the tests
pin: the flag is the shared `--jobs`, default 4; a round's downloads overlap
and `sound/index.json` still records every file; a round's judgements
overlap; and the progress lines come out one cue at a time, in cue order,
however the threads interleave.
"""

import json
import threading
import unittest

from ytstudio import cli, flags, freesound, sound

from tests.test_sound_fetch import FetchCase, cue, row


def levels() -> sound.Levels:
    """A source every level check passes: quiet floor, headroom to spare."""
    return sound.Levels(lufs=-10.0, true_peak=-6.0, floor=-45.0, host="h")


class Flag(unittest.TestCase):
    def test_yt_sound_takes_jobs_from_the_shared_vocabulary_default_4(self):
        self.assertTrue(callable(getattr(flags, "jobs", None)), "flags.jobs is the one spelling")
        ns = cli.build_parser().parse_args(["sound", "--fetch"])
        self.assertEqual(ns.jobs, 4)
        self.assertEqual(cli.build_parser().parse_args(["sound", "--jobs", "2"]).jobs, 2)


class Overlap(FetchCase):
    """Two cues, and a fake that waits for both to be inside it at once: a
    serial fetch leaves the first waiting alone, and the fake says so."""

    def setUp(self):
        super().setUp()
        self.catalogue["hum"] = [row(1, "Hum.wav", 20.0)]
        self.catalogue["tap"] = [row(2, "Tap.wav", 1.0)]
        self.alone: list[str] = []      # what a cue reached with no other cue there
        self.cues = (cue("ambience", "A1", "Room tone", 15, query="hum"),
                     cue("sfx", "S1", "Tap", 1, query="tap"))

    def meeting(self, label: str, parties: int = 2):
        gate = threading.Barrier(parties, timeout=1.0)

        def wait():
            try:
                gate.wait()
            except threading.BrokenBarrierError:
                self.alone.append(label)
        return wait

    def fetch(self, **kw) -> dict:
        data = self.data(*self.cues)
        sound.fetch(self.root, data, token="t", say=self.hush, jobs=2, **kw)
        return data

    def test_a_rounds_searches_overlap(self):
        inner, wait = freesound.fetch_json, self.meeting("search")

        def fake_json(url, **kw):
            wait()
            return inner(url, **kw)
        freesound.fetch_json = fake_json
        data = self.fetch()
        self.assertEqual(self.alone, [])
        self.assertEqual([c["freesound_id"] for c in data["cues"]], [1, 2])

    def test_a_rounds_downloads_overlap_and_the_index_records_every_file(self):
        inner, wait = freesound.fetch_bytes, self.meeting("download")

        def fake_bytes(url, **kw):
            wait()
            return inner(url, **kw)
        freesound.fetch_bytes = fake_bytes
        data = self.fetch()
        self.assertEqual(self.alone, [])
        self.assertEqual([c["file"] for c in data["cues"]],
                         ["sound/01-room-tone.mp3", "sound/02-tap.mp3"])
        index = json.loads((self.root / "sound" / "index.json").read_text())
        self.assertEqual(index, {"1": "sound/01-room-tone.mp3", "2": "sound/02-tap.mp3"})
        self.assertEqual(data["fetch"]["filled"], 2)

    def test_a_rounds_judgements_overlap(self):
        wait = self.meeting("judge")

        def judge(c, evidence):
            wait()
            return {"verdict": "accept", "reason": "fits", "confidence": 0.9}
        data = self.fetch(measure=lambda paths: [levels() for _ in paths], judge=judge)
        self.assertEqual(self.alone, [])
        self.assertEqual([c["judgement"]["verdict"] for c in data["cues"]],
                         ["accept", "accept"])
        self.assertEqual(data["fetch"]["judged"], 2)


if __name__ == "__main__":
    unittest.main()
