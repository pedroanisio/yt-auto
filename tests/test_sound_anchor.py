"""sound.anchor_cues: a cue's place is derived from the spoken word it lands
on, after spotting, so `yt build` can follow a re-recorded voice (AUTO-140).

The model still writes `at` as a whole second; the anchor is computed from
the timeline the build will place the takes on. It stays out of the
listen-back's digest. No model is called.
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ytstudio import build, cli, sound
from ytstudio.script import Section, Shot


def _words(*pairs):
    return [{"text": t, "start": s, "end": round(s + 0.4, 3)} for t, s in pairs]


def _sections():
    return [Section(heading="Open", shots=[
                Shot(label="S01", n=1, narration="It starts here", seconds=4, at=0),
                Shot(label="S02", n=2, narration="and then it turns", seconds=5, at=4)]),
            Section(heading="Close", shots=[
                Shot(label="S03", n=3, narration="the comet leaves the Sun", seconds=6,
                     at=9)])]


def _voice():
    return {"lang": "en", "takes": [
        {"label": "S01", "at": 0, "seconds": 4, "file": "s01.wav", "text": "It starts here",
         "words": _words(("It", 0.0), ("starts", 0.4), ("here", 1.0))},
        {"label": "S02", "at": 4, "seconds": 5, "file": "s02.wav",
         "text": "and then it turns",
         "words": _words(("and", 0.0), ("then", 0.6), ("it", 1.4), ("turns", 2.0))},
        {"label": "S03", "at": 9, "seconds": 6, "file": "s03.wav",
         "text": "the comet leaves the Sun",
         "words": _words(("the", 0.0), ("comet", 0.5), ("leaves", 1.1),
                         ("the", 2.8), ("Sun", 3.6))}]}


def _cue(cue_id, at, **kw):
    return {"cue_id": cue_id, "kind": kw.pop("kind", "sfx"), "name": cue_id, "at": at,
            "seconds": kw.pop("seconds", 1), "level": "-20 dBFS", **kw}


class Derived(unittest.TestCase):
    def placed(self, voice):
        return build.timeline(_sections(), voice)

    def test_a_cue_anchors_to_the_nearest_word_start_within_the_window(self):
        voice = _voice()
        [cue] = sound.anchor_cues([_cue("S1", 12)], self.placed(voice), voice)
        take = voice["takes"][2]
        self.assertEqual(cue["anchor"], {
            "shot": "S03", "take": build.take_mark(take), "word": 3, "text": "the",
            "offset": 0.2})
        # the take's mark names its file and a sha256
        self.assertTrue(cue["anchor"]["take"].startswith("s03.wav@"))
        self.assertEqual(len(cue["anchor"]["take"].split("@")[1]), 64)

    def test_a_cue_that_names_a_placed_shot_anchors_inside_it(self):
        voice = _voice()
        # 9 s is S03's start, but the cue says it belongs to S02's last word
        [cue] = sound.anchor_cues([_cue("S1", 9, shot="S02")], self.placed(voice), voice)
        self.assertEqual(cue["anchor"]["shot"], "S02")
        self.assertEqual((cue["anchor"]["word"], cue["anchor"]["text"]), (-1, ""))
        self.assertEqual(cue["anchor"]["offset"], 5.0)
        [cue] = sound.anchor_cues([_cue("S1", 6, shot="S02")], self.placed(voice), voice)
        self.assertEqual((cue["anchor"]["shot"], cue["anchor"]["word"],
                          cue["anchor"]["text"]), ("S02", 3, "turns"))
        self.assertEqual(cue["anchor"]["offset"], 0.0)

    def test_a_shot_name_that_is_not_placed_falls_back_to_the_time(self):
        voice = _voice()
        [cue] = sound.anchor_cues([_cue("S1", 12, shot="9.9")], self.placed(voice), voice)
        self.assertEqual(cue["anchor"]["shot"], "S03")

    def test_with_no_voice_the_anchor_is_the_planned_shot_start(self):
        [cue] = sound.anchor_cues([_cue("S1", 6)], self.placed(None), None)
        self.assertEqual(cue["anchor"], {"shot": "S02", "take": "", "word": -1,
                                         "text": "", "offset": 2.0})

    def test_a_cue_with_no_word_in_the_window_anchors_to_the_shot_start(self):
        voice = _voice()
        # S01's words start at 0, 0.4 and 1.0: 3.5 s is 2.5 s from the last
        [cue] = sound.anchor_cues([_cue("S1", 3.5)], self.placed(voice), voice)
        self.assertEqual((cue["anchor"]["shot"], cue["anchor"]["word"]), ("S01", -1))
        self.assertEqual(cue["anchor"]["offset"], 3.5)
        self.assertEqual(cue["anchor"]["take"], build.take_mark(voice["takes"][0]))

    def test_nothing_placed_means_no_anchor(self):
        [cue] = sound.anchor_cues([_cue("S1", 3)], [], None)
        self.assertNotIn("anchor", cue)

    def test_the_listen_backs_digest_does_not_read_the_anchor(self):
        voice = _voice()
        cues = [_cue("M1", 0, kind="music", seconds=15), _cue("S1", 12)]
        before = sound.heard(json.loads(json.dumps(cues)))
        anchored = sound.anchor_cues(json.loads(json.dumps(cues)), self.placed(voice), voice)
        self.assertTrue(all(c.get("anchor") for c in anchored))
        self.assertEqual(sound.heard(anchored), before)
        self.assertNotIn("anchor", sound.HEARD_FIELDS)
        # the Cue objects the stage prints carry it too, and still digest the same
        objects = [sound.Cue(**c) for c in anchored]
        bare = [sound.Cue(**{k: v for k, v in c.items() if k != "anchor"}) for c in anchored]
        self.assertTrue(all(c.anchor for c in objects))
        self.assertEqual(sound.heard(objects), sound.heard(bare))

    def test_the_model_is_not_asked_for_an_anchor(self):
        item = sound.CUES_SCHEMA["properties"]["cues"]["items"]
        self.assertNotIn("anchor", item["properties"])
        self.assertEqual(item["properties"]["at"], {"type": "integer"})

    def test_the_cue_sheet_lists_the_anchor_beside_the_shot(self):
        voice = _voice()
        [raw] = sound.anchor_cues([_cue("S1", 12, shot="S03")], self.placed(voice), voice)
        cue = sound.Cue(**raw)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cues.csv"
            sound.write_csv(str(path), [cue])
            text = path.read_text()
        self.assertIn("anchor", text.splitlines()[0])
        self.assertIn('S03 word 3 ""the"" +0.2s', text)


class FakeDesigner:
    """Stands in for the model: a sheet of two cues, as spotting returns it."""

    def __init__(self, *a, **kw):
        self.usage = "usage: fake"

    def run(self, **kw):
        return {"idea": {"title": "A film"}, "runtime": 15, "palette": {},
                "cues": [_cue("S1", 12), _cue("M1", 0, kind="music", seconds=15)],
                "coverage": {}, "review": {}, "failed_sections": 0}


class Stage(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        script = {"idea": {"title": "A film", "genre": "explainer",
                           "description": "a premise"},
                  "sections": [{"heading": s.heading, "shots": [
                      {"label": sh.label, "n": sh.n, "narration": sh.narration,
                       "seconds": sh.seconds, "at": sh.at} for sh in s.shots]}
                      for s in _sections()]}
        (self.root / "script.json").write_text(json.dumps(script))
        (self.root / "voice.json").write_text(json.dumps(_voice()))

    def tearDown(self):
        self._tmp.cleanup()

    def run_sound(self, *extra):
        args = cli.build_parser().parse_args(
            ["sound", "--project", str(self.root), "--quiet", "--no-house", *extra])
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return args.run(args)

    def test_a_fresh_spot_writes_an_anchor_with_its_take_on_every_cue(self):
        with mock.patch.object(sound, "SoundDesigner", FakeDesigner):
            self.assertEqual(self.run_sound(), 0)
        cues = json.loads((self.root / "sound.json").read_text())["cues"]
        self.assertEqual({c["cue_id"]: (c["anchor"]["shot"], c["anchor"]["word"])
                          for c in cues}, {"S1": ("S03", 3), "M1": ("S01", 0)})
        self.assertTrue(all(c["anchor"]["take"] for c in cues))

    def test_fetch_on_an_existing_anchorless_sheet_leaves_it_anchorless(self):
        sheet = FakeDesigner().run()
        (self.root / "sound.json").write_text(json.dumps(sheet))
        with mock.patch.object(sound, "fetch", lambda *a, **kw: {}), \
                mock.patch.object(sound, "harness_judge", lambda *a, **kw: None), \
                mock.patch.object(sound, "SoundDesigner") as designer:
            self.assertEqual(self.run_sound("--fetch"), 0)
        designer.assert_not_called()
        cues = json.loads((self.root / "sound.json").read_text())["cues"]
        self.assertFalse(any("anchor" in c for c in cues))


if __name__ == "__main__":
    unittest.main()
