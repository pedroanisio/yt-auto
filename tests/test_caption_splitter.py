"""One caption splitter: the sidecar track and the burned-in captions are the same cues."""
import re
import tempfile
import unittest
from pathlib import Path

from ytstudio import design, voice

LONG = ("a longer sentence that does not fit on one line of a caption and so "
        "has to be split into several cues that follow the words as they are said")


def take(at, text, per_word=0.25):
    words = text.split()
    return {"at": at, "seconds": round(len(words) * per_word, 3), "text": text,
            "words": [{"text": w, "start": i * per_word, "end": i * per_word + per_word * 0.9}
                      for i, w in enumerate(words)]}


def as_takes(rows):
    return [voice.Take(n=i, label=f"s{i}", section="a", text=r["text"], at=r["at"],
                       seconds=r["seconds"],
                       words=[voice.Word(**w) for w in r["words"]])
            for i, r in enumerate(rows, 1)]


class Limits(unittest.TestCase):
    def test_one_place_for_the_limits(self):
        self.assertEqual((design.CAPTION_LINE_CHARS, design.CAPTION_LINES, design.CAPTION_CPS),
                         (42, 2, 17.0))
        self.assertEqual(voice.GATE_LINE_CHARS, design.CAPTION_LINE_CHARS)
        self.assertEqual(voice.GATE_LINES, design.CAPTION_LINES)
        self.assertEqual(voice.GATE_CPS, design.CAPTION_CPS)
        self.assertEqual(voice.caption_lines(LONG), design.wrap_lines(LONG))


class Splitter(unittest.TestCase):
    def legal(self, cues):
        last = -1.0
        for c in cues:
            lines = design.wrap_lines(c.text)
            self.assertLessEqual(len(lines), 2, c.text)
            self.assertTrue(all(len(x) <= 42 for x in lines), c.text)
            self.assertGreaterEqual(c.start, last)
            self.assertGreater(c.end, c.start)
            self.assertLessEqual(sum(map(len, lines)) / (c.end - c.start), 17.0, c.text)
            last = c.end

    def test_default_chunks_fit_two_lines_of_42(self):
        chunks = design.caption_chunks(take(0, LONG, 0.35))
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            lines = design.wrap_lines(c.text)
            self.assertLessEqual(len(lines), 2)
            self.assertTrue(all(len(x) <= 42 for x in lines))
        self.assertEqual(" ".join(c.text for c in chunks), LONG)

    def test_cues_obey_the_gate_for_several_takes(self):
        rows = [take(0, LONG, 0.35), take(14, "short one", 0.4), take(16, LONG, 0.3)]
        cues, *_ = design.caption_cues(rows, film_end=40)
        self.legal(cues)
        self.assertEqual(" ".join(c.text for c in cues),
                         " ".join(r["text"] for r in rows))

    def test_a_fast_cue_is_lengthened_into_the_gap_not_past_the_next(self):
        rows = [take(0, "tiny words here", 0.05), take(5, "next", 0.5)]
        cues, *_ = design.caption_cues(rows, film_end=10)
        self.assertGreaterEqual(cues[0].end - cues[0].start, 15 / 17.0)
        self.assertLessEqual(cues[0].end, cues[1].start)

    def test_a_take_running_into_the_next_is_clamped(self):
        rows = [take(0, LONG, 0.35), take(3, "after", 0.5)]
        cues, clamped, _ = design.caption_cues(rows, film_end=40)
        self.assertGreaterEqual(clamped, 1)
        self.assertTrue(all(a.end <= b.start for a, b in zip(cues, cues[1:])))


class TooFast(unittest.TestCase):
    """The Euler film's real case: 70 characters in 3.96 s, 0.35 s free before it
    and the next cue right after it."""
    TEXT = ("the quick brown fox jumps over the lazy dog and then runs "
            "off to see")

    def rows(self, text, seconds, gap_before=0.35, after=0.0):
        words = text.split()
        per = seconds / len(words)
        first = {"at": 100.0, "seconds": 5.0, "text": "before it",
                 "words": [{"text": "before", "start": 0.0, "end": 2.0},
                           {"text": "it", "start": 2.0, "end": 4.65}]}
        at = 100.0 + 4.65 + gap_before
        fast = {"at": at, "seconds": seconds, "text": text,
                "words": [{"text": w, "start": i * per, "end": (i + 1) * per}
                          for i, w in enumerate(words)]}
        nxt = {"at": at + seconds + after, "seconds": 2.0, "text": "next one here",
               "words": [{"text": "next", "start": 0, "end": 0.6},
                         {"text": "one", "start": 0.6, "end": 1.2},
                         {"text": "here", "start": 1.2, "end": 2.0}]}
        return [first, fast, nxt]

    def gate(self, cues, film_end):
        last = -1.0
        for c in cues:
            lines = design.wrap_lines(c.text)
            self.assertLessEqual(len(lines), 2)
            self.assertTrue(all(len(x) <= 42 for x in lines))
            self.assertLessEqual(c.end, film_end + 0.05)
            self.assertGreaterEqual(c.start, last)
            self.assertLessEqual(sum(map(len, lines)) / (c.end - c.start), 17.0, c.text)
            last = c.end

    def test_70_characters_in_3_96_seconds_pass_the_gate(self):
        text = " ".join(["word"] * 14)[:70].rstrip()
        text = text + "x" * (70 - len(text))
        self.assertEqual(len(text), 70)
        rows = self.rows(text, 3.96)
        cues, _, illegal = design.caption_cues(rows, film_end=120)
        self.assertEqual(illegal, 0)
        self.gate(cues, 120)
        self.assertEqual(" ".join(c.text for c in cues),
                         " ".join(r["text"] for r in rows))

    def test_it_borrows_the_gap_before_it_when_that_is_enough(self):
        text = "a" * 34 + " " + "b" * 35
        rows = self.rows(text, 3.96, gap_before=1.0)
        cues, _, illegal = design.caption_cues(rows, film_end=120)
        self.assertEqual(illegal, 0)
        self.assertEqual(len(cues), 3)
        self.assertLess(cues[1].start, 104.65 + 1.0)
        self.assertGreaterEqual(cues[1].start, cues[0].end)

    def test_no_room_at_all_is_counted_and_never_overlaps(self):
        # 69 characters need 4.06 s; only 3.96 s exist between its neighbours
        text = "a" * 34 + " " + "b" * 35
        rows = self.rows(text, 3.96, gap_before=0.0)
        cues, _, illegal = design.caption_cues(rows, film_end=120)
        self.assertGreaterEqual(illegal, 1)
        self.assertEqual(" ".join(c.text for c in cues),
                         " ".join(r["text"] for r in rows))
        self.assertTrue(all(a.end <= b.start for a, b in zip(cues, cues[1:])))

    def test_the_real_euler_figures_pass_by_borrowing_or_splitting(self):
        text = "the quick brown fox jumps over the lazy dog and then runs off to see it"
        text = text[:70].rstrip()
        rows = self.rows(text, 3.96, gap_before=0.35)
        cues, _, illegal = design.caption_cues(rows, film_end=120)
        self.assertEqual(illegal, 0)
        self.gate(cues, 120)

    def test_a_single_word_that_cannot_fit_is_counted(self):
        rows = [{"at": 0, "seconds": 0.1, "text": "extraordinarily",
                 "words": [{"text": "extraordinarily", "start": 0, "end": 0.1}]},
                {"at": 0.12, "seconds": 1, "text": "next",
                 "words": [{"text": "next", "start": 0, "end": 1}]}]
        cues, _, illegal = design.caption_cues(rows, film_end=5)
        self.assertEqual(illegal, 1)

    def test_write_captions_warns_and_ends_inside_the_film(self):
        import contextlib
        import io
        rows = [{"at": 0, "seconds": 0.1, "text": "extraordinarily",
                 "words": [{"text": "extraordinarily", "start": 0, "end": 0.1}]},
                {"at": 0.12, "seconds": 1, "text": "next",
                 "words": [{"text": "next", "start": 0, "end": 1}]}]
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(err):
            voice.write_captions(Path(tmp) / "n.vtt", as_takes(rows), film_end=1.12)
            text = (Path(tmp) / "n.vtt").read_text(encoding="utf-8")
        self.assertIn("cannot be made legal", err.getvalue())
        ends = re.findall(r"--> (\d\d):(\d\d):(\d\d)\.(\d{3})", text)
        for h, m, s, ms in ends:
            self.assertLessEqual(int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000, 1.17)

    def test_cues_stay_inside_the_film(self):
        rows = [take(0, "last words of the film", 0.1)]
        cues, _, _ = design.caption_cues(rows, film_end=1.0)
        self.assertLessEqual(cues[-1].end, 1.0)


class Sidecar(unittest.TestCase):
    def test_vtt_and_srt_carry_the_splitters_cues(self):
        rows = [take(0, LONG, 0.35), take(15, LONG, 0.3)]
        expected, *_ = design.caption_cues(rows)
        with tempfile.TemporaryDirectory() as tmp:
            vtt, srt = Path(tmp) / "n.vtt", Path(tmp) / "n.srt"
            voice.write_captions(vtt, as_takes(rows))
            voice.write_captions(srt, as_takes(rows), srt=True)
            for path, comma in ((vtt, False), (srt, True)):
                text = path.read_text(encoding="utf-8")
                got = re.findall(r"(\d\d:\d\d:\d\d[.,]\d{3}) --> (\d\d:\d\d:\d\d[.,]\d{3})\n(.*?)\n\n",
                                 text, re.S)
                self.assertEqual(len(got), len(expected))
                for (s, e, body), cue in zip(got, expected):
                    self.assertEqual(s, voice._stamp(cue.start, comma=comma))
                    self.assertEqual(e, voice._stamp(cue.end, comma=comma))
                    self.assertEqual(body.split("\n"), design.wrap_lines(cue.text))


class Wordless(unittest.TestCase):
    def test_legalised_descriptive_cues_still_pass_the_same_rules(self):
        raw = [{"at": 0, "seconds": 1, "text": "[a low hum rises and holds]"},
               {"at": 1.2, "seconds": 3, "text": "[" + "wind " * 14 + "]"}]
        cues, _ = voice.legalise(raw, 12.0)
        self.assertTrue(cues)
        for c in cues:
            self.assertLessEqual(len(c["lines"]), design.CAPTION_LINES)
            self.assertTrue(all(len(x) <= design.CAPTION_LINE_CHARS for x in c["lines"]))
            self.assertLessEqual(sum(map(len, c["lines"])) / (c["end"] - c["start"]),
                                 design.CAPTION_CPS)


if __name__ == "__main__":
    unittest.main()
