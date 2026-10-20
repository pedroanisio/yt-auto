"""A sliced encode writes the render.json an unsliced encode does (AUTO-28).

The slice and mix reports are faked; nothing reaches a host.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import qa, render  # noqa: E402


def slice_report(i, start, end, *, quality="final", ok=True, findings=(), warnings=(),
                 evaluation=(), unsupported=(), frames=300, diagnostics=()):
    return {"format": "scene-render-cli/1", "ok": ok, "exit_code": 0 if ok else 1,
            "diagnostics": list(diagnostics),
            "result": {"outputs": [{
                "id": "main", "path": f"/tmp/yt-render/p/encode-1/slice-{i:02d}.mp4",
                "files": [f"/tmp/yt-render/p/encode-1/slice-{i:02d}.mp4"],
                "quality": quality, "range": [start, end], "fps": 30, "frames": frames,
                "findings": list(findings), "evaluation_warnings": list(evaluation),
                "unsupported": list(unsupported), "warnings": list(warnings)}]}}


def mix_report(**loud):
    out = {"id": "main", "path": "/tmp/yt-render/p/audio-1/mix.wav", "range": [0, 20],
           "loudness": -14.2, "true_peak": -1.5, "loudness_range": 6.0, "short_term_max": -11.0}
    out.update(loud)
    return {"format": "scene-render-cli/1", "ok": True, "exit_code": 0,
            "result": {"outputs": [out]}}


class Merge(unittest.TestCase):
    def merged(self, slices=None, mix=None):
        slices = slices if slices is not None else [slice_report(0, 0, 10), slice_report(1, 10, 20)]
        return render.merge_slice_reports(slices, mix if mix is not None else mix_report(),
                                          "/w/p/video.mp4", 0.0, 20.0)

    def test_one_output_for_the_joined_file(self):
        rep = self.merged()
        self.assertEqual(rep["format"], "scene-render-cli/1")
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["exit_code"], 0)
        (out,) = rep["result"]["outputs"]
        self.assertEqual(out["path"], "/w/p/video.mp4")
        self.assertEqual(out["files"], ["/w/p/video.mp4"])
        self.assertEqual(out["range"], [0.0, 20.0])
        self.assertEqual(out["duration"], 20.0)
        self.assertEqual(out["quality"], "final")
        self.assertEqual(out["frames"], 600)
        self.assertEqual(out["fps"], 30)

    def test_loudness_is_the_mixs(self):
        out = self.merged()["result"]["outputs"][0]
        self.assertEqual(render.loudness_of(out), {"integrated": -14.2, "true_peak": -1.5,
                                                   "range": 6.0, "short_term_max": -11.0})

    def test_engine_reports_are_merged_once_each(self):
        f = {"severity": "warning", "message": "object a is OFF-CAMERA", "at": 3}
        rep = self.merged([
            slice_report(0, 0, 10, findings=[f], warnings=["W1: x"], evaluation=[{"code": "E", "message": "m"}]),
            slice_report(1, 10, 20, findings=[f, {"severity": "error", "message": "b"}],
                         warnings=["W1: x", "W2: y"], unsupported=["u"],
                         diagnostics=[{"code": "D", "message": "d"}])])
        out = rep["result"]["outputs"][0]
        self.assertEqual([x["message"] for x in out["findings"]], ["object a is OFF-CAMERA", "b"])
        self.assertEqual(out["warnings"], ["W1: x", "W2: y"])
        self.assertEqual(len(out["evaluation_warnings"]), 1)
        self.assertEqual(out["unsupported"], ["u"])
        self.assertEqual(rep["diagnostics"], [{"code": "D", "message": "d"}])

    def test_a_slice_below_final_quality_shows(self):
        rep = self.merged([slice_report(0, 0, 10), slice_report(1, 10, 20, quality="draft")])
        self.assertEqual(rep["result"]["outputs"][0]["quality"], "draft")

    def test_a_failed_part_makes_the_report_not_ok(self):
        rep = self.merged([slice_report(0, 0, 10), slice_report(1, 10, 20, ok=False)])
        self.assertFalse(rep["ok"])
        self.assertNotEqual(rep["exit_code"], 0)

    def test_parts_without_json_still_give_an_envelope(self):
        rep = render.merge_slice_reports([{}, {}], {}, "/w/p/video.mp4", 5.0, 25.0)
        out = rep["result"]["outputs"][0]
        self.assertEqual(out["path"], "/w/p/video.mp4")
        self.assertEqual(out["range"], [5.0, 25.0])
        self.assertEqual(render.loudness_of(out), {})

    def test_readers_accept_it(self):
        rep = self.merged()
        self.assertTrue(any("LUFS" in line for line in render.summarise(rep)))
        self.assertEqual(qa.engine_diagnostics(rep, [{"label": "a", "at": 0, "seconds": 20}]), {})


class EncodeSliced(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "film"
        self.root.mkdir()
        (self.root / "scene.xml").write_text("<scene/>")
        self.ran = []

    def tearDown(self):
        self._tmp.cleanup()

    def fake_remote(self, cmd, project_root, host, **kw):
        self.ran.append(list(cmd))
        out = cmd[cmd.index("-o") + 1]
        start, end = float(cmd[cmd.index("--start") + 1]), float(cmd[cmd.index("--end") + 1])
        if out == "mix.wav":
            rep = mix_report()
        else:
            rep = slice_report(int(out[6:8]), start, end)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(rep) if "--json" in cmd else "",
                                           stderr="")

    def run_sliced(self, **kw):
        host = render.Host("root@h", engine="/opt/scene-render", ram_gb=64)

        def fake_run(argv, **k):
            if argv[0] == "rsync":
                (self.root / "video.mp4").write_bytes(b"\0" * 16)
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        with mock.patch.object(render, "hosts", return_value=[host]), \
                mock.patch.object(render, "_run_remote", side_effect=self.fake_remote), \
                mock.patch.object(render, "refuse_remote"), \
                mock.patch.object(render, "remove_remote"), \
                mock.patch.object(render.subprocess, "run", side_effect=fake_run):
            return render.encode_sliced(str(self.root / "scene.xml"), self.root, slices=2,
                                        quality="final", start=0.0, end=20.0, strict=True,
                                        verbose=False, **kw)

    def test_the_encode_writes_render_json(self):
        self.assertEqual(self.run_sliced(), 0)
        rep = json.loads((self.root / "render.json").read_text())
        self.assertTrue(rep["ok"])
        (out,) = rep["result"]["outputs"]
        self.assertEqual(out["path"], str(self.root / "video.mp4"))
        self.assertEqual(out["range"], [0.0, 20.0])
        self.assertEqual(out["quality"], "final")
        self.assertEqual(out["loudness"], -14.2)
        self.assertTrue(all("--json" in c for c in self.ran))

    def test_a_named_report_path_is_honoured(self):
        target = self.root / "out" / "render.json"
        target.parent.mkdir()
        self.assertEqual(self.run_sliced(report_path=target), 0)
        self.assertTrue(target.exists())
        self.assertFalse((self.root / "render.json").exists())

    def test_a_failed_slice_writes_no_report(self):
        def bad(cmd, *a, **k):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
        self.fake_remote = bad
        self.assertEqual(self.run_sliced(), 1)
        self.assertFalse((self.root / "render.json").exists())


if __name__ == "__main__":
    unittest.main()
