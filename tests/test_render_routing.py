"""Routing by the host's live load (AUTO-69).

A saturated host (load over its cores, or a GPU nearly full) stops receiving
stills while the other host idles; a sync that fails sends the job to the other
host once and marks the first dead. No test opens a connection: subprocess.run
is replaced.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import hermetic  # noqa: E402
from ytstudio import render  # noqa: E402


def setUpModule():
    hermetic.fake_remote_module(sys.modules[__name__])


def done(argv, rc=0, out="", err=""):
    return subprocess.CompletedProcess(argv, rc, stdout=out, stderr=err)


def small_and_big():
    small = render.Host("small", engine="/e", ram_gb=8, cores=8, gpu="3070", gpu_mem_mb=8192)
    big = render.Host("big", engine="/e", ram_gb=125, cores=12, gpu="3050", gpu_mem_mb=8192)
    return small, big


class HostHealth(unittest.TestCase):
    def setUp(self):
        render._HEALTH.clear()
        self.addCleanup(render._HEALTH.clear)

    def test_one_round_trip_reads_load_and_gpu_memory_used(self):
        calls = []

        def fake(argv, **kw):
            calls.append(argv)
            return done(argv, 0, "LOAD=29.41\nGPUUSED=7812\n")
        with mock.patch.object(render.subprocess, "run", side_effect=fake):
            health = render.host_health("small")
        self.assertTrue(health.alive)
        self.assertAlmostEqual(health.load1, 29.41)
        self.assertEqual(health.gpu_used_mb, 7812)
        self.assertEqual(len(calls), 1)
        self.assertIn("ConnectTimeout=4", calls[0])
        self.assertIn("/proc/loadavg", calls[0][-1])
        self.assertIn("memory.used", calls[0][-1])

    def test_a_reading_is_cached_for_a_moment(self):
        with mock.patch.object(render.subprocess, "run",
                               side_effect=lambda a, **k: done(a, 0, "LOAD=1.0\nGPUUSED=100\n")) as run:
            render.host_health("small")
            again = render.host_health("small")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(again.gpu_used_mb, 100)

    def test_a_host_that_refuses_or_times_out_is_not_alive(self):
        with mock.patch.object(render.subprocess, "run", side_effect=lambda a, **k: done(a, 255, "", "refused")):
            self.assertFalse(render.host_health("small").alive)
        render._HEALTH.clear()
        with mock.patch.object(render.subprocess, "run", side_effect=subprocess.TimeoutExpired("ssh", 1)):
            health = render.host_health("small")
        self.assertFalse(health.alive)
        self.assertTrue(health.error)

    def test_a_host_without_a_gpu_or_a_load_reads_as_quiet(self):
        with mock.patch.object(render.subprocess, "run", side_effect=lambda a, **k: done(a, 0, "LOAD=\nGPUUSED=\n")):
            health = render.host_health("small")
        self.assertTrue(health.alive)
        self.assertEqual((health.load1, health.gpu_used_mb), (0.0, 0))

    def test_ssh_alive_is_the_same_reading(self):
        with mock.patch.object(render.subprocess, "run", side_effect=lambda a, **k: done(a, 0)) as run:
            self.assertTrue(render.ssh_alive("small"))
            self.assertTrue(render.host_health("small").alive)
        self.assertEqual(run.call_count, 1)


class PickHost(unittest.TestCase):
    """docs/compute.md's rule stays (memory, not GPU); the live load only takes
    a saturated host out of the light jobs."""

    def test_a_still_skips_a_host_whose_load_is_over_its_cores(self):
        small, big = small_and_big()
        health = {"small": render.Health(alive=True, load1=29.0), "big": render.Health(alive=True, load1=2.0)}
        for job in ("still", "frames", "validate", "window"):
            self.assertIs(render.pick_host([small, big], job, health), big, job)
        # a load under the cores is not saturation
        health["small"] = render.Health(alive=True, load1=7.9)
        self.assertIs(render.pick_host([small, big], "still", health), small)

    def test_a_still_skips_a_host_whose_gpu_is_nearly_full(self):
        small, big = small_and_big()
        health = {"small": render.Health(alive=True, gpu_used_mb=8192 - render.GPU_FREE_FLOOR_MB + 1),
                  "big": render.Health(alive=True)}
        self.assertIs(render.pick_host([small, big], "still", health), big)
        health["small"] = render.Health(alive=True, gpu_used_mb=8192 - render.GPU_FREE_FLOOR_MB)
        self.assertIs(render.pick_host([small, big], "still", health), small)

    def test_an_encode_follows_the_memory_whatever_the_load(self):
        small, big = small_and_big()
        health = {"small": render.Health(alive=True), "big": render.Health(alive=True, load1=40.0)}
        self.assertIs(render.pick_host([small, big], "encode", health), big)
        self.assertIs(render.pick_host([small, big], "audio", health), big)

    def test_when_every_host_is_saturated_the_usual_choice_stands(self):
        small, big = small_and_big()
        health = {"small": render.Health(alive=True, load1=29.0), "big": render.Health(alive=True, load1=30.0)}
        self.assertIs(render.pick_host([small, big], "still", health), small)

    def test_without_a_reading_nothing_changes(self):
        small, big = small_and_big()
        self.assertIs(render.pick_host([small, big], "still"), small)
        self.assertIs(render.pick_host([small, big], "still", {}), small)

    def test_saturated_names_the_reason(self):
        small, _ = small_and_big()
        self.assertEqual(render.saturated(small, None), "")
        self.assertIn("load", render.saturated(small, render.Health(alive=True, load1=29.0)))
        self.assertIn("GPU", render.saturated(small, render.Health(alive=True, gpu_used_mb=8000)))
        self.assertEqual(render.saturated(small, render.Health(alive=True, load1=3.0, gpu_used_mb=100)), "")


class Routing(unittest.TestCase):
    def setUp(self):
        render._HOSTS.clear()
        render._HEALTH.clear()
        small, big = small_and_big()
        render._HOSTS["small"], render._HOSTS["big"] = small, big
        self.env = mock.patch.dict("os.environ", {"RS_RENDER_HOSTS": "small,big"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(render._HOSTS.clear)
        self.addCleanup(render._HEALTH.clear)

    def loads(self, **load):
        def fake(argv, **kw):
            return done(argv, 0, f"LOAD={load.get(argv[-2], 0.0)}\nGPUUSED=0\n")
        return mock.patch.object(render.subprocess, "run", side_effect=fake)

    def test_a_saturated_still_host_is_skipped_for_the_idle_one(self):
        with self.loads(small=29.0, big=1.5):
            self.assertEqual(render.remote_host(None, "still"), "big")

    def test_a_quiet_still_host_keeps_the_stills(self):
        with self.loads(small=3.0, big=1.5):
            self.assertEqual(render.remote_host(None, "still"), "small")


class SyncRetry(unittest.TestCase):
    """A sync that fails sends the job to the other host once and marks this one dead."""

    def setUp(self):
        render._HOSTS.clear()
        render._HEALTH.clear()
        small, big = small_and_big()
        render._HOSTS["small"], render._HOSTS["big"] = small, big
        self.env = mock.patch.dict("os.environ", {"RS_RENDER_HOSTS": "small,big"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(render._HOSTS.clear)
        self.addCleanup(render._HEALTH.clear)

    def run_job(self, failing, code=255, err="ssh: connect to host: timed out", host="small"):
        seen = self.last_seen = []

        def fake(argv, **kw):
            seen.append(list(argv))
            if argv[0] == "rsync":
                target = argv[-1].partition(":")[0] if "--delete" in argv else argv[-2].partition(":")[0]
                if target in failing:
                    raise subprocess.CalledProcessError(code, argv, "", err)
                return done(argv)
            if "rmdir" in argv[-1]:
                return done(argv)
            return done(argv, 0, "LOAD=0\nGPUUSED=0\n__YT_JOB_EXIT__=0\n")
        with mock.patch.object(render.subprocess, "run", side_effect=fake), \
                mock.patch.object(render.time, "sleep"):
            proc = render._run_remote(["scene-render", "render", "/w/m/s.xml", "-o", "/w/m/o.png"],
                                      Path("/w/m"), host, verbose=False, job="still")
        return proc, seen

    def test_the_job_runs_on_the_other_host_and_the_first_is_dead(self):
        proc, seen = self.run_job({"small"})
        self.assertEqual(proc.returncode, 0)
        launches = [a for a in seen if a[0] == "ssh" and "setsid" in a[-1]]
        self.assertEqual([a[-2] for a in launches], ["big"])
        syncs = [a for a in seen if a[0] == "rsync" and "--delete" in a]
        self.assertEqual([a[-1].partition(":")[0] for a in syncs], ["small", "big"])
        self.assertFalse(render._HEALTH["small"].alive)
        self.assertIn("sync", render._HEALTH["small"].error)

    def test_a_second_failure_is_raised_not_retried_again(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_job({"small", "big"})

    def test_a_host_already_marked_dead_is_not_synced_again(self):
        # with no live candidate remote_host falls back to the first configured
        # host; under a fresh dead mark that one would only cost another connect
        # timeout, so the failure is raised instead of synced there
        render.mark_dead("small", "sync failed: away")
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_job({"big", "small"}, host="big")
        syncs = [a[-1].partition(":")[0] for a in self.last_seen if a[0] == "rsync" and "--delete" in a]
        self.assertEqual(syncs, ["big"])

    def test_a_sync_that_works_retries_nothing(self):
        _, seen = self.run_job(set())
        self.assertEqual([a[-2] for a in seen if a[0] == "ssh" and "setsid" in a[-1]], ["small"])
        self.assertNotIn("small", render._HEALTH)                   # nothing marked, nothing re-read

    def test_a_partial_transfer_is_raised_and_marks_nothing_dead(self):
        # rsync 23 is a file that vanished or could not be read here, not a host
        # that is away: the host keeps its reading and the job is not moved.
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            self.run_job({"small"}, code=23, err="rsync: some files could not be transferred")
        self.assertEqual(caught.exception.returncode, 23)
        self.assertNotIn("small", render._HEALTH)

    def test_a_connection_refused_by_name_is_dead_whatever_the_code(self):
        _, seen = self.run_job({"small"}, code=12, err="ssh: connect to host small port 22: Connection refused")
        self.assertEqual([a[-2] for a in seen if a[0] == "ssh" and "setsid" in a[-1]], ["big"])
        self.assertFalse(render._HEALTH["small"].alive)


def slice_argv_target(argv) -> str:
    """The host an rsync argv talks to: the remote side is the one token with a colon."""
    return next((t.partition(":")[0] for t in (argv[-1], argv[-2]) if ":" in t), "")


class SlicedRouting(unittest.TestCase):
    """The slices' audio mix has host affinity (the slices are sent to it and the
    join happens there), so a failed sync to its host is raised, never retried on
    the small host; a slice may move, and its measurement names the host that ran
    it."""

    def setUp(self):
        render._HOSTS.clear()
        render._HEALTH.clear()
        small, big = small_and_big()
        render._HOSTS["small"], render._HOSTS["big"] = small, big
        self.hosts = [small, big]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "film"
        self.root.mkdir()
        (self.root / "scene.xml").write_text("<scene/>")
        self.env = mock.patch.dict("os.environ", {"RS_RENDER_HOSTS": "small,big"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(render._HOSTS.clear)
        self.addCleanup(render._HEALTH.clear)

    def run_sliced(self, failing, after_slices=False):
        """`failing` hosts take no sync; with `after_slices`, only once both slices
        were launched (the mix shares the mirror the slices synced)."""
        seen = self.seen = []

        def fake(argv, **kw):
            seen.append(list(argv))
            if argv[0] == "rsync":
                launched = sum(a[0] == "ssh" and "setsid" in a[-1] for a in seen)
                up = any(t.startswith("--rsync-path") for t in argv)        # a sync, not a pull
                if up and slice_argv_target(argv) in failing and (launched >= 2 or not after_slices):
                    raise subprocess.CalledProcessError(255, argv, "", "ssh: connect to host: timed out")
                if ":" not in argv[-1] and argv[-1].endswith(".mp4"):
                    Path(argv[-1]).write_bytes(b"\0" * 16)         # the join's pull of the film
                return done(argv)
            if "setsid" in argv[-1]:
                return done(argv, 0, "__YT_JOB_EXIT__=0\n", "__YT_GPU_PEAK_MB__=3000 pmon\n")
            return done(argv, 0, "LOAD=0\nGPUUSED=0\n")
        with mock.patch.object(render, "hosts", return_value=self.hosts), \
                mock.patch.object(render.subprocess, "run", side_effect=fake), \
                mock.patch.object(render.time, "sleep"):
            code = render.encode_sliced(str(self.root / "scene.xml"), self.root, slices=2,
                                        quality=None, start=0.0, end=20.0, strict=False,
                                        verbose=False)
        return code, seen

    def launches(self, seen):
        return [(a[-2], "mix.wav" in a[-1]) for a in seen if a[0] == "ssh" and "setsid" in a[-1]]

    def test_the_audio_mix_never_moves_to_the_small_host(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_sliced({"big"}, after_slices=True)
        # the slices ran on both hosts, then nothing launched the mix: not on
        # big, whose sync failed, and not on small, which cannot hold it
        self.assertEqual([host for host, mix in self.launches(self.seen) if mix], [])
        self.assertCountEqual([host for host, mix in self.launches(self.seen) if not mix], ["small", "big"])

    def test_a_slice_moves_and_its_measurement_names_where_it_ran(self):
        code, seen = self.run_sliced({"small"})
        self.assertEqual(code, 0)
        self.assertEqual([h for h, mix in self.launches(seen) if not mix], ["big", "big"])
        report = json.loads((self.root / "render.json").read_text())
        self.assertEqual(report["gpu_memory"]["host"], "big")
        self.assertEqual(report["gpu_memory"]["host_total_mb"], 8192)
        self.assertFalse(render._HEALTH["small"].alive)

    def test_a_moved_slice_waits_its_turn_and_a_later_one_skips_the_dead_host(self):
        # plan_slices sized a slice for the smallest GPU alone, so the slice that
        # leaves the dead host must not render beside the one already on big; and
        # a later slice due on the dead host goes straight to big rather than pay
        # another failed sync. The first launch on big holds until small's sync
        # has failed and the moved slice had every chance to arrive beside it.
        small_failed, another, guard = threading.Event(), threading.Event(), threading.Lock()
        on_big, most = [0], [0]
        seen = []

        def fake(argv, **kw):
            seen.append(list(argv))
            if argv[0] == "rsync":
                up = any(t.startswith("--rsync-path") for t in argv)
                if up and slice_argv_target(argv) == "small":
                    small_failed.set()
                    raise subprocess.CalledProcessError(255, argv, "", "ssh: connect to host: timed out")
                if ":" not in argv[-1] and argv[-1].endswith(".mp4"):
                    Path(argv[-1]).write_bytes(b"\0" * 16)
                return done(argv)
            if "setsid" in argv[-1] and "mix.wav" not in argv[-1] and argv[-2] == "big":
                with guard:
                    on_big[0] += 1
                    most[0] = max(most[0], on_big[0])
                    if on_big[0] > 1:
                        another.set()
                small_failed.wait(5)
                another.wait(0.3)
                with guard:
                    on_big[0] -= 1
                return done(argv, 0, "__YT_JOB_EXIT__=0\n", "__YT_GPU_PEAK_MB__=3000 pmon\n")
            if "setsid" in argv[-1]:
                return done(argv, 0, "__YT_JOB_EXIT__=0\n")
            return done(argv, 0, "LOAD=0\nGPUUSED=0\n")
        with mock.patch.object(render, "hosts", return_value=self.hosts), \
                mock.patch.object(render.subprocess, "run", side_effect=fake), \
                mock.patch.object(render.time, "sleep"):
            code = render.encode_sliced(str(self.root / "scene.xml"), self.root, slices=3,
                                        quality=None, start=0.0, end=30.0, strict=False,
                                        verbose=False)
        self.assertEqual(code, 0)
        self.assertEqual([h for h, mix in self.launches(seen) if not mix], ["big"] * 3)
        self.assertEqual(most[0], 1, "two slices rendered on big at once")
        to_small = [a for a in seen if a[0] == "rsync" and any(t.startswith("--rsync-path") for t in a)
                    and slice_argv_target(a) == "small"]
        self.assertEqual(len(to_small), 1, "a later slice was synced to the host marked dead")


if __name__ == "__main__":
    unittest.main()
