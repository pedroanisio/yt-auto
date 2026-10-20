"""yt keeps the machine awake while a stage waits on a model or render host."""

import unittest
from unittest import mock

from ytstudio import cli, inhibit


class FakeProc:
    def __init__(self):
        self.stdin = mock.Mock()
        self.terminated = False
        self.waited = False

    def wait(self, timeout=None):
        self.waited = True
        return 0

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True


class HeldTests(unittest.TestCase):
    def setUp(self):
        self.procs = []

        def popen(argv, **kw):
            self.argv = argv
            p = FakeProc()
            self.procs.append(p)
            return p
        self.popen = mock.patch.object(inhibit.subprocess, "Popen", popen)
        self.which = mock.patch.object(inhibit.shutil, "which",
                                       lambda n: "/usr/bin/" + n)
        self.popen.start(); self.which.start()
        self.addCleanup(self.popen.stop); self.addCleanup(self.which.stop)

    def test_taken_then_released(self):
        with inhibit.held("stage x"):
            self.assertEqual(len(self.procs), 1)
            self.assertFalse(self.procs[0].waited)
        self.assertIn("--what=sleep:idle", self.argv)
        self.assertEqual(self.argv[0], "systemd-inhibit")
        self.procs[0].stdin.close.assert_called()
        self.assertTrue(self.procs[0].waited)

    def test_released_on_error(self):
        with self.assertRaises(RuntimeError):
            with inhibit.held("x"):
                raise RuntimeError
        self.assertTrue(self.procs[0].waited)

    def test_missing_tool_does_not_block(self):
        with mock.patch.object(inhibit.shutil, "which", lambda n: None):
            ran = []
            with inhibit.held("x"):
                ran.append(1)
        self.assertEqual(ran, [1])
        self.assertEqual(self.procs, [])

    def test_main_holds_for_model_stage_only(self):
        seen = []

        def run(args):
            seen.append(len(self.procs) - sum(p.waited for p in self.procs))
            return 0
        for argv, expect in ((["doctor"], 1), (["ls"], 0)):
            seen.clear(); self.procs.clear()
            parser = cli.build_parser()
            real = parser.parse_args(argv)
            real.run = run
            with mock.patch.object(cli, "build_parser",
                                   return_value=mock.Mock(parse_args=lambda a: real)):
                cli.main(argv)
            self.assertEqual(seen, [expect], argv)
            self.assertTrue(all(p.waited for p in self.procs))


if __name__ == "__main__":
    unittest.main()
