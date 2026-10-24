"""Delivery reaches the voice (AUTO-139): the screenplay's marks are
rendered for the engine that can take them and recorded for the one that
cannot; captions, cps and caption timing stay on the line as written.

Nothing is synthesised: the synthesisers are fakes, and espeak-ng is a fake
executable that records its argv.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ytstudio import voice  # noqa: E402
from ytstudio.core import Idea  # noqa: E402
from ytstudio.script import Section, Shot  # noqa: E402

LINE = "Schrödinger put a cat in a box."
MARKS = {"emphasis": ["cat"], "pauses": ["box"],
         "pronounce": [{"term": "Schrödinger", "say": "SHROH-ding-er"}]}


def kinds(rows):
    return sorted({r["kind"] for r in rows})


def strip_tags(text):
    import re
    return re.sub(r"<[^>]*>", "", text)


def film(narration=LINE, note="", **marks):
    shot = Shot(narration=narration, n=1, label="1.1", seconds=4, note=note, **marks)
    plain = Shot(narration="Then it was opened.", n=2, label="1.2", seconds=3)
    return [Section(heading="One", shots=[shot, plain])]


def recorder(sections, tmp, engine="espeak", **kw):
    kw.setdefault("opts", {"wpm": 165, "speed": 1.0})
    return voice.VoiceOver(Idea(title="T", genre="g", description="d"), sections,
                           Path(tmp), engine=engine,
                           voice=kw.pop("voice", "en-us" if engine == "espeak" else "v1"),
                           lang=kw.pop("lang", "en"), verbose=False, **kw)


class RenderMarks(unittest.TestCase):
    def test_espeak_gets_ssml_emphasis_a_break_and_the_lowercased_respelling(self):
        spoken, applied, recorded = voice.render_marks(LINE, MARKS, "espeak")
        self.assertTrue(spoken.startswith("<speak>") and spoken.endswith("</speak>"))
        self.assertIn("<emphasis>cat</emphasis>", spoken)
        self.assertIn('<break time="300ms"/>box', spoken)
        self.assertIn("<emphasis>shroh</emphasis>-ding-er", spoken)
        self.assertNotIn("Schrödinger", spoken)
        self.assertEqual(kinds(applied), ["emphasis", "pause", "pronounce"])
        self.assertEqual(recorded, [])
        ET.fromstring(spoken)

    def test_an_ampersand_and_a_bracket_are_escaped_into_well_formed_xml(self):
        spoken, _, _ = voice.render_marks("Bohr & Schrödinger said x < y.",
                                          MARKS | {"emphasis": ["said"], "pauses": []},
                                          "espeak")
        root = ET.fromstring(spoken)
        self.assertIn("&", "".join(root.itertext()))
        self.assertIn("<", "".join(root.itertext()))

    def test_chatterbox_takes_the_respelling_only(self):
        spoken, applied, recorded = voice.render_marks(LINE, MARKS, "chatterbox")
        self.assertEqual(spoken, "shroh-ding-er put a cat in a box.")
        self.assertEqual(kinds(applied), ["pronounce"])
        self.assertEqual(kinds(recorded), ["emphasis", "pause"])

    def test_elevenlabs_records_the_pause_until_breaks_are_verified(self):
        spoken, applied, recorded = voice.render_marks(LINE, MARKS, "elevenlabs")
        self.assertEqual(spoken, "shroh-ding-er put a cat in a box.")
        self.assertNotIn("pause", voice.MARK_SUPPORT["elevenlabs"])
        self.assertEqual(kinds(recorded), ["emphasis", "pause"])

    def test_the_stub_takes_nothing_and_says_so(self):
        spoken, applied, recorded = voice.render_marks(LINE, MARKS, "stub")
        self.assertEqual(spoken, "")
        self.assertEqual(applied, [])
        self.assertEqual(kinds(recorded), ["emphasis", "pause", "pronounce"])

    def test_no_marks_nothing_is_escaped_or_wrapped(self):
        for engine in voice.MARK_SUPPORT:
            self.assertEqual(voice.render_marks("A & B.", {}, engine), ("", [], []))

    def test_a_mark_on_a_word_the_line_lacks_is_recorded_not_dropped(self):
        spoken, applied, recorded = voice.render_marks(
            LINE, {"emphasis": ["dog"], "pauses": [], "pronounce": []}, "espeak")
        self.assertEqual(spoken, "")
        self.assertEqual([r["word"] for r in recorded], ["dog"])

    def test_the_respelling_keeps_the_word_count_and_no_capitals_reach_an_engine(self):
        shot = Shot(narration="Bohr and Schrödinger's cat. Ask Bohr.", **MARKS | {
            "pronounce": [{"term": "Schrödinger", "say": "SHROH-ding-er"},
                          {"term": "Bohr", "say": "BOHR"}], "emphasis": ["cat", "Ask"]})
        for engine in voice.MARK_SUPPORT:
            marks = {"emphasis": shot.emphasis, "pauses": shot.pauses,
                     "pronounce": shot.pronounce}
            spoken, _, _ = voice.render_marks(shot.narration, marks, engine)
            words = strip_tags(spoken or shot.narration)
            self.assertEqual(len(words.split()), len(shot.narration.split()), engine)
            if spoken:
                self.assertNotIn("SHROH", spoken, engine)
                self.assertNotIn("BOHR", spoken, engine)
                self.assertIn("shroh-ding-er's", strip_tags(spoken), engine)
        self.assertEqual(shot.pronounce[0]["say"], "SHROH-ding-er")


class Takes(unittest.TestCase):
    def test_a_take_carries_its_spoken_text_and_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            take, plain = recorder(film(note="slow, wry", **MARKS), tmp).takes()
        self.assertTrue(take.spoken.startswith("<speak>"))
        self.assertEqual(take.spoken_chars, len(take.spoken))
        self.assertEqual(take.chars, len(LINE))
        self.assertEqual(take.text, LINE)
        self.assertEqual(take.delivery["performance"], "slow, wry")
        self.assertEqual(take.delivery["emphasis"], ["cat"])
        self.assertEqual(kinds(take.delivery["applied"]), ["emphasis", "pause", "pronounce"])
        self.assertEqual(take.delivery["recorded_only"], [])
        self.assertEqual(plain.spoken, "")
        self.assertEqual(plain.spoken_chars, len(plain.text))

    def test_cps_stays_on_the_line_as_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            take = recorder(film(**MARKS), tmp).takes()[0]
        take.seconds = 2.0
        self.assertEqual(take.cps, len(LINE) / 2.0)


class Fingerprint(unittest.TestCase):
    """The cache key of a take with no marks is today's, so existing WAVs
    and their kit cache still hit; a mark changes it."""

    PINNED = {"espeak": "1d42641ba06061cd", "elevenlabs": "52264c20c0f644df"}

    def fingerprint(self, engine, **marks):
        opts = ({"wpm": 165, "speed": 1.0} if engine == "espeak"
                else {"speed": 0.95, "settings": {"stability": 0.5}})
        secs = [Section(heading="One", shots=[Shot(narration=LINE, n=1, label="1.1",
                                                    seconds=4, **marks)])]
        with tempfile.TemporaryDirectory() as tmp:
            rec = recorder(secs, tmp, engine=engine, opts=opts)
            return rec._fingerprint(rec.takes()[0])

    def test_no_marks_is_todays_key(self):
        for engine, pinned in self.PINNED.items():
            self.assertEqual(self.fingerprint(engine), pinned, engine)

    def test_a_mark_changes_it(self):
        for engine, pinned in self.PINNED.items():
            self.assertNotEqual(self.fingerprint(engine, **MARKS), pinned, engine)
        self.assertNotEqual(self.fingerprint("espeak", emphasis=["cat"]),
                            self.fingerprint("espeak", emphasis=["box"]))


class Espeak(unittest.TestCase):
    """espeak-ng reads SSML only with -m, which is passed only for SSML."""

    def fake(self, tmp):
        log = Path(tmp) / "argv.jsonl"
        exe = Path(tmp) / "espeak-ng"
        exe.write_text(textwrap.dedent(f"""\
            #!{sys.executable}
            import json, sys, wave
            argv = sys.argv[1:]
            text = open(argv[argv.index("-f") + 1], encoding="utf-8").read()
            with open({str(log)!r}, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({{"argv": argv, "text": text}}) + "\\n")
            with wave.open(argv[argv.index("-w") + 1], "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
                w.writeframes(b"\\x10\\x10" * 22050)
            """), encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
        return log

    def run_espeak(self, sections, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            log = self.fake(tmp)
            with mock.patch.dict(os.environ, {"PATH": f"{tmp}{os.pathsep}"
                                              + os.environ.get("PATH", "")}):
                recorder(sections, Path(tmp) / "voice", **kw).run()
            return [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]

    def test_an_unmarked_take_is_todays_argv(self):
        calls = self.run_espeak(film())
        argv = calls[0]["argv"]
        self.assertEqual(argv[:4], ["-v", "en-us", "-s", "165"])
        self.assertEqual(argv[4], "-w")
        self.assertEqual(argv[6], "-f")
        self.assertEqual(len(argv), 8)
        self.assertEqual(calls[0]["text"], LINE)

    def test_a_marked_take_is_read_as_ssml(self):
        calls = self.run_espeak(film(**MARKS))
        self.assertIn("-m", calls[0]["argv"])
        self.assertTrue(calls[0]["text"].startswith("<speak>"))
        self.assertIn("shroh", calls[0]["text"])
        self.assertNotIn("-m", calls[1]["argv"])


class Captions(unittest.TestCase):
    def test_a_marked_take_captions_the_line_as_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            marked = recorder(film(**MARKS), tmp).takes()[0]
            plain = recorder(film(), tmp).takes()[0]
            for take in (marked, plain):
                take.seconds = 3.0
                take.words = voice.words_estimated(take.text, take.seconds)
            out = []
            for take in (marked, plain):
                path = Path(tmp) / "c.vtt"
                voice.write_captions(path, [take])
                out.append(path.read_text(encoding="utf-8"))
        self.assertEqual(out[0], out[1])
        self.assertIn("Schrödinger", out[0])
        self.assertNotIn("shroh", out[0])

    @staticmethod
    def alignment(spoken):
        chars = list(spoken)
        return {"alignment": {"characters": chars,
                              "character_start_times_seconds": [i * 0.05 for i in range(len(chars))],
                              "character_end_times_seconds": [i * 0.05 + 0.04
                                                              for i in range(len(chars))]}}

    def take(self, spoken):
        return voice.Take(n=1, label="1.1", section="One", text=LINE, seconds=2.0,
                          spoken=spoken, delivery={})

    def test_aligned_spoken_words_are_mapped_back_to_the_line(self):
        spoken = 'shroh-ding-er put a cat in a <break time="0.3s"/>box.'
        take = self.take(spoken)
        words, timing = voice.caption_words(take, self.alignment(spoken))
        self.assertEqual([w.text for w in words], LINE.split())
        self.assertEqual(timing, "exact")
        self.assertEqual(words[0].start, 0.0)
        self.assertAlmostEqual(words[0].end, 12 * 0.05 + 0.04)
        # the tag counts as no token: "box." starts after it
        box = spoken.index("box")
        self.assertAlmostEqual(words[-1].start, box * 0.05)
        self.assertNotIn("caption_mismatch", take.delivery)

    def test_a_count_mismatch_falls_back_to_estimated_words_of_the_line(self):
        spoken = "shroh ding er put a cat in a box."
        take = self.take(spoken)
        words, timing = voice.caption_words(take, self.alignment(spoken))
        self.assertEqual(timing, "estimated")
        self.assertEqual([w.text for w in words], LINE.split())
        self.assertEqual(words, voice.words_estimated(LINE, 2.0))
        self.assertEqual(take.delivery["caption_mismatch"], {"spoken": 9, "text": 7})

    def test_an_unmarked_take_is_folded_as_today(self):
        take = voice.Take(n=1, label="1.1", section="One", text=LINE, seconds=2.0)
        words, timing = voice.caption_words(take, self.alignment(LINE))
        self.assertEqual([w.text for w in words], LINE.split())
        self.assertEqual(timing, "exact")


class AnOldScript(unittest.TestCase):
    """With no marks and no delivery rows, a run is today's: the captions
    byte for byte, and voice.json only gains the new per-take keys and the
    top-level summary. The stub engine records silence and the fake
    director casts and reviews; nothing is called."""

    VTT = ("WEBVTT\n\n00:00:00.000 --> 00:00:02.857\nLine number 1, said by "
           "Schrödinger & co.\n\n00:00:03.157 --> 00:00:06.013\nLine number 2, said "
           "by Schrödinger & co.\n\n")
    SRT = ("1\n00:00:00,000 --> 00:00:02,857\nLine number 1, said by Schrödinger & "
           "co.\n\n2\n00:00:03,157 --> 00:00:06,013\nLine number 2, said by "
           "Schrödinger & co.\n\n")
    KEYS = {"by_section", "casting", "director", "drift_seconds", "engine", "engine_lang",
            "idea", "lang", "layout", "overlaps", "planned_seconds", "review",
            "spoken_seconds", "takes", "timing", "too_fast_for_captions", "usage",
            "voice", "voice_end"}
    TAKE_KEYS = {"at", "cached", "chars", "engine", "file", "label", "n", "planned_at",
                 "planned_seconds", "rms_dbfs", "seconds", "section", "text", "timing",
                 "voice", "words"}
    NEW_KEYS = set()

    def setUp(self):
        from tests import test_wordless
        from ytstudio import harness
        saved = harness.ENGINE
        self.addCleanup(setattr, harness, "ENGINE", saved)
        harness.ENGINE = test_wordless.FakeEngine
        test_wordless.FakeEngine.prompts = []
        test_wordless.FakeEngine.answers = [
            {"voice": "", "speed": 1.0, "why": "w"},
            {"verdict": "ok", "retakes": [], "notes": "n"}]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        script = {"sections": [{"heading": "One", "shots": [
            {"n": i, "label": f"1.{i}", "visual": "v",
             "narration": f"Line number {i}, said by Schrödinger & co.", "seconds": 3}
            for i in (1, 2)]}]}
        (self.root / "script.json").write_text(json.dumps(script), encoding="utf-8")
        (self.root / "idea.json").write_text(json.dumps(
            {"title": "T", "genre": "g", "description": "d"}), encoding="utf-8")
        self.args = test_wordless.WordlessRun.args(self)
        self.args.engine = "stub"

    def test_captions_are_todays_and_voice_json_only_gains_the_new_keys(self):
        self.assertEqual(voice.run(self.args), 0)
        self.assertEqual((self.root / "narration.vtt").read_text(encoding="utf-8"), self.VTT)
        self.assertEqual((self.root / "narration.srt").read_text(encoding="utf-8"), self.SRT)
        data = json.loads((self.root / "voice.json").read_text(encoding="utf-8"))
        self.assertEqual(set(data) - self.KEYS, self.NEW_KEYS)
        self.assertEqual(self.KEYS - set(data), set())
        self.assertNotIn("draft", data)
        for take in data["takes"]:
            self.assertEqual(set(take) - self.TAKE_KEYS, {"spoken", "spoken_chars", "delivery"})
            self.assertEqual(take["spoken"], "")
            self.assertEqual(take["spoken_chars"], take["chars"])


if __name__ == "__main__":
    unittest.main()
