"""A silent shot is a mark in the review listing, never narration. Measured: a
polish or revise pass handed "[picture only, no narration]" back as a shot's
line, the voice stage sent it to ElevenLabs, which strips bracketed text and
refused the empty input (HTTP 400), and the whole voice stage stopped."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import script  # noqa: E402


class SilentShots(unittest.TestCase):
    DATA = {"sections": [{"heading": "S", "shots": [
        {"n": 1, "label": "1.1", "narration": "A line that is spoken."},
        {"n": 2, "label": "1.2", "narration": script.SILENT},
        {"n": 3, "label": "1.3", "narration": "  [no narration]  "},
        {"n": 4, "label": "1.4", "narration": "[a] spoken [b]"}]}]}

    def test_reading_a_screenplay_turns_the_mark_into_silence(self):
        shots = script.sections_of(self.DATA)[0].shots
        self.assertEqual([s.narration for s in shots],
                         ["A line that is spoken.", "", "", "[a] spoken [b]"])

    def test_the_listing_still_marks_silent_shots(self):
        shots = script.sections_of(self.DATA)[0].shots
        self.assertIn(f"2. [1.2 ", script.shot_listing(shots))
        self.assertIn(script.SILENT, script.shot_listing(shots))

    def test_spoken_keeps_real_words(self):
        self.assertEqual(script.spoken("  two   spaces "), "two spaces")
        self.assertEqual(script.spoken("[just a direction]"), "")


if __name__ == "__main__":
    unittest.main()
