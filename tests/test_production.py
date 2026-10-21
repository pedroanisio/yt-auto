"""The production stages decide through the harness; this pins what the
deterministic half does with those decisions.

build: the model says what happens to a shot nobody drew, and the builder
applies it -- an effect stretched over the gap, a licensed image, or an
honest block. pilot: the model chooses the cuts and the validator holds them
to the budget and the nesting rule. retime: only the shots the model accepted
take their measured length.
"""

import json
import tempfile
import unittest
from pathlib import Path

from ytstudio import build, pilot, retime
from ytstudio.core import Idea
from ytstudio.script import Section, Shot


def _idea():
    return Idea.from_raw({"title": "A film", "genre": "explainer",
                          "description": "a premise"})


def _sections():
    return [Section(heading="Open", shots=[
                Shot(label="1.1", n=1, visual="a thing appears", narration="Hello there",
                     seconds=4, at=0),
                Shot(label="1.2", n=2, visual="it turns", narration="and it turns",
                     seconds=3, at=4)]),
            Section(heading="Close", shots=[
                Shot(label="2.1", n=3, visual="the end card", narration="goodbye",
                     on_screen="THE END", seconds=3, at=7)])]


class BuildApplies(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "vfx").mkdir()
        (self.root / "vfx" / "open.scene.xml").write_text("<scene/>")
        self.vfx = {"scene": {"by_label": {
            "1.1": {"file": "vfx/open.scene.xml", "symbol": "fx-1-1"}}}}

    def tearDown(self):
        self._tmp.cleanup()

    def builder(self, edit=None):
        return build.SceneBuilder(_idea(), _sections(), self.root, vfx=self.vfx,
                                  edit=edit, verbose=False, audio=False)

    def test_the_brief_names_what_is_drawn_and_what_is_not(self):
        brief = self.builder().brief()
        drawn = {s["label"]: s["drawn_by"] for s in brief["shots"]}
        self.assertEqual(drawn, {"1.1": "effect", "1.2": "none", "2.1": "none"})
        self.assertEqual(brief["uncovered"], ["1.2", "2.1"])
        self.assertIn("design", brief)

    def test_without_a_decision_every_uncovered_shot_is_a_placeholder(self):
        _, manifest = self.builder().build()
        self.assertEqual(manifest["placeholders"], ["1.2", "2.1"])
        self.assertFalse(manifest["publishable"])

    def test_extend_previous_stretches_the_effect_over_the_gap(self):
        edit = {"coverage": [{"label": "1.2", "action": "extend_previous",
                              "reason": "same motif, keep it turning"},
                             {"label": "2.1", "action": "block",
                              "reason": "an end card needs its own art"}],
                "captions_off": [], "tail_seconds": 1.5, "notes": "", "blocking": []}
        data, manifest = self.builder(edit).build()
        self.assertEqual(manifest["placeholders"], ["2.1"])
        shots = {s["label"]: s for s in manifest["shots"]}
        self.assertEqual(shots["1.2"]["asset"], "effect")
        self.assertIn("1.1", shots["1.2"]["file"])
        # one include, running from the first shot's start to the second's end
        text = data.decode()
        self.assertEqual(text.count("<include"), 1)
        self.assertIn('start="0" end="7"', text)
        self.assertEqual(manifest["scene"]["duration"], 10 + 1.5)
        self.assertEqual(manifest["editorial"]["applied"]["1.2"], "extend_previous")

    def test_an_image_the_model_chose_must_exist_or_it_is_a_block(self):
        edit = {"coverage": [{"label": "1.2", "action": "image",
                              "image": "raw/nothing.png", "reason": "x"}],
                "captions_off": [], "tail_seconds": 0.5, "notes": "", "blocking": []}
        _, manifest = self.builder(edit).build()
        self.assertIn("1.2", manifest["placeholders"])
        self.assertTrue(any("nothing.png" in f for f in manifest["findings"]))

    def test_captions_are_a_sidecar_track_unless_asked_to_burn(self):
        # measured on the Euler draft: burned captions printed over the authored
        # tallies, formulas and name labels in four sections
        from xml.etree import ElementTree
        edit = {"coverage": [], "captions_off": [], "tail_seconds": 0.5, "notes": "",
                "blocking": []}
        data, _ = self.builder(edit).build()
        track = ElementTree.fromstring(data).find("captions/captionTrack")
        self.assertEqual(track.get("mode"), "sidecar")
        burned = build.SceneBuilder(_idea(), _sections(), self.root, vfx=self.vfx, edit=edit,
                                    verbose=False, audio=False, burn_captions=True)
        data, _ = burned.build()
        self.assertEqual(ElementTree.fromstring(data).find("captions/captionTrack").get("mode"),
                         "burn")

    def test_captions_off_and_blocking_reach_the_manifest(self):
        edit = {"coverage": [], "captions_off": ["2.1"], "tail_seconds": 0.5,
                "notes": "the end card carries its own words",
                "blocking": ["2.1 has a super the narration never says"]}
        _, manifest = self.builder(edit).build()
        self.assertEqual(manifest["cues"], 2)
        self.assertFalse(manifest["publishable"])
        self.assertTrue(any("never says" in f for f in manifest["findings"]))
        self.assertEqual(manifest["editorial"]["notes"],
                         "the end card carries its own words")


def _beats():
    rows = [("1.1", 0.0, 4.0, "Open", {"hook", "voice"}),
            ("1.2", 4.0, 3.0, "Open", {"voice", "fx:turn"}),
            ("2.1", 7.0, 5.0, "Middle", {"voice", "cue:music"}),
            ("2.2", 12.0, 6.0, "Middle", {"voice", "super"}),
            ("3.1", 18.0, 4.0, "Close", {"payoff", "voice"})]
    return [pilot.Beat(label=l, at=a, seconds=s, section=sec, traits=t)
            for l, a, s, sec, t in rows]


class PilotSelection(unittest.TestCase):
    def test_a_valid_selection_has_no_violations(self):
        chosen = {10: ["1.1", "3.1"], 20: ["1.1", "1.2", "2.1", "3.1"]}
        self.assertEqual(pilot.validate(_beats(), chosen, [10, 20]), [])

    def test_violations_are_named(self):
        chosen = {10: ["1.1", "2.2", "9.9"], 20: ["1.2", "2.1"]}
        found = " ".join(pilot.validate(_beats(), chosen, [10, 20]))
        self.assertIn("9.9", found)                    # unknown shot
        self.assertIn("10s", found)                    # over budget
        self.assertIn("not inside", found)             # nesting broken

    def test_a_cut_from_labels_has_the_same_shape_as_the_coverage_cut(self):
        beats = _beats()
        ranked = pilot.rank(beats)
        auto = pilot.cut(beats, ranked, 10.0)
        chosen = pilot.cut_from(beats, ["1.1", "3.1"], 10.0)
        self.assertEqual(set(auto), set(chosen))
        self.assertEqual(chosen["shots"], ["1.1", "3.1"])
        self.assertEqual(chosen["seconds"], 8.0)
        self.assertEqual(chosen["cuts"], 1)
        self.assertIn("hook", chosen["proves"])
        self.assertIn("super", chosen["does_not_prove"])


class RetimeAccepts(unittest.TestCase):
    def setUp(self):
        self.script = {"sections": [{"heading": "Open", "shots": [
            {"label": "1.1", "seconds": 4, "at": 0},
            {"label": "1.2", "seconds": 3, "at": 4}]}]}
        self.voice = {"takes": [{"label": "1.1", "seconds": 6.2},
                                {"label": "1.2", "seconds": 1.1}]}

    def test_only_accepted_shots_take_their_measured_length(self):
        out = retime.apply(json.loads(json.dumps(self.script)), self.voice,
                           accepted={"1.1"})
        shots = out["sections"][0]["shots"]
        self.assertEqual([s["seconds"] for s in shots], [6, 3])
        self.assertEqual([s["at"] for s in shots], [0, 6])

    def test_no_filter_means_every_measured_shot(self):
        out = retime.apply(json.loads(json.dumps(self.script)), self.voice)
        self.assertEqual([s["seconds"] for s in out["sections"][0]["shots"]], [6, 1])


if __name__ == "__main__":
    unittest.main()


class VoiceCasting(unittest.TestCase):
    def test_candidates_are_per_engine_and_language(self):
        from ytstudio import voice
        kokoro = voice.candidates("kokoro", "p")
        self.assertTrue(kokoro and all(v["id"].startswith("p") for v in kokoro))
        self.assertEqual(voice.candidates("stub", "en"), [])

    def test_a_cast_voice_must_be_a_candidate_to_be_used(self):
        from ytstudio import voice
        cast = {"voice": "pm_alex", "speed": 0.95, "why": "warm, unhurried",
                "settings": {"stability": 0.5}}
        chosen, opts = voice.apply_cast(cast, voice.candidates("kokoro", "p"),
                                        engine="kokoro", forced="")
        self.assertEqual(chosen, "pm_alex")
        self.assertEqual(opts["speed"], 0.95)
        self.assertNotIn("settings", opts)          # not an ElevenLabs knob
        chosen, _ = voice.apply_cast(cast | {"voice": "nobody"},
                                     voice.candidates("kokoro", "p"),
                                     engine="kokoro", forced="")
        self.assertEqual(chosen, "")
        chosen, _ = voice.apply_cast(cast, [], engine="kokoro", forced="pf_dora")
        self.assertEqual(chosen, "pf_dora")        # --voice always wins

    def test_speed_is_clamped_to_what_a_listener_accepts(self):
        from ytstudio import voice
        _, opts = voice.apply_cast({"voice": "", "speed": 3.0, "why": ""}, [],
                                   engine="kokoro", forced="")
        self.assertEqual(opts["speed"], voice.SPEED_RANGE[1])


class GateTriage(unittest.TestCase):
    def test_blocking_items_fail_the_gate_even_when_the_tool_passed(self):
        from ytstudio import gate
        self.assertEqual(gate.exit_code(0, {"blocking": [], "acceptable": []}), 0)
        self.assertEqual(gate.exit_code(0, {"blocking": [{"what": "x"}]}), 1)
        self.assertEqual(gate.exit_code(3, {"blocking": []}), 3)


class BuildHoists(unittest.TestCase):
    """An <include> carries neither the included document's lights nor its
    styles (engine warning E12), so the builder lifts them into the scene."""

    SYMBOL_DOC = """<?xml version="1.0"?>
<scene version="1.1">
  <project width="1920" height="1080" fps="30" duration="4"/>
  <styles>
    <token name="rim-white" value="#F4F8FB"/>
    <token name="background" value="#000001"/>
  </styles>
  <symbols><symbol id="fx-1-1"/></symbols>
  <lights>
    <light id="key" type="rect-area" color="#FFD9A0" intensity="8" x="-900" width="900" height="900"/>
    <light id="sky" type="dome" sky="gradient"/>
  </lights>
</scene>
"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "vfx").mkdir()
        (self.root / "vfx" / "open.scene.xml").write_text(self.SYMBOL_DOC)
        self.vfx = {"scene": {"by_label": {
            "1.1": {"file": "vfx/open.scene.xml", "symbol": "fx-1-1"}}}}

    def tearDown(self):
        self._tmp.cleanup()

    def test_lights_are_lifted_and_gated_to_the_include_span(self):
        edit = {"coverage": [{"label": "1.2", "action": "extend_previous", "reason": ""},
                             {"label": "2.1", "action": "block", "reason": ""}],
                "captions_off": [], "tail_seconds": 0.5, "notes": "", "blocking": []}
        data, manifest = build.SceneBuilder(
            _idea(), _sections(), self.root, vfx=self.vfx, edit=edit,
            audio=False, verbose=False).build()
        text = data.decode()
        self.assertIn('<light id="shot-1.1-key" type="rect-area"', text)
        self.assertIn('<light id="shot-1.1-sky" type="dome"', text)
        # on for [0, 7), off after; a light with no intensity of its own is 1
        self.assertIn('<key time="0" value="8"/>', text)
        self.assertIn('<key time="7" value="0"/>', text)
        self.assertIn('<key time="0" value="1"/>', text)
        self.assertEqual(manifest["hoisted"]["lights"], 2)
        self.assertFalse([f for f in manifest["findings"] if f.startswith("error")])

    def test_tokens_are_lifted_and_the_authored_file_wins_a_conflict(self):
        data, manifest = build.SceneBuilder(
            _idea(), _sections(), self.root, vfx=self.vfx,
            styles={"styles": [{"name": "x", "palette": [
                {"hex": "#101010", "use": "background"}]}]},
            audio=False, verbose=False).build()
        text = data.decode()
        self.assertIn('<token name="rim-white" value="#F4F8FB"/>', text)
        # the symbols were composed against the file's value, so it stands and
        # the design only fills the roles no file declares
        self.assertIn('<token name="background" value="#000001"/>', text)
        self.assertNotIn('<token name="background" value="#101010"/>', text)
        self.assertIn('<token name="ink"', text)
        self.assertFalse(any("token" in f for f in manifest["findings"]))
        # the design's eight role tokens plus the one the file adds; the
        # `background` both declare is not counted twice
        self.assertEqual(manifest["hoisted"]["tokens"], 9)


class ElevenLabsCasting(unittest.TestCase):
    """ElevenLabs has no default voice: the candidates come from the operator
    or from the account, and a recording never goes out without an id."""

    VOICES = {"voices": [
        {"voice_id": "v-ana", "name": "Ana", "category": "premade",
         "labels": {"accent": "brazilian", "gender": "female", "age": "young",
                    "description": "warm", "use_case": "narration"}},
        {"voice_id": "v-joao", "name": "João", "category": "cloned", "labels": {}}]}

    class FakeKit:
        calls: list = []
        fail = False

        @staticmethod
        def api_key():
            if ElevenLabsCasting.FakeKit.fail:
                raise RuntimeError("no key")
            return "k"

        class Client:
            def __init__(self, key):
                pass

            def get_json(self, path, query=None):
                ElevenLabsCasting.FakeKit.calls.append((path, query))
                return ElevenLabsCasting.VOICES

    def setUp(self):
        from unittest import mock
        from ytstudio import voice
        self.FakeKit.calls, self.FakeKit.fail = [], False
        self._kit = mock.patch.object(voice, "_kit", return_value=self.FakeKit)
        self._kit.start()
        self._env = mock.patch.dict("os.environ", {})
        self._env.start()
        import os
        os.environ.pop("ELEVENLABS_VOICES", None)

    def tearDown(self):
        self._env.stop()
        self._kit.stop()

    def test_the_operators_list_wins_when_set(self):
        import os
        from ytstudio import voice
        os.environ["ELEVENLABS_VOICES"] = "v-1=Nora, v-2"
        self.assertEqual(voice.candidates("elevenlabs", "pt"),
                         [{"id": "v-1", "about": "Nora"}, {"id": "v-2", "about": ""}])
        self.assertEqual(self.FakeKit.calls, [])

    def test_otherwise_the_accounts_voices_are_the_candidates(self):
        from ytstudio import voice
        offered = voice.candidates("elevenlabs", "pt")
        self.assertEqual([v["id"] for v in offered], ["v-ana", "v-joao"])
        self.assertIn("Ana", offered[0]["about"])
        self.assertIn("brazilian", offered[0]["about"])
        self.assertIn("narration", offered[0]["about"])
        self.assertEqual(self.FakeKit.calls[0][0], "/v2/voices")

    def test_an_unreachable_account_offers_nothing_and_does_not_raise(self):
        from ytstudio import voice
        self.FakeKit.fail = True
        self.assertEqual(voice.candidates("elevenlabs", "pt"), [])

    def test_a_recording_without_a_voice_id_is_refused_before_any_request(self):
        from ytstudio import voice
        with self.assertRaises(voice.CastingError) as caught:
            voice.require_voice("elevenlabs", "", [])
        self.assertIn("--voice", str(caught.exception))
        self.assertIn("ELEVENLABS_VOICES", str(caught.exception))
        with self.assertRaises(voice.CastingError) as caught:
            voice.require_voice("elevenlabs", "", [{"id": "v-ana", "about": "Ana"}])
        self.assertIn("v-ana", str(caught.exception))     # the choices it had
        voice.require_voice("elevenlabs", "v-ana", [])     # fine
        voice.require_voice("kokoro", "", [])              # engines with a default are fine

    def test_the_casting_instruction_says_elevenlabs_has_no_default(self):
        from ytstudio import voice
        text = voice.cast_instruction("elevenlabs", "pt")
        self.assertIn("no default", text)
        self.assertNotIn("empty keeps", text)
        self.assertIn("empty keeps", voice.cast_instruction("kokoro", "pt"))
