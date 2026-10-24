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

        def ask(name, prompt, schema):
            asked.append((prompt, schema))
            return dict(self.AUDIT)
        p._decide =ask
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
            p._decide =lambda name, prompt, schema: (asked.append(prompt) or {"verdict": "ok", "build_order": []})
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


# --------------------------------------------------------------------------- #
# AUTO-144: the motion audit -- does the viewer watch the change?
# --------------------------------------------------------------------------- #

#: One world, two phases: shots 1.1 at 0-3 and 1.2 at 3-6.
WORLD_DOC = """<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.3">
  <project width="1920" height="1080" fps="30" duration="6" background="#000000"/>
  <output path="p.mp4" codec="h264"/>
  <symbols>
    <symbol id="world-open" width="1920" height="1080" duration="6">
      <camera id="c" x="0" y="0" z="-500" active="true">
        <animate property="z"><key time="3.5" value="-500"/><key time="5" value="-300"/></animate>
      </camera>
      <shape id="a" shape="rect" x="0" y="0" width="100" height="100" fill="#FFFFFF">
        <animate property="x"><key time="0" value="0"/><key time="2" value="300"/></animate>
      </shape>
      <text id="title" text="hi" width="200" height="50" opacity="0">
        <animate property="opacity"><key time="4" value="0"/><key time="5" value="1"/></animate>
      </text>
      <shape id="p1" shape="rect" x="0" y="200" width="50" height="50" fill="#FFFFFF" opacity="0">
        <animate property="opacity"><key time="0.5" value="0"/><key time="1" value="1"/></animate>
      </shape>
      <shape id="p2" shape="rect" x="0" y="300" width="50" height="50" fill="#FFFFFF" opacity="0">
        <animate property="opacity"><key time="1.5" value="0"/><key time="2" value="1"/></animate>
      </shape>
      <group id="late" start="3">
        <shape id="b" shape="rect" x="0" y="400" width="50" height="50" fill="#FFFFFF">
          <animate property="y"><key time="0" value="400"/><key time="1" value="500"/></animate>
        </shape>
      </group>
      <particleEmitter id="dust" x="0" y="0"/>
    </symbol>
  </symbols>
  <composition>
    <instance id="u1" symbol="world-open" start="0" end="6"/>
  </composition>
</scene>
"""

PHASE_ONE, PHASE_TWO = [(0.0, 3.0)], [(3.0, 6.0)]


def targets(rows):
    return sorted({r["target"] for r in rows})


def told(eid, changing, changed_by=(), unfolds=True, what="the block slides", fix=""):
    return {"id": eid, "unfolds": unfolds, "what_changes": what,
            "shown_changing": changing, "changed_by": list(changed_by), "fix": fix}


class MotionSlice(unittest.TestCase):
    def test_an_animate_is_in_the_phase_its_keys_fall_in(self):
        one = reach.motion_slice(WORLD_DOC, "world-open", PHASE_ONE)
        two = reach.motion_slice(WORLD_DOC, "world-open", PHASE_TWO)
        [row] = [r for r in one if r["target"] == "shape#a"]
        self.assertEqual((row["construct"], row["family"], row["property"], row["start"],
                          row["end"]), ("animate", "motion", "x", 0.0, 2.0))
        self.assertNotIn("shape#a", targets(two))
        self.assertIn("camera#c", targets(two))
        self.assertNotIn("camera#c", targets(one))

    def test_a_simulation_runs_the_whole_world(self):
        one = reach.motion_slice(WORLD_DOC, "world-open", PHASE_ONE)
        two = reach.motion_slice(WORLD_DOC, "world-open", PHASE_TWO)
        for rows in (one, two):
            [dust] = [r for r in rows if r["target"] == "particleEmitter#dust"]
            self.assertEqual((dust["family"], dust["property"], dust["start"], dust["end"]),
                             ("simulation", "", 0.0, 6.0))

    def test_an_ancestors_start_moves_its_keys(self):
        two = reach.motion_slice(WORLD_DOC, "world-open", PHASE_TWO)
        [late] = [r for r in two if r["target"] == "shape#b"]
        self.assertEqual((late["start"], late["end"]), (3.0, 4.0))
        self.assertNotIn("shape#b", targets(reach.motion_slice(WORLD_DOC, "world-open",
                                                               PHASE_ONE)))

    def test_a_symbol_of_its_own_is_read_whole(self):
        self.assertEqual(reach.motion_slice(DOC, "fx-room", None), [])
        self.assertEqual(reach.motion_slice(DOC, "fx-flat", None), [])
        whole = reach.motion_slice(WORLD_DOC, "world-open", None)
        self.assertIn("shape#a", targets(whole))
        self.assertIn("camera#c", targets(whole))
        self.assertEqual(reach.motion_slice("<scene><broken", "world-open", None), [])
        self.assertEqual(reach.motion_slice(WORLD_DOC, "no-such", None), [])


class MotionProblems(unittest.TestCase):
    def slices(self):
        return {"one": reach.motion_slice(WORLD_DOC, "world-open", PHASE_ONE),
                "two": reach.motion_slice(WORLD_DOC, "world-open", PHASE_TWO)}

    def problems(self, *rows, ids=("one", "two"), slices=None):
        return reach.motion_problems({"effects": list(rows)}, list(ids),
                                     slices if slices is not None else self.slices())

    def test_a_good_answer_has_no_problem(self):
        self.assertEqual(self.problems(told("one", True, ["shape#a"]),
                                       told("two", True, ["camera#c"])), [])

    def test_an_unanswered_a_twice_answered_and_an_unknown_id_fail(self):
        self.assertTrue(any("two" in p for p in self.problems(told("one", False))))
        self.assertTrue(self.problems(told("one", False), told("one", False),
                                      told("two", False)))
        self.assertTrue(any("zz" in p for p in self.problems(
            told("one", False), told("two", False), told("zz", False))))

    def test_a_change_outside_the_effects_shots_fails(self):
        # the world animates the title's opacity at 4-5, in phase two only
        got = self.problems(told("one", True, ["text#title"]), told("two", False))
        self.assertTrue(any("text#title" in p for p in got), got)

    def test_one_fade_on_one_target_is_an_entrance_not_a_change(self):
        got = self.problems(told("one", False), told("two", True, ["text#title"]))
        self.assertTrue(any("text#title" in p for p in got), got)

    def test_reveals_on_two_targets_at_different_times_are_a_step_build(self):
        self.assertEqual(self.problems(told("one", True, ["shape#p1", "shape#p2"]),
                                       told("two", False)), [])
        self.assertTrue(self.problems(told("one", True, ["shape#p1"]), told("two", False)))

    def test_a_present_construct_that_does_not_change_shows_nothing(self):
        slices = {"fx-room": reach.motion_slice(DOC, "fx-room", None),
                  "fx-flat": reach.motion_slice(DOC, "fx-flat", None)}
        self.assertTrue(self.problems(told("fx-room", True, ["camera#c"]),
                                      told("fx-flat", False), ids=("fx-room", "fx-flat"),
                                      slices=slices))
        self.assertTrue(self.problems(told("fx-room", False), told("fx-flat", True, []),
                                      ids=("fx-room", "fx-flat"), slices=slices))
        self.assertTrue(self.problems(told("fx-room", False),
                                      told("fx-flat", True, ["shape#r"]),
                                      ids=("fx-room", "fx-flat"), slices=slices))

    def test_a_camera_move_inside_the_window_passes(self):
        self.assertEqual(self.problems(told("one", False), told("two", True, ["camera#c"])),
                         [])

    def test_static_processes_are_what_unfolds_and_is_not_shown_changing(self):
        answer = {"effects": [told("one", False, fix="build it", what="the cascade"),
                              told("two", True, ["camera#c"]),
                              told("three", False, unfolds=False)]}
        self.assertEqual(reach.static_processes(answer),
                         [{"id": "one", "what_changes": "the cascade", "fix": "build it"}])
        self.assertEqual(reach.static_processes(None), [])

    def test_the_schema_requires_every_field(self):
        item = reach.MOTION_SCHEMA["properties"]["effects"]["items"]
        self.assertEqual(set(item["required"]), {"id", "unfolds", "what_changes",
                                                 "shown_changing", "changed_by", "fix"})

    def test_the_prompt_gives_the_rows_and_judges_against_the_treatment(self):
        slices = self.slices()
        effects = [{"id": "one", "name": "Move", "type": "2d-diagram", "conveys": "CONVEYS-1",
                    "brief": "BRIEF-1", "shots": ["1.1"]},
                   {"id": "two", "name": "Late", "type": "2d-diagram", "conveys": "c2",
                    "brief": "b2", "shots": ["1.2"]}]
        constraints = [{"label": "1.1", "severity": "important", "source": "picture",
                        "problem": "picture:static-process: shot 1.1: FROZEN", "fix": "f"},
                       {"label": "1.2", "severity": "important", "source": "check",
                        "problem": "FACT", "fix": "f"}]
        text = reach.motion_prompt(effects, slices, constraints)
        one = text.split("`one`", 1)[1].split("`two`", 1)[0]
        self.assertIn("CONVEYS-1", one)
        self.assertIn("BRIEF-1", one)
        self.assertIn("FROZEN", one)
        self.assertIn("shape#a x 0-2", one)
        self.assertNotIn("FACT", text)                   # a picture-check constraint only
        self.assertIn("Judge against the treatment", text)
        self.assertNotIn("score", reach.MOTION_ROLE.lower())


class FakeReviewer:
    """The reviewer harness the motion audit builds: records how it was made
    and what it was asked, answers in order, raises an exception given."""
    made: list = []
    asked: list = []
    answers: list = []

    def __init__(self, root, stage, **kw):
        type(self).made.append(dict(kw, stage=stage))
        self.model = kw.get("model")
        from claude_engine import Usage
        self.usage = Usage(cost_usd=0.25, calls=1)

    def decide(self, name, prompt, schema):
        type(self).asked.append((name, prompt, schema))
        got = type(self).answers.pop(0)
        if isinstance(got, Exception):
            raise got
        return json.loads(json.dumps(got))


SECTION_OPEN = {"heading": "Open", "seconds": 6, "shots": [
    {"label": "1.1", "at": 0, "seconds": 3, "visual": "v1"},
    {"label": "1.2", "at": 3, "seconds": 3, "visual": "v2"}]}
GROUPS = [{"section": "Open", "seconds": 6, "cuts": [], "effects": [
    {"id": "open-move", "name": "Move", "type": "2d-diagram", "conveys": "c1", "brief": "b1",
     "shots": ["1.1"], "section": "Open"},
    {"id": "open-late", "name": "Late", "type": "2d-diagram", "conveys": "c2", "brief": "b2",
     "shots": ["1.2"], "section": "Open"}]}]
WORLD_SCENE = {"symbols": {"world-open": {"file": "vfx/01-open.scene.xml"}},
               "phases": {"open-move": "world-open", "open-late": "world-open"},
               "dropped": {}}
GOOD = {"effects": [told("open-move", True, ["shape#a"]),
                    told("open-late", False, what="the title appears",
                         fix="build the title in steps")]}


class MotionAudit(unittest.TestCase):
    def setUp(self):
        FakeReviewer.made, FakeReviewer.asked, FakeReviewer.answers = [], [], []
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "vfx").mkdir()
        (self.root / "vfx" / "01-open.scene.xml").write_text(WORLD_DOC)
        self.saved: dict = {}
        self.cached: dict = {}

    def planner(self, asked, fresh=True):
        import threading
        from claude_engine import Usage
        p = Audit.planner(Audit(), self.root, asked)
        p.fresh, p._stale = fresh, set()
        p.reviewer_model, p.model = "opus", "sonnet"
        p.house_digest = {}
        p.usage = Usage()
        p._account_lock = threading.Lock()
        p.script = []
        p.bundle = {"premise": "p", "picture_check": []}
        cached, saved = self.cached, self.saved
        p.dossier = type("D", (), {"load": lambda s, n: cached.get(n),
                                   "save": lambda s, n, d: saved.__setitem__(n, d)})()
        return p

    def run_reach(self, p):
        with mock.patch.object(reach, "manifest", return_value=[]), \
                mock.patch.object(vfx.harness, "Harness", FakeReviewer):
            return p.reach(GROUPS, WORLD_SCENE, {"sections": [SECTION_OPEN]})

    def test_it_asks_the_reviewer_family_with_the_phases_rows(self):
        asked: list = []
        FakeReviewer.answers = [GOOD]
        p = self.planner(asked)
        out = self.run_reach(p)
        self.assertEqual([schema for _, schema in asked], [reach.REACH_SCHEMA])  # plan-reach
        [made] = FakeReviewer.made
        self.assertEqual(made["model"], "opus")
        self.assertEqual(made["stage"], "vfx")
        self.assertIs(made["role"], reach.MOTION_ROLE)
        self.assertNotIn("timeout", made)                   # the text default, not 1800 s
        [(name, prompt, schema)] = FakeReviewer.asked
        self.assertEqual(name, "reach-motion")
        self.assertIs(schema, reach.MOTION_SCHEMA)
        move = prompt.split("`open-move`", 1)[1].split("`open-late`", 1)[0]
        self.assertIn("shape#a", move)
        self.assertNotIn("camera#c", move)                  # phase two's
        motion = out["motion"]
        self.assertEqual(motion["static"], [{"id": "open-late",
                                             "what_changes": "the title appears",
                                             "fix": "build the title in steps"}])
        self.assertEqual(motion["reason"], "")
        self.assertEqual(out["audit"]["reach"]["score"], 6)  # the score is untouched
        self.assertAlmostEqual(p.usage.cost_usd, 0.25)      # the reviewer is booked

    def test_a_phase_with_one_fade_lands_under_static_processes(self):
        # The card's acceptance: the world animates only one opacity fade in
        # the phase; a reviewer that calls it a change is asked again, and
        # the honest answer is a static process in vfx.json and vfx.md.
        from tests.test_vfx_picture_constraints import plain_data, rendered
        fade = WORLD_DOC.replace('<animate property="z"><key time="3.5" value="-500"/>'
                                 '<key time="5" value="-300"/></animate>', "")
        fade = fade.replace('<particleEmitter id="dust" x="0" y="0"/>', "")
        fade = fade.replace('<animate property="y"><key time="0" value="400"/>'
                            '<key time="1" value="500"/></animate>', "")
        (self.root / "vfx" / "01-open.scene.xml").write_text(fade)
        FakeReviewer.answers = [
            {"effects": [told("open-move", True, ["shape#a"]),
                         told("open-late", True, ["text#title"], what="the title appears")]},
            {"effects": [told("open-move", True, ["shape#a"]),
                         told("open-late", False, what="the pressure builds",
                              fix="show the pressure rising in steps")]}]
        out = self.run_reach(self.planner([]))
        self.assertIn("one fade", FakeReviewer.asked[1][1])
        self.assertEqual([s["id"] for s in out["motion"]["static"]], ["open-late"])
        data = plain_data()
        data["reach"] = out
        md = rendered(data)
        block = md.split("### Static processes", 1)[1].split("\n## ", 1)[0]
        self.assertIn("`open-late` | the pressure builds | show the pressure rising in steps",
                      block)
        self.assertIn("static_processes", json.dumps(reach.summary(out)))

    def test_a_problem_is_asked_once_more_by_name(self):
        FakeReviewer.answers = [{"effects": [told("open-move", True, ["text#title"]),
                                             told("open-late", False)]}, GOOD]
        out = self.run_reach(self.planner([]))
        self.assertEqual([n for n, _, _ in FakeReviewer.asked],
                         ["reach-motion", "reach-motion-again"])
        self.assertIn("text#title", FakeReviewer.asked[1][1])
        self.assertEqual([s["id"] for s in out["motion"]["static"]], ["open-late"])

    def test_a_second_bad_answer_is_not_evidenced(self):
        bad = {"effects": [told("open-move", True, ["text#title"])]}
        FakeReviewer.answers = [bad, bad]
        out = self.run_reach(self.planner([]))
        self.assertEqual(out["motion"]["static"], [])
        self.assertIn("text#title", out["motion"]["reason"])
        self.assertEqual(out["motion"]["answer"], bad)

    def test_a_failed_reviewer_leaves_the_rest_of_the_record(self):
        from ytstudio import harness
        FakeReviewer.answers = [harness.HarnessError("the reviewer is down")]
        out = self.run_reach(self.planner([]))
        self.assertIn("the reviewer is down", out["motion"]["reason"])
        self.assertEqual(out["motion"]["static"], [])
        self.assertEqual(out["audit"]["reach"]["score"], 6)
        self.assertIsNotNone(out["usage"])
        self.assertIsNotNone(out["report"])

    def test_a_failed_plan_reach_still_raises(self):
        from ytstudio import harness
        p = self.planner([])

        def down(name, prompt, schema):
            raise harness.HarnessError("plan-reach is down")
        p._decide = down
        with self.assertRaises(harness.HarnessError):
            self.run_reach(p)
        self.assertEqual(FakeReviewer.asked, [])

    def test_a_cached_record_without_motion_asks_only_the_motion_audit(self):
        asked: list = []
        self.cached["14-reach"] = {"usage": {"elements": {}, "symbols": {}},
                                   "report": {"families": {}}, "audit": Audit.AUDIT,
                                   "reason": ""}
        FakeReviewer.answers = [GOOD]
        out = self.run_reach(self.planner(asked, fresh=False))
        self.assertEqual(asked, [])                         # no plan-reach
        self.assertEqual([n for n, _, _ in FakeReviewer.asked], ["reach-motion"])
        self.assertEqual(out["audit"], Audit.AUDIT)
        self.assertEqual([s["id"] for s in out["motion"]["static"]], ["open-late"])
        self.assertEqual(self.saved["14-reach"]["motion"], out["motion"])

    def test_a_cached_record_with_motion_asks_nothing(self):
        asked: list = []
        self.cached["14-reach"] = {"usage": {}, "report": {}, "audit": Audit.AUDIT,
                                   "reason": "", "motion": {"answer": GOOD, "static": [],
                                                            "reason": ""}}
        out = self.run_reach(self.planner(asked, fresh=False))
        self.assertEqual((asked, FakeReviewer.asked), ([], []))
        self.assertEqual(out["motion"]["reason"], "")

    def test_a_cached_record_of_nothing_authored_gains_its_reason(self):
        self.cached["14-reach"] = {"usage": None, "report": None, "audit": None,
                                   "reason": "nothing was authored, so there is nothing "
                                             "to audit"}
        out = self.run_reach(self.planner([], fresh=False))
        self.assertEqual(FakeReviewer.asked, [])
        self.assertEqual(out["motion"], {"answer": None, "static": [],
                                         "reason": "nothing was authored, so there is "
                                                   "nothing to audit"})
        self.assertTrue(reach.needs_motion({"usage": None}))
        self.assertFalse(reach.needs_motion(out))
        self.assertFalse(reach.needs_motion(None))

    def test_the_plan_names_a_static_process_as_required_before_upgrades(self):
        asked: list = []
        p = self.planner(asked)
        p._decide = lambda name, prompt, schema: (asked.append(prompt)
                                                  or {"verdict": "ok", "build_order": []})
        effects = [{"section": "Open", "effects": [
            {"id": "fx-flat", "name": "Flat", "type": "2d-diagram", "difficulty": 2,
             "hours": 2, "section": "Open", "shots": ["1.1"]}]}]
        p.plan(effects, {"hours": 2, "count": 1, "seconds": 5}, {"symbols": {"fx-flat": {}}},
               {"audit": Audit.AUDIT, "motion": {"static": [
                   {"id": "fx-flat", "what_changes": "the cascade", "fix": "build it"}]}})
        prompt = asked[0]
        self.assertIn("REQUIRED: the cascade is shown static -- build it", prompt)
        self.assertLess(prompt.index("REQUIRED:"), prompt.index("upgrade to"))
        self.assertIn("not optional", prompt)
        # none: the prompt is what it was
        asked.clear()
        p.plan(effects, {"hours": 2, "count": 1, "seconds": 5}, {"symbols": {"fx-flat": {}}},
               {"audit": Audit.AUDIT})
        plain = asked[0]
        asked.clear()
        p.plan(effects, {"hours": 2, "count": 1, "seconds": 5}, {"symbols": {"fx-flat": {}}},
               {"audit": Audit.AUDIT, "motion": {"static": [], "reason": ""}})
        self.assertEqual(asked[0], plain)
        self.assertNotIn("REQUIRED", plain)


class MotionSummary(unittest.TestCase):
    STATIC = {"answer": None, "reason": "",
              "static": [{"id": "open-late", "what_changes": "the title appears",
                          "fix": "build it"}]}

    def test_summary_carries_the_static_processes(self):
        s = reach.summary({"audit": Audit.AUDIT, "report": {"families": {}, "caveats": []},
                           "motion": self.STATIC})
        self.assertEqual(s["score"], 6)
        self.assertEqual(s["static_processes"], [{"id": "open-late",
                                                  "what_changes": "the title appears"}])

    def test_summary_without_an_audit_is_still_read_when_motion_is(self):
        s = reach.summary({"usage": None, "report": None, "audit": None, "reason": "r",
                           "motion": self.STATIC})
        self.assertIsNone(s["score"])
        self.assertEqual(s["upgrades"], [])
        self.assertEqual(len(s["static_processes"]), 1)
        self.assertIsNone(reach.summary({"audit": None, "reason": "r"}))  # older: as before

    def test_vfx_md_lists_the_static_processes(self):
        from tests.test_vfx_picture_constraints import plain_data, rendered
        data = plain_data()
        data["reach"] = {"usage": {}, "report": {"families": {}, "caveats": []},
                         "audit": Audit.AUDIT, "reason": "", "motion": self.STATIC}
        md = rendered(data)
        block = md.split("### Static processes", 1)[1].split("\n## ", 1)[0]
        self.assertIn("`open-late`", block)
        self.assertIn("the title appears", block)
        self.assertIn("build it", block)

    def test_vfx_md_lists_them_with_no_audit(self):
        from tests.test_vfx_picture_constraints import plain_data, rendered
        data = plain_data()
        data["reach"] = {"usage": None, "report": None, "audit": None,
                         "reason": "plan-reach was not read", "motion": self.STATIC}
        md = rendered(data)
        self.assertIn("## Reach", md)
        self.assertIn("plan-reach was not read", md)
        self.assertIn("`open-late`", md.split("### Static processes", 1)[1])

    def test_vfx_md_says_why_motion_could_not_be_read(self):
        from tests.test_vfx_picture_constraints import plain_data, rendered
        data = plain_data()
        data["reach"] = {"usage": {}, "report": {"families": {}, "caveats": []},
                         "audit": Audit.AUDIT, "reason": "",
                         "motion": {"answer": None, "static": [],
                                    "reason": "the motion audit failed: down"}}
        md = rendered(data)
        self.assertIn("the motion audit failed: down",
                      md.split("### Static processes", 1)[1].split("\n## ", 1)[0])

    def test_vfx_md_of_nothing_authored_says_why_once(self):
        from tests.test_vfx_picture_constraints import plain_data, rendered
        data = plain_data()
        why = "nothing was authored, so there is nothing to audit"
        data["reach"] = {"usage": None, "report": None, "audit": None, "reason": why,
                         "motion": {"answer": None, "static": [], "reason": why}}
        md = rendered(data)
        self.assertEqual(md.count(why), 1)


if __name__ == "__main__":
    unittest.main()
