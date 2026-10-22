"""`yt run` walks the stages with fake runners: no model, no host.

Each stage's `run` is replaced by a fake that records when it ran and writes
the artifact that proves it did. The sound stage runs beside the keyframe and
vfx on its own thread, since only the build reads the cue sheet (AUTO-75).
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from ytstudio import cli, spend, workspace  # noqa: E402
from ytstudio import (build, keyframe, props, render, research, retime, script,  # noqa: E402
                      sound, styles, vfx, voice)

MODULES = {"research": research, "styles": styles, "script": script, "props": props,
           "voice": voice, "retime": retime, "keyframe": keyframe, "vfx": vfx,
           "sound": sound, "build": build, "render": render}


class RunCase(unittest.TestCase):
    """`yt run` against fake stages: each records when it ran, writes the
    artifact that proves it did and, when `costs` prices it, a manifest that
    reports that spend (`records` adds keys to it, such as a verdict)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "film"
        self.root.mkdir()
        self.order: list[str] = []
        self.codes: dict[str, int] = {}
        self.costs: dict[str, float] = {}
        self.records: dict[str, dict] = {}
        self.hooks: dict[str, callable] = {}
        self.seen: dict[str, object] = {}
        for stage, module in MODULES.items():
            patch = mock.patch.object(module, "run", self.fake(stage))
            patch.start()
            self.addCleanup(patch.stop)

    def fake(self, stage):
        artifact = dict(workspace.STAGES)[stage]

        def run(args):
            self.order.append(f"{stage}:start")
            self.seen[stage] = args
            if stage in self.hooks:
                self.hooks[stage]()
            record = {"usage": spend.record(self.costs.get(stage, 0.0), "fake", 1),
                      **self.records.get(stage, {})}
            (self.root / workspace.ARTIFACTS[artifact]).write_text(json.dumps(record) + "\n")
            if stage in spend.MANIFEST:
                (self.root / spend.MANIFEST[stage]).write_text(json.dumps(record) + "\n")
            self.order.append(f"{stage}:end")
            return self.codes.get(stage, 0)
        return run

    def run_yt(self, *flags: str) -> int:
        args = cli.build_parser().parse_args(
            ["run", "--project", str(self.root), "--quiet", *flags])
        with redirect_stderr(io.StringIO()) as err, redirect_stdout(io.StringIO()) as out:
            code = args.run(args)
        self.err, self.out = err.getvalue(), out.getvalue()
        return code

    def started(self, stage: str) -> list[str]:
        return [s for s in self.order if s.startswith(stage + ":")]


class Beside(RunCase):
    def test_sound_starts_before_the_keyframe_returns_and_build_waits_for_it(self):
        sound_started, vfx_done = threading.Event(), threading.Event()
        seen = {}

        def in_keyframe():
            seen["sound_running"] = sound_started.wait(timeout=5)

        def in_sound():
            sound_started.set()
            # Sound takes longer than the keyframe and vfx together.
            vfx_done.wait(timeout=5)

        self.hooks.update(keyframe=in_keyframe, sound=in_sound, vfx=vfx_done.set)
        self.assertEqual(self.run_yt("--skip", "render"), 0)
        self.assertTrue(seen.get("sound_running"), self.order)
        # vfx started while sound was still running; the build waited for it.
        self.assertLess(self.order.index("vfx:start"), self.order.index("sound:end"))
        self.assertLess(self.order.index("sound:end"), self.order.index("build:start"))
        self.assertEqual(self.started("sound"), ["sound:start", "sound:end"])

    def test_a_failed_sound_stage_fails_the_run_before_build(self):
        self.codes["sound"] = 2
        self.assertEqual(self.run_yt(), 2)
        self.assertIn("sound exited 2", self.err)
        self.assertEqual(self.started("build"), [])
        self.assertEqual(self.started("vfx"), ["vfx:start", "vfx:end"])

    def test_a_sound_stage_that_crashes_beside_the_walk_is_a_failure_not_a_hang(self):
        def boom():
            raise RuntimeError("the cue sheet")
        self.hooks["sound"] = boom
        self.assertNotEqual(self.run_yt(), 0)
        self.assertIn("the cue sheet", self.err)
        self.assertEqual(self.started("build"), [])

    def test_sound_is_joined_when_the_walk_stops_early(self):
        self.codes["vfx"] = 1
        self.assertEqual(self.run_yt(), 1)
        self.assertEqual(self.started("sound"), ["sound:start", "sound:end"])

    def test_a_ctrl_c_in_the_keyframe_ends_the_run_without_waiting_for_sound(self):
        # The interpreter waits on a non-daemon thread, so before this a
        # Ctrl-C printed the traceback and the sound stage spent on for the
        # rest of its hour. The thread is a daemon: the interrupt surfaces at
        # once, `main` prints "interrupted", and sound goes with the process.
        self.assertTrue(cli.Beside("sound", lambda: 0).daemon)
        release = threading.Event()

        def interrupted():
            raise KeyboardInterrupt

        def let_sound_finish():
            release.set()
            for thread in threading.enumerate():
                if thread.name == "yt sound":
                    thread.join(timeout=5)

        self.hooks.update(keyframe=interrupted, sound=lambda: release.wait(timeout=5))
        self.addCleanup(let_sound_finish)
        started = time.monotonic()
        with self.assertRaises(KeyboardInterrupt):
            self.run_yt("--skip", "render")
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(self.started("sound"), ["sound:start"])

    def test_a_system_exit_on_the_sound_thread_counts_with_its_code(self):
        # argparse rejecting the sound argv raises SystemExit on that thread;
        # threading drops it silently, so it is caught and its code kept.
        def usage():
            raise SystemExit(2)
        self.hooks["sound"] = usage
        self.assertEqual(self.run_yt(), 2)
        self.assertIn("sound exited 2", self.err)
        self.assertEqual(self.started("build"), [])


class SkipAndRedo(RunCase):
    def test_a_skipped_sound_stage_never_starts(self):
        self.assertEqual(self.run_yt("--skip", "sound,render"), 0)
        self.assertEqual(self.started("sound"), [])
        self.assertEqual(self.started("build"), ["build:start", "build:end"])

    def test_a_sound_sheet_already_there_is_not_redone_unless_asked(self):
        (self.root / "sound.json").write_text("{}\n")
        self.assertEqual(self.run_yt("--skip", "render"), 0)
        self.assertEqual(self.started("sound"), [])
        self.assertIn("sound: already done", self.err)
        self.order.clear()
        self.assertEqual(self.run_yt("--redo", "sound", "--skip", "render"), 0)
        self.assertEqual(self.started("sound"), ["sound:start", "sound:end"])
        self.assertEqual(self.started("vfx"), [])

    def test_the_walk_is_in_pipeline_order_with_sound_beside_the_keyframe(self):
        self.assertEqual(self.run_yt(), 0)
        serial = [s[:-6] for s in self.order if s.endswith(":start") and s != "sound:start"]
        self.assertEqual(serial, [s for s in cli.PIPELINE if s != "sound"])
        self.assertLess(self.order.index("sound:start"), self.order.index("keyframe:end"))


if __name__ == "__main__":
    unittest.main()
