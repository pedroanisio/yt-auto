"""The suite never reaches a render host, whatever .env names (AUTO-24)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("YT_NO_REMOTE", "1")

from ytstudio import assess, gate, geometry, render, vfx  # noqa: E402

HOSTS = {"RS_RENDER_HOSTS": "root@203.0.113.1", "YT_NO_REMOTE": "1"}
HOST = "root@203.0.113.1"


def _boom(*a, **k):
    raise AssertionError(f"a subprocess was started: {a[:1]}")


class Hermetic(unittest.TestCase):
    def test_flag_is_set_for_the_suite(self):
        self.assertEqual(os.environ.get("YT_NO_REMOTE"), "1")

    def test_guard_refuses_only_when_flagged(self):
        with mock.patch.dict(os.environ, {"YT_NO_REMOTE": "1"}):
            with self.assertRaises(render.RemoteRefused):
                render.refuse_remote("ssh")
        env = {k: v for k, v in os.environ.items() if k != "YT_NO_REMOTE"}
        with mock.patch.dict(os.environ, env, clear=True):
            render.refuse_remote("ssh")

    def test_hosts_in_env_route_nothing(self):
        with mock.patch.dict(os.environ, HOSTS):
            self.assertEqual(render.host_names(), [])
            self.assertEqual(render.remote_host(None), "")

    def test_every_launcher_refuses_without_starting_ssh(self):
        root = Path(tempfile.mkdtemp())
        plan = mock.MagicMock(syncs=[["rsync", "a", "b"]], host=HOST)
        calls = {
            "run_job": lambda: render.run_job(["scene-render", "x"], root, HOST, job="t"),
            "remove_remote": lambda: render.remove_remote(HOST, "/tmp/yt-render/a/b"),
            "ssh_alive": lambda: render.ssh_alive(HOST),
            "probe": lambda: render.probe("root@203.0.113.9"),
            "engine_validate": lambda: render.engine_validate(b"<x/>", host=HOST),
            "remote_frames": lambda: geometry.remote_frames(root, "d.xml", [0.0], HOST),
            "engine_facts": lambda: assess.engine_facts(HOST),
            "gate.run_remote": lambda: gate.run_remote(plan, {}, root, verbose=False,
                                                       keep_remote=False),
        }
        refused = {}
        # engine_validate runs a local binary when this machine has one, which is
        # a process but not ssh: hide it so the launcher under test is the remote one.
        with mock.patch.dict(os.environ, HOSTS), mock.patch.object(subprocess, "run", _boom), \
                mock.patch.object(subprocess, "Popen", _boom), \
                mock.patch.object(render, "engine", return_value=None):
            for name, call in calls.items():
                try:
                    call()
                except render.RemoteRefused:
                    refused[name] = True
                except AssertionError as exc:
                    self.fail(f"{name} started a process: {exc}")
                except Exception:
                    pass
        # the ones that must say so loudly rather than swallow it
        for name in ("run_job", "remove_remote", "ssh_alive", "probe", "remote_frames"):
            self.assertIn(name, refused)

    def test_validation_with_hosts_in_env_attempts_no_ssh(self):
        from ytstudio.scene_sdk import sdk
        S = sdk()
        sc = S.Scene(version="auto")
        sc.add_project(width=64, height=64, fps="30", duration=1, background="#000000")
        sc.add_symbols().add_symbol(id="s", width=64, height=64)
        sc.add_composition().add_instance(id="u", symbol="s")
        sc.add_output(path="p.mp4", codec="h264")
        ws = vfx.Workspace(Path(tempfile.mkdtemp()))
        name = ws.doc_name(1, {"heading": "Open"})
        (ws.dir / name).write_bytes(sc.to_bytes(layout="pretty"))
        seen = []
        real = subprocess.run

        def watch(argv, *a, **k):
            seen.append(argv[0] if isinstance(argv, (list, tuple)) else argv)
            if seen[-1] in ("ssh", "rsync"):
                raise AssertionError("ssh attempted")
            return real(argv, *a, **k)

        with mock.patch.dict(os.environ, HOSTS), mock.patch.object(subprocess, "run", watch):
            ws.validate(name)
        self.assertNotIn("ssh", seen)
        self.assertNotIn("rsync", seen)


if __name__ == "__main__":
    unittest.main()
