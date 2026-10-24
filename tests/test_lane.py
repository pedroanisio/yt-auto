"""A yt video as a govern lane: stages whose exit checks are yt's evidence.

govern runs any process through checked stages, holds gates for the owner's
own words and keeps one ledger of tokens and machine time; yt knows what a
video's stages are and what proves each one done. `ytstudio.lane` is the
seam: `process()` generates the govern process from `workspace.STAGES` (so
the two cannot drift: .govern/processes/yt-video.json must equal it), and
`check()` is every stage's exit check, answered from the same files `yt
doctor` reads.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ytstudio import doctor, lane, workspace  # noqa: E402
from ytstudio.core import write_json  # noqa: E402

PROCESS_FILE = ROOT / ".govern" / "processes" / "yt-video.json"
GOVERN = Path.home() / "src" / "super-tools" / "bin" / "govern"


class Process(unittest.TestCase):
    def setUp(self):
        self.proc = lane.process()

    def test_every_pipeline_stage_is_a_checked_stage_in_order(self):
        names = [s["name"] for s in self.proc["stages"]]
        self.assertEqual(names[:len(workspace.STAGES)], [s for s, _ in workspace.STAGES])
        self.assertEqual(names[-1], "deliver")
        for stage in self.proc["stages"]:
            self.assertEqual(stage.get("requires"), "check", stage["name"])
            self.assertTrue(stage.get("exit"), stage["name"])

    def test_every_check_names_a_command_the_lane_starts_with(self):
        commands = {}
        for group in self.proc["setup"]:
            commands.update(group.get("commands", {}))
        for stage in self.proc["stages"]:
            argv = commands[stage["check"]]
            # govern runs a lane's commands in the lane's folder, so the
            # module is named by its path, not imported from the cwd
            self.assertEqual(argv[:2], ["{python}", "{repo}/ytstudio/lane.py"])
            self.assertIn("{dir}", argv)
            self.assertEqual(argv[-1], stage["name"])

    def test_the_owner_gates_the_script_and_the_final_cut(self):
        gates = {g["name"]: g for g in self.proc["gates"]}
        self.assertEqual(gates["script"]["stage"], "script")
        self.assertEqual(gates["final"]["stage"], "deliver")
        self.assertEqual(gates["final"]["needs"], ["script"])

    def test_the_manual_is_the_operators_manual(self):
        manual = (PROCESS_FILE.parent / self.proc["manual"]).resolve()
        self.assertEqual(manual, (ROOT / "ytstudio" / "SKILL.md").resolve())

    def test_the_workspace_file_is_the_generated_process(self):
        self.assertTrue(PROCESS_FILE.is_file(), "run: python3 ytstudio/lane.py process --write")
        on_disk = json.loads(PROCESS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(on_disk, self.proc,
                         "the stages changed; run: python3 ytstudio/lane.py process --write")

    @unittest.skipUnless(GOVERN.exists() and shutil.which("node"), "govern is not installed")
    def test_govern_parses_it_strictly(self):
        proc = subprocess.run([str(GOVERN), "process", "show", "yt-video"],
                              cwd=ROOT, capture_output=True, text=True, timeout=60,
                              env=dict(os.environ, GOVERN_REPO=str(ROOT)))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        for stage, _ in workspace.STAGES:
            self.assertIn(stage, proc.stdout)


class Check(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name) / "work"
        self.root = self.work / "a-film"
        self.root.mkdir(parents=True)
        write_json(self.root / "idea.json", {"title": "A film", "genre": "g",
                                             "description": "d"})
        # the lane folder govern hands over is .govern/projects/<slug>
        self.lane_dir = Path(self._tmp.name) / ".govern" / "projects" / "a-film"
        self.lane_dir.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def check(self, stage):
        return lane.check(self.lane_dir, stage, work=self.work)

    def test_the_lane_folder_names_the_project(self):
        self.assertEqual(lane.project_for(self.lane_dir, work=self.work).root, self.root)

    def test_a_stage_without_its_artifact_is_not_done(self):
        ok, lines = self.check("styles")
        self.assertFalse(ok)
        self.assertIn("styles.json", "\n".join(lines))

    def test_a_stage_with_its_artifact_and_no_fail_is_done(self):
        write_json(self.root / "styles.json", {"styles": []})
        ok, _ = self.check("styles")
        self.assertTrue(ok)

    def test_a_film_in_the_fallback_palette_leaves_the_styles_stage_done(self):
        # AUTO-155: before a render the fallback is a styles warn, never a
        # fail, so the lane (and `yt run`) does not loop on the styles stage
        from ytstudio import doctor
        write_json(self.root / "styles.json", {"styles": []})
        (self.root / "scene.xml").write_text("<scene/>")
        write_json(self.root / "build.json", {
            "findings": [], "layers": {}, "timeline": {},
            "design": {"treatment": "", "source": "fallback", "roles": {},
                       "fallback": "styles.json lists no treatment"}})
        rows = [f for f in doctor.check_project(workspace.Project(self.root)).findings
                if "fallback palette" in f.message]
        self.assertEqual([(f.level, f.area) for f in rows], [("warn", "styles")])
        ok, lines = self.check("styles")
        self.assertTrue(ok, lines)

    def test_a_fail_in_the_stages_area_keeps_it_open(self):
        write_json(self.root / "scene.xml", {})
        write_json(self.root / "build.json", {"findings": ["error: the include is missing"],
                                              "layers": {}, "timeline": {}})
        ok, lines = self.check("build")
        self.assertFalse(ok)
        self.assertIn("include is missing", "\n".join(lines))

    def test_deliver_is_done_only_on_the_delivery_evidence(self):
        ok, lines = self.check("deliver")
        self.assertFalse(ok)
        write_json(self.root / "build.json", {"scene": {"duration": 10.0}, "audio_tracks": 2,
                                              "findings": [], "layers": {}, "timeline": {}})
        write_json(self.root / "render.json", {"ok": True, "result": {"outputs": [{
            "quality": "final", "range": [0, 10], "warnings": [], "findings": []}]}})
        (self.root / "video.mp4").write_bytes(b"\x00" * 64)
        stamp = time.time() - 100
        for n, name in enumerate(("render.json", "video.mp4")):
            os.utime(self.root / name, (stamp + n, stamp + n))
        write_json(self.root / "gate.json", {"mode": "technical", "exit": 0,
                                             "triage": {"verdict": "deliverable"}})
        write_json(self.root / "qa.json", {"pass": "pass-02", "verdict": "READY TO PUBLISH",
                                           "overall": 9.1})
        # AUTO-147: and a current, ready upload set from `yt package`
        ok, lines = self.check("deliver")
        self.assertFalse(ok)
        self.assertEqual([ln for ln in lines if ln.startswith("missing:")],
                         [ln for ln in lines if "package" in ln])
        self.assertTrue(lines)
        (self.root / "package").mkdir()
        write_json(self.root / "package" / "package.json", {
            "verdict": "ready", "findings": [], "scene_sha256": "",
            "video_sha256": doctor.scene_digest(self.root / "video.mp4")})
        ok, lines = self.check("deliver")
        self.assertTrue(ok, lines)

    def test_the_deliver_exit_names_the_package(self):
        deliver = next(s for s in lane.process()["stages"] if s["name"] == "deliver")
        self.assertIn("yt package", deliver["exit"])
        self.assertIn("yt package", lane.__doc__)

    def test_an_unknown_stage_is_an_error_not_a_pass(self):
        with self.assertRaises(ValueError):
            self.check("publish")

    def test_the_command_line_exits_by_the_verdict_from_the_lane_folder(self):
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        argv = [sys.executable, str(ROOT / "ytstudio" / "lane.py"), "check",
                str(self.lane_dir), "styles", "--work", str(self.work)]
        run = lambda: subprocess.run(argv, cwd=self.lane_dir, env=env,  # noqa: E731
                                     capture_output=True, text=True)
        first = run()
        self.assertEqual(first.returncode, 1, first.stderr)
        write_json(self.root / "styles.json", {"styles": []})
        self.assertEqual(run().returncode, 0)


if __name__ == "__main__":
    unittest.main()
