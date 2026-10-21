"""Picture and narration on one clock.

`yt script` budgets a shot by words per minute; `yt voice` measures what the
read took. Once a take exists, every later stage plans against the measured
length: the vfx brief states it, the authored symbol has to span it, the
build refuses a symbol shorter than its shot, and `yt run` reconciles the
screenplay (`retime`) before anything is authored.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import build, cli, vfx, workspace  # noqa: E402
from ytstudio.core import Idea  # noqa: E402
from ytstudio.script import Section, Shot  # noqa: E402

SECTIONS = [{"heading": "Open", "seconds": 10, "beats": [],
             "shots": [{"label": "1.1", "at": 0, "seconds": 6, "visual": "a cube",
                        "narration": "One cube.", "on_screen": "", "asset": ""},
                       {"label": "1.2", "at": 6, "seconds": 4, "visual": "more cubes",
                        "narration": "", "on_screen": "", "asset": ""}]}]

WORDS = {"1.1": {"at": 0.0, "seconds": 9.5, "timing": "exact",
                 "words": [{"w": "One", "s": 0.0}, {"w": "cube.", "s": 8.9}]}}


class MeasuredClock(unittest.TestCase):
    def test_a_recorded_shot_takes_the_takes_length_and_a_silent_one_follows(self):
        out = vfx.measured_sections(SECTIONS, WORDS)
        first, second = out[0]["shots"]
        self.assertEqual((first["at"], first["seconds"]), (0.0, 9.5))
        self.assertEqual(first["planned_seconds"], 6)
        self.assertEqual(first["timed_by"], "voice")
        self.assertEqual((second["at"], second["seconds"]), (9.5, 4))
        self.assertEqual(second["timed_by"], "script")
        self.assertEqual(out[0]["seconds"], 13.5)
        self.assertEqual(SECTIONS[0]["shots"][0]["seconds"], 6)   # the input is untouched

    def test_without_takes_nothing_changes(self):
        out = vfx.measured_sections(SECTIONS, {})
        self.assertEqual([s["seconds"] for s in out[0]["shots"]], [6, 4])
        self.assertEqual(out[0]["seconds"], 10)

    def test_the_world_spans_the_measured_section(self):
        worlds = vfx.world_symbols(vfx.measured_sections(SECTIONS, WORDS)[0], [])
        self.assertEqual(worlds[0]["seconds"], 13.5)
        self.assertEqual([(s["at"], s["seconds"]) for s in worlds[0]["shots"]],
                         [(0.0, 9.5), (9.5, 4.0)])

    def test_the_brief_states_the_recorded_length_and_the_plan_it_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = vfx.Workspace(Path(tmp))
            section = vfx.measured_sections(SECTIONS, WORDS)[0]
            path = ws.brief(1, idea=Idea(title="T", genre="g", description="d"),
                            house_rules="", style={"look": "", "motion_rules": []},
                            tokens={}, frame=(1920, 1080), section=section,
                            effects=[], words=WORDS, assets=[])
            text = path.read_text(encoding="utf-8")
            self.assertIn("#### 1.1 · at 0s · 9.5s", text)
            self.assertIn("recorded", text)
            self.assertIn("planned 6s", text)
            self.assertIn('duration="13.5"', text)


DOC = """<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.3">
  <project width="1920" height="1080" fps="30" duration="10" background="#000000"/>
  <output path="p.mp4" codec="h264"/>
  <symbols>
    <symbol id="world-open" width="1920" height="1080" duration="10">
      <object3D id="hero-cube" primitive="box" width="10" height="10" depth="10">
        <animate property="x"><key time="0" value="0"/><key time="10" value="100"/></animate>
      </object3D>
    </symbol>
  </symbols>
  <composition><instance id="u" symbol="world-open" start="0" end="10"/></composition>
</scene>
"""


class TimingFindings(unittest.TestCase):
    def test_a_symbol_shorter_than_its_shots_is_an_error(self):
        found = vfx.timing_findings(DOC, {"world-open": 13.92})
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("error"))
        self.assertIn("world-open", found[0])
        self.assertIn("10s", found[0])
        self.assertIn("13.92s", found[0])

    def test_a_symbol_that_spans_its_shots_is_fine(self):
        self.assertEqual(vfx.timing_findings(DOC, {"world-open": 10.0}), [])
        self.assertEqual(vfx.timing_findings(DOC, {"world-open": 9.8}), [])

    def test_animation_that_stops_long_before_the_end_is_a_warning(self):
        doc = DOC.replace('duration="10">', 'duration="14">')
        found = vfx.timing_findings(doc, {"world-open": 14.0})
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("warning"))
        self.assertIn("10s", found[0])

    def test_symbols_nobody_asked_about_are_ignored(self):
        self.assertEqual(vfx.timing_findings(DOC, {"other": 20.0}), [])

    def test_validate_reports_timing_against_the_shots_it_is_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = vfx.Workspace(Path(tmp))
            ws.dir.mkdir(parents=True, exist_ok=True)
            (ws.dir / "01-open.scene.xml").write_text(DOC, encoding="utf-8")
            _, findings = ws.validate(
                "01-open.scene.xml", frames=False,
                shots={"world-open": [{"label": "1.1", "at": 0.0, "seconds": 13.92}]})
            self.assertTrue(any(f.startswith("error") and "13.92s" in f for f in findings))


def _idea():
    return Idea(title="A film", genre="explainer", description="about a thing")


class BuildRefusesAShortSymbol(unittest.TestCase):
    SHORT = """<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.1">
  <project width="1920" height="1080" fps="30" duration="2"/>
  <symbols><symbol id="fx-1-1" duration="2"/></symbols>
</scene>
"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "vfx").mkdir()
        (self.root / "vfx" / "open.scene.xml").write_text(self.SHORT)
        self.vfx = {"scene": {"by_label": {
            "1.1": {"file": "vfx/open.scene.xml", "symbol": "fx-1-1"}}}}
        self.sections = [Section(heading="Open", shots=[
            Shot(label="1.1", n=1, visual="a thing", narration="Hello there",
                 seconds=4, at=0)])]
        self.voice = {"takes": [{"label": "1.1", "at": 0.0, "seconds": 5.5,
                                 "file": "001.wav", "words": []}]}

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_brief_tells_the_editor_how_long_the_symbol_is(self):
        rows = build.SceneBuilder(_idea(), self.sections, self.root, vfx=self.vfx,
                                  voice=self.voice, audio=False, verbose=False).brief()["shots"]
        self.assertEqual(rows[0]["seconds"], 5.5)
        self.assertEqual(rows[0]["symbol_seconds"], 2.0)

    def test_the_build_names_the_mismatch_and_is_not_publishable(self):
        edit = {"coverage": [], "captions_off": [], "tail_seconds": 0.5, "notes": "",
                "blocking": []}
        _, manifest = build.SceneBuilder(
            _idea(), self.sections, self.root, vfx=self.vfx, voice=self.voice,
            edit=edit, audio=False, verbose=False).build()
        errors = [f for f in manifest["findings"] if f.startswith("error")]
        self.assertEqual(len(errors), 1)
        self.assertIn("1.1", errors[0])
        self.assertIn("5.5s", errors[0])
        self.assertIn("2s", errors[0])
        self.assertIn("retime", errors[0])
        self.assertFalse(manifest["publishable"])


class RunOrder(unittest.TestCase):
    def test_retime_runs_between_voice_and_vfx(self):
        order = list(cli.PIPELINE)
        self.assertEqual(order.index("retime"), order.index("voice") + 1)
        # only the look (the lead section, timed to the measured read) sits between
        self.assertEqual(order.index("keyframe"), order.index("retime") + 1)
        self.assertEqual(order.index("vfx"), order.index("keyframe") + 1)
        self.assertEqual(workspace.Project(Path("/x")).path("retime").name, "retime.json")


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- #
# the run settles the clock before anything is authored against it
# --------------------------------------------------------------------------- #

import argparse  # noqa: E402
import json  # noqa: E402
from ytstudio import retime, sound  # noqa: E402
from ytstudio.core import write_json  # noqa: E402


class RetimeSettles(unittest.TestCase):
    def test_unsettled_names_the_shots_to_record_or_rewrite_again(self):
        report = {"decisions": [
            {"label": "1.1", "action": "rerecord", "note": "too slow"},
            {"label": "1.2", "action": "accept", "note": ""},
            {"label": "2.1", "action": "rewrite", "note": "too long"},
            {"label": "2.2", "action": "keep_planned", "note": ""}]}
        self.assertEqual(retime.unsettled(report), {"rerecord": ["1.1"], "rewrite": ["2.1"]})
        self.assertEqual(retime.unsettled({"decisions": [
            {"label": "1.1", "action": "accept", "note": ""}]}), {})
        self.assertIsInstance(retime.UNSETTLED, int)
        self.assertNotIn(retime.UNSETTLED, (0, 1))

    def _project(self, tmp: Path, decisions: list[dict]) -> workspace.Project:
        project = workspace.Project(tmp)
        write_json(tmp / "retime.json", {"decisions": decisions})
        return project

    def test_a_rerecord_decision_records_again_and_retimes_once_more(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp), [
                {"label": "1.1", "action": "rerecord", "note": "brisker"}])
            calls = []

            def run_stage(stage, extra):
                calls.append((stage, list(extra)))
                if stage == "retime":   # the second pass accepts
                    write_json(Path(tmp) / "retime.json", {"decisions": [
                        {"label": "1.1", "action": "accept", "note": ""}]})
                return 0
            code = cli.settle_retime(retime.UNSETTLED, project, run_stage, say=lambda m: None)
            self.assertEqual(code, 0)
            self.assertEqual(calls, [("voice", ["--redo", "1.1"]), ("retime", [])])

    def test_a_second_rerecord_stops_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp), [
                {"label": "1.1", "action": "rerecord", "note": "still slow"}])
            calls = []

            def run_stage(stage, extra):
                calls.append(stage)
                return retime.UNSETTLED if stage == "retime" else 0
            code = cli.settle_retime(retime.UNSETTLED, project, run_stage, say=lambda m: None)
            self.assertEqual(code, retime.UNSETTLED)
            self.assertEqual(calls, ["voice", "retime"])

    def test_a_rewrite_decision_stops_at_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp), [
                {"label": "1.1", "action": "rewrite", "note": "too long"}])
            calls = []
            code = cli.settle_retime(retime.UNSETTLED, project, lambda s, e: calls.append(s) or 0,
                                     say=lambda m: None)
            self.assertEqual(code, retime.UNSETTLED)
            self.assertEqual(calls, [])

    def test_a_settled_retime_passes_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp), [])
            self.assertEqual(cli.settle_retime(0, project, lambda s, e: 1, say=lambda m: None), 0)


class RedoVfxIsFresh(unittest.TestCase):
    def test_redoing_vfx_authors_again_instead_of_replaying_the_cache(self):
        args = argparse.Namespace(quick=True, minutes=1.0)
        self.assertIn("--fresh", cli.run_extras(args, redo={"vfx"})["vfx"])
        self.assertIn("--fresh", cli.run_extras(args, redo={"all"})["vfx"])
        self.assertNotIn("--fresh", cli.run_extras(args, redo=set())["vfx"])


class SoundOnTheMeasuredClock(unittest.TestCase):
    SCRIPT = {"sections": [{"heading": "Open", "seconds": 10, "shots": [
        {"label": "1.1", "at": 0, "seconds": 6, "narration": "One cube."},
        {"label": "1.2", "at": 6, "seconds": 4, "narration": ""}]}]}
    VOICE = {"takes": [{"label": "1.1", "at": 0.0, "seconds": 9.5, "file": "a.wav"}]}

    def test_sections_take_the_takes_lengths(self):
        sections = sound.on_measured_clock(json.loads(json.dumps(self.SCRIPT)), self.VOICE)
        self.assertEqual([(s.label, s.at, s.seconds) for s in sections[0].shots],
                         [("1.1", 0, 10), ("1.2", 10, 4)])
        self.assertEqual(sections[0].actual_seconds, 14)

    def test_without_narration_the_plan_stands(self):
        sections = sound.on_measured_clock(json.loads(json.dumps(self.SCRIPT)), None)
        self.assertEqual([s.seconds for s in sections[0].shots], [6, 4])


class RetimeSeesEachRead(unittest.TestCase):
    SCRIPT = {"sections": [{"heading": "Open", "seconds": 10, "shots": [
        {"label": "1.1", "at": 0, "seconds": 10, "narration": "One cube."}]}]}

    def voice(self, seconds, file):
        return {"takes": [{"label": "1.1", "at": 0.0, "seconds": seconds, "file": file}]}

    def test_a_new_recording_is_a_new_question_even_when_it_rounds_the_same(self):
        first, _ = retime.plan(self.SCRIPT, self.voice(13.92, "001-1-1.wav"))
        second, _ = retime.plan(self.SCRIPT, self.voice(13.6, "001-1-1.wav"))
        self.assertEqual((first[0]["from"], first[0]["to"]), (10, 14))
        self.assertEqual(second[0]["to"], 14)
        self.assertNotEqual(first[0]["measured"], second[0]["measured"])
        self.assertEqual(second[0]["measured"], 13.6)

    def test_the_editor_is_told_about_the_earlier_round(self):
        earlier = {"decisions": [{"label": "1.1", "action": "rerecord", "note": "brisker"}],
                   "changes": [{"label": "1.1", "measured": 13.92}]}
        text = retime.reconcile_prompt(
            rows=[{"label": "1.1", "from": 10, "to": 14, "measured": 13.6}],
            stale=[], totals={}, earlier=earlier)
        self.assertIn("earlier round", text)
        self.assertIn("rerecord", text)
        self.assertIn("13.92", text)
        self.assertIn("13.6", text)
        self.assertIn("recorded again at the editor's request", text)
        self.assertNotIn("earlier round", retime.reconcile_prompt(
            rows=[{"label": "1.1", "from": 10, "to": 14, "measured": 13.6}],
            stale=[], totals={}, earlier=None))
