"""`yt sound --fetch`: a candidate that passes the level checks is judged against its cue.

Freesound, the meter and the model are faked. What the tests pin is the loop,
not the model's taste: a candidate is judged only after the level checks pass;
an accept is recorded with its evidence; a reject takes the same next-ranked
path as a level refusal, with its reason kept; when every candidate is
rejected the cue is unfilled and says why; the evidence handed to the judge
carries the Freesound name, tags, description, duration and licence, the
measured levels and spectral facts, and a spectrogram image; the harness judge
sends the image down the vision path (an inspection that reads it) and asks a
plain decision when there is none.
"""

import base64
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.hermetic import fake_remote
from tests.test_harness import FakeEngine
from tests.test_sound_fetch import FetchCase, cue, row
from tests.test_sound_levels import EBUR
from ytstudio import freesound, harness, render, sound
from ytstudio.sound import Levels

os.environ.setdefault("YT_NO_REMOTE", "1")

PNG = b"\x89PNG\r\n\x1a\nfake"

#: The owner's report on the Euler film: (cue kind, cue name, what it was given).
REAL_FAILURES = [
    ("ambience", "study air", "car (19)"),
    ("ambience", "study air back", "A Robin Singing At The Summer Cafe"),
    ("music", "home pad", "room-tone bedroom"),
    ("music", "resolution chord", "Elevator arrive"),
    ("music", "warm bed", "Nightmare_Approaching_sound.wav"),
    ("music", "second bed", "TV Test Tone (1kHz -6dBFS)"),
]


def spectral(**kw) -> Levels:
    return Levels(lufs=-10.0, true_peak=-6.0, floor=-45.0, centroid_hz=900.0,
                  flatness=0.02, tonal=True, spectrogram=PNG, **kw)


def hit_row(sound_id, name, duration=20.0, tags=("a", "b"), description="d"):
    return row(sound_id, name, duration) | {"tags": list(tags),
                                            "description": description}


class Judged(FetchCase):
    """fetch with a fake meter and a fake judge that logs what it was shown."""

    def run_fetch(self, *cues, verdicts=None, levels=None, judge=True):
        self.seen: list = []
        verdicts = verdicts or {}

        def fake_judge(c, evidence):
            self.seen.append((c, evidence))
            hid = evidence["hit"]["id"]
            ok, why = verdicts.get(hid, (True, "fits"))
            return {"verdict": "accept" if ok else "reject", "reason": why,
                    "confidence": 0.9}

        def measure(paths):
            return [(levels or {}).get(Path(p).name, spectral()) for p in paths]
        data = self.data(*cues)
        sound.fetch(self.root, data, token="t", say=self.hush, measure=measure,
                    judge=fake_judge if judge else None)
        return data


class Loop(Judged):
    def setUp(self):
        super().setUp()
        self.catalogue["room tone"] = [
            hit_row(1, "First.wav", tags=("room", "tone"), description="a quiet room"),
            hit_row(2, "Second.wav"), hit_row(3, "Third.wav")]
        self.cue = cue("ambience", "A1", "Study air", 10, query="room tone",
                       purpose="the sound of the study", description="a still stone room",
                       search_description="room tone, quiet")

    def test_an_accepted_candidate_is_recorded_with_its_verdict_and_evidence(self):
        data = self.run_fetch(self.cue)
        c = data["cues"][0]
        self.assertEqual(c["freesound_id"], 1)
        j = c["judgement"]
        self.assertEqual(j["verdict"], "accept")
        self.assertEqual(j["reason"], "fits")
        self.assertEqual(j["rejected"], [])
        ev = j["evidence"]
        self.assertEqual(ev["name"], "First.wav")
        self.assertEqual(ev["tags"], ["room", "tone"])
        self.assertEqual(ev["description"], "a quiet room")
        self.assertEqual(ev["licence"], c["licence"])
        self.assertEqual(ev["lufs"], -10.0)
        self.assertEqual(ev["centroid_hz"], 900.0)
        self.assertEqual(ev["flatness"], 0.02)
        self.assertEqual(len(self.seen), 1)

    def test_the_judge_is_shown_the_candidate_the_cue_and_a_spectrogram_file(self):
        self.run_fetch(self.cue)
        shown_cue, evidence = self.seen[0]
        self.assertEqual(shown_cue["name"], "Study air")
        hit = evidence["hit"]
        for key in ("id", "name", "tags", "description", "duration", "licence"):
            self.assertIn(key, hit)
        self.assertEqual(evidence["levels"].lufs, -10.0)
        self.assertEqual(Path(evidence["spectrogram"]).read_bytes(), PNG)

    def test_a_rejection_tries_the_next_ranked_hit_and_keeps_the_reason(self):
        data = self.run_fetch(self.cue, verdicts={1: (False, "a car, not a room")})
        c = data["cues"][0]
        self.assertEqual(c["freesound_id"], 2)
        self.assertEqual(c["judgement"]["verdict"], "accept")
        rej = c["judgement"]["rejected"]
        self.assertEqual([r["freesound_id"] for r in rej], [1])
        self.assertEqual(rej[0]["reason"], "a car, not a room")
        self.assertEqual(rej[0]["name"], "First.wav")
        self.assertTrue(any("a car, not a room" in r["reason"]
                            for r in data["fetch"]["refused"]))
        self.assertEqual(data["unfilled"], [])

    def test_every_candidate_rejected_leaves_the_cue_unfilled_with_the_reasons(self):
        data = self.run_fetch(self.cue, verdicts={
            1: (False, "reason one"), 2: (False, "reason two"), 3: (False, "reason three")})
        c = data["cues"][0]
        self.assertNotIn("file", c)
        self.assertNotIn("freesound_id", c)
        self.assertNotIn("judgement", c)
        (miss,) = data["unfilled"]
        for text in ("reason one", "reason two", "reason three"):
            self.assertIn(text, miss["reason"])
        self.assertEqual(len(self.seen), sound.FETCH_TRIES)
        self.assertEqual(list((self.root / "sound").glob("*.mp3")), [])

    def test_a_candidate_refused_on_level_is_never_judged(self):
        loud_floor = Levels(lufs=-20.0, true_peak=-3.0, floor=-10.0,
                            centroid_hz=1.0, flatness=0.5, tonal=False, spectrogram=PNG)
        self.run_fetch(self.cue, levels={"01-study-air.mp3": loud_floor})
        judged = [e["hit"]["id"] for _, e in self.seen]
        self.assertNotIn(1, judged)

    def test_without_a_judge_the_fetch_is_as_before(self):
        data = self.run_fetch(self.cue, judge=False)
        self.assertNotIn("judgement", data["cues"][0])
        self.assertEqual(data["cues"][0]["freesound_id"], 1)

    def test_a_judge_that_fails_leaves_the_candidate_unjudged_not_the_fetch_dead(self):
        def broken(c, evidence):
            raise harness.HarnessError("the model is down")
        data = self.data(self.cue)
        sound.fetch(self.root, data, token="t", say=self.hush,
                    measure=lambda ps: [spectral() for _ in ps], judge=broken)
        c = data["cues"][0]
        self.assertEqual(c["freesound_id"], 1)
        self.assertEqual(c["judgement"]["verdict"], "unjudged")
        self.assertIn("model is down", c["judgement"]["reason"])


class RealFailures(Judged):
    def test_each_failure_the_owner_heard_is_rejected_and_the_next_hit_kept(self):
        for n, (kind, name, wrong) in enumerate(REAL_FAILURES):
            with self.subTest(wrong):
                self.catalogue[f"q{n}"] = [hit_row(100 + n, wrong), hit_row(200 + n, "Good.wav")]
                data = self.run_fetch(
                    cue(kind, f"C{n}", name, 10, query=f"q{n}"),
                    verdicts={100 + n: (False, f"{wrong!r} is not {name!r}")})
                c = data["cues"][0]
                self.assertEqual(c["freesound_id"], 200 + n)
                self.assertEqual(c["judgement"]["rejected"][0]["name"], wrong)
                self.assertEqual(self.seen[0][1]["hit"]["name"], wrong)


class Fields(unittest.TestCase):
    def test_the_search_asks_for_tags_and_description(self):
        for field in ("tags", "description"):
            self.assertIn(field, freesound.FIELDS.split(","))

    def test_a_hit_keeps_them(self):
        h = freesound.Hit.from_raw(hit_row(5, "X.wav", tags=("a", "b"), description="why"))
        self.assertEqual(h.tags, ["a", "b"])
        self.assertEqual(h.description, "why")

    def test_a_hit_without_them_has_none(self):
        h = freesound.Hit.from_raw(row(5, "X.wav", 3))
        self.assertEqual((h.tags, h.description), ([], ""))


SPEC = ("@@SPEC {n}\ncentroid=1234.5 flatness=0.0123\n@@PNG {n}\n"
        + base64.b64encode(PNG).decode() + "\n@@SPECEND {n}\n")


class Remote(unittest.TestCase):
    def setUp(self):
        fake_remote(self, RS_RENDER_HOST="gpu")
        render._HOSTS.clear()
        render._HOSTS["gpu"] = render.Host(addr="gpu", engine="/e/scene-render",
                                           ffprobe="/opt/af/bin/ffprobe")
        patch = mock.patch.object(render, "ssh_alive", return_value=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(render._HOSTS.clear)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.paths = []
        for n in range(2):
            p = Path(self._tmp.name) / f"{n}.mp3"
            p.write_bytes(b"ID3" + bytes([n]))
            self.paths.append(p)

    def test_the_same_batch_measures_the_spectrum_and_draws_the_spectrogram(self):
        script = sound.measure_script("/tmp/x", ["0.wav"])
        self.assertIn("aspectralstats", script)
        self.assertIn("showspectrumpic", script)

    def test_spectral_facts_and_the_image_come_back_with_the_levels(self):
        out = "".join(f"@@FILE {n}\n{EBUR}\n@@END {n} 0\n" + SPEC.format(n=n)
                      for n in range(2))
        done = subprocess.CompletedProcess([], 0, out.encode(), b"")
        with mock.patch("subprocess.run", return_value=done) as run:
            got = sound.measure_remote(self.paths)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(got[0].lufs, -27.5)
        self.assertEqual(got[0].centroid_hz, 1234.5)
        self.assertEqual(got[0].flatness, 0.0123)
        self.assertTrue(got[0].tonal)
        self.assertEqual(got[0].spectrogram, PNG)

    def test_a_host_that_cannot_do_the_spectrum_still_levels_the_file(self):
        out = f"@@FILE 0\n{EBUR}\n@@END 0 0\n@@FILE 1\n{EBUR}\n@@END 1 0\n"
        done = subprocess.CompletedProcess([], 0, out.encode(), b"")
        with mock.patch("subprocess.run", return_value=done):
            got = sound.measure_remote(self.paths)
        self.assertEqual(got[0].lufs, -27.5)
        self.assertIsNone(got[0].centroid_hz)
        self.assertIsNone(got[0].tonal)
        self.assertEqual(got[0].spectrogram, b"")


class HarnessJudge(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._engine = harness.ENGINE
        harness.ENGINE = FakeEngine
        FakeEngine.json_calls, FakeEngine.stream_calls = [], []
        FakeEngine.answers, FakeEngine.texts, FakeEngine.fail = [], [], False
        self.cue = cue("ambience", "A1", "Study air", 10, purpose="the sound of the study",
                       description="a still stone room", search_description="quiet room tone")
        self.evidence = {"hit": {"id": 7, "name": "car (19)", "tags": ["car"],
                                 "description": "a car passing", "duration": 12.0,
                                 "licence": "CC0"},
                         "levels": spectral(), "spectrogram": None}

    def tearDown(self):
        harness.ENGINE = self._engine
        self._tmp.cleanup()

    def test_no_image_is_a_plain_decision_carrying_the_cue_and_the_evidence(self):
        FakeEngine.answers = [{"verdict": "reject", "reason": "a car", "confidence": 0.95}]
        got = sound.harness_judge(self.root, model="sonnet")(self.cue, self.evidence)
        self.assertEqual(got["verdict"], "reject")
        (call,) = FakeEngine.json_calls
        for text in ("Study air", "the sound of the study", "a still stone room",
                     "quiet room tone", "car (19)", "a car passing", "ambience"):
            self.assertIn(text, call["prompt"])
        self.assertEqual(set(call["schema"]["required"]),
                         {"verdict", "reason", "confidence"})
        self.assertEqual(call["schema"]["properties"]["verdict"]["enum"],
                         ["accept", "reject"])
        self.assertEqual(FakeEngine.stream_calls, [])

    def test_an_image_goes_down_the_vision_path(self):
        image = self.root / "sound" / "judge" / "7" / "spectrogram.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(PNG)
        self.evidence["spectrogram"] = image
        FakeEngine.answers = [{"verdict": "accept", "reason": "ok", "confidence": 0.8}]
        got = sound.harness_judge(self.root, model="sonnet")(self.cue, self.evidence)
        self.assertEqual(got["verdict"], "accept")
        (call,) = FakeEngine.stream_calls
        self.assertIn("Read", call["tools"])
        self.assertEqual(Path(call["cwd"]), image.parent)


if __name__ == "__main__":
    unittest.main()
