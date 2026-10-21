"""geometry.py: a hero attached to an object by design is no penetration of it.

The penetration check took every overlap between a hero group and a set
solid for a merge, so a hero built onto something -- a cap on the end bead
of a strand of overlapping beads -- failed every round, and the author kept
trying to separate two parts that must touch. These pin the way out: a
`<meta name="hero-attached" value="<hero> <object or prefix*> ...">`
declaration, qualified like `hero-rests` (by the vfx gate's include id
`shot/`, through a scene's includes), skips those overlaps on the
penetration path only; a hero is never checked against the nodes it
follows by `parent`; and the finding's fix names the meta with the actual
ids. No test reaches ssh, a model or the engine.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import test_geometry as base  # noqa: E402

from ytstudio import geometry, vfx  # noqa: E402
from ytstudio.scene_sdk import sdk  # noqa: E402

# Four sphere beads of radius 10, 15 apart on x (each overlaps the next, as
# a strand's beads do), centred at y = -100; the 5' cap is a sphere of
# radius 8 on the end bead. At x = 55 it overlaps bead 4 alone (by 8 on x,
# its tolerance is 1.6); at x = 40 it overlaps beads 3 and 4.
BEAD, BEAD_STEP, CAP = 10.0, 15.0, 8.0
BEADS = tuple(f"rna-e1-{k}" for k in range(1, 5))
PRESENT = ("floor", "desk") + BEADS + ("hero-cap",)


def strand_doc(*, attached=(), cap_x: float = 55.0, cap_parent: str | None = None) -> str:
    """A world built with the SDK: a floor, a box desk whose top is at
    y = -80, a strand of overlapping beads and a `hero-cap` sphere (at
    `cap_x`, local to `cap_parent` when it has one), with one
    `hero-attached` meta per entry of `attached`."""
    S = sdk()
    sc = S.Scene(version="1.3")
    sc.add_project(width="1920", height="1080", fps="30", duration="4", background="#101418")
    sc.add_output(path="p.mp4", codec="h264")
    sc.add_materials().add_material(id="m", baseColor="#AAAAAA")
    if attached:
        meta = sc.add_metadata()
        for value in attached:
            meta.add_meta(name=geometry.ATTACH_META, value=value)
    sym = sc.add_symbols().add_symbol(id="world-t", width="1920", height="1080", duration="4")
    sym.add_camera(id="cam", x="0", y="-100", z="-400", target="hero-cap",
                   focalLength="50", active="true")
    sym.add_object3D(id="floor", primitive="plane", material="m", x="0", y="0", z="0",
                     width="900", height="900", rotationX="90")
    sym.add_object3D(id="desk", primitive="box", material="m", x="50", y="-40", z="100",
                     width="100", height="80", depth="100")
    for k, bead in enumerate(BEADS):
        sym.add_object3D(id=bead, primitive="sphere", material="m", x=f"{k * BEAD_STEP:g}",
                         y="-100", z="100", radius=f"{BEAD:g}")
    place = {"parent": cap_parent, "y": "0", "z": "0"} if cap_parent else {"y": "-100", "z": "100"}
    sym.add_object3D(id="hero-cap", primitive="sphere", material="m", x=f"{cap_x:g}",
                     radius=f"{CAP:g}", **place)
    sc.add_composition().add_instance(id="use", symbol="world-t", start="0", end="4")
    return sc.to_bytes(layout="pretty").decode("utf-8")


def cap_at(t: float, y: float | None = None, prefix: str = "shot/") -> dict:
    """eval's envelope with every node of the strand world drawn; the cap
    keyed to `y` when given, else where the document puts it."""
    return base.frame(t, present=PRESENT, prefix=prefix,
                      **({"hero_cap": {"y": y}} if y is not None else {}))


def report_of(doc: str, *, path=lambda t: None, attached=None):
    """The check over the strand world's symbol, the cap on `path` (time
    -> y, None for the document's), the attachments the document declares
    unless `attached` is given."""
    objects = geometry.document_objects(doc, symbol="world-t", prefix="shot/")
    if attached is None:
        attached = geometry.attachments_declared(doc, prefix="shot/")
    times = geometry.sample_times([{"at": 0, "seconds": 4}], step=0.5)
    return geometry.evaluate(objects, times, lambda ts: [cap_at(t, path(t)) for t in ts],
                             attached=attached)


def hit(report) -> list[tuple[str, str]]:
    return sorted({(w["hero"], w["object"]) for w in report["worst"]})


class Declared(unittest.TestCase):
    """The meta, parsed and qualified exactly as `hero-rests` is."""

    def test_the_meta_is_one_row_per_object_qualified_by_the_prefix(self):
        doc = strand_doc(attached=("hero-cap rna-e1-* knot",))
        self.assertEqual(geometry.attachments_declared(doc, prefix="shot/"),
                         [{"hero": "shot/hero-cap", "object": "shot/rna-e1-*"},
                          {"hero": "shot/hero-cap", "object": "shot/knot"}])
        self.assertEqual(geometry.attachments_declared(doc),
                         [{"hero": "hero-cap", "object": "rna-e1-*"},
                          {"hero": "hero-cap", "object": "knot"}])
        self.assertEqual(geometry.attachments_declared(strand_doc()), [])

    def test_a_hero_with_nothing_or_a_bare_star_declares_nothing(self):
        self.assertEqual(geometry.attachments_declared(
            '<meta name="hero-attached" value="hero-cap"/>'), [])
        self.assertEqual(geometry.attachments_declared(
            '<meta name="hero-attached" value="hero-cap *"/>'), [])
        # a rest is not an attachment, nor the other way round
        self.assertEqual(geometry.attachments_declared(
            '<meta name="hero-rests" value="hero-cap rna-e1-4 2"/>'), [])
        self.assertEqual(geometry.rests_declared(
            '<meta name="hero-attached" value="hero-cap rna-e1-4 2"/>'), [])

    def test_a_prefix_star_matches_ids_starting_with_it_and_nothing_else(self):
        rows = [{"hero": "s/hero-cap", "object": "s/rna-e1-*"},
                {"hero": "s/hero-cap", "object": "s/knot"}]
        self.assertTrue(geometry.joined("s/hero-cap", "s/rna-e1-15", rows))
        self.assertTrue(geometry.joined("s/hero-cap", "s/knot", rows))
        self.assertFalse(geometry.joined("s/hero-cap", "s/rna-e10-1", rows))
        self.assertFalse(geometry.joined("s/hero-cap", "s/knot-2", rows))   # exact without *
        self.assertFalse(geometry.joined("s/hero-other", "s/rna-e1-15", rows))


class Attached(unittest.TestCase):
    """The penetration path skips what the hero is attached to, and only that."""

    def test_an_undeclared_cap_on_its_bead_is_still_a_penetration(self):
        report = report_of(strand_doc())
        self.assertEqual(hit(report), [("shot/hero-cap", "shot/rna-e1-4")])
        # bead 4 spans x 35 to 55, the cap 47 to 63: 8 units along x
        self.assertAlmostEqual(report["worst"][0]["depth"], 8.0)
        self.assertEqual(report["worst"][0]["axis"], "x")

    def test_a_declared_attachment_is_no_penetration(self):
        report = report_of(strand_doc(attached=("hero-cap rna-e1-4",)))
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["penetrations"], [])
        self.assertEqual(report["attached"], [{"hero": "shot/hero-cap",
                                               "object": "shot/rna-e1-4"}])
        lines = geometry.findings(report, symbol="world-t")
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("note:"), lines)

    def test_a_prefix_star_covers_every_bead_an_exact_id_one(self):
        self.assertEqual(hit(report_of(strand_doc(cap_x=40))),
                         [("shot/hero-cap", "shot/rna-e1-3"), ("shot/hero-cap", "shot/rna-e1-4")])
        self.assertEqual(report_of(strand_doc(cap_x=40, attached=("hero-cap rna-e1-*",)))
                         ["penetrations"], [])
        self.assertEqual(hit(report_of(strand_doc(cap_x=40, attached=("hero-cap rna-e1-4",)))),
                         [("shot/hero-cap", "shot/rna-e1-3")])
        # more objects on one meta, or one meta each, are the same declaration
        for attached in (("hero-cap rna-e1-3 rna-e1-4",), ("hero-cap rna-e1-3", "hero-cap rna-e1-4")):
            self.assertEqual(report_of(strand_doc(cap_x=40, attached=attached))["penetrations"], [])

    def test_the_attachment_counts_everywhere_else(self):
        doc = strand_doc(attached=("hero-cap rna-e1-*",))
        # keyed down into the desk from 2 s: the beads are declared, the desk is not
        report = report_of(doc, path=lambda t: -75.0 if t >= 2 else None)
        self.assertEqual(hit(report), [("shot/hero-cap", "shot/desk")])
        self.assertEqual(report["worst"][0]["first"], 2.0)
        # the beads stay set solids for reads-merged and the shadows
        for bead in BEADS:
            self.assertIn(f"shot/{bead}", report["solids"])

    def test_a_hero_is_never_inside_what_it_follows_by_parent(self):
        # the cap follows bead 4 (10 along x from its centre: 55, as above)
        report = report_of(strand_doc(cap_x=10, cap_parent="rna-e1-4"))
        self.assertEqual(report["penetrations"], [])
        self.assertEqual(report["attached"], [])             # nothing was declared
        # a sibling bead still counts: 5 back from bead 4 is x = 40, over bead 3
        report = report_of(strand_doc(cap_x=-5, cap_parent="rna-e1-4"))
        self.assertEqual(hit(report), [("shot/hero-cap", "shot/rna-e1-3")])

    def test_ancestors_are_every_parent_up_the_chain_and_a_cycle_is_cut(self):
        objects = [{"id": "s/strand", "parent": None}, {"id": "s/bead", "parent": "s/strand"},
                   {"id": "s/hero-cap", "parent": "s/bead"}, {"id": "s/fin", "parent": "s/hero-cap"}]
        self.assertEqual(geometry.ancestors(objects, "s/hero-cap"), ["s/bead", "s/strand"])
        self.assertEqual(geometry.ancestors(objects, "s/strand"), [])
        loop = [{"id": "a", "parent": "b"}, {"id": "b", "parent": "a"},
                {"id": "hero-x", "parent": "a"}]
        self.assertEqual(geometry.ancestors(loop, "hero-x"), ["a", "b"])
        self.assertEqual(geometry.ancestors(loop, "a"), ["b"])


class Advice(unittest.TestCase):
    """The fix says the parts can be declared joined, in the meta's own syntax."""

    def test_the_fix_names_the_meta_with_the_actual_ids(self):
        row = {"time": 2.0, "hero": "shot-1.1/hero-cap", "object": "shot-1.1/rna-e1-15",
               "depth": 80.0, "axis": "x", "tolerance": 1.6}
        text = geometry.advice(row)
        self.assertIn('<meta name="hero-attached" value="hero-cap rna-e1-15"/>', text)
        self.assertIn("touch by design", text)
        self.assertIn("*", text)
        self.assertIn("move the path of", text)              # moving it is still the first fix

    def test_the_finding_carries_it(self):
        lines = geometry.findings(report_of(strand_doc()), symbol="world-t")
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("error:"), lines)
        for needle in ("hero-cap", "penetrates `rna-e1-4`",
                       f'<meta name="{geometry.ATTACH_META}" value="hero-cap rna-e1-4"/>'):
            self.assertIn(needle, lines[0])


class Project(unittest.TestCase):
    """`yt qa` and `yt gate` read the declarations through the scene's includes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "vfx" / "v01").mkdir(parents=True)
        (self.root / "vfx" / "v01" / "a.scene.xml").write_text(
            strand_doc(attached=("hero-cap rna-e1-*",)), encoding="utf-8")
        (self.root / "vfx" / "v01" / "b.scene.xml").write_text(strand_doc(), encoding="utf-8")
        self.scene = ('<?xml version="1.0"?><scene version="1.3"><project width="1920" '
                      'height="1080" fps="30" duration="8"/><composition>'
                      '<include id="shot-1.1" start="0" end="4" src="vfx/v01/a.scene.xml" '
                      'symbol="world-t"/><include id="shot-2.1" start="4" end="8" '
                      'src="vfx/v01/b.scene.xml" symbol="world-t"/></composition></scene>')
        (self.root / "scene.xml").write_text(self.scene, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_scene_attachments_are_qualified_by_the_include_id(self):
        self.assertEqual(geometry.scene_attachments(self.scene, self.root),
                         [{"hero": "shot-1.1/hero-cap", "object": "shot-1.1/rna-e1-*"}])

    def test_check_project_skips_only_the_declaring_include(self):
        def run(times):
            # the frame graph holds the include live at t only
            return [cap_at(t, prefix="shot-1.1/" if t < 4 else "shot-2.1/") for t in times]
        shots = [{"label": "1.1", "at": 0.0, "seconds": 4.0},
                 {"label": "2.1", "at": 4.0, "seconds": 4.0}]
        report = geometry.check_project(self.root / "scene.xml", self.root, shots, step=1.0,
                                        runner=run)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["attached"],
                         [{"hero": "shot-1.1/hero-cap", "object": "shot-1.1/rna-e1-*"}])
        self.assertEqual(hit(report), [("shot-2.1/hero-cap", "shot-2.1/rna-e1-4")])


class VfxGate(unittest.TestCase):
    """The authoring gate honours the meta, as `yt build` includes the world (`shot/`)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ws = vfx.Workspace(self.root, version="v01")
        self.name = self.ws.doc_name(1, base.SECTION)
        self.shots = {"world-t": base.SECTION["shots"]}

    def tearDown(self):
        self._tmp.cleanup()

    def found(self, doc: str) -> list[str]:
        (self.ws.dir / self.name).write_text(doc, encoding="utf-8")

        def factory(doc_path, *, files=None, root=None, host=""):
            return lambda times: [cap_at(t) for t in times]
        with patch.object(vfx, "EVAL_RUNNER", factory):
            return self.ws.geometry(self.name, shots=self.shots)

    def test_an_undeclared_cap_fails_with_the_meta_in_the_fix(self):
        errors = [f for f in self.found(strand_doc()) if f.startswith("error")]
        self.assertEqual(len(errors), 1, errors)
        for needle in ("world-t", "hero-cap", "rna-e1-4",
                       '<meta name="hero-attached" value="hero-cap rna-e1-4"/>'):
            self.assertIn(needle, errors[0])

    def test_a_declared_cap_passes(self):
        found = self.found(strand_doc(attached=("hero-cap rna-e1-*",)))
        self.assertFalse([f for f in found if f.startswith("error")], found)
        self.assertTrue(any(f.startswith("note") and "no penetration" in f for f in found), found)

    def test_the_brief_names_the_meta(self):
        from ytstudio.core import Idea
        brief = self.ws.brief(1, idea=Idea(title="T", genre="g", description="d"), house_rules="",
                              style={}, tokens={"background": "#000"}, frame=(1920, 1080),
                              section=base.SECTION, effects=[], words={}, assets=[])
        text = brief.read_text(encoding="utf-8")
        self.assertIn(f'<meta name="{geometry.ATTACH_META}"', text)
        self.assertIn("prefix*", text)


if __name__ == "__main__":
    unittest.main()
