"""AUTO-40: a long continuous world stays inside memory -- the vfx gate names layers that
are never time-bounded, and an encode is sliced from the GPU memory a job measured, not
from fields no producer writes."""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import hermetic  # noqa: E402
from ytstudio import render, vfx  # noqa: E402

REAL_ENCODE_SLICED = render.encode_sliced


def world(unbounded: int, bounded: int = 2, duration: int = 60) -> str:
    rows = ['<layer id="u%d" asset="a%d"/>' % (i, i) for i in range(unbounded)]
    rows += ['<layer id="b%d" asset="b%d" start="%d" end="%d"/>' % (i, i, i, i + 2)
             for i in range(bounded)]
    return ('<scene version="1.3"><project width="1920" height="1080" fps="30" '
            'duration="%d"/><symbol id="w" duration="%d">' % (duration, duration)
            + "".join(rows) + "</symbol></scene>")


def gpu_report(peak_mb: int, seconds: float) -> dict:
    return {"gpu_memory": {"peak_mb": peak_mb, "programme_seconds": seconds, "host": "h",
                           "host_total_mb": 8192}}


class Gate(unittest.TestCase):
    def test_unbounded_layers_in_a_long_world_are_named_with_their_count(self):
        found = vfx.unbounded_layer_findings(world(105))
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith("warning"), found)
        self.assertIn("105", found[0])
        self.assertIn("u0", found[0])
        self.assertIn("60", found[0])

    def test_bounded_layers_raise_nothing(self):
        self.assertEqual(vfx.unbounded_layer_findings(world(0, bounded=5)), [])

    def test_a_world_no_longer_than_its_layers_raises_nothing(self):
        self.assertEqual(vfx.unbounded_layer_findings(world(3, bounded=2, duration=2)), [])

    def test_a_bad_document_raises_nothing(self):
        self.assertEqual(vfx.unbounded_layer_findings("<scene"), [])


class Survey(unittest.TestCase):
    def survey(self, stdout: str) -> render.Host:
        render._HOSTS.pop("root@gpu", None)
        self.addCleanup(render._HOSTS.pop, "root@gpu", None)
        hermetic.fake_remote(self)
        done = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
        with mock.patch.object(render.subprocess, "run", return_value=done) as run:
            host = render.probe("root@gpu")
        self.script = run.call_args.args[0][-1]
        return host

    def test_the_gpu_total_is_asked_of_nvidia_smi_and_parsed(self):
        host = self.survey("ENGINE=/e\nRAM=125\nCORES=8\nGPU=RTX 3070\nGPUMEM=8192\n")
        self.assertEqual(host.gpu_mem_mb, 8192)
        self.assertIn("--query-gpu=memory.total --format=csv,noheader,nounits", self.script)

    def test_no_gpu_or_unreadable_text_is_zero(self):
        self.assertEqual(self.survey("ENGINE=/e\nGPUMEM=\n").gpu_mem_mb, 0)
        self.assertEqual(render.parse_mb("[N/A]"), 0)
        self.assertEqual(render.parse_mb(" 4096 \n"), 4096)


class Sampler(unittest.TestCase):
    """job_script(sample_gpu=True) run for real against a fake nvidia-smi."""

    # an engine process (pid1) with a child of its own (pid2), as a real render has
    ENGINE = ("""sh -c 'echo $$ > pid1; sh -c "echo \\$\\$ > pid2; sleep 1.2; true"; echo hello'; """
              "exit 3")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "mirror").mkdir()
        (self.tmp / "bin").mkdir()
        self.job = self.tmp / "job"

    def fake_smi(self, pmon: str = "", accounted: str = "", used: str = "echo 0"):
        """pmon / accounted / used are shell fragments printing that query's output; every
        call is counted in `calls`; `p1`/`p2` are the engine's and its child's pids."""
        smi = self.tmp / "bin" / "nvidia-smi"
        smi.write_text(
            "#!/bin/sh\n"
            f"echo x >> {self.tmp}/calls\n"
            f"p1=$(cat {self.job}/pid1 2>/dev/null); p2=$(cat {self.job}/pid2 2>/dev/null)\n"
            'case "$1" in\n'
            f"  pmon) echo '# gpu pid type fb ccpm command'; echo '0 1 G 9999 0 other'; {pmon or ':'};;\n"
            f"  --query-accounted-apps*) {accounted or ':'};;\n"
            f"  --query-gpu=memory.used) {used};;\n"
            "esac\n")
        smi.chmod(0o755)

    def script(self, binary: str | None = None) -> str:
        return render.job_script(str(self.tmp / "mirror"), str(self.job), binary or self.ENGINE, "",
                                 sample_gpu=True)

    def plain(self) -> str:
        return render.job_script(str(self.tmp / "mirror"), str(self.job), self.ENGINE, "")

    def env(self, *, smi: bool = True) -> dict:
        if smi:
            return dict(os.environ, PATH=f"{self.tmp / 'bin'}:{os.environ['PATH']}")
        tools = self.tmp / "tools"
        tools.mkdir(exist_ok=True)
        for name in ("sh", "rm", "cp", "sleep", "ps", "awk", "tr", "cat", "sort", "rsync", "wc"):
            found = shutil.which(name)
            if found and not (tools / name).exists():
                (tools / name).symlink_to(found)
        return dict(os.environ, PATH=str(tools))

    def run_script(self, script: str | None = None, env: dict | None = None):
        return subprocess.run(["sh", "-c", script or self.script()], capture_output=True, text=True,
                              env=env or self.env())

    def calls(self) -> int:
        f = self.tmp / "calls"
        return len(f.read_text().split()) if f.exists() else 0

    def test_only_the_engine_and_its_children_are_counted(self):
        # the engine's shell (700) plus its child (300); pid 1 lists 9999 and is not ours
        self.fake_smi(pmon='echo "0 $p1 G 700 0 e"; echo "0 $p2 C 300 0 c"')
        proc = self.run_script()
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertEqual(render.take_gpu_peak(proc), 1000)
        self.assertEqual(proc.gpu_source, "pmon")
        self.assertNotIn(render.GPU_MARK, proc.stderr)

    def test_accounting_catches_a_render_shorter_than_the_period(self):
        # pmon sees nothing of ours; accounting kept the engine's maximum
        self.fake_smi(accounted='echo "$p1, 640"; echo "1, 9999"')
        proc = self.run_script()
        self.assertEqual(render.take_gpu_peak(proc), 640)
        self.assertEqual(proc.gpu_source, "accounted")

    def test_with_neither_the_baseline_delta_is_the_fallback(self):
        count = self.tmp / "n"
        self.fake_smi(used=f"echo x >> {count}; if [ $(wc -l < {count}) -le 1 ]; "
                           "then echo 1000; else echo 1600; fi")
        proc = self.run_script()
        self.assertEqual(render.take_gpu_peak(proc), 600)
        self.assertEqual(proc.gpu_source, "baseline-delta")

    def test_nothing_seen_prints_no_mark(self):
        self.fake_smi()
        proc = self.run_script()
        self.assertNotIn(render.GPU_MARK, proc.stderr)
        self.assertIsNone(render.take_gpu_peak(proc))

    def test_the_sampler_never_changes_exit_code_or_stdout(self):
        self.fake_smi(pmon='echo "0 $p1 G 700 0 e"')
        bare = self.run_script(self.plain(), env=self.env(smi=False))
        sampled = self.run_script()
        self.assertEqual((bare.returncode, bare.stdout), (3, "hello\n"))
        self.assertEqual((sampled.returncode, sampled.stdout), (3, "hello\n"))
        ok = self.run_script(self.script("echo fine"))
        self.assertEqual((ok.returncode, ok.stdout), (0, "fine\n"))

    def test_a_host_without_nvidia_smi_runs_the_job_as_before(self):
        sampled = self.run_script(env=self.env(smi=False))
        plain = self.run_script(self.plain(), env=self.env(smi=False))
        self.assertEqual((sampled.returncode, sampled.stdout, sampled.stderr),
                         (plain.returncode, plain.stdout, plain.stderr))
        self.assertEqual(sampled.returncode, 3, sampled.stderr)
        self.assertNotIn(render.GPU_MARK, sampled.stderr)

    def assert_sampler_stopped(self):
        before = self.calls()
        time.sleep(0.8)
        self.assertEqual(self.calls(), before, "the sampler is still running")
        self.assertFalse((self.job / render.GPU_PEAK_FILE).exists())

    def test_the_sampler_is_killed_when_the_engine_exits_normally_or_fails(self):
        for engine in ("sleep 0.6", "sleep 0.6; exit 9"):
            with self.subTest(engine=engine):
                self.fake_smi()
                self.run_script(self.script(engine))
                self.assert_sampler_stopped()

    def test_the_sampler_is_killed_when_the_job_is_terminated(self):
        self.fake_smi()
        p = subprocess.Popen(["sh", "-c", self.script("sleep 1")], env=self.env(),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.5)
        p.terminate()
        p.wait(timeout=10)
        self.assertEqual(p.returncode, 143)
        self.assert_sampler_stopped()

    def test_an_unsampled_script_has_no_sampler(self):
        script = render.job_script("/m", "/j", "scene-render", "encode x")
        self.assertNotIn("nvidia-smi", script)
        self.assertIsNone(render.take_gpu_peak(subprocess.CompletedProcess([], 0, "", "plain")))

    def test_the_remote_run_samples_only_a_host_with_a_gpu(self):
        hermetic.fake_remote(self)
        for gpu, expect in (("RTX 3070", True), ("", False)):
            render._HOSTS["root@s"] = render.Host("root@s", engine="/e", gpu=gpu)
            self.addCleanup(render._HOSTS.pop, "root@s", None)
            seen = []

            def fake(argv, **kw):
                seen.append(" ".join(map(str, argv)))
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
            tmp = Path(tempfile.mkdtemp())
            self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
            with mock.patch.object(render.subprocess, "run", side_effect=fake), \
                    mock.patch.object(render, "_run_detached", side_effect=lambda h, s, r, **k: (
                        seen.append(s) or subprocess.CompletedProcess(
                            [], 0, "", f"{render.GPU_MARK}900 pmon\n"), True)), \
                    mock.patch.object(render, "remove_remote"):
                proc = render._run_remote(["scene-render", "render", "scene.xml"], tmp, "root@s",
                                          verbose=False)
            self.assertEqual("nvidia-smi pmon" in " ".join(seen), expect)
            self.assertEqual((proc.gpu_peak_mb, proc.gpu_source), (900, "pmon"))
            self.assertEqual(proc.stderr, "")


class Report(unittest.TestCase):
    def test_an_encode_stores_peak_and_the_range_it_rendered(self):
        block = render.gpu_measurement(5000, 40.0, "root@h", 8192, "pmon")
        rep = render.job_report("encode", exit_code=0, stdout="", gpu_memory=block)
        self.assertEqual(rep["gpu_memory"], {"kind": "encode", "peak_mb": 5000,
                                             "programme_seconds": 40.0, "host": "root@h",
                                             "host_total_mb": 8192, "source": "pmon"})
        self.assertNotIn("gpu_memory", render.job_report("encode", exit_code=0, stdout=""))

    def test_a_still_stores_a_peak_and_no_rate(self):
        block = render.gpu_measurement(5000, None, "root@h", 8192, "accounted")
        self.assertEqual(block, {"kind": "still", "peak_mb": 5000, "host": "root@h",
                                 "host_total_mb": 8192, "source": "accounted"})
        self.assertNotIn("programme_seconds", block)

    def test_nothing_sampled_or_nothing_used_is_no_measurement(self):
        self.assertIsNone(render.gpu_measurement(None, 40.0))
        self.assertIsNone(render.gpu_measurement(0, 40.0))
        self.assertIsNone(render.gpu_measurement(900, 0))      # an encode of no range


class Planner(unittest.TestCase):
    def hosts(self, *totals):
        return [render.Host("h%d" % i, engine="/x", ram_gb=125, gpu="g", gpu_mem_mb=t)
                for i, t in enumerate(totals)]

    def test_a_report_gives_mb_per_second_and_the_worst_one_wins(self):
        self.assertAlmostEqual(render.measured_mb_per_second(gpu_report(4000, 20)), 200.0)
        self.assertAlmostEqual(render.measured_mb_per_second(gpu_report(4000, 20),
                                                             gpu_report(9000, 30)), 300.0)

    def test_a_still_is_never_a_rate(self):
        still = {"gpu_memory": render.gpu_measurement(8000, None, "h", 8192)}
        self.assertIsNone(render.measured_mb_per_second(still))
        # even a still block carrying seconds (an older report) is not read as a rate
        still["gpu_memory"]["programme_seconds"] = 64.61
        self.assertIsNone(render.measured_mb_per_second(still))
        self.assertEqual(render.still_peak_mb(still, gpu_report(4000, 20)), 8000)
        self.assertIsNone(render.still_peak_mb(gpu_report(4000, 20)))

    def test_plan_slices_ignores_a_still_and_says_so(self):
        n, why = render.plan_slices(2, 0.0, 100.0, self.hosts(8192), model=None,
                                    still_peak=8000)
        self.assertEqual(n, 2)
        self.assertIn("8000", why)
        self.assertIn("not a rate: ignored", why)
        self.assertIn("not measured", why)

    def test_no_producer_means_no_measurement(self):
        # fields the engine never writes are not read
        rss = {"result": {"outputs": [{"range": [0, 20], "peak_rss_gb": 10}]}}
        self.assertIsNone(render.measured_mb_per_second(rss, None, {}, {"gpu_memory": "x"}))

    def test_slices_rise_until_each_fits_the_smallest_gpu(self):
        n, why = render.plan_slices(1, 0.0, 100.0, self.hosts(24576, 8192), model=(0.0, 200.0, "t"))
        # 20000 MiB in all; the 8 GB GPU holds 8192 * 0.8 = 6553 -> 4 slices
        self.assertEqual(n, 4)
        self.assertLessEqual(100.0 / n * 200.0, 8192 * 0.8)
        self.assertIn("8192", why)

    def test_a_larger_request_is_kept(self):
        self.assertEqual(render.plan_slices(6, 0.0, 10.0, self.hosts(8192), model=(0.0, 200.0, "t"))[0], 6)

    def test_no_measurement_keeps_the_count_and_says_so(self):
        n, why = render.plan_slices(3, 0.0, 100.0, self.hosts(8192), model=None)
        self.assertEqual(n, 3)
        self.assertIn("not measured", why)

    def test_a_host_that_reports_no_gpu_total_keeps_the_count(self):
        n, why = render.plan_slices(2, 0.0, 100.0, self.hosts(0), model=(0.0, 200.0, "t"))
        self.assertEqual(n, 2)
        self.assertIn("no usable host", why)

    def still(self, peak=920):
        return {"gpu_memory": render.gpu_measurement(peak, None, "h", 8192, "pmon")}

    def test_a_still_is_the_base_and_the_encode_adds_growth(self):
        base, growth, basis = render.memory_model(self.still(920), gpu_report(1113, 10))
        self.assertEqual(base, 920)
        self.assertAlmostEqual(growth, 19.3)
        self.assertIn("still", basis)
        n, why = render.plan_slices(1, 0.0, 65.0, self.hosts(8192), model=(base, growth, basis))
        self.assertEqual(n, 1)           # 920 + 19.3 * 65 = 2175 MiB against 6553
        self.assertIn("920 MiB base", why)
        self.assertIn("19.3 MiB/s", why)

    def test_a_12_minute_film_is_not_cut_into_13_slices(self):
        model = render.memory_model(self.still(920), gpu_report(1113, 10))
        n, _ = render.plan_slices(1, 0.0, 720.0, self.hosts(8192), model=model)
        self.assertEqual(n, 3)           # (6553 - 920) / 19.3 = 291 s per slice

    def test_growth_above_the_gpu_is_sliced(self):
        model = render.memory_model(self.still(920), gpu_report(6920, 10))   # 600 MiB/s
        n, why = render.plan_slices(1, 0.0, 65.0, self.hosts(8192), model=model)
        self.assertEqual(n, 7)           # (6553.6 - 920) / 600 = 9.4 s per slice
        self.assertIn("600.0 MiB/s", why)

    def test_growth_is_floored_at_zero(self):
        base, growth, _ = render.memory_model(self.still(1200), gpu_report(1113, 10))
        self.assertEqual((base, growth), (1200, 0.0))

    def test_without_a_still_one_encode_is_an_upper_bound(self):
        base, growth, basis = render.memory_model(gpu_report(1113, 10))
        self.assertEqual((base, growth), (0.0, 111.3))
        self.assertIn("upper bound", basis)

    def test_two_encodes_of_different_lengths_fit_both_terms(self):
        base, growth, _ = render.memory_model(gpu_report(1113, 10), gpu_report(1500, 30))
        self.assertAlmostEqual(growth, 19.35)
        self.assertAlmostEqual(base, 919.5)

    def test_a_base_over_the_gpu_keeps_the_count(self):
        n, why = render.plan_slices(2, 0.0, 65.0, self.hosts(8192), model=(9000.0, 1.0, "t"))
        self.assertEqual(n, 2)
        self.assertIn("slicing cannot help", why)

    def test_a_fitting_encode_stays_one_slice(self):
        self.assertEqual(render.plan_slices(1, 0.0, 10.0, self.hosts(8192), model=(0.0, 200.0, "t"))[0], 1)

    def test_reports_are_read_from_the_encode_and_the_still(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "render-still.json").write_text(json.dumps(gpu_report(4000, 20)))
            found = render.memory_reports(root / "render.json")
            self.assertAlmostEqual(render.measured_mb_per_second(*found), 200.0)


class AutoSlice(unittest.TestCase):
    def setUp(self):
        hermetic.fake_remote(self)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "scene.xml").write_text("<scene/>")
        self.host = render.Host("root@h", engine="/x", ram_gb=125, gpu="g", gpu_mem_mb=8192)
        self.sliced = []
        for p in (mock.patch.object(render, "host_names", return_value=["root@h"]),
                  mock.patch.object(render, "remote_host", return_value="root@h"),
                  mock.patch.object(render, "hosts", return_value=[self.host]),
                  mock.patch.object(render, "encode_sliced",
                                    side_effect=lambda *a, **k: self.sliced.append(k["slices"]) or 0),
                  mock.patch.object(render, "_run_remote", side_effect=self.unsliced)):
            p.start()
            self.addCleanup(p.stop)
        self.ran = []

    def unsliced(self, cmd, *a, **k):
        self.ran.append(cmd)
        proc = subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        proc.gpu_peak_mb = 3000
        return proc

    def args(self, **over):
        base = dict(project=str(self.tmp), title=None, work=str(self.tmp.parent), genre="t",
                    description="d", scene=str(self.tmp / "scene.xml"), video_out=None,
                    quality=None, start=None, end=100.0, still=None, frames=None, strict=False,
                    parallel=None, validate=False, output=None, quiet=True, model=None, slices=1,
                    keep_remote=False)
        return argparse.Namespace(**base | over)

    def test_a_long_encode_the_measurement_says_will_not_fit_is_sliced_unasked(self):
        (self.tmp / "render-still.json").write_text(json.dumps(gpu_report(4000, 20)))
        render.run(self.args())
        self.assertEqual(self.sliced, [4])      # 20000 MiB against 8192 * 0.8
        self.assertEqual(self.ran, [])

    def test_with_no_measurement_it_encodes_unsliced(self):
        render.run(self.args())
        self.assertEqual(self.sliced, [])
        self.assertEqual(len(self.ran), 1)

    def test_a_short_encode_that_fits_stays_whole(self):
        (self.tmp / "render-still.json").write_text(json.dumps(gpu_report(4000, 20)))
        render.run(self.args(end=10.0))
        self.assertEqual(self.sliced, [])

    def test_the_encode_report_records_the_peak(self):
        render.run(self.args())
        rep = json.loads((self.tmp / "render.json").read_text())
        self.assertEqual(rep["gpu_memory"]["peak_mb"], 3000)
        self.assertEqual(rep["gpu_memory"]["programme_seconds"], 100.0)
        self.assertEqual(rep["gpu_memory"]["host_total_mb"], 8192)

    def test_encode_sliced_cuts_by_a_recorded_measurement(self):
        root = self.tmp
        (root / "render.json").write_text(json.dumps(gpu_report(4000, 20)))
        ran = []

        def fake(cmd, *a, **k):
            ran.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="stop")
        with mock.patch.object(render, "_run_remote", side_effect=fake):
            REAL_ENCODE_SLICED(str(root / "scene.xml"), root, slices=2, quality=None, start=0.0,
                               end=100.0, strict=False, verbose=False)
        # 200 MiB/s over 100 s is 20000 MiB; 8192 * 0.8 per slice -> 4 slices, not the 2 asked
        self.assertEqual(len([c for c in ran if "slice-" in c[c.index("-o") + 1]]), 4, ran)

    def test_encode_sliced_says_when_memory_was_not_measured(self):
        err = io.StringIO()
        with mock.patch.object(render, "_run_remote", side_effect=lambda c, *a, **k:
                               subprocess.CompletedProcess(c, 1, stdout="", stderr="stop")), \
                mock.patch("sys.stderr", err):
            REAL_ENCODE_SLICED(str(self.tmp / "scene.xml"), self.tmp, slices=2, quality=None,
                               start=0.0, end=100.0, strict=False, verbose=False)
        self.assertIn("not measured", err.getvalue())


if __name__ == "__main__":
    unittest.main()
