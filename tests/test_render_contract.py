"""The render stage's contracts with the other stages and with the hosts.

Each of these was broken once, on a real project, the same day:

- two jobs on one project shared a remote directory and one overwrote the
  other's finished film before it was pulled back;
- qa and pilot call run() with a partial Namespace, and a flag added to the
  CLI crashed them with AttributeError;
- a still, a validation and a pilot output all wrote render.json, clobbering
  the encode report the delivery gate reads;
- the report pulled back named paths under the host's scratch directory, so
  the gate could not match the candidate.

No test here opens an ssh connection: the remote work is factored into pure
functions (path derivation, rsync argument building, pull-back selection) and
subprocess.run is replaced where a whole run is exercised.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import render  # noqa: E402

MARK = "\n__YT_JOB_EXIT__=0\n"      # the attach's final line; only a marker settles a job


class RemoteDirectories(unittest.TestCase):
    """Every job gets a directory of its own under REMOTE_ROOT/<slug>/."""

    def test_job_directory_is_scoped_by_slug_and_job(self):
        self.assertEqual(render.remote_dir("monty", "encode", "4242-ab12cd"),
                         f"{render.REMOTE_ROOT}/monty/encode-4242-ab12cd")

    def test_two_jobs_on_one_project_never_share_a_directory(self):
        a, b = render.job_tag(), render.job_tag()
        self.assertNotEqual(a, b)
        self.assertNotEqual(render.remote_dir("monty", "still", a),
                            render.remote_dir("monty", "still", b))
        # the pid is in the tag so a stray directory can be traced to a process
        self.assertTrue(a.startswith(f"{render.os.getpid()}-"))

    def test_sync_up_creates_the_directory_and_ships_the_project(self):
        argv = render.sync_up_args(Path("/w/monty"), "root@h", "/tmp/yt-render/monty/encode-1-a")
        self.assertEqual(argv[0], "rsync")
        self.assertEqual(argv[-2:], ["/w/monty/", "root@h:/tmp/yt-render/monty/encode-1-a/"])
        self.assertIn("--delete", argv)
        for pattern in render.SYNC_EXCLUDES:
            self.assertIn(f"--exclude={pattern}", argv)
        # the parent is made on the host in the same trip: no separate ssh
        mkdir = [a for a in argv if a.startswith("--rsync-path=")]
        self.assertEqual(len(mkdir), 1)
        self.assertIn("mkdir -p /tmp/yt-render/monty/encode-1-a", mkdir[0])

    def test_sync_up_can_ship_one_file_with_extra_rules(self):
        argv = render.sync_up_args(Path("/s/empty.vtt"), "root@h", "/tmp/yt-gate/x/inputs/empty.vtt",
                                   file=True, delete=False, excludes=(), includes=("video-draft.mp4",))
        self.assertNotIn("--delete", argv)
        self.assertEqual(argv[-2:], ["/s/empty.vtt", "root@h:/tmp/yt-gate/x/inputs/empty.vtt"])
        self.assertIn("--include=video-draft.mp4", argv)

    def test_pull_back_selects_results_and_nothing_else(self):
        argv = render.pull_back_args("root@h", "/tmp/yt-render/monty/still-1-a", Path("/w/monty"))
        self.assertEqual(argv[-2:], ["root@h:/tmp/yt-render/monty/still-1-a/", "/w/monty/"])
        for pattern in render.PULL_BACK:
            self.assertIn(f"--include={pattern}", argv)
        self.assertEqual(argv[-3], "--exclude=*")
        self.assertIn("--include=*/", argv)

    def test_the_pull_back_patterns_are_kept(self):
        for pattern in ("*.mp4", "*.png", "*.wav", "render.json", "render-*.json", "pilot*.json"):
            self.assertIn(pattern, render.PULL_BACK)

    def test_remove_remote_refuses_anything_outside_the_scratch_roots(self):
        with mock.patch.object(render.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                render.remove_remote("root@h", "/root")
            with self.assertRaises(ValueError):
                render.remove_remote("root@h", "/tmp/yt-render")
            run.assert_not_called()

    def test_remove_remote_removes_one_job_directory(self):
        with mock.patch.object(render.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            render.remove_remote("root@h", "/tmp/yt-render/monty/encode-1-a")
            argv = run.call_args[0][0]
            self.assertEqual(argv[0], "ssh")
            self.assertEqual(argv[-2], "root@h")
            self.assertIn("rm -rf /tmp/yt-render/monty/encode-1-a", argv[-1])


class Mirror(unittest.TestCase):
    """One persistent mirror per slug on each host; a job is hardlinked from it."""

    def test_the_mirror_lives_beside_the_job_directories(self):
        self.assertEqual(render.mirror_dir("monty"), f"{render.REMOTE_ROOT}/monty/mirror")

    def test_the_upload_leaves_behind_what_no_render_needs(self):
        for pattern in ("qa/", "decisions/", "review-frames/", "raw/", "*.bak", ".cache/",
                        "video*.mp4", "pilot*.mp4", "vfx/frames/", "vfx/v*/frames/"):
            self.assertIn(pattern, render.SYNC_EXCLUDES)
        # the scene references these: they must travel
        for kept in ("props", "sound", "vfx"):
            self.assertFalse(any(p.rstrip("/").split("/")[0] == kept and p.count("/") == 1
                                 for p in render.SYNC_EXCLUDES), kept)

    def test_a_job_is_linked_from_the_mirror_with_a_rsync_fallback(self):
        script = render.link_job_script("/tmp/yt-render/monty/mirror", "/tmp/yt-render/monty/still-1-a")
        self.assertIn("cp -al /tmp/yt-render/monty/mirror /tmp/yt-render/monty/still-1-a", script)
        self.assertIn("rsync -a --link-dest=/tmp/yt-render/monty/mirror /tmp/yt-render/monty/mirror/ "
                      "/tmp/yt-render/monty/still-1-a/", script)
        self.assertLess(script.index("cp -al"), script.index("--link-dest"))

    def test_prune_removes_only_the_slugs_mirror_on_every_host(self):
        seen = []
        with mock.patch.object(render.subprocess, "run",
                               side_effect=lambda argv, **kw: (seen.append(list(argv)) or
                                                               subprocess.CompletedProcess(argv, 0, "", ""))):
            render.prune_remote(["root@a", "root@b"], "monty")
        self.assertEqual([a[-2] for a in seen], ["root@a", "root@b"])
        for argv in seen:
            self.assertIn("rm -rf /tmp/yt-render/monty/mirror", argv[-1])
            self.assertNotIn("still-", argv[-1])


class RemoteRun(unittest.TestCase):
    """_run_remote: up, run, back, remove -- in that order, in its own directory."""

    def _calls(self, keep_remote: bool):
        seen: list[list[str]] = []

        def fake(argv, **kw):
            seen.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, stdout="{}" + MARK * (argv[0] == "ssh"),
                                               stderr="")

        with mock.patch.object(render.subprocess, "run", side_effect=fake):
            render._HOSTS["root@h"] = render.Host("root@h", engine="/opt/sr/scene-render")
            try:
                proc = render._run_remote(["scene-render", "render", "/w/monty/scene.xml", "-o",
                                           "/w/monty/still.png", "--time", "7.5"],
                                          Path("/w/monty"), "root@h", verbose=False,
                                          remote="/tmp/yt-render/monty/still-1-a",
                                          keep_remote=keep_remote)
            finally:
                render._HOSTS.pop("root@h", None)
        self.assertEqual(proc.returncode, 0)
        return seen

    def test_runs_inside_the_job_directory_and_removes_it(self):
        seen = self._calls(keep_remote=False)
        kinds = [argv[0] for argv in seen]
        self.assertEqual(kinds, ["rsync", "ssh", "rsync", "ssh"])
        up, run, back, rm = seen
        # the upload refreshes the slug's mirror, incrementally; the job is
        # hardlinked from it and the engine runs there, in the same ssh
        self.assertEqual(up[-1], "root@h:/tmp/yt-render/monty/mirror/")
        self.assertIn("--delete", up)
        self.assertIn("cp -al /tmp/yt-render/monty/mirror /tmp/yt-render/monty/still-1-a", run[-1])
        self.assertIn("cd /tmp/yt-render/monty/still-1-a && /opt/sr/scene-render render scene.xml "
                      "-o still.png --time 7.5", run[-1])
        self.assertLess(run[-1].index("cp -al"), run[-1].index("cd /tmp/yt-render/monty/still-1-a &&"))
        self.assertEqual(back[-2:], ["root@h:/tmp/yt-render/monty/still-1-a/", "/w/monty/"])
        self.assertIn("rm -rf /tmp/yt-render/monty/still-1-a", rm[-1])
        self.assertNotIn("mirror", rm[-1])

    def test_run_job_is_the_public_hook_for_other_stages(self):
        seen: list[list[str]] = []
        with mock.patch.object(render.subprocess, "run",
                               side_effect=lambda argv, **kw: (seen.append(list(argv)) or
                                                               subprocess.CompletedProcess(
                                                                   argv, 0, "{}" + MARK * (argv[0] == "ssh"), ""))):
            proc = render.run_job(["scene-render", "encode", "/w/monty/scene.xml", "-o", "w.mp4"],
                                  Path("/w/monty"), "root@h", job="window")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual([a[0] for a in seen], ["rsync", "ssh", "rsync", "ssh"])
        self.assertIn("/tmp/yt-render/monty/window-", seen[1][-1])
        self.assertIn("rm -rf /tmp/yt-render/monty/window-", seen[3][-1])

    def test_keep_remote_leaves_the_directory_for_debugging(self):
        seen = self._calls(keep_remote=True)
        self.assertEqual([argv[0] for argv in seen], ["rsync", "ssh", "rsync"])


class ReportPaths(unittest.TestCase):
    """Only the film's own encode owns render.json."""

    def test_only_the_encode_writes_render_json(self):
        base = Path("/w/monty/render.json")
        self.assertEqual(render.report_path_for(base, "encode", None), base)
        self.assertEqual(render.report_path_for(base, "still", None), base.with_name("render-still.json"))
        self.assertEqual(render.report_path_for(base, "validate", None), base.with_name("render-validate.json"))
        self.assertEqual(render.report_path_for(base, "audio", None), base.with_name("render-audio.json"))

    def test_a_named_output_gets_its_own_report(self):
        base = Path("/w/monty/render.json")
        self.assertEqual(render.report_path_for(base, "encode", "pilot-8s"),
                         base.with_name("render-pilot-8s.json"))


class Localise(unittest.TestCase):
    def test_remote_paths_are_rewritten_to_the_project_root(self):
        report = {"result": {"outputs": [{"path": "/tmp/yt-render/monty/encode-1-a/video.mp4",
                                          "files": ["/tmp/yt-render/monty/encode-1-a/video.mp4"],
                                          "note": "/tmp/yt-render/monty/other/x"}]}}
        out = render._localise(report, "/tmp/yt-render/monty/encode-1-a", "/w/monty")
        output = out["result"]["outputs"][0]
        self.assertEqual(output["path"], "/w/monty/video.mp4")
        self.assertEqual(output["files"], ["/w/monty/video.mp4"])
        self.assertEqual(output["note"], "/tmp/yt-render/monty/other/x")


class RunContract(unittest.TestCase):
    """run() as qa and pilot call it: a partial Namespace, no CLI."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "scene.xml").write_text("<scene/>")
        self.seen: list[list[str]] = []

        def fake(argv, **kw):
            self.seen.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(
                {"ok": True, "command": "render", "result": {"outputs": []}})
                + MARK * (argv[0] == "ssh"), stderr="")

        self.patches = [mock.patch.object(render.subprocess, "run", side_effect=fake),
                        mock.patch.object(render, "engine", return_value=Path("/opt/sr/scene-render")),
                        mock.patch.object(render, "host_names", return_value=[])]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _args(self, **over):
        # pilot's set of flags, from before --estimate, --slices, --host,
        # --local and --keep-remote existed
        base = dict(project=str(self.tmp), title=None, work=str(self.tmp.parent),
                    genre="test", description="a test project",
                    scene=str(self.tmp / "scene.xml"), video_out=None, quality=None,
                    start=None, end=None, still=None, frames=None, strict=False,
                    parallel=None, validate=False, output=None, quiet=True, model=None)
        return argparse.Namespace(**base | over)

    def test_defaults_cover_every_flag_the_cli_registers(self):
        defaults = render.defaults()
        for name in ("slices", "estimate", "host", "local", "keep_remote", "parallel",
                     "output", "audio_only", "strict", "still", "frames", "validate"):
            self.assertIn(name, defaults)
        self.assertEqual(defaults["slices"], 1)
        self.assertEqual(defaults["estimate"], 0)
        self.assertFalse(defaults["keep_remote"])

    def test_with_defaults_fills_what_is_missing_or_none(self):
        args = argparse.Namespace(slices=None, still=7.5)
        render.with_defaults(args)
        self.assertEqual(args.slices, 1)
        self.assertEqual(args.still, 7.5)
        self.assertFalse(args.keep_remote)
        self.assertFalse(args.quiet)

    def test_a_still_from_a_namespace_without_newer_flags(self):
        code = render.run(self._args(still=7.5, video_out=str(self.tmp / "probe.png")))
        self.assertEqual(code, 0)
        self.assertEqual(self.seen[-1][1], "render")
        self.assertIn("--time", self.seen[-1])

    def test_a_still_never_writes_render_json(self):
        render.run(self._args(still=7.5, video_out=str(self.tmp / "probe.png")))
        self.assertFalse((self.tmp / "render.json").exists())
        self.assertTrue((self.tmp / "render-still.json").exists())

    def test_a_validation_and_a_named_output_never_write_render_json(self):
        render.run(self._args(validate=True))
        self.assertTrue((self.tmp / "render-validate.json").exists())
        render.run(self._args(output="pilot-8s"))
        self.assertTrue((self.tmp / "render-pilot-8s.json").exists())
        self.assertIn("--report", self.seen[-1])
        self.assertTrue(self.seen[-1][self.seen[-1].index("--report") + 1].endswith("render-pilot-8s.json"))
        self.assertFalse((self.tmp / "render.json").exists())

    def test_the_encode_owns_render_json(self):
        render.run(self._args())
        self.assertTrue((self.tmp / "render.json").exists())

    def test_a_remote_report_is_localised_to_the_project_root(self):
        remote = {}

        def fake(argv, **kw):
            self.seen.append(list(argv))
            out = ""
            if argv[0] == "ssh" and "encode" in argv[-1]:
                out = json.dumps({"ok": True, "command": "encode", "result": {"outputs": [
                    {"path": f"{remote['dir']}/video.mp4", "files": [f"{remote['dir']}/video.mp4"]}]}})
            return subprocess.CompletedProcess(argv, 0, stdout=out + MARK * (argv[0] == "ssh"),
                                               stderr="")

        def dir_(slug, job, tag, root=render.REMOTE_ROOT):
            remote["dir"] = f"{root}/{slug}/{job}-{tag}"
            return remote["dir"]

        with mock.patch.object(render.subprocess, "run", side_effect=fake), \
                mock.patch.object(render, "host_names", return_value=["root@h"]), \
                mock.patch.object(render, "probe", return_value=render.Host(
                    "root@h", engine="/opt/sr/scene-render", ram_gb=125)), \
                mock.patch.object(render, "remote_dir", side_effect=dir_):
            code = render.run(self._args())
        self.assertEqual(code, 0)
        report = json.loads((self.tmp / "render.json").read_text())
        self.assertEqual(report["result"]["outputs"][0]["path"], str(self.tmp.resolve() / "video.mp4"))
        self.assertTrue(remote["dir"].startswith(f"{render.REMOTE_ROOT}/{self.tmp.name}/encode-"))
        self.assertTrue(any(argv[0] == "ssh" and "rm -rf " + remote["dir"] in argv[-1]
                            for argv in self.seen))


if __name__ == "__main__":
    unittest.main()


class OutputDirs(unittest.TestCase):
    """The mirror excludes qa/ and other output trees, so a job must create
    the parent directory of whatever the engine is told to write."""

    def test_parent_dirs_of_outputs_are_listed(self):
        from ytstudio import render
        cmd = ["scene-render", "render", "scene.xml", "-o", "qa/pass-06/frames/burst/f_%04d.png",
               "--frames", "38..53", "--report", "render-still.json"]
        self.assertEqual(render.output_dirs(cmd), ["qa/pass-06/frames/burst"])

    def test_a_bare_file_name_needs_no_directory(self):
        from ytstudio import render
        self.assertEqual(render.output_dirs(["scene-render", "render", "s.xml", "-o", "probe.png"]), [])

    def test_the_remote_script_makes_those_directories_first(self):
        from ytstudio import render
        script = render.job_script("/tmp/yt-render/m/mirror", "/tmp/yt-render/m/still-1-a",
                                   "scene-render", "render scene.xml -o qa/p/f.png",
                                   dirs=["qa/p"])
        self.assertLess(script.index("mkdir -p qa/p"), script.index("scene-render render"))
        self.assertGreater(script.index("mkdir -p qa/p"), script.index("cd /tmp/yt-render/m/still-1-a"))
