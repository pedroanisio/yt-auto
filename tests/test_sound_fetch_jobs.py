"""`yt sound --fetch --jobs N`: a round's cues are searched, downloaded and
judged several at once (AUTO-76).

Freesound and the judge are faked at the same seams as `test_sound_fetch`
and `test_sound_judge`; a barrier inside the fake proves the overlap, since
a serial fetch never has two cues inside it at the same time. What the tests
pin: the flag is the shared `--jobs`, default 4, and the stage hands it to
the fetch; a round's searches and downloads overlap and `sound/index.json`
still records every file; two cues landing on one Freesound id download it
once and share the file; a round's judgements overlap and a hit's
spectrogram is written once; the progress lines, a failed search's line
among them, and the `unfilled` record come one cue at a time, in cue order,
however the threads interleave; and an interrupt drops the round's queued
jobs.
"""

import contextlib
import io
import json
import os
import signal
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from ytstudio import cli, flags, freesound, harness, sound, workspace
from ytstudio.core import Idea

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

    def test_the_stage_hands_jobs_to_the_fetch(self):
        args = cli.build_parser().parse_args(["sound", "--fetch", "--jobs", "2", "--quiet"])
        got = {}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(sound, "fetch", lambda root, data, **kw: got.update(kw)), \
                mock.patch.object(sound, "harness_judge", lambda root, **kw: None), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            data = {"runtime": 20, "cues": [cue("silence", "Q1", "Hold", 2)],
                    "coverage": {}, "unfilled": []}
            sound._finish(args, Idea(title="t", genre="g", description="d"),
                          workspace.Project(root=Path(tmp)), data)
        self.assertEqual(got.get("jobs"), 2)


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

    def meeting(self, label: str, parties: int = 2, timeout: float = 1.0):
        gate = threading.Barrier(parties, timeout=timeout)

        def wait():
            try:
                gate.wait()
            except threading.BrokenBarrierError:
                self.alone.append(label)
        return wait

    def fetch(self, **kw) -> dict:
        data = self.data(*self.cues)
        kw.setdefault("say", self.hush)
        sound.fetch(self.root, data, token="t", jobs=2, **kw)
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

    def test_the_unfilled_list_is_in_cue_order_whichever_search_ends_first(self):
        self.catalogue.clear()
        inner = freesound.fetch_json

        def fake_json(url, **kw):
            if "hum" in url:
                time.sleep(0.05)        # the first cue's search is the slower one
            return inner(url, **kw)
        freesound.fetch_json = fake_json
        data = self.fetch()
        self.assertEqual([m["cue_id"] for m in data["unfilled"]], ["A1", "S1"])
        self.assertEqual(data["fetch"]["unfilled"], 2)

    def test_two_cues_on_one_freesound_id_download_it_once(self):
        # Both searches land on id 7; the second cue finds the first's file
        # under the id's lock, so one download, one file, one index entry,
        # as the serial fetch gave. The barrier proves the two downloads
        # never overlapped: the first waits alone and times out.
        self.catalogue["hum"] = [row(7, "Hum.wav", 20.0)]
        self.cues = (cue("ambience", "A1", "Room tone", 15, query="hum"),
                     cue("ambience", "A2", "Hall tone", 15, query="hum"))
        inner, wait = freesound.fetch_bytes, self.meeting("download", timeout=0.2)

        def fake_bytes(url, **kw):
            wait()
            return inner(url, **kw)
        freesound.fetch_bytes = fake_bytes
        data = self.fetch()
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(self.alone, ["download"])
        self.assertEqual([c["file"] for c in data["cues"]],
                         ["sound/01-room-tone.mp3", "sound/01-room-tone.mp3"])
        self.assertEqual(sorted((self.root / "sound").glob("*.mp3")),
                         [self.root / "sound" / "01-room-tone.mp3"])
        index = json.loads((self.root / "sound" / "index.json").read_text())
        self.assertEqual(index, {"7": "sound/01-room-tone.mp3"})
        self.assertEqual(data["fetch"]["filled"], 2)
        self.assertEqual(data["fetch"]["cached"], 1)

    def test_two_cues_on_one_hit_write_its_spectrogram_once(self):
        # The image is shared by id; a second write would truncate it while
        # the other cue's judge may be reading it.
        self.catalogue["hum"] = [row(7, "Hum.wav", 20.0)]
        self.cues = (cue("ambience", "A1", "Room tone", 15, query="hum"),
                     cue("ambience", "A2", "Hall tone", 15, query="hum"))
        png, wait = b"\x89PNG spectrogram", self.meeting("judge")
        writes: list[Path] = []
        inner = Path.write_bytes

        def counted(path, blob):
            if path.name == "spectrogram.png":
                writes.append(path)
            return inner(path, blob)

        def judge(c, evidence):
            wait()
            self.assertEqual(evidence["spectrogram"].read_bytes(), png)
            return {"verdict": "accept", "reason": "fits", "confidence": 0.9}
        with mock.patch.object(Path, "write_bytes", counted):
            data = self.fetch(judge=judge, measure=lambda paths: [
                sound.Levels(lufs=-10.0, true_peak=-6.0, floor=-45.0, host="h",
                             spectrogram=png) for _ in paths])
        self.assertEqual(self.alone, [])
        self.assertEqual(len(writes), 1)
        self.assertEqual([c["judgement"]["verdict"] for c in data["cues"]],
                         ["accept", "accept"])

    def test_a_failed_search_is_told_with_the_cues_lines_not_printed_from_its_thread(self):
        # Freesound's own "search failed" line would interleave with the other
        # cues' progress from a pool thread; it joins the cue's lines instead.
        inner, lines = freesound.fetch_json, []

        def fake_json(url, **kw):
            if "hum" in url:
                time.sleep(0.05)        # the failing search is the slower one
                raise urllib.error.URLError("no route")
            return inner(url, **kw)
        freesound.fetch_json = fake_json
        with contextlib.redirect_stderr(io.StringIO()) as err:
            data = self.fetch(say=lines.append,
                              measure=lambda paths: [levels() for _ in paths])
        self.assertEqual(err.getvalue(), "")
        self.assertEqual([line.split()[0] for line in lines], ["freesound:", "A1", "S1"])
        self.assertIn("search failed at https://freesound.org/apiv2/search/text/ (URLError)",
                      lines[0])
        self.assertEqual([m["cue_id"] for m in data["unfilled"]], ["A1"])

    def interrupted(self, run, started: threading.Event):
        """`run()` with a SIGINT whose handler is slow to act, as CPython's
        can be when the signal lands while the main thread waits on a lock
        (it may see it only once the lock is released, AUTO-129): the
        handler waits for a second job to start before it raises, so a job
        the pool could still pick up after the interrupt always is, and one
        never handed to the pool times the wait out."""
        def late(signum, frame):
            started.wait(0.5)
            raise KeyboardInterrupt
        before = signal.signal(signal.SIGINT, late)
        try:
            with self.assertRaises(KeyboardInterrupt):
                run()
        finally:
            signal.signal(signal.SIGINT, before)

    @staticmethod
    def interrupt() -> None:
        """SIGINT to the main thread once it waits for a pool job to end.

        Not at once: the fakes are so quick that the first job can reach
        here while the main thread is still inside its first `submit`,
        starting the pool's thread, and an interrupt there lands before
        anything else was queued, so the old `pool.map` fetch passed by
        luck (AUTO-129). Its stack says where it is; the wait is a poll
        with a deadline, and what the test decides does not hang on its
        length."""
        main = threading.main_thread().ident
        base = os.path.join("concurrent", "futures", "_base.py")

        def waits() -> bool:
            frame = sys._current_frames().get(main)
            while frame is not None:
                code = frame.f_code
                if code.co_filename.endswith(base) and code.co_name in ("result", "wait"):
                    return True
                frame = frame.f_back
            return False
        deadline = time.monotonic() + 2.0
        while not waits() and time.monotonic() < deadline:
            time.sleep(0.001)
        signal.pthread_kill(main, signal.SIGINT)

    def test_an_interrupt_drops_the_rounds_queued_judgements(self):
        # Ctrl-C while the first judge is out: the pool's exit would otherwise
        # run every queued session before the interrupt reaches the operator,
        # and a worker that finishes takes the next queued one before the
        # main thread acts on a late signal. No sleep decides it: the handler
        # waits for another judgement to start, so one that can run after the
        # interrupt always does.
        self.catalogue["knock"] = [row(3, "Knock.wav", 1.0)]
        self.cues += (cue("sfx", "S2", "Knock", 1, query="knock"),)
        judged, started = [], threading.Event()

        def judge(c, evidence):
            judged.append(c["cue_id"])
            if c["cue_id"] == "A1":
                self.interrupt()
            else:
                started.set()
            return {"verdict": "accept", "reason": "fits", "confidence": 0.9}
        data = self.data(*self.cues)
        self.interrupted(lambda: sound.fetch(
            self.root, data, token="t", jobs=1, say=self.hush,
            measure=lambda paths: [levels() for _ in paths], judge=judge), started)
        self.assertEqual(judged, ["A1"])

    def test_an_interrupt_drops_the_rounds_queued_searches_and_downloads(self):
        # The same for the round's first half: a Ctrl-C during the first
        # cue's download leaves the other cues unsearched. A search that
        # starts is written down before the handler is woken, so the
        # assertion does not depend on how far that worker gets before the
        # interrupt is raised.
        self.catalogue["knock"] = [row(3, "Knock.wav", 1.0)]
        self.cues += (cue("sfx", "S2", "Knock", 1, query="knock"),)
        inner, started = freesound.fetch_json, threading.Event()
        late: list[str] = []

        def fake_json(url, **kw):
            if "hum" not in url:
                late.append(url)
                started.set()
            return inner(url, **kw)

        def fake_bytes(url, **kw):
            self.downloads.append(url)
            self.interrupt()
            return b"ID3" + url.encode()
        freesound.fetch_json, freesound.fetch_bytes = fake_json, fake_bytes
        data = self.data(*self.cues)
        self.interrupted(lambda: sound.fetch(self.root, data, token="t", jobs=1,
                                             say=self.hush), started)
        self.assertEqual(late, [])
        self.assertEqual(self.searches, ["hum"])
        self.assertEqual(len(self.downloads), 1)

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

    def test_the_progress_lines_come_one_cue_at_a_time_in_cue_order(self):
        # A judge that fails says so from its thread, then the cue is accepted
        # unjudged: two lines a cue, which the threads would interleave.
        wait, lines = self.meeting("judge"), []

        def judge(c, evidence):
            wait()
            raise harness.HarnessError("the model is down")
        self.fetch(say=lines.append, measure=lambda paths: [levels() for _ in paths],
                   judge=judge)
        self.assertEqual(self.alone, [])
        self.assertEqual([line.split()[0] for line in lines], ["A1", "A1", "S1", "S1"])
        self.assertIn("the judge failed", lines[0])
        self.assertIn("sound/01-room-tone.mp3 <-", lines[1])


class InTurn(unittest.TestCase):
    """`sound._in_turn`, the hand-over the fetch's rounds use in place of
    `pool.map` (AUTO-129)."""

    def test_the_results_come_in_item_order_whichever_job_ends_first(self):
        second = threading.Event()

        def fn(x):
            if x == 0:
                second.wait(1.0)        # the first item ends after the second
            else:
                second.set()
            return x * 10
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sound._in_turn(pool, fn, [0, 1, 2], 2), [0, 10, 20])

    def test_a_job_that_fails_stops_the_items_not_yet_handed_over(self):
        # `pool.map` cancels its queued jobs on a failure too, but a free
        # worker can take the next one before the cancel; here it is never
        # handed over.
        ran: list[int] = []

        def fn(x):
            ran.append(x)
            if x == 0:
                raise OSError("disk full")
            return x
        with ThreadPoolExecutor(1) as pool:
            with self.assertRaises(OSError):
                sound._in_turn(pool, fn, [0, 1, 2], 1)
        self.assertEqual(ran, [0])


if __name__ == "__main__":
    unittest.main()
