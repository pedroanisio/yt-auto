"""qa.py: motion evidence and the mechanical checks that run before the model.

Four stills cannot evidence a camera move, and the model should not be the
only thing that notices a flat frame. These pin the deterministic half of
that: the burst plan written in samples.json, the frame renderer seam, the
numbers Pillow computes from a burst, the contact sheet, the engine's own
diagnostics summarised per shot, the severity rule for mechanical findings,
the cap they put on the model's scorecard, and the fixed low score of a
silent film. No test renders on a host or asks the model.
"""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

from claude_engine import Event, Usage
from ytstudio import harness, qa, rhythm

W, H = 640, 360


def _build(audio_tracks=0):
    shots, t = [], 0.0
    for label, seconds in (("1.1", 3.0), ("1.2", 4.0), ("1.3", 4.0), ("1.4", 5.5)):
        shots.append({"label": label, "at": t, "seconds": seconds, "asset": "effect",
                      "section": "One take"})
        t += seconds
    return {"scene": {"duration": t, "fps": "30", "width": W, "height": H},
            "shots": shots, "placeholders": [], "findings": [],
            "audio_tracks": audio_tracks}


def steady_cutting(case: unittest.TestCase) -> None:
    """_build's four shots (3, 4, 4, 5.5 s) vary by CV 0.22, which direction
    12 calls metronomic (AUTO-141), and their cut times and runtime are
    pinned by the tests that read them. A test about something else lowers
    the floor under that CV for itself, so its film is judged on what it
    tests; RhythmStage and test_qa_rhythm keep the floor."""
    patch = mock.patch.dict(rhythm.RHYTHM["cv_floor"], {"value": 0.2})
    patch.start()
    case.addCleanup(patch.stop)


def _square(path: Path, x: int, *, size: int = 120, level: int = 255,
            fill: int = 40) -> Path:
    """A frame with one bright square at `x`: moving it is motion, and the
    squares' area is what clips."""
    im = Image.new("L", (W, H), fill)
    ImageDraw.Draw(im).rectangle([x, 100, x + size, 100 + size], fill=level)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return path


def _flat(path: Path, level: int = 120) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("L", (W, H), level).save(path)
    return path


def _clipped(path: Path, share: float = 0.5) -> Path:
    im = Image.new("L", (W, H), 90)
    ImageDraw.Draw(im).rectangle([0, 0, int(W * share), H], fill=255)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return path


class BurstPlan(unittest.TestCase):
    def test_a_burst_is_one_contiguous_window_around_the_midpoint(self):
        plan = qa.burst_plan(9.0, fps=30, seconds=4.0)
        first, end = plan["frames"][0], plan["frames"][-1] + 1
        self.assertEqual(plan["frames"], list(range(first, end)))     # one A..B job
        self.assertEqual(plan["range"], f"{first}..{end}")
        self.assertEqual(plan["still_frame"], 270)
        self.assertIn(270, plan["frames"])
        self.assertLessEqual(len(plan["frames"]), qa.BURST_MAX_FRAMES)
        self.assertAlmostEqual(plan["span"], qa.BURST_SECONDS, delta=1 / 30)
        self.assertEqual(plan, qa.burst_plan(9.0, fps=30, seconds=4.0))   # deterministic

    def test_a_short_shot_keeps_the_burst_inside_itself(self):
        plan = qa.burst_plan(0.4, fps=30, seconds=0.8)
        self.assertTrue(all(0 <= f < 24 for f in plan["frames"]), plan)
        self.assertGreater(len(plan["frames"]), 1)
        self.assertIn(plan["still_frame"], plan["frames"])

    def test_a_high_frame_rate_is_capped_by_frames_not_seconds(self):
        plan = qa.burst_plan(9.0, fps=120, seconds=4.0)
        self.assertEqual(len(plan["frames"]), qa.BURST_MAX_FRAMES)
        self.assertIn(plan["still_frame"], plan["frames"])

    def test_samples_carry_the_burst_and_its_file_names(self):
        rows = qa.sample(_build())
        for row in rows:
            self.assertIn("burst", row)
            self.assertEqual(len(row["burst"]["files"]), len(row["burst"]["frames"]))
            for frame, name in zip(row["burst"]["frames"], row["burst"]["files"]):
                self.assertTrue(name.startswith("burst/"), name)
                self.assertIn(f"{frame:04d}", name)
            self.assertAlmostEqual(row["seconds"], next(
                s["seconds"] for s in _build()["shots"] if s["label"] == row["label"]))


class Renderer(unittest.TestCase):
    """The seam: render_frames asks a callable for each job, never ssh."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qa-motion-"))
        self.jobs = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake(self, job):
        self.jobs.append(job)
        out = job["out"]
        if "frames" in job:
            a, b = (int(v) for v in job["frames"].split(".."))
            for f in range(a, b):
                _square(Path(out.replace("%04d", f"{f:04d}")), 10 + f)
        else:
            _square(Path(out), 10)
        return 0

    def test_a_pass_of_n_samples_costs_n_jobs_one_frame_range_each(self):
        rows = qa.sample(_build())
        self.assertEqual(len(rows), 4)
        problems = qa.render_frames(None, None, "scene.xml", rows, self.tmp / "frames",
                                    render=self.fake)
        self.assertEqual(problems, [])
        self.assertEqual(len(self.jobs), len(rows))
        self.assertTrue(all("frames" in j and "still" not in j for j in self.jobs), self.jobs)
        for row, job in zip(rows, self.jobs):
            self.assertEqual(job["frames"], row["burst"]["range"])
            self.assertIn("%04d", job["out"])
            self.assertTrue((self.tmp / "frames" / row["frame"]).is_file())
            for name in row["burst"]["files"]:
                self.assertTrue((self.tmp / "frames" / name).is_file(), name)
        # The still is the window's midpoint frame, not a second render.
        row = rows[0]
        mid = row["burst"]["files"][row["burst"]["frames"].index(row["burst"]["still_frame"])]
        self.assertEqual((self.tmp / "frames" / row["frame"]).read_bytes(),
                         (self.tmp / "frames" / mid).read_bytes())
        # Already rendered: nothing is asked again.
        self.jobs.clear()
        qa.render_frames(None, None, "scene.xml", rows, self.tmp / "frames", render=self.fake)
        self.assertEqual(self.jobs, [])

    def test_a_burst_that_does_not_come_back_falls_back_to_one_still(self):
        rows = qa.sample(_build(), limit=1)
        problems = qa.render_frames(None, None, "scene.xml", rows, self.tmp / "frames",
                                    render=lambda job: 3)
        self.assertTrue(any("exited 3" in p for p in problems), problems)
        self.assertTrue(any("burst" in p for p in problems), problems)
        self.assertTrue(any("no frame at" in p for p in problems), problems)

        def still_only(job):
            self.jobs.append(job)
            if "still" in job:
                _square(Path(job["out"]), 10)
                return 0
            return 1
        problems = qa.render_frames(None, None, "scene.xml", rows, self.tmp / "frames",
                                    render=still_only)
        self.assertEqual([("frames" in j, "still" in j) for j in self.jobs],
                         [(True, False), (False, True)])
        self.assertTrue((self.tmp / "frames" / rows[0]["frame"]).is_file())
        self.assertEqual(len(problems), 1)                 # the burst, not the still

    def test_the_engine_renderer_keeps_the_render_stage_contract(self):
        seen = []
        original = qa.render_stage.run
        qa.render_stage.run = lambda ns: seen.append(ns) or 0
        try:
            args = argparse.Namespace(quality="preview", project="p", title=None,
                                      work=None, host=None, local=False)
            render = qa.engine_renderer(args, "scene.xml")
            render({"still": 1.5, "out": "a.png"})
            render({"frames": "30..31", "out": "b_%04d.png"})
        finally:
            qa.render_stage.run = original
        self.assertEqual(seen[0].still, 1.5)
        self.assertIsNone(seen[0].frames)
        self.assertEqual(seen[0].video_out, "a.png")
        self.assertEqual(seen[1].frames, "30..31")
        self.assertIsNone(seen[1].still)
        self.assertEqual(seen[1].video_out, "b_%04d.png")
        for ns in seen:
            self.assertEqual(ns.scene, "scene.xml")
            self.assertTrue(ns.quiet)
            self.assertFalse(ns.validate)
            self.assertIsNone(ns.start)


class Numbers(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qa-motion-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def frames(self, xs):
        return [_square(self.tmp / f"f_{i:04d}.png", x) for i, x in enumerate(xs)]

    def test_a_frozen_burst_has_no_difference_at_all(self):
        motion = qa.burst_motion(self.frames([100, 100, 100, 100, 100]))
        self.assertTrue(motion["frozen"])
        self.assertIsNone(motion["jump"])
        self.assertEqual(len(motion["steps"]), 4)
        self.assertTrue(all(s == 0 for s in motion["steps"]), motion)

    def test_a_steady_move_is_neither_frozen_nor_a_jump(self):
        motion = qa.burst_motion(self.frames([100, 110, 120, 130, 140]))
        self.assertFalse(motion["frozen"])
        self.assertIsNone(motion["jump"])
        self.assertGreater(motion["mean"], 0)
        self.assertEqual(motion["verdict"], "moving")

    def test_a_slow_drift_across_the_window_is_moving_not_frozen(self):
        # One pixel every other frame: each step is tiny, the window is not.
        motion = qa.burst_motion(self.frames([100 + i // 2 for i in range(16)]))
        self.assertTrue(all(s <= qa.FROZEN_DIFF for s in motion["steps"]), motion)
        self.assertGreater(motion["total"], qa.FROZEN_DIFF)
        self.assertFalse(motion["frozen"])
        self.assertEqual(motion["verdict"], "moving")

    def test_one_step_far_above_the_others_is_a_jump(self):
        motion = qa.burst_motion(self.frames([100, 104, 108, 400, 404]))
        self.assertEqual(motion["jump"], 3)             # the step into frame 3
        self.assertEqual(motion["verdict"], "jump")
        self.assertFalse(motion["frozen"])

    def test_fewer_than_two_frames_is_not_evidence(self):
        motion = qa.burst_motion(self.frames([100]))
        self.assertEqual(motion["steps"], [])
        self.assertFalse(motion["frozen"])
        self.assertEqual(motion["verdict"], "not rendered")

    def test_still_statistics_name_flat_and_clipped_frames(self):
        flat = qa.frame_stats(_flat(self.tmp / "flat.png"))
        self.assertLess(flat["spread"], qa.FLAT_STD)
        self.assertEqual(qa.judge_still(flat), ["flat"])
        clipped = qa.frame_stats(_clipped(self.tmp / "clip.png", 0.5))
        self.assertGreater(clipped["clipped_share"], qa.CLIPPED_SHARE)
        self.assertIn("clipped", qa.judge_still(clipped))
        fine = qa.frame_stats(_square(self.tmp / "fine.png", 100))
        self.assertEqual(qa.judge_still(fine), [])


class Diagnostics(unittest.TestCase):
    def report(self):
        def off(ident, a, b):
            return {"code": "X-rs-scene-render-3D-OFF-CAMERA", "severity": "warning",
                    "time": [a, b],
                    "message": f"object3D {ident} is drawn from {a:.3f} s to {b:.3f} s and "
                               "is never inside the camera's view: its bounds lie wholly "
                               "behind, beside or beyond the camera's frustum"}
        return {"diagnostics": [{"code": "W1", "severity": "warning", "message": "top-level"}],
                "result": {"outputs": [{"findings": [
                    off("shot-1.1/a", 0.0, 2.967), off("shot-1.1/b", 0.0, 2.967),
                    off("shot-1.3/c", 7.0, 10.967),
                    {"code": "SA01", "severity": "warning", "time": [3.0, 6.9],
                     "message": "text crosses the safe area"}],
                    "evaluation_warnings": [{"code": "E12", "severity": "warning",
                                             "path": "shot-1.2", "message": "include declares <styles>"}],
                    "warnings": []}]}}

    def test_objects_never_in_view_are_counted_per_shot_with_their_ids(self):
        diag = qa.engine_diagnostics(self.report(), _build()["shots"])
        self.assertEqual(diag["1.1"]["off_camera"], ["shot-1.1/a", "shot-1.1/b"])
        self.assertEqual(diag["1.3"]["off_camera"], ["shot-1.3/c"])
        self.assertEqual(diag.get("1.2", {}).get("off_camera", []), [])
        self.assertEqual(diag.get("1.4", {}).get("off_camera", []), [])
        self.assertEqual(diag["1.2"]["warnings"], 2)       # SA01 and E12 land on 1.2
        self.assertEqual(qa.engine_diagnostics(None, _build()["shots"]), {})
        self.assertEqual(qa.engine_diagnostics({}, _build()["shots"]), {})


class Mechanical(unittest.TestCase):
    def rows(self, **motion_by_label):
        rows = qa.sample(_build())
        for row in rows:
            row["still"] = {"mean": 100.0, "spread": 40.0, "dominant_share": 0.1,
                            "clipped_share": 0.0}
            row["motion"] = {"steps": [2.0, 2.1, 2.0, 2.2], "mean": 2.1, "max": 2.2,
                             "frozen": False, "jump": None, "verdict": "moving"}
            row.update(motion_by_label.get(row["label"], {}))
        return rows

    def test_every_mechanical_finding_has_an_owner_a_fix_and_a_timestamp(self):
        rows = self.rows(**{"1.2": {"still": {"mean": 120.0, "spread": 1.0,
                                              "dominant_share": 1.0, "clipped_share": 0.0}}})
        found = qa.mechanical_findings(rows)
        self.assertEqual(len(found), 1)
        f = found[0]
        for key in ("timestamp", "frame", "problem", "why", "root_cause", "owner",
                    "fix", "severity", "kind", "shot"):
            self.assertIn(key, f)
        self.assertEqual(f["kind"], "flat")
        self.assertEqual(f["shot"], "1.2")
        self.assertEqual(f["timestamp"], 5.0)
        self.assertIn("mechanical", f["root_cause"].lower() + f["why"].lower())

    def test_a_defect_on_the_hook_or_the_ending_is_blocking_elsewhere_minor(self):
        frozen = {"motion": {"steps": [0.0, 0.0, 0.0, 0.0], "mean": 0.0, "max": 0.0,
                             "frozen": True, "jump": None, "verdict": "frozen"}}
        on_hook = qa.mechanical_findings(self.rows(**{"1.1": frozen}))
        self.assertEqual([f["severity"] for f in on_hook], ["blocking"])
        on_end = qa.mechanical_findings(self.rows(**{"1.4": frozen}))
        self.assertEqual([f["severity"] for f in on_end], ["blocking"])
        in_middle = qa.mechanical_findings(self.rows(**{"1.2": frozen}))
        self.assertEqual([f["severity"] for f in in_middle], ["minor"])

    def test_the_same_defect_on_two_sampled_shots_is_blocking(self):
        clipped = {"still": {"mean": 200.0, "spread": 50.0, "dominant_share": 0.3,
                             "clipped_share": 0.4}}
        found = qa.mechanical_findings(self.rows(**{"1.2": clipped, "1.3": clipped}))
        self.assertEqual(len(found), 2)
        self.assertTrue(all(f["severity"] == "blocking" for f in found), found)
        self.assertTrue(all(f["kind"] == "clipped" for f in found))

    def test_a_jump_inside_a_shot_is_a_finding(self):
        jump = {"motion": {"steps": [2.0, 2.0, 60.0, 2.0], "mean": 16.5, "max": 60.0,
                           "frozen": False, "jump": 2, "verdict": "jump"}}
        found = qa.mechanical_findings(self.rows(**{"1.3": jump}))
        self.assertEqual([f["kind"] for f in found], ["jump"])
        self.assertIn("60", found[0]["problem"])

    def test_engine_diagnostics_become_findings_with_the_ids(self):
        diag = {"1.2": {"off_camera": ["shot-1.2/x", "shot-1.2/y"], "warnings": 0}}
        found = qa.mechanical_findings(self.rows(), diagnostics=diag)
        self.assertEqual([f["kind"] for f in found], ["off_camera"])
        self.assertIn("2 object", found[0]["problem"])
        self.assertIn("shot-1.2/x", found[0]["problem"])
        self.assertEqual(found[0]["severity"], "minor")

    def test_an_unmeasured_sample_raises_no_finding(self):
        rows = qa.sample(_build())
        self.assertEqual(qa.mechanical_findings(rows), [])


# --------------------------------------------------------------------------- #
# AUTO-145: a diagram built in steps, or shown whole and talked over
# --------------------------------------------------------------------------- #

def _drawing(path: Path, lines: int, *, shift: int = 0, rise: int = 0, ground=None) -> Path:
    """`lines` bright horizontal strokes on a dark ground (or on `ground`):
    a diagram with that many parts drawn."""
    im = ground.copy() if ground is not None else Image.new("L", (W, H), 30)
    draw = ImageDraw.Draw(im)
    for i in range(lines):
        y = 20 + i * 11 + rise
        draw.line([(40 + shift, y), (600 + shift, y)], fill=230, width=3)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return path


def _texture():
    """A busy ground: 1,500 small grey squares at seeded random places."""
    import random
    rng = random.Random(1)
    im = Image.new("L", (W, H), 30)
    draw = ImageDraw.Draw(im)
    for _ in range(1500):
        x, y = rng.randrange(W), rng.randrange(H)
        draw.rectangle([x, y, x + 3, y + 3], fill=rng.choice((60, 90, 120)))
    return im


#: _build's 1.2 (3-7 s) is the first shot of a diagram effect; its take
#: says one line 1.5 s in, at 4.5 s, between the build stills.
BUILDS = {"1.2": "fx-steps"}
VOICE = {"takes": [{"label": "1.2", "text": "Then the second part.", "seconds": 4.0,
                    "words": [{"text": "Then", "start": 1.5, "end": 1.8},
                              {"text": "the", "start": 1.8, "end": 1.9},
                              {"text": "second", "start": 1.9, "end": 2.3},
                              {"text": "part.", "start": 2.3, "end": 2.8}]}]}


def _built(series=None):
    """The sampled rows of _build with 1.2's build stills, each reading
    given its ink in time order: first, the line, last."""
    rows = qa.sample(_build(), voice=VOICE, builds=BUILDS)
    if series is not None:
        points = sorted([e for r in rows if r["label"] == "1.2"
                         for e in (r.get("builds") or []) + (r.get("lines") or [])],
                        key=lambda e: e.get("t", e.get("at")))
        for entry, ink in zip(points, series):
            entry["ink"] = ink
    return rows


class Ink(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qa-ink-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_blank_frame_has_no_ink_and_more_lines_have_more(self):
        self.assertEqual(qa.INK_EDGE, 32)
        self.assertAlmostEqual(qa.frame_ink(_flat(self.tmp / "blank.png", 30)), 0.0,
                               places=3)
        inks = [qa.frame_ink(_drawing(self.tmp / f"d{n}.png", n)) for n in (1, 5, 20)]
        self.assertGreater(inks[0], 0.0)
        self.assertEqual(inks, sorted(inks))
        self.assertLess(inks[0], inks[2])
        self.assertIsNone(qa.frame_ink(self.tmp / "missing.png"))

    def test_measure_records_ink_on_every_still_and_reading(self):
        rows = _built()
        frames = self.tmp / "frames"
        for row in rows:
            _drawing(frames / row["frame"], 10)
            for name in (row.get("burst") or {}).get("files") or []:
                _drawing(frames / name, 10)
        qa.measure(rows, frames)
        self.assertTrue(all(isinstance(r.get("ink"), float) for r in rows), rows)
        entries = [e for r in rows for e in (r.get("builds") or []) + (r.get("lines") or [])]
        self.assertTrue(entries)
        self.assertTrue(all(isinstance(e.get("ink"), float) for e in entries), entries)


class NoBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qa-build-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def found(self, series):
        return [f for f in qa.mechanical_findings(_built(series)) if f["kind"] == "no-build"]

    def test_a_series_that_never_grows_is_a_minor_no_build_with_its_ink(self):
        self.assertEqual(qa.BUILD_STEP, 0.15)
        [f] = self.found([0.20, 0.21, 0.22])
        self.assertEqual((f["shot"], f["owner"], f["severity"]), ("1.2", "vfx", "minor"))
        self.assertIn("effect `fx-steps` is on screen whole from the cut", f["problem"])
        self.assertIn("ink 0.20 at 3.25 s", f["problem"])
        self.assertIn("0.22 at 6.75 s", f["problem"])
        self.assertIn("talked over", f["problem"])
        self.assertEqual(f["ink"], [[3.25, 0.2], [4.5, 0.21], [6.75, 0.22]])
        for key in ("why", "root_cause", "fix", "timestamp", "frame"):
            self.assertTrue(f[key], key)

    def test_the_same_drawing_moved_three_pixels_is_no_build(self):
        drawn = qa.frame_ink(_drawing(self.tmp / "a.png", 18))
        moved = qa.frame_ink(_drawing(self.tmp / "b.png", 18, shift=3))
        self.assertEqual(len(self.found([drawn, drawn, moved])), 1)

    def test_a_diagram_that_grows_files_nothing(self):
        self.assertEqual(self.found([0.05, 0.12, 0.22]), [])

    def test_a_growing_diagram_over_a_busy_ground_files_nothing(self):
        ground = _texture()
        series = [qa.frame_ink(_drawing(self.tmp / f"t{n}.png", n, ground=ground))
                  for n in (3, 15, 30)]
        self.assertGreater(series[0], 0.1)                  # the ground alone has ink
        self.assertEqual(self.found(series), [])

    def test_a_build_still_is_never_the_shots_home_and_carries_no_motion(self):
        rows = _built([0.05, 0.12, 0.22])
        for row in rows:
            if row.get("builds") and "burst" not in row:
                # an empty opening frame is what a build looks like
                row["still"] = {"mean": 30.0, "spread": 0.5, "dominant_share": 1.0,
                                "clipped_share": 0.0}
        diag = {"1.2": {"off_camera": ["shot-1.2/x"], "warnings": 0}}
        found = qa.mechanical_findings(rows, diagnostics=diag)
        [off] = [f for f in found if f["kind"] == "off_camera"]
        self.assertEqual(off["timestamp"], 5.0)              # the midpoint still
        self.assertEqual([f["kind"] for f in found], ["off_camera"])   # no flat, no frozen

    def test_rows_without_ink_file_nothing_and_are_named(self):
        rows = _built()
        self.assertEqual([f for f in qa.mechanical_findings(rows)
                          if f["kind"] == "no-build"], [])
        card = qa.note_builds({"not_evidenced": []}, BUILDS, rows)
        [line] = card["not_evidenced"]
        self.assertTrue(line.startswith("builds:"), line)
        self.assertIn("1.2", line)
        self.assertIn("ink", line)

    def test_a_shot_that_ends_on_a_blank_frame_files_nothing_and_is_named(self):
        # A render that came out black has nothing to grow towards: not a
        # no-build, and not measured either, so the scorecard says so.
        rows = _built([0.0, 0.0, 0.0])
        self.assertEqual(self.found([0.0, 0.0, 0.0]), [])
        card = qa.note_builds({"not_evidenced": []}, BUILDS, rows)
        [line] = card["not_evidenced"]
        self.assertIn("1.2", line)
        self.assertIn("ended on a blank frame", line)
        # one that grows from blank is measured and named nowhere
        self.assertEqual(qa.note_builds({"not_evidenced": []}, BUILDS,
                                        _built([0.0, 0.1, 0.2])), {"not_evidenced": []})


class Cap(unittest.TestCase):
    def scores(self, **over):
        base = {k: 9.0 for k in qa.CATEGORIES}
        base.update(over)
        return base

    def test_a_blocking_mechanical_finding_caps_visual_and_vfx_at_six(self):
        blocking = [{"kind": "flat", "severity": "blocking"}]
        scores, overall, capped = qa.cap_scores(self.scores(), 9.0, blocking)
        self.assertEqual(scores["visual_quality"], qa.MECHANICAL_CAP)
        self.assertEqual(scores["animation_vfx"], qa.MECHANICAL_CAP)
        self.assertEqual(scores["cinematography"], 9.0)
        self.assertLess(overall, 9.0)
        self.assertEqual(sorted(capped), ["animation_vfx", "visual_quality"])

    def test_a_minor_mechanical_finding_or_a_lower_score_changes_nothing(self):
        minor = [{"kind": "flat", "severity": "minor"}]
        scores, overall, capped = qa.cap_scores(self.scores(), 9.0, minor)
        self.assertEqual(scores, self.scores())
        self.assertEqual(overall, 9.0)
        self.assertEqual(capped, [])
        low = self.scores(visual_quality=4.0, animation_vfx=5.5)
        scores, overall, capped = qa.cap_scores(low, 8.0, [{"severity": "blocking"}])
        self.assertEqual(scores["visual_quality"], 4.0)
        self.assertEqual(scores["animation_vfx"], 5.5)
        self.assertEqual(capped, [])

    def test_the_verdict_cannot_be_ready_while_a_blocking_mechanical_finding_stands(self):
        scores, overall, _ = qa.cap_scores(self.scores(), 9.5, [{"severity": "blocking"}])
        verdict, reasons = qa.verdict_of(scores, overall, blocking=[])
        self.assertEqual(verdict, qa.NOT_READY)
        self.assertTrue(any("visual_quality" in r for r in reasons), reasons)


class Silence(unittest.TestCase):
    def card(self):
        return {"scores": {k: 5.0 for k in qa.CATEGORIES}, "overall": 5.0,
                "not_evidenced": []}

    def test_a_film_without_audio_tracks_scores_sound_and_music_low_not_neutral(self):
        card = qa.score_silence(self.card(), _build(audio_tracks=0))
        self.assertEqual(card["scores"]["sound_design"], qa.SILENT_SCORE)
        self.assertEqual(card["scores"]["music_score"], qa.SILENT_SCORE)
        self.assertEqual(card["scores"]["voiceover"], 5.0)
        joined = " ".join(card["not_evidenced"])
        self.assertIn("sound_design", joined)
        self.assertIn("music_score", joined)
        self.assertIn("not evidenced", joined)
        self.assertLess(card["overall"], 5.0)
        self.assertTrue(qa.silent(_build(audio_tracks=0)))
        self.assertTrue(qa.silent(_build(audio_tracks=[])))
        self.assertTrue(qa.silent({}))

    def test_a_film_with_audio_tracks_keeps_the_reviewer_scores(self):
        for tracks in (3, [{"id": "vo"}]):
            card = qa.score_silence(self.card(), _build(audio_tracks=tracks))
            self.assertEqual(card["scores"]["sound_design"], 5.0)
            self.assertEqual(card["not_evidenced"], [])
            self.assertFalse(qa.silent(_build(audio_tracks=tracks)))


class ContactSheet(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qa-motion-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_every_still_is_tiled_with_its_timestamp(self):
        rows = qa.sample(_build())
        frames = self.tmp / "frames"
        for row in rows:
            _square(frames / row["frame"], 50)
            row["motion"] = {"steps": [1.0], "mean": 1.0, "max": 1.0, "frozen": False,
                             "jump": None, "verdict": "moving"}
        out = qa.contact_sheet(rows, frames, self.tmp / "contact-sheet.png")
        self.assertTrue(out.is_file())
        with Image.open(out) as im:
            cols = min(len(rows), qa.SHEET_COLUMNS)
            rowsn = -(-len(rows) // cols)
            self.assertEqual(im.size[0], cols * qa.SHEET_TILE[0])
            self.assertEqual(im.size[1], rowsn * (qa.SHEET_TILE[1] + qa.SHEET_CAPTION))
        # A missing still leaves a labelled empty tile rather than failing.
        (frames / rows[0]["frame"]).unlink()
        self.assertTrue(qa.contact_sheet(rows, frames, self.tmp / "again.png").is_file())


class FindingsMarkdown(unittest.TestCase):
    def test_mechanical_section_and_burst_numbers_are_written(self):
        rows = qa.sample(_build(), limit=2)
        rows[0]["motion"] = {"steps": [0.0, 0.0, 0.0, 0.0], "mean": 0.0, "max": 0.0,
                             "frozen": True, "jump": None, "verdict": "frozen"}
        rows[0]["still"] = {"mean": 120.0, "spread": 1.0, "dominant_share": 1.0,
                            "clipped_share": 0.0}
        mech = qa.mechanical_findings(rows)
        card = {"scores": {k: 6.0 for k in qa.CATEGORIES}, "overall": 6.0,
                "verdict": qa.NOT_READY, "rationale": "r", "findings": [], "blocking": [],
                "not_evidenced": [], "mechanical": mech, "capped": ["visual_quality"]}
        text = qa.findings_md("pass-05", card, rows, {"regressions": [], "deltas": {}},
                              reasons=["x"])
        self.assertIn("## Mechanical", text)
        self.assertIn("frozen", text)
        self.assertIn("flat", text)
        self.assertIn("0.0/0.0/0.0/0.0", text)
        self.assertIn("capped", text.lower())
        self.assertIn("00:01.500", text)


# --------------------------------------------------------------------------- #
# the whole stage, with a fake renderer and a fake engine
# --------------------------------------------------------------------------- #

SCORECARD = {
    "scores": {k: 9.2 for k in qa.CATEGORIES}, "overall": 9.2,
    "verdict": "READY TO PUBLISH", "findings": [], "blocking": [],
    "not_evidenced": [], "rationale": "looked fine",
    "story": {"premise_restated": "one take", "sections": [],
              "satisfaction": "3 neither satisfied nor dissatisfied",
              "satisfaction_why": "nothing to hold"},
}


class FakeEngine:
    stream_calls: list = []
    json_calls: list = []

    def __init__(self, config):
        self.config = config
        self.usage = Usage()

    def ask_json(self, prompt, schema, system=None):
        FakeEngine.json_calls.append(prompt)
        return dict(SCORECARD)

    def stream(self, prompt, system=None):
        FakeEngine.stream_calls.append({"prompt": prompt, "cwd": self.config.cwd})
        yield Event(type="assistant", subtype="tool_use", tool_name="Read",
                    tool_input={"file_path": "contact-sheet.png"})
        yield Event(type="assistant", text="```json\n" + json.dumps(SCORECARD) + "\n```")
        yield Event(type="result", raw={})


class StageCase(unittest.TestCase):
    """A project with a scene, a build, a render report and the fakes."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qa-motion-"))
        self.work = self.tmp / "work"
        self.root = self.work / "one-take"
        self.root.mkdir(parents=True)
        (self.root / "scene.xml").write_text("<scene/>")
        (self.root / "build.json").write_text(json.dumps(_build()))
        (self.root / "render.json").write_text(json.dumps({
            "diagnostics": [], "result": {"outputs": [{"findings": [
                {"code": "X-rs-scene-render-3D-OFF-CAMERA", "severity": "warning",
                 "time": [0.0, 2.967],
                 "message": "object3D shot-1.1/leg is drawn from 0.000 s to 2.967 s and "
                            "is never inside the camera's view"}]}]}}))
        self._engine, self._renderer = harness.ENGINE, qa.RENDERER
        harness.ENGINE = FakeEngine
        FakeEngine.stream_calls, FakeEngine.json_calls = [], []
        self.jobs = []

    def tearDown(self):
        harness.ENGINE, qa.RENDERER = self._engine, self._renderer
        shutil.rmtree(self.tmp, ignore_errors=True)

    def renderer(self, frozen_hook: bool):
        def render(job):
            self.jobs.append(job)
            out = job["out"]
            if "frames" in job:
                a, b = (int(v) for v in job["frames"].split(".."))
                for f in range(a, b):
                    x = 100 if (frozen_hook and f < 90) else 100 + f // 2
                    _square(Path(out.replace("%04d", f"{f:04d}")), x)
            else:
                _square(Path(out), 100)
            return 0
        return lambda args, scene: render

    def args(self, **kw):
        base = dict(project=str(self.root), title=None, index=None, source=None,
                    stdin=None, genre="short", description="one take", work=str(self.work),
                    scene=None, frame_limit=4, quality="preview", reuse=None,
                    host=None, local=False, fresh=False, quiet=True, model="sonnet",
                    in_build=None, in_script=None, in_voice=None, in_sound=None,
                    in_styles=None, in_gate=None, house=None, no_house=True)
        base.update(kw)
        return argparse.Namespace(**base)


class Stage(StageCase):
    def test_the_pass_carries_motion_evidence_and_the_model_is_capped(self):
        qa.RENDERER = self.renderer(frozen_hook=True)
        code = qa.run(self.args())
        self.assertEqual(code, 1)
        pass_dir = self.root / "qa" / "pass-01"
        self.assertTrue((pass_dir / "contact-sheet.png").is_file())
        samples = json.loads((pass_dir / "samples.json").read_text())
        self.assertTrue(all("burst" in s for s in samples))
        card = json.loads((pass_dir / "scorecard.json").read_text())
        kinds = {f["kind"] for f in card["mechanical"]}
        self.assertIn("frozen", kinds)
        self.assertIn("off_camera", kinds)
        hook = [f for f in card["mechanical"] if f["shot"] == "1.1"]
        self.assertTrue(all(f["severity"] == "blocking" for f in hook), hook)
        self.assertEqual(card["scores"]["visual_quality"], qa.MECHANICAL_CAP)
        self.assertEqual(card["scores"]["animation_vfx"], qa.MECHANICAL_CAP)
        self.assertEqual(card["scores"]["sound_design"], qa.SILENT_SCORE)
        self.assertEqual(card["verdict"], qa.NOT_READY)
        self.assertTrue(any("shot-1.1/leg" in b for b in card["blocking"]), card["blocking"])
        prompt = FakeEngine.stream_calls[0]["prompt"]
        self.assertIn("contact-sheet.png", prompt)
        self.assertIn("frozen", prompt)
        self.assertIn("shot-1.1/leg", prompt)
        self.assertIn("steps", prompt)
        text = (pass_dir / "findings.md").read_text()
        self.assertIn("## Mechanical", text)
        self.assertIn("not evidenced", text)
        self.assertEqual(len(self.jobs), 4)                   # one job per sample
        self.assertTrue(all("frames" in j for j in self.jobs))

    def test_by_default_every_shot_is_sampled_and_nothing_is_named_unsampled(self):
        # AUTO-103: no --frames is one still per shot, each in samples.json.
        qa.RENDERER = self.renderer(frozen_hook=False)
        qa.run(self.args(frame_limit=None))
        pass_dir = self.root / "qa" / "pass-01"
        samples = json.loads((pass_dir / "samples.json").read_text())
        self.assertEqual(sorted(s["label"] for s in samples), ["1.1", "1.2", "1.3", "1.4"])
        card = json.loads((pass_dir / "scorecard.json").read_text())
        self.assertEqual(card["unsampled"], [])
        self.assertFalse(any(n.startswith("frames:") for n in card["not_evidenced"]))

    def test_a_tight_frames_names_the_shots_it_left_unsampled(self):
        qa.RENDERER = self.renderer(frozen_hook=False)
        qa.run(self.args(frame_limit=2))
        pass_dir = self.root / "qa" / "pass-01"
        card = json.loads((pass_dir / "scorecard.json").read_text())
        self.assertEqual(len(card["samples"]), 2)
        self.assertEqual(len(card["unsampled"]), 2)
        line = next(n for n in card["not_evidenced"] if n.startswith("frames:"))
        self.assertIn("2 of 4 shots", line)
        self.assertIn("--frames 2", line)
        for label in card["unsampled"]:
            self.assertIn(label, line)
        self.assertIn(line, (pass_dir / "findings.md").read_text())

    def test_qa_json_names_the_house_it_was_judged_under(self):
        # AUTO-89: with no house file anywhere, the shipped default is the house.
        qa.RENDERER = self.renderer(frozen_hook=False)
        qa.run(self.args(no_house=False))
        record = json.loads((self.root / "qa.json").read_text())
        self.assertEqual(record["house"]["sources"], ["ytstudio/house/default.md"])
        self.assertTrue(record["house"]["sha256"])

    def test_reuse_measures_the_old_frames_again_without_rendering(self):
        (self.root / "render.json").unlink()            # nothing blocking from the engine
        qa.RENDERER = self.renderer(frozen_hook=False)
        qa.run(self.args())
        rendered = len(self.jobs)
        self.assertGreater(rendered, 0)
        qa.RENDERER = lambda args, scene: (lambda job: self.fail("rendered on --reuse"))
        code = qa.run(self.args(reuse="pass-01", fresh=True))
        self.assertEqual(code, 1)                       # silent film: never ready
        card = json.loads((self.root / "qa" / "pass-01" / "scorecard.json").read_text())
        self.assertFalse(any(f["kind"] == "frozen" for f in card["mechanical"]))
        self.assertEqual(card["scores"]["visual_quality"], 9.2)   # nothing blocking: no cap
        self.assertEqual(len(self.jobs), rendered)


class BuildStage(StageCase):
    """AUTO-145 end to end: `yt qa` on a film whose first diagram shot is
    1.2 (3-7 s), the effect running on over 1.3."""

    def setUp(self):
        super().setUp()
        (self.root / "render.json").unlink()            # nothing from the engine
        build = _build()
        for shot in build["shots"]:
            shot.update(include=f"shot-{shot['label']}", file="vfx/v01/a.scene.xml#w")
        build["shots"][2]["include"] = "shot-1.2"
        (self.root / "build.json").write_text(json.dumps(build))
        (self.root / "vfx.json").write_text(json.dumps({"sections": [{"effects": [
            {"id": "fx-steps", "type": "2d-diagram", "shots": ["1.2", "1.3"]}]}],
            "scene": {"dropped": {}}}))

    def renderer(self, grows: bool):
        def render(job):
            self.jobs.append(job)
            out = job["out"]
            if "frames" in job:
                a, b = (int(v) for v in job["frames"].split(".."))
                times = [(f, f / 30) for f in range(a, b)]
            else:
                times = [(None, float(job["still"]))]
            for f, t in times:
                # a pixel's sway frame to frame, so no burst reads frozen
                parts = int(2 + 3 * t) if grows else 30
                _drawing(Path(out.replace("%04d", f"{f:04d}") if f is not None else out),
                         parts, rise=(f or 0) % 2)
            return 0
        return lambda args, scene: render

    def card(self):
        return json.loads((self.root / "qa" / "pass-01" / "scorecard.json").read_text())

    def test_a_finished_diagram_from_the_cut_is_a_minor_no_build(self):
        qa.RENDERER = self.renderer(grows=False)
        qa.run(self.args(frame_limit=None))
        samples = json.loads((self.root / "qa" / "pass-01" / "samples.json").read_text())
        self.assertTrue(all("ink" in s for s in samples), samples)
        self.assertEqual(sum(1 for s in samples if s.get("builds")), 2)
        card = self.card()
        [f] = [f for f in card["mechanical"] if f["kind"] == "no-build"]
        self.assertEqual((f["shot"], f["severity"], f["owner"]), ("1.2", "minor", "vfx"))
        self.assertIn("fx-steps", f["problem"])
        self.assertEqual(card["scores"]["visual_quality"], 9.2)   # advisory: no cap

    def test_a_diagram_phase_inside_the_sections_world_is_judged(self):
        # The default layout (AUTO-145 fix): one world per section, every
        # shot of it folded into the include of its first shot. The diagram
        # starts at 1.2, inside 1.1's world, and is still judged.
        build = json.loads((self.root / "build.json").read_text())
        for shot in build["shots"]:
            shot["include"] = "shot-1.1"
        (self.root / "build.json").write_text(json.dumps(build))
        qa.RENDERER = self.renderer(grows=False)
        qa.run(self.args(frame_limit=None))
        [f] = [f for f in self.card()["mechanical"] if f["kind"] == "no-build"]
        self.assertEqual(f["shot"], "1.2")

    def test_a_diagram_that_grows_files_none_and_nor_does_its_later_shot(self):
        qa.RENDERER = self.renderer(grows=True)
        qa.run(self.args(frame_limit=None))
        card = self.card()
        self.assertEqual([f for f in card["mechanical"] if f["kind"] == "no-build"], [])
        self.assertFalse(any(n.startswith("builds:") for n in card["not_evidenced"]))


TEXT_WORLD = """<scene version="1.3">
  <assets>
    <text id="t-super" text="The paint hid the rust on every last joint" width="900" height="120"/>
    <text id="t-quote" text="We knew it would fail one day" width="900" height="120"/>
    <text id="t-gone" text="a label the camera never once turns to see" width="900" height="120"/>
  </assets>
  <symbols>
    <symbol id="world-a" width="640" height="360">
      <layer id="l-super" asset="t-super"/>
      <layer id="quote-1" asset="t-quote"/>
      <layer id="l-gone" asset="t-gone"/>
    </symbol>
  </symbols>
</scene>
"""


class TextStage(StageCase):
    """AUTO-145 end to end: `yt qa` counts the words the rendered document
    puts on screen, from the scene QA renders and the world it includes,
    and files text-dense from them with the screenplay's supers and the
    engine's off-camera ids."""

    def setUp(self):
        super().setUp()
        (self.root / "vfx" / "v01").mkdir(parents=True)
        (self.root / "vfx" / "v01" / "a.scene.xml").write_text(TEXT_WORLD)
        (self.root / "scene.xml").write_text(
            '<scene version="1.3"><project width="640" height="360" fps="30" '
            'duration="16.5"/><composition><include id="shot-1.2" start="3" end="7" '
            'src="vfx/v01/a.scene.xml" symbol="world-a"/></composition></scene>')
        (self.root / "script.json").write_text(json.dumps({"sections": [{"shots": [
            {"label": "1.2", "narration": "It rusted.",
             "on_screen_text": ["The paint hid the rust on every last joint"]}]}]}))
        (self.root / "render.json").write_text(json.dumps({
            "diagnostics": [], "result": {"outputs": [{"findings": [
                {"code": "X-rs-scene-render-3D-OFF-CAMERA", "severity": "warning",
                 "time": [3.0, 7.0],
                 "message": "text shot-1.2/l-gone is drawn from 3.000 s to 7.000 s and "
                            "is never inside the camera's view"}]}]}}))
        self._geometry = qa.GEOMETRY
        qa.GEOMETRY = lambda args, project, scene, shots: {"status": "skipped",
                                                           "why": "a test"}
        self.addCleanup(setattr, qa, "GEOMETRY", self._geometry)
        steady_cutting(self)

    def test_a_nine_word_label_is_one_minor_text_dense_naming_its_node(self):
        qa.RENDERER = self.renderer(frozen_hook=False)
        qa.run(self.args(frame_limit=None))
        pass_dir = self.root / "qa" / "pass-01"
        samples = json.loads((pass_dir / "samples.json").read_text())
        self.assertTrue(all("text" in s for s in samples), samples)
        [inside] = [s for s in samples if s["label"] == "1.2"]
        self.assertEqual(sorted(inside["text"]["ids"]),
                         ["shot-1.2/l-super", "shot-1.2/quote-1"])   # not the unseen one
        self.assertEqual(inside["text"]["words"], 9)                 # not the quote
        card = json.loads((pass_dir / "scorecard.json").read_text())
        [f] = [f for f in card["mechanical"] if f["kind"] == "text-dense"]
        self.assertEqual((f["shot"], f["severity"], f["owner"]), ("1.2", "minor", "vfx"))
        self.assertIn("`shot-1.2/l-super`", f["problem"])
        self.assertIn("the screenplay asked for this super; rule:wall-of-text already "
                      "flagged it", f["problem"])
        self.assertNotIn("quote-1", f["problem"])
        self.assertNotIn("l-gone", f["problem"])
        self.assertEqual(card["scores"]["visual_quality"], 9.2)      # advisory: no cap


# --------------------------------------------------------------------------- #
# the rhythm step (AUTO-141)
# --------------------------------------------------------------------------- #

def _aligned_voice(build, wpm=170.0):
    """voice.json with one exact take per shot, narrated from 0.2 s in to
    0.2 s before the shot's end."""
    takes = []
    for shot in build["shots"]:
        d, n = 60.0 / wpm, int((shot["seconds"] - 0.4) * wpm / 60.0)
        words = [{"text": f"w{i}", "start": round(0.2 + i * d, 3),
                  "end": round(0.2 + (i + 1) * d, 3)} for i in range(n)]
        takes.append({"label": shot["label"], "section": shot["section"], "at": shot["at"],
                      "seconds": shot["seconds"], "timing": "exact", "words": words,
                      "text": " ".join(w["text"] for w in words)})
    return {"engine": "kokoro", "takes": takes, "timing": ["exact"]}


class RhythmStage(StageCase):
    def setUp(self):
        super().setUp()
        self._scan = qa.RHYTHM_SCAN
        self.scans = []
        (self.root / "voice.json").write_text(json.dumps(_aligned_voice(_build())))
        self.addCleanup(setattr, qa, "RHYTHM_SCAN", self._scan)

    def current_cut(self):
        video = self.root / "video.mp4"
        video.write_bytes(b"not really an mp4")
        scene_at = (self.root / "scene.xml").stat().st_mtime
        os.utime(video, (scene_at + 10, scene_at + 10))
        return video

    def held_hook(self, cut, args, **kw):
        """Scores with nothing changing over the first 9 s (the hook), the
        picture changing every frame after it."""
        self.scans.append(str(cut))
        fps = rhythm.value("scan_fps")
        scores = [(round(k / fps, 3), 0.0 if k / fps <= 9.0 else 0.05)
                  for k in range(int(16.5 * fps))]
        return {"scores": scores, "scan": {"host": "render-1", "fps": fps, "width": 320,
                                           "upload": "sent", "upload_seconds": 0.4,
                                           "upload_bytes": 17, "scan_seconds": 2.0}}

    def test_a_blocking_hold_on_the_hook_blocks_and_caps(self):
        self.current_cut()
        qa.RENDERER = self.renderer(frozen_hook=False)
        qa.RHYTHM_SCAN = self.held_hook
        with mock.patch.dict(rhythm.RHYTHM["calibrated"], {"value": True}):
            code = qa.run(self.args())
        self.assertEqual(code, 1)
        self.assertEqual(len(self.scans), 1)
        pass_dir = self.root / "qa" / "pass-01"
        card = json.loads((pass_dir / "scorecard.json").read_text())
        hold = [f for f in card["mechanical"] if f["kind"] == "hold"]
        self.assertEqual([(f["shot"], f["severity"]) for f in hold], [("1.1", "blocking")])
        self.assertTrue(any("while the narration runs" in b for b in card["blocking"]),
                        card["blocking"])
        self.assertEqual(card["verdict"], qa.NOT_READY)
        self.assertEqual(card["scores"]["editing_pacing"], qa.MECHANICAL_CAP)
        self.assertEqual(card["scores"]["visual_quality"], qa.MECHANICAL_CAP)
        self.assertIn("editing_pacing", card["capped"])
        record = json.loads((pass_dir / "rhythm.json").read_text())
        self.assertEqual(record["cv"]["basis"], "events")
        self.assertEqual(record["longest_hold"]["shot"], "1.1")
        self.assertIn("rate", record["opening"])
        self.assertIn("median", record["opening"])
        self.assertEqual([r["section"] for r in record["sections"]], ["One take"])
        self.assertEqual(record["scan"]["upload_bytes"], 17)
        self.assertTrue(record["scores"])
        self.assertEqual(card["rhythm"]["cv"], record["cv"])
        self.assertNotIn("scores", card["rhythm"])
        # the reviewer is shown the numbers as measurements
        prompt = FakeEngine.stream_calls[0]["prompt"]
        self.assertIn("\"rhythm\"", prompt)
        self.assertIn("not judged", prompt)
        self.assertIn("caps editing_pacing", prompt)
        self.assertIn("editing_pacing", (pass_dir / "findings.md").read_text())

    def test_equal_shots_with_no_scan_are_not_ready_and_capped(self):
        build = _build()
        for n, shot in enumerate(build["shots"]):
            shot["at"], shot["seconds"] = 4.0 * n, 4.0
        build["scene"]["duration"] = 16.0
        (self.root / "build.json").write_text(json.dumps(build))
        (self.root / "render.json").unlink()            # no off-camera finding to cap
        (self.root / "voice.json").write_text(json.dumps(_aligned_voice(build)))
        qa.RENDERER = self.renderer(frozen_hook=False)
        qa.RHYTHM_SCAN = lambda *a, **k: self.fail("scanned without a cut")
        code = qa.run(self.args())
        self.assertEqual(code, 1)
        card = json.loads((self.root / "qa" / "pass-01" / "scorecard.json").read_text())
        metronomic = [f for f in card["mechanical"] if f["kind"] == "metronomic"]
        self.assertEqual([(f["basis"], f["severity"], f["owner"]) for f in metronomic],
                         [("shots", "blocking", "script")])
        self.assertLessEqual(card["scores"]["editing_pacing"], qa.MECHANICAL_CAP)
        self.assertEqual(card["scores"]["visual_quality"], 9.2)
        self.assertTrue(any("cuts and builds vary" in r for r in card["blocking"]))
        self.assertTrue(any("blocking item" in r for r in card["bar"]), card["bar"])
        # the bar names the rhythm finding that capped editing_pacing
        self.assertTrue(any("editing_pacing" in r and "cuts and builds vary" in r
                            for r in card["bar"]), card["bar"])

    def test_the_bar_names_the_rhythm_finding_when_the_reviewer_was_already_under(self):
        # The reviewer gave editing_pacing 5.0, so the cap lowers nothing;
        # the bar still says which rhythm finding holds it there.
        build = _build()
        for n, shot in enumerate(build["shots"]):
            shot["at"], shot["seconds"] = 4.0 * n, 4.0
        build["scene"]["duration"] = 16.0
        (self.root / "build.json").write_text(json.dumps(build))
        (self.root / "render.json").unlink()
        (self.root / "voice.json").write_text(json.dumps(_aligned_voice(build)))
        qa.RENDERER = self.renderer(frozen_hook=False)
        with mock.patch.dict(SCORECARD["scores"], {"editing_pacing": 5.0}):
            qa.run(self.args())
        card = json.loads((self.root / "qa" / "pass-01" / "scorecard.json").read_text())
        self.assertEqual(card["scores"]["editing_pacing"], 5.0)
        self.assertNotIn("editing_pacing", card["capped"])
        self.assertTrue(any("editing_pacing" in r and "metronomic" in r
                            and "cuts and builds vary" in r for r in card["bar"]), card["bar"])

    def test_a_scan_the_link_dropped_is_not_evidenced(self):
        # The real scan_remote, its ssh exiting 255 after half the film's
        # scores, the hook held still in that half: nothing render-side is
        # measured from it, and no hold is filed or passed.
        from tests import hermetic
        from ytstudio import render
        self.current_cut()
        hermetic.fake_remote(self, RS_RENDER_HOSTS="render-1")
        half = "".join(f"frame:{k} pts:{k} pts_time:{k / 4:g}\n"
                       f"lavfi.scene_score={0.0 if k < 36 else 0.05}\n" for k in range(33))

        def run(argv, **kw):
            if argv[0] == "ssh":
                return subprocess.CompletedProcess(argv, 255, "", half)
            return subprocess.CompletedProcess(argv, 0, "", "")
        qa.RHYTHM_SCAN = rhythm.scan_remote
        qa.RENDERER = self.renderer(frozen_hook=False)
        with mock.patch.object(rhythm.subprocess, "run", run), \
                mock.patch.object(render, "remote_host", lambda args, job: "render-1"), \
                mock.patch.object(render, "remove_remote", lambda host, path, **k: None), \
                mock.patch.dict(render._HOSTS, {"render-1": render.Host(
                    addr="render-1", ffprobe="/opt/ff/ffprobe")}), \
                mock.patch.dict(rhythm.RHYTHM["calibrated"], {"value": True}):
            qa.run(self.args())
        self.assert_render_measures_not_evidenced("exit 255")

    def assert_render_measures_not_evidenced(self, why):
        card = json.loads((self.root / "qa" / "pass-01" / "scorecard.json").read_text())
        rows = card["rhythm"]["not_evidenced"]
        self.assertEqual({r["measure"] for r in rows if r["measure"] != "wpm"},
                         {"hold", "slow-opening"})
        self.assertTrue(all(why in r["why"] for r in rows if r["measure"] != "wpm"), rows)
        self.assertEqual(card["rhythm"]["cv"]["basis"], "shots")
        self.assertIsNotNone(card["rhythm"]["cv"]["shots"])
        self.assertEqual(card["rhythm"]["sections"][0]["timing"], "exact")
        self.assertIsNotNone(card["rhythm"]["sections"][0]["wpm"])
        self.assertTrue(any(n.startswith("rhythm:") for n in card["not_evidenced"]))
        self.assertFalse([f for f in card["mechanical"] if f["kind"] in ("hold",
                                                                        "slow-opening")])
        return card

    def test_under_no_remote_the_render_measures_are_not_evidenced(self):
        self.current_cut()
        qa.RENDERER = self.renderer(frozen_hook=False)
        with mock.patch.dict(os.environ, {"YT_NO_REMOTE": "1"}):
            qa.run(self.args())
        self.assert_render_measures_not_evidenced("YT_NO_REMOTE")

    def test_with_no_render_host_the_render_measures_are_not_evidenced(self):
        self.current_cut()
        qa.RENDERER = self.renderer(frozen_hook=False)
        qa.RHYTHM_SCAN = lambda *a, **k: (_ for _ in ()).throw(rhythm.RhythmError(
            "no render host configured (RS_RENDER_HOSTS or RS_RENDER_HOST)"))
        qa.run(self.args())
        self.assert_render_measures_not_evidenced("RS_RENDER_HOSTS")

    def test_a_stale_cut_is_not_scanned(self):
        video = self.current_cut()
        scene_at = (self.root / "scene.xml").stat().st_mtime
        os.utime(video, (scene_at - 10, scene_at - 10))
        qa.RENDERER = self.renderer(frozen_hook=False)
        qa.RHYTHM_SCAN = lambda *a, **k: self.fail("scanned a stale cut")
        qa.run(self.args())
        card = self.assert_render_measures_not_evidenced("predates scene.xml")
        self.assertIn("yt render", card["rhythm"]["not_evidenced"][0]["command"])

    def test_reuse_reads_rhythm_json_and_scans_nothing(self):
        self.current_cut()
        qa.RENDERER = self.renderer(frozen_hook=False)
        qa.RHYTHM_SCAN = self.held_hook
        qa.run(self.args())
        first = json.loads((self.root / "qa" / "pass-01" / "rhythm.json").read_text())
        qa.RENDERER = lambda args, scene: (lambda job: self.fail("rendered on --reuse"))
        qa.RHYTHM_SCAN = lambda *a, **k: self.fail("scanned on --reuse")
        qa.run(self.args(reuse="pass-01", fresh=True))
        card = json.loads((self.root / "qa" / "pass-01" / "scorecard.json").read_text())
        self.assertEqual(card["rhythm"]["cv"], first["cv"])
        self.assertEqual(card["rhythm"]["longest_hold"], first["longest_hold"])
        self.assertEqual(len(self.scans), 1)


if __name__ == "__main__":
    unittest.main()
