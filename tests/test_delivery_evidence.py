"""Delivery evidence is judged against the scene the video was rendered from.

`yt qa` renders its frames from scene.xml, not from video.mp4, so a rebuild
after the final render followed by `yt qa` gives a READY pass for a video of
the previous scene. Doctor judges a gate or QA pass stale against the newer of
video.mp4 and scene.xml (the rule `yt improve` uses), a video older than
scene.xml is a delivery fail, and a QA pass that recorded the digest of the
scene it judged is compared by digest where it can be.
"""

from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_delivery import DeliveryCase  # noqa: E402
from ytstudio import doctor, qa  # noqa: E402

SCENE_1, SCENE_2 = b"<scene v=1/>", b"<scene v=2/>"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class SceneEvidence(DeliveryCase):
    def built(self, data: bytes = SCENE_1) -> None:
        self.write("scene.xml", data)
        self.stamp("scene.xml")

    def scored_scene(self, data: bytes | None = SCENE_1) -> None:
        """A READY pass; with `data`, one that recorded the digest it judged."""
        body = {"pass": "pass-03", "verdict": "READY TO PUBLISH", "overall": 9.2}
        if data is not None:
            body["scene_sha256"] = digest(data)
        self.write("qa.json", body)
        self.stamp("qa.json")

    def never_ready(self) -> None:
        report = self.report()
        self.assertFalse(report.metrics["delivery"]["ready"])
        self.assertFalse([f for f in report.findings
                          if f.area == "delivery" and f.level == "ok"])

    def is_ready(self) -> bool:
        return bool(self.report().metrics["delivery"]["ready"])


class Rebuilt(SceneEvidence):
    def test_a_scene_rebuilt_after_the_final_render_is_a_delivery_fail(self):
        self.rendered(); self.built(); self.gated(); self.scored_scene()
        fails = self.fails_about("scene.xml")
        self.assertEqual(len(fails), 1)
        self.assertIn("video", fails[0].message)
        self.assertTrue(fails[0].fix.startswith("yt render"))
        self.never_ready()

    def test_the_scene_before_the_render_is_ready(self):
        self.built(); self.rendered(); self.gated(); self.scored_scene()
        self.assertEqual(self.delivery("fail"), [])
        self.assertTrue(self.is_ready())


class Mtime(SceneEvidence):
    """A gate or a QA pass without a digest is stale against the newer of
    video.mp4 and scene.xml."""

    def test_a_qa_pass_older_than_the_scene_is_stale(self):
        self.built(); self.rendered(); self.gated(); self.scored_scene(None)
        self.assertTrue(self.is_ready())
        self.stamp("scene.xml")
        self.assertTrue(self.fails_about("QA pass-03 predates"))
        self.never_ready()

    def test_a_gate_older_than_the_scene_is_stale(self):
        self.built(); self.rendered(); self.gated(); self.scored_scene(None)
        self.stamp("scene.xml")
        self.assertTrue(self.fails_about("gate predates"))
        self.never_ready()

    def test_a_pass_after_both_is_not_stale(self):
        self.built(); self.rendered(); self.gated(); self.scored_scene(None)
        self.assertTrue(self.is_ready())


class Digest(SceneEvidence):
    def test_a_pass_that_judged_another_scene_is_stale_whatever_the_times(self):
        self.built(SCENE_2); self.rendered(); self.gated()
        self.scored_scene(SCENE_1)             # the newest file, but of the older scene
        fails = self.fails_about("QA pass-03")
        self.assertEqual(len(fails), 1)
        self.assertIn("scene", fails[0].message)
        self.never_ready()

    def test_a_pass_of_this_scene_is_kept_when_the_scene_was_only_touched(self):
        self.built(SCENE_1); self.rendered(); self.gated(); self.scored_scene(SCENE_1)
        self.assertTrue(self.is_ready())
        self.stamp("scene.xml")       # same bytes, newer mtime: the gate is what goes stale
        self.assertFalse(self.fails_about("QA pass-03"))

    def test_a_digest_pass_older_than_the_video_is_stale(self):
        self.built(); self.scored_scene(); self.rendered(); self.gated()
        self.assertTrue(self.fails_about("QA pass-03 predates"))

    def test_the_digest_is_the_sha256_of_the_scene_bytes(self):
        self.assertEqual(qa.scene_digest(SCENE_1), digest(SCENE_1))
        self.assertEqual(doctor.scene_digest(self.root / "missing.xml"), "")
        self.built()
        self.assertEqual(doctor.scene_digest(self.root / "scene.xml"), digest(SCENE_1))


class JudgeCalibration(SceneEvidence):
    """AUTO-156: whether the QA judge was fixture-calibrated is recorded in
    qa.json and gates nothing: doctor's delivery result is the same."""

    def scored_with(self, label: dict | None) -> None:
        body = {"pass": "pass-03", "verdict": "READY TO PUBLISH", "overall": 9.2,
                "scene_sha256": digest(SCENE_1)}
        if label is not None:
            body["judge_calibration"] = label
        self.write("qa.json", body)
        self.stamp("qa.json")

    def outcome(self):
        report = self.report()
        return (report.metrics["delivery"],
                sorted((f.level, f.message) for f in report.findings if f.area == "delivery"))

    def test_the_delivery_result_is_the_same_calibrated_or_not(self):
        self.built(); self.rendered(); self.gated()
        results = []
        for fixtures in (None, False, True):
            self.scored_with(None if fixtures is None else {
                "fixtures": fixtures, "labelled": 0, "model": "sonnet", "digest": "d",
                "why": "no report"})
            results.append(self.outcome())
        self.assertTrue(results[0][0]["ready"])
        self.assertEqual(results[1], results[0])
        self.assertEqual(results[2], results[0])

    def test_doctor_never_reads_it(self):
        source = Path(doctor.__file__).read_text(encoding="utf-8")
        self.assertNotIn("judge_calibration", source)


if __name__ == "__main__":
    unittest.main()
