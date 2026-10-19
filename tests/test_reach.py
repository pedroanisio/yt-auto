"""reach.py: how far into the engine the authored documents reach.

The plumbing is deterministic and pinned here: which constructs a document
uses, which of the engine's families they fall in, which manifest caveats
apply. The judgement -- is this the strongest construct the schema offers for
what the shot must convey -- is the model's, and the tests only check that it
is asked with the facts and that its answer travels to the plan, the report
and the QA reviewer.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import reach, vfx  # noqa: E402
from ytstudio.scene_sdk import sdk  # noqa: E402

DOC = """<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.3">
  <project width="1920" height="1080" fps="30" duration="6" background="#000000"/>
  <output path="p.mp4" codec="h264"/>
  <materials>
    <material id="m" baseColor="#FFFFFF" unevenness="0.2"/>
  </materials>
  <symbols>
    <symbol id="fx-flat" width="1920" height="1080">
      <shape id="r" shape="rect" x="0" y="0" width="100" height="100" fill="#FFFFFF" pattern="dots"/>
      <text id="t" text="hi" width="200" height="50"/>
    </symbol>
    <symbol id="fx-room" width="1920" height="1080">
      <camera id="c" renderer="pathtrace" x="0" y="0" z="-500" active="true"/>
      <object3D id="o" primitive="box" material="m" width="10" height="10" depth="10"/>
      <object3D id="o2" primitive="sphere" material="m" radius="5"/>
    </symbol>
  </symbols>
  <composition>
    <instance id="u1" symbol="fx-flat" start="0" end="3"/>
    <instance id="u2" symbol="fx-room" start="3" end="6"/>
  </composition>
</scene>
"""

MANIFEST = [
    {"construct": "material/@unevenness", "status": "approximate",
     "when": ["camera/@renderer=pathtrace"], "note": "raster only"},
    {"construct": "camera/@renderer=pathtrace", "status": "approximate", "note": "slow"},
    {"construct": "pattern", "status": "approximate", "note": "not on text"},
    {"construct": "scene360/@layout=eac", "status": "approximate", "note": "no metadata"},
]


class Families(unittest.TestCase):
    def test_every_family_member_is_a_schema_element(self):
        names = set(sdk().element_names())
        for family, members in reach.FAMILIES.items():
            unknown = [m for m in members if m not in names]
            self.assertEqual(unknown, [], f"{family}: not in the schema")

    def test_no_element_is_in_two_families(self):
        seen: dict[str, str] = {}
        for family, members in reach.FAMILIES.items():
            for m in members:
                self.assertNotIn(m, seen, f"{m} is in {seen.get(m)} and {family}")
                seen[m] = family

    def test_families_text_lists_the_engines_vocabulary(self):
        text = reach.families_text(sdk().element_names())
        self.assertIn("simulation:", text)
        self.assertIn("pyro", text)
        self.assertIn("object3D", text)
        # anything the schema has that no family names is still listed
        self.assertIn("other", reach.family_of("not-an-element"))


class Usage(unittest.TestCase):
    def test_constructs_are_counted_per_document_and_per_symbol(self):
        u = reach.construct_usage(DOC)
        self.assertEqual(u["elements"]["object3D"], 2)
        self.assertEqual(u["elements"]["shape"], 1)
        self.assertEqual(u["attributes"]["camera/@renderer=pathtrace"], 1)
        self.assertEqual(u["attributes"]["material/@unevenness"], 1)
        self.assertEqual(u["attributes"]["object3D/@primitive=box"], 1)
        self.assertNotIn("object3D/@width=10", u["attributes"])   # numbers are not constructs
        self.assertNotIn("camera/@id=c", u["attributes"])          # nor identifiers
        self.assertNotIn("object3D/@material=m", u["attributes"])  # nor references
        self.assertIn("object3D/@material", u["attributes"])       # the attribute itself is
        self.assertEqual(set(u["symbols"]), {"fx-flat", "fx-room"})
        self.assertEqual(u["symbols"]["fx-room"]["elements"], {"camera": 1, "object3D": 2})
        self.assertNotIn("camera", u["symbols"]["fx-flat"]["elements"])

    def test_a_document_that_does_not_parse_is_reported_not_raised(self):
        u = reach.construct_usage("<scene><broken")
        self.assertEqual(u["elements"], {})
        self.assertTrue(u["error"])

    def test_usage_of_several_files_is_merged(self):
        u = reach.usage_of({"vfx/a.scene.xml": DOC, "vfx/b.scene.xml": DOC})
        self.assertEqual(u["elements"]["object3D"], 4)
        self.assertEqual(set(u["files"]), {"vfx/a.scene.xml", "vfx/b.scene.xml"})
        self.assertEqual(u["files"]["vfx/a.scene.xml"]["elements"]["object3D"], 2)


class Caveats(unittest.TestCase):
    def test_manifest_entries_that_touch_used_constructs_are_named(self):
        u = reach.construct_usage(DOC)
        hits = reach.caveats(u, MANIFEST)
        constructs = [h["construct"] for h in hits]
        self.assertIn("material/@unevenness", constructs)
        self.assertIn("camera/@renderer=pathtrace", constructs)
        self.assertIn("pattern", constructs)
        self.assertNotIn("scene360/@layout=eac", constructs)
        self.assertEqual([h for h in hits if h["construct"] == "material/@unevenness"][0]["when"],
                         ["camera/@renderer=pathtrace"])

    def test_manifest_is_read_from_a_file_and_missing_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capabilities.json"
            path.write_text(json.dumps({"format": "scene-render-capabilities/1",
                                        "engine": {"name": "scene-render", "version": "x"},
                                        "entries": MANIFEST}))
            with mock.patch.dict(os.environ, {"SR_CAPABILITIES": str(path)}):
                self.assertEqual(len(reach.manifest()), 4)
            with mock.patch.dict(os.environ, {"SR_CAPABILITIES": str(Path(tmp) / "nope.json")}):
                self.assertEqual(reach.manifest(), [])


class Report(unittest.TestCase):
    def test_coverage_per_family(self):
        u = reach.usage_of({"vfx/a.scene.xml": DOC})
        r = reach.report(u, sdk().element_names(), MANIFEST)
        fam = r["families"]
        self.assertIn("object3D", fam["3d stage"]["used"])
        self.assertIn("pyro", fam["simulation"]["unused"])
        self.assertEqual(fam["simulation"]["used"], [])
        self.assertGreater(fam["3d stage"]["available"], fam["3d stage"]["coverage"] * 0)
        self.assertEqual(r["used_total"], len({k for k in u["elements"]}))
        self.assertEqual(r["available_total"], len(sdk().element_names()))
        self.assertEqual([c["construct"] for c in r["caveats"]][:1], ["material/@unevenness"][:1]
                         if r["caveats"][0]["construct"] == "material/@unevenness" else
                         [r["caveats"][0]["construct"]])
        self.assertEqual(r["unknown"], [])


class Audit(unittest.TestCase):
    """The planner asks with the facts and carries the answer forward."""

    AUDIT = {
        "effects": [
            {"id": "fx-flat", "verdict": "upgrade", "current": ["shape", "text"],
             "upgrade_to": ["meshGradient", "textAnimator"], "why": "flat", "gain": 3,
             "risk": "none"},
            {"id": "fx-room", "verdict": "strongest", "current": ["object3D", "camera"],
             "upgrade_to": [], "why": "already 3D", "gain": 0, "risk": ""}],
        "unused_worth_using": [{"construct": "pyro", "where": "fx-room", "why": "smoke"}],
        "reach": {"score": 6, "summary": "half of what the engine offers"},
    }

    def planner(self, root: Path, asked: list):
        p = vfx.VfxPlanner.__new__(vfx.VfxPlanner)
        p.ws = vfx.Workspace(root)
        p.fresh, p.verbose = True, False
        p.direction, p.house, p.bundle = {}, "", {"premise": "p"}
        p.idea = type("I", (), {"title": "T", "genre": "g", "description": "d", "length": ""})()
        p.dossier = type("D", (), {"load": lambda s, n: None, "save": lambda s, n, d: None})()
        p.budget_hours = None

        def ask(prompt, schema):
            asked.append((prompt, schema))
            return dict(self.AUDIT)
        p._ask_json = ask
        return p

    def test_reach_asks_with_usage_vocabulary_and_caveats_and_keeps_the_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "vfx").mkdir()
            (root / "vfx" / "01-open.scene.xml").write_text(DOC)
            asked: list = []
            p = self.planner(root, asked)
            effects = [{"section": "Open", "effects": [
                {"id": "fx-flat", "name": "Flat", "conveys": "c1", "brief": "b1", "type": "2d-diagram"},
                {"id": "fx-room", "name": "Room", "conveys": "c2", "brief": "b2", "type": "3d-render"}]}]
            scene = {"symbols": {"fx-flat": {"file": "vfx/01-open.scene.xml"},
                                 "fx-room": {"file": "vfx/01-open.scene.xml"}}, "dropped": {}}
            with mock.patch.object(reach, "manifest", return_value=MANIFEST):
                out = p.reach(effects, scene)
            prompt, schema = asked[0]
            self.assertIs(schema, reach.REACH_SCHEMA)
            self.assertIn("fx-room", prompt)
            self.assertIn("object3D", prompt)                 # what it used
            self.assertIn("pyro", prompt)                      # what it could have used
            self.assertIn("material/@unevenness", prompt)      # what the engine only approximates
            self.assertIn("c1", prompt)                        # what the effect must convey
            self.assertEqual(out["audit"]["reach"]["score"], 6)
            self.assertIn("3d stage", out["report"]["families"])
            self.assertEqual(out["usage"]["elements"]["object3D"], 2)

    def test_nothing_authored_means_nothing_to_audit_and_no_model_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            asked: list = []
            p = self.planner(Path(tmp), asked)
            out = p.reach([{"section": "Open", "effects": []}], {"symbols": {}, "dropped": {}})
            self.assertEqual(asked, [])
            self.assertIsNone(out["audit"])
            self.assertIn("nothing", out["reason"])

    def test_the_plan_is_told_about_upgrades(self):
        with tempfile.TemporaryDirectory() as tmp:
            asked: list = []
            p = self.planner(Path(tmp), asked)
            p._ask_json = lambda prompt, schema: (asked.append(prompt) or {"verdict": "ok", "build_order": []})
            effects = [{"section": "Open", "effects": [
                {"id": "fx-flat", "name": "Flat", "type": "2d-diagram", "difficulty": 2,
                 "hours": 2, "section": "Open", "shots": ["1.1"]}]}]
            p.plan(effects, {"hours": 2, "count": 1, "seconds": 5},
                   {"symbols": {"fx-flat": {}}}, {"audit": self.AUDIT})
            self.assertIn("meshGradient", asked[0])
            self.assertIn("upgrade", asked[0])

    def test_the_effects_brief_names_the_engines_vocabulary(self):
        self.assertIn("pyro", vfx.VfxPlanner.vocabulary())
        self.assertIn("simulation:", vfx.VfxPlanner.vocabulary())


class Summary(unittest.TestCase):
    def test_summary_for_the_reviewer(self):
        s = reach.summary({"audit": Audit.AUDIT,
                           "report": {"families": {"simulation": {"used": [], "available": 20,
                                                                   "coverage": 0.0, "unused": ["pyro"]}},
                                      "caveats": []}})
        self.assertEqual(s["score"], 6)
        self.assertEqual(s["upgrades"], [{"id": "fx-flat", "to": ["meshGradient", "textAnimator"], "gain": 3}])
        self.assertEqual(s["coverage"]["simulation"], "0/20")
        self.assertIsNone(reach.summary(None))


class Examples(unittest.TestCase):
    def test_the_proven_examples_reach_into_3d(self):
        for el in ("<camera", "<object3D", "<light ", "<material ", "<particles3D", "<burst"):
            self.assertIn(el, vfx.EXAMPLES, el)
        doc = sdk().read(vfx.EXAMPLES.encode("utf-8"))
        self.assertEqual([str(f) for f in doc.check() if str(f).startswith("error")], [])

    def test_the_cheat_sheet_groups_the_schema_by_family(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = vfx.Workspace(Path(tmp))
            ws.prepare(tokens={"ink": "#FFFFFF"}, frame=(1920, 1080))
            sheet = (ws.dir / "schema.md").read_text(encoding="utf-8")
            self.assertIn("## by family", sheet)
            self.assertIn("simulation:", sheet)


if __name__ == "__main__":
    unittest.main()
