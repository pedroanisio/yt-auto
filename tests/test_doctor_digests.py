"""Doctor judges gate.json by the bytes it judged, and names a vfx section
carried without a review of its current inputs (AUTO-114).

The gate used to be stale whenever gate.json was older than video.mp4 or
scene.xml, so a copy, a checkout or a touch that changed no byte made it
stale, and a replaced file with an older mtime did not. `yt gate` now
records `video_sha256` and `scene_sha256`, and doctor compares those where
they are present, as it does QA's `scene_sha256`. A gate.json without them
is judged by mtime, as before.
"""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_delivery import DeliveryCase  # noqa: E402
from ytstudio import doctor  # noqa: E402

SCENE_1, SCENE_2 = b"<scene v=1/>", b"<scene v=2/>"
VIDEO = b"\x00" * 1024                 # what DeliveryCase.rendered writes


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Sealed(DeliveryCase):
    """A built scene and a gate that recorded the digests it judged."""

    def built(self, data: bytes = SCENE_1) -> None:
        self.write("scene.xml", data)
        self.stamp("scene.xml")

    def sealed(self, video: bytes = VIDEO, scene: bytes = SCENE_1) -> None:
        """A deliverable gate that recorded the digests of what it judged."""
        self.write("gate.json", {"mode": "technical", "exit": 0,
                                 "triage": {"verdict": "deliverable", "blocking": []},
                                 "video_sha256": digest(video), "scene_sha256": digest(scene)})
        self.stamp("gate.json")


class GateDigests(Sealed):
    def gate_fails(self):
        return [f for f in self.delivery("fail") if "gate" in f.message]

    def test_a_touched_video_keeps_a_gate_that_judged_its_bytes(self):
        self.built(); self.rendered(); self.sealed(); self.scored()
        self.stamp("video.mp4")                  # newer, same bytes
        self.assertFalse(self.fails_about("gate predates"))
        self.assertEqual(self.gate_fails(), [])

    def test_a_touched_scene_keeps_it_too(self):
        self.built(); self.rendered(); self.sealed(); self.scored()
        self.stamp("scene.xml")
        self.assertEqual(self.gate_fails(), [])

    def test_a_replaced_video_is_flagged_whatever_the_times(self):
        self.built(); self.rendered(); self.sealed(video=b"another cut")
        self.scored()
        fails = self.gate_fails()
        self.assertEqual(len(fails), 1)
        self.assertIn("video.mp4", fails[0].message)
        self.assertTrue(fails[0].fix.startswith("yt gate"))
        self.assertFalse(self.report().metrics["delivery"]["ready"])

    def test_a_replaced_scene_is_flagged_whatever_the_times(self):
        self.built(SCENE_2); self.rendered(); self.sealed(scene=SCENE_1); self.scored()
        fails = self.gate_fails()
        self.assertEqual(len(fails), 1)
        self.assertIn("scene.xml", fails[0].message)
        self.assertTrue(fails[0].fix.startswith("yt gate"))

    def test_a_gate_without_digests_is_judged_by_mtime_as_before(self):
        self.built(); self.rendered(); self.gated(); self.scored()
        self.assertEqual(self.gate_fails(), [])
        self.stamp("video.mp4")
        fails = self.gate_fails()
        self.assertEqual([f.message for f in fails],
                         ["the gate predates the render it should judge"])


class GateProblem(Sealed):
    """AUTO-148: doctor's gate rows are one function, doctor.gate_problem,
    which QA reads too; its strings are doctor's own, case by case."""

    def problem(self, gate):
        return doctor.gate_problem(gate, gate_file=self.root / "gate.json",
                                   video=self.root / "video.mp4",
                                   scene=self.root / "scene.xml")

    def gate(self, **over):
        data = {"mode": "technical", "exit": 0,
                "triage": {"verdict": "deliverable", "blocking": []},
                "video_sha256": digest(VIDEO), "scene_sha256": digest(SCENE_1)} | over
        data = {k: v for k, v in data.items() if v is not None}
        self.write("gate.json", data)
        self.stamp("gate.json")
        return data

    def test_each_case_is_doctors_own_string(self):
        self.built(); self.rendered()
        self.assertEqual(self.problem(None), "no delivery gate has run on this render")
        self.assertEqual(self.problem(self.gate(mode="frames")),
                         "the last gate checked decoded frames only; delivery needs "
                         "the technical gate")
        self.assertEqual(self.problem(self.gate(video_sha256=digest(b"another"))),
                         "the gate judged a different video.mp4 than the one now rendered")
        self.assertEqual(self.problem(self.gate(scene_sha256=digest(SCENE_2))),
                         "the gate judged a different scene.xml than the one now built")
        self.assertEqual(self.problem(self.gate(exit=1, triage={
            "verdict": "not deliverable", "blocking": [{"what": "x"}]})),
            "the gate found it not deliverable (1 blocking)")
        self.assertEqual(self.problem(self.gate(triage={"verdict": "not deliverable",
                                                        "blocking": []})),
                         "the gate found it not deliverable (0 blocking)")
        self.assertEqual(self.problem(self.gate()), "")

    def test_a_gate_without_digests_older_than_the_video_predates_it(self):
        self.built(); self.rendered()
        old = self.gate(video_sha256=None, scene_sha256=None)
        self.assertEqual(self.problem(old), "")
        self.stamp("video.mp4")
        self.assertEqual(self.problem(old), "the gate predates the render it should judge")

    def test_a_scene_digest_with_no_video_file_holds(self):
        # standalone QA beside a scene with no video.mp4: the video check
        # is skipped, the scene digest is still read
        self.built()
        self.assertEqual(self.problem(self.gate()), "")
        self.assertEqual(self.problem(self.gate(scene_sha256=digest(SCENE_2))),
                         "the gate judged a different scene.xml than the one now built")


class StoryProblem(Sealed):
    """AUTO-148: a screenplay its story reviewer rejected, or could not
    judge, is not delivered: delivery_evidence lists it."""

    def scripted(self, review):
        self.write("script.json", {"sections": [], **({"review": review} if review else {})})

    def story_rows(self):
        return [m for m in self.report().metrics["delivery"]["missing"]
                if "story review" in m]

    def test_a_reject_quotes_the_first_unresolved_problem(self):
        self.built(); self.rendered(); self.sealed(); self.scored()
        self.scripted({"verdict": "reject", "grades": {"train": "Poor"},
                       "unresolved": [{"problem": "the train never leaves"}]})
        rows = self.story_rows()
        self.assertEqual(rows, ["story review rejected the screenplay (the train never "
                                "leaves)"])
        self.assertFalse(self.report().metrics["delivery"]["ready"])
        fails = [f for f in self.delivery("fail") if "story review" in f.message]
        self.assertIn("yt script", fails[0].fix)
        self.assertIn("--fresh --review", fails[0].fix)

    def test_a_reject_without_unresolved_names_the_weak_grades(self):
        self.scripted({"verdict": "reject", "grades": {"train": "Poor", "hook": "Fair",
                                                       "voice": "Good"}})
        self.assertEqual(doctor.story_problem(json.loads(
            (self.root / "script.json").read_text())),
            "story review rejected the screenplay (train Poor, hook Fair)")
        self.assertEqual(doctor.story_problem({"review": {"verdict": "reject"}}),
                         "story review rejected the screenplay (see script.md)")

    def test_not_evidenced_is_listed(self):
        self.built(); self.rendered(); self.sealed(); self.scored()
        self.scripted({"verdict": "not_evidenced"})
        self.assertEqual(self.story_rows(), [
            "story review could not judge the screenplay (not_evidenced: fewer than "
            "two valid samples)"])

    def test_a_passing_or_absent_review_lists_nothing(self):
        # packaged: delivery needs a current, ready upload set (AUTO-147)
        self.built(); self.rendered(); self.sealed(); self.scored(); self.packaged()
        for review in ({"verdict": "consider"}, {"verdict": "recommend"}, None):
            self.scripted(review)
            self.assertEqual(self.story_rows(), [], review)
        self.assertTrue(self.report().metrics["delivery"]["ready"])


class UnreviewedSections(DeliveryCase):
    """vfx.json `authored` naming a section carried without a review of its
    current inputs is a vfx warning whose fix authors it again."""

    def vfx(self, authored: list[dict]) -> None:
        self.write("vfx.json", {"totals": {"count": 2, "hours": 1, "templates": {}},
                                "plan": {}, "authored": authored})

    def warnings(self):
        return [f for f in self.report().findings
                if f.area == "vfx" and f.level == "warn" and "review" in f.message]

    def test_a_carried_unreviewed_section_names_its_command(self):
        self.vfx([{"n": 1, "score": 8, "findings": []},
                  {"n": 2, "score": None, "reviewed": False, "carried_from": "v01",
                   "inherited": {"score": 8, "from_version": "v01"},
                   "findings": ["warning: carried: section 2's inputs changed since its review; "
                                "it was not reviewed against them"]}])
        warned = self.warnings()
        self.assertEqual(len(warned), 1)
        self.assertIn("section 2", warned[0].message)
        self.assertIn("yt vfx", warned[0].fix)
        self.assertTrue(warned[0].fix.endswith("--sections 2"))

    def test_an_inherited_record_alone_is_enough(self):
        self.vfx([{"n": 3, "score": None, "inherited": {"score": 7}, "findings": []}])
        self.assertTrue(self.warnings()[0].fix.endswith("--sections 3"))

    def test_the_approved_look_is_a_note_that_does_not_send_it_back_as_lost(self):
        # never scored, so no review was lost; re-authoring it is the operator's call
        self.vfx([{"n": 1, "look": True, "score": None, "reviewed": False,
                   "inherited": {"score": None, "review": None}, "findings": []}])
        self.assertEqual(self.warnings(), [])
        notes = [f for f in self.report().findings
                 if f.area == "vfx" and f.level == "note" and "approved look" in f.message]
        self.assertEqual(len(notes), 1)
        self.assertIn("yt keyframe --fresh", notes[0].fix)

    def test_records_without_the_fields_give_no_warning(self):
        self.vfx([{"n": 1, "score": 8, "findings": []}, {"n": 2, "score": 7, "findings": []}])
        self.assertEqual(self.warnings(), [])


if __name__ == "__main__":
    unittest.main()
