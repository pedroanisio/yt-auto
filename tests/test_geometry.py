"""geometry.py: does the hero pass through the set?

The vision reviewers mention a plane sinking into a desk and never block on
it, so this is a mechanical check: the document's object3D nodes become
world axis-aligned boxes at the times the engine's `eval` reports, the
hero's box is intersected with every set solid, and a merge deeper than a
tenth of the hero's smallest dimension is a penetration with a time, an
object and a depth. These pin the parsing, the boxes (rotation, parents,
the primitives, a mesh approximated and marked), the records, the sampling,
the one-job runner's script, and the three hooks: `yt vfx`'s authoring
gate, `yt qa`'s mechanical findings and `yt gate`'s blocking item. No test
reaches ssh, a model or the engine.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_engine import Event, Usage
from tests import hermetic
from ytstudio import gate, geometry, harness, qa, vfx
from ytstudio.scene_sdk import sdk

# A 20-unit hero cube over a desk whose top face is at y = -80 (y points down,
# as the camera's schema says: the floor is y = 0 and up is negative).
DESK_TOP = -80.0
HERO = 20.0


def world_doc(*, keys=((0, -150), (4, -150)), rest: str | None = None,
              extra=None) -> str:
    """A world built with the SDK: a floor, a box desk, a `hero-` box keyed
    on y, and a thin plane the camera sees through."""
    S = sdk()
    sc = S.Scene(version="1.3")
    sc.add_project(width="1920", height="1080", fps="30", duration="4", background="#101418")
    sc.add_output(path="p.mp4", codec="h264")
    sc.add_materials().add_material(id="m", baseColor="#AAAAAA")
    if rest:
        sc.add_metadata().add_meta(name=geometry.REST_META, value=rest)
    sym = sc.add_symbols().add_symbol(id="world-t", width="1920", height="1080", duration="4")
    sym.add_camera(id="cam", x="0", y="-100", z="-400", target="hero-box",
                   focalLength="50", active="true")
    sym.add_object3D(id="floor", primitive="plane", material="m", x="0", y="0", z="0",
                     width="900", height="900", rotationX="90")
    sym.add_object3D(id="desk", primitive="box", material="m", x="0", y="-40", z="100",
                     width="100", height="80", depth="100")
    sym.add_object3D(id="glass", primitive="plane", material="m", x="0", y="-100", z="300",
                     width="100", height="100", opacity="0.2")
    hero = sym.add_object3D(id="hero-box", primitive="box", material="m", x="0",
                            y=str(keys[0][1]), z="100", width=str(HERO), height=str(HERO),
                            depth=str(HERO))
    anim = hero.add_animate(property="y")
    for t, y in keys:
        anim.add_key(time=str(t), value=str(y))
    for kw in extra or []:
        sym.add_object3D(**kw)
    sc.add_composition().add_instance(id="use", symbol="world-t", start="0", end="4")
    return sc.to_bytes(layout="pretty").decode("utf-8")


def frame(t: float, present=("floor", "desk", "glass", "hero-box"), prefix="shot/",
          **props) -> dict:
    """What `scene-render eval --time t --format json` prints: the envelope
    with the frame graph, every node's evaluated props at that time."""
    nodes = [{"id": prefix.rstrip("/"), "kind": "include", "draw": True, "props": {}}]
    for oid in present:
        nodes.append({"id": f"{prefix}{oid}", "kind": "object3D", "draw": True,
                      "parent": 0, "props": props.get(oid.replace("-", "_"), {})})
    return {"command": "eval", "ok": True, "exit_code": 0, "error": None,
            "format": "scene-render-cli/1",
            "result": {"frame": int(round(t * 30)), "nodes": nodes}}


def hero_at(t: float, y: float) -> dict:
    return frame(t, hero_box={"y": y})


def bounds_at(doc: str, y: float) -> dict:
    objects = geometry.overlay(geometry.document_objects(doc, symbol="world-t", prefix="shot/"),
                               hero_at(1.0, y))
    bounds = geometry.world_bounds(objects)
    groups = geometry.hero_groups(objects)
    return bounds | geometry.group_bounds(bounds, groups)


class Boxes(unittest.TestCase):
    """Each primitive's box in its own frame, from the schema's attributes."""

    def box(self, **attrs):
        return geometry.local_box({"id": "o", "primitive": "box"} | attrs)

    def test_a_box_uses_width_height_depth(self):
        b = self.box(width=10.0, height=4.0, depth=6.0)
        self.assertEqual((b["lo"], b["hi"]), ([-5.0, -2.0, -3.0], [5.0, 2.0, 3.0]))
        self.assertFalse(b["approximate"])

    def test_the_schema_defaults_apply_when_absent(self):
        # radius 50, so width and height 100; depth 10
        b = self.box()
        self.assertEqual((b["lo"], b["hi"]), ([-50.0, -50.0, -5.0], [50.0, 50.0, 5.0]))
        s = geometry.local_box({"id": "s", "primitive": "sphere", "radius": 7.0})
        self.assertEqual((s["lo"], s["hi"]), ([-7.0] * 3, [7.0] * 3))

    def test_a_plane_has_no_thickness(self):
        p = geometry.local_box({"id": "p", "primitive": "plane", "width": 30.0, "height": 20.0})
        self.assertEqual((p["lo"], p["hi"]), ([-15.0, -10.0, 0.0], [15.0, 10.0, 0.0]))

    def test_round_solids_stand_along_y(self):
        c = geometry.local_box({"id": "c", "primitive": "cylinder", "radius": 5.0, "height": 40.0})
        self.assertEqual((c["lo"], c["hi"]), ([-5.0, -20.0, -5.0], [5.0, 20.0, 5.0]))
        k = geometry.local_box({"id": "k", "primitive": "capsule", "radius": 5.0})
        self.assertEqual(k["hi"][1], 10.0)      # height defaults to four radii
        t = geometry.local_box({"id": "t", "primitive": "torus", "radius": 10.0})
        self.assertAlmostEqual(t["hi"][0], 13.5)    # ring plus half the tube (0.7 r)
        self.assertAlmostEqual(t["hi"][1], 3.5)

    def test_an_extrude_takes_its_path_bounds_and_depth(self):
        e = geometry.local_box({"id": "e", "primitive": "extrude",
                                "path": "M 0 15 L -12 -15 L 0 -15 Z", "depth": 0.16})
        self.assertEqual(e["lo"], [-12.0, -15.0, -0.08])
        self.assertEqual(e["hi"], [0.0, 15.0, 0.08])
        self.assertFalse(e["approximate"])

    def test_path_bounds_follow_relative_and_curve_commands(self):
        lo, hi = geometry.path_bounds("M 10 10 l 5 -5 h 10 v 20 C 0 0 40 40 20 20 z")
        self.assertEqual((lo, hi), ((0.0, 0.0), (40.0, 40.0)))

    def test_a_mesh_is_a_unit_cube_marked_approximate(self):
        m = geometry.local_box({"id": "m", "primitive": "mesh", "mesh": "model"})
        self.assertEqual((m["lo"], m["hi"]), ([-0.5] * 3, [0.5] * 3))
        self.assertTrue(m["approximate"])
        t = geometry.local_box({"id": "t", "primitive": "text", "text": "ab", "height": 10.0})
        self.assertTrue(t["approximate"])
        self.assertEqual(t["hi"][1], 5.0)

    def test_what_has_no_extent_is_no_box(self):
        self.assertIsNone(geometry.local_box({"id": "v", "primitive": "volume"}))


class Transforms(unittest.TestCase):
    """World boxes: the eight corners rotated and scaled, the extent taken,
    the parent chain composed."""

    def test_a_rotated_thin_plane_lies_flat(self):
        objects = [{"id": "floor", "primitive": "plane", "width": 100.0, "height": 100.0,
                    "rotationX": 90.0, "y": -3.0}]
        b = geometry.world_bounds(objects)["floor"]
        self.assertAlmostEqual(b["lo"][0], -50.0)
        self.assertAlmostEqual(b["hi"][0], 50.0)
        self.assertAlmostEqual(b["lo"][1], -3.0, places=6)     # no thickness in y
        self.assertAlmostEqual(b["hi"][1], -3.0, places=6)
        self.assertAlmostEqual(b["lo"][2], -50.0)
        self.assertAlmostEqual(b["hi"][2], 50.0)

    def test_a_yawed_box_grows_its_footprint(self):
        objects = [{"id": "b", "primitive": "box", "width": 10.0, "height": 2.0, "depth": 10.0,
                    "rotationY": 45.0}]
        b = geometry.world_bounds(objects)["b"]
        self.assertAlmostEqual(b["hi"][0], 10 / 2 ** 0.5, places=6)
        self.assertAlmostEqual(b["hi"][2], 10 / 2 ** 0.5, places=6)
        self.assertAlmostEqual(b["hi"][1], 1.0, places=6)

    def test_scale_applies_before_rotation_and_position(self):
        objects = [{"id": "b", "primitive": "box", "width": 2.0, "height": 2.0, "depth": 2.0,
                    "scaleX": 3.0, "x": 10.0, "y": -5.0, "z": 1.0}]
        b = geometry.world_bounds(objects)["b"]
        self.assertEqual(b["lo"], [7.0, -6.0, 0.0])
        self.assertEqual(b["hi"], [13.0, -4.0, 2.0])

    def test_a_child_follows_its_parent_by_the_parent_attribute(self):
        objects = [
            {"id": "p", "primitive": "box", "width": 0.1, "height": 0.1, "depth": 0.1,
             "x": 100.0, "rotationY": 90.0, "scaleX": 2.0, "scaleY": 2.0, "scaleZ": 2.0},
            {"id": "c", "primitive": "box", "width": 2.0, "height": 2.0, "depth": 2.0,
             "x": 10.0, "parent": "p"},
        ]
        b = geometry.world_bounds(objects)["c"]
        centre = [(lo + hi) / 2 for lo, hi in zip(b["lo"], b["hi"])]
        # the child's offset turns with the parent: 20 units (scaled) along z, none along x
        self.assertAlmostEqual(centre[0], 100.0, places=6)
        self.assertAlmostEqual(abs(centre[2]), 20.0, places=6)
        self.assertAlmostEqual(b["hi"][0] - b["lo"][0], 4.0, places=6)    # scaled by the parent

    def test_a_parent_cycle_does_not_hang(self):
        objects = [{"id": "a", "primitive": "box", "parent": "b"},
                   {"id": "b", "primitive": "box", "parent": "a"}]
        self.assertEqual(set(geometry.world_bounds(objects)), {"a", "b"})


class Documents(unittest.TestCase):
    """Parsing: the symbol's object3D nodes with typed attributes, qualified
    by the include they are drawn through."""

    def test_objects_of_one_symbol_are_typed_and_prefixed(self):
        rows = geometry.document_objects(world_doc(), symbol="world-t", prefix="shot/")
        ids = [r["id"] for r in rows]
        self.assertEqual(ids, ["shot/floor", "shot/desk", "shot/glass", "shot/hero-box"])
        desk = next(r for r in rows if r["id"] == "shot/desk")
        self.assertEqual((desk["primitive"], desk["x"], desk["y"], desk["z"]),
                         ("box", 0.0, -40.0, 100.0))
        self.assertEqual(desk["width"], 100.0)
        glass = next(r for r in rows if r["id"] == "shot/glass")
        self.assertEqual(glass["opacity"], 0.2)

    def test_a_parent_is_qualified_too(self):
        doc = world_doc(extra=[{"id": "fin", "primitive": "box", "material": "m",
                                "parent": "hero-box", "y": "5", "width": "2", "height": "2",
                                "depth": "2", "castShadow": "false"}])
        rows = geometry.document_objects(doc, symbol="world-t", prefix="s/")
        fin = next(r for r in rows if r["id"] == "s/fin")
        self.assertEqual(fin["parent"], "s/hero-box")
        self.assertIs(fin["castShadow"], False)

    def test_a_scene_resolves_its_includes_by_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "vfx" / "v01").mkdir(parents=True)
            (root / "vfx" / "v01" / "a.scene.xml").write_text(
                world_doc(rest="hero-box desk 3.5"), encoding="utf-8")
            scene = ('<?xml version="1.0"?><scene version="1.3"><project width="1920" '
                     'height="1080" fps="30" duration="7"/><composition>'
                     '<include id="shot-1.1" start="0" end="4" src="vfx/v01/a.scene.xml" '
                     'symbol="world-t"/><include id="shot-2.1" start="4" end="7" '
                     'src="vfx/v01/a.scene.xml" symbol="world-t"/></composition></scene>')
            (root / "scene.xml").write_text(scene, encoding="utf-8")
            rows = geometry.scene_objects(scene, root)
            ids = [r["id"] for r in rows]
            self.assertIn("shot-1.1/hero-box", ids)
            self.assertIn("shot-2.1/desk", ids)
            self.assertEqual(len(rows), 8)
            rests = geometry.scene_rests(scene, root)
            self.assertEqual(rests, [{"hero": "shot-1.1/hero-box", "surface": "shot-1.1/desk",
                                      "at": 3.5},
                                     {"hero": "shot-2.1/hero-box", "surface": "shot-2.1/desk",
                                      "at": 3.5}])
            self.assertEqual(geometry.document_duration(scene), 7.0)

    def test_a_declared_rest_is_read_from_meta(self):
        doc = world_doc(rest="hero-box desk 3.5")
        self.assertEqual(geometry.rests_declared(doc, prefix="shot/"),
                         [{"hero": "shot/hero-box", "surface": "shot/desk", "at": 3.5}])
        self.assertEqual(geometry.rests_declared(world_doc()), [])
        self.assertEqual(geometry.rests_declared('<meta name="hero-rests" value="x y"/>'), [])

    def test_overlay_applies_the_evaluated_props_and_drops_what_is_not_drawn(self):
        rows = geometry.document_objects(world_doc(), symbol="world-t", prefix="shot/")
        now = geometry.overlay(rows, frame(2.0, present=("desk", "hero-box"),
                                           hero_box={"y": -70.0, "rotationY": 30.0}))
        self.assertEqual([r["id"] for r in now], ["shot/desk", "shot/hero-box"])
        hero = now[-1]
        self.assertEqual((hero["y"], hero["rotationY"]), (-70.0, 30.0))
        self.assertEqual(rows[-1]["y"], -150.0)                 # the parse is untouched
        self.assertEqual(len(geometry.overlay(rows, None)), 4)  # no frame: the document's values

    def test_heroes_and_solids(self):
        doc = world_doc(extra=[
            {"id": "fin", "primitive": "box", "material": "m", "parent": "hero-box",
             "width": "2", "height": "2", "depth": "2"},
            {"id": "shadow", "primitive": "sphere", "material": "m", "radius": "5",
             "castShadow": "false", "receiveShadow": "false"},
            {"id": "catcher", "primitive": "plane", "material": "m", "shadowCatcher": "true"},
            {"id": "ghost", "primitive": "box", "material": "m", "opacity": "0.1"}])
        rows = geometry.document_objects(doc, symbol="world-t", prefix="s/")
        self.assertEqual(geometry.hero_groups(rows), {"s/hero-box": ["s/hero-box", "s/fin"]})
        self.assertEqual(geometry.solids(rows, exclude=("s/hero-box", "s/fin")),
                         ["s/floor", "s/desk"])


class Penetration(unittest.TestCase):
    """The records: a merge deeper than the tolerance, never a resting contact."""

    def setUp(self):
        self.doc = world_doc()

    def test_a_hero_through_the_desk_is_a_penetration_with_depth_and_axis(self):
        bounds = bounds_at(self.doc, DESK_TOP + 5)         # 15 of its 20 units inside
        found = geometry.penetrations(bounds, ["shot/hero-box"], ["shot/floor", "shot/desk"],
                                      time=2.5)
        self.assertEqual(len(found), 1)
        row = found[0]
        self.assertEqual((row["time"], row["hero"], row["object"], row["axis"]),
                         (2.5, "shot/hero-box", "shot/desk", "y"))
        self.assertAlmostEqual(row["depth"], 15.0)
        self.assertAlmostEqual(row["tolerance"], HERO * geometry.TOLERANCE_SHARE)
        self.assertFalse(row["approximate"])

    def test_a_resting_contact_within_the_tolerance_passes(self):
        bounds = bounds_at(self.doc, DESK_TOP - HERO / 2 + 1.0)   # 1 unit in; tolerance is 2
        self.assertEqual(geometry.penetrations(bounds, ["shot/hero-box"], ["shot/desk"]), [])
        bounds = bounds_at(self.doc, DESK_TOP - HERO / 2 - 30)    # well above
        self.assertEqual(geometry.penetrations(bounds, ["shot/hero-box"], ["shot/desk"]), [])

    def test_the_tolerance_is_a_tenth_of_the_smallest_dimension(self):
        box = {"lo": [0.0, 0.0, 0.0], "hi": [40.0, 4.0, 30.0], "approximate": False}
        self.assertAlmostEqual(geometry.tolerance_of(box), 0.4)
        bounds = bounds_at(self.doc, DESK_TOP + 5)
        self.assertEqual(geometry.penetrations(bounds, ["shot/hero-box"], ["shot/desk"],
                                               tolerance=20.0), [])

    def test_floating_at_the_declared_rest(self):
        bounds = bounds_at(self.doc, DESK_TOP - HERO / 2 - 5)     # 5 units of air
        row = geometry.floating(bounds, "shot/hero-box", "shot/desk", at=3.5)
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row["gap"], 5.0)
        self.assertEqual((row["time"], row["hero"], row["surface"]), (3.5, "shot/hero-box", "shot/desk"))
        touching = bounds_at(self.doc, DESK_TOP - HERO / 2 + 0.5)
        self.assertIsNone(geometry.floating(touching, "shot/hero-box", "shot/desk", at=3.5))

    def test_an_approximate_box_marks_its_record(self):
        doc = world_doc(extra=[{"id": "hero-model", "primitive": "mesh", "material": "m",
                                "mesh": "model", "x": "0", "y": str(DESK_TOP), "z": "100",
                                "scaleX": "20", "scaleY": "20", "scaleZ": "20"}])
        rows = geometry.overlay(geometry.document_objects(doc, symbol="world-t", prefix="s/"),
                                None)
        bounds = geometry.world_bounds(rows)
        found = geometry.penetrations(bounds, ["s/hero-model"], ["s/desk"])
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["approximate"])


class Sampling(unittest.TestCase):
    def test_times_step_through_every_shot(self):
        shots = [{"label": "1.1", "at": 0, "seconds": 1.0}, {"label": "1.2", "at": 1.0, "seconds": 0.5}]
        self.assertEqual(geometry.sample_times(shots), [0.0, 0.25, 0.5, 0.75, 1.0, 1.25])
        self.assertEqual(geometry.sample_times(shots, step=0.5), [0.0, 0.5, 1.0])

    def test_without_shots_the_duration_is_stepped(self):
        self.assertEqual(geometry.sample_times([], step=0.5, duration=1.2), [0.0, 0.5, 1.0])
        self.assertEqual(geometry.sample_times([]), [])


class Evaluate(unittest.TestCase):
    """One runner call for every time; records per time; the worst per pair."""

    def setUp(self):
        self.doc = world_doc()
        self.objects = geometry.document_objects(self.doc, symbol="world-t", prefix="shot/")

    def runner(self, path):
        """A fake of the engine seam: the hero descends along `path`
        (time -> y), and the call is recorded."""
        calls = []

        def run(times):
            calls.append(list(times))
            return [hero_at(t, path(t)) for t in times]
        return calls, run

    def test_records_name_time_hero_object_and_depth(self):
        calls, run = self.runner(lambda t: -150 + 30 * t)   # through the desk from 2 s
        times = geometry.sample_times([{"at": 0, "seconds": 4}], step=0.5)
        report = geometry.evaluate(self.objects, times, run)
        self.assertEqual(calls, [times])                     # one job, every time
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["evaluated"], len(times))
        hit = [r["time"] for r in report["penetrations"]]
        self.assertEqual(hit, [2.5, 3.0, 3.5])
        worst = report["worst"]
        self.assertEqual(len(worst), 1)
        # at 2.5 s the hero is 15 units in; from 3 s it is wholly inside, and the
        # least separation is its own 20-unit width
        self.assertEqual((worst[0]["hero"], worst[0]["object"], worst[0]["time"]),
                         ("shot/hero-box", "shot/desk", 3.0))
        self.assertAlmostEqual(worst[0]["depth"], 20.0)
        self.assertAlmostEqual(report["penetrations"][0]["depth"], 15.0)
        self.assertEqual((worst[0]["first"], worst[0]["last"], worst[0]["count"]), (2.5, 3.5, 3))
        self.assertEqual(report["heroes"], {"shot/hero-box": ["shot/hero-box"]})
        self.assertIn("shot/desk", report["solids"])
        self.assertNotIn("shot/glass", report["solids"])

    def test_a_clean_flight_and_a_declared_rest(self):
        rest_y = DESK_TOP - HERO / 2 + 0.5
        calls, run = self.runner(lambda t: -150 if t < 3 else rest_y)
        times = geometry.sample_times([{"at": 0, "seconds": 4}], step=0.5)
        rests = geometry.rests_declared(world_doc(rest="hero-box desk 3.5"), prefix="shot/")
        report = geometry.evaluate(self.objects, times, run, rests=rests)
        self.assertEqual(report["penetrations"], [])
        self.assertEqual(report["floating"], [])
        self.assertEqual(calls[0][-1], 3.5)                 # the rest time is evaluated
        # the same flight that never comes down floats at its rest
        calls, run = self.runner(lambda t: -150)
        report = geometry.evaluate(self.objects, times, run, rests=rests)
        self.assertEqual(len(report["floating"]), 1)
        self.assertAlmostEqual(report["floating"][0]["gap"], 60.0)

    def test_a_frame_the_engine_could_not_evaluate_is_skipped_not_fatal(self):
        def run(times):
            return [None if t == 1.0 else hero_at(t, -150) for t in times]
        report = geometry.evaluate(self.objects, [0.5, 1.0, 1.5], run)
        self.assertEqual(report["evaluated"], 2)
        self.assertEqual(report["penetrations"], [])

    def test_no_runner_means_skipped_with_a_reason(self):
        report = geometry.evaluate(self.objects, [0.5], None)
        self.assertEqual(report["status"], "skipped")
        self.assertIn("engine", report["why"])

    def test_findings_name_the_time_the_object_the_depth_and_the_fix(self):
        calls, run = self.runner(lambda t: -150 + 30 * t)
        shots = [{"label": "1.1", "at": 0, "seconds": 2}, {"label": "1.2", "at": 2, "seconds": 2}]
        report = geometry.evaluate(self.objects, geometry.sample_times(shots, step=0.5), run)
        lines = geometry.findings(report, symbol="world-t", shots=shots)
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertTrue(line.startswith("error:"), line)
        for needle in ("hero-box", "desk", "3s", "20.0", "3.5s", "shot 1.2", "move the path of",
                       "clears desk by at least 22.0"):
            self.assertIn(needle, line)


class Runner(unittest.TestCase):
    """The production runner: one job whose shell loop evaluates every time."""

    @classmethod
    def setUpClass(cls):
        hermetic.fake_remote_class(cls)

    def test_the_script_links_the_job_dir_and_loops_eval_per_time(self):
        script = geometry.eval_script("/tmp/yt-render/p/mirror", "/tmp/yt-render/p/eval-1-ab",
                                      shlex.quote("/root/render/scene-render"),
                                      "vfx/v01/.proof-a.scene.xml", [0.25, 0.5, 13.0])
        self.assertIn("cp -al", script)
        self.assertIn("cd /tmp/yt-render/p/eval-1-ab", script)
        self.assertEqual(script.count("eval "), 1)
        self.assertIn("for t in 0.25 0.5 13", script)
        self.assertIn("--time $t --format json", script)
        self.assertIn("B=/root/render/scene-render;", script)

    def test_the_output_is_one_envelope_per_time_in_order(self):
        good = json.dumps(hero_at(0.25, -150))
        bad = json.dumps({"ok": False, "error": {"message": "x"}, "result": None})
        frames = geometry.parse_frames(good + "\n" + bad + "\n" + good + "\n", 4)
        self.assertEqual(len(frames), 4)
        self.assertEqual(frames[0]["result"]["frame"], 8)
        self.assertIsNone(frames[1])
        self.assertIsNone(frames[3])                         # the engine stopped early

    def test_without_an_engine_or_a_host_there_is_no_runner(self):
        with patch.object(geometry.render, "engine", lambda: None), \
                patch.object(geometry.render, "remote_host", lambda *a, **k: ""):
            self.assertIsNone(geometry.engine_runner("scene.xml", root=Path(".")))

    def test_a_remote_runner_syncs_once_runs_one_ssh_and_removes_the_job(self):
        ran = []

        def fake_run(argv, **kw):
            ran.append(argv)
            out = "\n".join(json.dumps(hero_at(t, -150)) for t in (0.25, 0.5)) + "\n"
            return subprocess.CompletedProcess(argv, 0, stdout=out if argv[0] == "ssh" and
                                               "eval" in " ".join(argv) else "", stderr="")
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(geometry.render, "engine", lambda: None), \
                patch.object(geometry.render, "probe",
                             lambda h, **k: geometry.render.Host(addr=h, engine="/r/scene-render")), \
                patch.object(geometry.subprocess, "run", fake_run):
            root = Path(tmp) / "proj"
            root.mkdir()
            (root / "scene.xml").write_text("<scene/>")
            run = geometry.engine_runner("scene.xml", root=root, host="root@h")
            frames = run([0.25, 0.5])
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[1]["result"]["frame"], 15)
        kinds = [a[0] for a in ran]
        self.assertEqual(kinds.count("rsync"), 1)                # the mirror, once
        evals = [a for a in ran if a[0] == "ssh" and "--format json" in a[-1]]
        self.assertEqual(len(evals), 1)                           # one job for both times
        self.assertIn("for t in 0.25 0.5", evals[0][-1])
        self.assertIn("B=/r/scene-render;", evals[0][-1])
        # removed afterwards (render.remove_remote's script, which rmdirs the parent too)
        self.assertTrue(any(a[0] == "ssh" and "rmdir" in a[-1] for a in ran))

    def test_a_scratch_bundle_is_materialised_and_its_mirror_removed(self):
        ran = []

        def fake_run(argv, **kw):
            ran.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(hero_at(1, -150)) + "\n",
                                               stderr="")
        with patch.object(geometry.render, "engine", lambda: None), \
                patch.object(geometry.render, "probe",
                             lambda h, **k: geometry.render.Host(addr=h, engine="/r/sr")), \
                patch.object(geometry.subprocess, "run", fake_run):
            run = geometry.engine_runner("vfx/v01/.proof-a.scene.xml",
                                         files={"vfx/v01/a.scene.xml": b"<scene/>",
                                                "vfx/v01/.proof-a.scene.xml": b"<scene/>"},
                                         host="root@h")
            frames = run([1.0])
        self.assertEqual(len(frames), 1)
        sync = next(a for a in ran if a[0] == "rsync")
        self.assertTrue(sync[-1].endswith("/mirror/"), sync)
        removes = [a[-1] for a in ran if a[0] == "ssh" and "rmdir" in a[-1]]
        self.assertEqual(len(removes), 2)                         # the job and the scratch mirror
        self.assertTrue(any(r.rstrip("/").endswith("/mirror") or "/mirror;" in r for r in removes), removes)


# --------------------------------------------------------------------------- #
# the hooks
# --------------------------------------------------------------------------- #

def varied_png() -> bytes:
    import io
    from PIL import Image
    im = Image.new("RGB", (64, 36))
    for x in range(64):
        for y in range(36):
            im.putpixel((x, y), ((x * 4) % 256, (y * 7) % 256, 120))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


SECTION = {"heading": "Open", "seconds": 4, "shots": [
    {"label": "1.1", "at": 0, "seconds": 2, "visual": "launch", "narration": ""},
    {"label": "1.2", "at": 2, "seconds": 2, "visual": "land", "narration": ""}]}


class VfxGate(unittest.TestCase):
    """The authoring gate evaluates the world's proof document at the sampled
    times and makes a penetration an error with the fix."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ws = vfx.Workspace(self.root, version="v01")
        self.name = self.ws.doc_name(1, SECTION)
        self.shots = {"world-t": SECTION["shots"]}

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, doc: str) -> None:
        (self.ws.dir / self.name).write_text(doc, encoding="utf-8")

    def runner_factory(self, path):
        made = []

        def factory(doc_path, *, files=None, root=None, host=""):
            made.append({"doc_path": doc_path, "files": sorted(files or {}), "host": host})

            def run(times):
                made[-1]["times"] = list(times)
                return [hero_at(t, path(t)) for t in times]
            return run
        return made, factory

    def test_a_merge_is_an_error_naming_time_object_depth_and_fix(self):
        self.write(world_doc())
        made, factory = self.runner_factory(lambda t: -150 + 30 * t)
        with patch.object(vfx, "EVAL_RUNNER", factory):
            found = self.ws.geometry(self.name, shots=self.shots)
        self.assertEqual(len(made), 1)
        self.assertTrue(made[0]["doc_path"].startswith("vfx/v01/.proof-"))
        self.assertIn(f"vfx/v01/{self.name}", made[0]["files"])
        self.assertIn(made[0]["doc_path"], made[0]["files"])
        self.assertEqual(made[0]["times"], geometry.sample_times(SECTION["shots"]))
        errors = [f for f in found if f.startswith("error")]
        self.assertEqual(len(errors), 1, found)
        for needle in ("world-t", "hero-box", "desk", "3.75s", "shot 1.2", "move the path of"):
            self.assertIn(needle, errors[0])

    def test_a_clean_flight_passes_and_says_what_was_checked(self):
        self.write(world_doc())
        made, factory = self.runner_factory(lambda t: -150)
        with patch.object(vfx, "EVAL_RUNNER", factory):
            found = self.ws.geometry(self.name, shots=self.shots)
        self.assertFalse([f for f in found if f.startswith("error")], found)
        self.assertTrue(any("note" in f and "penetration" in f for f in found), found)

    def test_validate_runs_the_geometry_gate_with_the_frames(self):
        self.write(world_doc())
        made, factory = self.runner_factory(lambda t: -150 + 30 * t)
        with patch.object(vfx.render, "engine_validate", lambda data, **kw: []), \
                patch.object(vfx.render, "engine_render_still",
                             lambda xml, at, **kw: (varied_png(), "rendered")), \
                patch.object(vfx, "encode_findings", lambda *a, **k: []), \
                patch.object(vfx, "EVAL_RUNNER", factory):
            symbols, findings = self.ws.validate(self.name, shots=self.shots)
        self.assertEqual(symbols, ["world-t"])
        self.assertTrue(any(f.startswith("error") and "penetrates" in f for f in findings), findings)

    def test_without_an_engine_the_gate_says_so(self):
        self.write(world_doc())
        with patch.object(vfx, "EVAL_RUNNER", lambda *a, **k: None):
            found = self.ws.geometry(self.name, shots=self.shots)
        self.assertTrue(any(f.startswith("note") and "engine" in f for f in found), found)

    def test_the_brief_tells_the_author_the_check_exists(self):
        from ytstudio.core import Idea
        idea = Idea(title="T", genre="cinematic", description="d")
        brief = self.ws.brief(1, idea=idea, house_rules="", style={}, tokens={"background": "#000"},
                              frame=(1920, 1080), section=SECTION, effects=[], words={}, assets=[])
        text = brief.read_text(encoding="utf-8")
        self.assertIn("penetrat", text)
        self.assertIn(geometry.REST_META, text)
        self.assertIn("0.25", text)


def _report(records, *, floating=()):
    return geometry.summarise(records, floating=list(floating), times=[0.25 * k for k in range(60)],
                              evaluated=60, heroes={"shot-1.1/hero-plane": ["shot-1.1/hero-plane"]},
                              solids=["shot-1.1/desk-top"])


def _record(t, depth=4.0):
    return {"time": t, "hero": "shot-1.1/hero-plane", "object": "shot-1.1/desk-top",
            "depth": depth, "axis": "y", "tolerance": 0.9, "approximate": False}


class QaHook(unittest.TestCase):
    """A `penetration` finding per sampled shot: blocking, owner vfx, with
    the numbers, listed under Mechanical and capping the scores."""

    def shots(self):
        return [{"label": "1.1", "at": 0.0, "seconds": 3.0}, {"label": "1.2", "at": 3.0, "seconds": 4.0},
                {"label": "1.3", "at": 7.0, "seconds": 4.0}, {"label": "1.4", "at": 11.0, "seconds": 4.0}]

    def test_records_are_grouped_by_the_shot_they_fall_in(self):
        report = _report([_record(12.75, 2.0), _record(13.0, 4.0), _record(14.5, 3.0)])
        by_shot = qa.geometry_by_shot(report, self.shots())
        self.assertEqual(list(by_shot), ["1.4"])
        self.assertEqual(len(by_shot["1.4"]), 3)
        self.assertEqual(qa.geometry_by_shot({"status": "skipped"}, self.shots()), {})

    def test_a_penetration_finding_is_blocking_and_owned_by_vfx(self):
        samples = [{"t": 13.0, "label": "1.4", "frame": "t_00m13s000.png", "asset": "effect",
                    "why": "ending", "still": {"mean": 120, "spread": 40, "dominant_share": 0.2,
                                               "clipped_share": 0.0},
                    "motion": {"frozen": False, "jump": None, "steps": []}},
                   {"t": 5.0, "label": "1.2", "frame": "t_00m05s000.png", "asset": "effect",
                    "why": "25%", "still": {"mean": 120, "spread": 40, "dominant_share": 0.2,
                                            "clipped_share": 0.0},
                    "motion": {"frozen": False, "jump": None, "steps": []}}]
        report = _report([_record(12.75, 2.0), _record(13.0, 4.0)])
        found = qa.mechanical_findings(samples, geometry=qa.geometry_by_shot(report, self.shots()))
        self.assertEqual([f["kind"] for f in found], ["penetration"])
        row = found[0]
        self.assertEqual((row["shot"], row["severity"], row["owner"], row["timestamp"]),
                         ("1.4", "blocking", "vfx", 13.0))
        for needle in ("hero-plane", "desk-top", "4.0", "13", "12.75"):
            self.assertIn(needle, row["problem"])
        scores = {k: 9.0 for k in qa.CATEGORIES}
        capped_scores, overall, capped = qa.cap_scores(scores, 9.0, found)
        self.assertEqual(sorted(capped), sorted(qa.CAPPED))
        self.assertEqual(capped_scores["visual_quality"], qa.MECHANICAL_CAP)
        card = {"verdict": qa.NOT_READY, "overall": overall, "scores": capped_scores,
                "findings": [], "mechanical": found, "capped": capped}
        md = qa.findings_md("pass-08", card, samples, qa.compare(None, card), reasons=[])
        mechanical = md.split("## Mechanical", 1)[1]
        self.assertIn("penetration", mechanical)
        self.assertIn("4.0", mechanical)
        self.assertIn("**vfx**", mechanical)

    def test_the_engine_seam_is_a_module_level_callable(self):
        self.assertTrue(callable(qa.GEOMETRY))
        self.assertIs(qa.GEOMETRY, qa.engine_geometry)


class FakeEngine:
    """The gate's supervisor: answers the triage from a script."""
    answers: list = []
    calls: list = []

    def __init__(self, config):
        self.config = config
        self.usage = Usage()
        self.usage.cost_usd = 0.01
        self.usage.calls = 1

    def ask_json(self, prompt, schema, system=None):
        FakeEngine.calls.append(prompt)
        return FakeEngine.answers.pop(0) if FakeEngine.answers else {
            "blocking": [], "acceptable": [], "verdict": "deliverable", "notes": ""}

    def stream(self, prompt, system=None):
        yield Event(type="result", raw={})


class GateHook(unittest.TestCase):
    """The delivery gate adds a BLOCKING `hero penetrates set` item from the
    geometry check, whatever the tool and the supervisor said."""

    def test_rows_carry_the_worst_instances_owner_and_fix(self):
        report = _report([_record(12.75, 2.0), _record(13.0, 4.0), _record(14.5, 3.0)])
        rows = gate.geometry_rows(report)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["what"].startswith("hero penetrates set"))
        for needle in ("hero-plane", "desk-top", "4.0", "13s", "12.75", "14.5", "3 of 60"):
            self.assertIn(needle, rows[0]["what"])
        self.assertEqual(rows[0]["owner"], "vfx")
        self.assertIn("yt vfx", rows[0]["fix"])
        self.assertEqual(gate.geometry_rows(_report([])), [])
        self.assertEqual(gate.geometry_rows({"status": "skipped", "why": "no engine"}), [])

    def test_the_triage_cannot_pass_a_film_with_a_merge(self):
        triage = {"blocking": [], "acceptable": [], "verdict": "deliverable", "notes": ""}
        out = gate.apply_geometry(triage, _report([_record(13.0)]))
        self.assertEqual(out["verdict"], "not deliverable")
        self.assertEqual(len(out["blocking"]), 1)
        self.assertEqual(gate.exit_code(0, out), 1)
        clean = gate.apply_geometry(dict(triage), _report([]))
        self.assertEqual(clean["verdict"], "deliverable")

    def test_run_records_the_geometry_in_gate_json_and_fails(self):
        FakeEngine.answers = [{"blocking": [], "acceptable": [], "verdict": "deliverable",
                               "notes": "all fine"}]
        FakeEngine.calls = []
        kept = harness.ENGINE
        harness.ENGINE = FakeEngine
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "a-film"
                root.mkdir()
                (root / "scene.xml").write_text("<scene/>", encoding="utf-8")
                (root / "video.mp4").write_bytes(b"\0")
                (root / "render.json").write_text("{}", encoding="utf-8")
                (root / "narration.vtt").write_text("WEBVTT\n", encoding="utf-8")
                found = {"scene": str(root / "scene.xml"), "video": str(root / "video.mp4"),
                         "encode report": str(root / "render.json"),
                         "captions": str(root / "narration.vtt"), "engine": "/e", "ffprobe": "/f",
                         "gate": "/g/production_gate.py", "tools dir": "/t", "voice end": 15.0}
                args = argparse.Namespace(
                    title="A film", project=str(root), scene=None, video=None, render=None,
                    captions=[], voice=None, build=None, voice_end=15.0, tools_dir=None,
                    loudness_target=-14.0, max_illegal=None, frames=False, preflight=False,
                    fresh=True, host=None, local=True, keep_remote=False, quiet=True,
                    model="sonnet", work=str(Path(tmp)), env_file=None)
                seen = []

                def fake_geometry(a, project, scene, shots):
                    seen.append((str(scene), shots))
                    return _report([_record(13.0, 4.0)])

                passed = subprocess.CompletedProcess(["python3"], 0, stdout="TECHNICAL PASS", stderr="")
                with patch.object(gate, "inputs", lambda project, a, host="": (found, [])), \
                        patch.object(gate.subprocess, "run", lambda *a, **k: passed), \
                        patch.object(gate, "GEOMETRY", fake_geometry), \
                        patch.object(gate.house, "for_args", lambda *a, **k: ("", "")), \
                        contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    code = gate.run(args)
                data = json.loads((root / "gate.json").read_text(encoding="utf-8"))
        finally:
            harness.ENGINE = kept
        self.assertEqual(code, 1)
        self.assertEqual(seen[0][0], str(root / "scene.xml"))
        self.assertEqual(data["tool_exit"], 0)
        self.assertEqual(data["exit"], 1)
        self.assertEqual(data["triage"]["verdict"], "not deliverable")
        self.assertTrue(data["triage"]["blocking"][0]["what"].startswith("hero penetrates set"))
        self.assertEqual(data["geometry"]["status"], "ok")
        self.assertEqual(len(data["geometry"]["worst"]), 1)
        self.assertIn("penetrat", FakeEngine.calls[0])          # the supervisor was told


# --------------------------------------------------------------------------- #
# the screen: does the airborne hero read as merged into the desk?
# --------------------------------------------------------------------------- #

def screen_doc(*, light: dict | None = None, hero_shadow: bool = True) -> str:
    """A camera looking down at a box desk (top at y = -80) and a hero box
    keyed on y, built with the SDK. The camera is fixed (no target)."""
    S = sdk()
    sc = S.Scene(version="1.3")
    sc.add_project(width="1920", height="1080", fps="30", duration="4", background="#101418")
    sc.add_output(path="p.mp4", codec="h264")
    sc.add_materials().add_material(id="m", baseColor="#AAAAAA")
    sym = sc.add_symbols().add_symbol(id="world-t", width="1920", height="1080", duration="4")
    sym.add_camera(id="cam", x="0", y="-200", z="-50", yaw="0", pitch="-40",
                   focalLength="35", active="true")
    sym.add_object3D(id="desk", primitive="box", material="m", x="0", y="-40", z="100",
                     width="100", height="80", depth="100")
    hero = sym.add_object3D(id="hero-box", primitive="box", material="m", x="0", y="-100",
                            z="100", width="20", height="20", depth="20",
                            **({} if hero_shadow else {"castShadow": "false"}))
    hero.add_animate(property="y").add_key(time="0", value="-100")
    sc.add_composition().add_instance(id="use", symbol="world-t", start="0", end="4")
    lights = sc.add_lights()
    lights.add_light(id="fill", type="ambient", intensity="1")
    if light:
        lights.add_light(**light)
    return sc.to_bytes(layout="pretty").decode("utf-8")


SUN_DOWN = {"id": "sun", "type": "directional", "intensity": "2", "yaw": "0", "pitch": "-90",
            "castShadow": "true"}


def cam_frame(t: float, hero_y: float, *, prefix: str = "shot/", lights=()) -> dict:
    """eval's envelope with the camera node first (the result's `camera`
    indexes it) and the light elements."""
    nodes = [{"id": prefix.rstrip("/"), "kind": "include", "draw": True, "props": {}},
             {"id": f"{prefix}cam", "kind": "camera", "draw": True, "parent": 0, "props": {}}]
    for oid in ("desk", "hero-box"):
        nodes.append({"id": f"{prefix}{oid}", "kind": "object3D", "draw": True, "parent": 0,
                      "props": {"y": hero_y} if oid == "hero-box" else {}})
    return {"ok": True, "format": "scene-render-cli/1",
            "result": {"frame": int(round(t * 30)), "camera": 1, "nodes": nodes,
                       "elements": [{"element": "lightType", "key": k, "props": {"intensity": 2.0}}
                                    for k in lights]}}


def screen_report(doc: str, path, times=None, lights=()):
    objects = geometry.document_objects(doc, symbol="world-t", prefix="shot/")
    cameras = geometry.document_cameras(doc, symbol="world-t", prefix="shot/")
    times = times or geometry.sample_times([{"at": 0, "seconds": 4}])
    return geometry.evaluate(objects, times, lambda ts: [cam_frame(t, path(t), lights=lights)
                                                         for t in ts],
                             cameras=cameras, lights=geometry.document_lights(doc),
                             frame=(1920, 1080))


class Projection(unittest.TestCase):
    """The camera, as the schema describes it and as one render measured it."""

    def test_the_camera_conventions(self):
        self.assertEqual([round(v, 6) for v in geometry.direction(0, 0)], [0, 0, 1])
        self.assertEqual([round(v, 6) for v in geometry.direction(90, 0)], [1, 0, 0])
        self.assertEqual([round(v, 6) for v in geometry.direction(0, -90)], [0, 1, 0])  # down
        pose = geometry.camera_pose({"x": 0, "y": 0, "z": 0, "focalLength": 36, "sensorWidth": 36})
        x, y, depth = geometry.project([0, 0, 100], pose, (1920, 1080))
        self.assertEqual((round(x), round(y), round(depth)), (960, 540, 100))
        x, y, _ = geometry.project([10, 10, 100], pose, (1920, 1080))
        self.assertAlmostEqual(x, 960 + 1920 * 10 / 100)      # f = W * focal / sensor
        self.assertGreater(y, 540)                            # +y is down on screen too
        self.assertIsNone(geometry.project([0, 0, -5], pose, (1920, 1080)))   # behind

    def test_a_target_replaces_yaw_and_pitch(self):
        bounds = {"t": {"lo": [99, -1, 99], "hi": [101, 1, 101], "approximate": False}}
        pose = geometry.camera_pose({"x": 0, "y": 0, "z": 0, "target": "t", "yaw": -70}, bounds)
        self.assertAlmostEqual(pose["yaw"], 45.0)
        x, y, _ = geometry.project([100, 0, 100], pose, (1920, 1080))
        self.assertAlmostEqual(x, 960, places=6)

    def test_the_projection_matches_the_engine_render(self):
        """The calibration still rendered on the host (camera yaw 12, pitch
        -14, roll 10, 40 mm; a box turned 20 deg about x and 15 about z)
        drew the box's silhouette over pixels (870, 238)-(1158, 747)."""
        cam = {"x": 20, "y": -120, "z": -260, "yaw": 12, "pitch": -14, "roll": 10,
               "focalLength": 40, "sensorWidth": 36}
        box = {"id": "b", "primitive": "box", "x": 110, "y": -30, "z": 120, "width": 30.0,
               "height": 80.0, "depth": 50.0, "rotationX": 20.0, "rotation": 15.0}
        pose = geometry.camera_pose(cam)
        transform = geometry._matrix(box)
        pts = [geometry.project(geometry._apply(transform, c), pose, (1920, 1080))
               for c in geometry._corners(geometry.local_box(box))]
        rect = (min(p[0] for p in pts), min(p[1] for p in pts),
                max(p[0] for p in pts), max(p[1] for p in pts))
        for got, drawn in zip(rect, (870, 238, 1158, 747)):
            self.assertLess(abs(got - drawn), 1.0)

    def test_screen_rects_clip_to_the_frame_and_the_top_face(self):
        pose = geometry.camera_pose({"x": 0, "y": -200, "z": -50, "pitch": -40, "focalLength": 35})
        desk = {"lo": [-50, -80, 50], "hi": [50, 0, 150], "approximate": False}
        whole = geometry.screen_rect(desk, pose, (1920, 1080))
        top = geometry.screen_rect(desk, pose, (1920, 1080), top_only=True)
        self.assertTrue(0 <= whole[0] < whole[2] <= 1920)
        self.assertLessEqual(whole[1], top[1])
        self.assertLess(top[3], whole[3])                     # the sides hang below the top


class ReadsMerged(unittest.TestCase):
    """A hero a little above a farther surface, over it on screen, with no
    shadow on it, for half a second or more."""

    def test_a_hero_just_above_the_desk_reads_merged(self):
        report = screen_report(screen_doc(), lambda t: -100.0)     # 10 above the top
        self.assertEqual(report["screen"], "checked")
        spans = report["reads_merged"]
        self.assertEqual(len(spans), 1, spans)
        span = spans[0]
        self.assertEqual((span["hero"], span["object"], span["first"], span["last"]),
                         ("shot/hero-box", "shot/desk", 0.0, 3.75))
        self.assertEqual(span["seconds"], 4.0)
        self.assertAlmostEqual(span["clearance"], 10.0)
        self.assertGreater(span["overlap"], geometry.OVERLAP_SHARE)
        self.assertLess(span["ratio"], geometry.CLEARANCE_RATIO)
        self.assertEqual(report["penetrations"], [])
        lines = geometry.findings(report, symbol="world-t")
        self.assertTrue(lines[0].startswith("error:"))
        for needle in ("reads merged", "desk", "0s to 3.75s", "10.0 units", "shadow",
                       "line of sight", "separate them on screen"):
            self.assertIn(needle, lines[0])

    def test_a_shadow_on_the_desk_is_the_grounding_cue(self):
        report = screen_report(screen_doc(light=SUN_DOWN), lambda t: -100.0, lights=("sun",))
        self.assertEqual(report["reads_merged"], [])

    def test_a_shadow_needs_both_a_shadow_light_and_a_casting_hero(self):
        noshadow = dict(SUN_DOWN, castShadow="false")
        report = screen_report(screen_doc(light=noshadow), lambda t: -100.0, lights=("sun",))
        self.assertEqual(len(report["reads_merged"]), 1)
        report = screen_report(screen_doc(light=SUN_DOWN, hero_shadow=False), lambda t: -100.0,
                               lights=("sun",))
        self.assertEqual(len(report["reads_merged"]), 1)
        self.assertFalse(report["reads_merged"][0]["casts"])
        self.assertIn("castShadow=\"false\" on every part",
                      geometry.merged_advice(report["reads_merged"][0]))

    def test_a_shadow_that_lands_off_the_surface_is_no_cue(self):
        grazing = dict(SUN_DOWN, yaw="90", pitch="-10")      # throws the shadow far along +x
        report = screen_report(screen_doc(light=grazing), lambda t: -100.0, lights=("sun",))
        self.assertEqual(len(report["reads_merged"]), 1)

    def test_a_short_overlap_passes(self):
        # near the desk for one sample (0.25 s), far above it otherwise
        report = screen_report(screen_doc(), lambda t: -100.0 if t == 1.0 else -190.0)
        self.assertEqual(report["reads_merged"], [])
        report = screen_report(screen_doc(), lambda t: -100.0 if 1.0 <= t < 1.5 else -190.0)
        self.assertEqual([(s["first"], s["last"], s["seconds"]) for s in report["reads_merged"]],
                         [(1.0, 1.25, 0.5)])

    def test_a_hero_far_above_has_parallax_and_passes(self):
        report = screen_report(screen_doc(), lambda t: -170.0)     # 80 above, distance ~150
        self.assertEqual(report["reads_merged"], [])

    def test_a_resting_contact_is_not_a_merge(self):
        report = screen_report(screen_doc(), lambda t: -90.5)      # 0.5 above: touching
        self.assertEqual(report["reads_merged"], [])

    def test_a_hidden_top_face_is_not_a_surface(self):
        bounds = {"top": {"lo": [-50, -82, 50], "hi": [50, -78, 150], "approximate": False},
                  "apron": {"lo": [-45, -77, 140], "hi": [45, -70, 145], "approximate": False}}
        pose = geometry.camera_pose({"x": 0, "y": -200, "z": -50, "pitch": -40, "focalLength": 35})
        self.assertTrue(geometry.top_visible("top", bounds, ["top", "apron"], pose))
        self.assertFalse(geometry.top_visible("apron", bounds, ["top", "apron"], pose))

    def test_without_a_camera_the_screen_is_not_checked(self):
        doc = screen_doc()
        objects = geometry.document_objects(doc, symbol="world-t", prefix="shot/")
        report = geometry.evaluate(objects, [0.0], lambda ts: [cam_frame(0, -100)])
        self.assertTrue(report["screen"].startswith("not checked"))
        self.assertEqual(report["reads_merged"], [])

    def test_the_landing_advice_gives_the_surface_top_in_numbers(self):
        row = {"time": 13.0, "hero": "s/hero-plane", "object": "s/desk-top", "depth": 4.0,
               "axis": "y", "tolerance": 1.3, "surface_top": -79.0, "hero_bottom": -75.0}
        text = geometry.advice(row)
        for needle in ("lowest point equals the surface's top", "y=-79", "y=-75", "4.0 lower"):
            self.assertIn(needle, text)


def _merged_report():
    span = {"hero": "shot-1.1/hero-plane", "object": "shot-1.1/desk-top", "first": 8.0,
            "last": 11.5, "seconds": 3.75, "samples": 15, "overlap": 0.72, "clearance": 3.5,
            "ratio": 0.24, "casts": True}
    report = _report([])
    report["reads_merged"] = [span]
    report["step"] = 0.25
    return report


class ScreenHooks(unittest.TestCase):
    """reads-merged through the three hooks."""

    def test_vfx_makes_it_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = vfx.Workspace(Path(tmp), version="v01")
            name = ws.doc_name(1, SECTION)
            (ws.dir / name).write_text(screen_doc(), encoding="utf-8")

            def factory(doc_path, *, files=None, root=None, host=""):
                return lambda times: [cam_frame(t, -100.0) for t in times]
            with patch.object(vfx, "EVAL_RUNNER", factory):
                found = ws.geometry(name, shots={"world-t": SECTION["shots"]})
        errors = [f for f in found if f.startswith("error")]
        self.assertEqual(len(errors), 1, found)
        self.assertIn("reads merged", errors[0])

    def test_vfx_brief_states_the_resting_and_grounding_rules(self):
        from ytstudio.core import Idea
        with tempfile.TemporaryDirectory() as tmp:
            ws = vfx.Workspace(Path(tmp), version="v01")
            text = ws.brief(1, idea=Idea(title="T", genre="g", description="d"), house_rules="",
                            style={}, tokens={"background": "#000"}, frame=(1920, 1080),
                            section=SECTION, effects=[], words={}, assets=[]).read_text()
        for needle in ("lowest point equals the surface's top", "y=-79", "reads merged",
                       "castShadow", "line of sight"):
            self.assertIn(needle, text)
        self.assertIn(f'<meta name="{geometry.REST_META}"', vfx.EXAMPLES)
        self.assertIn('id="hero-block"', vfx.EXAMPLES)

    def test_qa_files_a_blocking_reads_merged_finding_per_shot(self):
        shots = [{"label": "1.2", "at": 3.0, "seconds": 4.0}, {"label": "1.3", "at": 7.0, "seconds": 4.0},
                 {"label": "1.4", "at": 11.0, "seconds": 4.0}]
        by_shot = qa.merged_by_shot(_merged_report(), shots)
        self.assertEqual(sorted(by_shot), ["1.3", "1.4"])
        sample = {"t": 9.0, "label": "1.3", "frame": "t_00m09s000.png", "asset": "effect",
                  "why": "50%", "still": {"mean": 120, "spread": 40, "dominant_share": 0.2,
                                          "clipped_share": 0.0},
                  "motion": {"frozen": False, "jump": None, "steps": []}}
        found = qa.mechanical_findings([sample], merged=by_shot)
        self.assertEqual([(f["kind"], f["severity"], f["owner"]) for f in found],
                         [("reads-merged", "blocking", "vfx")])
        for needle in ("desk-top", "8-11.5s", "72%", "3.5 units"):
            self.assertIn(needle, found[0]["problem"])
        _, _, capped = qa.cap_scores({k: 9.0 for k in qa.CATEGORIES}, 9.0, found)
        self.assertEqual(sorted(capped), sorted(qa.CAPPED))

    def test_gate_blocks_on_it(self):
        rows = gate.geometry_rows(_merged_report())
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["what"].startswith(gate.READS_MERGED))
        self.assertIn("8s to 11.5s", rows[0]["what"])
        self.assertEqual(rows[0]["owner"], "vfx")
        out = gate.apply_geometry({"blocking": [], "acceptable": [], "verdict": "deliverable",
                                   "notes": ""}, _merged_report())
        self.assertEqual(out["verdict"], "not deliverable")


class Documentation(unittest.TestCase):
    ROOT = Path(__file__).resolve().parent.parent

    def test_the_manual_and_the_readme_name_the_check(self):
        skill = (self.ROOT / "ytstudio" / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(skill, r"\|[^\n]*[Pp]enetrat[^\n]*\|")          # a trap row
        readme = (self.ROOT / "README.md").read_text(encoding="utf-8")
        for stage in ("`yt vfx`", "`yt qa`", "`yt gate`"):
            rows = [l for l in readme.splitlines() if l.startswith(f"| {stage} |")]
            self.assertTrue(rows, f"no table row for {stage}")
            self.assertTrue(any("penetrat" in r for r in rows), f"{stage}'s row does not name the check")
            self.assertTrue(any("reads merged" in r or "reads-merged" in r for r in rows),
                            f"{stage}'s row does not name the screen check")
        self.assertRegex(skill, r"\|[^\n]*reads merged[^\n]*\|")


if __name__ == "__main__":
    unittest.main()
