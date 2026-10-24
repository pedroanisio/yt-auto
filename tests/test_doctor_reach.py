"""AUTO-144: an unresolved static process reaches `yt doctor`, and from it
`yt improve` re-authors only its section.

The motion audit (`yt vfx`, vfx.json `reach.motion`) names the effects that
must show a change over time and show it static. Doctor warns once per
effect, owner vfx, with the fix `yt vfx --sections N`, N found by joining
the effect's id to its section's heading in vfx.json `sections` and that
heading to the authoring record (`authored[].section`, `.n`). A motion
audit that could not be read, and a plan's warnings, are notes. An older
vfx.json without `motion` gives nothing new. No model, no network.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import doctor, workspace  # noqa: E402
from ytstudio.core import write_json  # noqa: E402

STATIC = [{"id": "two-pressure", "what_changes": "the pressure builds",
           "fix": "show the pressure rising in steps"}]


def vfx_json(static=(), reason="", warnings=(), motion=True):
    reach = {"usage": {}, "report": {}, "audit": None, "reason": ""}
    if motion:
        reach["motion"] = {"answer": None, "static": list(static), "reason": reason}
    second = {"section": "Two", "seconds": 6, "cuts": [], "effects": [
        {"id": "two-pressure", "name": "Pressure", "type": "2d-diagram", "shots": ["2.1"]}]}
    if warnings:
        second["warnings"] = list(warnings)
    return {"totals": {}, "plan": {}, "reach": reach,
            "sections": [{"section": "One", "seconds": 6, "cuts": [], "effects": [
                             {"id": "one-open", "name": "Open", "type": "3d-render",
                              "shots": ["1.1"]}]},
                         second],
            "authored": [{"n": 1, "section": "One", "worlds": [], "findings": []},
                         {"n": 2, "section": "Two", "worlds": [], "findings": []}]}


class Doctor(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        write_json(self.root / "idea.json", {"title": "T", "genre": "g", "description": "d"})

    def vfx(self, data):
        write_json(self.root / "vfx.json", data)
        project = workspace.Project(self.root)
        # the look's own warn (no approved keyframe here) is another card's
        return project, [f for f in doctor.check_project(project).findings
                         if f.area == "vfx" and "keyframe" not in f.message]

    def test_a_static_process_is_a_warn_naming_its_section(self):
        project, found = self.vfx(vfx_json(STATIC))
        [warn] = [f for f in found if f.level == "warn"]
        self.assertIn("two-pressure explains the pressure builds with a static picture",
                      warn.message)
        self.assertEqual(warn.fix, doctor._cmd("vfx", project, "--sections", "2"))

    def test_an_effect_no_section_holds_falls_back_to_the_whole_film(self):
        data = vfx_json([{"id": "lost-effect", "what_changes": "x", "fix": "y"}])
        project, found = self.vfx(data)
        [warn] = [f for f in found if f.level == "warn"]
        self.assertEqual(warn.fix, doctor._cmd("vfx", project))
        data = vfx_json(STATIC)
        data["authored"][1]["section"] = "Renamed"
        project, found = self.vfx(data)
        [warn] = [f for f in found if f.level == "warn"]
        self.assertNotIn("--sections", warn.fix)

    def test_an_unread_motion_audit_and_the_plans_warnings_are_notes(self):
        _, found = self.vfx(vfx_json(reason="the motion audit failed: down",
                                     warnings=["two-pressure covers 2.1, a process its "
                                               "picture-check says is shown static"]))
        notes = [f.message for f in found if f.level == "note"]
        self.assertTrue(any("the motion audit failed: down" in m for m in notes), notes)
        self.assertTrue(any("two-pressure covers 2.1" in m for m in notes), notes)
        self.assertEqual([f for f in found if f.level == "warn"], [])

    def test_an_older_vfx_json_gives_nothing_new(self):
        _, before = self.vfx(vfx_json(motion=False))
        self.assertEqual([f.level for f in before], ["ok"])
        _, clean = self.vfx(vfx_json())
        self.assertEqual([f.level for f in clean], ["ok"])


class Improve(unittest.TestCase):
    """The doctor warn, routed: improve.collect carries its section, and
    routed_sections sends vfx that section alone."""

    def test_the_warn_is_routed_to_its_section(self):
        from ytstudio import improve
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / "idea.json", {"title": "T", "genre": "g", "description": "d"})
            data = vfx_json(STATIC)
            data["authored"][0]["worlds"] = [{"id": "w1", "start": 0, "seconds": 6,
                                              "shots": ["1.1"]}]
            data["authored"][1]["worlds"] = [{"id": "w2", "start": 6, "seconds": 6,
                                              "shots": ["2.1"]}]
            write_json(root / "vfx.json", data)
            rows = improve.doctor_rows(workspace.Project(root))
            found = improve.collect({}, doctor=rows)
            [row] = [r for r in found if r["source"] == "doctor" and r["owner"] == "vfx"
                     and "static picture" in r["problem"]]
            self.assertEqual(row["section_n"], 2)
            self.assertEqual(improve.routed_sections(root, [row]), [2])


if __name__ == "__main__":
    unittest.main()
