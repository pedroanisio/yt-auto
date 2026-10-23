"""qa.py: voice, sound and music are scored only from a current listening review (AUTO-105).

A still cannot evidence the voice, the effects or the music, and the
measurements QA was handed (the cue list, the mix settings, the narration's
numbers) barely move when the mix or a take changes, so those three scores
froze. These pin the rule that replaced them: the reviews that exist
(sound.json's listen-back, voice.json's take review) reach the reviewer marked
with what they heard and whether the film carries it; a stale one is left
out with a note; and a category no current review covers is recorded
not_evidenced with no score, which the bar reads as "READY cannot be
judged", never as a low number. No model is called and nothing renders.
"""

import argparse
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from ytstudio import harness, qa, sound, voice
from tests import test_qa_motion as motion


def _cues():
    return [{"cue_id": "M1", "kind": "music", "name": "bed", "at": 0, "seconds": 10,
             "purpose": "carry the open", "level": "-24 dBFS", "source": "freesound",
             "fade_in": 1.0, "fade_out": 2.0, "file": "sound/m1.mp3"},
            {"cue_id": "S1", "kind": "sfx", "name": "hit", "at": 3, "seconds": 1,
             "purpose": "the crash", "level": "-12 dBFS peak", "source": "freesound",
             "fade_in": 0.0, "fade_out": 0.0, "file": "sound/s1.mp3"}]


def _sound(cues=None):
    cues = cues if cues is not None else _cues()
    return {"cues": cues, "mix": {"narration": "-16 LUFS"},
            "review": {"flags": [{"cue": "M1", "problem": "the bed swamps the hook line",
                                  "severity": "minor", "fix": "duck 3 dB"}],
                       "verdict": "ready to hand to an editor", "revisions": [],
                       "unresolved": [], "heard": sound.heard(cues)}}


def _takes():
    return [{"label": "1.1", "text": "This comet is leaving the Sun.", "file": "voice/1.1.wav",
             "seconds": 2.5, "engine": "kokoro", "voice": "af"},
            {"label": "1.2", "text": "And its tail is in front.", "file": "voice/1.2.wav",
             "seconds": 2.0, "engine": "kokoro", "voice": "af"}]


def _voice(takes=None):
    takes = takes if takes is not None else _takes()
    return {"engine": "kokoro", "takes": takes,
            "review": voice.reviewed({"verdict": "ok", "retakes": [],
                                      "notes": "a steady read, the hook lands"}, takes)}


def _scores(value=9.2):
    return {k: value for k in qa.CATEGORIES}


class Evidence(unittest.TestCase):
    """Each review that exists, marked current or stale, and why."""

    def rows(self, snd, vo, **kw):
        return {r["source"]: r for r in qa.listening_evidence(snd, vo, **kw)}

    def test_reviews_that_heard_what_the_film_carries_are_evidence_with_what_they_said(self):
        rows = self.rows(_sound(), _voice())
        self.assertTrue(rows["sound"]["current"])
        self.assertEqual(rows["sound"]["covers"], ["sound_design", "music_score"])
        self.assertIn("the bed swamps the hook line", json.dumps(rows["sound"]["said"]))
        self.assertTrue(rows["voice"]["current"])
        self.assertEqual(rows["voice"]["covers"], ["voiceover"])
        self.assertIn("the hook lands", json.dumps(rows["voice"]["said"]))
        self.assertEqual(qa.audio_unheard(list(rows.values())), {})

    def test_a_listen_back_of_a_replaced_sheet_is_stale_and_says_nothing(self):
        snd = _sound()
        snd["cues"][0]["level"] = "-30 dBFS"           # a mix ride after the review
        row = self.rows(snd, _voice())["sound"]
        self.assertFalse(row["current"])
        self.assertNotIn("said", row)
        self.assertIn("stale", row["why"])
        self.assertNotEqual(row["heard"], row["now"])
        unheard = qa.audio_unheard(qa.listening_evidence(snd, _voice()))
        self.assertEqual(sorted(unheard), ["music_score", "sound_design"])
        self.assertIn("yt sound", unheard["sound_design"]["command"])
        self.assertIn("--review", unheard["sound_design"]["command"])

    def test_a_review_that_does_not_say_what_it_heard_is_not_evidence(self):
        snd = _sound()
        del snd["review"]["heard"]                     # written before AUTO-105
        row = self.rows(snd, _voice())["sound"]
        self.assertFalse(row["current"])
        self.assertIn("does not record", row["why"])

    def test_a_listen_back_that_never_answered_is_not_evidence(self):
        snd = _sound()
        snd["review"] = {"flags": [{"cue": "M1", "rule": "rule:level", "problem": "p",
                                    "severity": "important", "fix": "f"}], "verdict": "",
                         "heard": sound.heard(snd["cues"])}
        self.assertFalse(self.rows(snd, _voice())["sound"]["current"])

    def test_a_film_built_before_the_reviewed_sheet_does_not_carry_it(self):
        rows = self.rows(_sound(), _voice(), built_before={"sound"})
        self.assertFalse(rows["sound"]["current"])
        self.assertIn("build.json predates sound.json", rows["sound"]["why"])
        self.assertIn("yt build", rows["sound"]["command"])
        self.assertTrue(rows["voice"]["current"])

    def test_no_review_at_all_names_the_command_that_produces_one(self):
        unheard = qa.audio_unheard(qa.listening_evidence(None, None))
        self.assertEqual(sorted(unheard), sorted(qa.AUDIO))
        self.assertIn("yt voice", unheard["voiceover"]["command"])
        self.assertIn("yt sound", unheard["music_score"]["command"])
        self.assertIn("no sound.json", unheard["music_score"]["missing"])

    def test_every_step_of_a_remedy_names_the_project(self):
        where = "--project /films/one-take"
        rows = qa.listening_evidence(None, None, where=where) + qa.listening_evidence(
            _sound(), _voice(), built_before={"sound"}, where=where)
        commands = [r["command"] for r in rows if not r["current"]]
        self.assertEqual(len(commands), 3)
        for command in commands:
            steps = command.replace(", then ", ", ").replace(" and ", ", ").split(", ")
            self.assertGreater(len(steps), 1, command)
            for step in steps:
                self.assertTrue(step.startswith("yt "), command)
                self.assertIn(where, step, command)
        self.assertTrue(commands[-1].startswith(f"yt build {where}, then yt render"))

    def test_a_wordless_film_has_no_take_review_to_stand_in(self):
        unheard = qa.audio_unheard(qa.listening_evidence(
            _sound(), {"wordless": True, "takes": []}))
        self.assertEqual(list(unheard), ["voiceover"])

    def test_the_take_review_is_no_longer_handed_over_as_a_measurement(self):
        self.assertNotIn("review", qa.narration_measurements(_voice()))


class Unscored(unittest.TestCase):
    """No current evidence: no score, and the bar says READY cannot be judged."""

    def card(self):
        return {"scores": _scores(), "overall": 9.2, "not_evidenced": []}

    def test_without_evidence_the_three_are_unscored_and_the_bar_names_them(self):
        unheard = qa.audio_unheard(qa.listening_evidence(None, None))
        card = qa.score_unheard(self.card(), unheard)
        for key in qa.AUDIO:
            self.assertIsNone(card["scores"][key])
            self.assertIn(key, " ".join(card["not_evidenced"]))
        self.assertEqual(card["scores"]["visual_quality"], 9.2)
        self.assertEqual(sorted(card["audio_not_evidenced"]), sorted(qa.AUDIO))
        self.assertIn("yt sound", " ".join(card["not_evidenced"]))
        verdict, reasons = qa.verdict_of(card["scores"], card["overall"], blocking=[])
        self.assertEqual(verdict, qa.NOT_READY)
        for key in qa.AUDIO:
            said = [r for r in reasons if key in r]
            self.assertEqual(len(said), 1, reasons)
            self.assertIn("READY cannot be judged", said[0])
            self.assertNotIn("under", said[0])          # never a frozen number

    def test_with_current_evidence_the_scores_stand_and_gate_ready_as_before(self):
        unheard = qa.audio_unheard(qa.listening_evidence(_sound(), _voice()))
        card = qa.score_unheard(self.card(), unheard)
        self.assertEqual(card["scores"], _scores())
        self.assertEqual(card["not_evidenced"], [])
        self.assertEqual(qa.verdict_of(card["scores"], 9.2, blocking=[])[0], qa.READY)
        low = dict(card["scores"], voiceover=8.0)
        verdict, reasons = qa.verdict_of(low, 9.2, blocking=[])
        self.assertEqual(verdict, qa.NOT_READY)
        self.assertIn("voiceover 8.0 is under 8.5", reasons)

    def test_the_overall_is_the_mean_of_what_was_scored_not_the_reviewers_number(self):
        # told to leave them out, the reviewer averaged its zeros (or its old
        # frozen numbers) in anyway; neither is a measurement of anything
        unheard = qa.audio_unheard(qa.listening_evidence(None, None))
        for audio, filed in ((0.0, 6.44), (6.5, 8.39), (9.9, 9.41)):
            card = dict(self.card(), overall=filed,
                        scores=dict(_scores(), voiceover=audio, sound_design=audio,
                                    music_score=audio))
            card = qa.score_unheard(card, unheard)
            self.assertEqual(card["overall"], 9.2, (audio, filed))
            self.assertEqual(card["overall_filed"], filed)
            verdict, reasons = qa.verdict_of(card["scores"], card["overall"], blocking=[])
            self.assertFalse(any(r.startswith("overall") for r in reasons), reasons)

    def test_with_everything_heard_the_reviewers_overall_stands(self):
        card = dict(self.card(), overall=9.05)
        card = qa.score_unheard(card, {})
        self.assertEqual(card["overall"], 9.05)
        self.assertNotIn("overall_filed", card)

    def test_a_silent_films_rule_stands_beside_it(self):
        card = qa.score_silence(self.card(), motion._build(audio_tracks=0))
        unheard = qa.audio_unheard(qa.listening_evidence(None, None), silent=True)
        card = qa.score_unheard(card, unheard)
        self.assertEqual(card["scores"]["sound_design"], qa.SILENT_SCORE)
        self.assertEqual(card["scores"]["music_score"], qa.SILENT_SCORE)
        self.assertIsNone(card["scores"]["voiceover"])

    def test_an_unscored_category_is_no_regression_and_no_part_of_a_mean(self):
        before = {"pass": "pass-01", "scores": _scores(), "overall": 9.2}
        after = {"pass": "pass-02", "scores": dict(_scores(), voiceover=None), "overall": 9.3}
        diff = qa.compare(before, after)
        self.assertIsNone(diff["deltas"]["voiceover"])
        self.assertEqual(diff["regressions"], [])
        # the overalls are compared over what both passes scored: the +0.1
        # filed is not a gain in anything measured
        self.assertEqual(diff["overall_delta"], 0.0)
        self.assertEqual(diff["unscored"], ["voiceover"])
        self.assertNotIn("unscored", qa.compare(before, dict(before, overall=9.3)))
        self.assertEqual(qa._mean(dict(_scores(8.0), voiceover=None)), 8.0)


class Stage(unittest.TestCase):
    """The whole stage, with a fake renderer and a fake reviewer."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qa-listen-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / "work"
        self.root = self.work / "one-take"
        self.root.mkdir(parents=True)
        (self.root / "scene.xml").write_text("<scene/>")
        saved = harness.ENGINE, qa.RENDERER, qa.GEOMETRY
        self.addCleanup(setattr, qa, "GEOMETRY", saved[2])
        self.addCleanup(setattr, qa, "RENDERER", saved[1])
        self.addCleanup(setattr, harness, "ENGINE", saved[0])
        harness.ENGINE = motion.FakeEngine
        motion.FakeEngine.stream_calls, motion.FakeEngine.json_calls = [], []
        qa.GEOMETRY = lambda args, project, scene, shots: {"status": "skipped",
                                                          "why": "a test"}

        def render(args, scene):
            def job(j):
                out = j["out"]
                a, b = (int(v) for v in j["frames"].split(".."))
                for f in range(a, b):
                    motion._square(Path(out.replace("%04d", f"{f:04d}")), 100 + f // 2)
                return 0
            return job
        qa.RENDERER = render

    def put(self, name, data):
        (self.root / name).write_text(json.dumps(data))

    def built_last(self):
        """build.json written after the stages it read, as `yt build` does."""
        self.put("build.json", motion._build(audio_tracks=3))
        later = (self.root / "build.json").stat().st_mtime + 60
        os.utime(self.root / "build.json", (later, later))

    def args(self, **kw):
        base = dict(project=str(self.root), title=None, index=None, source=None,
                    stdin=None, genre="short", description="one take", work=str(self.work),
                    scene=None, frame_limit=4, quality="preview", reuse=None,
                    host=None, local=False, fresh=False, quiet=True, model="sonnet",
                    in_build=None, in_script=None, in_voice=None, in_sound=None,
                    in_styles=None, in_gate=None, house=None, no_house=True)
        base.update(kw)
        return argparse.Namespace(**base)

    def run_qa(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = qa.run(self.args())
        self.stdout = out.getvalue()
        self.prompt = motion.FakeEngine.stream_calls[-1]["prompt"]
        self.card = json.loads((self.root / "qa" / "pass-01" / "scorecard.json").read_text())
        self.md = (self.root / "qa" / "pass-01" / "findings.md").read_text()
        return code

    def test_current_reviews_are_in_the_brief_and_the_film_can_be_ready(self):
        self.put("sound.json", _sound())
        self.put("voice.json", _voice())
        self.built_last()
        self.assertEqual(self.run_qa(), 0)
        self.assertIn("the bed swamps the hook line", self.prompt)
        self.assertIn("a steady read, the hook lands", self.prompt)
        self.assertIn(voice.heard(_takes())["sha256"], self.prompt)   # what it heard
        self.assertEqual(self.card["scores"], _scores())
        self.assertEqual(self.card["verdict"], qa.READY)
        self.assertEqual(self.card["audio_not_evidenced"], {})
        self.assertTrue(all(r["current"] for r in self.card["listening"]))

    def test_a_stale_listen_back_is_left_out_with_a_note_and_its_categories_unscored(self):
        snd = _sound()
        snd["cues"][0]["level"] = "-30 dBFS"           # the sheet changed after the review
        self.put("sound.json", snd)
        self.put("voice.json", _voice())
        self.built_last()
        self.assertEqual(self.run_qa(), 1)
        self.assertNotIn("the bed swamps the hook line", self.prompt)
        self.assertIn("stale", self.prompt)
        self.assertIn("a steady read, the hook lands", self.prompt)
        self.assertIsNone(self.card["scores"]["sound_design"])
        self.assertIsNone(self.card["scores"]["music_score"])
        self.assertEqual(self.card["scores"]["voiceover"], 9.2)
        self.assertEqual(self.card["verdict"], qa.NOT_READY)
        self.assertTrue(any("sound_design" in r and "READY cannot be judged" in r
                            for r in self.card["bar"]), self.card["bar"])
        self.assertIn("| sound_design | not evidenced |", self.md)
        self.assertIn("yt sound", self.md)
        self.assertIn("not evidenced", self.stdout)
        record = json.loads((self.root / "qa.json").read_text())
        self.assertEqual(sorted(record["audio_not_evidenced"]), ["music_score", "sound_design"])

    def test_a_stale_take_review_does_not_reach_the_reviewer_as_a_measurement(self):
        vo = _voice()
        vo["takes"][0]["file"] = "voice/1.1-retake.wav"   # re-recorded after the review
        self.put("sound.json", _sound())
        self.put("voice.json", vo)
        self.built_last()
        self.run_qa()
        self.assertNotIn("a steady read, the hook lands", self.prompt)
        self.assertIsNone(self.card["scores"]["voiceover"])
        self.assertEqual(self.card["scores"]["sound_design"], 9.2)

    def test_a_sheet_reviewed_after_the_build_is_not_the_mix_the_film_carries(self):
        # re-spotted and re-reviewed, never rebuilt: the review is of its own
        # sheet, but the film still carries the one build.json was made from
        self.put("voice.json", _voice())
        self.built_last()
        self.put("sound.json", _sound())
        later = (self.root / "build.json").stat().st_mtime + 60
        os.utime(self.root / "sound.json", (later, later))
        self.assertEqual(self.run_qa(), 1)
        self.assertNotIn("the bed swamps the hook line", self.prompt)
        self.assertIn("build.json predates sound.json", self.prompt)
        self.assertIsNone(self.card["scores"]["sound_design"])
        self.assertIsNone(self.card["scores"]["music_score"])
        self.assertEqual(self.card["scores"]["voiceover"], 9.2)
        self.assertTrue(self.card["audio_not_evidenced"]["sound_design"]["command"]
                        .startswith("yt build"))

    def test_with_no_listening_evidence_the_three_are_not_evidenced_and_unscored(self):
        self.built_last()
        self.assertEqual(self.run_qa(), 1)
        for key in qa.AUDIO:
            self.assertIsNone(self.card["scores"][key])
        self.assertEqual(sorted(self.card["audio_not_evidenced"]), sorted(qa.AUDIO))
        # the reviewer is told which it may not score
        told = self.prompt.split("No current listening review")[1][:600]
        for key in qa.AUDIO:
            self.assertIn(key, told)
        self.assertIn("## Listening evidence", self.md)
        self.assertIn("yt voice", self.md)


if __name__ == "__main__":
    unittest.main()
