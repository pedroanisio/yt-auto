"""qa.sample: a still where each narrated line starts (AUTO-104).

A midpoint still misses an event that happens elsewhere in its shot: on the
reported film shot VII's still sat at 38.9 s while the refusal line played
39.3-41.7, so every pass said "the refusal is not on screen". The narration
says when each line lands, and the picture is meant to show it then. These
pin the line stills: one at the start of each sentence of a take, placed on
the film by build.json's shot `at`, merged into its shot's sample when
within half a second of it, the payoff, re-hook and reveal lines kept first
under a tight --frames, none for a wordless film, and the line in the brief
beside its still.
"""

import argparse
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ytstudio import harness, qa
from tests import test_qa_motion as motion
from tests.test_qa import STORY


def _words(*spans):
    """A take's word timings from (text, start, end) triples, take-relative."""
    return [{"text": w, "start": a, "end": b} for w, a, b in spans]


def _take(label, words, seconds=None):
    return {"label": label, "text": " ".join(w["text"] for w in words),
            "seconds": seconds if seconds is not None else words[-1]["end"],
            "words": words, "timing": "exact"}


def _refusal():
    """A shot like the reported film's VII: 36.1-41.7 s, midpoint 38.9; its
    take opens on one sentence and the refusal starts 3.6 s in, at 39.7 s
    (the reported 39.3 s is within half a second of the midpoint, so it
    merges into that still: see the half-second test below)."""
    build = {"scene": {"fps": 30, "duration": 41.7},
             "shots": [{"label": "VI", "at": 30.0, "seconds": 6.1, "section": "Fall"},
                       {"label": "VII", "at": 36.1, "seconds": 5.6, "section": "Fall"}]}
    voice = {"takes": [_take("VII", _words(
        ("The", 0.05, 0.2), ("envoys", 0.2, 0.7), ("arrived.", 0.7, 1.4),
        ("He", 3.6, 3.8), ("refused", 3.8, 4.4), ("the", 4.4, 4.5),
        ("crown.", 4.5, 5.6)))]}
    return build, voice


class LineStills(unittest.TestCase):
    def test_a_line_after_the_midpoint_gets_its_own_still_at_its_start(self):
        build, voice = _refusal()
        rows = [r for r in qa.sample(build, voice=voice) if r["label"] == "VII"]
        mid = next(r for r in rows if r["t"] == 38.9)
        self.assertNotIn("line:", mid["why"])
        line = next(r for r in rows if r["t"] == 39.7)
        self.assertEqual(line["why"], "line: He refused the crown.")
        self.assertEqual(line["lines"], [{"at": 39.7, "text": "He refused the crown."}])
        # the line's own burst, kept inside its shot like any other still
        self.assertTrue(36.1 <= line["burst"]["times"][0] < line["burst"]["times"][-1] < 41.7)
        self.assertNotEqual(line["frame"], mid["frame"])

    def test_a_line_opening_its_shot_is_sampled_inside_the_shot_not_on_the_cut(self):
        # The first word is said at the cut; the still is half a burst inside.
        build, voice = _refusal()
        rows = [r for r in qa.sample(build, voice=voice) if r["label"] == "VII"]
        opening = next(r for r in rows if "The envoys arrived." in r["why"])
        self.assertEqual(opening["t"], round(36.1 + qa.BURST_SECONDS / 2, 3))
        self.assertEqual(opening["lines"][0]["at"], 36.15)

    def test_a_line_within_half_a_second_of_the_midpoint_adds_nothing(self):
        build = {"scene": {"fps": 30},
                 "shots": [{"label": "1", "at": 10.0, "seconds": 4.0}]}
        voice = {"takes": [_take("1", _words(("Look", 1.7, 2.0), ("here.", 2.0, 3.9)),
                                 seconds=4.0)]}
        rows = qa.sample(build, voice=voice)
        self.assertEqual([r["t"] for r in rows], [12.0])
        why = rows[0]["why"].split("; ")
        self.assertEqual(why[:2], ["hook", "ending"])
        self.assertEqual(why[-1], "line: Look here.")
        self.assertEqual(rows[0]["lines"], [{"at": 11.7, "text": "Look here."}])

    def test_the_label_is_the_lines_first_words(self):
        build = {"shots": [{"label": "1", "at": 0.0, "seconds": 9.0}]}
        long = _words(*[(w, n * 0.3 + 6.0, n * 0.3 + 6.3) for n, w in enumerate(
            "one two three four five six seven eight.".split())])
        rows = qa.sample(build, voice={"takes": [_take("1", long, seconds=9.0)]})
        line = next(r for r in rows if r["t"] == 6.0)
        self.assertEqual(line["why"], "line: one two three four five ...")
        self.assertEqual(line["lines"][0]["text"], "one two three four five six seven eight.")

    def test_a_take_without_word_times_is_split_by_its_length(self):
        build = {"shots": [{"label": "1", "at": 0.0, "seconds": 8.0}]}
        voice = {"takes": [{"label": "1", "text": "Short. And then a much longer one.",
                            "seconds": 8.0}]}
        rows = qa.sample(build, voice=voice)
        second = [r for r in rows if "And then a much" in r["why"]]
        self.assertEqual(len(second), 1)
        self.assertTrue(0.5 < second[0]["t"] < 3.5, second[0])

    def test_a_payoff_line_survives_a_tight_limit_ahead_of_an_ordinary_line(self):
        build = {"scene": {"fps": 30}, "shots": [
            {"label": "1", "at": 0.0, "seconds": 6.0, "function": "context"},
            {"label": "2", "at": 6.0, "seconds": 6.0, "function": "payoff"},
            {"label": "3", "at": 12.0, "seconds": 6.0, "function": "context"}]}
        late = lambda: _words(("Then", 4.5, 4.8), ("this.", 4.8, 5.8))  # noqa: E731
        voice = {"takes": [_take(label, late(), seconds=6.0) for label in "123"]}
        everything = qa.sample(build, voice=voice)
        self.assertEqual(len(everything), 6)        # three midpoints, three lines
        rows = qa.sample(build, voice=voice, limit=4)
        lines = [r for r in rows if r.get("lines")]
        self.assertEqual([(r["label"], r["t"]) for r in lines], [("2", 10.5)])
        self.assertEqual(sorted(r["label"] for r in rows), ["1", "2", "2", "3"])

    def test_the_shots_come_before_any_line_under_a_tight_limit(self):
        build, voice = _refusal()
        rows = qa.sample(build, voice=voice, limit=2)
        self.assertEqual(sorted(r["label"] for r in rows), ["VI", "VII"])
        self.assertFalse(any(r.get("lines") for r in rows))

    def test_a_wordless_film_samples_no_lines(self):
        build, _ = _refusal()
        plain = qa.sample(build)
        self.assertEqual(qa.sample(build, voice={"wordless": True, "takes": []}), plain)
        self.assertEqual(qa.sample(build, voice={}), plain)
        self.assertEqual(qa.sample(build, voice=None), plain)
        self.assertFalse(any("line:" in r["why"] for r in plain))

    def test_a_line_outside_its_shot_is_not_sampled(self):
        # build.json says where the shot is; a word timed past its end is not in it
        build = {"shots": [{"label": "1", "at": 0.0, "seconds": 3.0},
                           {"label": "2", "at": 3.0, "seconds": 3.0}]}
        voice = {"takes": [_take("1", _words(("Here.", 0.1, 0.5), ("Gone.", 3.5, 4.0)))]}
        rows = qa.sample(build, voice=voice)
        self.assertFalse(any("Gone." in r["why"] for r in rows))

    def test_evenly_spaced_stills_keep_clear_of_line_stills(self):
        build, voice = _refusal()
        rows = qa.sample(build, voice=voice, limit=12)
        self.assertEqual(len(rows), 12)
        times = sorted(r["t"] for r in rows)
        spread = [r for r in rows if r["why"] == qa.SPREAD_WHY]
        for r in spread:
            others = [t for t in times if t != r["t"]]
            self.assertTrue(all(abs(r["t"] - t) >= qa.SPREAD_GAP - 1e-6 for t in others), r)

    def test_a_shots_per_shot_findings_stay_on_its_midpoint_still(self):
        # The opening line's still is earlier than the midpoint; what is
        # recorded per shot (off-camera ids) is still filed on the midpoint.
        build, voice = _refusal()
        rows = qa.sample(build, voice=voice)
        found = qa.mechanical_findings(rows, diagnostics={"VII": {"off_camera": ["leg"]}})
        off = [f for f in found if f["kind"] == "off_camera"]
        self.assertEqual([f["timestamp"] for f in off], [38.9])


class LineBrief(unittest.TestCase):
    """The whole stage: the line beside the still it was sampled for."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qa-lines-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / "work"
        self.root = self.work / "one-take"
        self.root.mkdir(parents=True)
        (self.root / "scene.xml").write_text("<scene/>")
        build = motion._build()                      # 1.4 is 11.0-16.5 s
        (self.root / "build.json").write_text(json.dumps(build))
        (self.root / "voice.json").write_text(json.dumps({"takes": [_take("1.4", _words(
            ("It", 0.1, 0.3), ("bends.", 0.3, 1.0),
            ("That", 4.0, 4.3), ("is", 4.3, 4.5), ("the", 4.5, 4.6), ("wind.", 4.6, 5.4)),
            seconds=5.5)]}))
        (self.root / "script.json").write_text(json.dumps({"sections": [
            {"heading": "One take", "shots": [
                {"label": "1.4", "narration": "It bends. That is the wind."}]}]}))
        saved = harness.ENGINE, qa.RENDERER
        self.addCleanup(setattr, qa, "RENDERER", saved[1])
        self.addCleanup(setattr, harness, "ENGINE", saved[0])
        seen = self.seen = {"prompts": []}

        class Reader(motion.FakeEngine):
            def stream(self_, prompt, system=None):
                from claude_engine import Event
                seen["prompts"].append(prompt)
                card = dict(motion.SCORECARD, story=STORY)
                yield Event(type="assistant", text="```json\n" + json.dumps(card) + "\n```")
                yield Event(type="result", raw={})

        harness.ENGINE = Reader

        def render(args, scene):
            def job(j):
                out = j["out"]
                a, b = (int(v) for v in j["frames"].split(".."))
                for f in range(a, b):
                    motion._square(Path(out.replace("%04d", f"{f:04d}")), 100 + f // 2)
                return 0
            return job
        qa.RENDERER = render

    def args(self, **kw):
        base = dict(project=str(self.root), title=None, index=None, source=None,
                    stdin=None, genre="short", description="one take", work=str(self.work),
                    scene=None, frame_limit=None, quality="preview", reuse=None,
                    host=None, local=False, fresh=False, quiet=True, model="sonnet",
                    in_build=None, in_script=None, in_voice=None, in_sound=None,
                    in_styles=None, in_gate=None, house=None, no_house=True)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_the_brief_shows_the_line_beside_its_still(self):
        with contextlib.redirect_stdout(io.StringIO()):
            qa.run(self.args())
        samples = json.loads((self.root / "qa" / "pass-01" / "samples.json").read_text())
        line = next(r for r in samples if r["t"] == 15.0)     # 11.0 + 4.0
        self.assertEqual(line["why"], "line: That is the wind.")
        frame = "\"frame\": \"%s\"" % line["frame"]
        prompt = self.seen["prompts"][0]
        start = prompt.index(frame)
        row = prompt[start:prompt.index("}", prompt.index("\"lines\"", start))]
        self.assertIn("\"why_sampled\": \"line: That is the wind.\"", row)
        self.assertIn("\"text\": \"That is the wind.\"", row)
        self.assertIn("\"at\": 15.0", row)
        self.assertIn("at the moment it is said", prompt)


class Docs(unittest.TestCase):
    """The manual, the README, production.md, the module and --frames say it."""

    ROOT = Path(__file__).resolve().parent.parent

    def flat(self, rel):
        return " ".join((self.ROOT / rel).read_text(encoding="utf-8").split())

    def test_every_page_names_the_line_stills(self):
        for rel in ("ytstudio/SKILL.md", "README.md", "docs/production.md"):
            text = self.flat(rel)
            self.assertIn("`line: <first words>`", text, rel)
            self.assertIn("AUTO-104", text, rel)
        for rel in ("ytstudio/SKILL.md", "README.md"):
            self.assertIn("`qa.KEY_LINES`", self.flat(rel), rel)
        doc = " ".join(qa.__doc__.split())
        self.assertIn("`line: <first words>`", doc)
        self.assertIn("KEY_LINES", doc)

    def test_the_frames_help_counts_the_line_stills(self):
        parser = argparse.ArgumentParser()
        qa.register(parser.add_subparsers())
        sub = parser._subparsers._group_actions[0].choices["qa"]
        frames = next(a for a in sub._actions if a.dest == "frame_limit")
        self.assertIn("line", frames.help)


if __name__ == "__main__":
    unittest.main()
