"""The keyframe record is written after every round, and approval refuses a
record that no longer describes the files on disk (AUTO-18); the screenplay's
binding picture findings reach the author's brief (AUTO-8)."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from tests.test_keyframe import FakeEngine, ProjectCase, SCRIPT, KEYFRAME_DOC
from ytstudio import harness, keyframe
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
