"""AUTO-154: `yt doctor` warns when a section's world is flatter than its
own shots declare.

Each shot names the treatment it belongs to (script.json `style`), and each
library treatment has a dimension (styles.dimension_of). `yt vfx` measures
which of 2D, 2.5D and 3D each authored world uses (vfx.json
`reach.dimensions`, or the stored reach usage for an older record). A
section whose shots are 3d or 2.5d and whose world is not is one warn under
vfx, routed by `yt vfx --sections N`; a primary treatment no section
realises is one more. Flat is allowed: a 2D or footage-led film says
nothing, and nothing here fails, blocks READY or calls a model.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_delivery import DeliveryCase  # noqa: E402
from tests.test_reach import FLAT, PERSPECTIVE, THREED_GROUP, film  # noqa: E402
from ytstudio import doctor, workspace  # noqa: E402


class DoctorDimension(DeliveryCase):
    def project(self, styles, script, vfx_doc):
        self.write("styles.json", styles)
        self.write("script.json", script)
        self.write("vfx.json", dict(vfx_doc, totals={}, plan={}))
        return workspace.Project(self.root)

    def found(self, project):
        return [f for f in doctor.check_project(project).findings
                if f.area == "vfx" and "dimension" in f.message
                or f.area == "vfx" and " authored world is " in f.message
                or f.area in ("vfx", "script") and "primary treatment" in f.message
                or f.area == "vfx" and "not in the style library" in f.message]

    def test_flat_world_under_3d_shots_warns_with_section(self):
        project = self.project(*film(
            [("One", [("1.1", "2d motion graphics")], FLAT),
             ("Two", [("2.1", "3d animation"), ("2.2", "3d animation")], FLAT),
             ("Three", [("3.1", "3d animation")], PERSPECTIVE)],
            styles_first="2d motion graphics"))
        [warn] = [f for f in self.found(project) if f.level == "warn"]
        self.assertIn("section 2 (Two)", warn.message)
        self.assertIn("2.1, 2.2", warn.message)
        self.assertIn("are 3d (3d animation)", warn.message)
        self.assertIn("its authored world is 2d", warn.message)
        self.assertEqual(warn.fix, doctor._cmd("vfx", project, "--sections", "2"))

    def test_acceptance_section_3_and_the_primary_treatment(self):
        project = self.project(*film(
            [("One", [("1.1", "2d motion graphics")], FLAT),
             ("Two", [("2.1", "data visualisation")], FLAT),
             ("Three", [("3.1", "3d animation"), ("3.2", "3d animation")], FLAT)],
            styles_first="3d animation"))
        warns = [f for f in self.found(project) if f.level == "warn"]
        self.assertEqual(len(warns), 2)
        section = next(f for f in warns if "section 3" in f.message)
        self.assertIn("3.1, 3.2", section.message)
        self.assertIn("3d", section.message)
        self.assertIn("2d", section.message)
        self.assertEqual(section.fix, doctor._cmd("vfx", project, "--sections", "3"))
        film_warn = next(f for f in warns if "primary treatment" in f.message)
        self.assertIn("the primary treatment 3d animation is 3d and no section realises it",
                      film_warn.message)
        # a warning, never a fail: delivery and READY are not touched
        self.assertFalse([f for f in doctor.check_project(project).findings
                          if f.level == "fail" and "dimension" in f.message])

    def test_flat_film_with_flat_styles_is_silent(self):
        for style in ("2d motion graphics", "archival montage"):
            project = self.project(*film(
                [("One", [("1.1", style)], FLAT), ("Two", [("2.1", style)], FLAT)],
                styles_first=style))
            self.assertEqual(self.found(project), [], style)

    def test_reach_without_usage_is_a_note_not_a_warn(self):
        styles, script, doc = film([("One", [("1.1", "3d animation")], FLAT)])
        doc["reach"] = {"usage": None, "report": None, "audit": None, "reason": ""}
        found = self.found(self.project(styles, script, doc))
        self.assertEqual([f.level for f in found], ["note"])
        self.assertIn("dimension mix not measured: the reach record has no usage",
                      found[0].message)
        # a flat film has nothing to measure, so it is not told so
        styles, script, doc = film([("One", [("1.1", "2d motion graphics")], FLAT)])
        doc["reach"] = {"usage": None, "report": None, "audit": None, "reason": ""}
        self.assertEqual(self.found(self.project(styles, script, doc)), [])

    def test_a_reach_reason_already_says_why_there_is_no_usage(self):
        styles, script, doc = film([("One", [("1.1", "3d animation")], FLAT)])
        doc["reach"] = {"usage": None, "report": None, "audit": None, "dimensions": None,
                        "reason": "nothing was authored, so there is nothing to audit"}
        self.assertEqual(self.found(self.project(styles, script, doc)), [])

    def test_an_older_record_is_measured_from_its_usage(self):
        project = self.project(*film(
            [("One", [("1.1", "3d animation")], FLAT)], stored=False))
        [warn] = [f for f in self.found(project) if f.level == "warn"]
        self.assertIn("section 1 (One)", warn.message)

    def test_mostly_unmapped_shot_styles_note(self):
        project = self.project(*film(
            [("One", [("1.1", "3D cutaway"), ("1.2", "glossy explainer"),
                      ("1.3", "2d motion graphics")], FLAT)]))
        found = self.found(project)
        [note] = [f for f in found if f.level == "note"]
        self.assertIn("most shots name a treatment that is not in the style library "
                      "(e.g. '3D cutaway'); their dimension is unknown", note.message)
        self.assertFalse([f for f in found if f.level == "warn"])

    def test_every_gap_is_a_warn_improve_routes_to_its_section(self):
        # one warn per section, so `yt improve` re-authors those sections and
        # not the whole film with --fresh (improve reads one `--sections N`)
        from ytstudio import improve
        rows = [(f"S{n}", [(f"{n}.1", "3d animation")], FLAT) for n in range(1, 9)]
        rows.append(("S9", [("9.1", "2d motion graphics")], FLAT))
        project = self.project(*film(rows))
        warns = [f for f in self.found(project) if f.level == "warn"]
        self.assertEqual(len(warns), 8)
        for n, warn in enumerate(warns, 1):
            self.assertIn(f"section {n} (S{n})", warn.message)
            self.assertEqual(warn.fix, doctor._cmd("vfx", project, "--sections", str(n)))
        found = [r for r in improve.collect({}, doctor=improve.doctor_rows(project))
                 if r["source"] == "doctor" and " authored world is " in r["problem"]]
        self.assertEqual([r["section_n"] for r in found], list(range(1, 9)))
        self.assertEqual(improve.routed_sections(self.root, found), list(range(1, 9)))

    def test_a_primary_treatment_no_shot_takes_is_the_scripts_not_vfx(self):
        # no shot declares 3d, so re-authoring vfx would change nothing: the
        # screenplay and styles.json disagree, and the warn says so under script
        from ytstudio import improve
        project = self.project(*film(
            [("One", [("1.1", "2d motion graphics")], FLAT),
             ("Two", [("2.1", "data visualisation")], FLAT)],
            styles_first="3d animation"))
        [warn] = [f for f in self.found(project) if f.level == "warn"]
        self.assertEqual(warn.area, "script")
        self.assertIn("primary treatment 3d animation", warn.message)
        self.assertIn(doctor._cmd("script", project, "--fresh"), warn.fix)
        rows = [r for r in improve.collect({}, doctor=improve.doctor_rows(project))
                if r["source"] == "doctor" and "primary treatment" in r["problem"]]
        self.assertEqual([r["owner"] for r in rows], ["script"])
        argv = improve.stage_argvs(["vfx"], {r["owner"] for r in rows}, ["--project", "p"],
                                   quality="final", root=self.root)[0]
        self.assertNotIn("--fresh", argv)

    def test_an_older_usage_shape_is_measured_end_to_end(self):
        # a usage from before AUTO-154: no "flags", and threeD recorded by
        # presence only, never as "=true"
        def old(body):
            styles, script, doc = film([("One", [("1.1", "isometric diagram")], body)],
                                       styles_first="isometric diagram", stored=False)
            usage = doc["reach"]["usage"]
            del usage["flags"]
            for per in [usage] + list(usage["symbols"].values()):
                per["attributes"] = {k: n for k, n in per["attributes"].items()
                                     if not k.endswith("=true")}
            return styles, script, doc
        from ytstudio import reach
        styles, script, doc = old(THREED_GROUP)
        self.assertEqual(self.found(self.project(styles, script, doc)), [])
        evidence = reach.realised_dimensions(doc)["One"]["evidence"]
        self.assertIn("group/@threeD x2 (value not recorded)", evidence)
        warns = [f for f in self.found(self.project(*old(FLAT))) if f.level == "warn"]
        warn = next(f for f in warns if " authored world is " in f.message)
        self.assertIn("section 1 (One)", warn.message)
        self.assertIn("are 2.5d (isometric diagram)", warn.message)

    def test_improve_routes_a_gap_to_its_section(self):
        from ytstudio import improve
        project = self.project(*film(
            [("One", [("1.1", "2d motion graphics")], FLAT),
             ("Two", [("2.1", "3d animation")], FLAT)]))
        rows = improve.collect({}, doctor=improve.doctor_rows(project))
        [row] = [r for r in rows if r["source"] == "doctor" and "authored world" in r["problem"]]
        self.assertEqual(row["section_n"], 2)
        self.assertEqual(row["severity"], "minor")


if __name__ == "__main__":
    unittest.main()
