"""AUTO-82: `yt vfx` writes each section's record as the section finishes
(and a partial one after each gate), so a stop, kill or crash keeps every
finished section; 12-author is assembled from those files and `--recover`
reads them. Between rounds the loop reads `vfx/vNN/FINISH` (written by
`yt vfx --finish-now --title X`) or a SIGUSR1 flag and ends on the best
reviewed round with `stopped: finish`. The model is never called and the
render host is a stub."""

import io
import json
import os
import signal
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from ytstudio import cli, harness, vfx
from ytstudio.core import Idea, read_json
from ytstudio.research import Dossier
from tests.test_look_section import GROUPS, OUTLINE, TWO_DOC, Case
from tests.test_vfx_world import STYLE, WORLD_DOC, FakeEngine, varied_png


def scored(*scores):
    FakeEngine.reviews = [{"frames": [], "score": s, "summary": f"scored {s}"} for s in scores]


class Killed(FakeEngine):
    """An author killed on its n-th call: the process dies mid-section."""

    kill_on: int = 0

    def stream(self, prompt, system=None, resume=False):
        writes = "Write" in tuple(self.config.tools or ())
        authored = 1 + sum(1 for c in FakeEngine.stream_calls if "Write" in c["tools"])
        if writes and authored == Killed.kill_on:
            raise KeyboardInterrupt("killed")
        yield from super().stream(prompt, system=system, resume=resume)


class Finishing(FakeEngine):
    """An author whose n-th call is followed by the operator asking the run to
    finish: through the FINISH file or SIGUSR1."""

    finish_on: int = 0
    by_signal: bool = False
    planner = None

    def stream(self, prompt, system=None, resume=False):
        yield from super().stream(prompt, system=system, resume=resume)
        writes = "Write" in tuple(self.config.tools or ())
        authored = sum(1 for c in FakeEngine.stream_calls if "Write" in c["tools"])
        if writes and authored == Finishing.finish_on:
            if Finishing.by_signal:
                os.kill(os.getpid(), signal.SIGUSR1)
                Finishing.planner.finish.wait(5)
            else:
                vfx.finish_now(Path(self.config.cwd).parent.parent)


class Records(Case):
    def record(self, stem: str) -> dict | None:
        return read_json(self.root / "vfx" / "v01" / "rounds" / stem / "record.json", warn=False)

    def test_a_killed_run_keeps_the_records_of_the_sections_that_finished(self):
        harness.ENGINE = Killed
        Killed.kill_on = 2                                   # section 2's first session
        FakeEngine.docs = [WORLD_DOC]
        scored(8)
        with self.assertRaises(KeyboardInterrupt):
            self.author(self.planner(revisions=1, jobs=1))
        dossier = Dossier(self.root, self.idea, at=self.root)
        self.assertIsNone(dossier.load("12-author"))         # the run never saved its record
        one = self.record("01-open")
        self.assertEqual((one["n"], one["score"], one["finished"]), (1, 8, True))
        self.assertIsNone(self.record("02-two"))

        FakeEngine.stream_calls = []
        rebuilt = {r["n"]: r for r in self.planner(recover=True).recovered(OUTLINE, GROUPS)}
        self.assertEqual(FakeEngine.stream_calls, [])         # nothing authored, nothing asked
        self.assertEqual(rebuilt[1]["score"], 8)
        self.assertEqual(rebuilt[1]["budget"]["spent_usd"], one["budget"]["spent_usd"])
        self.assertTrue(rebuilt[1]["recovered"])
        self.assertTrue(rebuilt[2]["findings"][0].startswith("error: recovered"))

    def test_a_partial_record_follows_every_gate_and_recovers_on_the_best_round(self):
        harness.ENGINE = Killed
        Killed.kill_on = 2                                   # section 1's first revision
        FakeEngine.docs = [WORLD_DOC]
        scored(5)
        with self.assertRaises(KeyboardInterrupt):
            self.author(self.planner(revisions=2, jobs=1))
        partial = self.record("01-open")
        self.assertFalse(partial["finished"])
        self.assertEqual([r["score"] for r in partial["rounds"]], [5])
        # the document the kill interrupted is not what the record describes
        (self.root / "vfx" / "v01" / "01-open.scene.xml").write_text("<scene>half</scene>")
        rebuilt = {r["n"]: r for r in self.planner(recover=True).recovered(OUTLINE, GROUPS)}
        one = rebuilt[1]
        self.assertEqual((one["best_round"], one["score"]), (0, 5))
        self.assertTrue(one["recovered"])
        self.assertIn("#AAAAAA", (self.root / one["file"]).read_text())
        self.assertFalse([f for f in one["findings"] if f.startswith("error")])

    def test_the_record_is_assembled_from_the_files_and_the_files_match_it(self):
        FakeEngine.docs = [WORLD_DOC, TWO_DOC]
        scored(8, 8)
        authored = self.author(self.planner(revisions=0, jobs=1))
        for rec in authored:
            stem = Path(rec["file"]).name[:-len(".scene.xml")]
            self.assertEqual(self.record(stem), rec)
        saved = Dossier(self.root, self.idea, at=self.root).load("12-author")
        self.assertEqual(saved, authored)

    def test_a_record_is_written_whole(self):
        ws = vfx.Workspace(self.root, version="v01")
        name = "01-open.scene.xml"
        written: list[str] = []
        replace = os.replace

        def seen(src, dst):
            written.append(Path(src).name)
            replace(src, dst)
        with patch.object(vfx.os, "replace", seen):
            path = ws.write_record(name, {"n": 1, "score": 5})
        self.assertEqual(path, self.root / "vfx" / "v01" / "rounds" / "01-open" / "record.json")
        self.assertEqual(ws.read_record(name), {"n": 1, "score": 5})
        self.assertTrue(written and all(w != "record.json" for w in written))   # tmp, then renamed
        self.assertFalse(list(path.parent.glob("*.tmp*")))


class Finish(Case):
    def setUp(self):
        super().setUp()
        harness.ENGINE = Finishing
        Finishing.finish_on, Finishing.by_signal, Finishing.planner = 0, False, None

    def test_the_finish_file_ends_the_loop_on_the_best_reviewed_round(self):
        FakeEngine.docs = [WORLD_DOC, WORLD_DOC.replace("#AAAAAA", "#BBBBBB")]
        scored(5, 6, 9)
        Finishing.finish_on = 2                            # written after the first revision
        planner = self.planner(revisions=3, jobs=1, sections_only=None)
        doc = {r["n"]: r for r in self.author(planner)}[1]
        self.assertEqual([r["score"] for r in doc["rounds"]], [5, 6])
        self.assertEqual(doc["stopped"], "finish")
        self.assertEqual(doc["budget"]["stopped"], "finish")
        self.assertEqual((doc["best_round"], doc["score"]), (1, 6))
        self.assertIn("#BBBBBB", (self.root / doc["file"]).read_text())
        self.assertTrue((self.root / "vfx" / "v01" / vfx.FINISH_FILE).is_file())

    def test_the_signal_ends_the_loop_too(self):
        scored(5, 4, 9)
        Finishing.finish_on, Finishing.by_signal = 2, True
        planner = self.planner(revisions=3, jobs=1)
        Finishing.planner = planner
        doc = {r["n"]: r for r in self.author(planner)}[1]
        self.assertEqual(len(doc["rounds"]), 2)
        self.assertEqual(doc["stopped"], "finish")
        self.assertEqual(doc["best_round"], 0)
        self.assertEqual(signal.getsignal(signal.SIGUSR1), signal.SIG_DFL)   # restored

    def test_a_section_at_the_bar_is_done_not_finished(self):
        scored(8)
        Finishing.finish_on = 1
        doc = {r["n"]: r for r in self.author(self.planner(revisions=2, jobs=1))}[1]
        self.assertIsNone(doc["stopped"])


class Cli(unittest.TestCase):
    def test_finish_now_writes_the_file_into_the_active_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for v in ("v01", "v02"):
                (root / "vfx" / v).mkdir(parents=True)
            path = vfx.finish_now(root)
            self.assertEqual(path, root / "vfx" / "v02" / vfx.FINISH_FILE)
            self.assertTrue(path.is_file())
            with self.assertRaises(ValueError):
                vfx.finish_now(root / "none")

    def test_the_command_asks_the_run_to_finish_and_runs_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "vfx" / "v03").mkdir(parents=True)
            args = cli.build_parser().parse_args(
                ["vfx", "--project", tmp, "--finish-now", "--quiet"])
            out, err = io.StringIO(), io.StringIO()
            with patch.object(vfx, "planner_from_args",
                              lambda *a, **k: self.fail("the planner must not run")), \
                    redirect_stdout(out), redirect_stderr(err):
                code = vfx.run(args)
            self.assertEqual(code, 0)
            self.assertTrue((root / "vfx" / "v03" / vfx.FINISH_FILE).is_file())
            self.assertIn("v03", out.getvalue() + err.getvalue())
            self.assertNotIn("finished", err.getvalue())

    def test_the_command_says_when_the_newest_version_is_already_finished(self):
        # a run still planning has not opened its version: the file lands in
        # the last run's, whose record the dossier holds, and is never read
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "vfx" / "v03").mkdir(parents=True)
            Dossier(root, Idea(title="T", genre="g", description="d"),
                    at=root).save("12-author", [{"n": 1, "version": "v03", "score": 8}])
            args = cli.build_parser().parse_args(
                ["vfx", "--project", tmp, "--finish-now", "--quiet"])
            out, err = io.StringIO(), io.StringIO()
            with patch.object(vfx, "planner_from_args",
                              lambda *a, **k: self.fail("the planner must not run")), \
                    redirect_stdout(out), redirect_stderr(err):
                code = vfx.run(args)
            self.assertEqual(code, 0)
            self.assertIn("v03 (finished)", err.getvalue())
            self.assertIn("will not read", err.getvalue())


if __name__ == "__main__":
    unittest.main()
