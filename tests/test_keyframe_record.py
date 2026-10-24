"""The keyframe record is written after every round, and approval refuses a
record that no longer describes the files on disk (AUTO-18); the screenplay's
binding picture findings reach the author's brief (AUTO-8)."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import unittest
from pathlib import Path

from tests.test_keyframe import FakeEngine, ProjectCase, SCRIPT, KEYFRAME_DOC
from ytstudio import cli, doctor, harness, keyframe, workspace
from ytstudio.core import write_json

LOW = {"seen": "a grey box", "scores": {c: 5 for c in keyframe.CATEGORIES},
       "findings": [{"category": "visual_quality", "problem": "flat", "fix": "light it",
                     "severity": "major"}], "score": 5, "summary": "a blockout"}


class Stopped(KeyboardInterrupt):
    pass


class StoppingEngine(FakeEngine):
    """Authors as the fake does, but the operator stops the run at author call
    number `stop_at` (1-based, counted over the writing calls), after the file
    was written."""

    stop_at = 0
    authored = 0
    revised_doc = ""

    def stream(self, prompt, system=None, resume=False):
        writing = "Write" in tuple(self.config.tools or ())
        if writing:
            StoppingEngine.authored += 1
            if StoppingEngine.authored > 1 and StoppingEngine.revised_doc:
                FakeEngine.doc = StoppingEngine.revised_doc
        stop = writing and StoppingEngine.authored == StoppingEngine.stop_at
        for event in super().stream(prompt, system, resume):
            yield event
            if stop and event.type == "assistant":
                raise Stopped()


class Record(ProjectCase):
    def setUp(self):
        super().setUp()
        harness.ENGINE = StoppingEngine
        StoppingEngine.stop_at, StoppingEngine.authored = 0, 0
        StoppingEngine.revised_doc = ""

    def stop_at(self, n, **kw):
        StoppingEngine.stop_at, StoppingEngine.authored = n, 0
        with self.assertRaises(Stopped):
            self.run_stage(**kw)

    def test_the_record_is_on_disk_after_every_round(self):
        FakeEngine.reviews = [copy.deepcopy(LOW)]
        self.stop_at(2)                      # round 0 gated, the revision stopped
        record = keyframe.load_record(self.root)
        self.assertEqual([r["round"] for r in record["rounds"]], [0])
        self.assertFalse(record["finished"])
        self.assertEqual(record["score"], 5)
        self.assertTrue(record["document_digest"])
        self.assertTrue(record["program_digest"])

    def test_a_finished_run_says_so_and_still_approves(self):
        record = self.run_stage()
        self.assertTrue(record["finished"])
        self.assertTrue(keyframe.load_record(self.root)["finished"])
        self.assertEqual(keyframe.approve(self.root)["approval"]["state"], "approved")

    def test_a_run_stopped_after_a_round_leaves_a_consistent_record(self):
        FakeEngine.reviews = [copy.deepcopy(LOW)]
        StoppingEngine.revised_doc = KEYFRAME_DOC.replace("intensity=\"3\"", "intensity=\"6\"")
        self.stop_at(2)
        # the revision rewrote the files after the last record: refused
        with self.assertRaises(keyframe.KeyframeError):
            keyframe.approve(self.root)

    def test_stopping_a_second_run_does_not_leave_a_mix_approvable(self):
        self.run_stage()                                      # run 1, whole
        first = keyframe.load_record(self.root)
        FakeEngine.doc = KEYFRAME_DOC.replace("intensity=\"3\"", "intensity=\"5\"")
        self.stop_at(1, fresh=True)                           # run 2 stopped while authoring
        record = keyframe.load_record(self.root)
        self.assertEqual(record["document_digest"], first["document_digest"])   # run 1's record
        with self.assertRaises(keyframe.KeyframeError) as ctx:
            keyframe.approve(self.root)
        self.assertIn("changed after the record", str(ctx.exception))
        self.assertNotEqual(keyframe.load_record(self.root)["approval"]["state"], "approved")

    def test_a_changed_program_is_refused(self):
        self.run_stage()
        program = self.root / "keyframe" / keyframe.PROGRAM_NAME
        program.write_text(program.read_text() + "\n# edited\n")
        with self.assertRaises(keyframe.KeyframeError):
            keyframe.approve(self.root)

    def test_a_record_without_digests_still_approves(self):
        self.run_stage()
        record = keyframe.load_record(self.root)
        for key in ("document_digest", "program_digest", "finished"):
            record.pop(key, None)
        write_json(keyframe.record_path(self.root), record)
        self.assertEqual(keyframe.approve(self.root)["approval"]["state"], "approved")


class FallbackPalette(ProjectCase):
    """A look authored on design.py's house fallback palette cannot be
    approved (AUTO-155): every film that falls back has the same colours.
    The refusal is apart from approvable(), so `yt run` does not author the
    look again and again while styles.json is what needs fixing."""

    def test_a_fallback_look_is_refused(self):
        (self.root / "styles.json").unlink()
        record = self.run_stage()
        self.assertEqual(record["palette_source"], "fallback")
        self.assertEqual(keyframe.load_record(self.root)["palette_source"], "fallback")
        with self.assertRaises(keyframe.KeyframeError) as ctx:
            keyframe.approve(self.root)
        self.assertIn("yt styles --rich", str(ctx.exception))
        self.assertIn("fallback palette", str(ctx.exception))
        self.assertNotEqual(keyframe.load_record(self.root)["approval"]["state"], "approved")
        sheet = (self.root / "keyframe.md").read_text(encoding="utf-8")
        self.assertNotIn("--approve", sheet)
        self.assertIn("yt styles --rich", sheet)
        args = cli.build_parser().parse_args(
            ["keyframe", "--project", str(self.root), "--approve", "--quiet"])
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(args.run(args), 1)
        self.assertIn("yt styles --rich", err.getvalue())

    def test_doctor_names_the_styles_fix_not_the_approval(self):
        (self.root / "styles.json").unlink()
        self.run_stage()
        rows = [f for f in doctor.check_project(workspace.Project(self.root)).findings
                if f.area == "keyframe"]
        self.assertEqual(len(rows), 1, rows)
        self.assertIn("fallback palette", rows[0].message)
        # palette_source is frozen in keyframe.json and `yt keyframe` keeps a
        # pending look, so the fix names the fresh look as well
        self.assertEqual(rows[0].fix, f"yt styles --project {self.root} --rich, then "
                                      f"yt keyframe --project {self.root} --fresh")

    def test_doctor_vfx_warning_names_the_styles_fix_not_the_approval(self):
        (self.root / "styles.json").unlink()
        self.run_stage()
        write_json(self.root / "vfx.json", {"authored": [{"n": 1, "file": "x"}]})
        rows = [f for f in doctor.check_project(workspace.Project(self.root)).findings
                if f.area == "vfx" and "without an approved keyframe" in f.message]
        self.assertEqual(len(rows), 1, rows)
        self.assertNotIn("--approve", rows[0].fix)
        self.assertTrue(rows[0].fix.startswith(
            f"yt styles --project {self.root} --rich, then "
            f"yt keyframe --project {self.root} --fresh, then "), rows[0].fix)

    def test_a_partial_palette_still_approves(self):
        # STYLES names five hexes; the roles it leaves open are filled from
        # the fallback, but the palette is the film's own
        record = self.run_stage()
        self.assertEqual(record["palette_source"], "styles.json")
        self.assertEqual(keyframe.palette_refusal(record), "")
        self.assertEqual(keyframe.approve(self.root)["approval"]["state"], "approved")

    def test_an_old_record_without_palette_source_approves(self):
        (self.root / "styles.json").unlink()
        self.run_stage()
        record = keyframe.load_record(self.root)
        record.pop("palette_source")
        write_json(keyframe.record_path(self.root), record)
        self.assertEqual(keyframe.approve(self.root)["approval"]["state"], "approved")

    def test_look_needs_redo_ignores_the_palette(self):
        (self.root / "keyframe").mkdir(exist_ok=True)
        (self.root / "keyframe" / "keyframe.scene.xml").write_text(KEYFRAME_DOC)
        record = {"source": "section", "section": 1, "document": "keyframe/keyframe.scene.xml",
                  "frame": "keyframe/keyframe.png", "errors": [], "findings": [],
                  "palette_source": "fallback",
                  "approval": {"state": "pending", "notes": "", "digest": "", "at": ""}}
        write_json(keyframe.record_path(self.root), record)
        self.assertEqual(keyframe.approvable(record), "")
        self.assertTrue(keyframe.palette_refusal(record))
        self.assertFalse(keyframe.look_needs_redo(self.root))


class PictureFindings(ProjectCase):
    def test_a_binding_picture_flag_reaches_the_authors_brief(self):
        script = copy.deepcopy(SCRIPT)
        script["check"] = {"flags": [
            {"n": 0, "severity": "blocker", "problem": "the vessel is shown with two sails",
             "fix": "show one sail"},
            {"n": 0, "severity": "nit", "problem": "cosmetic wording quibble", "fix": "-"}]}
        write_json(self.root / "script.json", script)
        self.run_stage()
        brief = (self.root / "keyframe" / keyframe.BRIEF_NAME).read_text()
        self.assertIn("Fact-check constraints", brief)
        self.assertIn("the vessel is shown with two sails", brief)
        self.assertIn("show one sail", brief)
        self.assertNotIn("cosmetic wording quibble", brief)

    def test_no_flags_no_section(self):
        self.run_stage()
        brief = (self.root / "keyframe" / keyframe.BRIEF_NAME).read_text()
        self.assertNotIn("Fact-check constraints", brief)


if __name__ == "__main__":
    unittest.main()
