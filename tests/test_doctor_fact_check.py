"""AUTO-133: doctor's fact-check fails and the script gate read one list.

Doctor failed on every unresolved blocker of script.json's `check`
(n=0 as "script-wide") while the script stage only reported them, so a film
could be filed READY with doctor failing it. Both now read
`script.fact_blockers`; doctor's wording is what it always was.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import doctor, script, workspace  # noqa: E402


def shots():
    return [{"n": 1, "label": "1.1", "narration": "The bridge fell."},
            {"n": 2, "label": "1.2", "narration": "One bolt held it."},
            {"n": 3, "label": "2.1", "narration": "The paint hid the rust."}]


#: A shot blocker, a whole-script blocker, a resolved blocker, a blocker the
#: writer kept with a reason, an important flag and a minor one.
CHECK = {"flags": [
    {"n": 2, "severity": "blocker", "problem": "the bolt count is not in the research",
     "fix": "say a bolt"},
    {"n": 0, "severity": "blocker", "problem": "the title promises a cause nobody found",
     "fix": "retitle it"},
    {"n": 3, "severity": "blocker", "problem": "resolved by hand", "fix": "-",
     "resolved": True},
    {"n": 1, "severity": "blocker", "problem": "the night is not in the research",
     "fix": "drop the night", "kept": "the report says overnight"},
    {"n": 3, "severity": "important", "problem": "overstated", "fix": "hedge"},
    {"n": 1, "severity": "minor", "problem": "a nit", "fix": "-"}]}


class FactBlockers(unittest.TestCase):
    def test_every_unresolved_blocker_with_its_label_and_whether_it_was_kept(self):
        rows = script.fact_blockers(CHECK, shots())
        self.assertEqual(rows, [
            {"n": 2, "label": "1.2", "problem": "the bolt count is not in the research",
             "fix": "say a bolt", "kept": False},
            {"n": 0, "label": "script-wide",
             "problem": "the title promises a cause nobody found", "fix": "retitle it",
             "kept": False},
            {"n": 1, "label": "1.1", "problem": "the night is not in the research",
             "fix": "drop the night", "kept": True}])

    def test_no_check_and_a_clean_check_have_none(self):
        self.assertEqual(script.fact_blockers({}, shots()), [])
        self.assertEqual(script.fact_blockers(None, shots()), [])
        self.assertEqual(script.fact_blockers(
            {"flags": [{"n": 1, "severity": "important", "problem": "p"}]}, shots()), [])

    def test_a_shot_the_script_does_not_have_is_named_by_its_number(self):
        rows = script.fact_blockers({"flags": [{"n": 9, "severity": "blocker",
                                                 "problem": "p", "fix": "f"}]}, shots())
        self.assertEqual(rows[0]["label"], "shot 9")


class DoctorReadsThem(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "a-film"
        self.root.mkdir()
        (self.root / "idea.json").write_text(json.dumps(
            {"title": "A film", "genre": "g", "description": "d"}))
        (self.root / "script.json").write_text(json.dumps({
            "sections": [{"heading": "One", "shots": shots()}], "check": CHECK}))

    def fails(self):
        report = doctor.check_project(workspace.Project(self.root))
        return [(f.message, f.fix) for f in report.findings
                if f.level == "fail" and f.area == "fact-check"]

    def test_the_fails_read_exactly_as_before(self):
        self.assertEqual(self.fails(), [
            ("shot 2: the bolt count is not in the research", "say a bolt"),
            ("script-wide: the title promises a cause nobody found", "retitle it"),
            ("shot 1: the night is not in the research", "drop the night")])

    def test_they_come_from_the_scripts_definition(self):
        with mock.patch.object(script, "fact_blockers", wraps=script.fact_blockers) as seen:
            self.fails()
        self.assertTrue(seen.called)


if __name__ == "__main__":
    unittest.main()
