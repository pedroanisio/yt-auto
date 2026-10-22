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
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from claude_engine import Event, Usage
from ytstudio import harness, qa

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


class Stage(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
