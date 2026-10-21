"""Research is sized to the film's target runtime, not to a ten-minute film.

A 60-second script once sat on 193 claims and 126 sources. The plan now
shrinks questions, claims and shots with the idea's target length; a ten-minute
film (or an idea that names no length) plans exactly as it always did, and an
explicit flag still wins.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_engine import Usage  # noqa: E402
from ytstudio import harness, research  # noqa: E402
from ytstudio.core import Idea  # noqa: E402

NOTES = [{"question": "q", "notes": "n", "visited": []}]


class FakeEngine:
    calls: list[str] = []

    def __init__(self, config):
        self.usage = Usage()

    def ask_json(self, prompt, schema, system=None):
        FakeEngine.calls.append(prompt)
        return {name: [] for name in schema.get("required", [])}


def idea(length="", genre="explainer"):
    return Idea(title="Comets", genre=genre, description="What a tail is.",
                length=length)


class Parse(unittest.TestCase):
    def test_lengths_people_write(self):
        for text, seconds in (("60 seconds", 60), ("90s", 90), ("1 min", 60),
                              ("10 minutes", 600), ("8-12 min", 600),
                              ("10:00", 600), ("1 hour", 3600),
                              ("about 2 minutes", 120)):
            self.assertEqual(research.runtime_seconds(text), seconds, text)

    def test_no_length_is_unknown(self):
        for text in ("", "short", None):
            self.assertIsNone(research.runtime_seconds(text), text)


class Plan(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for target, name in ((research, "ClaudeEngine"), (harness, "ENGINE")):
            p = mock.patch.object(target, name, FakeEngine)
            p.start()
            self.addCleanup(p.stop)
        FakeEngine.calls = []

    def researcher(self, which, **kw):
        root = Path(self._tmp.name)
        return research.Researcher(
            which, research.Dossier(root, which, at=root), verbose=False, **kw)

    def at(self, minutes):
        return self.researcher(idea(str(minutes) + " min"))

    def test_a_minute_plans_at_most_three_questions(self):
        r = self.researcher(idea("60 seconds"))
        self.assertTrue(2 <= r.depth <= 3, r.depth)
        r.questions()
        self.assertIn("Give exactly " + str(r.depth) + " questions",
                      FakeEngine.calls[-1])

    def test_a_minute_asks_for_about_forty_claims(self):
        self.researcher(idea("60 seconds")).claims(NOTES)
        self.assertIn("about 40 claims", FakeEngine.calls[-1])

    def test_a_minute_plans_six_to_eight_shots(self):
        r = self.researcher(idea("60 seconds"))
        self.assertTrue(6 <= r.shot_count <= 8, r.shot_count)
        r.shots([])
        self.assertIn(str(r.shot_count) + " visuals", FakeEngine.calls[-1])

    def test_ten_minutes_is_unchanged(self):
        for length in ("10 minutes", "", "12 min"):
            r = self.researcher(idea(length))
            self.assertEqual(r.depth, research.DEFAULT_DEPTH, length)
            self.assertEqual(r.shot_count, 14, length)
            r.claims(NOTES)
            self.assertTrue(FakeEngine.calls[-1].endswith(
                research.SCOPES["explainer"]["claims"]), length)

    def test_scale_is_monotonic(self):
        depths = [self.at(m).depth for m in (1, 3, 6, 10)]
        shots = [self.at(m).shot_count for m in (1, 3, 6, 10)]
        self.assertEqual(depths, sorted(depths))
        self.assertEqual(shots, sorted(shots))

    def test_explicit_flags_win(self):
        r = self.researcher(idea("60 seconds"), depth=7, shot_count=20)
        self.assertEqual((r.depth, r.shot_count), (7, 20))

    def test_a_wordless_minute_stays_in_its_scope(self):
        r = self.researcher(idea("60 seconds", genre="cinematic"))
        self.assertLessEqual(r.depth, 3)
        self.assertEqual(r.scope["name"], "cinematic")

    def test_the_shots_flag_defaults_to_unset(self):
        p = argparse.ArgumentParser()
        research.register(p.add_subparsers())
        self.assertIsNone(p.parse_args(["research"]).shots)


if __name__ == "__main__":
    unittest.main()
