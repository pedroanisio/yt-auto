"""render.summarise: the lines read out of an encode report.

The engine reports an output's loudness as flat numbers (`loudness` in
LUFS, `true_peak`, `loudness_range`, `short_term_max`); an older shape
nested them under `loudness`. Both are read, a silent output has no
loudness line, and the engine's warnings are shown, not dropped.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import render  # noqa: E402


def report(**output) -> dict:
    base = {"files": ["video.mp4"], "fps": 30.0, "frames": 478, "findings": []}
    base.update(output)
    return {"ok": True, "result": {"outputs": [base]}}


class Summary(unittest.TestCase):
    def test_flat_loudness_numbers_are_read(self):
        lines = render.summarise(report(loudness=-14.41, true_peak=-1.0087,
                                        loudness_range=3.7, short_term_max=-13.9))
        self.assertEqual(len(lines), 1)
        self.assertIn("-14.4 LUFS", lines[0])
        self.assertIn("peak -1.0 dBTP", lines[0])
        self.assertIn("range 3.7 LU", lines[0])

    def test_the_nested_shape_is_still_read(self):
        lines = render.summarise(report(loudness={"integrated": -16.2, "true_peak": -2.0}))
        self.assertIn("-16.2 LUFS", lines[0])
        self.assertIn("peak -2.0 dBTP", lines[0])

    def test_a_silent_output_has_no_loudness(self):
        lines = render.summarise(report(loudness=None, true_peak=None))
        self.assertNotIn("LUFS", lines[0])
        self.assertNotIn("dBTP", lines[0])
        self.assertIn("video.mp4", lines[0])

    def test_engine_warnings_are_shown_once(self):
        lines = render.summarise(report(
            loudness=None,
            evaluation_warnings=[{"code": "E12", "message": "include declares <styles>"}],
            warnings=["X-OFF-CAMERA: object3D never inside the camera's view",
                      "X-OFF-CAMERA: object3D never inside the camera's view"],
            findings=[{"code": "X-OFF-CAMERA", "severity": "warning",
                       "message": "object3D never inside the camera's view"}]))
        joined = "\n".join(lines)
        self.assertIn("[warning] E12: include declares <styles>", joined)
        self.assertEqual(joined.count("never inside the camera's view"), 1)

    def test_loudness_of_reads_either_shape(self):
        self.assertEqual(render.loudness_of({"loudness": -14.0, "true_peak": -1.0}),
                         {"integrated": -14.0, "true_peak": -1.0})
        self.assertEqual(render.loudness_of({"loudness": {"integrated": -15.0}}),
                         {"integrated": -15.0})
        self.assertEqual(render.loudness_of({"loudness": None}), {})
        self.assertEqual(render.loudness_of({}), {})


if __name__ == "__main__":
    unittest.main()
