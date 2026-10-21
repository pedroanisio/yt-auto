"""AUTO-46 / AUTO-47: a pull-back never overwrites a newer local file, and a sliced
encode leaves no slice MP4 in the project.

rsync runs for real on local directories (the pull-back's own options, the host replaced by a
path); the remote work is faked like tests/test_sliced_report.py does. No ssh, no network.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import hermetic  # noqa: E402
from tests.test_sliced_report import mix_report, slice_report  # noqa: E402
from ytstudio import render  # noqa: E402


def setUpModule():
    hermetic.fake_remote_module(sys.modules[__name__])


def put(path: Path, text: str, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (mtime, mtime))


class PullBack(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.local = Path(self._tmp.name) / "film"
        self.remote = Path(self._tmp.name) / "job"
        self.local.mkdir()
        self.remote.mkdir()

    def pull(self, cmd):
        """The pull-back argv for `cmd`, run for real with the remote side a local path."""
        argv = render.pull_back_args("h", "/r", self.local, named=render.named_outputs(cmd))
        i = argv.index("-e")
        argv = argv[:i] + argv[i + 2:-2] + [f"{self.remote}/", f"{self.local}/"]
        subprocess.run(argv, check=True, capture_output=True)

    def test_an_older_remote_report_never_replaces_a_newer_local_one(self):
        put(self.local / "render.json", "fresh", 2000)
        put(self.remote / "render.json", "stale", 1000)
        put(self.remote / "render-still.json", "mine", 3000)
        put(self.remote / "still.png", "png", 3000)
        self.pull(["scene-render", "render", "scene.xml", "-o", "still.png", "--time", "1"])
        self.assertEqual((self.local / "render.json").read_text(), "fresh")

    def test_the_fallback_patterns_also_keep_the_newer_file(self):
        put(self.local / "render.json", "fresh", 2000)
        put(self.local / "retake.wav", "fresh", 2000)
        put(self.remote / "render.json", "stale", 1000)
        put(self.remote / "retake.wav", "stale", 1000)
        put(self.remote / "pilot.mp4", "new", 3000)
        self.pull(["scene-render", "encode", "scene.xml", "--output", "pilot"])
        self.assertEqual((self.local / "render.json").read_text(), "fresh")
        self.assertEqual((self.local / "retake.wav").read_text(), "fresh")
        self.assertEqual((self.local / "pilot.mp4").read_text(), "new")

    def test_a_still_pulls_back_only_its_own_outputs(self):
        put(self.remote / "still.png", "png", 3000)
        put(self.remote / "render-still.json", "report", 3000)
        put(self.remote / "render.json", "other", 3000)
        put(self.remote / "voice.wav", "other", 3000)
        put(self.remote / "art" / "key.png", "other", 3000)
        put(self.remote / "video.mp4", "other", 3000)
        self.pull(["scene-render", "render", "scene.xml", "-o", "still.png", "--time", "1"])
        got = sorted(str(p.relative_to(self.local)) for p in self.local.rglob("*") if p.is_file())
        self.assertEqual(got, ["still.png"])

    def test_a_named_report_and_a_nested_output_come_back(self):
        put(self.remote / "render-audio.json", "r", 3000)
        put(self.remote / "sub" / "mix.wav", "w", 3000)
        put(self.remote / "sub" / "other.wav", "x", 3000)
        self.pull(["scene-render", "encode", "s.xml", "-o", "sub/mix.wav", "--report", "render-audio.json"])
        got = sorted(str(p.relative_to(self.local)) for p in self.local.rglob("*") if p.is_file())
        self.assertEqual(got, ["render-audio.json", "sub/mix.wav"])

    def test_a_frames_directory_comes_back_whole(self):
        put(self.remote / "frames" / "f1.png", "a", 3000)
        put(self.remote / "frames" / "f2.png", "b", 3000)
        put(self.remote / "stray.png", "c", 3000)
        self.pull(["scene-render", "render", "s.xml", "-o", "frames", "--frames", "1..2"])
        got = sorted(str(p.relative_to(self.local)) for p in self.local.rglob("*") if p.is_file())
        self.assertEqual(got, ["frames/f1.png", "frames/f2.png"])

    def test_outputs_outside_the_project_are_not_named(self):
        self.assertEqual(render.named_outputs(["x", "-o", "/tmp/a.png", "--report", "../r.json"]), ())


class Sliced(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "film"
        self.root.mkdir()
        (self.root / "scene.xml").write_text("<scene/>")
        self.ran: list[list[str]] = []
        self.kept: list[bool] = []
        self.fail_slice = None
        self.error = "boom"

    def fake_remote(self, cmd, project_root, host, **kw):
        self.ran.append(list(cmd))
        if kw.get("keep_remote"):
            self.kept.append(True)
        out = cmd[cmd.index("-o") + 1]
        start, end = float(cmd[cmd.index("--start") + 1]), float(cmd[cmd.index("--end") + 1])
        if out == "mix.wav":
            return subprocess.CompletedProcess(cmd, 0, json.dumps(mix_report()), "")
        i = int(re.search(r"slice-(\d+)\.mp4$", out).group(1))
        if i == self.fail_slice:
            return subprocess.CompletedProcess(cmd, 1, "", self.error)
        (project_root / out).parent.mkdir(parents=True, exist_ok=True)
        (project_root / out).write_bytes(b"\0" * 8)         # the pull-back
        return subprocess.CompletedProcess(cmd, 0, json.dumps(slice_report(i, start, end)), "")

    def run_sliced(self, **kw):
        host = render.Host("root@h", engine="/opt/scene-render", ram_gb=64)

        def fake_run(argv, **k):
            if argv[0] == "rsync" and not argv[-1].rpartition(":")[0]:
                src = argv[-2].rpartition(":")[2]               # the join's pull of one film
                Path(argv[-1]).write_bytes(b"\0" * 16)
                self.pulled = src
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        with mock.patch.object(render, "hosts", return_value=[host]), \
                mock.patch.object(render, "_run_remote", side_effect=self.fake_remote), \
                mock.patch.object(render, "refuse_remote"), \
                mock.patch.object(render, "remove_remote"), \
                mock.patch.object(render.subprocess, "run", side_effect=fake_run):
            return render.encode_sliced(str(self.root / "scene.xml"), self.root, slices=2,
                                        quality="final", start=0.0, end=20.0, strict=True,
                                        verbose=False, **kw)

    def slice_files(self):
        return sorted(p for p in self.root.rglob("slice-*.mp4"))

    def test_no_slice_remains_after_a_sliced_encode(self):
        self.assertEqual(self.run_sliced(), 0)
        self.assertEqual(self.slice_files(), [])
        self.assertTrue((self.root / "video.mp4").exists())
        self.assertFalse((self.root / ".slices").exists())

    def test_the_mirror_does_not_carry_the_slices(self):
        self.assertIn(".slices/", render.SYNC_EXCLUDES)
        self.run_sliced()
        outs = [c[c.index("-o") + 1] for c in self.ran if "slice-" in c[c.index("-o") + 1]]
        self.assertTrue(all(o.startswith(".slices/") for o in outs), outs)

    def test_a_failed_run_keeps_finished_slices_and_the_next_run_reuses_them(self):
        self.fail_slice = 1
        self.assertEqual(self.run_sliced(), 1)
        self.assertEqual(len(self.slice_files()), 1)
        self.ran.clear()
        self.fail_slice = None
        self.assertEqual(self.run_sliced(), 0)
        encoded = [c[c.index("-o") + 1] for c in self.ran if "slice-" in c[c.index("-o") + 1]]
        self.assertEqual(len(encoded), 1, encoded)
        self.assertIn("slice-01.mp4", encoded[0])
        self.assertEqual(self.slice_files(), [])

    def test_a_changed_scene_does_not_reuse_old_slices(self):
        self.fail_slice = 1
        self.run_sliced()
        (self.root / "scene.xml").write_text("<scene changed='1'/>")
        self.ran.clear()
        self.fail_slice = None
        self.run_sliced()
        self.assertEqual(len([c for c in self.ran if "slice-" in c[c.index("-o") + 1]]), 2)

    def test_a_changed_included_document_or_asset_does_not_reuse_old_slices(self):
        (self.root / "vfx" / "v01").mkdir(parents=True)
        (self.root / "scene.xml").write_text('<scene><include href="vfx/v01/a.scene.xml"/>'
                                             '<image src="art/k.png"/></scene>')
        (self.root / "vfx" / "v01" / "a.scene.xml").write_text('<doc><image src="b.png"/></doc>')
        (self.root / "vfx" / "v01" / "b.png").write_bytes(b"1")
        (self.root / "art").mkdir()
        (self.root / "art" / "k.png").write_bytes(b"1")
        for rel, data in (("vfx/v01/a.scene.xml", b'<doc><image src="b.png"/><x/></doc>'),
                          ("vfx/v01/b.png", b"2"), ("art/k.png", b"2")):
            self.fail_slice = 1
            self.run_sliced()                               # slice 0 finished, kept
            (self.root / rel).write_bytes(data)
            self.ran.clear()
            self.fail_slice = None
            self.assertEqual(self.run_sliced(), 0)
            self.assertEqual(len([c for c in self.ran if "slice-" in c[c.index("-o") + 1]]), 2, rel)

    def test_an_unreadable_reference_reuses_nothing(self):
        (self.root / "scene.xml").write_text('<scene><image src="gone.png"/></scene>')
        self.fail_slice = 1
        self.run_sliced()
        self.ran.clear()
        self.fail_slice = None
        self.run_sliced()
        self.assertEqual(len([c for c in self.ran if "slice-" in c[c.index("-o") + 1]]), 2)

    def test_output_names_the_film_and_its_report(self):
        report = self.root / "render-pilot.json"
        self.assertEqual(self.run_sliced(output="pilot", report_path=report), 0)
        self.assertTrue((self.root / "pilot.mp4").exists())
        self.assertFalse((self.root / "video.mp4").exists())
        self.assertTrue(report.exists())
        self.assertFalse((self.root / "render.json").exists())
        self.assertEqual(json.loads(report.read_text())["result"]["outputs"][0]["path"],
                         str(self.root.resolve() / "pilot.mp4"))

    def test_keep_remote_keeps_the_join_directory(self):
        self.run_sliced(keep_remote=True)
        self.assertTrue(self.kept)

    def test_the_failure_report_carries_each_slices_own_line_in_full(self):
        self.fail_slice = 0
        self.error = "starting\nerror: " + "x" * 400 + " end-of-line\n"
        self.assertEqual(self.run_sliced(), 1)
        message = json.loads((self.root / "render-failed.json").read_text())["message"]
        self.assertIn("error: " + "x" * 400 + " end-of-line", message)
        self.assertIn("slice 0", message)


if __name__ == "__main__":
    unittest.main()
