"""A film with no narration carries descriptive captions and passes the gate's caption rules.

`yt voice` on a screenplay with no narration used to refuse, so no narration.vtt
and no voice.json existed and the delivery gate could never pass. Now the model
writes sound descriptions through the harness, code makes each cue legal to the
gate's exact limits, and voice.json records the voice end as the last shot's
start (the gate's tail check treats everything after it as the ending).
"""

import argparse
import html
import json
import math
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_engine import Usage  # noqa: E402
from ytstudio import gate, harness, voice  # noqa: E402
from ytstudio.core import Idea  # noqa: E402
from ytstudio.script import Section, Shot  # noqa: E402


class GateRuleError(AssertionError):
    pass


def require(ok, message):
    if not ok:
        raise GateRuleError(message)


def gate_captions(path, duration):
    """scene-render's production_gate.captions, copied (that tool lives in another repo)."""
    text = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    require(Path(path).suffix.lower() in {".vtt", ".srt"}, "Final captions must be VTT or SRT")

    def seconds(value):
        fields = value.replace(",", ".").split(":")
        require(len(fields) in (2, 3), "Invalid caption timestamp")
        return sum(float(field) * 60 ** i for i, field in enumerate(reversed(fields)))
    count, previous = 0, -1.0
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.splitlines()
        indices = [i for i, line in enumerate(lines) if "-->" in line]
        if not indices:
            require(block.startswith(("WEBVTT", "NOTE", "STYLE", "REGION")), "Malformed caption block")
            continue
        require(len(indices) == 1, "Malformed caption cue")
        i = indices[0]
        times = lines[i].split("-->")
        start, end = seconds(times[0].strip()), seconds(times[1].strip().split()[0])
        require(math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration + 0.05,
                "Caption outside final video timeline")
        require(start >= previous, "Overlapping or unordered caption cues")
        previous = end
        content = [html.unescape(re.sub(r"<[^>]*>", "", line)) for line in lines[i + 1:]]
        require(1 <= len(content) <= 2 and all(0 < len(line) <= 42 for line in content),
                "Captions exceed two 42-character lines")
        require(sum(len(line) for line in content) / (end - start) <= 17.0,
                "Captions exceed 17 characters per second")
        count += 1
    require(count > 0, "No caption cues found")
    return count


def silent_film():
    shots = [Shot(visual="v", narration="", n=i, label=f"1.{i}", seconds=4)
             for i in (1, 2, 3)]
    return [Section(heading="One", shots=shots)]      # shots at 0, 4, 8; ends at 12


def write_vtt(root, cues):
    path = Path(root) / "t.vtt"
    path.write_text("WEBVTT\n\n" + "\n".join(
        f"{voice._stamp(a)} --> {voice._stamp(b)}\n{t}\n" for a, b, t in cues),
        encoding="utf-8")
    return path


class LegaliseToTheGatesLimits(unittest.TestCase):
    def legal(self, raw, end=12.0):
        cues, notes = voice.legalise(raw, end)
        with tempfile.TemporaryDirectory() as tmp:
            if cues:
                gate_captions(write_vtt(tmp, [(c["start"], c["end"], "\n".join(c["lines"]))
                                              for c in cues]), end)
        return cues, notes

    def test_fast_cue_is_lengthened_inside_the_gap(self):
        cues, notes = self.legal([{"at": 1, "seconds": 1, "text": "[a door slams shut twice]"}])
        self.assertEqual(len(cues), 1)
        self.assertGreaterEqual(cues[0]["end"] - cues[0]["start"], 25 / 17)
        self.assertTrue(any("lengthen" in n for n in notes))

    def test_cue_that_cannot_be_lengthened_is_dropped_and_named(self):
        raw = [{"at": 1, "seconds": 1, "text": "[a long low rumble builds]"},
               {"at": 1.5, "seconds": 2, "text": "[glass breaks]"}]
        cues, notes = self.legal(raw, end=2.1)
        self.assertEqual([" ".join(c["lines"]) for c in cues], ["[glass breaks]"])
        self.assertTrue(any("drop" in n for n in notes))

    def test_a_cue_ten_milliseconds_short_borrows_slack_instead_of_dropping(self):
        raw = [{"at": 0.5, "seconds": 1.6, "text": "[Soft strings hold a note]"},
               {"at": 2.1, "seconds": 1.4, "text": "[Low cello fifth swells]"},
               {"at": 3.5, "seconds": 2, "text": "[A door clicks shut]"}]
        cues, notes = self.legal(raw)                  # 24 chars: 1.41 s needed, 1.40 s free
        self.assertEqual(len(cues), 3)
        self.assertTrue(any("borrow" in n for n in notes))
        self.assertFalse(any("dropped" in n for n in notes))

    def test_overlap_and_disorder_are_repaired(self):
        raw = [{"at": 5, "seconds": 3, "text": "[wind rises]"},
               {"at": 1, "seconds": 6, "text": "[a clock ticks]"}]
        cues, _ = self.legal(raw)
        self.assertEqual([c["lines"][0] for c in cues], ["[a clock ticks]", "[wind rises]"])

    def test_cue_outside_the_timeline_is_clamped_or_dropped(self):
        raw = [{"at": 11.9, "seconds": 5, "text": "[a final chime]"},
               {"at": 30, "seconds": 2, "text": "[never heard]"},
               {"at": -3, "seconds": 5, "text": "[a low hum]"}]
        cues, _ = self.legal(raw)
        self.assertTrue(all(0 <= c["start"] < c["end"] <= 12.0 for c in cues))
        self.assertNotIn("[never heard]", [c["lines"][0] for c in cues])

    def test_wraps_to_two_lines_of_42_and_drops_what_does_not_fit(self):
        long = "[" + " ".join(["rattling"] * 7) + "]"            # 64 chars: two lines
        cues, _ = self.legal([{"at": 0, "seconds": 8, "text": long}])
        self.assertEqual(len(cues[0]["lines"]), 2)
        self.assertTrue(all(len(line) <= 42 for line in cues[0]["lines"]))
        cues, notes = self.legal([{"at": 0, "seconds": 8, "text": "[" + "x " * 60 + "]"}])
        self.assertEqual(cues, [])
        self.assertTrue(any("drop" in n for n in notes))

    def test_empty_text_is_dropped(self):
        cues, _ = voice.legalise([{"at": 0, "seconds": 2, "text": "   "}], 12.0)
        self.assertEqual(cues, [])

    def test_the_gate_rejects_the_unrepaired_cue(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(GateRuleError):
                gate_captions(write_vtt(tmp, [(1, 2, "[a door slams shut twice]")]), 12)


class FakeEngine:
    answers: list = []
    prompts: list = []

    def __init__(self, config):
        self.usage = Usage()

    def ask_json(self, prompt, schema, system=None):
        FakeEngine.prompts.append(prompt)
        return FakeEngine.answers.pop(0)


class WordlessRun(unittest.TestCase):
    CUES = {"cues": [
        {"at": 0.5, "seconds": 3, "text": "[a low drone fades in]"},
        {"at": 4.5, "seconds": 1, "text": "[footsteps on gravel, slow]"},
        {"at": 9, "seconds": 2, "text": "[a single bell]"}]}

    def setUp(self):
        self.old = harness.ENGINE
        harness.ENGINE = FakeEngine
        FakeEngine.answers, FakeEngine.prompts = [json.loads(json.dumps(self.CUES))], []
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        script = {"sections": [{"heading": "One", "shots": [
            {"n": i, "label": f"1.{i}", "visual": "v", "narration": "", "seconds": 4}
            for i in (1, 2, 3)]}]}
        (self.root / "script.json").write_text(json.dumps(script), encoding="utf-8")
        (self.root / "sound.json").write_text(json.dumps({"cues": [
            {"kind": "sfx", "name": "bell", "at": 9, "seconds": 2, "shot": "1.3",
             "description": "one struck bell"}]}), encoding="utf-8")
        (self.root / "idea.json").write_text(json.dumps(
            {"title": "T", "genre": "g", "description": "d"}), encoding="utf-8")

    def tearDown(self):
        harness.ENGINE = self.old
        self.tmp.cleanup()

    def args(self):
        return argparse.Namespace(
            list_engines=False, project=str(self.root), title=None, script=None, lang="",
            quiet=True, model=None, fresh=False, engine="auto", dry_run=False,
            work=None, house=None, no_house=True, audio_format=None, stitch=False,
            voice="", reference=None, redo="", layout="sequential", gap=0.3,
            section_gap=1.0, rate=24000, wpm=165, engine_model=None, max_chars=None,
            no_normalise=False, no_trim=False)

    def test_no_narration_no_longer_refuses_and_writes_a_legal_track(self):
        self.assertEqual(voice.run(self.args()), 0)
        for name in ("narration.vtt", "narration.srt"):
            self.assertGreaterEqual(gate_captions(self.root / name, 12.0), 2, name)
        data = json.loads((self.root / "voice.json").read_text(encoding="utf-8"))
        self.assertEqual(data["takes"], [])
        self.assertIs(data["wordless"], True)
        self.assertEqual(data["voice_end"], 8.0)
        self.assertEqual(data["voice_end_source"], "last shot start")

    def test_the_model_is_given_the_cue_sheet_and_the_shots(self):
        voice.run(self.args())
        self.assertEqual(len(FakeEngine.prompts), 1)
        self.assertIn("one struck bell", FakeEngine.prompts[0])
        self.assertIn("1.3", FakeEngine.prompts[0])

    def test_the_gate_command_carries_the_last_shots_start(self):
        voice.run(self.args())
        project = gate.workspace.Project(self.root)
        ns = argparse.Namespace(captions=[], voice=None, voice_end=None, video=None,
                                scene=None, render=None, tools_dir=None, build=None)
        found, _ = gate.inputs(project, ns)
        self.assertEqual(found["voice end"], 8.0)
        self.assertTrue(found["captions"].endswith("narration.vtt"))
        cmd = gate.local_command(found | {"project": self.root}, [], frames=False,
                                 loudness_target=-14.0, max_illegal=None)
        self.assertEqual(cmd[cmd.index("--voice-end") + 1], "8.0")

    def test_a_voice_end_of_zero_is_not_missing(self):
        (self.root / "voice.json").write_text(json.dumps(
            {"wordless": True, "voice_end": 0.0, "takes": []}), encoding="utf-8")
        project = gate.workspace.Project(self.root)
        ns = argparse.Namespace(captions=[], voice=None, voice_end=None, video=None,
                                scene=None, render=None, tools_dir=None, build=None)
        _, missing = gate.inputs(project, ns)
        self.assertNotIn("voice end", missing)

    def test_the_hint_for_missing_captions_names_the_wordless_case(self):
        hint = next(row[3] for row in gate.STOPS if row[0] == "No caption cues found")
        self.assertIn("wordless", hint)


class OtherReadersOfAWordlessVoice(unittest.TestCase):
    """voice.json of a wordless film: takes [], engine "none", voice_end = last shot start."""
    VOICE = {"engine": "none", "wordless": True, "takes": [], "voice_end": 8.0,
             "film_end": 12.0, "spoken_seconds": 0.0, "planned_seconds": 0,
             "drift_seconds": 0.0, "timing": [], "too_fast_for_captions": [],
             "overlaps": [], "descriptive_captions": {"cues": 10, "asked": 10, "repairs": []}}
    SCRIPT = {"sections": [{"heading": "One", "shots": [
        {"n": i, "label": f"1.{i}", "visual": "v", "narration": "", "seconds": 4}
        for i in (1, 2, 3)]}]}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "silent"
        self.root.mkdir()
        self.put("idea.json", {"title": "T", "genre": "g", "description": "d"})
        self.put("voice.json", self.VOICE)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, data):
        (self.root / name).write_text(json.dumps(data), encoding="utf-8")

    def doctor(self, runtime=15):
        from ytstudio import doctor, workspace
        self.put("sound.json", {"cues": [], "runtime": runtime, "coverage": {}})
        self.put("build.json", {"scene": {"duration": 12.0}, "findings": [], "layers": {},
                                "timeline": {"shots": 3, "from_voice": 0}})
        return doctor.check_project(workspace.Project(self.root)).findings

    def test_doctor_compares_the_cue_sheet_with_the_films_end_not_the_voice_end(self):
        said = [f.message for f in self.doctor(runtime=12)]
        self.assertFalse([m for m in said if "cue sheet" in m], said)
        said = [f.message for f in self.doctor(runtime=20)]
        self.assertTrue([m for m in said if "film ends at 0:12" in m], said)

    def test_doctor_does_not_ask_for_a_rebuild_from_a_narration_that_does_not_exist(self):
        said = [f.message for f in self.doctor()]
        self.assertFalse([m for m in said if "screenplay estimates" in m], said)

    def test_doctor_says_wordless_film_with_its_caption_count(self):
        line = [f.message for f in self.doctor() if f.area == "voice" and f.level == "ok"]
        self.assertEqual(line, ["a wordless film with 10 descriptive captions"])

    def test_retime_says_there_is_no_narration_to_measure(self):
        import contextlib
        import io
        from ytstudio import retime
        self.put("script.json", self.SCRIPT)
        self.assertEqual(retime.plan(self.SCRIPT, self.VOICE)[0], [])
        ns = argparse.Namespace(project=str(self.root), script=None, voice=None, title=None,
                                quiet=True, model=None, fresh=False, dry_run=True,
                                house=None, no_house=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(retime.run(ns), 0)
        self.assertIn("wordless film has no narration", out.getvalue())

    def test_qa_tells_the_reviewer_the_film_is_wordless(self):
        from ytstudio import qa
        got = qa.narration_measurements(self.VOICE)
        self.assertTrue(got["wordless"])
        self.assertEqual(got["descriptive_captions"], 10)
        self.assertNotIn("wordless", qa.narration_measurements({"engine": "kokoro"}))

    def test_build_and_sound_time_a_wordless_film_from_the_screenplay(self):
        from ytstudio import build, sound
        sections = silent_film()
        placed = build.timeline(sections, self.VOICE)
        self.assertEqual([(p.at, p.seconds, p.source) for p in placed],
                         [(0.0, 4.0, "script"), (4.0, 4.0, "script"), (8.0, 4.0, "script")])
        self.assertEqual(
            sum(s.actual_seconds for s in sound.on_measured_clock(self.SCRIPT, self.VOICE)),
            sum(s.actual_seconds for s in sound.sections_of(self.SCRIPT)))
        self.assertEqual(build.stale_takes(sections, self.VOICE), [])


if __name__ == "__main__":
    unittest.main()
