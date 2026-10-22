"""AUTO-31 and AUTO-32: stills cached by fingerprint; one report shape for every render job.

The engine is a fake file and subprocess.run is replaced; nothing reaches a host.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import hermetic  # noqa: E402
from ytstudio import doctor, render, workspace  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"frame"
DOC = b'<scene><image src="art/a.png"/></scene>'
OK_REPORT = json.dumps({"format": "scene-render-cli/1", "ok": True, "result": {}})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cache = self.tmp / ".cache" / "stills"
        self.binary = self.tmp / "scene-render"
        self.binary.write_bytes(b"engine-v1")
        self.calls: list[list[str]] = []
        for p in (mock.patch.object(render, "engine", side_effect=lambda: self.binary),
                  mock.patch.object(render, "host_names", return_value=[]),
                  mock.patch.object(render, "REMOTE_ROOT", str(self.tmp / "remote")),
                  mock.patch.object(render.subprocess, "run", side_effect=self.fake)):
            p.start()
            self.addCleanup(p.stop)

    def fake(self, argv, **kw):
        argv = [str(a) for a in argv]
        if argv[1:2] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="scene-render 1.0\n", stderr="")
        self.calls.append(argv)
        if argv[1:2] == ["render"]:
            Path(kw["cwd"], "frame.png").write_bytes(PNG)
        return subprocess.CompletedProcess(argv, 0, stdout=OK_REPORT, stderr="")

    def renders(self):
        return [c for c in self.calls if c[1:2] == ["render"]]

    def still(self, doc=DOC, at=1.0, files=None, **kw):
        files = {"art/a.png": b"art-v1"} if files is None else files
        return render.engine_render_still(doc, at, files=files, cache_dir=self.cache, **kw)

    def reports(self):
        return sorted((self.cache.parent / "reports").glob("*.json"))


class StillCache(Base):
    def test_the_same_document_at_the_same_time_runs_the_engine_once(self):
        first = self.still()
        second = self.still()
        self.assertEqual(first[0], PNG)
        self.assertEqual(second[0], PNG)
        self.assertEqual(len(self.renders()), 1)
        self.assertIn("cache", second[1])

    def test_a_changed_asset_invalidates_the_cache(self):
        self.still()
        self.still(files={"art/a.png": b"art-v2"})
        self.assertEqual(len(self.renders()), 2)

    def test_a_changed_engine_invalidates_the_cache(self):
        self.still()
        self.binary.write_bytes(b"engine-v2")
        self.still()
        self.assertEqual(len(self.renders()), 2)

    def test_time_quality_and_document_each_change_the_key(self):
        self.still()
        self.still(at=2.0)
        self.still(quality="final")
        self.still(doc=DOC + b" ")
        self.assertEqual(len(self.renders()), 4)

    def test_a_hit_is_served_before_any_ssh(self):
        self.still()
        with mock.patch.object(render, "remote_host", side_effect=AssertionError("ssh")), \
                mock.patch.object(render, "refuse_remote", side_effect=AssertionError("ssh")):
            png, _ = self.still()
        self.assertEqual(png, PNG)

    def test_a_failed_render_is_not_cached(self):
        def fail(argv, **kw):
            self.calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")
        with mock.patch.object(render.subprocess, "run", side_effect=fail):
            png, message = self.still()
        self.assertIsNone(png)
        self.assertIn("render failed", message)
        self.still()
        self.assertEqual(len(self.renders()), 2)

    def test_the_fingerprint_names_what_it_depends_on(self):
        key = render.still_fingerprint(DOC, {"a": b"1"}, "doc.scene.xml", 1.0, "draft", "engine-a")
        self.assertEqual(key, render.still_fingerprint(DOC, {"a": b"1"}, "doc.scene.xml", 1.0,
                                                       "draft", "engine-a"))
        for other in (render.still_fingerprint(DOC, {"a": b"2"}, "doc.scene.xml", 1.0, "draft", "engine-a"),
                      render.still_fingerprint(DOC, {"a": b"1"}, "doc.scene.xml", 1.5, "draft", "engine-a"),
                      render.still_fingerprint(DOC, {"a": b"1"}, "doc.scene.xml", 1.0, "final", "engine-a"),
                      render.still_fingerprint(DOC, {"a": b"1"}, "doc.scene.xml", 1.0, "draft", "engine-b")):
            self.assertNotEqual(key, other)


class HostFreeHits(Base):
    """sdkkit.frame answers from the cache before it picks a host or takes its lock."""

    DOC = ('<scene><project width="8" height="8" fps="30" duration="1"/>'
           '<image src="../../tex/a.png"/><textStyle fontFile="../../fonts/f.ttf"/></scene>')

    def setUp(self):
        super().setUp()
        from ytstudio import sdkkit
        self.sdkkit = sdkkit
        self.proj = self.tmp / "proj"
        for sub in ("vfx/v1", "tex", "fonts"):
            (self.proj / sub).mkdir(parents=True)
        (self.proj / "tex" / "a.png").write_bytes(b"tex-v1")
        (self.proj / "fonts" / "f.ttf").write_bytes(b"font-v1")
        self.doc = self.proj / "vfx" / "v1" / "a.scene.xml"
        self.doc.write_text(self.DOC)
        self.locks: list[str] = []
        outer, real = self, sdkkit.host_lock

        class Lock(real):
            def __init__(self, host):
                outer.locks.append(host)
                super().__init__(host)
        for p in (mock.patch.object(sdkkit, "host_lock", Lock),
                  mock.patch.object(sdkkit.framecheck, "judge_frame", return_value=[]),
                  mock.patch.object(render, "still_cache_dir", return_value=self.cache)):
            p.start()
            self.addCleanup(p.stop)

    def frame(self):
        return self.sdkkit.frame(self.doc, 0.5, out_dir=self.tmp / "out")

    def test_a_hit_costs_no_ssh_and_takes_no_lock(self):
        first, _ = self.frame()
        self.assertIsNotNone(first)
        self.locks.clear()
        with mock.patch.object(render, "remote_host", side_effect=AssertionError("ssh")):
            path, _ = self.frame()
        self.assertIsNotNone(path)
        self.assertEqual(self.locks, [])
        self.assertEqual(len(self.renders()), 1)

    def test_a_hit_says_it_came_from_the_cache_not_that_it_rendered(self):
        self.frame()
        _, findings = self.frame()
        notes = [f for f in findings if f.startswith("note:")]
        self.assertTrue(any("served from cache (rendered earlier on" in n for n in notes), notes)
        self.assertFalse(any(self.sdkkit.RENDER_NOTE in n for n in notes), notes)

    def test_a_real_render_keeps_the_render_note(self):
        _, findings = self.frame()
        self.assertTrue(any(f.startswith(f"note: {self.sdkkit.RENDER_NOTE} ") for f in findings))

    def test_a_changed_texture_outside_the_shipped_set_renders_again(self):
        self.frame()
        (self.proj / "tex" / "a.png").write_bytes(b"tex-v2")
        self.frame()
        self.assertEqual(len(self.renders()), 2)

    def test_a_changed_font_renders_again(self):
        self.frame()
        (self.proj / "fonts" / "f.ttf").write_bytes(b"font-v2")
        self.frame()
        self.assertEqual(len(self.renders()), 2)

    def test_an_unreadable_reference_neither_reads_nor_writes_the_cache(self):
        self.frame()
        before = set(self.cache.glob("*.png"))
        (self.proj / "tex" / "a.png").write_bytes(b"tex-v2")
        with mock.patch.object(render, "reference_files", return_value=None):
            self.frame()
            self.frame()
        self.assertEqual(set(self.cache.glob("*.png")), before)
        self.assertEqual(len(self.renders()), 3)

    def test_reference_files_reads_every_attribute(self):
        got = render.reference_files(self.doc.read_text(), self.doc.parent)
        self.assertEqual(sorted(got.values()), [b"font-v1", b"tex-v1"])

    def test_reference_files_is_none_when_one_cannot_be_read(self):
        (self.proj / "tex" / "a.png").unlink()
        self.assertIsNone(render.reference_files(self.doc.read_text(), self.doc.parent))
        (self.proj / "tex" / "a.png").write_bytes(b"x")
        (self.proj / "tex" / "a.png").chmod(0)
        self.addCleanup((self.proj / "tex" / "a.png").chmod, 0o644)
        if not os.access(self.proj / "tex" / "a.png", os.R_OK):
            self.assertIsNone(render.reference_files(self.doc.read_text(), self.doc.parent))


class StillReports(Base):
    def test_a_render_leaves_a_rendered_report(self):
        self.still()
        (path,) = self.reports()
        rep = json.loads(path.read_text())
        self.assertEqual(rep["format"], "scene-render-cli/1")
        self.assertEqual(rep["status"], "rendered")
        self.assertEqual(rep["job"], "still")
        self.assertEqual(rep["engine"]["version"], "scene-render 1.0")
        self.assertEqual(len(rep["engine"]["sha256"]), 64)
        self.assertIn("seconds", rep)

    def test_a_still_killed_by_its_timeout_leaves_a_timeout_report(self):
        def hang(argv, **kw):
            raise subprocess.TimeoutExpired(argv, kw["timeout"])
        with mock.patch.object(render.subprocess, "run", side_effect=hang), \
                self.assertRaises(subprocess.TimeoutExpired):
            self.still(timeout=7.0)
        (path,) = self.reports()
        rep = json.loads(path.read_text())
        self.assertEqual(rep["status"], "timeout")
        self.assertTrue(rep["timeout"])
        self.assertEqual(rep["timeout_seconds"], 7.0)
        self.assertFalse(rep["ok"])

    def test_a_document_naming_a_missing_asset_is_blocked_and_launches_nothing(self):
        png, message = self.still(files={})
        self.assertIsNone(png)
        self.assertIn("art/a.png", message)
        self.assertEqual(self.calls, [])
        rep = json.loads(self.reports()[0].read_text())
        self.assertEqual(rep["status"], "blocked-inputs")
        self.assertEqual(rep["missing_inputs"], ["art/a.png"])

    def test_a_failure_leaves_a_failed_report(self):
        def fail(argv, **kw):
            return subprocess.CompletedProcess(argv, 3, stdout="", stderr="no")
        with mock.patch.object(render.subprocess, "run", side_effect=fail):
            self.still()
        self.assertEqual(json.loads(self.reports()[0].read_text())["status"], "failed")


class RunReports(unittest.TestCase):
    """run(): the report is written whatever the outcome."""

    def setUp(self):
        hermetic.fake_remote(self)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "scene.xml").write_text('<scene><image src="art/a.png"/></scene>')
        (self.tmp / "art").mkdir()
        (self.tmp / "art" / "a.png").write_bytes(b"x")
        self.outcome = subprocess.CompletedProcess([], 0, stdout=OK_REPORT, stderr="")
        for p in (mock.patch.object(render, "engine", return_value=Path("/opt/sr/scene-render")),
                  mock.patch.object(render, "host_names", return_value=[]),
                  mock.patch.object(render.subprocess, "run", side_effect=self.fake)):
            p.start()
            self.addCleanup(p.stop)

    def fake(self, argv, **kw):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    def args(self, **over):
        base = dict(project=str(self.tmp), title=None, work=str(self.tmp.parent), genre="t",
                    description="d", scene=str(self.tmp / "scene.xml"), video_out=None,
                    quality=None, start=None, end=None, still=None, frames=None, strict=False,
                    parallel=None, validate=False, output=None, quiet=True, model=None)
        return argparse.Namespace(**base | over)

    def report(self, name):
        return json.loads((self.tmp / name).read_text())

    def test_success_carries_status_and_seconds(self):
        render.run(self.args())
        rep = self.report("render.json")
        self.assertEqual(rep["status"], "rendered")
        self.assertEqual(rep["format"], "scene-render-cli/1")
        self.assertIn("seconds", rep)
        self.assertIn("engine", rep)

    def test_a_failure_with_no_envelope_still_leaves_a_report(self):
        self.outcome = subprocess.CompletedProcess([], 2, stdout="", stderr="panic: bad")
        self.assertEqual(render.run(self.args(still=1.0)), 2)
        rep = self.report("render-still.json")
        self.assertEqual(rep["status"], "failed")
        self.assertEqual(rep["exit_code"], 2)
        self.assertFalse(rep["ok"])
        self.assertIn("panic", rep["message"])

    def test_a_failed_envelope_is_kept_and_marked_failed(self):
        self.outcome = subprocess.CompletedProcess([], 1, stdout=json.dumps(
            {"format": "scene-render-cli/1", "ok": False, "diagnostics": [{"code": "E1"}]}), stderr="")
        render.run(self.args(audio_only=True))
        rep = self.report("render-audio.json")
        self.assertEqual(rep["status"], "failed")
        self.assertEqual(rep["diagnostics"], [{"code": "E1"}])

    def test_a_missing_asset_is_blocked_before_the_engine_runs(self):
        (self.tmp / "art" / "a.png").unlink()
        self.outcome = AssertionError("the engine must not run")
        self.assertEqual(render.run(self.args()), 1)
        rep = self.report("render.json")
        self.assertEqual(rep["status"], "blocked-inputs")
        self.assertEqual(rep["missing_inputs"], ["art/a.png"])

    def test_an_abandoned_job_leaves_a_report(self):
        self.outcome = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            render.run(self.args(still=1.0))
        rep = self.report("render-still.json")
        self.assertEqual(rep["status"], "failed")
        self.assertIn("abandoned", rep["message"])

    def test_an_unknown_remote_outcome_is_recorded(self):
        note = "remote job outcome unknown: no result marker from h after 120 polls"
        rep = render.job_report("encode", exit_code=1, stdout="", stderr=note, seconds=3.0)
        self.assertEqual(rep["status"], "failed")
        self.assertTrue(rep["outcome_unknown"])

    def test_a_lost_deadline_is_a_timeout(self):
        rep = render.job_report("encode", exit_code=1, stdout="",
                                stderr="remote job lost: it did not finish within 21600 s",
                                seconds=3.0)
        self.assertEqual(rep["status"], "timeout")
        self.assertTrue(rep["timeout"])

    def test_the_sliced_encode_leaves_a_failed_report(self):
        host = render.Host("root@h", engine="/x/sr", ram_gb=8)
        path = self.tmp / "render.json"
        with mock.patch.object(render, "hosts", return_value=[host]), \
                mock.patch.object(render, "_run_remote", return_value=subprocess.CompletedProcess(
                    [], 1, stdout="", stderr="slice died")):
            code = render.encode_sliced(str(self.tmp / "scene.xml"), self.tmp, slices=2,
                                        quality=None, start=0.0, end=10.0, strict=False,
                                        verbose=False, report_path=path)
        self.assertEqual(code, 1)
        # beside render.json (which keeps describing the film on disk), never over it
        self.assertFalse(path.exists())
        rep = json.loads((self.tmp / "render-failed.json").read_text())
        self.assertEqual(rep["status"], "failed")
        self.assertIn("slice", rep["message"])


class RemoteDeadline(Base):
    """An exit 137 within reach of the deadline is the host's `timeout -k`
    kill: the report says timeout, and so does the message the author reads."""

    def test_an_exit_137_at_the_deadline_says_the_still_ran_past_it(self):
        def killed(argv, **kw):
            self.calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 137, stdout=b"", stderr=b"\nKilled\n")
        host = render.Host("root@h", engine="/x/sr")
        with mock.patch.object(render, "probe", return_value=host), \
                mock.patch.object(render, "refuse_remote", lambda *a, **k: None), \
                mock.patch.object(render.subprocess, "run", side_effect=killed):
            png, message = self.still(host="root@h", timeout=5)
        self.assertIsNone(png)
        self.assertIn("(exit 137)", message)
        self.assertIn("ran past 5 s", message)
        rep = json.loads(self.reports()[-1].read_text())
        self.assertEqual(rep["status"], "timeout")


class DoctorLists(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = workspace.Project(self.tmp)

    def write(self, rel, **fields):
        path = self.tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"format": "scene-render-cli/1", **fields}))

    def lines(self):
        report = doctor.Report("t")
        doctor.check_renders(self.project, report)
        return [(f.level, f.message) for f in report.findings if f.area == "render-jobs"]

    def test_a_timed_out_still_and_a_blocked_document_are_listed(self):
        self.write(".cache/reports/still-ab12.json", status="timeout", job="still", timeout=True,
                   timeout_seconds=240.0, seconds=240.0, ok=False)
        self.write("render.json", status="blocked-inputs", job="encode", ok=False,
                   missing_inputs=["art/a.png"])
        text = "\n".join(m for _, m in self.lines())
        self.assertIn("still", text)
        self.assertIn("timeout", text)
        self.assertIn("240", text)
        self.assertIn("blocked-inputs", text)
        self.assertIn("art/a.png", text)
        self.assertTrue(all(level == "warn" for level, _ in self.lines()))

    def test_rendered_reports_and_old_reports_raise_nothing(self):
        self.write("render.json", status="rendered", job="encode", ok=True)
        self.write("render-old.json", ok=True)
        self.assertEqual(self.lines(), [])


if __name__ == "__main__":
    unittest.main()
