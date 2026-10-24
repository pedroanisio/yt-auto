"""AUTO-110: a reviewer names where a finding is, and the author is told
which pixels and which 3D objects that is.

The vfx reviewer is shown, beside each clean proof frame, a copy with a
labelled 8x6 grid (frames/grid/<frame>.png, made at review time and never
kept as evidence); a finding may name the cells it is about (`cells`,
enum-checked like `category`). The next round's brief turns those cells into
one pixel rectangle and, when the engine's eval of that frame is reachable
through EVAL_RUNNER, the ids of the object3D nodes whose screen boxes fall in
them. No model is called and the engine is a fake eval frame.
"""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from ytstudio import framecheck, geometry, harness, render, vfx
from ytstudio.core import Idea
from ytstudio.research import Dossier
from tests.test_vfx_world import (EFFECTS, SECTION, STYLE, WORLD_DOC, FakeEngine,
                                  varied_png)

# A camera at z = -400 looking down +z (36 mm on a 36 mm sensor: the focal
# distance is the frame's width, 1920 px), a 10-unit `hero-cell` box placed
# to land in cell B2 (centre 360, 270) and a `set-block` box in G5 (centre
# 1560, 810), and a text label, which has no box.
GRID_DOC = '''<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.3">
  <project width="1920" height="1080" fps="30" duration="4" background="#101418"/>
  <symbols>
    <symbol id="world-g" width="1920" height="1080" duration="4">
      <camera id="cam" x="0" y="0" z="-400" yaw="0" pitch="0" focalLength="36" sensorWidth="36" active="true"/>
      <object3D id="hero-cell" primitive="box" x="-125" y="-56.25" z="0" width="10" height="10" depth="10"/>
      <object3D id="set-block" primitive="box" x="125" y="56.25" z="0" width="10" height="10" depth="10"/>
      <text id="label-title" x="100" y="100" width="300" height="60">Title</text>
    </symbol>
  </symbols>
  <composition>
    <instance id="u" symbol="world-g" start="0" end="4"/>
  </composition>
</scene>
'''

FLAT_DOC = GRID_DOC.replace(
    '<object3D id="hero-cell" primitive="box" x="-125" y="-56.25" z="0" width="10" height="10" depth="10"/>\n', ""
).replace('<object3D id="set-block" primitive="box" x="125" y="56.25" z="0" width="10" height="10" depth="10"/>\n', "")


def eval_frame(*, present=("hero-cell", "set-block"), prefix="shot/") -> dict:
    """What eval prints at one time: the include, the camera (the result's
    `camera` indexes it) and the object3D nodes, at their document values."""
    nodes = [{"id": prefix.rstrip("/"), "kind": "include", "draw": True, "props": {}},
             {"id": f"{prefix}cam", "kind": "camera", "draw": True, "parent": 0, "props": {}}]
    nodes += [{"id": f"{prefix}{oid}", "kind": "object3D", "draw": True, "parent": 0,
               "props": {}} for oid in present]
    return {"ok": True, "format": "scene-render-cli/1",
            "result": {"frame": 30, "camera": 1, "nodes": nodes}}


def grid_review(cells=("C3",), frame="frames/01-open__world-open__5s.png", score=5) -> dict:
    return {"frames": [{"frame": frame, "shot": "1.2", "seen": "a sphere",
                        "findings": [{"category": "cinematography",
                                      "problem": "the hero sits low in the frame",
                                      "fix": "pitch the camera down 5 degrees",
                                      "severity": "major", "cells": list(cells)},
                                     {"category": "visual_quality",
                                      "problem": "one grade over everything",
                                      "fix": "lift the key", "severity": "minor"}]}],
            "score": score, "summary": "s"}


class StubReviewer:
    """Harness.inspect's seam: records what it was asked, answers a script."""

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def inspect(self, name, prompt, schema, *, cwd):
        self.calls.append({"name": name, "prompt": prompt, "schema": schema, "cwd": cwd})
        return json.loads(json.dumps(self.answer))


class PlannerCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._engine = harness.ENGINE
        harness.ENGINE = FakeEngine
        FakeEngine.stream_calls, FakeEngine.json_calls, FakeEngine.reviews = [], [], []
        FakeEngine.doc, FakeEngine.docs = WORLD_DOC, []
        FakeEngine.time_out_on = FakeEngine.crash_on = 0
        idea = Idea(title="T", genre="g", description="d")
        self.bundle = {"idea": idea, "outline": {}, "claims": [], "assets": [], "premise": ""}
        self.ws = vfx.Workspace(self.root, version="v01")
        self.planner = vfx.VfxPlanner(
            self.bundle, Dossier(self.root, idea, at=self.root), self.ws, model="sonnet",
            sections=[SECTION], revisions=1, author_bar=7.0, verbose=False)
        self.name = self.ws.doc_name(1, SECTION)
        self.stem = self.name[:-len(".scene.xml")]
        self.worlds = [{"id": "world-open", "shots": SECTION["shots"], "phases": []}]

    def tearDown(self):
        harness.ENGINE = self._engine
        self._tmp.cleanup()

    def frames(self, *times) -> list[Path]:
        out = []
        (self.ws.dir / "frames").mkdir(exist_ok=True)
        for t in times:
            png = self.ws.dir / "frames" / f"{self.stem}__world-open__{t:g}s.png"
            png.write_bytes(varied_png())
            out.append(png)
        return out


class GridCopies(PlannerCase):
    def test_the_review_writes_a_grid_copy_beside_every_frame_it_lists(self):
        pngs = self.frames(1.5, 5)
        grid = self.ws.dir / "frames" / "grid"
        grid.mkdir()
        (grid / f"{self.stem}__world-open__9s.png").write_bytes(b"an old round's copy")
        (grid / "02-other__w__1s.png").write_bytes(b"another section's copy")
        reviewer = StubReviewer({"frames": [], "score": 8, "summary": "ok"})
        with patch.object(vfx, "EVAL_RUNNER", lambda *a, **k: None):
            self.planner._review(reviewer, 1, self.name, SECTION, self.worlds, 0)
        prompt = reviewer.calls[0]["prompt"]
        for png in pngs:
            copy = grid / png.name
            self.assertTrue(copy.is_file(), copy)
            self.assertEqual(png.read_bytes(), varied_png())          # the clean frame untouched
            self.assertNotEqual(copy.read_bytes(), png.read_bytes())
            self.assertEqual(Image.open(io.BytesIO(copy.read_bytes())).size,
                             Image.open(io.BytesIO(png.read_bytes())).size)
            # the grid path sits in the frame's own entry, after its frame
            at = prompt.index(f'"frame": "frames/{png.name}"')
            g = prompt.index(f'"grid": "frames/grid/{png.name}"')
            self.assertGreater(g, at)
            self.assertNotIn('"frame":', prompt[at + 1:g])
        self.assertFalse((grid / f"{self.stem}__world-open__9s.png").exists())  # stale: cleared
        self.assertTrue((grid / "02-other__w__1s.png").exists())  # not this section's
        # the reviewer judges the clean frame and uses the grid to say where
        self.assertIn("clean frame", prompt)
        self.assertIn("`cells`", prompt)
        self.assertIn("A-H", prompt)
        self.assertIn("1-6", prompt)

    def test_a_kept_round_holds_the_clean_frames_alone(self):
        self.frames(1.5)
        program = self.ws.dir / self.name
        program.write_text(WORLD_DOC, encoding="utf-8")
        reviewer = StubReviewer({"frames": [], "score": 8, "summary": "ok"})
        with patch.object(vfx, "EVAL_RUNNER", lambda *a, **k: None):
            self.planner._review(reviewer, 1, self.name, SECTION, self.worlds, 0)
        self.assertTrue(any((self.ws.dir / "frames" / "grid").iterdir()))
        kept = self.ws.keep_round(self.name, 0)
        self.assertFalse((kept / "frames" / "grid").exists())
        self.assertEqual(sorted(p.name for p in (kept / "frames").iterdir()),
                         [f"{self.stem}__world-open__1.5s.png"])
        # restoring the round puts back clean frames only
        self.assertTrue(self.ws.restore_round(self.name, 0))
        self.assertEqual(sorted(p.name for p in (self.ws.dir / "frames").glob("*.png")),
                         [f"{self.stem}__world-open__1.5s.png"])


    def test_without_pillow_the_review_runs_and_lists_no_grid(self):
        # The vfx review ran without Pillow before the grid copies came: the
        # copy is a viewing aid, so a python3 with no PIL loses it and nothing else.
        self.frames(1.5, 5)
        reviewer = StubReviewer({"frames": [], "score": 8, "summary": "ok"})
        with patch.dict(sys.modules, {"PIL": None}), \
                patch.object(vfx, "EVAL_RUNNER", lambda *a, **k: None):
            review = self.planner._review(reviewer, 1, self.name, SECTION, self.worlds, 0)
        self.assertEqual(review, {"frames": [], "score": 8, "summary": "ok"})
        prompt = reviewer.calls[0]["prompt"]
        self.assertNotIn('"grid":', prompt)
        self.assertNotIn(vfx.GRID_NOTE, prompt)                 # no copy, so no word of one
        self.assertFalse(any((self.ws.dir / "frames" / "grid").glob("*.png")))

    def test_a_frame_that_cannot_be_read_gets_no_copy(self):
        good, bad = self.frames(1.5, 5)
        bad.write_bytes(b"not a png")
        self.assertIsNone(vfx.grid_copy(bad))
        reviewer = StubReviewer({"frames": [], "score": 8, "summary": "ok"})
        with patch.object(vfx, "EVAL_RUNNER", lambda *a, **k: None):
            self.planner._review(reviewer, 1, self.name, SECTION, self.worlds, 0)
        prompt = reviewer.calls[0]["prompt"]
        self.assertIn(f'"grid": "frames/grid/{good.name}"', prompt)
        self.assertNotIn(f'"grid": "frames/grid/{bad.name}"', prompt)
        self.assertIn(vfx.GRID_NOTE, prompt)                    # one frame has a copy
        bad_only = self.frames(9)[0]
        good.unlink()
        (self.ws.dir / "frames" / f"{self.stem}__world-open__5s.png").unlink()
        bad_only.write_bytes(b"not a png")
        self.planner._review(reviewer, 1, self.name, SECTION, self.worlds, 1)
        self.assertNotIn(vfx.GRID_NOTE, reviewer.calls[1]["prompt"])  # none has one


class Schema(unittest.TestCase):
    def review(self, cells):
        return {"frames": [{"frame": "frames/a.png", "seen": "s", "findings": [
            {"category": "cinematography", "problem": "p", "fix": "f", "severity": "major",
             "cells": cells}]}], "score": 5, "summary": "s",
                "previous": {"verdict": "same", "findings": []}}

    def test_cells_are_optional_and_checked_against_the_grid(self):
        for delta in (False, True):
            schema = vfx.review_schema(delta=delta)
            item = schema["properties"]["frames"]["items"]["properties"]["findings"]["items"]
            self.assertIn("cells", item["properties"])
            self.assertNotIn("cells", item["required"])
            self.assertEqual(item["properties"]["cells"]["items"]["enum"],
                             list(framecheck.GRID_CELLS))
            self.assertEqual(harness.enum_violations(self.review(["C3"]), schema), [])
            self.assertEqual(harness.enum_violations(self.review([]), schema), [])
            bad = harness.enum_violations(self.review(["Z9"]), schema)
            self.assertEqual(len(bad), 1)
            self.assertIn("cells", bad[0])
            self.assertIn("Z9", bad[0])


class ScreenBoxes(unittest.TestCase):
    def setUp(self):
        self.objects = geometry.document_objects(GRID_DOC, symbol="world-g", prefix="shot/")
        self.cameras = geometry.document_cameras(GRID_DOC, symbol="world-g", prefix="shot/")

    def test_each_object3d_gets_its_screen_rectangle(self):
        boxes = geometry.screen_boxes(self.objects, self.cameras, eval_frame(), (1920, 1080))
        self.assertEqual(sorted(boxes), ["shot/hero-cell", "shot/set-block"])  # no text box
        x0, y0, x1, y1 = boxes["shot/hero-cell"]
        self.assertTrue(240 < x0 < 360 < x1 < 480, boxes)
        self.assertTrue(180 < y0 < 270 < y1 < 360, boxes)

    def test_cells_name_the_objects_inside_them(self):
        boxes = geometry.screen_boxes(self.objects, self.cameras, eval_frame(), (1920, 1080))
        rects = framecheck.grid_cells((1920, 1080))
        found = geometry.in_cells(boxes, {c: rects[c] for c in ("B2", "G5", "A6")})
        self.assertEqual(found, {"B2": ["shot/hero-cell"], "G5": ["shot/set-block"], "A6": []})

    def test_an_object_eval_does_not_draw_has_no_box(self):
        boxes = geometry.screen_boxes(self.objects, self.cameras,
                                      eval_frame(present=("hero-cell",)), (1920, 1080))
        self.assertEqual(sorted(boxes), ["shot/hero-cell"])

    def test_an_orthographic_or_missing_camera_maps_nothing(self):
        ortho = [dict(c, projection="orthographic") for c in self.cameras]
        self.assertIsNone(geometry.screen_boxes(self.objects, ortho, eval_frame(), (1920, 1080)))
        self.assertIsNone(geometry.screen_boxes(self.objects, [], eval_frame(), (1920, 1080)))


class CellObjects(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ws = vfx.Workspace(self.root, version="v01")
        self.name = "01-grid.scene.xml"
        (self.ws.dir / self.name).write_text(GRID_DOC, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_cells_objects_come_from_one_eval_on_the_host(self):
        made = []

        def factory(doc_path, *, files=None, root=None, host=""):
            made.append({"doc_path": doc_path, "files": sorted(files or {})})

            def run(times):
                made[-1]["times"] = list(times)
                return [eval_frame() for _ in times]
            return run
        with patch.object(vfx, "EVAL_RUNNER", factory):
            found = self.ws.cell_objects(self.name, "world-g", 2.0, ["B2", "A6"], (1920, 1080))
        self.assertEqual(found, {"B2": ["shot/hero-cell"], "A6": []})
        self.assertEqual(len(made), 1)
        self.assertEqual(made[0]["times"], [2.0])
        self.assertTrue(made[0]["doc_path"].startswith("vfx/v01/.proof-"))
        self.assertIn(f"vfx/v01/{self.name}", made[0]["files"])
        self.assertIn(made[0]["doc_path"], made[0]["files"])

    def test_nothing_to_evaluate_is_none(self):
        args = (self.name, "world-g", 2.0, ["B2"], (1920, 1080))
        self.assertIsNone(self.ws.cell_objects(*args, runner=lambda times: None))
        self.assertIsNone(self.ws.cell_objects(*args, runner=lambda times: [{"ok": False}]))
        with patch.object(vfx, "EVAL_RUNNER", lambda *a, **k: None):
            self.assertIsNone(self.ws.cell_objects(*args))           # no engine reachable
        (self.ws.dir / self.name).write_text(GRID_DOC.replace(
            'focalLength="36"', 'projection="orthographic" focalLength="36"'), encoding="utf-8")
        self.assertIsNone(self.ws.cell_objects(*args, runner=lambda ts: [eval_frame()]))
        (self.ws.dir / self.name).write_text(FLAT_DOC, encoding="utf-8")
        called = []
        self.assertIsNone(self.ws.cell_objects(
            *args, runner=lambda ts: called.append(ts) or [eval_frame(present=())]))
        self.assertEqual(called, [])                              # no object3D: no eval paid


    def test_an_eval_that_raises_is_none_with_the_reason(self):
        args = (self.name, "world-g", 2.0, ["B2"], (1920, 1080))
        for exc in (subprocess.CalledProcessError(23, ["rsync"]), OSError("ssh: no route"),
                    render.RemoteRefused("YT_NO_REMOTE is set"),
                    subprocess.TimeoutExpired(["ssh"], 5)):
            def run(times, exc=exc):
                raise exc
            found, why = self.ws.locate(*args, runner=run)
            self.assertIsNone(found, exc)
            self.assertIn("eval at 2s failed", why)

            def factory(doc_path, *, files=None, root=None, host="", exc=exc):
                raise exc
            with patch.object(vfx, "EVAL_RUNNER", factory):
                found, why = self.ws.locate(*args)
            self.assertIsNone(found, exc)
            self.assertIn("failed", why)


class Listing(unittest.TestCase):
    def finding(self, **kw):
        return dict({"category": "cinematography", "problem": "the hero sits low",
                     "fix": "pitch the camera down", "severity": "major"}, **kw)

    def listing(self, *findings):
        return vfx.VfxPlanner._review_listing(
            {"frames": [{"frame": "frames/x.png", "shot": "2", "findings": list(findings)}],
             "score": 5, "summary": "s"}, frame=(1920, 1080))

    def test_a_located_finding_names_its_cells_pixels_and_objects(self):
        text = self.listing(self.finding(cells=["C3", "C4"], objects=["shot/hero-cell"]))
        line = next(ln for ln in text.splitlines() if ln.startswith("- "))
        self.assertIn("frames/x.png (shot 2, cinematography)", line)
        self.assertIn("cells C3,C4 = px (480,360)-(720,720)", line)
        self.assertIn("objects: shot/hero-cell", line)
        self.assertIn("the hero sits low", line)
        self.assertIn("REQUIRED FIX: pitch the camera down", text)

    def test_cells_with_no_object_in_them_say_so(self):
        text = self.listing(self.finding(cells=["A6"], objects=[]))
        self.assertIn("cells A6 = px (0,900)-(240,1080)", text)
        self.assertIn("no 3D object", text)

    def test_without_a_mapping_the_pixels_stand_alone_with_the_reason(self):
        text = self.listing(self.finding(cells=["C3"], objects=None,
                                         unmapped="no engine reachable to evaluate the frame"))
        self.assertIn("cells C3 = px (480,360)-(720,540)", text)
        self.assertIn("no engine reachable to evaluate the frame", text)
        self.assertNotIn("objects:", text)

    def test_a_finding_about_the_whole_frame_has_no_cells(self):
        text = self.listing(self.finding(cells=[]), self.finding(problem="the grade is flat"))
        self.assertNotIn("cells", text)
        self.assertNotIn("px (", text)
        self.assertIn("- [major] frames/x.png (shot 2, cinematography): the grade is flat", text)


    def test_cells_that_are_not_a_list_name_no_cell(self):
        text = self.listing(self.finding(cells="C3,C4", objects=None, unmapped="x"))
        self.assertNotIn("cells", text)
        self.assertNotIn("px (", text)


class Locating(PlannerCase):
    """After the review, each frame with a located finding is evaluated once
    and its findings get the object ids in their cells."""

    def test_one_eval_per_located_frame_and_the_objects_on_each_finding(self):
        (self.ws.dir / self.name).write_text(WORLD_DOC, encoding="utf-8")
        self.frames(5)
        answer = grid_review(cells=("D3", "E3", "D4", "E4"))
        answer["frames"][0]["findings"].append(
            {"category": "graphics_overlays", "problem": "the corner is empty",
             "fix": "f", "severity": "minor", "cells": ["A1"]})
        runs = []

        def factory(doc_path, *, files=None, root=None, host=""):
            def run(times):
                runs.append(list(times))
                return [eval_frame(present=("hero-ball",)) for _ in times]
            return run
        with patch.object(vfx, "EVAL_RUNNER", factory):
            review = self.planner._review(StubReviewer(answer), 1, self.name, SECTION,
                                          self.worlds, 0)
        self.assertEqual(runs, [[5.0]])                                # one eval, at the frame's t
        located, whole, corner = review["frames"][0]["findings"]
        self.assertEqual(located["objects"], ["shot/hero-ball"])
        self.assertNotIn("objects", whole)                             # no cells, nothing resolved
        self.assertEqual(corner["objects"], [])

    def test_without_an_engine_the_finding_keeps_its_cells_and_says_why(self):
        (self.ws.dir / self.name).write_text(WORLD_DOC, encoding="utf-8")
        self.frames(5)
        with patch.object(vfx, "EVAL_RUNNER", lambda *a, **k: None):
            review = self.planner._review(StubReviewer(grid_review()), 1, self.name, SECTION,
                                          self.worlds, 0)
        located = review["frames"][0]["findings"][0]
        self.assertEqual(located["cells"], ["C3"])
        self.assertIsNone(located["objects"])
        self.assertTrue(located["unmapped"])
        listing = vfx.VfxPlanner._review_listing(review, frame=(1920, 1080))
        self.assertIn("cells C3 = px (480,360)-(720,540)", listing)
        self.assertIn(located["unmapped"], listing)


    def test_a_host_failure_at_locate_time_keeps_the_paid_review(self):
        (self.ws.dir / self.name).write_text(WORLD_DOC, encoding="utf-8")
        self.frames(5)

        def factory(doc_path, *, files=None, root=None, host=""):
            def run(times):
                raise subprocess.CalledProcessError(23, ["rsync"])
            return run
        with patch.object(vfx, "EVAL_RUNNER", factory):
            review = self.planner._review(StubReviewer(grid_review()), 1, self.name, SECTION,
                                          self.worlds, 0)
        self.assertIsNotNone(review)
        located = review["frames"][0]["findings"][0]
        self.assertEqual(located["cells"], ["C3"])
        self.assertIsNone(located["objects"])
        self.assertIn("failed", located["unmapped"])

    def test_a_finding_on_another_sections_frame_is_not_evaluated(self):
        (self.ws.dir / self.name).write_text(WORLD_DOC, encoding="utf-8")
        self.frames(5)
        runs = []

        def factory(doc_path, *, files=None, root=None, host=""):
            return lambda times: runs.append(times) or [eval_frame() for _ in times]
        with patch.object(vfx, "EVAL_RUNNER", factory):
            review = self.planner._review(
                StubReviewer(grid_review(frame="frames/02-other__w__1s.png")), 1, self.name,
                SECTION, self.worlds, 0)
        located = review["frames"][0]["findings"][0]
        self.assertEqual(runs, [])
        self.assertIsNone(located["objects"])
        self.assertIn("not one of this section's proof frames", located["unmapped"])

    def test_cells_given_as_a_string_pay_no_eval_and_map_nothing(self):
        (self.ws.dir / self.name).write_text(WORLD_DOC, encoding="utf-8")
        self.frames(5)
        answer = grid_review()
        answer["frames"][0]["findings"][0]["cells"] = "C3,C4"
        answer["frames"][0]["findings"][1]["cells"] = ["Z9"]   # nothing on the grid
        runs = []

        def factory(doc_path, *, files=None, root=None, host=""):
            return lambda times: runs.append(times) or [eval_frame() for _ in times]
        with patch.object(vfx, "EVAL_RUNNER", factory):
            review = self.planner._review(StubReviewer(answer), 1, self.name, SECTION,
                                          self.worlds, 0)
        self.assertEqual(runs, [])
        for f in review["frames"][0]["findings"]:
            self.assertNotIn("objects", f)


class Rounds(PlannerCase):
    """Through the stage: the record keeps the cells and objects, the next
    author is told them, and a cell off the grid fails the review."""

    def author(self, runner_factory):
        group = {"section": "Open", "seconds": 15, "effects": EFFECTS, "cuts": []}
        with patch.object(vfx.render, "engine_validate", lambda data, **kw: []), \
                patch.object(vfx.render, "engine_render_still",
                             lambda xml, at, **kw: (varied_png(), "rendered")), \
                patch.object(vfx, "encode_findings", lambda *a, **k: []), \
                patch.object(vfx, "EVAL_RUNNER", runner_factory):
            return self.planner.author({"sections": [SECTION]}, [group], STYLE,
                                       {"ink": "#FFFFFF"})[0]

    def frame_name(self, t):
        return f"frames/{self.stem}__world-open__{t:g}s.png"

    def test_the_next_author_gets_cells_pixels_and_objects(self):
        FakeEngine.reviews = [grid_review(cells=("C3", "C4"), frame=self.frame_name(5)),
                              {"frames": [], "score": 8, "summary": "reads"}]

        def factory(doc_path, *, files=None, root=None, host=""):
            return lambda times: [eval_frame(present=("hero-ball", "wall")) for _ in times]
        doc = self.author(factory)
        first = doc["rounds"][0]["review"]["frames"][0]["findings"][0]
        self.assertEqual(first["cells"], ["C3", "C4"])
        self.assertEqual(first["objects"], ["shot/wall"])
        authors = [c for c in FakeEngine.stream_calls if "Write" in c["tools"]]
        self.assertIn("cells C3,C4 = px (480,360)-(720,720), objects: shot/wall",
                      authors[1]["prompt"])
        grid = self.ws.dir / "frames" / "grid"
        shown = [c for c in FakeEngine.stream_calls if "Write" not in c["tools"]][0]["prompt"]
        for png in sorted((self.ws.dir / "rounds" / self.stem / "r00" / "frames").glob("*.png")):
            self.assertIn(f"frames/grid/{png.name}", shown)
        self.assertTrue(any(grid.glob("*.png")))
        self.assertFalse(any((self.ws.dir / "rounds").rglob("grid")))

    def test_a_cell_off_the_grid_fails_the_review_like_a_bad_category(self):
        FakeEngine.reviews = [grid_review(cells=("Z9",), frame=self.frame_name(5))]
        doc = self.author(lambda *a, **k: None)
        self.assertIsNone(doc["rounds"][0]["score"])


if __name__ == "__main__":
    unittest.main()
