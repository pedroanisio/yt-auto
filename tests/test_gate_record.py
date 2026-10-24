"""gate.json records the bytes it judged (AUTO-114): `video_sha256` and
`scene_sha256`, byte hashes of the local video and scene the gate was
given (the files plan_remote ships to a host), so `yt doctor` can tell a
touched file from a replaced one. Nothing here reaches a host or a model:
the remote run and the supervisor are stubs.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_engine import Event, Usage  # noqa: E402
from tests import hermetic  # noqa: E402,F401
from ytstudio import doctor, gate, harness  # noqa: E402


class Supervisor:
    """The gate's triage, answered from a script."""

    def __init__(self, config):
        self.config = config
        self.usage = Usage()
        self.usage.cost_usd = 0.01
        self.usage.calls = 1

    def ask_json(self, prompt, schema, system=None):
        return {"blocking": [], "acceptable": [], "verdict": "deliverable", "notes": ""}

    def stream(self, prompt, system=None):
        yield Event(type="result", raw={})


class GateRecord(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "a-film"
        self.root.mkdir()
        (self.root / "scene.xml").write_bytes(b"<scene judged='yes'/>")
        (self.root / "video.mp4").write_bytes(b"\x00\x01video bytes")
        (self.root / "render.json").write_text("{}", encoding="utf-8")
        (self.root / "narration.vtt").write_text("WEBVTT\n", encoding="utf-8")
        self.found = {"scene": str(self.root / "scene.xml"),
                      "video": str(self.root / "video.mp4"),
                      "encode report": str(self.root / "render.json"),
                      "captions": str(self.root / "narration.vtt"), "engine": "/e",
                      "ffprobe": "/f", "gate": "/g/production_gate.py", "tools dir": "/t",
                      "voice end": 15.0}
        self._engine = harness.ENGINE
        harness.ENGINE = Supervisor

    def tearDown(self):
        harness.ENGINE = self._engine
        self._tmp.cleanup()

    def run_gate(self, *, host: str | None) -> dict:
        args = argparse.Namespace(
            title="A film", project=str(self.root), scene=None, video=None, render=None,
            captions=[], voice=None, build=None, voice_end=15.0, tools_dir=None,
            loudness_target=-14.0, max_illegal=None, frames=False, preflight=False,
            fresh=True, host=host, local=host is None, keep_remote=False, quiet=True,
            model="sonnet", work=str(self.root.parent), env_file=None)
        passed = subprocess.CompletedProcess(["python3"], 0, stdout="TECHNICAL PASS", stderr="")
        ran = []

        def remote(plan, report, root, **kw):
            ran.append(plan.host)
            return passed

        with patch.object(gate, "inputs", lambda project, a, host="": (self.found, [])), \
                patch.object(gate.subprocess, "run", lambda *a, **k: passed), \
                patch.object(gate, "run_remote", remote), \
                patch.object(gate, "GEOMETRY", lambda *a: {"status": "skipped", "why": "stub"}), \
                patch.object(gate.house, "for_args", lambda *a, **k: ("", "")), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(gate.run(args), 0)
        self.assertEqual(ran, [host] if host else [])
        return json.loads((self.root / "gate.json").read_text(encoding="utf-8"))

    def sha(self, name: str) -> str:
        return hashlib.sha256((self.root / name).read_bytes()).hexdigest()

    def test_a_local_gate_records_the_digests_of_what_it_judged(self):
        data = self.run_gate(host=None)
        self.assertEqual(data["video_sha256"], self.sha("video.mp4"))
        self.assertEqual(data["scene_sha256"], self.sha("scene.xml"))

    def test_a_gate_on_a_host_hashes_the_local_files_it_shipped(self):
        data = self.run_gate(host="root@h")
        self.assertEqual(data["video_sha256"], self.sha("video.mp4"))
        self.assertEqual(data["scene_sha256"], self.sha("scene.xml"))

    def test_the_hash_is_doctors_byte_hash(self):
        self.assertEqual(doctor.scene_digest(self.root / "video.mp4"), self.sha("video.mp4"))
        self.assertEqual(doctor.scene_digest(self.root / "missing.mp4"), "")


if __name__ == "__main__":
    unittest.main()
