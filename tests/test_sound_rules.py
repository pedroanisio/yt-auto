"""The cue sheet's rule findings (AUTO-83): what can be checked from the
timecodes is checked before the model is asked to listen back.

Each rule is pinned on a small sheet; the flags are shaped like the review's
so `revise` can answer them, and a kept one reaches the build's findings.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import build, sound  # noqa: E402


def cue(kind, cue_id, at, seconds, level="-20 dBFS"):
    # every cue fades out: a bed without one is its own rule (rule:fade)
    return sound.Cue(kind=kind, name=cue_id, at=at, seconds=seconds,
                     purpose="p", level=level, cue_id=cue_id, fade_out=1.0)


def rules(cues, runtime=60):
    return sound.rule_findings(cues, runtime)


def by_rule(flags, rule):
    return [f for f in flags if f["rule"] == rule]


class Shape(unittest.TestCase):
    def test_a_rule_flag_is_a_review_flag_with_its_rule_named(self):
        flags = rules([cue("music", "M1", 0, 30), cue("music", "M2", 20, 10)])
        self.assertEqual(len(flags), 1)
        flag = flags[0]
        required = sound.REVIEW_SCHEMA["properties"]["flags"]["items"]["required"]
        for key in required:
            self.assertIn(key, flag)
        self.assertEqual(flag["rule"], "rule:overlap")
        self.assertTrue(flag["problem"].startswith("rule:overlap: "))
        self.assertIn(flag["severity"], sound.REVISABLE)
        self.assertIn(flag["rule"], sound.RULES)

    def test_every_rule_is_in_the_set_and_named_by_the_docs(self):
        # one sheet breaks every rule; the docs name each one the build may warn about
        sheet = [cue("music", "M1", 0, 30), cue("music", "M2", 20, 20, level=""),
                 cue("sfx", "S1", 61, 1)]
        sheet.append(sound.Cue(kind="ambience", name="A1", at=0, seconds=60,
                               purpose="p", level="-40 dBFS", cue_id="A1"))
        found = {f["rule"] for f in rules(sheet)}
        self.assertEqual(found, set(sound.RULES))
        root = Path(__file__).resolve().parent.parent
        for doc in ("README.md", "ytstudio/SKILL.md"):
            text = (root / doc).read_text(encoding="utf-8")
            for rule in sound.RULES:
                self.assertTrue(f"`{rule}`" in text, f"{doc} does not name {rule}")

    def test_a_clean_sheet_has_no_findings(self):
        sheet = [cue("ambience", "A1", 0, 60), cue("music", "M1", 0, 30),
                 cue("sfx", "S1", 10, 1), cue("silence", "Q1", 40, 3)]
        self.assertEqual(rules(sheet), [])


class Overlap(unittest.TestCase):
    def test_two_cues_of_one_kind_that_overlap_flag_the_later_one(self):
        flags = by_rule(rules([cue("music", "M1", 0, 30), cue("music", "M2", 20, 10)]),
                        "rule:overlap")
        self.assertEqual([f["cue"] for f in flags], ["M2"])
        self.assertIn("M1", flags[0]["problem"])
        self.assertIn("0:20", flags[0]["problem"])

    def test_different_kinds_may_overlap(self):
        sheet = [cue("music", "M1", 0, 30), cue("ambience", "A1", 0, 60),
                 cue("sfx", "S1", 5, 1), cue("silence", "Q1", 10, 2)]
        self.assertEqual(by_rule(rules(sheet), "rule:overlap"), [])

    def test_a_cue_that_starts_where_the_last_one_ends_is_not_an_overlap(self):
        sheet = [cue("music", "M1", 0, 30), cue("music", "M2", 30, 10)]
        self.assertEqual(by_rule(rules(sheet), "rule:overlap"), [])

    def test_a_silence_over_a_silence_is_an_overlap(self):
        sheet = [cue("silence", "Q1", 10, 5), cue("silence", "Q2", 12, 5)]
        self.assertEqual([f["cue"] for f in by_rule(rules(sheet), "rule:overlap")],
                         ["Q2"])


class Level(unittest.TestCase):
    def test_a_silence_with_no_level_is_flagged(self):
        flags = by_rule(rules([cue("silence", "Q1", 10, 5, level="")]), "rule:level")
        self.assertEqual([f["cue"] for f in flags], ["Q1"])
        self.assertIn("level", flags[0]["fix"])

    def test_any_cue_with_no_level_is_flagged(self):
        sheet = [cue("music", "M1", 0, 30, level=""), cue("sfx", "S1", 5, 1, level="   ")]
        self.assertEqual([f["cue"] for f in by_rule(rules(sheet), "rule:level")],
                         ["M1", "S1"])

    def test_a_level_in_words_is_still_a_level_here(self):
        # words-only levels are the build's finding, not the sheet's rule
        self.assertEqual(by_rule(rules([cue("music", "M1", 0, 30, level="quiet")]),
                                 "rule:level"), [])


class Clock(unittest.TestCase):
    def test_a_cue_starting_after_the_film_ends_is_a_blocker(self):
        flags = by_rule(rules([cue("sfx", "S1", 61, 1), cue("music", "M1", 0, 30)]),
                        "rule:clock")
        self.assertEqual([(f["cue"], f["severity"]) for f in flags], [("S1", "blocker")])
        self.assertIn("1:00", flags[0]["problem"])
        # revise answers fix or keep; the suggestion offers nothing else
        self.assertNotIn("drop", flags[0]["fix"])
        self.assertIn("keep", flags[0]["fix"])

    def test_a_cue_running_past_the_end_is_important(self):
        flags = by_rule(rules([cue("music", "M1", 50, 20)]), "rule:clock")
        self.assertEqual([(f["cue"], f["severity"]) for f in flags], [("M1", "important")])

    def test_a_cue_ending_on_the_last_second_is_fine(self):
        self.assertEqual(by_rule(rules([cue("music", "M1", 30, 30)]), "rule:clock"), [])


class NoBed(unittest.TestCase):
    """A transient is what `build.transient_shift` calls one: sfx, stinger or
    transition of two seconds or less. With nothing sustained under it, the
    limiter takes the loudness the master was lifted to."""

    def test_a_transient_with_nothing_under_it_is_flagged(self):
        sheet = [cue("music", "M1", 0, 10), cue("sfx", "S1", 20, 1)]
        flags = by_rule(rules(sheet), "rule:no-bed")
        self.assertEqual([f["cue"] for f in flags], ["S1"])
        self.assertIn("0:20", flags[0]["problem"])

    def test_a_transient_under_a_bed_is_not(self):
        sheet = [cue("ambience", "A1", 0, 60), cue("stinger", "X1", 20, 1)]
        self.assertEqual(by_rule(rules(sheet), "rule:no-bed"), [])

    def test_a_long_effect_counts_as_a_bed_and_is_not_a_transient(self):
        sheet = [cue("sfx", "S1", 10, 8), cue("sfx", "S2", 12, 1)]
        self.assertEqual(by_rule(rules(sheet), "rule:no-bed"), [])

    def test_the_classification_is_the_builds(self):
        long = build.TRANSIENT_SECONDS + 1
        sheet = [cue("transition", "T1", 20, long)]
        self.assertEqual(by_rule(rules(sheet), "rule:no-bed"), [])
        sheet = [cue("transition", "T1", 20, build.TRANSIENT_SECONDS)]
        self.assertEqual([f["cue"] for f in by_rule(rules(sheet), "rule:no-bed")], ["T1"])

    def test_a_silence_is_not_a_bed(self):
        sheet = [cue("silence", "Q1", 0, 60), cue("sfx", "S1", 20, 1)]
        self.assertEqual([f["cue"] for f in by_rule(rules(sheet), "rule:no-bed")], ["S1"])


class ReachesTheBuild(unittest.TestCase):
    """A rule flag the listen-back kept is a warning in build.json naming the cue."""

    def setUp(self):
        from tests.test_build_audio import _idea, _sections, _wav
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "vfx").mkdir()
        (self.root / "vfx" / "open.scene.xml").write_text("<scene/>")
        _wav(self.root / "sound" / "pad.wav", 12)
        self.vfx = {"scene": {"by_label": {
            label: {"file": "vfx/open.scene.xml", "symbol": "fx-all"}
            for label in ("1.1", "1.2", "2.1")}}}
        self.idea, self.sections = _idea(), _sections()

    def tearDown(self):
        self._tmp.cleanup()

    def sound(self, unresolved):
        return {"runtime": 10,
                "cues": [{"cue_id": "M1", "kind": "music", "at": 0, "seconds": 6,
                          "level": "-30 dBFS", "description": "a pad",
                          "file": "sound/pad.wav", "fade_out": 1}],
                "review": {"flags": [], "verdict": "no", "unresolved": unresolved}}

    def build(self, data, **kw):
        kw.setdefault("verbose", False)
        return build.SceneBuilder(self.idea, self.sections, self.root, vfx=self.vfx,
                                  sound=data, **kw).build()[1]

    def test_a_kept_rule_flag_is_a_warning_naming_the_cues(self):
        manifest = self.build(self.sound([
            {"cue": "M8", "rule": "rule:overlap", "severity": "important",
             "problem": "rule:overlap: M8 plays over M7", "fix": "move it",
             "kept": "the overlap is the handover"},
            {"cue": "Q9", "rule": "rule:level", "severity": "important",
             "problem": "rule:level: no level", "fix": "say how far it drops",
             "kept": "still flagged after 2 round(s) of revision; left as it is"}]))
        found = [f for f in manifest["findings"] if "rule:" in f]
        self.assertEqual(len(found), 1, manifest["findings"])
        self.assertTrue(found[0].startswith("warning:"))
        for text in ("M8", "rule:overlap", "the overlap is the handover",
                     "Q9", "rule:level"):
            self.assertIn(text, found[0])

    def test_a_model_flag_left_standing_is_not_a_rule_warning(self):
        manifest = self.build(self.sound([
            {"cue": "M1", "severity": "important", "problem": "too long",
             "fix": "shorten", "kept": "left as it is"}]))
        self.assertEqual([f for f in manifest["findings"] if "rule:" in f], [])

    def test_no_audio_says_nothing_about_the_sheet(self):
        manifest = self.build(self.sound([
            {"cue": "M8", "rule": "rule:overlap", "severity": "important",
             "problem": "rule:overlap: x", "fix": "y", "kept": "z"}]), audio=False)
        self.assertEqual([f for f in manifest["findings"] if "rule:" in f], [])


if __name__ == "__main__":
    unittest.main()
