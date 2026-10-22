"""Host health and detached remote jobs (AUTO-17, AUTO-19).

No test opens a connection: subprocess.run, socket and sleep are replaced.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import hermetic  # noqa: E402
from ytstudio import doctor, render  # noqa: E402


def setUpModule():
    # every test here replaces subprocess.run, so the refusal is lifted for them
    hermetic.fake_remote_module(sys.modules[__name__])


def done(argv, rc=0, out="", err=""):
    return subprocess.CompletedProcess(argv, rc, stdout=out, stderr=err)


def exit_marker(code):
    return f"__YT_JOB_EXIT__={code}\n"


class Routing(unittest.TestCase):
    def setUp(self):
        render._HOSTS.clear()
        render._HEALTH.clear()
        render._HOSTS["a"] = render.Host("a", engine="/e", ram_gb=16, cores=8)
        render._HOSTS["b"] = render.Host("b", engine="/e", ram_gb=64, cores=16)
        self.env = mock.patch.dict("os.environ", {"RS_RENDER_HOSTS": "a,b"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(render._HOSTS.clear)
        self.addCleanup(render._HEALTH.clear)

    def ssh_down(self, *dead):
        calls = []

        def fake(argv, **kw):
            calls.append((argv, kw))
            return done(argv, 255 if argv[-2] in dead else 0)
        return mock.patch.object(render.subprocess, "run", side_effect=fake), calls

    def test_a_still_goes_to_the_other_host_when_ssh_refuses(self):
        patch, calls = self.ssh_down("a")             # a is the small host: the usual still target
        with patch:
            self.assertEqual(render.remote_host(None, "still"), "b")
        self.assertIn("a", render._HOSTS["a"].error or "a")

    def test_the_recheck_is_short_and_quiet(self):
        patch, calls = self.ssh_down()
        with patch:
            render.remote_host(None, "still")
        argv, kw = calls[0]
        self.assertIn("ConnectTimeout=4", argv)
        self.assertIn("BatchMode=yes", argv)
        self.assertLessEqual(kw["timeout"], 15)

    def test_a_healthy_check_is_cached_for_a_moment(self):
        # AUTO-69: every candidate is read once (its load decides the routing),
        # and the next moments' jobs reuse the readings.
        patch, calls = self.ssh_down()
        with patch:
            render.remote_host(None, "still")
            render.remote_host(None, "still")
        self.assertEqual(sorted(argv[-2] for argv, _ in calls), ["a", "b"])

    def test_an_encode_skips_a_dead_host_too(self):
        patch, _ = self.ssh_down("b")                 # b is the big host
        with patch:
            self.assertEqual(render.remote_host(None, "encode"), "a")

    def test_when_every_host_is_down_the_first_name_is_returned(self):
        patch, _ = self.ssh_down("a", "b")
        with patch:
            self.assertEqual(render.remote_host(None, "still"), "a")

    def test_a_timeout_counts_as_down(self):
        def fake(argv, **kw):
            if argv[-2] == "a":
                raise subprocess.TimeoutExpired(argv, 1)
            return done(argv)
        with mock.patch.object(render.subprocess, "run", side_effect=fake):
            self.assertEqual(render.remote_host(None, "still"), "b")


class Doctor(unittest.TestCase):
    def report(self, host, tcp):
        rep = doctor.Report("t")
        with mock.patch.object(render, "tcp_open", return_value=tcp):
            doctor.check_hosts(rep, [host])
        return rep.findings

    def test_tcp_up_ssh_down_is_named(self):
        host = render.Host("root@h", error="Connection timed out during banner exchange")
        found = self.report(host, True)
        self.assertTrue(any("TCP" in f.message and "SSH" in f.message for f in found),
                        [f.message for f in found])

    def test_a_host_that_is_simply_down_is_not_called_tcp_up(self):
        host = render.Host("root@h", error="No route to host")
        found = self.report(host, False)
        self.assertFalse(any("TCP" in f.message and "not SSH" in f.message for f in found))
        self.assertTrue(found)

    def test_load_and_gpu_used_are_printed_beside_the_routing(self):
        small = render.Host("root@small", engine="/e", version="sr 0.6", ram_gb=8, cores=8,
                            gpu="3070", gpu_mem_mb=8192)
        big = render.Host("root@big", engine="/e", version="sr 0.6", ram_gb=125, cores=12, gpu="3050")
        health = {"root@small": render.Health(alive=True, load1=29.4, gpu_used_mb=7812),
                  "root@big": render.Health(alive=True, load1=1.2)}
        rep = doctor.Report("t")
        doctor.check_hosts(rep, [small, big], health)
        lines = {f.message.split(" on ")[1].split(" ")[0]: f.message for f in rep.findings if f.level == "ok"}
        self.assertIn("load 29.4", lines["root@small"])
        self.assertIn("GPU 7812/8192 MB used", lines["root@small"])
        self.assertIn("load 1.2", lines["root@big"])
        # the saturated host does not get the stills; the big one takes everything
        self.assertNotIn("stills", lines["root@small"])
        self.assertIn("stills", lines["root@big"])
        warn = [f for f in rep.findings if f.level == "warn" and "root@small" in f.message]
        self.assertEqual(len(warn), 1, [f.message for f in rep.findings])
        self.assertIn("saturated", warn[0].message)

    def test_an_idle_gpu_is_printed_as_zero_used_not_left_out(self):
        # 0 used is a reading like any other: the line says so rather than
        # looking like a host whose GPU was never read.
        host = render.Host("root@h", engine="/e", version="sr 0.6", ram_gb=8, cores=8,
                           gpu="3070", gpu_mem_mb=8192)
        rep = doctor.Report("t")
        doctor.check_hosts(rep, [host], {"root@h": render.Health(alive=True, load1=0.3)})
        ok = [f.message for f in rep.findings if f.level == "ok"]
        self.assertIn("GPU 0/8192 MB used", ok[0])
        # a host that did not answer has no reading to print
        rep = doctor.Report("t")
        doctor.check_hosts(rep, [host], {"root@h": render.Health(alive=False, error="timed out")})
        self.assertNotIn("GPU 0/8192", " ".join(f.message for f in rep.findings))

    def test_without_readings_the_line_is_as_before(self):
        host = render.Host("root@h", engine="/e", version="sr 0.6", ram_gb=8, cores=8)
        rep = doctor.Report("t")
        doctor.check_hosts(rep, [host])
        ok = [f.message for f in rep.findings if f.level == "ok"]
        self.assertEqual(len(ok), 1)
        self.assertNotIn("load", ok[0])

    def test_tcp_open_reads_the_host_out_of_user_at_host(self):
        with mock.patch("socket.create_connection") as conn:
            self.assertTrue(render.tcp_open("root@10.0.0.9"))
        self.assertEqual(conn.call_args[0][0], ("10.0.0.9", 22))
        with mock.patch("socket.create_connection", side_effect=OSError):
            self.assertFalse(render.tcp_open("root@10.0.0.9"))


class Detached(unittest.TestCase):
    def setUp(self):
        render._HOSTS["h"] = render.Host("h", engine="/opt/sr/scene-render")
        self.addCleanup(render._HOSTS.clear)

    def run_job(self, answers):
        seen = []

        def fake(argv, **kw):
            seen.append(list(argv))
            if argv[0] == "ssh" and answers and "rmdir" not in argv[-1]:
                return answers.pop(0)(argv)
            if argv[0] == "ssh" and "rmdir" not in argv[-1]:
                return done(argv, 0, exit_marker(0))
            return done(argv, 0, "{}")
        with mock.patch.object(render.subprocess, "run", side_effect=fake), \
                mock.patch.object(render.time, "sleep"):
            proc = render._run_remote(["scene-render", "render", "/w/m/s.xml", "-o", "/w/m/o.mp4"],
                                      Path("/w/m"), "h", verbose=False, remote="/tmp/yt-render/m/encode-1-a")
        return proc, seen

    def test_every_ssh_and_rsync_carries_a_keepalive(self):
        _, seen = self.run_job([])
        for argv in seen:
            if argv[0] == "ssh":
                self.assertIn("ServerAliveInterval=15", argv)
            else:
                self.assertTrue(any("ServerAliveInterval" in t for t in argv), argv)
        self.assertIn("ServerAliveInterval=15", render.probe_argv("h", "true"))

    def test_every_ssh_and_rsync_carries_a_connect_timeout(self):
        # AUTO-69: a host that takes TCP but never answers ssh hung every sync,
        # launch, poll and removal for good; the probe was the only bounded call.
        _, seen = self.run_job([])
        self.assertTrue(seen)
        for argv in seen:
            if argv[0] == "ssh":
                self.assertIn("ConnectTimeout=10", argv)
            else:
                self.assertTrue(any("ConnectTimeout=10" in t for t in argv), argv)
        self.assertIn("ConnectTimeout=10", render.ssh_argv("h", "true"))
        self.assertIn("ConnectTimeout=10", render.SSH_COMMAND)

    def test_a_probe_keeps_its_own_shorter_timeout_and_names_it_once(self):
        # ssh takes the first value of a repeated option, so the probe's must be
        # the only one on its argv.
        argv = render.probe_argv("h", "true", 4)
        self.assertEqual([t for t in argv if t.startswith("ConnectTimeout=")], ["ConnectTimeout=4"])
        self.assertEqual(render.CONNECT_TIMEOUT, 10)

    def test_the_engine_runs_detached_with_a_status_file(self):
        _, seen = self.run_job([])
        launch = next(a for a in seen if a[0] == "ssh")[-1]
        self.assertIn("setsid", launch)
        self.assertIn("nohup", launch)
        self.assertIn("/tmp/yt-render/m/encode-1-a.status", launch)
        self.assertLess(launch.index("setsid"), launch.index("scene-render render"))

    def test_a_dropped_ssh_leaves_the_job_and_the_next_poll_pulls_it_back(self):
        proc, seen = self.run_job([lambda a: done(a, 255, "", "client_loop: send disconnect"),
                                   lambda a: done(a, 255),
                                   lambda a: done(a, 0, "rendered\n" + exit_marker(0), "")])
        ssh = [a[-1] for a in seen if a[0] == "ssh"]
        self.assertEqual(sum("setsid" in s for s in ssh), 1)        # launched once
        polls = [s for s in ssh if "setsid" not in s and "rmdir" not in s]
        self.assertEqual(len(polls), 2)
        for s in polls:
            self.assertIn(".status", s)
            self.assertNotIn("scene-render", s)                     # a poll never re-runs the engine
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "rendered")
        self.assertEqual([a[0] for a in seen].count("rsync"), 2)    # sync up, pull back
        self.assertEqual(seen[-2][0], "rsync")

    def test_the_engine_exit_code_comes_back(self):
        proc, _ = self.run_job([lambda a: done(a, 0, "out\n" + exit_marker(3), "bad scene")])
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(proc.stdout, "out")                        # marker stripped

    def test_a_job_that_exits_255_is_its_result_not_a_dropped_session(self):
        proc, seen = self.run_job([lambda a: done(a, 0, exit_marker(255), "boom")])
        self.assertEqual(proc.returncode, 255)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "boom")
        ssh = [a[-1] for a in seen if a[0] == "ssh" and "rmdir" not in a[-1]]
        self.assertEqual(len(ssh), 1)                               # zero polls

    def test_a_lost_job_returns_at_once_with_an_error(self):
        proc, seen = self.run_job([lambda a: done(a, 0, "__YT_JOB_LOST__=dead\n")])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("lost", proc.stderr)
        ssh = [a[-1] for a in seen if a[0] == "ssh" and "rmdir" not in a[-1]]
        self.assertEqual(len(ssh), 1)

    def test_a_lost_job_is_reported_after_a_dropped_poll_too(self):
        proc, _ = self.run_job([lambda a: done(a, 255),
                                lambda a: done(a, 0, "__YT_JOB_LOST__=deadline\n")])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("lost", proc.stderr)

    def test_the_script_records_the_pid_and_checks_it(self):
        _, seen = self.run_job([])
        launch = next(a for a in seen if a[0] == "ssh")[-1]
        self.assertIn("echo $$ >", launch)
        self.assertIn("/tmp/yt-render/m/encode-1-a.pid", launch)
        self.assertIn("kill -0", launch)
        self.assertIn("__YT_JOB_LOST__", launch)
        self.assertIn("__YT_JOB_EXIT__", launch)
        poll = render.attach_script("/tmp/yt-render/m/encode-1-a")
        self.assertIn("kill -0", poll)
        self.assertIn("date +%s", poll)                             # overall deadline

    def test_the_attach_loop_reports_a_dead_pid_and_a_finished_job(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base = d + "/job"
            gone = subprocess.Popen(["true"])
            gone.wait()
            Path(base + ".pid").write_text(str(gone.pid))
            r = subprocess.run(["sh", "-c", render.attach_script(base)],
                               capture_output=True, text=True, timeout=20)
            self.assertIn("__YT_JOB_LOST__", r.stdout)
            self.assertEqual(r.returncode, 0)
            Path(base + ".out").write_text("hi")
            Path(base + ".status").write_text("255\n")
            r = subprocess.run(["sh", "-c", render.attach_script(base)],
                               capture_output=True, text=True, timeout=20)
            self.assertEqual(r.returncode, 0)
            self.assertTrue(r.stdout.endswith("__YT_JOB_EXIT__=255\n"), r.stdout)

    def test_polling_gives_up_when_the_host_stays_away(self):
        proc, seen = self.run_job([lambda a: done(a, 255)] * 200)
        self.assertEqual(proc.returncode, 255)
        self.assertLess(len(seen), 200)

    def test_a_killed_ssh_without_a_marker_is_polled_then_pulled_back_once(self):
        for rc in (-15, 143, 1):
            with self.subTest(rc=rc):
                proc, seen = self.run_job([lambda a, rc=rc: done(a, rc),
                                           lambda a: done(a, 0, "ok\n" + exit_marker(0))])
                self.assertEqual(proc.returncode, 0)
                kinds = [a[0] for a in seen]
                self.assertEqual(kinds.count("rsync"), 2)           # sync up, one pull back
                pull = max(i for i, k in enumerate(kinds) if k == "rsync")
                polls = [i for i, a in enumerate(seen) if a[0] == "ssh" and "setsid" not in a[-1]
                         and "rmdir" not in a[-1]]
                self.assertEqual(len(polls), 1)
                self.assertLess(polls[0], pull)                     # pulled after the marker
                self.assertEqual(sum("rmdir" in a[-1] for a in seen if a[0] == "ssh"), 1)

    def test_no_marker_ever_leaves_the_job_alone_and_names_it(self):
        proc, seen = self.run_job([lambda a: done(a, -15)] * 500)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("/tmp/yt-render/m/encode-1-a", proc.stderr)
        self.assertIn("h:", proc.stderr)
        self.assertEqual([a[0] for a in seen].count("rsync"), 1)    # sync up only
        self.assertFalse(any("rmdir" in a[-1] for a in seen if a[0] == "ssh"))
        self.assertLessEqual(len([a for a in seen if a[0] == "ssh"]), render.REATTACH_TRIES + 1)

    def test_the_verbose_output_says_the_job_was_left(self):
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf), \
                mock.patch.object(render.subprocess, "run", side_effect=lambda a, **k: done(a, -15)
                                  if a[0] == "ssh" else done(a, 0)), \
                mock.patch.object(render.time, "sleep"):
            render._run_remote(["scene-render", "render", "/w/m/s.xml"], Path("/w/m"), "h",
                               verbose=True, remote="/tmp/yt-render/m/encode-1-a")
        self.assertIn("Left in place: h:/tmp/yt-render/m/encode-1-a", buf.getvalue())

    def test_cleanup_removes_the_control_files_with_the_directory(self):
        _, seen = self.run_job([])
        rm = seen[-1][-1]
        self.assertIn("rm -rf /tmp/yt-render/m/encode-1-a", rm)
        self.assertIn("encode-1-a.status", rm)


if __name__ == "__main__":
    unittest.main()
