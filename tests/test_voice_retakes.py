"""Retakes are directed (speed per take) and --redo forces only the named takes."""

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from tests import test_wordless
from ytstudio import harness, qa, voice
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


class SectionDeliveryOnElevenLabs(unittest.TestCase):
    """AUTO-139 part B: the director's per-section settings are sent, one
    tts_scenes call per distinct settings value; a film with no delivery
    rows makes today's calls, so nothing is re-billed."""

    #: What the code before AUTO-139 sent for this run, captured from it.
    TODAY_CALLS = [
        {"fmt": "pcm_24000", "force": False, "ids": ["001-1-1", "002-1-2", "005-1-5"],
         "language": None, "max_chars": None, "model": "m",
         "settings": {"stability": 0.4}, "timestamps": True},
        {"fmt": "pcm_24000", "force": True, "ids": ["004-1-4"], "language": None,
         "max_chars": None, "model": "m", "settings": {"stability": 0.4},
         "timestamps": True},
        {"fmt": "pcm_24000", "force": True, "ids": ["003-1-3"], "language": None,
         "max_chars": None, "model": "m", "settings": {"speed": 0.88, "stability": 0.4},
         "timestamps": True}]
    TODAY_SCENES = [
        [{"id": "001-1-1", "text": "Line number 1."}, {"id": "002-1-2", "text": "Line number 2."},
         {"id": "005-1-5", "text": "Line number 5."}],
        [{"id": "004-1-4", "text": "Line number 4."}],
        [{"id": "003-1-3", "text": "Line number 3."}]]

    def run_with(self, secs=None, **kw):
        kit = FakeKit()
        scenes = []
        inner = kit.tts_scenes

        def tts_scenes(client, scenes_file, voice_id, out_dir, **call):
            scenes.append(json.loads(Path(scenes_file).read_text(encoding="utf-8")))
            return inner(client, scenes_file, voice_id, out_dir, **call)
        kit.tts_scenes = tts_scenes
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(voice, "pick_engine", lambda e: e), \
                mock.patch.object(voice, "_kit", lambda: kit):
            voice.VoiceOver(Idea(title="T", genre="g", description="d"), secs or sections(),
                            Path(tmp), engine="elevenlabs", voice="v1", verbose=False,
                            **kw).run()
        return [{k: v for k, v in c.items() if k != "log"} for c in kit.calls], scenes

    def test_an_old_script_with_a_named_retake_makes_todays_calls(self):
        calls, scenes = self.run_with(
            redo={"1.3", "1.4"}, opts={"speed": 0.9, "settings": {"stability": 0.4}},
            directions={"1.3": {"speed": 0.88}})
        self.assertEqual(calls, self.TODAY_CALLS)
        self.assertEqual(scenes, self.TODAY_SCENES)

    def two(self):
        one = [Shot(narration=f"Line number {i}.", n=i, label=f"1.{i}", seconds=3)
               for i in (1, 2)]
        two = [Shot(narration=f"Line number {i}.", n=i, label=f"2.{i - 2}", seconds=3)
               for i in (3, 4)]
        return [Section(heading="One", shots=one), Section(heading="Two", shots=two)]

    def test_one_call_per_distinct_settings_and_speed_only_where_a_section_gives_it(self):
        calls, _ = self.run_with(
            self.two(), opts={"speed": 0.9, "settings": {"stability": 0.4}},
            delivery={"One": {"note": "slow", "settings": {"style": 0.5}},
                      "Two": {"note": "brisk", "speed": 1.1}})
        self.assertEqual([c["ids"] for c in calls], [["001-1-1", "002-1-2"],
                                                     ["003-2-1", "004-2-2"]])
        self.assertEqual(calls[0]["settings"], {"stability": 0.4, "style": 0.5})
        self.assertEqual(calls[1]["settings"], {"stability": 0.4, "speed": 1.1})
        self.assertFalse(any(c["force"] for c in calls))

    def test_two_sections_with_the_same_settings_share_a_call(self):
        calls, _ = self.run_with(
            self.two(), opts={"speed": 0.9},
            delivery={"One": {"note": "a", "speed": 1.05}, "Two": {"note": "b", "speed": 1.05}})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["settings"], {"speed": 1.05})

    def test_the_respelled_line_is_sent(self):
        secs = sections(2)
        secs[0].shots[0].narration = "Ask Schrödinger."
        secs[0].shots[0].pronounce = [{"term": "Schrödinger", "say": "SHROH-ding-er"}]
        _, scenes = self.run_with(secs)
        self.assertEqual(scenes[0][0]["text"], "Ask shroh-ding-er.")
        self.assertEqual(scenes[0][1]["text"], "Line number 2.")


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


class TheStageKeepsTheMark(unittest.TestCase):
    """`yt voice` itself writes the take review with the mark of the takes
    it recorded, not only the helper: without it every voice.json would
    reach QA as a review "written before AUTO-105" and voiceover would stay
    unscored for good. The stub engine records silence, the fake director
    casts and reviews; nothing is called."""

    def setUp(self):
        saved = harness.ENGINE
        self.addCleanup(setattr, harness, "ENGINE", saved)
        harness.ENGINE = test_wordless.FakeEngine
        test_wordless.FakeEngine.prompts = []
        test_wordless.FakeEngine.answers = [
            {"voice": "", "speed": 1.0, "why": "the stub has one voice"},
            {"verdict": "ok", "retakes": [], "notes": "n"}]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        script = {"sections": [{"heading": "One", "shots": [
            {"n": i, "label": f"1.{i}", "visual": "v", "narration": f"Line number {i}.",
             "seconds": 3} for i in (1, 2)]}]}
        (self.root / "script.json").write_text(json.dumps(script), encoding="utf-8")
        (self.root / "idea.json").write_text(json.dumps(
            {"title": "T", "genre": "g", "description": "d"}), encoding="utf-8")

    def test_voice_json_carries_the_review_with_the_mark_qa_reads_as_current(self):
        args = test_wordless.WordlessRun.args(self)        # the same flags, this root
        args.engine = "stub"
        self.assertEqual(voice.run(args), 0)
        data = json.loads((self.root / "voice.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["takes"]), 2)
        self.assertEqual(data["review"]["verdict"], "ok")
        self.assertEqual(data["review"]["heard"], voice.heard(data["takes"]))
        row = {r["source"]: r for r in qa.listening_evidence(None, data)}["voice"]
        self.assertTrue(row["current"], row["why"])

    def test_the_director_hears_the_marks_and_its_section_notes_reach_the_takes(self):
        script = json.loads((self.root / "script.json").read_text(encoding="utf-8"))
        script["sections"][0]["shots"][0] |= {"emphasis": ["number"], "note": "wry"}
        (self.root / "script.json").write_text(json.dumps(script), encoding="utf-8")
        test_wordless.FakeEngine.answers[0] = {
            "voice": "", "speed": 1.0, "why": "w",
            "delivery": [{"section": "one", "note": "slow and low", "speed": 0.9},
                         {"section": "Coda", "note": "nowhere"}]}
        args = test_wordless.WordlessRun.args(self)
        args.engine = "stub"
        self.assertEqual(voice.run(args), 0)
        casting, retakes = test_wordless.FakeEngine.prompts
        self.assertIn("wry", casting)              # the shot's note, in the Sections brief
        self.assertIn('"number"', retakes)
        self.assertIn("slow and low", retakes)
        data = json.loads((self.root / "voice.json").read_text(encoding="utf-8"))
        self.assertEqual(data["takes"][0]["delivery"]["section_note"], "slow and low")
        self.assertEqual(data["casting"]["delivery"][0],
                         {"section": "one", "note": "slow and low", "speed": 0.9})
        self.assertEqual(data["delivery"]["unmatched"], [{"section": "Coda", "note": "nowhere"}])
        self.assertEqual(data["delivery"]["by_kind"]["speed"], {"applied": 0, "recorded_only": 2})


if __name__ == "__main__":
    unittest.main()
