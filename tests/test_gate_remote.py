"""`yt gate` on a render host, and what it says when the gate stops early.

The workstation has neither ffprobe nor the engine, by policy, and both render
hosts have both; so the gate ships the candidate, its envelope and the two
tools directories to a host, rewrites every path for the copy there, runs
production_gate.py with the host's engine and ffprobe, and pulls its output
back. Nothing here opens an ssh connection: the plan is a pure value.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import hermetic  # noqa: E402
from ytstudio import gate  # noqa: E402
from ytstudio import render  # noqa: E402

ROOT = Path("/w/monty")
FOUND = {
    "scene": "/w/monty/scene.xml",
    "video": "/w/monty/video.mp4",
    "encode report": "/w/monty/render.json",
    "captions": "/w/monty/narration.vtt",
    "engine": "/root/render/scene-render-v0.6.0-linux-x86_64/scene-render",
    "gate": "/src/scene-render/tools/production_gate.py",
    "tools dir": "/src/pnkit/tools",
    "voice end": 15.0,
}


def _plan(**over):
    found = FOUND | over.pop("found", {})
    return gate.plan_remote(found, over.pop("extras", ["/scratch/empty.vtt"]), ROOT, "root@h",
                            tag="77-abc123", **over)


class RemotePlan(unittest.TestCase):
    def test_the_job_has_a_directory_of_its_own(self):
        plan = _plan()
        self.assertEqual(plan.host, "root@h")
        self.assertEqual(plan.root, f"{gate.REMOTE_GATE_ROOT}/monty/gate-77-abc123")
        self.assertEqual(plan.project, f"{plan.root}/monty")

    def test_paths_inside_the_project_map_under_the_copy(self):
        plan = _plan()
        self.assertEqual(plan.paths["/w/monty/scene.xml"], f"{plan.project}/scene.xml")
        self.assertEqual(plan.paths["/w/monty/video.mp4"], f"{plan.project}/video.mp4")
        self.assertEqual(plan.paths["/w/monty/narration.vtt"], f"{plan.project}/narration.vtt")

    def test_paths_outside_the_project_go_to_the_inputs_directory(self):
        plan = _plan(found={"video": "/elsewhere/cut.mp4"})
        self.assertEqual(plan.paths["/scratch/empty.vtt"], f"{plan.root}/inputs/empty.vtt")
        self.assertEqual(plan.paths["/elsewhere/cut.mp4"], f"{plan.root}/inputs/cut.mp4")

    def test_the_envelope_is_always_the_rewritten_copy_in_inputs(self):
        plan = _plan()
        self.assertEqual(plan.paths["/w/monty/render.json"], f"{plan.root}/inputs/render.json")

    def test_command_runs_the_host_gate_with_its_engine_and_remote_paths(self):
        plan = _plan()
        root = plan.root
        self.assertEqual(plan.command, [
            "python3", f"{root}/tools/gate/production_gate.py", "technical", plan.project,
            "--scene", f"{plan.project}/scene.xml", "--video", f"{plan.project}/video.mp4",
            "--engine", FOUND["engine"], "--encode-report", f"{root}/inputs/render.json",
            "--tools-dir", f"{root}/tools/audio", "--voice-end", "15.0",
            "--loudness-target", "-14.0",
            "--captions", f"{plan.project}/narration.vtt", "--captions", f"{root}/inputs/empty.vtt",
            "--tail-check", f"{root}/tools/audio/tail_check.py"])

    def test_max_illegal_and_frames_mode(self):
        plan = _plan(max_illegal=0.02)
        self.assertEqual(plan.command[-2:], ["--max-illegal", "0.02"])
        plan = _plan(frames=True)
        self.assertEqual(plan.command, ["python3", f"{plan.root}/tools/gate/production_gate.py",
                                        "frames", f"{plan.root}/inputs/render.json"])

    def test_syncs_ship_the_project_the_two_tools_and_the_extras(self):
        plan = _plan()
        dests = [argv[-1] for argv in plan.syncs]
        self.assertEqual(dests, [f"root@h:{plan.project}/",
                                 f"root@h:{plan.root}/tools/gate/",
                                 f"root@h:{plan.root}/tools/audio/",
                                 f"root@h:{plan.root}/tools/audio/",
                                 f"root@h:{plan.root}/inputs/empty.vtt"])
        sources = [argv[-2] for argv in plan.syncs]
        # the last tools sync is yt's tail check and its library, over the tools dir's copy
        self.assertEqual(sources, ["/w/monty/", "/src/scene-render/tools/", "/src/pnkit/tools/",
                                   str(gate.bundled_tools()) + "/", "/scratch/empty.vtt"])
        for argv in plan.syncs:
            self.assertEqual(argv[0], "rsync")
            self.assertNotIn("--delete", argv)

    def test_the_project_sync_leaves_the_heavy_byproducts_behind_but_keeps_the_candidate(self):
        plan = _plan(found={"video": "/w/monty/video-draft.mp4"})
        project = plan.syncs[0]
        for pattern in ("qa/", "raw/", "review-frames/", "*.pdf", "video-*.mp4"):
            self.assertIn(f"--exclude={pattern}", project)
        # the candidate is named before the exclusions, so rsync keeps it
        self.assertIn("--include=video-draft.mp4", project)
        self.assertLess(project.index("--include=video-draft.mp4"), project.index("--exclude=video-*.mp4"))

    def test_the_envelope_is_rewritten_for_the_host(self):
        plan = _plan(found={"video": "/elsewhere/cut.mp4"})
        report = {"format": "scene-render-cli/1", "result": {"outputs": [
            {"path": "/w/monty/video.mp4", "files": ["/w/monty/video.mp4"]},
            {"path": "/elsewhere/cut.mp4", "files": ["/elsewhere/cut.mp4"]}]}}
        out = gate.remote_envelope(report, plan)
        outputs = out["result"]["outputs"]
        self.assertEqual(outputs[0]["path"], f"{plan.project}/video.mp4")
        self.assertEqual(outputs[1]["path"], f"{plan.root}/inputs/cut.mp4")
        self.assertEqual(outputs[1]["files"], [f"{plan.root}/inputs/cut.mp4"])
        self.assertEqual(report["result"]["outputs"][0]["path"], "/w/monty/video.mp4")  # untouched

    def test_the_remote_script_runs_the_command_and_reports_its_exit(self):
        plan = _plan()
        script = gate.remote_script(plan)
        self.assertTrue(script.startswith(f"cd {plan.root} && "))
        self.assertIn("python3 " + f"{plan.root}/tools/gate/production_gate.py technical", script)
        self.assertIn("--voice-end 15.0", script)


class RemoteRun(unittest.TestCase):
    """Every ssh and rsync the gate's run makes carries the connect timeout and
    BatchMode (AUTO-69): the pull of qa/TECHNICAL.json too, which a sshd that
    hangs between the gate's ssh and the pull held for good."""

    def setUp(self):
        hermetic.fake_remote(self)
        self.ran: list[list[str]] = []

    def run_gate(self):
        plan = gate.RemotePlan(host="root@h", root="/r/p/gate-1", project="/r/p/gate-1/p",
                               engine="/bin/e", local_root="/x/p", command=["gate"],
                               syncs=[render.sync_up_args(ROOT, "root@h", "/r/p/gate-1/p", delete=False)])

        def fake(argv, **kw):
            self.ran.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(gate.subprocess, "run", side_effect=fake), \
                mock.patch.object(gate.render_stage, "remove_remote"):
            gate.run_remote(plan, {}, Path(tmp), verbose=False, keep_remote=False)
        return self.ran

    def test_every_ssh_and_rsync_carries_the_connect_timeout(self):
        ran = self.run_gate()
        pulls = [a for a in ran if a[0] == "rsync" and a[-1].endswith("TECHNICAL.json")]
        self.assertEqual(len(pulls), 1, ran)
        for argv in ran:
            if argv[0] == "ssh":
                self.assertIn("ConnectTimeout=10", argv)
                self.assertIn("BatchMode=yes", argv)
            else:
                self.assertEqual(argv[0], "rsync")
                self.assertIn("-e", argv, argv)
                command = argv[argv.index("-e") + 1]
                self.assertIn("ConnectTimeout=10", command)
                self.assertIn("BatchMode=yes", command)


class HostChoice(unittest.TestCase):
    def test_an_explicit_host_wins(self):
        self.assertEqual(gate.gate_host(argparse.Namespace(host="root@x", local=False), have_local=True),
                         "root@x")

    def test_local_stays_local(self):
        self.assertEqual(gate.gate_host(argparse.Namespace(host="root@x", local=True), have_local=False), "")

    def test_local_tools_run_here(self):
        with mock.patch.object(render, "remote_host") as remote:
            self.assertEqual(gate.gate_host(argparse.Namespace(host=None, local=False), have_local=True), "")
            remote.assert_not_called()

    def test_without_local_tools_the_gate_routes_like_an_encode(self):
        with mock.patch.object(render, "remote_host", return_value="root@big") as remote:
            args = argparse.Namespace(host=None, local=False)
            self.assertEqual(gate.gate_host(args, have_local=False), "root@big")
            remote.assert_called_once_with(args, "encode")


class StoppedBy(unittest.TestCase):
    """When the gate stops early, the summary names the requirement and its owner."""

    def test_evaluation_warnings(self):
        stop = gate.stopped_by("NOT READY: Encode report has evaluation_warnings; resolve before release")
        self.assertEqual(stop["requirement"], "evaluation_warnings")
        self.assertEqual(stop["owner"], "build")
        self.assertIn("yt build", stop["fix"])
        self.assertEqual(stop["message"], "Encode report has evaluation_warnings; resolve before release")

    def test_identification(self):
        stop = gate.stopped_by("x\nNOT READY: Encode report must identify the candidate exactly once\n")
        self.assertEqual(stop["requirement"], "identification")
        self.assertEqual(stop["owner"], "render")

    def test_envelope(self):
        for text in ("Encode report must be the scene-render-cli/1 envelope of `scene-render encode --json`",
                     "Encode report is not one JSON document: Expecting value",
                     "Encode report is not a successful encode: no error recorded",
                     "Encode report has no result.outputs list"):
            with self.subTest(text=text):
                stop = gate.stopped_by(f"NOT READY: {text}")
                self.assertEqual(stop["requirement"], "envelope")
                self.assertEqual(stop["owner"], "render")

    def test_a_failed_check_names_the_tool_that_owns_it(self):
        stop = gate.stopped_by("NOT READY: Check failed (exit 1): /x/tools/audio/sr-audiocheck /p video.mp4\nboom")
        self.assertEqual(stop["requirement"], "sr-audiocheck")
        self.assertEqual(stop["owner"], "sound")
        stop = gate.stopped_by("NOT READY: Check failed (exit 1): /r/scene-render validate scene.xml --deny-warnings")
        self.assertEqual(stop["requirement"], "validate")
        self.assertEqual(stop["owner"], "build")

    def test_captions_and_voice(self):
        self.assertEqual(gate.stopped_by("NOT READY: No caption cues found")["owner"], "voice")
        self.assertEqual(gate.stopped_by("NOT READY: Last spoken word must end within the final video")["owner"],
                         "voice")

    def test_a_missing_tool_is_the_environment_not_a_stage(self):
        stop = gate.stopped_by("NOT READY: [Errno 2] No such file or directory: 'ffprobe'")
        self.assertEqual(stop["owner"], "environment")
        self.assertIn("--host", stop["fix"])

    def test_a_pass_or_an_unknown_line_stops_nothing(self):
        self.assertIsNone(gate.stopped_by("TECHNICAL PASS; perceptual review still required"))
        self.assertIsNone(gate.stopped_by(""))

    def test_an_unlisted_requirement_is_still_reported(self):
        stop = gate.stopped_by("NOT READY: Something new the tool checks")
        self.assertEqual(stop["requirement"], "Something new the tool checks")
        self.assertEqual(stop["owner"], "?")


if __name__ == "__main__":
    unittest.main()
