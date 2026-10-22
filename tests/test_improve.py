"""improve.py: QA's findings go back to the stages that own them.

`yt qa` names an owner for every finding; nothing routed them back, so the
same defects were reported pass after pass. `yt improve` is docs/production.md
§8 as code: it routes each finding to one stage (the mechanical kinds by
their stated owner, the prose ones by a model decision under a schema),
writes each stage a revision brief under `feedback/`, which the harness puts
in front of every decision that stage makes, re-runs the earliest routed
stage and everything downstream through a runner seam, scores a new QA pass,
and keeps it only when it measurably improved (§8.8, §9) -- restoring the
snapshot otherwise. These tests use no model, no network and no ssh: every
harness call is a FakeEngine and every stage is a fake runner that writes
the files a stage would.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from claude_engine import Usage  # noqa: E402
from ytstudio import (build, cli, harness, improve, keyframe, qa, script, sound,  # noqa: E402
                      styles, vfx, workspace)
from ytstudio.core import Idea, read_json, write_json  # noqa: E402

ROOT = HERE.parent


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #

class FakeEngine:
    """Every harness call: records the system prompt, answers by schema."""

    json_calls: list = []
    route = None          # callable(prompt) -> routing answer
    same = None           # callable(prompt) -> same-defect answer

    def __init__(self, config):
        self.config = config
        self.usage = Usage()
        self.usage.cost_usd = 0.01
        self.usage.calls = 1

    def ask_json(self, prompt, schema, system=None):
        FakeEngine.json_calls.append({"prompt": prompt, "schema": schema, "system": system})
        props = schema.get("properties", {})
        if "routes" in props:
            return (FakeEngine.route or route_all_to("vfx"))(prompt)
        if "matches" in props:
            return (FakeEngine.same or (lambda p: {"matches": []}))(prompt)
        return {k: "" for k in schema.get("required", [])}

    def stream(self, prompt, system=None):
        FakeEngine.json_calls.append({"prompt": prompt, "system": system})
        return iter(())


def finding_ids(prompt: str) -> list[str]:
    return re.findall(r'"id": "(F\d+)"', prompt)


def route_all_to(stage: str, *, brief=True):
    def answer(prompt):
        ids = finding_ids(prompt)
        return {"routes": [{"finding": i, "stage": stage, "why": "owns it"} for i in ids],
                "briefs": ([{"stage": stage, "must_change": [f"fix what {stage} owns"],
                             "check": "the next pass no longer shows it"}] if brief else [])}
    return answer


def mech(kind="penetration", shot="1.4", objects=("hero-x", "table-top"), t=13.0,
         owner="vfx", severity="blocking"):
    a, b = objects
    return {"timestamp": t, "frame": f"t{t}.png", "shot": shot, "owner": owner,
            "kind": kind, "severity": severity,
            "problem": f"hero penetrates the set: `{a}` inside `{b}`, up to 4.0 units",
            "why": "a hero inside a solid reads as a merge", "root_cause": "the keyed path",
            "fix": "raise the keyed path"}


def prose(problem, *, t=5.0, owner="vfx", severity="blocking"):
    return {"timestamp": t, "frame": f"t{t}.png", "problem": problem, "why": "it reads flat",
            "root_cause": "the light rig", "owner": owner, "fix": "light it", "severity": severity}


def card(overall, *, scores=None, mechanical=(), findings=(), verdict=None, cost=0.1):
    s = {k: overall for k in qa.CATEGORIES}
    s.update(scores or {})
    return {"scores": s, "overall": overall,
            "verdict": verdict or (qa.READY if overall >= 9.0 else qa.NOT_READY),
            "findings": list(findings), "mechanical": list(mechanical),
            "blocking": [f["problem"] for f in list(findings) + list(mechanical)
                         if f.get("severity") == "blocking"],
            "bar": [] if overall >= 9.0 else [f"overall {overall} is under 9.0"],
            "rationale": "because", "engine": {"cost_usd": cost}}


class FakeRunner:
    """Stands in for `yt <stage>`: records argv, writes what the stage would."""

    def __init__(self, root: Path, cards=(), *, costs=None, fail=None):
        self.root = root
        self.cards = list(cards)
        self.costs = costs or {}
        self.fail = fail or {}
        self.calls: list[list[str]] = []
        self.gate_at_qa = "qa never ran"

    def __call__(self, argv):
        self.calls.append(list(argv))
        stage = argv[0]
        cost = self.costs.get(stage, 0.0)
        if stage == "gate" and self.fail.get("gate") == "refuse":
            return 1                       # preflight refused: nothing written
        if stage == "qa":
            self.gate_at_qa = read_json(self.root / "gate.json", warn=False)
        if stage == "qa" and self.fail.get("qa") == "no-pass":
            return 1                       # the render host was down: no pass filed
        if stage == "qa":
            if not self.cards:
                raise AssertionError("qa ran more often than the test scripted")
            c = dict(self.cards.pop(0))
            pdir = qa.next_pass(self.root)
            pdir.mkdir(parents=True)
            c["pass"] = pdir.name
            write_json(pdir / "scorecard.json", c)
            write_json(self.root / "qa.json", {"pass": pdir.name, "verdict": c["verdict"],
                                               "overall": c["overall"]})
            return 0 if c["verdict"] == qa.READY else 1
        if stage == "vfx":
            data = read_json(self.root / "vfx.json", warn=False) or {}
            versions = list(data.get("authoring", {}).get("versions", []))
            new = f"v{len(versions) + 1:02d}"
            data["authoring"] = {"version": new, "versions": versions + [new]}
            data["usage"] = {"cost_usd": cost}
            write_json(self.root / "vfx.json", data)
        elif stage == "sound":
            write_json(self.root / "sound.json", {"cues": [], "usage": {"cost_usd": cost},
                                                  "run": len(self.calls)})
        elif stage == "build":
            (self.root / "scene.xml").write_text(f"<scene n='{len(self.calls)}'/>")
            write_json(self.root / "build.json", {"engine": {"cost_usd": cost},
                                                  "scene": {"duration": 15, "width": 1920,
                                                            "height": 1080, "fps": 30},
                                                  "audio_tracks": 3})
        elif stage == "render":
            (self.root / "video.mp4").write_bytes(f"video {len(self.calls)}".encode())
        elif stage == "gate":
            write_json(self.root / "gate.json", {"exit": 0, "engine": {"cost_usd": cost}})
        elif stage == "keyframe":
            write_json(self.root / "keyframe.json", {"cost_usd": cost})
        return self.fail.get(stage, 0)

    def stages(self):
        return [c[0] for c in self.calls]


class ImproveCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name) / "work"
        self.root = self.work / "a-test-film"
        self.root.mkdir(parents=True)
        self.idea = Idea(title="A test film", genre="cinematic", description="one take")
        write_json(self.root / "idea.json", {"title": self.idea.title, "genre": "cinematic",
                                             "description": "one take"})
        write_json(self.root / "script.json", {"target_seconds": 15, "sections": []})
        write_json(self.root / "styles.json", {"styles": []})
        write_json(self.root / "vfx.json", {"authoring": {"version": "v01",
                                                          "versions": ["v01"]}})
        write_json(self.root / "sound.json", {"cues": [], "original": True})
        write_json(self.root / "build.json", {"scene": {"duration": 15, "width": 1920,
                                                        "height": 1080, "fps": 30},
                                              "audio_tracks": 3})
        (self.root / "scene.xml").write_text("<scene original='1'/>")
        (self.root / "video.mp4").write_bytes(b"original video")
        old = 1_000_000_000
        for name in ("scene.xml", "video.mp4"):
            os.utime(self.root / name, (old, old))
        self._engine = harness.ENGINE
        harness.ENGINE = FakeEngine
        FakeEngine.json_calls, FakeEngine.route, FakeEngine.same = [], None, None

    def tearDown(self):
        harness.ENGINE = self._engine
        self._tmp.cleanup()

    def start_pass(self, c, name="pass-01"):
        pdir = self.root / "qa" / name
        pdir.mkdir(parents=True, exist_ok=True)
        write_json(pdir / "scorecard.json", dict(c, **{"pass": name}))
        write_json(self.root / "qa.json", {"pass": name, "verdict": c["verdict"],
                                           "overall": c["overall"]})
        return pdir

    def improver(self, runner, **kw):
        options = dict(model="sonnet", quiet=True, runner=runner, doctor=lambda p: [],
                       say=lambda msg: None)
        options.update(kw)
        return improve.Improver(workspace.Project(self.root), self.idea, **options)


# --------------------------------------------------------------------------- #
# routing
# --------------------------------------------------------------------------- #

class Routing(ImproveCase):
    def test_mechanical_kinds_route_to_their_owner_without_the_model(self):
        findings = improve.collect(card(5.0, mechanical=[mech(owner="vfx"),
                                                          mech("flat", "1.1", owner="build")]))
        # The model tries to send both elsewhere; the stated owner wins.
        FakeEngine.route = route_all_to("sound")
        h = harness.Harness(self.root, "improve", verbose=False)
        routing = improve.route(h, findings, name="route-pass-01")
        self.assertEqual(routing["routes"]["F1"]["stage"], "vfx")
        self.assertEqual(routing["routes"]["F2"]["stage"], "build")
        self.assertEqual(routing["routes"]["F1"]["by"], "mechanical")
        self.assertEqual(set(routing["stages"]), {"vfx", "build"})

    def test_every_mechanical_kind_is_known(self):
        for kind in ("penetration", "reads-merged", "flat", "clipped", "frozen", "jump",
                     "off_camera"):
            self.assertIn(kind, improve.MECHANICAL_KINDS)

    def test_prose_findings_are_routed_by_the_model(self):
        findings = improve.collect(card(5.0, findings=[
            prose("the hero reads flat under the key", owner="scene-render lighting"),
            prose("the pad is still playing at the touch", owner="audio")]))

        def answer(prompt):
            return {"routes": [{"finding": "F1", "stage": "vfx", "why": "the light rig"},
                               {"finding": "F2", "stage": "sound", "why": "the cue sheet"}],
                    "briefs": [{"stage": "vfx", "must_change": ["key the hero"],
                                "check": "the hero holds its material"},
                               {"stage": "sound", "must_change": ["fade by 11.3"],
                                "check": "no music at the touch"}]}

        FakeEngine.route = answer
        h = harness.Harness(self.root, "improve", verbose=False)
        routing = improve.route(h, findings, name="route-pass-01")
        self.assertEqual(routing["routes"]["F1"]["stage"], "vfx")
        self.assertEqual(routing["routes"]["F2"]["stage"], "sound")
        self.assertEqual(routing["stages"]["sound"]["must_change"], ["fade by 11.3"])
        prompt = FakeEngine.json_calls[0]["prompt"]
        self.assertIn("scene-render lighting", prompt)        # the free-text owner is shown
        for stage, _ in workspace.STAGES:
            self.assertIn(stage, json.dumps(FakeEngine.json_calls[0]["schema"]))

    def test_duplicates_merge_into_the_primary_findings_brief(self):
        findings = improve.collect(card(5.0, mechanical=[mech()], findings=[
            prose("the hero sinks into the table at 13s", t=13.0)]))
        FakeEngine.route = lambda p: {
            "routes": [{"finding": "F2", "stage": "build", "duplicate_of": "F1",
                        "why": "same as the measured penetration"}],
            "briefs": [{"stage": "vfx", "must_change": ["clear the set"], "check": "no merge"}]}
        h = harness.Harness(self.root, "improve", verbose=False)
        routing = improve.route(h, findings, name="route-pass-01")
        self.assertEqual(routing["routes"]["F2"]["stage"], "vfx")      # follows its primary
        self.assertEqual(routing["routes"]["F2"]["duplicate_of"], "F1")
        self.assertEqual(list(routing["stages"]), ["vfx"])
        self.assertEqual(routing["stages"]["vfx"]["findings"], ["F1", "F2"])

    def test_external_and_upstream_findings_go_to_a_human_and_are_not_looped(self):
        findings = improve.collect(card(5.0, findings=[
            prose("the gate demands caption cues for a film with no words", owner="gate"),
            prose("the glide-time claim has no source", owner="citations")]))
        FakeEngine.route = lambda p: {
            "routes": [{"finding": "F1", "stage": "external", "why": "production_gate policy"},
                       {"finding": "F2", "stage": "upstream-fact", "why": "a wrong claim"}],
            "briefs": []}
        h = harness.Harness(self.root, "improve", verbose=False)
        routing = improve.route(h, findings, name="route-pass-01")
        self.assertEqual(routing["stages"], {})
        self.assertEqual([r["id"] for r in routing["human"]], ["F1", "F2"])
        self.assertEqual(routing["human"][0]["route"], "external")

    def test_an_unrouted_finding_is_asked_again_once_then_an_error(self):
        findings = improve.collect(card(5.0, findings=[prose("a", owner="lighting"),
                                                       prose("b", t=6.0, owner="lighting")]))
        FakeEngine.route = lambda p: {"routes": [{"finding": "F1", "stage": "vfx",
                                                  "why": "x"}], "briefs": []}
        h = harness.Harness(self.root, "improve", verbose=False)
        with self.assertRaises(harness.HarnessError):
            improve.route(h, findings, name="route-pass-01")
        self.assertEqual(len(FakeEngine.json_calls), 2)

    def test_a_retry_that_routes_everything_is_taken(self):
        findings = improve.collect(card(5.0, findings=[prose("a"), prose("b", t=6.0)]))
        answers = [{"routes": [{"finding": "F1", "stage": "vfx", "why": "x"}], "briefs": []},
                   route_all_to("vfx")(json.dumps([{"id": "F1"}, {"id": "F2"}]))]
        FakeEngine.route = lambda p: answers.pop(0)
        h = harness.Harness(self.root, "improve", verbose=False)
        routing = improve.route(h, findings, name="route-pass-01")
        self.assertEqual(routing["routes"]["F2"]["stage"], "vfx")

    def test_a_finding_the_manifests_contradict_is_routed_nowhere(self):
        # build.json (setUp) places 3 audio tracks; QA claims there are none.
        findings = improve.collect(card(5.0, findings=[
            prose("The scene has no audio nodes (audio coverage 0/9).", owner="audio"),
            prose("the hero reads flat")]))
        h = harness.Harness(self.root, "improve", verbose=False)
        routing = improve.route(h, findings, name="r")
        self.assertEqual([c["id"] for c in routing["contradicted"]], ["F1"])
        self.assertIn("build.json: audio_tracks", routing["contradicted"][0]["contradicted_by"])
        self.assertNotIn("F1", routing["routes"])
        self.assertNotIn("audio coverage 0/9", FakeEngine.json_calls[0]["prompt"])
        self.assertEqual(routing["stages"]["vfx"]["findings"], ["F2"])

    def test_a_claim_the_manifests_support_is_routed(self):
        write_json(self.root / "build.json", {"audio_tracks": 0, "scene": {}})
        findings = improve.collect(card(5.0, findings=[
            prose("The scene has no audio tracks.", owner="audio")]))
        routing = improve.route(harness.Harness(self.root, "improve", verbose=False),
                                findings, name="r")
        self.assertEqual(routing["contradicted"], [])
        self.assertIn("F1", routing["routes"])

    def test_the_claims_checked_are_film_neutral(self):
        rows = improve.contradicted_by({"problem": "a placeholder card at 0:04"},
                                       {"placeholders": []})
        self.assertIn("build.json: placeholders", rows)
        self.assertIsNone(improve.contradicted_by({"problem": "the light is flat"},
                                                  {"audio_tracks": 0}))
        self.assertIn("narration_tracks", improve.contradicted_by(
            {"problem": "there is no narration track"}, {"narration_tracks": 2}))

    def test_gate_and_doctor_rows_are_evidence(self):
        gate = {"exit": 1, "triage": {"blocking": [{"what": "no caption cues",
                                                    "owner": "voice", "fix": "yt voice"}]}}
        rows = [("fail", "sound", "the listen-back says not ready", "yt sound --review"),
                ("ok", "build", "fine", ""), ("fail", "delivery", "QA says NOT READY", "")]
        found = improve.collect(card(5.0), gate=gate, doctor=rows)
        self.assertEqual([f["source"] for f in found], ["gate", "doctor"])
        self.assertEqual(found[1]["owner"], "sound")
        self.assertEqual(found[1]["severity"], "blocking")

    def test_a_mechanical_fingerprint_is_kind_shot_and_objects(self):
        a = improve.fingerprint(mech())
        self.assertEqual(a, "penetration|1.4|hero-x+table-top")
        self.assertEqual(improve.fingerprint(mech(t=12.5)), a)
        self.assertNotEqual(improve.fingerprint(mech(shot="1.3")), a)
        self.assertIsNone(improve.fingerprint(prose("words")))


# --------------------------------------------------------------------------- #
# the feedback channel
# --------------------------------------------------------------------------- #

FEEDBACK = "THE-NOTE: cast the hero's shadow on the surface it crosses"


def plant(root: Path, stage: str, text: str = FEEDBACK) -> None:
    (root / "feedback").mkdir(exist_ok=True)
    (root / "feedback" / f"{stage}.md").write_text(text, encoding="utf-8")


class Feedback(ImproveCase):
    def test_files_are_written_with_the_findings_verbatim_and_the_pass(self):
        c = card(5.3, mechanical=[mech()], findings=[prose("the hero material is plain white", t=9.0)])
        findings = improve.collect(c)
        h = harness.Harness(self.root, "improve", verbose=False)
        routing = improve.route(h, findings, name="route-pass-08")
        written = improve.write_feedback(self.root, routing, findings, from_pass="pass-08",
                                         card=c, improve_pass="pass-01")
        self.assertEqual(written, ["vfx"])
        md = (self.root / "feedback" / "vfx.md").read_text()
        self.assertIn("pass-08", md)
        self.assertIn("the hero material is plain white", md)
        self.assertIn("`hero-x` inside `table-top`", md)
        self.assertIn("0:09", md)
        self.assertIn("fix what vfx owns", md)
        self.assertIn("the next pass no longer shows it", md)
        twin = read_json(self.root / "feedback" / "vfx.json")
        self.assertEqual(twin["from_pass"], "pass-08")
        self.assertEqual(twin["stage"], "vfx")
        self.assertEqual(len(twin["findings"]), 2)

    def test_stale_open_feedback_for_an_unrouted_stage_is_removed(self):
        plant(self.root, "sound")
        c = card(5.0, mechanical=[mech()])
        findings = improve.collect(c)
        h = harness.Harness(self.root, "improve", verbose=False)
        routing = improve.route(h, findings, name="r")
        improve.write_feedback(self.root, routing, findings, from_pass="pass-01", card=c,
                               improve_pass="pass-01")
        self.assertFalse((self.root / "feedback" / "sound.md").exists())

    def test_closing_moves_the_notes_under_closed(self):
        plant(self.root, "vfx")
        write_json(self.root / "feedback" / "vfx.json", {"stage": "vfx"})
        improve.close_feedback(self.root, "pass-01")
        self.assertFalse((self.root / "feedback" / "vfx.md").exists())
        self.assertTrue((self.root / "feedback" / "closed" / "pass-01" / "vfx.md").is_file())
        self.assertTrue((self.root / "feedback" / "closed" / "pass-01" / "vfx.json").is_file())
        self.assertEqual(harness.feedback_for(self.root, "vfx"), "")


class Injection(ImproveCase):
    """Each stage's prompts carry its open feedback, and nothing without it."""

    def test_the_harness_puts_the_stages_feedback_in_its_system_prompt(self):
        plant(self.root, "build")
        system = harness.Harness(self.root, "build", house="## all\n- rule").system
        self.assertIn(FEEDBACK, system)
        self.assertIn("outrank the house standards", system)
        self.assertLess(system.index("- rule"), system.index(FEEDBACK))
        self.assertNotIn(FEEDBACK, harness.Harness(self.root, "sound").system)

    def test_no_feedback_no_injection(self):
        self.assertEqual(harness.feedback_for(self.root, "build"), "")
        self.assertNotIn("revision notes", harness.Harness(self.root, "build").system)
        self.assertEqual(harness.with_feedback("SYS", self.root, "vfx"), "SYS")
        self.assertEqual(harness.with_feedback("SYS", None, "vfx"), "SYS")

    def test_every_decision_and_inspection_sees_it(self):
        plant(self.root, "voice")
        h = harness.Harness(self.root, "voice", verbose=False)
        h.decide("cast", "who reads it?", {"type": "object", "required": []})
        self.assertIn(FEEDBACK, FakeEngine.json_calls[-1]["system"])

    def test_a_changed_note_asks_again(self):
        h = harness.Harness(self.root, "build", verbose=False)
        schema = {"type": "object", "required": []}
        h.decide("edit", "p", schema)
        plant(self.root, "build")
        harness.Harness(self.root, "build", verbose=False).decide("edit", "p", schema)
        self.assertEqual(len(FakeEngine.json_calls), 2)

    def _planner(self):
        from ytstudio.research import Dossier
        bundle = {"idea": self.idea, "outline": {}, "claims": [], "assets": [], "premise": ""}
        return vfx.VfxPlanner(bundle, Dossier(self.root, self.idea, at=self.root),
                              vfx.Workspace(self.root), model="sonnet", verbose=False)

    def test_vfx_planning_authoring_and_review_see_it(self):
        for present in (False, True):
            if present:
                plant(self.root, "vfx")
            FakeEngine.json_calls = []
            planner = self._planner()
            planner._decide("plan", "plan it", {"type": "object", "required": []})
            planner._stream(FakeEngine(None), 1, "author it")
            reviewer = harness.Harness(self.root, "vfx", verbose=False)
            for call in FakeEngine.json_calls:
                self.assertEqual(FEEDBACK in call["system"], present, call["prompt"])
            self.assertEqual(FEEDBACK in reviewer.system, present)

    def test_keyframe_authoring_and_review_see_it(self):
        def renderer(project_root, host, *, verbose=False):
            return lambda doc, out, at: (True, "fake")
        with patch.object(keyframe, "RENDERER", renderer):
            for present in (False, True):
                if present:
                    plant(self.root, "keyframe")
                FakeEngine.json_calls = []
                stage = keyframe.KeyframeStage(workspace.Project(self.root), self.idea,
                                               verbose=False)
                stage._stream(FakeEngine(None), "compose it")
                self.assertEqual(FEEDBACK in FakeEngine.json_calls[0]["system"], present)
                self.assertEqual(FEEDBACK in stage._harness("role").system, present)

    def test_sound_script_and_styles_see_it(self):
        makers = {
            "sound": lambda: sound.SoundDesigner(self.idea, root=self.root, verbose=False),
            "script": lambda: script.Screenwriter(self.idea, root=self.root, verbose=False),
            "styles": lambda: styles.Director(self.idea, root=self.root, verbose=False),
        }
        for name, make in makers.items():
            module = {"sound": sound, "script": script, "styles": styles}[name]
            for present in (False, True):
                if present:
                    plant(self.root, name)
                FakeEngine.json_calls = []
                make()._ask("decide", "ask", {"type": "object", "required": []})
                self.assertEqual(FEEDBACK in FakeEngine.json_calls[0]["system"], present,
                                 f"{name} with feedback={present}")

    def test_the_sound_synthesis_brief_carries_it(self):
        plant(self.root, "sound")
        data = {"cues": [{"cue_id": "S1", "kind": "sfx", "at": 1.0, "seconds": 1.0,
                          "query": "a tap"}]}
        seen = {}

        def author(ws, targets):
            seen["brief"] = (ws / "BRIEF.md").read_text()
            return Usage()

        sound.realise(self.root, data, author=author, say=lambda m: None)
        self.assertIn(FEEDBACK, seen["brief"])

    def test_build_decides_through_the_harness_so_it_sees_it(self):
        plant(self.root, "build")
        self.assertIn("Harness(project.root, \"build\"",
                      (ROOT / "ytstudio" / "build.py").read_text())
        self.assertIn(FEEDBACK, harness.Harness(self.root, "build").system)


# --------------------------------------------------------------------------- #
# the plan: which stages run, with which flags
# --------------------------------------------------------------------------- #

class Plan(ImproveCase):
    def test_earliest_routed_stage_then_everything_downstream(self):
        self.assertEqual(improve.plan({"sound", "vfx"}, self.root),
                         ["vfx", "sound", "build", "render"])
        self.assertEqual(improve.plan({"build"}, self.root), ["build", "render"])
        self.assertEqual(improve.plan(set(), self.root), [])

    def test_research_and_script_only_when_routed_to_them(self):
        write_json(self.root / "props.json", {})
        write_json(self.root / "keyframe.json", {})
        write_json(self.root / "dossier.json", {})
        got = improve.plan({"styles"}, self.root)
        self.assertNotIn("script", got)
        self.assertNotIn("research", got)
        self.assertEqual(got[0], "styles")
        self.assertIn("script", improve.plan({"script"}, self.root))

    def test_a_downstream_stage_the_film_never_ran_is_left_out(self):
        # No voice.json, no retime.json: the film has no narration.
        write_json(self.root / "keyframe.json", {})
        self.assertEqual(improve.plan({"keyframe"}, self.root),
                         ["keyframe", "vfx", "sound", "build", "render"])

    def test_the_flags_force_a_new_decision_where_routed(self):
        base = ["--project", str(self.root), "--model", "sonnet", "--quiet"]
        argv = improve.stage_argvs(["vfx", "sound", "build", "render"], {"vfx"}, base,
                                   quality="final", root=self.root)
        self.assertEqual([a[0] for a in argv], ["vfx", "sound", "build", "render"])
        self.assertIn("--fresh", argv[0])
        self.assertIn("--fetch", argv[1])
        self.assertNotIn("--fresh", argv[1])            # not routed: reused
        self.assertEqual(argv[3][-2:], ["--quality", "final"])
        forced = improve.stage_argvs(["sound"], {"sound"}, base, quality="draft",
                                     root=self.root)[0]
        self.assertTrue({"--fetch", "--fresh", "--review"} <= set(forced))

    def test_the_estimate_counts_what_runs_before_vfx(self):
        self.assertEqual(improve.estimate(["vfx", "sound", "build", "render"], None), 10.0)
        self.assertEqual(improve.estimate(["keyframe", "vfx", "build", "render"], None), 15.0)
        self.assertEqual(improve.estimate(["vfx"], 7.25), 7.25)
        self.assertEqual(improve.estimate(["build", "render"], None), 0.7)

    def test_script_keeps_the_films_length(self):
        base = ["--project", str(self.root)]
        argv = improve.stage_argvs(["script"], {"script"}, base, quality="final",
                                   root=self.root)[0]
        self.assertEqual(argv[argv.index("--minutes") + 1], "0.25")

    def test_script_revises_the_named_shots_when_every_finding_names_one(self):
        # AUTO-95: a finding that names its shot sends back that line, not
        # the screenplay from its plan; one film-wide finding needs the whole.
        base = ["--project", str(self.root)]
        named = [{"id": "F1", "shot": "1.3"}, {"id": "F2", "shot": "2.1"}]
        argv = improve.stage_argvs(["script", "voice"], {"script"}, base, quality="final",
                                   root=self.root, script_findings=named)
        self.assertEqual(argv[0][argv[0].index("--revise") + 1], "feedback")
        self.assertNotIn("--polish", argv[0])
        self.assertNotIn("--revise", argv[1])
        for findings in (named + [{"id": "F3", "shot": None}], [], None):
            argv = improve.stage_argvs(["script"], {"script"}, base, quality="final",
                                       root=self.root, script_findings=findings)[0]
            self.assertNotIn("--revise", argv)
            self.assertTrue({"--polish", "--verify"} <= set(argv), findings)

    def test_a_pass_routed_to_script_by_shot_runs_revise_feedback(self):
        self.start_pass(card(5.0, findings=[dict(prose("the line stumbles", owner="script"),
                                                 shot="1.3")]))
        FakeEngine.route = route_all_to("script")
        record = self.improver(FakeRunner(self.root, []), dry_run=True).run()
        script_argv = next(a for a in record["plan"]["argv"] if a[0] == "script")
        self.assertEqual(script_argv[script_argv.index("--revise") + 1], "feedback")
        twin = read_json(self.root / "feedback" / "script.json")
        self.assertEqual([f["shot"] for f in twin["findings"]], ["1.3"])

    def test_a_shot_finding_that_survived_a_briefed_pass_runs_the_whole_screenplay(self):
        # A line revision that left the finding standing (a super, a shot's
        # hold, a story beat a one-line fix cannot reach) is not tried again:
        # the next approach is the screenplay from its plan.
        base = ["--project", str(self.root)]
        named = [{"id": "F1", "shot": "1.3"}, {"id": "F2", "shot": "2.1"}]
        argv = improve.stage_argvs(["script"], {"script"}, base, quality="final",
                                   root=self.root, script_findings=named,
                                   survived={"F2": 1})[0]
        self.assertNotIn("--revise", argv)
        self.assertTrue({"--polish", "--verify"} <= set(argv))
        argv = improve.stage_argvs(["script"], {"script"}, base, quality="final",
                                   root=self.root, script_findings=named,
                                   survived={"F9": 2})[0]
        self.assertEqual(argv[argv.index("--revise") + 1], "feedback")

    def test_the_loop_escalates_a_shot_finding_the_revision_left_standing(self):
        stuck = dict(prose("the super shows the wrong figure", owner="script"), shot="2.1")
        self.start_pass(card(5.0, findings=[stuck]))
        FakeEngine.route = route_all_to("script")

        def same(prompt):
            ids = re.findall(r'"id": "(D\d+)"', prompt)
            return {"matches": [{"finding": finding_ids(prompt)[0], "defect": ids[0]}]}
        FakeEngine.same = same
        runner = FakeRunner(self.root, [card(5.5, findings=[stuck]), card(6.0)])
        self.improver(runner, max_passes=2, budget_usd=100).run()
        scripts = [a for a in runner.calls if a[0] == "script"]
        self.assertEqual(len(scripts), 2, runner.stages())
        self.assertEqual(scripts[0][scripts[0].index("--revise") + 1], "feedback")
        self.assertNotIn("--revise", scripts[1])
        self.assertIn("--polish", scripts[1])

    def test_resume_replays_a_by_shot_script_pass_as_revise_feedback(self):
        self.start_pass(card(5.0, findings=[dict(prose("the line stumbles", owner="script"),
                                                 shot="1.3")]))
        FakeEngine.route = route_all_to("script")
        self.improver(FakeRunner(self.root, [], fail={"script": 1})).run()
        runner = FakeRunner(self.root, [card(6.0)])
        self.improver(runner, resume=True, max_passes=1).run()
        self.assertEqual(runner.stages()[0], "script")
        self.assertEqual(runner.calls[0][runner.calls[0].index("--revise") + 1], "feedback")

    def test_script_written_again_has_its_plan_reviewed(self):
        # AUTO-92: a script the loop writes again, routed or downstream, is
        # read by the plan reviewer before its sections are paid for.
        self.assertEqual(improve.FORCE["script"], ["--polish", "--verify", "--review"])
        self.assertEqual(improve.REUSE["script"], ["--polish", "--verify", "--review"])
        base = ["--project", str(self.root), "--model", "sonnet", "--quiet"]
        for routed in ({"script"}, set()):
            argv = improve.stage_argvs(["script"], routed, base, quality="final",
                                       root=self.root)[0]
            self.assertTrue(cli.build_parser().parse_args(argv).review, routed)


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #

class Loop(ImproveCase):
    def test_a_pass_runs_the_chain_then_gate_then_qa_in_order(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [card(6.0)])
        record = self.improver(runner, max_passes=1).run()
        self.assertEqual(runner.stages(), ["vfx", "sound", "build", "render", "gate", "qa"])
        self.assertIn("--fresh", runner.calls[0])
        self.assertEqual(runner.calls[0][runner.calls[0].index("--project") + 1],
                         str(self.root))
        self.assertIn("--quality", runner.calls[3])
        self.assertEqual(record["passes"][0]["stages"], ["vfx", "sound", "build", "render"])

    def test_keep_on_improvement(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [card(6.0)])
        record = self.improver(runner, max_passes=1).run()
        p = record["passes"][0]
        self.assertEqual(p["decision"], "keep")
        self.assertEqual((p["overall_before"], p["overall_after"]), (5.0, 6.0))
        self.assertEqual(record["best"]["pass"], "pass-02")
        self.assertEqual(read_json(self.root / "vfx.json")["authoring"]["version"], "v02")
        self.assertEqual((self.root / "video.mp4").read_bytes()[:5], b"video")

    def test_revert_on_regression_restores_the_snapshot(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [card(4.0)])
        record = self.improver(runner, max_passes=1).run()
        p = record["passes"][0]
        self.assertEqual(p["decision"], "revert")
        self.assertEqual(read_json(self.root / "vfx.json")["authoring"]["version"], "v01")
        self.assertEqual(read_json(self.root / "vfx.json")["authoring"]["versions"], ["v01"])
        self.assertEqual((self.root / "video.mp4").read_bytes(), b"original video")
        self.assertEqual((self.root / "scene.xml").read_text(), "<scene original='1'/>")
        self.assertTrue(read_json(self.root / "sound.json")["original"])
        self.assertFalse((self.root / "gate.json").exists())     # it did not exist before
        self.assertEqual(read_json(self.root / "qa.json")["pass"], "pass-01")
        self.assertTrue((self.root / "improve" / "pass-01" / "snapshot").is_dir())
        self.assertEqual(record["best"]["pass"], "pass-01")
        self.assertTrue(any("overall" in r for r in p["reasons"]))

    def test_a_category_may_drop_by_the_tolerance_but_not_more(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [card(6.0, scores={"voiceover": 4.5})])
        p = self.improver(runner, max_passes=1).run()["passes"][0]
        self.assertEqual(p["decision"], "keep")          # dropped exactly 0.5

    def test_a_category_that_drops_more_than_the_tolerance_reverts(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [card(6.0, scores={"voiceover": 4.4})])
        p = self.improver(runner, max_passes=1).run()["passes"][0]
        self.assertEqual(p["decision"], "revert")
        self.assertTrue(any("voiceover" in r for r in p["reasons"]))

    def test_a_new_blocking_mechanical_finding_reverts(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [card(6.0, mechanical=[mech(), mech(
            "frozen", "1.1", owner="vfx")])])
        p = self.improver(runner, max_passes=1).run()["passes"][0]
        self.assertEqual(p["decision"], "revert")
        self.assertTrue(any("frozen" in r for r in p["reasons"]))

    def test_stop_on_ready(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [card(9.2)])
        record = self.improver(runner, max_passes=3).run()
        self.assertEqual(len(record["passes"]), 1)
        self.assertEqual(record["stop"]["reason"], "ready")
        self.assertEqual(record["best"]["verdict"], qa.READY)

    def test_stop_on_max_passes(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [card(6.0, mechanical=[mech("flat", "1.2")]),
                                        card(7.0, mechanical=[mech("clipped", "1.3")])])
        record = self.improver(runner, max_passes=2).run()
        self.assertEqual(len(record["passes"]), 2)
        self.assertEqual(record["stop"]["reason"], "max-passes")

    def test_stop_on_budget_before_a_pass_that_would_exceed_it(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [card(6.0)])
        record = self.improver(runner, budget_usd=5.0).run()
        self.assertEqual(runner.calls, [])                # a vfx pass is estimated at $10
        self.assertEqual(record["stop"]["reason"], "budget")
        self.assertIn("10", record["stop"]["detail"])

    def test_the_last_pass_cost_is_the_next_estimate(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [card(6.0, mechanical=[mech("flat", "1.2")]),
                                        card(7.0)], costs={"vfx": 6.0})
        record = self.improver(runner, budget_usd=12.0, max_passes=3).run()
        self.assertEqual(len(record["passes"]), 1)
        self.assertGreaterEqual(record["passes"][0]["cost_usd"], 6.0)
        self.assertEqual(record["stop"]["reason"], "budget")
        self.assertGreaterEqual(record["spent_usd"], 6.0)

    def test_a_defect_that_survives_three_passes_stops_the_loop_and_is_named(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [card(5.5, mechanical=[mech()]),
                                        card(6.0, mechanical=[mech()]),
                                        card(6.5, mechanical=[mech()]),
                                        card(7.0, mechanical=[mech()])])
        record = self.improver(runner, max_passes=5, budget_usd=100).run()
        self.assertEqual(len(record["passes"]), 3)
        self.assertEqual(record["stop"]["reason"], "survivor")
        self.assertIn("penetration|1.4|hero-x+table-top", record["stop"]["detail"])

    def test_a_prose_survivor_is_judged_by_the_model(self):
        start = card(5.0, findings=[prose("the hero reads flat")])
        self.start_pass(start)
        again = [card(5.5 + n / 2, findings=[prose(f"still a flat hero, take {n}")])
                 for n in range(4)]
        runner = FakeRunner(self.root, again)

        def same(prompt):
            ids = re.findall(r'"id": "(D\d+)"', prompt)
            news = finding_ids(prompt)
            return {"matches": [{"finding": news[0], "defect": ids[0]}]}

        FakeEngine.same = same
        record = self.improver(runner, max_passes=5, budget_usd=100).run()
        self.assertEqual(record["stop"]["reason"], "survivor")
        self.assertTrue(any("matches" in json.dumps(c.get("schema", {}))
                            for c in FakeEngine.json_calls))

    def test_a_fixed_defect_resets_its_count(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [card(5.5, mechanical=[mech()]),
                                        card(6.0, mechanical=[mech("flat", "1.2")]),
                                        card(6.5, mechanical=[mech()])])
        record = self.improver(runner, max_passes=3, budget_usd=100).run()
        self.assertEqual(record["stop"]["reason"], "max-passes")

    def test_a_failing_stage_reverts_and_stops(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [card(6.0)], fail={"build": 1})
        record = self.improver(runner).run()
        self.assertEqual(runner.stages(), ["vfx", "sound", "build"])
        self.assertEqual(record["passes"][0]["decision"], "failed")
        self.assertEqual(record["stop"]["reason"], "stage-failed")
        self.assertEqual(read_json(self.root / "vfx.json")["authoring"]["version"], "v01")
        attempt = self.root / "improve" / "pass-01" / "attempt"
        self.assertEqual(read_json(attempt / "vfx.json")["authoring"]["version"], "v02")

    def test_a_qa_that_files_no_pass_keeps_the_paid_pass_unjudged(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [], fail={"qa": "no-pass"})
        record = self.improver(runner).run()
        p = record["passes"][0]
        self.assertEqual(p["decision"], "unjudged")
        self.assertEqual(record["stop"]["reason"], "qa-failed")
        # Nothing was restored: the new authoring and the new video stand.
        self.assertEqual(read_json(self.root / "vfx.json")["authoring"]["version"], "v02")
        self.assertNotEqual((self.root / "video.mp4").read_bytes(), b"original video")
        self.assertTrue((self.root / "improve" / "pass-01" / "snapshot").is_dir())

    def test_the_next_run_scores_and_keeps_an_unjudged_pass_first(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        self.improver(FakeRunner(self.root, [], fail={"qa": "no-pass"})).run()
        runner = FakeRunner(self.root, [card(6.0)])
        record = self.improver(runner, max_passes=1).run()
        self.assertEqual(runner.stages(), ["qa"])          # only the judging ran
        p = record["passes"][0]
        self.assertTrue(p["resumed"])
        self.assertEqual(p["decision"], "keep")
        self.assertEqual(record["best"]["pass"], "pass-02")
        self.assertEqual(record["start"]["pass"], "pass-01")
        self.assertEqual(read_json(self.root / "vfx.json")["authoring"]["version"], "v02")
        self.assertTrue((self.root / "feedback" / "closed" / "pass-01" / "vfx.md").is_file())

    def test_the_next_run_reverts_an_unjudged_pass_that_regressed(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        self.improver(FakeRunner(self.root, [], fail={"qa": "no-pass"})).run()
        record = self.improver(FakeRunner(self.root, [card(4.0)]), max_passes=1).run()
        self.assertEqual(record["passes"][0]["decision"], "revert")
        self.assertEqual(read_json(self.root / "vfx.json")["authoring"]["version"], "v01")
        self.assertEqual((self.root / "video.mp4").read_bytes(), b"original video")

    def _failed_render(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        first = FakeRunner(self.root, [], fail={"render": 1}, costs={"vfx": 3.0})
        return self.improver(first).run()

    def test_resume_runs_a_failed_pass_again_from_the_stage_that_failed(self):
        self._failed_render()
        runner = FakeRunner(self.root, [card(6.0)])
        record = self.improver(runner, resume=True, max_passes=1).run()
        self.assertEqual(runner.stages(), ["render", "gate", "qa"])     # nothing re-authored
        p = record["passes"][0]
        self.assertTrue(p["resumed"])
        self.assertEqual(p["decision"], "keep")
        self.assertEqual(read_json(self.root / "vfx.json")["authoring"]["version"], "v02")
        self.assertEqual(record["best"]["pass"], "pass-02")
        self.assertGreaterEqual(record["spent_usd"], 3.0)               # the earlier spend counts
        self.assertTrue((self.root / "feedback" / "closed" / "pass-01" / "vfx.md").is_file())

    def test_resume_prices_a_script_with_the_flags_it_will_be_handed(self):
        # AUTO-92: a resumed pass that writes the script again hands it
        # --review, so its estimate is asked with that argv.
        self._failed_render()
        earlier = read_json(self.root / "improve.json")
        last = earlier["passes"][-1]
        last.update(stages=["script", *last["stages"]], routed={"script": {"findings": []}},
                    exit={"script": 1})
        write_json(self.root / "improve.json", earlier)
        seen = {}

        def priced(root, stages, sections=None, extras=None):
            seen.update(extras or {})
            return [{"stage": s, "usd": 1000.0, "basis": "assumed", "source": "t"}
                    for s in stages]
        runner = FakeRunner(self.root, [])
        with patch.object(improve, "stage_estimates", priced):
            self.improver(runner, resume=True, max_passes=1).run()
        self.assertEqual(runner.calls, [])
        self.assertIn("--review", seen.get("script", []))

    def test_resume_without_the_attempts_files_replays_vfx_from_its_cache(self):
        import shutil
        self._failed_render()
        shutil.rmtree(self.root / "improve" / "pass-01" / "attempt")
        runner = FakeRunner(self.root, [card(6.0)])
        self.improver(runner, resume=True, max_passes=1).run()
        self.assertEqual(runner.stages(), ["vfx", "sound", "build", "render", "gate", "qa"])
        self.assertNotIn("--fresh", runner.calls[0])                     # replayed, not authored
        first = read_json(self.root / "improve" / "pass-01" / "pass.json")["previous_attempt"]
        self.assertEqual(runner.calls[1], first["argv"][1])             # sound as the pass ran it

    def test_resume_that_regresses_restores_the_snapshot(self):
        self._failed_render()
        record = self.improver(FakeRunner(self.root, [card(4.0)]), resume=True,
                               max_passes=1).run()
        self.assertEqual(record["passes"][0]["decision"], "revert")
        self.assertEqual(read_json(self.root / "vfx.json")["authoring"]["version"], "v01")
        self.assertEqual((self.root / "video.mp4").read_bytes(), b"original video")

    def test_the_report_of_a_resumed_pass_measures_its_mechanical_fixes(self):
        self._failed_render()
        self.improver(FakeRunner(self.root, [card(4.0)]), resume=True, max_passes=1).run()
        md = (self.root / "improve.md").read_text()
        self.assertIn("gone in pass-02, but the pass was reverted", md)
        self.assertNotIn("still reported by pass-02", md)

    def test_resume_with_nothing_failed_runs_nothing_and_keeps_the_record(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        self.improver(FakeRunner(self.root, [card(6.0)]), max_passes=1).run()
        saved = (self.root / "improve.json").read_text()
        runner = FakeRunner(self.root, [])
        record = self.improver(runner, resume=True).run()
        self.assertEqual(runner.calls, [])
        self.assertEqual(record["stop"]["reason"], "resume-failed")
        self.assertEqual((self.root / "improve.json").read_text(), saved)

    def test_a_gate_that_wrote_nothing_is_not_read_as_the_new_videos(self):
        old_gate = {"exit": 1, "triage": {"blocking": [{"what": "the old video's defect",
                                                        "owner": "vfx", "fix": "x"}]}}
        write_json(self.root / "gate.json", old_gate)
        self.start_pass(card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [card(4.0)], fail={"gate": "refuse"})
        record = self.improver(runner, max_passes=1).run()
        self.assertIsNone(runner.gate_at_qa)                 # QA saw no stale gate
        self.assertIn("set aside", record["passes"][0]["gate"])
        self.assertTrue((self.root / "improve" / "pass-01" / "stale-gate.json").is_file())
        # the pass was reverted, so the old gate.json is back with the old video
        self.assertEqual(read_json(self.root / "gate.json"), old_gate)

    def test_a_gate_that_ran_is_left_for_qa(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [card(6.0)])
        record = self.improver(runner, max_passes=1).run()
        self.assertEqual(runner.gate_at_qa, {"exit": 0, "engine": {"cost_usd": 0.0}})
        self.assertNotIn("gate", record["passes"][0])

    def test_a_reverted_pass_keeps_what_it_made(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        self.improver(FakeRunner(self.root, [card(4.0)]), max_passes=1).run()
        attempt = self.root / "improve" / "pass-01" / "attempt"
        self.assertEqual(read_json(attempt / "vfx.json")["authoring"]["version"], "v02")
        self.assertNotEqual((attempt / "video.mp4").read_bytes(), b"original video")
        self.assertEqual((self.root / "video.mp4").read_bytes(), b"original video")

    def _reverted(self, old_gate=True):
        if old_gate:
            write_json(self.root / "gate.json", {"exit": 1, "triage": {"blocking": [
                {"what": "the old video's defect", "owner": "vfx", "fix": "x"}]}})
        self.start_pass(card(5.0, mechanical=[mech()]))
        self.improver(FakeRunner(self.root, [card(4.9)], fail={"gate": "refuse"}),
                      max_passes=1).run()

    def test_rejudge_re_renders_the_attempt_and_judges_it_on_clean_evidence(self):
        self._reverted()
        self.assertEqual(read_json(self.root / "vfx.json")["authoring"]["version"], "v01")
        runner = FakeRunner(self.root, [card(6.0)], fail={"gate": "refuse"})
        record = self.improver(runner, rejudge=True, max_passes=1).run()
        self.assertEqual(runner.stages(), ["render", "gate", "qa"])      # nothing authored
        self.assertIn("--quality", runner.calls[0])
        self.assertIsNone(runner.gate_at_qa)                              # old gate set aside
        p = record["passes"][0]
        self.assertTrue(p["rejudged"])
        self.assertEqual(p["previous_judgement"]["decision"], "revert")
        self.assertEqual(p["previous_judgement"]["overall_after"], 4.9)
        self.assertEqual(p["decision"], "keep")
        self.assertEqual(p["overall_before"], 5.0)
        self.assertEqual(read_json(self.root / "vfx.json")["authoring"]["version"], "v02")
        self.assertEqual(record["best"]["pass"], "pass-03")

    def test_rejudge_that_still_regresses_puts_the_reverted_state_back(self):
        self._reverted()
        record = self.improver(FakeRunner(self.root, [card(4.0)], fail={"gate": "refuse"}),
                               rejudge=True, max_passes=1).run()
        self.assertEqual(record["passes"][0]["decision"], "revert")
        self.assertEqual(read_json(self.root / "vfx.json")["authoring"]["version"], "v01")
        self.assertEqual((self.root / "video.mp4").read_bytes(), b"original video")
        self.assertEqual(read_json(self.root / "gate.json")["triage"]["blocking"][0]["what"],
                         "the old video's defect")

    def test_rejudge_needs_a_judged_pass_with_its_attempt(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [])
        record = self.improver(runner, rejudge=True).run()
        self.assertEqual(runner.calls, [])
        self.assertEqual(record["stop"]["reason"], "resume-failed")

    def test_feedback_is_closed_once_a_pass_consumed_it(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        seen = {}
        inner = FakeRunner(self.root, [card(6.0)])

        def runner(argv):
            if argv[0] == "vfx":
                seen["open"] = harness.feedback_for(self.root, "vfx")
            return inner(argv)

        self.improver(runner, max_passes=1).run()
        self.assertIn("table-top", seen["open"])
        self.assertFalse((self.root / "feedback" / "vfx.md").exists())
        self.assertTrue((self.root / "feedback" / "closed" / "pass-01" / "vfx.md").is_file())

    def test_findings_for_a_human_only_mean_no_stage_runs(self):
        self.start_pass(card(5.0, findings=[prose("captions demanded", owner="gate")]))
        FakeEngine.route = lambda p: {"routes": [{"finding": "F1", "stage": "external",
                                                  "why": "policy"}], "briefs": []}
        runner = FakeRunner(self.root, [])
        record = self.improver(runner).run()
        self.assertEqual(runner.calls, [])
        self.assertEqual(record["stop"]["reason"], "human")
        self.assertEqual(record["human"][0]["route"], "external")

    def test_a_stale_pass_is_scored_again_first(self):
        pdir = self.start_pass(card(5.0, mechanical=[mech()]))
        old = 900_000_000
        os.utime(pdir / "scorecard.json", (old, old))
        runner = FakeRunner(self.root, [card(5.0, mechanical=[mech()]), card(6.0)])
        record = self.improver(runner, max_passes=1).run()
        self.assertEqual(runner.stages()[0], "qa")
        self.assertEqual(record["start"]["pass"], "pass-02")

    def test_no_pass_at_all_is_scored_first(self):
        runner = FakeRunner(self.root, [card(5.0, mechanical=[mech()]), card(6.0)])
        record = self.improver(runner, max_passes=1).run()
        self.assertEqual(runner.stages()[0], "qa")
        self.assertEqual(record["start"]["pass"], "pass-01")

    def test_from_pass_picks_the_evidence(self):
        self.start_pass(card(5.0, mechanical=[mech()]), "pass-01")
        self.start_pass(card(5.5, mechanical=[mech("flat", "1.1", owner="build")]), "pass-02")
        runner = FakeRunner(self.root, [card(6.0)])
        record = self.improver(runner, max_passes=1, from_pass="pass-01").run()
        self.assertEqual(record["start"]["pass"], "pass-01")
        self.assertEqual(runner.stages()[0], "vfx")


class DryRun(ImproveCase):
    def test_dry_run_routes_and_writes_feedback_but_runs_nothing(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [])
        out = []
        record = self.improver(runner, dry_run=True, say=out.append).run()
        self.assertEqual(runner.calls, [])
        self.assertTrue((self.root / "feedback" / "vfx.md").is_file())
        self.assertEqual(record["stop"]["reason"], "dry-run")
        self.assertEqual(record["plan"]["stages"], ["vfx", "sound", "build", "render"])
        self.assertGreaterEqual(record["plan"]["estimate_usd"], 10.0)
        text = "\n".join(out)
        self.assertIn("vfx", text)
        self.assertIn("$", text)
        self.assertTrue((self.root / "improve" / "plan.json").is_file())
        self.assertFalse((self.root / "improve.json").exists())

    def test_the_router_sees_what_waits_on_the_operator(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        rows = [("note", "keyframe", "the keyframe (5/10) awaits approval", "yt keyframe --approve"),
                ("warn", "vfx", "authored without an approved keyframe", "")]
        self.improver(FakeRunner(self.root), dry_run=True, doctor=lambda p: rows).run()
        prompt = FakeEngine.json_calls[0]["prompt"]
        self.assertIn("awaits approval", prompt)
        self.assertIn("yt keyframe --approve", prompt)
        self.assertIn("authored without an approved keyframe", prompt)

    def test_dry_run_does_not_rescore_a_stale_pass(self):
        pdir = self.start_pass(card(5.0, mechanical=[mech()]))
        os.utime(pdir / "scorecard.json", (900_000_000, 900_000_000))
        runner = FakeRunner(self.root, [])
        self.improver(runner, dry_run=True).run()
        self.assertEqual(runner.calls, [])


class Report(ImproveCase):
    def test_improve_json_and_md_carry_the_record(self):
        self.start_pass(card(5.0, mechanical=[mech()],
                             findings=[prose("pale on pale", severity="minor")]))
        runner = FakeRunner(self.root, [card(6.0, findings=[prose("pale on pale",
                                                                    severity="minor")]),
                                        card(5.0)])
        self.improver(runner, max_passes=2, budget_usd=100).run()
        record = read_json(self.root / "improve.json")
        for key in ("passes", "stop", "start", "best", "spent_usd", "budget_usd", "human"):
            self.assertIn(key, record)
        first, second = record["passes"]
        self.assertEqual(first["decision"], "keep")
        self.assertEqual(second["decision"], "revert")
        self.assertIn("vfx", first["routed"])
        self.assertTrue(first["routed"]["vfx"]["must_change"])
        self.assertIn("scores_before", first)
        self.assertIn("scores_after", first)
        md = (self.root / "improve.md").read_text()
        for needle in ("Verdict", "NOT READY", "Scorecard", "Changes implemented",
                       "Remaining issues", "Blocking", "Minor", "Final artifact",
                       "Publication rationale", "video.mp4", "1920x1080",
                       "penetration", "pale on pale", "reverted"):
            self.assertIn(needle, md, needle)
        # The fixed defect reads timestamp -> problem -> root cause -> correction -> result.
        self.assertIn("0:13", md)
        self.assertTrue((self.root / "improve" / "pass-01" / "pass.json").is_file())


class Contradicted(ImproveCase):
    def test_the_report_lists_what_the_manifests_contradict(self):
        self.start_pass(card(5.0, mechanical=[mech()], findings=[
            prose("Audio coverage is 0/9 in the scene document.", owner="audio")]))
        self.improver(FakeRunner(self.root, [card(6.0)]), max_passes=1).run()
        record = read_json(self.root / "improve.json")
        self.assertEqual(len(record["contradicted"]), 1)
        md = (self.root / "improve.md").read_text()
        self.assertIn("contradicted by build.json: audio_tracks", md)


class Cli(unittest.TestCase):
    def test_improve_is_a_command_with_its_flags(self):
        args = cli.build_parser().parse_args(
            ["improve", "--title", "x", "--max-passes", "2", "--budget-usd", "9",
             "--target", "8.5", "--dry-run", "--from-pass", "pass-03",
             "--quality", "draft", "--resume", "--rejudge"])
        self.assertIs(args.run, improve.run)
        self.assertEqual((args.max_passes, args.budget_usd, args.target, args.dry_run,
                          args.from_pass, args.quality, args.resume, args.rejudge),
                         (2, 9.0, 8.5, True, "pass-03", "draft", True, True))
        defaults = cli.build_parser().parse_args(["improve", "--title", "x"])
        self.assertEqual((defaults.max_passes, defaults.budget_usd, defaults.target,
                          defaults.quality), (3, 15.0, 9.0, "final"))

    def test_an_author_of_the_reviewers_family_is_refused_before_any_stage(self):
        # AUTO-92: script (--review), keyframe and vfx are reviewed by
        # vfx.REVIEWER_MODEL; a loop whose author is that family would reach
        # one and stop partway, so it is refused up front, as `yt run` does.
        args = cli.build_parser().parse_args(
            ["improve", "--title", "x", "--model", "claude-opus-5-5"])
        with patch.object(improve.workspace, "resolve",
                               side_effect=AssertionError("resolved")), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(improve.run(args), 2)
        self.assertIn("does not grade its own work", err.getvalue())

    def test_improve_is_not_a_pipeline_stage(self):
        self.assertNotIn("improve", [s for s, _ in workspace.STAGES])

    def test_the_manual_documents_it(self):
        text = (ROOT / "ytstudio" / "SKILL.md").read_text()
        self.assertIn("`yt improve`", text)
        self.assertRegex(text, r"\| `yt improve` \|")
        self.assertIn("feedback/", text)
        readme = (ROOT / "README.md").read_text()
        self.assertIn("yt improve", readme)


class OwnerRouting(ImproveCase):
    """QA's owner is a stage, upstream-fact or external; routing is a lookup."""

    OWNERS = ("script", "sound", "vfx", "build", "external", "upstream-fact")

    def scorecard(self):
        return card(5.0, findings=[prose(f"defect {n} of the {o}", t=float(n), owner=o)
                                   for n, o in enumerate(self.OWNERS, 1)])

    def test_the_owners_are_the_stages_and_the_two_human_routes(self):
        self.assertEqual(qa.OWNERS, improve.ROUTES)
        self.assertEqual(qa.OWNERS[-2:], ("upstream-fact", "external"))
        for stage, _ in workspace.STAGES:
            self.assertIn(stage, qa.OWNERS)
        schema = qa.SCORECARD_SCHEMA["properties"]["findings"]["items"]["properties"]["owner"]
        self.assertEqual(schema["enum"], list(qa.OWNERS))

    def test_a_finding_whose_owner_is_a_route_is_routed_by_lookup(self):
        findings = improve.collect(self.scorecard())
        # The model, asked anyway for briefs and duplicates, sends everything to sound.
        FakeEngine.route = route_all_to("sound")
        h = harness.Harness(self.root, "improve", verbose=False)
        routing = improve.route(h, findings, name="route-pass-10")
        self.assertEqual({fid: r["stage"] for fid, r in routing["routes"].items()},
                         {f"F{n}": o for n, o in enumerate(self.OWNERS, 1)})
        self.assertEqual({r["by"] for r in routing["routes"].values()}, {"owner"})
        self.assertEqual({h["route"] for h in routing["human"]},
                         {"external", "upstream-fact"})

    def test_a_dry_run_routes_every_finding_without_a_model_routing_decision(self):
        self.start_pass(self.scorecard())
        FakeEngine.route = lambda p: {"routes": [], "briefs": []}   # the model routes nothing
        out = []
        record = self.improver(FakeRunner(self.root, []), dry_run=True, say=out.append).run()
        routes = record["plan"]["routes"]
        self.assertEqual(len(routes), len(self.OWNERS))
        self.assertEqual({r["by"] for r in routes.values()}, {"owner"})
        self.assertEqual(len(FakeEngine.json_calls), 1)        # one call, for the briefs
        self.assertEqual(set(record["plan"]["routed"]), {"script", "sound", "vfx", "build"})

    def test_a_scorecard_with_a_free_text_owner_is_still_routed_by_the_model(self):
        findings = improve.collect(card(5.0, findings=[prose("flat", owner="editor")]))
        FakeEngine.route = route_all_to("build")
        h = harness.Harness(self.root, "improve", verbose=False)
        routing = improve.route(h, findings, name="route-pass-01")
        self.assertEqual(routing["routes"]["F1"]["by"], "model")
        self.assertEqual(routing["routes"]["F1"]["stage"], "build")


class History(ImproveCase):
    """--resume and --rejudge continue the earlier run's record (AUTO-48)."""

    def two_passes_then_a_failed_render(self):
        """pass-01 kept (the defect survives it), pass-02 fails at its render."""
        self.start_pass(card(5.0, mechanical=[mech()]))

        class Breaks(FakeRunner):
            renders = 0

            def __call__(self, argv):
                if argv[0] == "render":
                    Breaks.renders += 1
                    if Breaks.renders == 2:
                        self.calls.append(list(argv))
                        return 1
                return super().__call__(argv)

        first = Breaks(self.root, [card(5.5, mechanical=[mech()])], costs={"vfx": 2.0})
        return self.improver(first, max_passes=2).run()

    def test_resume_carries_the_passes_defects_and_survivors_forward(self):
        before = self.two_passes_then_a_failed_render()
        self.assertEqual([p["name"] for p in before["passes"]], ["pass-01", "pass-02"])
        self.assertEqual(before["passes"][1]["decision"], "failed")
        self.assertEqual(before["defects"]["D1"]["survived"], 1)
        runner = FakeRunner(self.root, [card(6.0, mechanical=[mech()]),
                                        card(6.5, mechanical=[mech()])])
        record = self.improver(runner, resume=True, max_passes=3).run()
        names = [p["name"] for p in record["passes"]]
        self.assertEqual(names, ["pass-01", "pass-02", "pass-03"])
        self.assertEqual([p["decision"] for p in record["passes"]], ["keep"] * 3)
        self.assertTrue(record["passes"][1]["resumed"])
        self.assertEqual(record["stop"]["reason"], "max-passes")   # counted from pass-01
        self.assertEqual(record["defects"]["D1"]["survived"], 2)   # continued, not restarted
        self.assertEqual(len(record["defects"]), 1)
        saved = read_json(self.root / "improve.json")
        self.assertEqual([p["name"] for p in saved["passes"]], names)
        md = (self.root / "improve.md").read_text()
        for name in names:
            self.assertIn(name, md)

    def test_max_passes_counts_the_earlier_passes(self):
        self.two_passes_then_a_failed_render()
        runner = FakeRunner(self.root, [card(6.0, mechanical=[mech()])])
        record = self.improver(runner, resume=True, max_passes=2).run()
        self.assertEqual([p["name"] for p in record["passes"]], ["pass-01", "pass-02"])
        self.assertEqual(record["stop"]["reason"], "max-passes")
        self.assertNotIn("improve/pass-03", " ".join(" ".join(c) for c in runner.calls))

    def test_a_failed_resume_still_keeps_the_history(self):
        self.two_passes_then_a_failed_render()
        runner = FakeRunner(self.root, [], fail={"render": 1})
        record = self.improver(runner, resume=True, max_passes=3).run()
        self.assertEqual([p["name"] for p in record["passes"]], ["pass-01", "pass-02"])
        self.assertEqual(record["passes"][1]["decision"], "failed")
        self.assertIn("D1", record["defects"])

    def test_rejudge_keeps_the_earlier_passes(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        first = FakeRunner(self.root, [card(5.5, mechanical=[mech()]),
                                       card(4.0, mechanical=[mech()])])
        before = self.improver(first, max_passes=2).run()
        self.assertEqual([p["decision"] for p in before["passes"]], ["keep", "revert"])
        runner = FakeRunner(self.root, [card(6.0, mechanical=[mech()])])
        record = self.improver(runner, rejudge=True, max_passes=2).run()
        self.assertEqual([p["name"] for p in record["passes"]], ["pass-01", "pass-02"])
        self.assertEqual([p["decision"] for p in record["passes"]], ["keep", "keep"])
        self.assertTrue(record["passes"][1]["rejudged"])
        self.assertEqual(record["defects"]["D1"]["survived"],
                         before["defects"]["D1"]["survived"])
        saved = read_json(self.root / "improve.json")
        self.assertEqual(len(saved["passes"]), 2)


class DoctorNote(ImproveCase):
    def test_doctor_points_at_improve_while_qa_is_not_ready(self):
        from ytstudio import doctor
        self.start_pass(card(5.0))
        report = doctor.check_project(workspace.Project(self.root))
        notes = [f for f in report.findings if "yt improve" in f.fix]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].level, "note")
        self.assertIn("--dry-run", notes[0].fix)

    def test_no_note_once_ready(self):
        from ytstudio import doctor
        self.start_pass(card(9.5))
        report = doctor.check_project(workspace.Project(self.root))
        self.assertFalse([f for f in report.findings if "yt improve" in f.fix])


class StoryReview(ImproveCase):
    """AUTO-93: a script the loop writes again is read by the story review;
    a reject stops the pass before voice is paid for, and improve.md says
    so with the verdict."""

    class Runner(FakeRunner):
        def __init__(self, root, cards=(), *, verdict="reject", **kw):
            super().__init__(root, cards, **kw)
            self.verdict = verdict

        def __call__(self, argv):
            code = super().__call__(argv)
            if argv[0] == "script":
                write_json(self.root / "script.json", {
                    "target_seconds": 15, "sections": [], "written_by": "the pass",
                    "findings": ["error: story review: reject (train Poor)"]
                    if self.verdict == "reject" else [],
                    "review": {"verdict": self.verdict, "grades": {"train": "Poor"},
                               "unresolved": []}})
            return code

    def test_a_rejected_script_stops_the_pass_with_the_verdict_in_improve_md(self):
        self.start_pass(card(5.0, findings=[prose("the story never pays off", owner="script")]))
        FakeEngine.route = route_all_to("script")
        runner = self.Runner(self.root, [card(6.0)])
        record = self.improver(runner, max_passes=2, budget_usd=100).run()
        self.assertEqual(runner.stages(), ["script"])
        self.assertIn("--review", runner.calls[0])
        self.assertEqual(record["stop"]["reason"], "story-review")
        self.assertIn("reject", record["stop"]["detail"])
        self.assertEqual(record["passes"][0]["decision"], "stopped")
        # the project goes back to the best pass; the attempt is kept
        self.assertNotIn("written_by", read_json(self.root / "script.json"))
        attempt = self.root / "improve" / "pass-01" / "attempt" / "script.json"
        self.assertEqual(read_json(attempt)["review"]["verdict"], "reject")
        md = (self.root / improve.REPORT_MD).read_text()
        self.assertIn("story-review", md)
        self.assertIn("story review", md)
        self.assertIn("reject", md)

    def test_a_script_the_story_review_did_not_reject_goes_on(self):
        self.start_pass(card(5.0, findings=[prose("the story never pays off", owner="script")]))
        FakeEngine.route = route_all_to("script")
        runner = self.Runner(self.root, [card(6.0)], verdict="consider")
        record = self.improver(runner, max_passes=1, budget_usd=100).run()
        self.assertEqual(runner.stages()[0], "script")
        self.assertIn("qa", runner.stages())
        self.assertNotEqual(record["stop"]["reason"], "story-review")


if __name__ == "__main__":
    unittest.main()
