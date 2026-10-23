"""Retakes are directed (speed per take) and --redo forces only the named takes."""

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from ytstudio import voice
from ytstudio.core import Idea
from ytstudio.script import Section, Shot


def sections(count=5):
    shots = [Shot(narration=f"Line number {i}.", n=i, label=f"1.{i}", seconds=3,
                  at=3 * (i - 1)) for i in range(1, count + 1)]
    return [Section(heading="One", shots=shots)]


class FakeKit:
    """Stands in for the ElevenLabs kit: records each call, writes the files."""

    DEFAULT_MODEL = "m"

    def __init__(self):
        self.calls = []

    class Client:
        def __init__(self, key):
            pass

    def api_key(self):
        return "k"

    def tts_scenes(self, client, scenes_file, voice_id, out_dir, **kw):
        scenes = json.loads(Path(scenes_file).read_text(encoding="utf-8"))
        self.calls.append({"ids": [s["id"] for s in scenes], **kw})
        for scene in scenes:
            (Path(out_dir) / f"{scene['id']}.mp3").write_bytes(b"\x00\x01" * 2400)


def recorder(tmp, **kw):
    return voice.VoiceOver(Idea(title="T", genre="g", description="d"), sections(),
                           Path(tmp), engine="elevenlabs", voice="v1",
                           verbose=False, **kw)


class RedoForcesOnlyTheNamedTakes(unittest.TestCase):
    def run_with(self, **kw):
        kit = FakeKit()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(voice, "pick_engine", lambda e: e), \
                mock.patch.object(voice, "_kit", lambda: kit):
            recorder(tmp, **kw).run()
        return kit

    def forced(self, kit):
        return [i for c in kit.calls if c["force"] for i in c["ids"]]

    def test_two_named_takes_are_the_only_ones_sent_forced(self):
        kit = self.run_with(redo={"1.2", "1.4"})
        self.assertEqual(sorted(self.forced(kit)), ["002-1-2", "004-1-4"])
        kept = [i for c in kit.calls if not c["force"] for i in c["ids"]]
        self.assertEqual(sorted(kept), ["001-1-1", "003-1-3", "005-1-5"])

    def test_no_redo_is_one_unforced_call(self):
        kit = self.run_with()
        self.assertEqual(len(kit.calls), 1)
        self.assertFalse(kit.calls[0]["force"])
        self.assertEqual(len(kit.calls[0]["ids"]), 5)

    def test_all_forces_every_take(self):
        kit = self.run_with(redo={"all"})
        self.assertEqual(len(self.forced(kit)), 5)


class RetakesAreDirected(unittest.TestCase):
    def test_schema_asks_for_speed_per_retake(self):
        item = voice.RETAKE_SCHEMA["properties"]["retakes"]["items"]
        self.assertIn("speed", item["properties"])
        self.assertNotIn("speed", item["required"])

    def test_directions_keep_only_usable_rows_and_clamp(self):
        got = voice.directions_from([
            {"label": "1.1", "reason": "r", "speed": 0.5},
            {"label": "1.2", "reason": "r", "speed": "fast"},
            {"label": "", "reason": "r", "speed": 1.0},
            {"label": "1.3", "reason": "r", "speed": 0.92,
             "settings": {"stability": 0.7, "bogus": 1}}])
        self.assertEqual(got["1.1"]["speed"], voice.SPEED_RANGE[0])
        self.assertNotIn("1.2", got)
        self.assertEqual(got["1.3"], {"speed": 0.92, "settings": {"stability": 0.7}})

    def test_local_engine_records_the_retake_at_its_speed_only(self):
        seen = {}

        def synth(text, out, *, voice, rate, opts):
            seen[text] = opts.get("speed")
            voice_mod.write_wav(out, voice_mod.silence(0.5, rate), rate)
            return {"timing": "estimated"}

        voice_mod = voice
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(voice, "pick_engine", lambda e: e), \
                mock.patch.dict(voice.SYNTH, {"stub": synth}):
            rec = voice.VoiceOver(
                Idea(title="T", genre="g", description="d"), sections(2), Path(tmp),
                engine="stub", opts={"speed": 1.0}, verbose=False,
                redo={"1.2"}, directions={"1.2": {"speed": 0.9}})
            rec.run()
        self.assertEqual(seen["Line number 1."], 1.0)
        self.assertEqual(seen["Line number 2."], 0.9)

    def test_elevenlabs_sends_the_directed_take_alone_at_its_speed(self):
        kit = FakeKit()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(voice, "pick_engine", lambda e: e), \
                mock.patch.object(voice, "_kit", lambda: kit):
            recorder(tmp, redo={"1.3"}, opts={"settings": {"stability": 0.4}},
                     directions={"1.3": {"speed": 0.88}}).run()
        directed = [c for c in kit.calls if c["ids"] == ["003-1-3"]]
        self.assertEqual(len(directed), 1)
        self.assertTrue(directed[0]["force"])
        self.assertEqual(directed[0]["settings"], {"stability": 0.4, "speed": 0.88})
        for call in kit.calls:
            if call is not directed[0]:
                self.assertNotIn("speed", call["settings"] or {})


class TheTakeReviewSaysWhatItHeard(unittest.TestCase):
    """voice.json's take review carries the mark of the takes it was asked
    about (AUTO-105), so `yt qa` uses it only while the film carries them."""

    TAKES = [{"label": "1.1", "text": "Line one.", "file": "voice/1.1.wav", "seconds": 2.5,
              "engine": "kokoro", "voice": "af"},
             {"label": "1.2", "text": "Line two.", "file": "voice/1.2.wav", "seconds": 2,
              "engine": "kokoro", "voice": "af"}]

    def test_the_review_is_kept_with_the_mark_of_its_takes(self):
        review = voice.reviewed({"verdict": "ok", "retakes": [], "notes": "n"}, self.TAKES)
        self.assertEqual(review["verdict"], "ok")
        self.assertEqual(review["heard"], voice.heard(self.TAKES))
        self.assertEqual(review["heard"]["takes"], 2)

    def test_a_retake_changes_the_mark_and_a_number_written_as_float_does_not(self):
        mark = voice.heard(self.TAKES)
        same = [dict(t, seconds=float(t["seconds"])) for t in self.TAKES]
        self.assertEqual(voice.heard(same), mark)
        retaken = [dict(self.TAKES[0], file="voice/1.1-b.wav", seconds=2.9), self.TAKES[1]]
        self.assertNotEqual(voice.heard(retaken)["sha256"], mark["sha256"])


if __name__ == "__main__":
    unittest.main()
