"""`yt doctor` judges delivery from evidence, not from build.json.

The droplet film shipped as "publishable": a draft render, warnings in the
engine report, no gate and no QA pass. build.json's `publishable` only says
the build has no placeholder and no blocking editorial note. Delivery needs a
final render of the whole programme with nothing the engine warned about
(what `--strict` guarantees), a technical gate that found it deliverable and
a QA pass at READY TO PUBLISH -- both judged after that render. Each piece
missing is a fail, which is doctor's word for "blocks publishing".
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import doctor, workspace  # noqa: E402


def engine_report(*, quality="final", start=0.0, end=30.0, ok=True,
                  warnings=(), findings=(), unsupported=(), evaluation=()) -> dict:
    return {"format": "scene-render-cli/1", "ok": ok, "exit_code": 0 if ok else 1,
            "result": {"outputs": [{
                "quality": quality, "range": [start, end], "frames": int(end * 30),
                "warnings": list(warnings), "findings": list(findings),
                "unsupported": list(unsupported), "evaluation_warnings": list(evaluation)}]}}


class DeliveryCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "a-film"
        self.root.mkdir()
        self.write("idea.json", {"title": "A film", "genre": "cinematic",
                                 "description": "one drop"})
        self.write("build.json", {"scene": {"duration": 30.0}, "publishable": True,
                                  "findings": [], "layers": {}, "timeline": {"shots": 9},
                                  "audio_tracks": 4})
        self.clock = time.time() - 1000

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name: str, data, *, age: float | None = None) -> Path:
        path = self.root / name
        path.write_text(json.dumps(data) if not isinstance(data, bytes) else "", encoding="utf-8")
        if isinstance(data, bytes):
            path.write_bytes(data)
        return path

    def stamp(self, name: str) -> None:
        """Files stamped later are newer: video, then gate, then qa."""
        self.clock += 10
        os.utime(self.root / name, (self.clock, self.clock))

    def rendered(self, **report) -> None:
        self.write("render.json", engine_report(**report))
        self.write("video.mp4", b"\x00" * 1024)
        self.stamp("render.json")
        self.stamp("video.mp4")

    def gated(self, verdict="deliverable", exit=0, mode="technical") -> None:
        self.write("gate.json", {"mode": mode, "exit": exit,
                                 "triage": {"verdict": verdict, "blocking": []}})
        self.stamp("gate.json")

    def scored(self, verdict="READY TO PUBLISH", overall=9.2) -> None:
        self.write("qa.json", {"pass": "pass-03", "verdict": verdict, "overall": overall})
        self.stamp("qa.json")

    def report(self):
        return doctor.check_project(workspace.Project(self.root))

    def delivery(self, level=None):
        return [f for f in self.report().findings
                if f.area == "delivery" and (level is None or f.level == level)]

    def fails_about(self, text: str):
        return [f for f in self.delivery("fail") if text in f.message]


class Delivery(DeliveryCase):
    def test_no_render_means_no_delivery_verdict_yet(self):
        self.assertEqual(self.delivery(), [])

    def test_everything_in_place_is_ready(self):
        self.rendered(); self.gated(); self.scored()
        self.assertEqual(self.delivery("fail"), [])
        ok = self.delivery("ok")
        self.assertEqual(len(ok), 1)
        self.assertIn("READY TO PUBLISH", ok[0].message)
        self.assertTrue(self.report().metrics["delivery"]["ready"])

    def test_a_draft_render_is_not_delivery(self):
        self.rendered(quality="draft"); self.gated(); self.scored()
        fails = self.fails_about("draft")
        self.assertEqual(len(fails), 1)
        self.assertIn("--quality final --strict", fails[0].fix)
        self.assertFalse(self.report().metrics["delivery"]["ready"])

    def test_a_render_with_engine_warnings_is_not_delivery(self):
        self.rendered(warnings=["X-rs-scene-render-3D-OFF-CAMERA: object3D bk-3 ..."],
                      findings=[{"code": "FONT-SUB", "severity": "warning",
                                "message": "sans-serif is drawn with DejaVu Sans"}])
        self.gated(); self.scored()
        fails = self.fails_about("warning")
        self.assertEqual(len(fails), 1)
        self.assertIn("2", fails[0].message)            # counts what the engine said
        self.assertIn("--strict", fails[0].fix)

    def test_information_findings_do_not_block(self):
        self.rendered(findings=[{"code": "PREC1", "severity": "info", "message": "x"}])
        self.gated(); self.scored()
        self.assertEqual(self.delivery("fail"), [])

    def test_a_render_of_part_of_the_programme_is_not_delivery(self):
        self.rendered(start=0.0, end=12.0); self.gated(); self.scored()
        fails = self.fails_about("12")
        self.assertEqual(len(fails), 1)
        self.assertIn("30", fails[0].message)

    def test_a_failed_engine_run_is_not_delivery(self):
        self.rendered(ok=False); self.gated(); self.scored()
        self.assertTrue(self.fails_about("did not finish"))

    def test_no_gate_is_not_delivery(self):
        self.rendered(); self.scored()
        fails = self.fails_about("gate")
        self.assertEqual(len(fails), 1)
        self.assertTrue(fails[0].fix.startswith("yt gate"))

    def test_a_gate_that_found_it_not_deliverable_blocks(self):
        self.rendered(); self.gated(verdict="not deliverable", exit=1); self.scored()
        self.assertEqual(len(self.fails_about("not deliverable")), 1)

    def test_a_frames_only_gate_is_not_the_delivery_gate(self):
        self.rendered(); self.gated(mode="frames"); self.scored()
        self.assertEqual(len(self.fails_about("frames")), 1)

    def test_a_gate_older_than_the_render_is_stale(self):
        self.write("gate.json", {"mode": "technical", "exit": 0,
                                 "triage": {"verdict": "deliverable"}})
        self.stamp("gate.json")
        self.rendered(); self.scored()
        self.assertEqual(len(self.fails_about("predates")), 1)

    def test_no_qa_pass_is_not_delivery(self):
        self.rendered(); self.gated()
        fails = self.fails_about("QA")
        self.assertEqual(len(fails), 1)
        self.assertTrue(fails[0].fix.startswith("yt qa"))

    def test_a_qa_pass_below_the_bar_blocks(self):
        self.rendered(); self.gated(); self.scored(verdict="NOT READY", overall=6.1)
        fails = self.fails_about("NOT READY")
        self.assertEqual(len(fails), 1)
        self.assertIn("6.1", fails[0].message)

    def test_a_qa_pass_older_than_the_render_is_stale(self):
        self.write("qa.json", {"pass": "pass-01", "verdict": "READY TO PUBLISH"})
        self.stamp("qa.json")
        self.rendered(); self.gated()
        self.assertEqual(len(self.fails_about("predates")), 1)

    def test_build_publishable_does_not_override_missing_evidence(self):
        self.rendered(quality="draft")
        report = self.report()
        self.assertEqual(report.status, "blocked")
        self.assertGreaterEqual(len(self.delivery("fail")), 3)   # draft, gate, qa
        self.assertFalse(report.metrics["delivery"]["ready"])

    def test_a_programme_with_no_audio_is_not_delivery(self):
        self.write("build.json", {"scene": {"duration": 30.0}, "publishable": True,
                                  "audio_tracks": 0})
        self.rendered(); self.gated(); self.scored()
        fails = self.fails_about("no audio")
        self.assertEqual(len(fails), 1)
        self.assertIn("--synth", fails[0].fix)

    def test_cues_still_without_a_file_point_at_synthesis(self):
        self.write("script.json", {"sections": []})
        self.write("sound.json", {"cues": [
            {"cue_id": "C1", "kind": "sfx", "name": "plink", "at": 1, "seconds": 1},
            {"cue_id": "C2", "kind": "silence", "name": "breath", "at": 2, "seconds": 1},
            {"cue_id": "C3", "kind": "ambience", "name": "room", "at": 0, "seconds": 2,
             "file": "sound/gone.wav"}], "coverage": {}})
        warns = [f for f in self.report().findings
                 if f.area == "sound" and f.level == "warn" and "no file" in f.message]
        self.assertEqual(len(warns), 1)
        self.assertIn("2", warns[0].message)          # C1, and C3 whose file is gone
        self.assertIn("--synth", warns[0].fix)

    def test_doctor_reads_the_same_verdict_qa_writes(self):
        from ytstudio import qa
        self.assertEqual(doctor.READY_TO_PUBLISH, qa.READY)

    def test_the_first_next_step_repairs_the_render(self):
        self.rendered(quality="draft")
        steps = self.report().next_steps
        self.assertTrue(any("--quality final --strict" in s for s in steps), steps)


class NextStep(unittest.TestCase):
    def test_the_next_script_command_has_its_plan_reviewed(self):
        # AUTO-92: doctor suggests the script as `yt run` would run it.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "a-film"
            root.mkdir()
            for name in ("idea.json", "dossier.json", "styles.json"):
                (root / name).write_text(json.dumps(
                    {"title": "A film", "genre": "g", "description": "d"}))
            steps = doctor.check_project(workspace.Project(root)).next_steps
        step = next(s for s in steps if s.startswith("yt script"))
        for flag in ("--polish", "--verify", "--review"):
            self.assertIn(flag, step.split())


if __name__ == "__main__":
    unittest.main()
