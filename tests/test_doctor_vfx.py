"""doctor names each vfx section left under its bar, and why it stopped.

`yt vfx` ends a section on its best reviewed round when the revisions, the
budget or the host run out, even when that round is under the bar: the
stage never stops the film on it. Before this nothing said so afterwards;
now each such section is a doctor warn naming the score, the bar it was
reviewed against (the record's own `bar`, else the run's `author_bar`, else
the default 7) and the reason it stopped, with `yt vfx --sections N` as the
fix, so `yt improve` re-authors that section alone. The approved look, a
carried record and an unreviewed one keep their own rows.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from ytstudio import doctor, workspace  # noqa: E402
from ytstudio.core import write_json  # noqa: E402


class UnderBar(unittest.TestCase):
    def report(self, authored, **authoring):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "a-film"
            root.mkdir()
            write_json(root / "idea.json", {"title": "A film", "genre": "g",
                                            "description": "d"})
            write_json(root / "vfx.json", {"totals": {}, "plan": {}, "authored": authored,
                                           **({"authoring": authoring} if authoring else {})})
            report = doctor.check_project(workspace.Project(root))
            return report, str(root)

    def under(self, report):
        return [f for f in report.findings if f.area == "vfx" and "under its bar" in f.message]

    def rec(self, n=2, **kw):
        return dict({"n": n, "section": "Later", "score": 6, "bar": 7.0,
                     "stopped": "budget", "findings": [], "review": {"score": 6}}, **kw)

    def test_a_section_under_its_recorded_bar_warns_with_sections_fix(self):
        report, root = self.report([self.rec()])
        rows = self.under(report)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].level, "warn")
        self.assertEqual(rows[0].message,
                         "section 2 ended at 6/10, under its bar 7 (budget spent)")
        self.assertEqual(rows[0].fix, f"yt vfx --project {root} --sections 2")
        self.assertFalse([f for f in report.findings if f.level == "fail" and f.area == "vfx"])

    def test_at_or_over_the_bar_is_silent(self):
        report, _ = self.report([self.rec(score=7), self.rec(n=3, score=8.5)])
        self.assertEqual(self.under(report), [])

    def test_bar_falls_back_to_authoring_author_bar_then_7(self):
        record = self.rec(score=7.5)
        record.pop("bar")
        report, _ = self.report([record], author_bar=8.0)
        rows = self.under(report)
        self.assertEqual(len(rows), 1)
        self.assertIn("ended at 7.5/10, under its bar 8 ", rows[0].message)
        report, _ = self.report([record])
        self.assertEqual(self.under(report), [])           # 7.5 is over the default 7
        report, _ = self.report([dict(record, score=6.5)])
        self.assertIn("under its bar 7 ", self.under(report)[0].message)

    def test_look_inherited_and_unreviewed_records_get_no_under_bar_warn(self):
        for extra in ({"look": True}, {"inherited": {"score": 6}}, {"reviewed": False}):
            with self.subTest(extra=extra):
                report, _ = self.report([self.rec(**extra)])
                self.assertEqual(self.under(report), [])

    def test_score_none_gets_no_under_bar_warn(self):
        for score in (None, "6", True):
            with self.subTest(score=score):
                report, _ = self.report([self.rec(score=score)])
                self.assertEqual(self.under(report), [])

    def test_each_stopped_value_has_its_wording(self):
        wording = {"budget": "budget spent", "same": "revision did not change it",
                   "worse": "revision scored worse", "finish": "finish asked",
                   "infra": "host lost the round", "unreviewed": "round not reviewed",
                   "host": "host lost twice", "stage budget": "stage budget",
                   None: "revisions used"}
        for stopped, why in wording.items():
            with self.subTest(stopped=stopped):
                report, _ = self.report([self.rec(stopped=stopped)])
                rows = self.under(report)
                self.assertEqual(len(rows), 1)
                self.assertTrue(rows[0].message.endswith(f"under its bar 7 ({why})"),
                                rows[0].message)


if __name__ == "__main__":
    unittest.main()
