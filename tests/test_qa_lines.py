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


def _reported():
    """The reported film's VII exactly: 36.1-41.7 s, midpoint 38.9 s, and the
    refusal line said 3.2 s into its take, at 39.3 s: 0.4 s after the
    midpoint, past the edge of that still's burst (38.67-39.13 s at 30 fps)."""
    build, _ = _refusal()
    voice = {"takes": [_take("VII", _words(
        ("He", 3.2, 3.4), ("refused", 3.4, 4.0), ("the", 4.0, 4.1),
        ("crown.", 4.1, 5.6)), seconds=5.6)]}
    return build, voice


def _late(seconds=6.0, at=4.5):
    """A take whose one line starts well after its shot's midpoint."""
    return _words(("Then", at, at + 0.3), ("this.", at + 0.3, at + 1.3))


class LineStills(unittest.TestCase):
    def test_a_line_after_the_midpoint_gets_its_own_still_at_its_start(self):
        build, voice = _refusal()
        rows = [r for r in qa.sample(build, voice=voice) if r["label"] == "VII"]
        mid = next(r for r in rows if r["t"] == 38.9)
        self.assertNotIn("line:", mid["why"])
        line = next(r for r in rows if r["t"] == 39.7)
        self.assertEqual(line["why"], "line: He refused the crown.")
        self.assertEqual(line["lines"], [{"at": 39.7, "text": "He refused the crown.",
                                          "frame": line["frame"]}])
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

    def test_the_reported_line_gets_a_still_whose_burst_holds_its_start(self):
        # 39.3 s is within half a second of the 38.9 s midpoint, but no frame
        # of that still's burst is at or after it: merged there, the reviewer
        # would be asked about a moment it cannot see.
        build, voice = _reported()
        rows = [r for r in qa.sample(build, voice=voice) if r["label"] == "VII"]
        mid = next(r for r in rows if r["t"] == 38.9)
        self.assertNotIn("lines", mid)
        self.assertNotIn(round(39.3 * 30), mid["burst"]["frames"])
        line = next(r for r in rows if r.get("lines"))
        self.assertEqual(line["t"], 39.3)
        self.assertIn(round(39.3 * 30), line["burst"]["frames"])
        self.assertEqual(line["lines"], [{"at": 39.3, "text": "He refused the crown.",
                                          "frame": line["frame"]}])

    def test_a_line_inside_the_burst_of_its_shots_still_adds_nothing(self):
        build = {"scene": {"fps": 30},
                 "shots": [{"label": "1", "at": 10.0, "seconds": 4.0}]}
        voice = {"takes": [_take("1", _words(("Look", 2.1, 2.4), ("here.", 2.4, 3.9)),
                                 seconds=4.0)]}
        rows = qa.sample(build, voice=voice)
        self.assertEqual([r["t"] for r in rows], [12.0])
        why = rows[0]["why"].split("; ")
        self.assertEqual(why[:2], ["hook", "ending"])
        self.assertEqual(why[-1], "line: Look here.")
        # the frame that shows the line's start is the burst's, named beside it
        stem = rows[0]["frame"][:-4]
        self.assertEqual(rows[0]["lines"], [{"at": 12.1, "text": "Look here.",
                                             "frame": f"burst/{stem}_0363.png"}])
        self.assertIn(f"burst/{stem}_0363.png", rows[0]["burst"]["files"])

    def test_a_line_within_half_a_second_but_outside_the_burst_gets_its_own_still(self):
        build = {"scene": {"fps": 30},
                 "shots": [{"label": "1", "at": 10.0, "seconds": 4.0}]}
        voice = {"takes": [_take("1", _words(("Look", 1.7, 2.0), ("here.", 2.0, 3.9)),
                                 seconds=4.0)]}
        rows = qa.sample(build, voice=voice)
        self.assertEqual([r["t"] for r in rows], [11.7, 12.0])
        self.assertEqual(rows[0]["why"], "line: Look here.")
        self.assertNotIn("lines", rows[1])

    def test_a_line_never_merges_into_another_shots_still(self):
        # An insert nested in a long shot: its line starts inside the long
        # shot's burst, but it is the insert's line, so it takes a still in
        # the insert.
        build = {"scene": {"fps": 30}, "shots": [
            {"label": "A", "at": 0.0, "seconds": 10.0},
            {"label": "B", "at": 4.9, "seconds": 1.0}]}
        voice = {"takes": [_take("B", _words(("Now.", 0.2, 0.8)), seconds=1.0)]}
        rows = qa.sample(build, voice=voice)
        a = next(r for r in rows if r["label"] == "A")
        self.assertIn(round(5.1 * 30), a["burst"]["frames"])
        self.assertNotIn("lines", a)
        line = next(r for r in rows if r.get("lines"))
        self.assertEqual(line["label"], "B")
        self.assertIn(round(5.1 * 30), line["burst"]["frames"])
        # and across a cut: 0.3 s into the next shot, 0.4 s from the last
        # shot's still, the line is still sampled in its own shot
        build = {"scene": {"fps": 30}, "shots": [
            {"label": "A", "at": 1.8, "seconds": 0.2},
            {"label": "B", "at": 2.0, "seconds": 4.0}]}
        voice = {"takes": [_take("B", _words(("Now.", 0.3, 0.8)), seconds=4.0)]}
        line = next(r for r in qa.sample(build, voice=voice) if r.get("lines"))
        self.assertEqual((line["label"], line["t"]), ("B", 2.3))

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

    def test_payoff_then_rehook_then_reveal_lines_win_the_one_spare_still(self):
        def film(*functions):
            shots = [{"label": str(n), "at": 6.0 * n, "seconds": 6.0, "function": f}
                     for n, f in enumerate(("context",) + functions)]
            voice = {"takes": [_take(s["label"], _late(), seconds=6.0) for s in shots]}
            rows = qa.sample({"scene": {"fps": 30}, "shots": shots}, voice=voice,
                             limit=len(shots) + 1)
            return [r["label"] for r in rows if r.get("lines")]
        self.assertEqual(film("reveal", "rehook", "payoff"), ["3"])
        self.assertEqual(film("reveal", "rehook"), ["2"])
        self.assertEqual(film("reveal"), ["1"])

    def test_without_frames_the_line_stills_stop_at_every_shot_up_to(self):
        n = qa.EVERY_SHOT_UP_TO * 2 // 3
        shots = [{"label": f"S{k}", "at": 6.0 * k, "seconds": 6.0} for k in range(n)]
        voice = {"takes": [_take(s["label"], _late(), seconds=6.0) for s in shots]}
        build = {"scene": {"fps": 30}, "shots": shots}
        rows = qa.sample(build, voice=voice)
        self.assertEqual(len(rows), qa.EVERY_SHOT_UP_TO)
        self.assertEqual(qa.unsampled(build, rows), [])
        dropped = qa.lines_unsampled(build, voice, rows)
        self.assertEqual(len(dropped), 2 * n - qa.EVERY_SHOT_UP_TO)

    def test_the_spare_stills_go_to_lines_spread_over_the_film(self):
        # 50 shots of 3 s, each take opening on the cut and saying a second
        # line 0.7 s after its midpoint: 100 lines, 10 spare stills. The
        # lines inside a shot come before the ones its cut opens on, and
        # they are spread over the runtime, not taken from its start.
        shots = [{"label": f"S{k}", "at": 3.0 * k, "seconds": 3.0} for k in range(50)]
        voice = {"takes": [_take(s["label"], _words(
            ("Here", 0.1, 0.4), ("it", 0.4, 0.6), ("is.", 0.6, 0.9),
            ("Then", 2.2, 2.5), ("this.", 2.5, 2.9)), seconds=3.0) for s in shots]}
        build = {"scene": {"fps": 30}, "shots": shots}
        rows = qa.sample(build, voice=voice)
        lines = [r for r in rows if r.get("lines")]
        self.assertEqual(len(lines), qa.EVERY_SHOT_UP_TO - 50)
        self.assertTrue(all(r["why"] == "line: Then this." for r in lines), lines)
        times = sorted(r["t"] for r in lines)
        self.assertLess(times[0], 150 * 0.15)
        self.assertGreater(times[-1], 150 * 0.85)
        self.assertTrue(all(b - a > 6.0 for a, b in zip(times, times[1:])), times)
        dropped = qa.lines_unsampled(build, voice, rows)
        self.assertEqual(len(dropped), 100 - len(lines))
        self.assertEqual(dropped[0], {"shot": "S0", "at": 0.1, "text": "Here it is."})

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
        # the line stills count against --frames before the evenly spaced ones
        self.assertEqual(sorted(r["t"] for r in rows if r.get("lines")), [36.35, 39.7])
        spread = [r for r in rows if r["why"] == qa.SPREAD_WHY]
        self.assertEqual(len(spread), 12 - 4)
        for r in spread:
            others = [t for t in times if t != r["t"]]
            self.assertTrue(all(abs(r["t"] - t) >= qa.SPREAD_GAP - 1e-6 for t in others), r)

    def test_a_label_voiced_twice_is_its_last_take_as_build_places_it(self):
        # build.timeline keys the takes by label, the last one winning: an
        # earlier take of the same label is not on the soundtrack.
        build = {"shots": [{"label": "1", "at": 0.0, "seconds": 8.0}]}
        twice = _take("1", _words(("One.", 0.1, 0.5), ("Two.", 5.0, 5.5)), seconds=8.0)
        lines = qa.narrated_lines(build, {"takes": [twice, twice]})
        self.assertEqual([ln["text"] for ln in lines], ["One.", "Two."])
        old = _take("1", _words(("Discarded.", 6.0, 7.0)), seconds=8.0)
        lines = qa.narrated_lines(build, {"takes": [old, twice]})
        self.assertEqual([ln["text"] for ln in lines], ["One.", "Two."])
        rows = qa.sample(build, voice={"takes": [twice, twice]})
        self.assertEqual(sum(len(r.get("lines", [])) for r in rows), 2)

    def test_a_title_or_an_initialism_does_not_end_a_line(self):
        build = {"shots": [{"label": "1", "at": 0.0, "seconds": 9.0}]}
        take = _take("1", _words(
            ("Dr.", 0.1, 0.4), ("Lee", 0.4, 0.7), ("left", 0.7, 1.0), ("the", 1.0, 1.1),
            ("U.S.", 1.1, 1.6), ("today.", 1.6, 2.0), ("Mr.", 5.0, 5.3), ("Ng", 5.3, 5.6),
            ("stayed.", 5.6, 6.0)), seconds=9.0)
        lines = qa.narrated_lines(build, {"takes": [take]})
        self.assertEqual([ln["text"] for ln in lines],
                         ["Dr. Lee left the U.S. today.", "Mr. Ng stayed."])

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
        self.assertIn("\"frame\": \"%s\"" % line["lines"][0]["frame"], row)
        self.assertIn("at the moment it is said", prompt)
        self.assertIn("the burst frame", prompt)
        card = json.loads((self.root / "qa" / "pass-01" / "scorecard.json").read_text())
        self.assertEqual(card["lines_unsampled"], [])
        self.assertFalse(any(n.startswith("lines:") for n in card["not_evidenced"]))

    def test_a_tight_frames_names_the_lines_it_left_without_a_still(self):
        # Four shots, --frames 4: every shot keeps its still and neither line
        # gets one; the reviewer is told before it scores, and the scorecard
        # names them, so "the line is on screen" is not read as checked.
        with contextlib.redirect_stdout(io.StringIO()):
            qa.run(self.args(frame_limit=4))
        pass_dir = self.root / "qa" / "pass-01"
        card = json.loads((pass_dir / "scorecard.json").read_text())
        self.assertEqual(card["lines_unsampled"], [
            {"shot": "1.4", "at": 11.1, "text": "It bends."},
            {"shot": "1.4", "at": 15.0, "text": "That is the wind."}])
        note = next(n for n in card["not_evidenced"] if n.startswith("lines:"))
        self.assertIn("2 of 2 narrated lines", note)
        self.assertIn("--frames 4", note)
        self.assertIn("1.4 at 00:15.000", note)
        self.assertIn("2 of the 2 narrated lines", self.seen["prompts"][0])


class LinesNote(unittest.TestCase):
    def test_the_note_counts_and_names_the_lines_left_without_a_still(self):
        dropped = [{"shot": "VII", "at": 39.3, "text": "He refused the crown."}]
        card = qa.note_lines_unsampled({"not_evidenced": ["x"]}, dropped, total=3,
                                       limit=None, stills=qa.EVERY_SHOT_UP_TO)
        self.assertEqual(card["lines_unsampled"], dropped)
        self.assertEqual(card["not_evidenced"][0], "x")
        self.assertIn("1 of 3 narrated lines", card["not_evidenced"][1])
        self.assertIn(f"past the default of {qa.EVERY_SHOT_UP_TO} stills",
                      card["not_evidenced"][1])
        self.assertIn("VII at 00:39.300", card["not_evidenced"][1])
        reused = qa.note_lines_unsampled({}, dropped, total=3, limit=None,
                                         stills=4, reused=True)
        self.assertIn("in the reused pass", reused["not_evidenced"][0])

    def test_a_long_list_is_cut_short_in_the_note_but_kept_whole(self):
        dropped = [{"shot": str(k), "at": float(k), "text": "x."} for k in range(30)]
        card = qa.note_lines_unsampled({}, dropped, total=40, limit=50, stills=50)
        self.assertEqual(len(card["lines_unsampled"]), 30)
        self.assertIn("and 20 more", card["not_evidenced"][0])

    def test_a_film_without_narrated_lines_gets_no_key(self):
        self.assertEqual(qa.note_lines_unsampled({"not_evidenced": []}, [], total=0,
                                                 limit=None, stills=4),
                         {"not_evidenced": []})


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
        # the merge is the burst's, not half a second, and the lines left
        # without a still are named
        for rel in ("ytstudio/SKILL.md", "README.md", "docs/production.md"):
            text = self.flat(rel)
            self.assertIn("`lines_unsampled`", text, rel)
            self.assertNotIn("within half a second of a still", text, rel)
            self.assertNotIn("within 0.5 s of a still", text, rel)
        readme = self.flat("README.md")
        self.assertIn("adds the line stills first, then evenly spaced stills", readme)
        doc = " ".join(qa.__doc__.split())
        self.assertIn("`line: <first words>`", doc)
        self.assertIn("KEY_LINES", doc)
        self.assertIn("lines_unsampled", doc)
        self.assertNotIn("within half a second of a still", doc)

    def test_the_frames_help_counts_the_line_stills(self):
        parser = argparse.ArgumentParser()
        qa.register(parser.add_subparsers())
        sub = parser._subparsers._group_actions[0].choices["qa"]
        frames = next(a for a in sub._actions if a.dest == "frame_limit")
        self.assertIn("line", frames.help)


if __name__ == "__main__":
    unittest.main()
