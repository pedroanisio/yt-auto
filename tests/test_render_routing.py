"""Routing by the host's live load (AUTO-69).

A saturated host (load over its cores, or a GPU nearly full) stops receiving
stills while the other host idles; a sync that fails sends the job to the other
host once and marks the first dead. No test opens a connection: subprocess.run
is replaced.
"""

from __future__ import annotations

import subprocess
import sys
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

    def run_job(self, failing):
        seen = []

        def fake(argv, **kw):
            seen.append(list(argv))
            if argv[0] == "rsync":
                target = argv[-1].partition(":")[0] if "--delete" in argv else argv[-2].partition(":")[0]
                if target in failing:
                    raise subprocess.CalledProcessError(255, argv, "", "ssh: connect to host: timed out")
                return done(argv)
            if "rmdir" in argv[-1]:
                return done(argv)
            return done(argv, 0, "LOAD=0\nGPUUSED=0\n__YT_JOB_EXIT__=0\n")
        with mock.patch.object(render.subprocess, "run", side_effect=fake), \
                mock.patch.object(render.time, "sleep"):
            proc = render._run_remote(["scene-render", "render", "/w/m/s.xml", "-o", "/w/m/o.png"],
                                      Path("/w/m"), "small", verbose=False, job="still")
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

    def test_a_sync_that_works_retries_nothing(self):
        _, seen = self.run_job(set())
        self.assertEqual([a[-2] for a in seen if a[0] == "ssh" and "setsid" in a[-1]], ["small"])
        self.assertNotIn("small", render._HEALTH)                   # nothing marked, nothing re-read


if __name__ == "__main__":
    unittest.main()
