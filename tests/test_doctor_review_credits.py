"""AUTO-160: doctor fails a credit `yt review` filed against this scene.xml.

The fail is in area "licence", which no stage owns: `yt improve` does not
route it to build (it would only rebuild the same scene) and does not file
it beside QA's own review-credit row. A review.json of another scene is a
note, never a fail: it is not evidence about this film.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import doctor, improve, qa, review, workspace  # noqa: E402

ISSUE = {"heading": "The Pressure Builds", "what": "a chart redrawn from Smith 2019",
         "asset": "fig-pressure", "severity": "blocking", "fix": "add 'after Smith 2019'"}


class ReviewCredits(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "a-film"
        self.root.mkdir()
        (self.root / "idea.json").write_text(json.dumps(
            {"title": "A film", "genre": "g", "description": "d"}))
        self.scene = self.root / "scene.xml"
        self.scene.write_text("<scene version='1.3'/>")
        self.project = workspace.Project(self.root)

    def review_json(self, digest):
        (self.root / "review.json").write_text(json.dumps(
            {"scene_sha256": digest, "credit_issues": [ISSUE]}))

    def rows(self):
        report = doctor.check_project(self.project)
        return [(f.level, f.area, f.message, f.fix) for f in report.findings
                if f.area == "licence"]

    def test_a_current_credit_issue_is_a_licence_fail(self):
        self.review_json(qa.scene_digest(self.scene.read_bytes()))
        rows = self.rows()
        self.assertEqual(len(rows), 1, rows)
        level, area, message, fix = rows[0]
        self.assertEqual((level, area), ("fail", "licence"))
        self.assertEqual(message, "review: missing credit in The Pressure Builds: a chart "
                                  "redrawn from Smith 2019")
        self.assertTrue(fix.startswith("credit it at source (dossier credit_line, "
                                       "sound.json, props.json, or on screen in the vfx "
                                       "document), rebuild, then "))
        self.assertTrue(fix.endswith(f"yt review --project {self.root}"))

    def test_a_stale_review_is_a_note_not_a_fail(self):
        self.review_json("0" * 64)
        self.assertEqual(self.rows(), [("note", "licence", review.STALE_REVIEW,
                                        f"yt review --project {self.root}")])
        # and one from before AUTO-160, with no digest at all
        (self.root / "review.json").write_text(json.dumps({"credit_issues": [ISSUE]}))
        self.assertEqual([r[0] for r in self.rows()], ["note"])

    def test_no_review_json_says_nothing(self):
        self.assertEqual(self.rows(), [])

    def test_improve_collect_adds_no_finding_for_them(self):
        self.review_json(qa.scene_digest(self.scene.read_bytes()))
        rows = [(f.level, f.area, f.message, f.fix)
                for f in doctor.check_project(self.project).findings if f.area == "licence"]
        self.assertTrue(rows)
        self.assertNotIn("licence", improve.ROUTABLE)
        self.assertEqual(improve.collect({}, doctor=rows), [])


if __name__ == "__main__":
    unittest.main()
