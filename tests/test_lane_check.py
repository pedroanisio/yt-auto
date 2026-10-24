"""AUTO-52: a doctor finding blocks the stage that owns it in the govern lane.

lane.check keeps the fails doctor files under a stage's name and the evidence
doctor only warns about (a failed render job, an errored vfx section); warnings
alone never block.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import doctor, lane, workspace  # noqa: E402
from ytstudio.core import write_json  # noqa: E402


class LaneCheckTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name) / "work"
        self.root = self.work / "a-film"
        self.root.mkdir(parents=True)
        write_json(self.root / "idea.json", {"title": "A film", "genre": "g",
                                             "description": "d"})
        self.lane_dir = Path(self._tmp.name) / ".govern" / "projects" / "a-film"
        self.lane_dir.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def check(self, stage):
        ok, lines = lane.check(self.lane_dir, stage, work=self.work)
        return ok, "\n".join(lines)

    def render_done(self, report=None):
        (self.root / "video.mp4").write_bytes(b"\x00" * 64)
        write_json(self.root / "render.json",
                   report or {"status": "rendered", "result": {"outputs": []}})

    def vfx(self, authored, **more):
        write_json(self.root / "vfx.json", {"sections": [], "authored": authored,
                                            "totals": {}, "plan": {}, **more})

    # -- render ------------------------------------------------------------

    def test_a_clean_render_is_done(self):
        self.render_done()
        ok, why = self.check("render")
        self.assertTrue(ok, why)

    def test_a_failed_render_job_refuses_the_render_stage(self):
        self.render_done()
        write_json(self.root / "render-failed.json",
                   {"status": "failed", "job": "encode", "seconds": 90,
                    "message": "slice 37 out of memory"})
        ok, why = self.check("render")
        self.assertFalse(ok)
        self.assertIn("slice 37 out of memory", why)

    def test_failed_kit_stills_in_the_cache_never_block_the_render_stage(self):
        self.render_done()
        reports = self.root / ".cache" / "reports"
        reports.mkdir(parents=True)
        write_json(reports / "proof-08.json",
                   {"status": "timeout", "job": "vfx/v02/.proof-08", "seconds": 600})
        write_json(reports / "frame-67.json",
                   {"status": "failed", "job": "frame at 67.110 s", "message": "iteration"})
        ok, why = self.check("render")
        self.assertTrue(ok, why)

    def test_a_video_without_an_encode_report_refuses_the_render_stage(self):
        (self.root / "video.mp4").write_bytes(b"\x00" * 64)
        ok, why = self.check("render")
        self.assertFalse(ok)
        self.assertIn("render.json", why)

    def test_a_failed_report_older_than_a_good_encode_is_superseded(self):
        write_json(self.root / "render-failed.json", {"status": "failed", "job": "encode"})
        os.utime(self.root / "render-failed.json", (1, 1))
        self.render_done()
        ok, why = self.check("render")
        self.assertTrue(ok, why)

    def test_only_warnings_leave_the_render_stage_done(self):
        self.render_done({"status": "rendered", "result": {"outputs": [
            {"findings": [{"severity": "warning", "message": "soft clip"}]}]}})
        ok, why = self.check("render")
        self.assertTrue(ok, why)

    # -- vfx ---------------------------------------------------------------

    def test_an_errored_vfx_section_refuses_the_vfx_stage(self):
        self.vfx([{"n": 2, "findings": ["error: the authoring of section 2 failed: X"],
                   "review": None}])
        ok, why = self.check("vfx")
        self.assertFalse(ok)
        self.assertIn("section 2", why)

    def test_an_unreviewed_vfx_section_refuses_the_vfx_stage(self):
        self.vfx([{"n": 1, "findings": [], "review": {"score": 8}},
                  {"n": 3, "findings": [], "review": None}])
        ok, why = self.check("vfx")
        self.assertFalse(ok)
        self.assertIn("section 3", why)

    def test_reviewed_sections_with_a_continuity_warning_are_done(self):
        self.vfx([{"n": 1, "findings": ["warning: soft"], "review": {"score": 8}}],
                 continuity={"one_film": False, "score": 4, "breaks": []})
        ok, why = self.check("vfx")
        self.assertTrue(ok, why)

    def test_vfx_stage_is_not_failed_by_continuity_or_under_bar(self):
        # AUTO-153: a blocking break (from a reviewer of another family, so QA
        # blocks on it) and a section under its bar are doctor warns; the lane
        # is never stuck on a model's judgement.
        self.vfx([{"n": 1, "section": "Open", "findings": [], "review": {"score": 5},
                   "score": 5, "bar": 7.0, "stopped": "budget"},
                  {"n": 2, "section": "Later", "findings": [], "review": {"score": 8},
                   "score": 8, "bar": 7.0}],
                 continuity={"one_film": False, "score": 3, "summary": "s", "monotony": [],
                             "reviewer": {"model": "opus", "author": "sonnet",
                                          "engine": "claude"},
                             "breaks": [{"sections": ["Open", "Later"],
                                         "owner_section": "Later", "problem": "doubles",
                                         "fix": "f", "severity": "blocking"}]})
        report = doctor.check_project(workspace.Project(self.root))
        warns = [f.message for f in report.findings if f.area == "vfx" and f.level == "warn"]
        self.assertTrue(any("under its bar" in m for m in warns), warns)
        self.assertTrue(any(m.startswith("continuity: section 2") for m in warns), warns)
        ok, why = self.check("vfx")
        self.assertTrue(ok, why)

    # -- keyframe, props ---------------------------------------------------

    def test_a_stale_keyframe_refuses_the_keyframe_stage(self):
        (self.root / "keyframe").mkdir()
        (self.root / "keyframe" / "keyframe.scene.xml").write_text("<scene/>")
        write_json(self.root / "keyframe.json", {
            "document": "keyframe/keyframe.scene.xml",
            "approval": {"state": "approved", "at": "2026-10-20T10:00:00Z",
                         "digest": "0000000000000000"}})
        ok, why = self.check("keyframe")
        self.assertFalse(ok)
        self.assertIn("stale", why)

    def test_a_fail_filed_under_props_refuses_props(self):
        write_json(self.root / "props.json", {"props": []})
        real = doctor.check_project

        def with_fail(project):
            report = real(project)
            report.add("fail", "props", "a prop has no source")
            return report

        doctor.check_project = with_fail
        try:
            ok, why = self.check("props")
        finally:
            doctor.check_project = real
        self.assertFalse(ok)
        self.assertIn("a prop has no source", why)


if __name__ == "__main__":
    unittest.main()
