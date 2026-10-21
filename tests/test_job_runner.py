"""One ssh launcher (render.ssh_argv) for every remote caller, one frame-statistics algorithm."""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.hermetic import fake_remote
from ytstudio import assess, framecheck, gate, geometry, qa, render

PKG = Path(render.__file__).parent


def keepalive(case, argv):
    case.assertEqual(argv[0], "ssh")
    case.assertIn("BatchMode=yes", argv)
    case.assertIn("ServerAliveInterval=15", argv)
    case.assertIn("ServerAliveCountMax=4", argv)


class OneLauncher(unittest.TestCase):
    def test_only_render_names_ssh(self):
        offenders = [p.name for p in sorted(PKG.glob("*.py"))
                     if p.name != "render.py" and '"ssh"' in p.read_text()]
        self.assertEqual(offenders, [])

    def setUp(self):
        fake_remote(self)
        self.ran = []

    def fake(self, argv, **kw):
        self.ran.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    def test_gate_ssh_calls_carry_the_keepalives(self):
        plan = gate.RemotePlan(host="root@h", root="/r/p/gate-1", project="/r/p/gate-1/p",
                               engine="/bin/e", local_root="/x/p", command=["gate"])
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(gate.subprocess, "run", self.fake), \
                mock.patch.object(gate.render_stage, "remove_remote"):
            gate.run_remote(plan, {}, Path(tmp), verbose=False, keep_remote=False)
        ssh = [(a, kw) for a, kw in self.ran if a[0] == "ssh"]
        self.assertEqual(len(ssh), 2)
        for argv, _ in ssh:
            keepalive(self, argv)
        self.assertIn("mkdir -p", ssh[0][0][-1])
        self.assertIn("input", ssh[0][1])

    def test_geometry_ssh_carries_the_keepalives(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(geometry.render, "probe",
                                  lambda h, **k: render.Host(addr=h, engine="/r/e")), \
                mock.patch.object(geometry.subprocess, "run", self.fake):
            root = Path(tmp) / "proj"
            root.mkdir()
            geometry.remote_frames(root, "scene.xml", [0.5], "root@h")
        evals = [a for a, _ in self.ran if a[0] == "ssh" and "--format json" in a[-1]]
        self.assertEqual(len(evals), 1)
        keepalive(self, evals[0])

    def test_assess_ssh_carries_the_keepalives_and_its_connect_timeout(self):
        with mock.patch.object(assess.render, "engine", lambda: None), \
                mock.patch.object(assess.subprocess, "run", self.fake):
            assess.engine_facts("root@h")
        argv, kw = self.ran[0]
        keepalive(self, argv)
        self.assertIn("ConnectTimeout=8", argv)
        self.assertEqual(argv[-2:], ["root@h", argv[-1]])
        self.assertEqual(kw.get("timeout"), 60)


def _png(path_or_none=None):
    from PIL import Image
    im = Image.new("RGB", (64, 48))
    for x in range(64):
        for y in range(48):
            im.putpixel((x, y), (255,) * 3 if x < 20 else (x * 3, y * 5, 90))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    if path_or_none:
        Path(path_or_none).write_bytes(buf.getvalue())
    return buf.getvalue()


class OneFrameStats(unittest.TestCase):
    def test_framecheck_reads_a_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "f.png"
            data = _png(p)
            self.assertEqual(framecheck.frame_stats(p), framecheck.frame_stats(data))

    def test_qa_maps_the_framecheck_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "f.png"
            _png(p)
            st = framecheck.frame_stats(p)
            self.assertEqual(qa.frame_stats(p), {
                "mean": st["mean"], "spread": st["std"],
                "dominant_share": st["dominant_share"], "clipped_share": st["clipped"]})

    def test_qa_has_no_algorithm_of_its_own(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "f.png"
            _png(p)
            with mock.patch.object(framecheck, "frame_stats",
                                   return_value={"mean": 1, "std": 2, "dominant_share": 3,
                                                 "clipped": 4}) as stats:
                out = qa.frame_stats(p)
        stats.assert_called_once()
        self.assertEqual(out["spread"], 2)

    def test_an_unreadable_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.png"
            bad.write_bytes(b"nope")
            self.assertEqual(qa.frame_stats(bad), {})
            self.assertEqual(qa.frame_stats(Path(tmp) / "missing.png"), {})


if __name__ == "__main__":
    unittest.main()
