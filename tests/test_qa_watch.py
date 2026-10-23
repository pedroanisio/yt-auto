"""yt qa --watch: a model that watches the rendered cut through the gateway (AUTO-127).

QA judges stills: a still sampler misses what happens between samples (a
two-frame pop, a stuttering transition, a super that lands late). No
Anthropic or OpenAI model takes video, so `--watch` sends the cut, cut down
on a render host when it is too large, to a gateway model from another
maker than every author of the film, and turns what it saw into extra
sampled moments that the scorecard's reviewer confirms against its stills.

The claude and codex CLIs stay the default. Without --watch nothing here
runs: no catalog fetch, no node, no ssh, and the reviewer's prompt, schema
and samples are what they were.

Nothing reaches a model, the network or a render host: the gateway engine
runs a fake `node` (tests/gateway_fakes.py), the catalog is a fixture
(tests/fixtures/gateway/), ssh and rsync are a fake subprocess.run under
hermetic.fake_remote, and the scorecard reviewer is a fake engine.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from ytstudio import doctor, workspace  # noqa: E402


def _stale_partial(tmp: Path) -> workspace.Project:
    """A project whose final render covers 0-10 s of a 16.5 s programme and
    whose video.mp4 is older than scene.xml."""
    root = tmp / "cut"
    root.mkdir()
    (root / "scene.xml").write_text('<project duration="16.5"/>')
    (root / "build.json").write_text(json.dumps({"scene": {"duration": 16.5},
                                                 "audio_tracks": 2}))
    (root / "render.json").write_text(json.dumps({"ok": True, "result": {
        "outputs": [{"quality": "final", "range": [0, 10]}]}}))
    (root / "video.mp4").write_bytes(b"x")
    early = (root / "scene.xml").stat().st_mtime - 60
    os.utime(root / "video.mp4", (early, early))
    return workspace.Project(root)


class CutProblems(unittest.TestCase):
    """doctor's two rules about the cut, one function both doctor and qa call."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qa-watch-cut-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_doctor_cut_problems_unchanged(self):
        project = _stale_partial(self.tmp)
        final = f"yt render --project {project.root} --quality final --strict"
        # what delivery_evidence said before cut_problems existed, word for word
        self.assertEqual(doctor.delivery_evidence(project, None), [
            ("the render covers 0-10s of a 16.5s programme", final),
            ("video.mp4 predates scene.xml: the scene was rebuilt after the render, so "
             "the video shows an older cut", final),
            ("no delivery gate has run on this render", f"yt gate --project {project.root}"),
            ("no QA pass has scored this render", f"yt qa --project {project.root}")])
        self.assertEqual(doctor.cut_problems(project, None), [
            ("the render covers 0-10s of a 16.5s programme", final),
            ("video.mp4 predates scene.xml: the scene was rebuilt after the render, so "
             "the video shows an older cut", final)])

    def test_a_current_whole_cut_has_no_problem(self):
        project = _stale_partial(self.tmp)
        (project.root / "render.json").write_text(json.dumps({"ok": True, "result": {
            "outputs": [{"quality": "final", "range": [0, 16.5]}]}}))
        later = (project.root / "scene.xml").stat().st_mtime + 60
        os.utime(project.root / "video.mp4", (later, later))
        self.assertEqual(doctor.cut_problems(project, None), [])


if __name__ == "__main__":
    unittest.main()
