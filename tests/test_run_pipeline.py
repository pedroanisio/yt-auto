"""`yt run` walks the stages with fake runners: no model, no host.

Each stage's `run` is replaced by a fake that records when it ran and writes
the artifact that proves it did. The sound stage runs beside the keyframe and
vfx on its own thread, since only the build reads the cue sheet (AUTO-75).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from ytstudio import cli, spend, workspace  # noqa: E402
from ytstudio import (build, keyframe, props, render, research, retime, script,  # noqa: E402
                      sound, styles, vfx, voice)
from ytstudio import doctor, gate, improve, qa  # noqa: E402

MODULES = {"research": research, "styles": styles, "script": script, "props": props,
           "voice": voice, "retime": retime, "keyframe": keyframe, "vfx": vfx,
           "sound": sound, "build": build, "render": render}


class RunCase(unittest.TestCase):
    """`yt run` against fake stages: each records when it ran, writes the
    artifact that proves it did and, when `costs` prices it, a manifest that
    reports that spend (`records` adds keys to it, such as a verdict)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "film"
        self.root.mkdir()
        self.order: list[str] = []
        self.codes: dict[str, int] = {}
        self.costs: dict[str, float] = {}
        self.records: dict[str, dict] = {}
        self.hooks: dict[str, callable] = {}
        self.seen: dict[str, object] = {}
        for stage, module in MODULES.items():
            patch = mock.patch.object(module, "run", self.fake(stage))
            patch.start()
            self.addCleanup(patch.stop)
        # The delivery tail (AUTO-148): gate writes a deliverable gate.json on
        # the bytes on disk, qa a READY card, and doctor says delivery is
        # ready, unless a test says otherwise.
        self.gate_writes: str | None = "deliverable"
        self.qa_verdict = qa.READY
        self.delivered = True
        for stage, module in (("gate", gate), ("qa", qa), ("improve", improve)):
            patch = mock.patch.object(module, "run", self.tail_fake(stage))
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(doctor, "check_project", self.fake_doctor)
        patch.start()
        self.addCleanup(patch.stop)

    def digest(self, name: str) -> str:
        path = self.root / name
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""

    def gate_json(self, verdict: str = "deliverable") -> dict:
        return {"mode": "technical", "exit": 0 if verdict == "deliverable" else 1,
                "triage": {"verdict": verdict,
                           "blocking": [] if verdict == "deliverable" else ["loudness"]},
                "video_sha256": self.digest("video.mp4"),
                "scene_sha256": self.digest("scene.xml"),
                "usage": spend.record(self.costs.get("gate", 0.0), "fake", 1)}

    def qa_json(self, verdict: str) -> dict:
        return {"verdict": verdict, "pass": "pass-01", "overall": 9.3,
                "scene_sha256": self.digest("scene.xml"), "gate_evidence": "",
                "usage": spend.record(self.costs.get("qa", 0.0), "fake", 1)}

    def tail_fake(self, stage):
        def run(args):
            self.order.append(f"{stage}:start")
            self.seen[stage] = args
            if stage in self.hooks:
                self.hooks[stage]()
            if stage == "gate" and self.gate_writes:
                (self.root / "gate.json").write_text(json.dumps(self.gate_json(self.gate_writes)))
            if stage == "qa":
                (self.root / "qa.json").write_text(json.dumps(self.qa_json(self.qa_verdict)))
            self.order.append(f"{stage}:end")
            return self.codes.get(stage, 0 if stage != "gate" or self.gate_writes
                                  in (None, "deliverable") else 1)
        return run

    def fake_doctor(self, project, work=None):
        self.order.append("doctor:start")
        report = doctor.Report(project.root.name)
        missing = [] if self.delivered else ["QA pass-01 is NOT READY"]
        report.metrics["delivery"] = {"ready": self.delivered, "missing": missing}
        for problem in missing:
            report.add("fail", "delivery", problem, "yt improve --project film")
        return report

    def fake(self, stage):
        artifact = dict(workspace.STAGES)[stage]

        def run(args):
            self.order.append(f"{stage}:start")
            self.seen[stage] = args
            if stage in self.hooks:
                self.hooks[stage]()
            record = {"usage": spend.record(self.costs.get(stage, 0.0), "fake", 1),
                      **self.records.get(stage, {})}
            (self.root / workspace.ARTIFACTS[artifact]).write_text(json.dumps(record) + "\n")
            if stage in spend.MANIFEST:
                (self.root / spend.MANIFEST[stage]).write_text(json.dumps(record) + "\n")
            self.order.append(f"{stage}:end")
            return self.codes.get(stage, 0)
        return run

    def run_yt(self, *flags: str) -> int:
        args = cli.build_parser().parse_args(
            ["run", "--project", str(self.root), "--quiet", *flags])
        with redirect_stderr(io.StringIO()) as err, redirect_stdout(io.StringIO()) as out:
            code = args.run(args)
        self.err, self.out = err.getvalue(), out.getvalue()
        return code

    def started(self, stage: str) -> list[str]:
        return [s for s in self.order if s.startswith(stage + ":")]


class Beside(RunCase):
    def test_sound_starts_before_the_keyframe_returns_and_build_waits_for_it(self):
        sound_started, vfx_done = threading.Event(), threading.Event()
        seen = {}

        def in_keyframe():
            seen["sound_running"] = sound_started.wait(timeout=5)

        def in_sound():
            sound_started.set()
            # Sound takes longer than the keyframe and vfx together.
            vfx_done.wait(timeout=5)

        self.hooks.update(keyframe=in_keyframe, sound=in_sound, vfx=vfx_done.set)
        self.assertEqual(self.run_yt("--skip", "render"), 0)
        self.assertTrue(seen.get("sound_running"), self.order)
        # vfx started while sound was still running; the build waited for it.
        self.assertLess(self.order.index("vfx:start"), self.order.index("sound:end"))
        self.assertLess(self.order.index("sound:end"), self.order.index("build:start"))
        self.assertEqual(self.started("sound"), ["sound:start", "sound:end"])

    def test_a_failed_sound_stage_fails_the_run_before_build(self):
        self.codes["sound"] = 2
        self.assertEqual(self.run_yt(), 2)
        self.assertIn("sound exited 2", self.err)
        self.assertEqual(self.started("build"), [])
        self.assertEqual(self.started("vfx"), ["vfx:start", "vfx:end"])

    def test_a_sound_stage_that_crashes_beside_the_walk_is_a_failure_not_a_hang(self):
        def boom():
            raise RuntimeError("the cue sheet")
        self.hooks["sound"] = boom
        self.assertNotEqual(self.run_yt(), 0)
        self.assertIn("the cue sheet", self.err)
        self.assertEqual(self.started("build"), [])

    def test_sound_is_joined_when_the_walk_stops_early(self):
        self.codes["vfx"] = 1
        self.assertEqual(self.run_yt(), 1)
        self.assertEqual(self.started("sound"), ["sound:start", "sound:end"])

    def test_a_sound_stage_that_failed_before_the_walk_stopped_early_still_counts(self):
        # The usual fresh run: the walk stops before vfx while the look awaits
        # the operator, and the sound stage has already failed by then. Its
        # thread is no longer alive, which is no reason not to read its code.
        self.codes["sound"] = 2
        with mock.patch.object(keyframe, "approval_state", lambda root: {"state": "pending"}):
            self.assertEqual(self.run_yt(), 2)
        self.assertIn("sound exited 2", self.err)
        self.assertEqual(self.started("sound"), ["sound:start", "sound:end"])
        self.assertEqual(self.started("vfx"), [])

    def test_a_sound_failure_is_reported_beside_the_failure_that_stopped_the_walk(self):
        self.codes.update(sound=2, vfx=1)
        self.assertEqual(self.run_yt("--skip", "keyframe"), 1)
        self.assertIn("vfx exited 1", self.err)
        self.assertIn("sound exited 2", self.err)

    def test_a_ctrl_c_in_the_keyframe_ends_the_run_without_waiting_for_sound(self):
        # The interpreter waits on a non-daemon thread, so before this a
        # Ctrl-C printed the traceback and the sound stage spent on for the
        # rest of its hour. The thread is a daemon: the interrupt surfaces at
        # once, `main` prints "interrupted", and sound goes with the process.
        self.assertTrue(cli.Beside("sound", lambda: 0).daemon)
        release = threading.Event()

        def interrupted():
            raise KeyboardInterrupt

        def let_sound_finish():
            release.set()
            for thread in threading.enumerate():
                if thread.name == "yt sound":
                    thread.join(timeout=5)

        self.hooks.update(keyframe=interrupted, sound=lambda: release.wait(timeout=5))
        self.addCleanup(let_sound_finish)
        started = time.monotonic()
        with self.assertRaises(KeyboardInterrupt):
            self.run_yt("--skip", "render")
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(self.started("sound"), ["sound:start"])

    def test_a_system_exit_on_the_sound_thread_counts_with_its_code(self):
        # argparse rejecting the sound argv raises SystemExit on that thread;
        # threading drops it silently, so it is caught and its code kept.
        def usage():
            raise SystemExit(2)
        self.hooks["sound"] = usage
        self.assertEqual(self.run_yt(), 2)
        self.assertIn("sound exited 2", self.err)
        self.assertEqual(self.started("build"), [])


class SkipAndRedo(RunCase):
    def test_a_skipped_sound_stage_never_starts(self):
        self.assertEqual(self.run_yt("--skip", "sound,render"), 0)
        self.assertEqual(self.started("sound"), [])
        self.assertEqual(self.started("build"), ["build:start", "build:end"])

    def test_a_sound_sheet_already_there_is_not_redone_unless_asked(self):
        (self.root / "sound.json").write_text("{}\n")
        self.assertEqual(self.run_yt("--skip", "render"), 0)
        self.assertEqual(self.started("sound"), [])
        self.assertIn("sound: already done", self.err)
        self.order.clear()
        self.assertEqual(self.run_yt("--redo", "sound", "--skip", "render"), 0)
        self.assertEqual(self.started("sound"), ["sound:start", "sound:end"])
        self.assertEqual(self.started("vfx"), [])

    def test_the_walk_is_in_pipeline_order_with_sound_beside_the_keyframe(self):
        self.assertEqual(self.run_yt(), 0)
        serial = [s[:-6] for s in self.order if s.endswith(":start") and s != "sound:start"]
        # the delivery tail follows the render (AUTO-148); QA is READY, so
        # improve does not run
        self.assertEqual(serial, [s for s in cli.PIPELINE if s != "sound"]
                         + ["gate", "qa", "doctor"])
        self.assertLess(self.order.index("sound:start"), self.order.index("keyframe:end"))


class Tail(RunCase):
    """AUTO-148: after the render, `yt run` runs gate, then qa, then improve
    when QA is not READY, and ends on doctor's delivery verdict: exit 0 only
    when delivery is ready, cli.UNDELIVERED (4) when the tail ran and it is
    not."""

    def tail(self) -> list[str]:
        return [s[:-6] for s in self.order if s.endswith(":start")
                and s[:-6] in ("gate", "qa", "improve", "doctor")]

    def rendered(self):
        for name in ("scene.xml", "video.mp4", "dossier.json", "styles.json", "script.json",
                     "props.json", "voice.json", "retime.json", "keyframe.json", "vfx.json",
                     "sound.json"):
            (self.root / name).write_text(json.dumps({"name": name}) + "\n")

    def test_gate_then_qa_and_no_improve_when_qa_is_ready(self):
        self.assertEqual(self.run_yt(), 0)
        self.assertEqual(self.tail(), ["gate", "qa", "doctor"])
        self.assertLess(self.order.index("render:end"), self.order.index("gate:start"))
        self.assertIn("delivery", self.err)

    def test_improve_runs_when_qa_is_not_ready(self):
        self.qa_verdict = qa.NOT_READY
        self.delivered = False
        self.assertEqual(self.run_yt(), cli.UNDELIVERED)
        self.assertEqual(cli.UNDELIVERED, 4)
        self.assertEqual(self.tail(), ["gate", "qa", "improve", "doctor"])
        self.assertIn("QA pass-01 is NOT READY", self.err)
        self.assertIn("yt improve --project film", self.err)
        # improve's passes are the author's, its QA the tail's reviewer
        self.assertEqual(self.seen["improve"].model, "sonnet")
        self.assertEqual(self.seen["improve"].qa_model, vfx.REVIEWER_MODEL)
        # without a cap, improve keeps its own budget
        self.assertEqual(self.seen["improve"].budget_usd, 15.0)

    def test_the_tails_judges_are_the_reviewer_not_the_author(self):
        self.assertEqual(self.run_yt("--reviewer-model", "haiku"), 0)
        self.assertEqual((self.seen["gate"].model, self.seen["qa"].model), ("haiku", "haiku"))
        self.assertEqual(self.seen["render"].quality, "final")

    def test_a_cached_render_still_runs_the_tail(self):
        self.rendered()
        self.assertEqual(self.run_yt(), 0)
        self.assertEqual(self.started("render"), [])
        self.assertEqual(self.tail(), ["gate", "qa", "doctor"])

    def test_skip_render_runs_no_tail(self):
        self.assertEqual(self.run_yt("--skip", "render"), 0)
        self.assertEqual(self.tail(), [])

    def test_no_deliver_ends_at_render(self):
        self.delivered = False
        self.assertEqual(self.run_yt("--no-deliver"), 0)
        self.assertEqual(self.tail(), [])
        self.assertIn("render:end", self.order)
        self.assertIsNone(self.seen["render"].quality)

    def test_quick_runs_no_tail(self):
        self.delivered = False
        self.assertEqual(self.run_yt("--quick"), 0)
        self.assertEqual(self.tail(), [])
        self.assertEqual(self.seen["render"].quality, "draft")

    def test_a_keyframe_stop_or_a_failed_stage_runs_no_tail(self):
        with mock.patch.object(keyframe, "approval_state", lambda root: {"state": "pending"}):
            self.assertEqual(self.run_yt(), 0)
        self.assertEqual(self.tail(), [])
        self.order.clear()
        self.codes["render"] = 1
        self.assertEqual(self.run_yt("--redo", "render"), 1)
        self.assertEqual(self.tail(), [])

    def test_a_gate_that_wrote_nothing_stops_the_tail_with_its_code(self):
        self.gate_writes = None
        self.codes["gate"] = 1
        self.assertEqual(self.run_yt(), 1)
        self.assertEqual(self.tail(), ["gate"])
        self.assertIn("gate did not run on this render; " + gate.HOST_HINT, self.err)

    def test_a_gate_that_left_the_old_gate_json_stops_the_tail(self):
        self.rendered()
        old = self.gate_json("blocked")
        (self.root / "gate.json").write_text(json.dumps(old))
        self.gate_writes = None
        self.codes["gate"] = 1
        self.assertEqual(self.run_yt(), 1)
        self.assertEqual(self.tail(), ["gate"])
        self.assertIn("gate did not run on this render", self.err)

    def test_a_gate_that_found_it_not_deliverable_goes_on_to_qa(self):
        self.gate_writes = "blocked"
        self.qa_verdict = qa.NOT_READY
        self.delivered = False
        self.assertEqual(self.run_yt(), cli.UNDELIVERED)
        self.assertEqual(self.tail(), ["gate", "qa", "improve", "doctor"])

    def test_a_current_deliverable_gate_is_not_run_again(self):
        self.rendered()
        (self.root / "gate.json").write_text(json.dumps(self.gate_json()))
        self.assertEqual(self.run_yt(), 0)
        self.assertEqual(self.tail(), ["qa", "doctor"])

    def test_a_current_qa_is_not_run_again_unless_the_gate_ran(self):
        self.rendered()
        (self.root / "gate.json").write_text(json.dumps(self.gate_json()))
        (self.root / "qa.json").write_text(json.dumps(self.qa_json(qa.READY)))
        later = (self.root / "gate.json").stat().st_mtime + 5
        os.utime(self.root / "qa.json", (later, later))
        self.assertEqual(self.run_yt(), 0)
        self.assertEqual(self.tail(), ["doctor"])
        # a gate written in this run makes QA run again, current or not
        self.order.clear()
        (self.root / "gate.json").write_text(json.dumps(self.gate_json("blocked")))
        self.assertEqual(self.run_yt(), 0)
        self.assertEqual(self.tail(), ["gate", "qa", "doctor"])

    def test_a_qa_of_another_scene_or_with_gate_evidence_runs_again(self):
        self.rendered()
        (self.root / "gate.json").write_text(json.dumps(self.gate_json()))
        later = (self.root / "gate.json").stat().st_mtime + 5
        for card in ({**self.qa_json(qa.READY), "scene_sha256": "0" * 64},
                     {**self.qa_json(qa.READY), "gate_evidence": "no delivery gate"}):
            (self.root / "qa.json").write_text(json.dumps(card))
            os.utime(self.root / "qa.json", (later, later))
            self.order.clear()
            self.assertEqual(self.run_yt(), 0)
            self.assertEqual(self.tail(), ["qa", "doctor"], card)

    def test_a_qa_older_than_the_gate_or_the_video_runs_again(self):
        # A deliverable gate the tail does not rerun (say a `yt gate` by hand
        # after the last QA), or a video written after it: QA scored before
        # them is not reused, whatever its digest says.
        for newer in ("gate.json", "video.mp4"):
            self.rendered()
            (self.root / "gate.json").write_text(json.dumps(self.gate_json()))
            (self.root / "qa.json").write_text(json.dumps(self.qa_json(qa.READY)))
            scored = (self.root / "qa.json").stat().st_mtime
            for name in ("scene.xml", "video.mp4", "gate.json"):
                os.utime(self.root / name, (scored - 10, scored - 10))
            os.utime(self.root / newer, (scored + 5, scored + 5))
            self.order.clear()
            self.assertEqual(self.run_yt(), 0)
            self.assertEqual(self.tail(), ["qa", "doctor"], newer)

    def test_an_improve_refusal_is_returned_with_the_verdict_last(self):
        self.qa_verdict = qa.NOT_READY
        self.delivered = False
        self.codes["improve"] = 2
        self.assertEqual(self.run_yt(), 2)
        self.assertEqual(self.tail(), ["gate", "qa", "improve", "doctor"])
        self.assertIn("improve refused", self.err)
        self.assertTrue(self.err.rstrip().endswith("yt improve --project film"), self.err)


class TailVerdict(RunCase):
    """The tail against doctor's real delivery_evidence (no fake doctor):
    what the fake stages write is what doctor joins."""

    tail = Tail.tail

    def setUp(self):
        super().setUp()
        patch = mock.patch.object(doctor, "check_project", self.real_check)
        patch.start()
        self.addCleanup(patch.stop)
        self.records["build"] = {"scene": {"duration": 30.0}, "publishable": True,
                                 "findings": [], "layers": {}, "timeline": {"shots": 9},
                                 "audio_tracks": 4}
        self.render_at("final")

    real = staticmethod(doctor.check_project)

    def real_check(self, project, work=None):
        self.order.append("doctor:start")
        return TailVerdict.real(project, work)

    def render_at(self, quality: str):
        self.records["render"] = {"ok": True, "exit_code": 0, "result": {"outputs": [{
            "quality": quality, "range": [0.0, 30.0], "frames": 900, "warnings": [],
            "findings": [], "unsupported": [], "evaluation_warnings": []}]}}

    def test_a_final_render_gated_and_ready_on_the_same_bytes_exits_0(self):
        self.assertEqual(self.run_yt(), 0, self.err)
        self.assertEqual(self.tail(), ["gate", "qa", "doctor"])
        self.assertIn("doctor: deliverable", self.err)

    def test_a_render_at_another_tier_is_not_delivered(self):
        self.render_at("draft")
        self.assertEqual(self.run_yt(), cli.UNDELIVERED, self.err)
        self.assertIn("rendered at draft quality", self.err)


class TailBudget(RunCase):
    """Under --budget-usd the tail starts no stage whose estimate does not
    fit, and improve is handed what is left of the cap."""

    def setUp(self):
        super().setUp()
        prices = {"vfx": 20.0, "gate": 9.0, "qa": 9.0}

        def estimates(root, stages, sections=None, extras=None):
            return [{"stage": s, "usd": prices.get(s, 0.0), "basis": "fake", "source": "test"}
                    for s in list(stages) + ["gate", "qa"]]
        patch = mock.patch.object(spend, "stage_estimates", estimates)
        patch.start()
        self.addCleanup(patch.stop)
        self.costs.update(vfx=30.0)

    def test_a_cap_that_fits_render_but_not_qa_exits_3(self):
        self.costs["gate"] = 7.0
        self.assertEqual(self.run_yt("--budget-usd", "45"), retime.UNSETTLED)
        self.assertIn("render:end", self.order)
        self.assertEqual([s for s in self.order if s.startswith("qa:")], [])
        self.assertIn("== qa: estimated at $9.00", self.err)

    def test_a_gate_that_does_not_fit_exits_3(self):
        self.assertEqual(self.run_yt("--budget-usd", "38"), retime.UNSETTLED)
        self.assertEqual(self.started("gate"), [])
        self.assertIn("== gate: estimated at $9.00", self.err)

    def test_improve_is_handed_the_remainder(self):
        self.costs.update(gate=7.0, qa=3.0)
        self.qa_verdict = qa.NOT_READY
        self.delivered = False
        self.assertEqual(self.run_yt("--budget-usd", "60"), cli.UNDELIVERED)
        self.assertAlmostEqual(self.seen["improve"].budget_usd, 60 - 30 - 7 - 3)

    def test_no_remainder_starts_no_improve(self):
        self.costs.update(gate=5.0, qa=10.0)
        self.qa_verdict = qa.NOT_READY
        self.assertEqual(self.run_yt("--budget-usd", "45"), retime.UNSETTLED)
        self.assertEqual(self.started("improve"), [])
        self.assertIn("improve", self.err)


class FactCheckStop(RunCase):
    def test_a_script_held_by_a_fact_check_blocker_stops_the_run_before_voice(self):
        # AUTO-133: the script exits 1 with narration.txt unwritten while a
        # fact-check blocker stands; nothing after it is asked to read it,
        # and the stage's error line is what the operator reads. This re-pins
        # the run's stop on any failed stage, with the fake printing the
        # script's line; the line itself, and that the real stage exits 1
        # on it, are test_script_plan_review.FactGate's.
        line = ("error: fact-check blocker at 1.2: the bolt count is not in the research; "
                "narration.txt was not written.")
        self.hooks["script"] = lambda: print(line, file=sys.stderr)
        self.codes["script"] = 1
        self.assertEqual(self.run_yt("--skip", "sound"), 1)
        self.assertEqual(self.started("script"), ["script:start", "script:end"])
        self.assertEqual(self.started("voice"), [])
        self.assertEqual(self.started("build"), [])
        self.assertIn(line, self.err)


if __name__ == "__main__":
    unittest.main()
