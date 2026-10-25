"""qa.py: the review-and-improve harness over the rendered film.

The deterministic half is what these pin: where the evidence is sampled
(never at a shot's boundary, always across the runtime), how passes are
numbered, what the publication bar is, and how one pass is compared with the
one before it. The scoring itself is the model's, through the harness.
"""

import argparse
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from ytstudio import doctor, harness, narrative, qa, workspace
from tests import test_qa_motion as motion
from tests.test_harness_decisions import blank

#: A scorecard's story reading, as a pass written after AUTO-99 files it.
STORY = {"premise_restated": "the tail points away from the Sun because a wind blows it",
         "sections": [{"section": 1, "viewer_has_at_end": "a tail in front of a comet"}],
         "satisfaction": "4 satisfied", "satisfaction_why": "the payoff lands"}


def _build():
    shots = []
    t = 0.0
    for n, (label, seconds, asset) in enumerate([
            ("1.1", 4, "effect"), ("1.2", 6, "effect"), ("2.1", 10, "image"),
            ("2.2", 8, "effect"), ("3.1", 5, "placeholder"), ("3.2", 7, "effect")]):
        shots.append({"label": label, "at": t, "seconds": seconds, "asset": asset,
                      "section": "Open" if label[0] == "1" else
                                 "Middle" if label[0] == "2" else "Close"})
        t += seconds
    return {"scene": {"duration": t}, "shots": shots, "placeholders": ["3.1"],
            "findings": []}


def _beats():
    """The same film, its shots tagged by the screenplay's `function`
    (AUTO-90): a re-hook mid-film and the payoff before the last shot."""
    build = _build()
    for shot in build["shots"]:
        shot["function"] = {"1.1": "hook", "2.1": "rehook", "2.2": "payoff"}.get(
            shot["label"], "context")
    return build


class Edge(unittest.TestCase):
    """Which sampled shots a single mechanical defect blocks on: the hook, the
    ending and the payoff beat, matched as whole tokens of `why`."""

    def frozen_on(self, label):
        rows = [r for r in qa.sample(_beats()) if r["label"] == label]
        rows[0]["motion"] = {"steps": [0.0, 0.0], "mean": 0.0, "max": 0.0,
                             "frozen": True, "jump": None, "verdict": "frozen"}
        found = [f for f in qa.mechanical_findings(rows) if f["kind"] == "frozen"]
        self.assertEqual(len(found), 1)
        return found[0]["severity"]

    def test_a_defect_on_the_payoff_beat_blocks(self):
        self.assertEqual(self.frozen_on("2.2"), "blocking")

    def test_a_defect_on_a_rehook_does_not_block(self):
        self.assertEqual(self.frozen_on("2.1"), "minor")

    def test_tokens_not_substrings(self):
        self.assertFalse(qa._on_edge({"why": "beat: rehook"}))
        self.assertFalse(qa._on_edge({"why": "section start: Hooke's law; 50% of runtime"}))
        self.assertTrue(qa._on_edge({"why": "25% of runtime; beat: payoff"}))
        self.assertTrue(qa._on_edge({"why": "hook; section start: Open"}))
        self.assertTrue(qa._on_edge({"why": "ending"}))


class Sampling(unittest.TestCase):
    def test_samples_sit_at_shot_midpoints_across_the_runtime(self):
        samples = qa.sample(_build())
        times = [s["t"] for s in samples]
        self.assertEqual(times, sorted(times))
        mids = {round(s["at"] + s["seconds"] / 2, 3) for s in _build()["shots"]}
        self.assertTrue(all(t in mids for t in times), times)
        why = " ".join(s["why"] for s in samples)
        for word in ("hook", "section", "25%", "50%", "75%", "ending", "effect"):
            self.assertIn(word, why)

    def test_the_hook_and_the_ending_survive_a_tight_limit(self):
        samples = qa.sample(_build(), limit=3)
        labels = [s["label"] for s in samples]
        self.assertEqual(len(samples), 3)
        self.assertIn("1.1", labels)
        self.assertIn("3.2", labels)

    def test_the_hook_and_the_ending_survive_a_tight_limit_beside_the_beats(self):
        samples = qa.sample(_beats(), limit=4)
        self.assertEqual(sorted(s["label"] for s in samples), ["1.1", "2.1", "2.2", "3.2"])

    def test_every_rehook_and_payoff_shot_is_sampled_with_its_beat(self):
        samples = {s["label"]: s["why"] for s in qa.sample(_beats())}
        self.assertIn("beat: rehook", samples["2.1"].split("; "))
        self.assertIn("beat: payoff", samples["2.2"].split("; "))
        # the positional rows stay: a mislabel cannot drop the real ending
        self.assertIn("hook", samples["1.1"].split("; "))
        self.assertIn("ending", samples["3.2"].split("; "))

    def test_many_rehooks_do_not_crowd_out_the_section_starts(self):
        # nothing caps the rehooks a screenplay marks; a --frames below the
        # shot count still keeps the hook, the ending and every section
        # (sample's promise; by default every shot is sampled, AUTO-103)
        shots = []
        for n in range(20):
            section = f"S{n // 4 + 1}"
            function = ("hook" if n == 0 else "context" if n % 4 == 0 else "rehook")
            shots.append({"label": f"{n // 4 + 1}.{n % 4 + 1}", "at": n * 5.0,
                          "seconds": 5, "asset": "effect", "section": section,
                          "function": function})
        build = {"scene": {"duration": 100}, "shots": shots}
        samples = qa.sample(build, limit=14)
        self.assertEqual(len(samples), 14)
        why = [token for s in samples for token in s["why"].split("; ")]
        for section in ("S1", "S2", "S3", "S4", "S5"):
            self.assertIn(f"section start: {section}", why)
        self.assertIn("hook", why)
        self.assertIn("ending", why)
        self.assertIn("beat: rehook", why)

    def test_the_payoff_comes_before_the_section_starts(self):
        build = _beats()
        samples = qa.sample(build, limit=3)
        self.assertEqual(sorted(s["label"] for s in samples), ["1.1", "2.2", "3.2"])

    def test_a_rehook_beside_the_hook_still_names_both(self):
        build = _beats()
        build["shots"][0]["function"] = "rehook"
        why = qa.sample(build)[0]["why"].split("; ")
        self.assertIn("hook", why)
        self.assertIn("beat: rehook", why)

    def test_frame_names_carry_the_timestamp(self):
        self.assertEqual(qa.frame_name(83.456), "t_01m23s456.png")
        self.assertEqual(qa.frame_name(0), "t_00m00s000.png")


class Passes(unittest.TestCase):
    def test_passes_are_numbered_in_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(qa.next_pass(root).name, "pass-01")
            (root / "qa" / "pass-01").mkdir(parents=True)
            (root / "qa" / "pass-03").mkdir(parents=True)
            self.assertEqual(qa.next_pass(root).name, "pass-04")
            self.assertEqual(qa.latest_pass(root).name, "pass-03")


class Bar(unittest.TestCase):
    def scores(self, **over):
        base = {k: 9.2 for k in qa.CATEGORIES}
        base.update(over)
        return base

    def test_ready_needs_overall_nine_and_no_category_below_eight_and_a_half(self):
        verdict, reasons = qa.verdict_of(self.scores(), 9.1, blocking=[])
        self.assertEqual(verdict, qa.READY)
        self.assertEqual(reasons, [])
        verdict, reasons = qa.verdict_of(self.scores(voiceover=8.4), 9.1, blocking=[])
        self.assertEqual(verdict, qa.NOT_READY)
        self.assertTrue(any("voiceover" in r for r in reasons))
        verdict, reasons = qa.verdict_of(self.scores(), 8.9, blocking=[])
        self.assertEqual(verdict, qa.NOT_READY)
        verdict, reasons = qa.verdict_of(self.scores(), 9.5, blocking=["a placeholder"])
        self.assertEqual(verdict, qa.NOT_READY)

    def test_a_gate_problem_is_a_reason_and_keeps_ready_out_of_reach(self):
        # AUTO-148: perfect scores over a frames-only gate are not READY
        why = "the last gate checked decoded frames only; delivery needs the technical gate"
        verdict, reasons = qa.verdict_of(self.scores(), 9.5, blocking=[], gate_problem=why)
        self.assertEqual(verdict, qa.NOT_READY)
        self.assertEqual(reasons, [f"delivery gate: {why}"])
        self.assertEqual(qa.verdict_of(self.scores(), 9.5, blocking=[], gate_problem="")[0],
                         qa.READY)

    def test_script_blockers_name_a_story_review_that_rejected_or_could_not_judge(self):
        reject = {"review": {"verdict": "reject",
                             "unresolved": [{"problem": "the train never leaves"}]}}
        self.assertEqual(qa.script_blockers(reject), [
            "script: story review rejected the screenplay (the train never leaves)"])
        self.assertEqual(qa.script_blockers({"review": {"verdict": "not_evidenced"}}), [
            "script: story review could not judge the screenplay (not_evidenced: fewer "
            "than two valid samples)"])
        for script in ({"review": {"verdict": "consider"}},
                       {"review": {"verdict": "recommend"}}, {"sections": []}, None):
            self.assertEqual(qa.script_blockers(script), [], script)

    def test_mechanical_blockers_come_from_the_manifests(self):
        blockers = qa.mechanical_blockers(_build(), gate={"exit": 1, "triage": {
            "blocking": [{"what": "loudness -16 LUFS"}]}})
        joined = " ".join(blockers)
        self.assertIn("3.1", joined)
        self.assertIn("loudness", joined)
        self.assertEqual(qa.mechanical_blockers({"placeholders": [], "findings": []},
                                                gate=None), [])

    def test_a_standing_fact_check_blocker_is_a_mechanical_blocker(self):
        # AUTO-133: script.fact_blockers, the list the script gate reads;
        # important flags and resolved blockers are not blockers.
        script = {"sections": [{"shots": [{"n": 1, "label": "1.1"},
                                          {"n": 2, "label": "1.2"}]}],
                  "check": {"flags": [
                      {"n": 2, "severity": "blocker", "problem": "no source for it"},
                      {"n": 0, "severity": "blocker", "problem": "the title overclaims"},
                      {"n": 1, "severity": "blocker", "problem": "p", "resolved": True},
                      {"n": 1, "severity": "important", "problem": "overstated"}]}}
        empty = {"placeholders": [], "findings": []}
        self.assertEqual(qa.mechanical_blockers(empty, gate=None, script=script), [
            "fact-check: blocker at 1.2: no source for it",
            "fact-check: blocker at script-wide: the title overclaims"])
        self.assertEqual(qa.mechanical_blockers(empty, gate=None,
                                                script={"sections": script["sections"]}), [])


class AccuracyRecord(unittest.TestCase):
    """AUTO-150: the bar reason, the price and the scorecard keys."""

    def test_verdict_of_accuracy_keyword(self):
        scores = {k: 9.2 for k in qa.CATEGORIES}
        old = qa.verdict_of(scores, 9.2, blocking=[])
        self.assertEqual(old, (qa.READY, []))
        self.assertEqual(qa.verdict_of(scores, 9.2, blocking=[], accuracy=None), old)
        self.assertEqual(qa.verdict_of(scores, 9.2, blocking=[], accuracy={
            "status": "done", "bar": False}), old)
        verdict, reasons = qa.verdict_of(scores, 9.2, blocking=[], accuracy={
            "status": qa.NOT_EVIDENCED, "bar": True, "why": "vfx.json names no model",
            "command": "yt qa --accuracy-model <another family>"})
        self.assertEqual(verdict, qa.NOT_READY)
        self.assertEqual(reasons, [
            "on-screen accuracy is not evidenced: vfx.json names no model; READY cannot "
            "be judged until `yt qa --accuracy-model <another family>`"])

    def test_pass_usd_counts_accuracy_once(self):
        # one scoring sample asked: the reviewer's part is priced for three,
        # the accuracy check once
        card = {"usage": {"cost_usd": 1.1}, "scoring": {"asked": 1, "valid": 1},
                "engine": {"decisions": []}, "accuracy_engine": {"cost_usd": 0.5}}
        self.assertEqual(qa.pass_usd(card), round(0.6 * qa.QA_SAMPLES + 0.5, 2))
        del card["accuracy_engine"]
        self.assertEqual(qa.pass_usd(card), round(1.1 * qa.QA_SAMPLES, 2))

    def test_fact_check_key_kept(self):
        from ytstudio import improve
        from ytstudio.core import read_json, write_json
        fact = [{"n": 2, "label": "1.2", "problem": "no source", "fix": "cut it",
                 "kept": False}]
        final = {"status": "done", "model": "opus", "checked": 1, "not_checked": [],
                 "findings": [{"shot": "1.1", "quote": "40%", "claim_id": "c1",
                               "problem": "number-mismatch", "severity": "blocking",
                               "fix": "draw 14%", "owner": "vfx", "start": 3.0,
                               "in_screenplay": False}], "cost": 0.2}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scorecard.json"
            write_json(path, {"accuracy": {"fact_check": fact, "final": final}})
            card = read_json(path)
        self.assertEqual(card["accuracy"]["fact_check"], fact)
        rows = improve.collect(card)
        facts = [r for r in rows if r["source"] == "fact-check"]
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["problem"], "fact-check blocker at 1.2: no source")
        shown = [r for r in rows if r.get("kind") == "accuracy"]
        self.assertEqual(len(shown), 1)
        self.assertEqual((shown[0]["source"], shown[0]["owner"], shown[0]["shot"],
                          shown[0]["timestamp"]), ("qa", "vfx", "1.1", 3.0))


class ScratchTake(unittest.TestCase):
    """A stub or espeak narration proves the timeline and is never published
    (doctor.SCRATCH_ENGINES): QA blocks READY on it (AUTO-152), where doctor
    only warned and the film could still pass."""

    EMPTY = {"placeholders": [], "findings": []}
    WHERE = "--project /films/one-take"

    def voice(self, engine, takes):
        return {"engine": engine, "takes": [{"label": f"1.{n}", "text": "a line",
                                             "engine": e} for n, e in enumerate(takes, 1)]}

    def blockers(self, voice):
        return qa.mechanical_blockers(self.EMPTY, gate=None, voice=voice, where=self.WHERE)

    def test_espeak_and_stub_takes_block(self):
        for engine in doctor.SCRATCH_ENGINES:
            with self.subTest(engine=engine):
                got = self.blockers(self.voice(engine, [engine] * 4))
                self.assertEqual(len(got), 1, got)
                self.assertTrue(got[0].startswith("voice: narration is a"), got[0])
                self.assertIn(f"{engine} scratch take (4 of 4 takes)", got[0])
                self.assertIn(f"run yt voice {self.WHERE} --engine auto", got[0])
        self.assertIn("an espeak scratch take",
                      self.blockers(self.voice("espeak", ["espeak"]))[0])
        # a kokoro read with three takes left from a stub run
        got = self.blockers(self.voice("kokoro", ["kokoro", "stub", "stub", "kokoro", "stub"]))
        self.assertEqual(len(got), 1, got)
        self.assertIn("a stub scratch take (3 of 5 takes)", got[0])
        # a take that names no engine was read by the voice's own
        got = self.blockers({"engine": "espeak", "takes": [{"label": "1.1"}, {"label": "1.2"}]})
        self.assertIn("(2 of 2 takes)", got[0])
        # two scratch engines, one line each
        got = self.blockers(self.voice("espeak", ["espeak", "stub"]))
        self.assertEqual(len(got), 2, got)
        self.assertTrue(all(g.startswith("voice:") for g in got))

    def test_elevenlabs_and_kokoro_do_not_block(self):
        for engine in ("elevenlabs", "kokoro", "chatterbox"):
            with self.subTest(engine=engine):
                self.assertEqual(self.blockers(self.voice(engine, [engine] * 3)), [])

    def test_wordless_and_voice_none_do_not_block(self):
        self.assertEqual(self.blockers(None), [])
        self.assertEqual(self.blockers({}), [])
        self.assertEqual(self.blockers({"engine": "stub", "wordless": True, "takes": []}), [])
        # the old call, with no voice at all, is unchanged
        self.assertEqual(qa.mechanical_blockers(self.EMPTY, gate=None), [])

    def test_a_scratch_blocker_makes_verdict_not_ready(self):
        scores = {k: 9.5 for k in qa.CATEGORIES}
        self.assertEqual(qa.verdict_of(scores, 9.5, blocking=[])[0], qa.READY)
        blockers = self.blockers(self.voice("espeak", ["espeak"] * 2))
        verdict, reasons = qa.verdict_of(scores, 9.5, blocking=blockers)
        self.assertEqual(verdict, qa.NOT_READY)
        self.assertEqual(reasons, ["1 blocking item(s)"])


class FallbackPalette(unittest.TestCase):
    """A film in design.py's house fallback palette is not READY, whatever it
    scores (AUTO-155); the blocker names the stage that owns the fix."""

    def manifest(self, **design):
        return {"placeholders": [], "findings": [], "design": design}

    def test_a_fallback_design_blocks(self):
        blockers = qa.mechanical_blockers(
            self.manifest(source="fallback", fallback="no styles.json"), gate=None)
        self.assertEqual(len(blockers), 1, blockers)
        self.assertTrue(blockers[0].startswith("styles:"), blockers[0])
        self.assertIn("yt styles --rich", blockers[0])
        self.assertIn("no styles.json", blockers[0])

    def test_a_build_from_before_the_reason_still_blocks(self):
        blockers = qa.mechanical_blockers(self.manifest(source="fallback"), gate=None)
        self.assertEqual(len(blockers), 1, blockers)
        self.assertIn("source fallback", blockers[0])

    def test_a_styled_or_older_build_does_not(self):
        self.assertEqual(qa.mechanical_blockers(self.manifest(source="styles.json"),
                                                gate=None), [])
        self.assertEqual(qa.mechanical_blockers({"placeholders": [], "findings": []},
                                                gate=None), [])

    def test_the_build_warning_is_not_lifted_twice(self):
        manifest = self.manifest(source="fallback", fallback="no styles.json")
        manifest["findings"] = [
            "warning: design: the film is in design.py's house fallback palette "
            "(no styles.json), not its own; run yt styles --rich, then yt build"]
        blockers = [b for b in qa.mechanical_blockers(manifest, gate=None) if "palette" in b]
        self.assertEqual(len(blockers), 1, blockers)

    def test_every_score_at_the_top_is_still_not_ready(self):
        blockers = qa.mechanical_blockers(
            self.manifest(source="fallback", fallback="no styles.json"), gate=None)
        verdict, reasons = qa.verdict_of({k: 9.5 for k in qa.CATEGORIES}, 9.5,
                                         blocking=blockers)
        self.assertEqual(verdict, qa.NOT_READY)
        self.assertTrue(reasons)


class Comparison(unittest.TestCase):
    def test_regressions_are_categories_that_dropped(self):
        prev = {"scores": {k: 9.0 for k in qa.CATEGORIES}, "overall": 9.0}
        cur = {"scores": {k: 9.0 for k in qa.CATEGORIES} | {"editing_pacing": 8.3,
                                                             "voiceover": 9.4},
               "overall": 8.9}
        diff = qa.compare(prev, cur)
        self.assertEqual(diff["regressions"], ["editing_pacing"])
        self.assertAlmostEqual(diff["deltas"]["voiceover"], 0.4)
        self.assertAlmostEqual(diff["overall_delta"], -0.1)
        self.assertEqual(qa.compare(None, cur)["regressions"], [])

    def test_findings_markdown_lists_every_finding_with_its_owner(self):
        card = {"scores": {k: 9.0 for k in qa.CATEGORIES}, "overall": 9.0,
                "verdict": qa.READY, "rationale": "fine",
                "findings": [{"timestamp": 12.5, "frame": "t_00m12s500.png",
                              "problem": "caption enters the stage band",
                              "why": "collides with the figure", "root_cause": "cue layout",
                              "owner": "build", "fix": "captions_off for 2.1",
                              "severity": "blocking"}],
                "blocking": [], "not_evidenced": ["music_score"]}
        text = qa.findings_md("pass-02", card, [{"t": 12.5, "why": "50%", "label": "2.1",
                                                 "section": "Middle"}],
                              {"regressions": [], "deltas": {}, "overall_delta": 0.0},
                              reasons=[])
        self.assertIn("# pass-02", text)
        self.assertIn("00:12.500", text)
        self.assertIn("build", text)
        self.assertIn("music_score", text)


class OwnerEnum(unittest.TestCase):
    """A finding's owner is a stage, upstream-fact or external; the harness
    refuses any other word and asks again (AUTO-53)."""

    def card(self, owner):
        return {"scores": {k: 8.0 for k in qa.CATEGORIES}, "overall": 8.0,
                "verdict": qa.NOT_READY, "rationale": "r", "blocking": [],
                "not_evidenced": [], "story": STORY,
                "findings": [{"timestamp": 1.0, "problem": "p", "why": "w",
                              "root_cause": "c", "owner": owner, "fix": "f",
                              "severity": "minor"}]}

    def inspect(self, form_owner, retry_owner=None):
        import json
        from tests import test_harness as th
        from ytstudio import harness
        from claude_engine import Event
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        cwd = root / "qa" / "pass-01"
        cwd.mkdir(parents=True)
        th.FakeEngine.json_calls, th.FakeEngine.stream_calls = [], []
        th.FakeEngine.texts, th.FakeEngine.fail = ["done"], False
        th.FakeEngine.answers = [self.card(retry_owner)] if retry_owner else []

        class Filing(th.FakeEngine):
            def stream(self_, prompt, system=None):
                (cwd / "scorecard.form.json").write_text(
                    json.dumps(self.card(form_owner)), encoding="utf-8")
                yield Event(type="assistant", text="I filed the scorecard.")
                yield Event(type="result", raw={})

        saved, harness.ENGINE = harness.ENGINE, Filing
        self.addCleanup(setattr, harness, "ENGINE", saved)
        h = harness.Harness(root, "qa", model="sonnet", verbose=False)
        return h.inspect("pass-01", "p", qa.SCORECARD_SCHEMA, cwd=cwd,
                         form="scorecard.form.json"), th.FakeEngine.json_calls

    def test_the_schema_owner_is_the_stage_names_and_the_two_human_routes(self):
        owner = qa.SCORECARD_SCHEMA["properties"]["findings"]["items"]["properties"]["owner"]
        self.assertEqual(owner["enum"], list(qa.OWNERS))
        self.assertTrue({"vfx", "sound", "build", "upstream-fact", "external"} <= set(qa.OWNERS))
        self.assertNotIn("editor", qa.OWNERS)
        self.assertTrue(all(qa.DUTIES.get(o) for o in qa.OWNERS))

    def test_an_owner_in_the_enum_is_taken_as_filed(self):
        result, asked = self.inspect("sound")
        self.assertEqual(result["findings"][0]["owner"], "sound")
        self.assertEqual(asked, [])

    def test_an_owner_outside_the_enum_is_refused_and_asked_again(self):
        result, asked = self.inspect("editor", retry_owner="build")
        self.assertEqual(len(asked), 1)
        self.assertEqual(result["findings"][0]["owner"], "build")

    def test_an_owner_still_outside_the_enum_after_the_retry_is_an_error(self):
        from ytstudio import harness
        with self.assertRaises(harness.HarnessError) as ctx:
            self.inspect("editor", retry_owner="audio")
        self.assertIn("owner", str(ctx.exception))

    def test_the_enum_check_walks_nested_arrays(self):
        from ytstudio import harness
        bad = harness.enum_violations(self.card("gate"), qa.SCORECARD_SCHEMA)
        self.assertEqual(bad, ["findings[0].owner='gate'"])
        self.assertEqual(harness.enum_violations(self.card("vfx"), qa.SCORECARD_SCHEMA), [])


# --------------------------------------------------------------------------- #
# the story, read from the film (AUTO-99)
# --------------------------------------------------------------------------- #

def _script():
    """A screenplay with the plan's story fields (AUTO-90): two sections, a
    question opened in the first and closed in the second, a re-hook shot."""
    def shot(label, text, fn="context"):
        return {"label": label, "narration": text, "visual": f"picture {label}",
                "function": fn, "shows": f"what {label} demonstrates", "seconds": 3}
    return {
        "train": "A comet's tail must point somewhere: what pushes it?",
        "promise": "why the tail runs ahead of the comet",
        "payoff": "the solar wind, shown bending the tail",
        "stakes": "the same wind can put a power grid out",
        "viewer_stake": "your lights",
        "questions": [{"id": "Q1", "text": "What pushes the tail?", "opened_in": 1,
                       "closed_in": 2, "left_open_why": ""},
                      {"id": "Q2", "text": "Where does the wind end?", "opened_in": 2,
                       "closed_in": 0, "left_open_why": "the next film"}],
        "ending": {"payoff_line": "It is showing you the wind.", "callback": "the tail",
                   "next_film": "the heliopause", "device": "scale"},
        "sections": [
            {"heading": "Open", "opens": ["Q1"], "closes": [], "rehook": "",
             "escalation": "the tail is in front",
             "shots": [shot("1.1", "This comet is leaving the Sun.", "hook"),
                       shot("1.2", "And its tail is in front.")]},
            {"heading": "Close", "opens": ["Q2"], "closes": ["Q1"], "rehook": "Q1",
             "escalation": "light is too weak",
             "shots": [shot("2.1", "Light alone is too weak.", "rehook"),
                       shot("2.2", "It is showing you the wind.", "payoff")]}]}


class StoryRole(unittest.TestCase):
    """qa.ROLE carries the narrative rubric rendered once, never retyped."""

    def test_the_role_renders_its_rubric_sentences_from_the_narrative_module(self):
        rendered = narrative.rubric_text("writer", keys=qa.STORY_RUBRIC)
        self.assertIn(rendered, qa.ROLE)
        for key in ("hook", "stakes", "open_loops", "reveal_setup", "ending"):
            self.assertIn(key, qa.STORY_RUBRIC)
            self.assertIn(narrative.criterion(key).question.split("{")[0], qa.ROLE)
        self.assertIn(narrative.LONGER, qa.ROLE)
        self.assertIn("narrative_clarity", qa.ROLE)
        self.assertIn("editing_pacing", qa.ROLE)

    def test_the_role_carries_the_tension_row(self):
        # AUTO-136: rising stakes across sections now has a row of its own
        self.assertIn("tension", qa.STORY_RUBRIC)
        self.assertIn(narrative.criterion("tension").levels["Excellent"], qa.ROLE)

    def test_the_categories_are_eleven(self):
        # AUTO-146: editorial_identity is the eleventh, judged on the frames
        # and the narration, so not one of the listener's
        self.assertEqual(len(qa.CATEGORIES), 11)
        self.assertEqual(qa.CATEGORIES[-1], "editorial_identity")
        self.assertNotIn("editorial_identity", qa.AUDIO)
        self.assertNotIn("story", qa.CATEGORIES)

    def test_the_screenplays_duty_names_the_story(self):
        duty = qa.DUTIES["script"]
        for term in qa.STORY_DUTY:
            self.assertIn(term, duty)
        self.assertTrue(duty.startswith("the screenplay: the story ("), duty)
        for word in ("shots", "narration", "supers", "timing plan"):
            self.assertIn(word, duty)


class StorySchema(unittest.TestCase):
    def test_the_story_block_is_required_and_validates_under_blank(self):
        schema = qa.SCORECARD_SCHEMA
        self.assertIn("story", schema["required"])
        story = schema["properties"]["story"]
        self.assertEqual(set(story["required"]),
                         {"premise_restated", "sections", "satisfaction", "satisfaction_why"})
        self.assertEqual(story["properties"]["satisfaction"]["enum"], list(qa.SATISFACTION))
        self.assertEqual([s[0] for s in qa.SATISFACTION], list("12345"))
        self.assertIn(STORY["satisfaction"], qa.SATISFACTION)
        self.assertEqual(set(story["properties"]["sections"]["items"]["required"]),
                         {"section", "viewer_has_at_end"})
        card = blank(schema)
        self.assertEqual(harness.missing_fields(card, schema), [])
        self.assertEqual(harness.enum_violations(card, schema), [])
        self.assertEqual(harness.missing_fields(dict(card, story=STORY), schema), [])
        self.assertTrue(harness.missing_fields({k: v for k, v in card.items()
                                                if k != "story"}, schema))

    def test_an_older_scorecard_without_the_story_still_compares(self):
        old = {"pass": "pass-01", "scores": {k: 8.0 for k in qa.CATEGORIES}, "overall": 8.0}
        new = {"scores": {k: 8.2 for k in qa.CATEGORIES}, "overall": 8.2, "story": STORY}
        diff = qa.compare(old, new)
        self.assertEqual(diff["against"], "pass-01")
        self.assertEqual(diff["regressions"], [])
        self.assertAlmostEqual(diff["overall_delta"], 0.2)

    def test_satisfaction_is_not_evidenced_until_the_channel_has_retention(self):
        card = qa.unproven_satisfaction({"not_evidenced": ["music_score"], "story": STORY},
                                        retention=False)
        self.assertEqual(card["not_evidenced"][0], "music_score")
        note = card["not_evidenced"][-1]
        self.assertIn("satisfaction", note)
        self.assertIn(qa.RETENTION, note)
        self.assertEqual(card["story"]["satisfaction"], STORY["satisfaction"])  # recorded
        kept = qa.unproven_satisfaction({"not_evidenced": [], "story": STORY}, retention=True)
        self.assertEqual(kept["not_evidenced"], [])

    def test_findings_markdown_carries_the_story_reading(self):
        card = {"scores": {k: 8.0 for k in qa.CATEGORIES}, "overall": 8.0,
                "verdict": qa.NOT_READY, "findings": [], "blocking": [],
                "not_evidenced": [], "rationale": "r", "story": STORY}
        text = qa.findings_md("pass-03", card, [], {"deltas": {}, "regressions": []},
                              reasons=[])
        self.assertIn("## Story", text)
        self.assertIn(STORY["premise_restated"], text)
        self.assertIn("a tail in front of a comet", text)
        self.assertIn(STORY["satisfaction"], text)
        older = dict(card)
        older.pop("story")
        self.assertNotIn("## Story", qa.findings_md("pass-01", older, [], {}, reasons=[]))


class StoryBrief(unittest.TestCase):
    def build(self):
        shots, t = [], 0.0
        for sec in _script()["sections"]:
            for shot in sec["shots"]:
                shots.append({"label": shot["label"], "at": t, "seconds": 3.0,
                              "section": sec["heading"], "function": shot["function"]})
                t += 3.0
        return {"scene": {"duration": t}, "shots": shots}

    def test_the_brief_holds_the_plans_story_with_where_each_question_opens_and_closes(self):
        story = qa.story_brief(_script(), self.build())
        for key in ("train", "promise", "stakes", "payoff_line"):
            self.assertTrue(story[key], key)
        self.assertEqual(story["payoff_line"], "It is showing you the wind.")
        q1, q2 = story["questions"]
        self.assertEqual((q1["opened_in"], q1["opened_at"]), (1, 0.0))
        self.assertEqual((q1["closed_in"], q1["closed_at"]), (2, 6.0))
        self.assertIsNone(q2["closed_at"])
        self.assertEqual(q2["left_open_why"], "the next film")
        self.assertEqual([r["shot"] for r in story["rehooks"]], ["2.1"])
        self.assertEqual(story["rehooks"][0]["at"], 6.0)
        self.assertEqual(story["sections"][1]["escalation"], "light is too weak")

    def test_a_screenplay_from_before_the_story_fields_has_no_brief(self):
        self.assertIsNone(qa.story_brief({"sections": [{"heading": "x", "shots": []}]}, {}))
        self.assertIsNone(qa.story_brief({}, {}))

    def test_the_narration_is_written_line_by_line_with_its_shot_and_time(self):
        text = qa.narration_text(_script(), self.build())
        lines = text.splitlines()
        self.assertTrue(any(line.startswith("1.1") and "This comet is leaving the Sun."
                            in line for line in lines), text)
        self.assertIn("00:09.000", text)          # shot 2.2 is placed at 9 s
        self.assertIn("It is showing you the wind.", text)
        self.assertEqual(qa.narration_text({}, {}), "")

    def test_the_narration_is_what_was_voiced_when_voice_has_takes(self):
        # The film as told is the soundtrack: a take's words win over the
        # screenplay's line, and a shot with no take was not spoken.
        voice = {"takes": [{"label": "1.1", "text": "This comet, look, is leaving the Sun."},
                           {"label": "2.2", "text": "It is showing you the wind."}]}
        text = qa.narration_text(_script(), self.build(), voice)
        self.assertIn("as voiced", text.splitlines()[0])
        self.assertIn("This comet, look, is leaving the Sun.", text)
        self.assertNotIn("And its tail is in front.", text)
        unvoiced = qa.narration_text(_script(), self.build(), {"takes": []})
        self.assertIn("not yet voiced", unvoiced.splitlines()[0])
        self.assertIn("And its tail is in front.", unvoiced)


class StoryStage(unittest.TestCase):
    """The whole stage: the story brief in the prompt, narration.txt in the pass."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qa-story-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / "work"
        self.root = self.work / "one-take"
        self.root.mkdir(parents=True)
        (self.root / "scene.xml").write_text("<scene/>")
        (self.root / "build.json").write_text(json.dumps(motion._build()))
        script = _script()
        first = script["sections"][0]
        shots = [dict(first["shots"][0], label=label) for label in ("1.1", "1.2", "1.3", "1.4")]
        shots[3]["narration"] = "It is showing you the wind."
        script["sections"] = [dict(first, shots=shots)]
        (self.root / "script.json").write_text(json.dumps(script))
        saved = harness.ENGINE, qa.RENDERER
        self.addCleanup(setattr, qa, "RENDERER", saved[1])
        self.addCleanup(setattr, harness, "ENGINE", saved[0])
        seen = self.seen = {"prompts": []}
        card = dict(motion.SCORECARD, story=STORY)
        # The cards the reviewer files, one per inspection; the last repeats.
        self.cards = cards = [card]

        class Reader(motion.FakeEngine):
            def stream(self_, prompt, system=None):
                from claude_engine import Event
                cwd = Path(self_.config.cwd)
                seen["prompt"], seen["system"] = prompt, system
                seen["prompts"].append(prompt)
                seen["narration"] = ((cwd / "narration.txt").read_text()
                                     if (cwd / "narration.txt").is_file() else None)
                answer = cards.pop(0) if len(cards) > 1 else cards[0]
                yield Event(type="assistant", text="```json\n" + json.dumps(answer) + "\n```")
                yield Event(type="result", raw={})

        harness.ENGINE = Reader

        def render(args, scene):
            def job(j):
                out = j["out"]
                if "frames" in j:
                    a, b = (int(v) for v in j["frames"].split(".."))
                    for f in range(a, b):
                        motion._square(Path(out.replace("%04d", f"{f:04d}")), 100 + f // 2)
                else:
                    motion._square(Path(out), 100)
                return 0
            return job
        qa.RENDERER = render

    def args(self, **kw):
        base = dict(project=str(self.root), title=None, index=None, source=None,
                    stdin=None, genre="short", description="one take", work=str(self.work),
                    scene=None, frame_limit=4, quality="preview", reuse=None,
                    host=None, local=False, fresh=False, quiet=True, model="sonnet",
                    in_build=None, in_script=None, in_voice=None, in_sound=None,
                    in_styles=None, in_gate=None, house=None, no_house=True)
        base.update(kw)
        return argparse.Namespace(**base)

    def run_qa(self, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return qa.run(self.args(**kw))

    def test_the_prompt_names_the_shots_no_still_was_taken_of(self):
        # AUTO-103: under a tight --frames the reviewer is told which shots it
        # has no picture of before it scores, not only the scorecard after.
        self.run_qa(frame_limit=2)
        prompt = self.seen["prompt"]
        samples = json.loads((self.root / "qa" / "pass-01" / "samples.json").read_text())
        missing = sorted({"1.1", "1.2", "1.3", "1.4"} - {r["label"] for r in samples})
        self.assertEqual(len(missing), 2)
        line = next(l for l in prompt.splitlines() if "no still" in l)
        for label in missing:
            self.assertIn(label, line)
        self.run_qa(frame_limit=None)
        self.assertNotIn("no still", self.seen["prompt"])

    def test_the_prompt_carries_the_story_and_the_narration_is_evidence(self):
        self.run_qa()
        prompt = self.seen["prompt"]
        self.assertIn("The screenplay's story", prompt)
        self.assertIn("A comet's tail must point somewhere", prompt)
        self.assertIn("It is showing you the wind.", prompt)
        self.assertIn("narration.txt", prompt)
        # attention 23 has a direction: the explanation should need both
        self.assertIn("It should need both", prompt)
        # each frame says what its picture must demonstrate of its line
        self.assertIn("what 1.1 demonstrates", prompt)
        self.assertIn(qa.ROLE, self.seen["system"])
        # written before the inspection, so the digest of its evidence covers it
        self.assertIsNotNone(self.seen["narration"])
        self.assertIn("This comet is leaving the Sun.", self.seen["narration"])
        pass_dir = self.root / "qa" / "pass-01"
        self.assertTrue((pass_dir / "narration.txt").is_file())
        card = json.loads((pass_dir / "scorecard.json").read_text())
        self.assertEqual(card["story"]["premise_restated"], STORY["premise_restated"])
        self.assertTrue(any("satisfaction" in n for n in card["not_evidenced"]))

    def rewrite_line(self, label, line):
        path = self.root / "script.json"
        script = json.loads(path.read_text())
        for sec in script["sections"]:
            for shot in sec["shots"]:
                if shot["label"] == label:
                    shot["narration"] = line
        path.write_text(json.dumps(script))

    def test_a_reused_pass_keeps_its_narration_and_a_new_pass_reads_the_new_line(self):
        self.run_qa()
        first = self.root / "qa" / "pass-01" / "narration.txt"
        kept = first.read_text()
        self.rewrite_line("1.1", "A brand new opening line.")
        self.run_qa(reuse="pass-01")
        self.assertEqual(first.read_text(), kept)
        self.run_qa()
        second = (self.root / "qa" / "pass-02" / "narration.txt").read_text()
        self.assertIn("A brand new opening line.", second)
        self.assertEqual(self.seen["narration"], second)    # what the reviewer read

    def test_a_reused_pass_from_before_the_narration_says_it_was_written_later(self):
        self.run_qa()
        pass_dir = self.root / "qa" / "pass-01"
        (pass_dir / "narration.txt").unlink()
        asked = len(self.seen["prompts"])
        self.run_qa(reuse="pass-01")
        text = (pass_dir / "narration.txt").read_text()
        self.assertIn("written at a later review than the frames", text.splitlines()[0])
        # new evidence in the pass: the cached inspections are not served
        self.assertEqual(len(self.seen["prompts"]), asked + qa.QA_SAMPLES)

    def test_every_section_has_a_line_or_the_reviewer_is_asked_again(self):
        # structure 20: one line per screenplay section, a blank line fails
        blank_line = dict(STORY, sections=[{"section": 1, "viewer_has_at_end": " "}])
        self.cards[:] = [dict(self.cards[0], story=blank_line), self.cards[0]]
        self.run_qa()
        # the first sample is asked again; the others answered whole (AUTO-107)
        self.assertEqual(len(self.seen["prompts"]), qa.QA_SAMPLES + 1)
        self.assertIn("section 1", self.seen["prompts"][1].split("Answer again")[-1])
        card = json.loads((self.root / "qa" / "pass-01" / "scorecard.json").read_text())
        self.assertEqual(card["story"]["sections"], STORY["sections"])
        self.assertFalse(any("structure 20" in n for n in card["not_evidenced"]))

    def test_a_section_still_without_a_line_is_named_not_evidenced(self):
        missing = dict(STORY, sections=[{"section": 3, "viewer_has_at_end": "x"}])
        self.cards[:] = [dict(self.cards[0], story=missing)]
        self.run_qa()
        # each sample asked once more, no more (AUTO-107)
        self.assertEqual(len(self.seen["prompts"]), 2 * qa.QA_SAMPLES)
        card = json.loads((self.root / "qa" / "pass-01" / "scorecard.json").read_text())
        gaps = [n for n in card["not_evidenced"] if "structure 20" in n]
        self.assertEqual(len(gaps), 1, card["not_evidenced"])
        self.assertIn("section 1", gaps[0])
        self.assertIn("section 3", gaps[0])

    def test_a_films_own_retention_json_is_not_the_channels(self):
        # `yt retention` writes work/<slug>/retention.json for one published
        # film (AUTO-101); the satisfaction proxy waits on the channel's.
        (self.root / qa.RETENTION).write_text("{}")
        self.run_qa()
        card = json.loads((self.root / "qa" / "pass-01" / "scorecard.json").read_text())
        note = next(n for n in card["not_evidenced"] if "satisfaction" in n)
        self.assertIn("not a film's own", note)

    def test_satisfaction_is_kept_once_the_channel_has_retention(self):
        (self.work / qa.RETENTION).write_text("{}")
        self.run_qa()
        card = json.loads((self.root / "qa" / "pass-01" / "scorecard.json").read_text())
        self.assertFalse(any("satisfaction" in n for n in card["not_evidenced"]))

    # AUTO-153: a blocking continuity break from a reviewer of another family
    # keeps the film off READY; the same verdict from the author's family is
    # doctor's warn alone.

    def continuity_break(self, author):
        (self.root / "vfx.json").write_text(json.dumps({
            "authored": [{"n": 1, "section": "One"}, {"n": 2, "section": "Two"}],
            "continuity": {"one_film": False, "score": 3, "summary": "two films",
                           "monotony": [], "reviewer": {"model": "opus", "author": author,
                                                        "engine": "claude"},
                           "breaks": [{"sections": ["One", "Two"], "owner_section": "Two",
                                       "problem": "the hero is twice the size",
                                       "fix": "scale it", "severity": "blocking"}]}}))

    def scorecard(self, n: int) -> dict:
        return json.loads((self.root / "qa" / f"pass-{n:02d}" / "scorecard.json").read_text())

    def test_qa_run_with_a_blocking_continuity_break_is_not_ready(self):
        line = "vfx continuity: section 2 (Two): the hero is twice the size"
        self.run_qa()                   # pass-01: no vfx.json, the baseline
        base = self.scorecard(1)
        self.continuity_break("sonnet")
        self.run_qa()
        card = self.scorecard(2)
        self.assertEqual(card["verdict"], qa.NOT_READY)
        self.assertIn(line, card["blocking"])
        self.assertEqual(len(card["blocking"]), len(base["blocking"]) + 1)
        self.assertIn(f"{len(card['blocking'])} blocking item(s)", card["bar"])
        # delivery stays open on that scorecard: doctor reads qa.json's verdict
        project = workspace.Project(self.root)
        said = [p for p, _ in doctor.delivery_evidence(project, None)
                if p.startswith("QA pass-02 says")]
        self.assertEqual(len(said), 1, said)
        self.assertIn(qa.NOT_READY, said[0])
        # the author's own family: the break changes nothing QA files
        self.continuity_break("opus")
        self.run_qa()
        same = self.scorecard(3)
        self.assertNotIn(line, same["blocking"])
        self.assertEqual((same["verdict"], len(same["blocking"])),
                         (base["verdict"], len(base["blocking"])))

    # AUTO-133: a fact-check blocker standing in script.json keeps the film
    # off READY, and the scorecard records the rows improve routes.

    def test_qa_run_with_a_standing_fact_check_blocker_is_not_ready(self):
        line = "fact-check: blocker at 1.2: the wind speed is not in the research"
        self.run_qa()
        base = self.scorecard(1)
        self.assertEqual(base["accuracy"]["fact_check"], [])
        path = self.root / "script.json"
        script = json.loads(path.read_text())
        for n, shot in enumerate(script["sections"][0]["shots"], 1):
            shot["n"] = n
        script["check"] = {"flags": [
            {"n": 2, "severity": "blocker", "problem": "the wind speed is not in the research",
             "fix": "drop the number"},
            {"n": 3, "severity": "important", "problem": "overstated", "fix": "hedge"}]}
        path.write_text(json.dumps(script))
        self.run_qa()
        card = self.scorecard(2)
        self.assertEqual(card["verdict"], qa.NOT_READY)
        self.assertIn(line, card["blocking"])
        self.assertEqual(len(card["blocking"]), len(base["blocking"]) + 1)
        self.assertEqual(card["accuracy"]["fact_check"], [
            {"n": 2, "label": "1.2", "problem": "the wind speed is not in the research",
             "fix": "drop the number", "kept": False}])
        # never briefed under "Blocking, already proven": the scorecard prompt
        # stays what it was, so its cached pass is not asked again, and the
        # reviewer has no cue to file it a second time as a qa finding
        self.assertNotIn("the wind speed is not in the research", self.seen["prompt"])
        self.assertNotIn("fact-check: blocker", self.seen["prompt"])

    # AUTO-150: with a dossier, the text vfx authored is read against it by a
    # reviewer of another family; a blocking mismatch keeps the film off READY.

    ACCURATE_WORLD = """<scene version="1.3">
  <assets><chart id="c-share" labels="share"><series name="rose" values="40%"/></chart>
    <text id="t-pct" text="40%"/></assets>
  <symbols><symbol id="world-a" width="1920" height="1080">
    <layer id="l-pct" asset="t-pct"/></symbol></symbols>
</scene>
"""

    def on_screen(self, *, dossier=True, vfx_model=None, script_model="sonnet"):
        """Shot 1.1 includes a vfx world that draws "40%"; the dossier says 14%."""
        doc = self.root / "vfx" / "v01" / "a.scene.xml"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(self.ACCURATE_WORLD)
        (self.root / "scene.xml").write_text(
            '<scene version="1.3"><composition><include id="shot-1.1" start="0" end="3" '
            'src="vfx/v01/a.scene.xml" symbol="world-a"/></composition></scene>')
        built = json.loads((self.root / "build.json").read_text())
        built["shots"][0]["include"] = "shot-1.1"
        (self.root / "build.json").write_text(json.dumps(built))
        path = self.root / "script.json"
        data = json.loads(path.read_text())
        data["usage"] = {"model": script_model}
        path.write_text(json.dumps(data))
        if vfx_model:
            (self.root / "vfx.json").write_text(json.dumps({"usage": {"model": vfx_model}}))
        if dossier:
            (self.root / "dossier.json").write_text(json.dumps({"claims": [
                {"claim": "the share rose 14%", "confidence": "high", "source": "S1"}]}))

    def accuracy_answers(self, answer):
        """The accuracy decision answers `answer`; every prompt it is asked is kept."""
        asked = self.asked = []
        base = harness.ENGINE

        class Checker(base):
            def ask_json(self_, prompt, schema, system=None):
                if system and qa.ACCURACY_ROLE in system:
                    asked.append(prompt)
                    return answer
                return super().ask_json(prompt, schema, system)
        harness.ENGINE = Checker

    MISMATCH = {"findings": [{"shot": "1.1", "quote": "40%", "claim_id": "c1",
                              "problem": "number-mismatch", "severity": "blocking",
                              "fix": "draw 14%", "owner": "vfx"}],
                "checked": 1, "verdict": "one figure is wrong"}

    def test_accuracy_blocking_number_mismatch(self):
        self.on_screen(dossier=False)
        self.run_qa()
        base = self.scorecard(1)
        self.on_screen()
        self.accuracy_answers(self.MISMATCH)
        self.run_qa()
        card = self.scorecard(2)
        self.assertEqual(len(self.asked), 1)
        self.assertIn("40%", self.asked[0])
        self.assertEqual(card["verdict"], qa.NOT_READY)
        line = 'accuracy: number-mismatch at 0s (shot 1.1): "40%" -> vfx'
        self.assertIn(line, card["blocking"])
        self.assertEqual(len(card["blocking"]), len(base["blocking"]) + 1)
        final = card["accuracy"]["final"]
        self.assertEqual(final["status"], "done")
        self.assertEqual(final["findings"][0]["quote"], "40%")
        self.assertEqual(final["findings"][0]["owner"], "vfx")
        self.assertEqual(final["findings"][0]["start"], 0.0)
        self.assertEqual(card["accuracy"]["fact_check"], [])
        self.assertIn("accuracy_engine", card)
        self.assertEqual(card["accuracy_engine"]["model"], qa.ACCURACY_MODEL)
        record = json.loads((self.root / "qa.json").read_text())
        self.assertEqual(record["verdict"], qa.NOT_READY)
        self.assertEqual(record["accuracy"]["blocking"], 1)
        self.assertEqual(record["accuracy"]["status"], "done")
        self.assertIs(record["accuracy"]["bar"], False)
        findings = (self.root / "qa" / "pass-02" / "findings.md").read_text()
        self.assertIn("## On-screen accuracy", findings)
        self.assertIn(line, findings)
        # the scoring prompt is what it was: the accuracy pass is not briefed
        self.assertNotIn("number-mismatch", self.seen["prompt"])

    def test_no_dossier_unchanged(self):
        self.on_screen(dossier=False)
        self.accuracy_answers(self.MISMATCH)
        self.run_qa()
        card = self.scorecard(1)
        self.assertEqual(self.asked, [])
        self.assertEqual(card["accuracy"]["final"], {
            "status": qa.NOT_EVIDENCED, "why": "the film has no dossier",
            "command": "yt research", "bar": False})
        self.assertNotIn("accuracy_engine", card)
        self.assertFalse(any("accuracy" in str(b) for b in card["blocking"]))
        self.assertFalse(any("accuracy" in r for r in card["bar"]), card["bar"])
        # the same run with the accuracy code bypassed: none of it is reached,
        # and the verdict, the bar and the blocking list are the same
        never = mock.Mock(side_effect=AssertionError("reached without a dossier"))
        with mock.patch.object(qa, "scene_text", never), \
                mock.patch.object(qa, "accuracy_refusal", never), \
                mock.patch.object(qa, "accuracy", never):
            self.run_qa(fresh=True)
        again = self.scorecard(2)
        self.assertEqual((again["verdict"], again["bar"], again["blocking"]),
                         (card["verdict"], card["bar"], card["blocking"]))
        record = json.loads((self.root / "qa.json").read_text())
        self.assertEqual(record["accuracy"], {"status": qa.NOT_EVIDENCED, "bar": False,
                                              "checked": 0, "blocking": 0,
                                              "command": "yt research",
                                              "why": "the film has no dossier"})

    def test_defaulted_clash_is_a_bar_reason(self):
        self.on_screen(vfx_model="opus")
        self.accuracy_answers(self.MISMATCH)
        self.assertIn(self.run_qa(), (0, 1))
        card = self.scorecard(1)
        self.assertEqual(self.asked, [])
        self.assertEqual(card["verdict"], qa.NOT_READY)
        final = card["accuracy"]["final"]
        self.assertEqual((final["status"], final["bar"]), (qa.NOT_EVIDENCED, True))
        self.assertEqual(final["command"], "yt qa --accuracy-model <another family>")
        reason = next(r for r in card["bar"] if "on-screen accuracy is not evidenced" in r)
        self.assertIn("--accuracy-model <another family>", reason)
        record = json.loads((self.root / "qa.json").read_text())
        self.assertIs(record["accuracy"]["bar"], True)

    def test_explicit_clash_exits_2(self):
        self.on_screen(vfx_model="opus")
        rendered, render = [], qa.RENDERER
        qa.RENDERER = lambda args, scene: rendered.append(scene) or render(args, scene)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = qa.run(self.args(accuracy_model="opus"))
        self.assertEqual(code, 2)
        self.assertEqual(rendered, [])
        self.assertFalse((self.root / "qa").exists())
        self.assertIn("opus", err.getvalue())
        # with no dossier there is nothing to check, so nothing is refused
        (self.root / "dossier.json").unlink()
        self.assertNotEqual(self.run_qa(accuracy_model="opus"), 2)
        self.assertEqual(len(rendered), 1)

    def test_nothing_on_screen_is_done_whatever_the_clash(self):
        # A dossier film whose documents draw no readable text has nothing
        # to check: no bar, no exit 2, and no accuracy decision asked.
        self.on_screen(vfx_model="opus")
        (self.root / "scene.xml").write_text(
            '<scene version="1.3"><composition></composition></scene>')
        self.accuracy_answers(self.MISMATCH)
        self.run_qa()
        card = self.scorecard(1)
        self.assertEqual(self.asked, [])
        self.assertEqual(card["accuracy"]["final"], {
            "status": "done", "checked": 0, "findings": [], "not_checked": []})
        self.assertFalse(any("on-screen accuracy" in r for r in card["bar"]), card["bar"])
        self.assertNotEqual(self.run_qa(accuracy_model="opus"), 2)

    def test_an_accuracy_model_the_cli_cannot_run_exits_2(self):
        self.on_screen()
        rendered, render = [], qa.RENDERER
        qa.RENDERER = lambda args, scene: rendered.append(scene) or render(args, scene)
        for value in ("google/gemini-3.6-flash", "sdk:google/gemini-3.6-flash", "pool"):
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = qa.run(self.args(accuracy_model=value))
            self.assertEqual(code, 2, value)
            self.assertIn("claude or codex model", err.getvalue(), value)
        self.assertEqual(rendered, [])
        self.assertFalse((self.root / "qa").exists())

    # AUTO-156: qa.json says whether the judge is the one the QA fixtures
    # passed, and a line under the verdict says so; nothing reads it to gate.

    def printed(self, **kw) -> list[str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            qa.run(self.args(**kw))
        return out.getvalue().splitlines()

    def judge_line(self, lines: list[str]) -> str:
        i = next(n for n, line in enumerate(lines) if " — QA pass-" in line)
        return lines[i + 1]

    def calibrated(self, **report):
        path = self.work / qa.CALIBRATION_QA / "report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"passed": True, "model": "sonnet", "cases": [],
                                    "fixture_set": qa.fixture_set_digest(qa.QA_FIXTURES)}
                                   | report))

    def test_qa_json_records_judge_calibration_false_without_report(self):
        lines = self.printed()
        record = json.loads((self.root / "qa.json").read_text())
        calibration = record["judge_calibration"]
        self.assertEqual(set(calibration), {"fixtures", "labelled", "model", "digest", "why"})
        self.assertIs(calibration["fixtures"], False)
        self.assertEqual(calibration["labelled"], 0)
        self.assertEqual(calibration["model"], "sonnet")
        self.assertEqual(calibration["digest"], qa.judge_digest("sonnet", record["house"]))
        self.assertIn("calibration-qa", calibration["why"])
        self.assertEqual(self.judge_line(lines),
                         f"judge: not calibrated against the QA fixtures ({calibration['why']}); "
                         "0 owner-labelled films")

    def test_judge_calibration_false_on_model_mismatch(self):
        self.run_qa()
        house_digest = json.loads((self.root / "qa.json").read_text())["house"]
        self.calibrated(model="opus", digest=qa.judge_digest("opus", house_digest))
        lines = self.printed(model="sonnet")
        calibration = json.loads((self.root / "qa.json").read_text())["judge_calibration"]
        self.assertIs(calibration["fixtures"], False)
        self.assertIn("opus", calibration["why"])
        self.assertIn("opus", self.judge_line(lines))
        # a report that did not pass, or another judge's digest, is not calibrated either
        self.calibrated(passed=False, digest=qa.judge_digest("sonnet", house_digest))
        self.run_qa()
        self.assertIs(json.loads((self.root / "qa.json").read_text())
                      ["judge_calibration"]["fixtures"], False)
        self.calibrated(digest=qa.judge_digest("sonnet", {"block_sha256": "other rules"}))
        self.run_qa()
        self.assertIs(json.loads((self.root / "qa.json").read_text())
                      ["judge_calibration"]["fixtures"], False)

    def test_judge_calibration_true_when_report_matches(self):
        self.run_qa()
        house_digest = json.loads((self.root / "qa.json").read_text())["house"]
        self.calibrated(digest=qa.judge_digest("sonnet", house_digest))
        lines = self.printed()
        calibration = json.loads((self.root / "qa.json").read_text())["judge_calibration"]
        self.assertIs(calibration["fixtures"], True, calibration["why"])
        self.assertEqual(calibration["labelled"], 0)
        self.assertEqual(self.judge_line(lines),
                         "judge: fixture-calibrated (sonnet); 0 owner-labelled films")

    def test_verdict_strings_unchanged_by_calibration_label(self):
        before = self.printed()
        record = json.loads((self.root / "qa.json").read_text())
        findings = (self.root / "qa" / "pass-01" / "findings.md").read_text()
        self.calibrated(digest=qa.judge_digest("sonnet", record["house"]))
        after = self.printed()
        again = json.loads((self.root / "qa.json").read_text())
        self.assertEqual(again["verdict"], record["verdict"])
        self.assertIn(again["verdict"], (qa.READY, qa.NOT_READY))
        self.assertEqual(again["reasons"], record["reasons"])
        verdict_line = lambda lines: next(l for l in lines if " — QA pass-" in l)  # noqa: E731
        self.assertEqual(verdict_line(after).split(": ", 1)[1],
                         verdict_line(before).split(": ", 1)[1])
        self.assertTrue(self.judge_line(before).startswith("judge: not calibrated"))
        self.assertTrue(self.judge_line(after).startswith("judge: fixture-calibrated"))
        for text in (findings, (self.root / "qa" / "pass-01" / "findings.md").read_text()):
            self.assertNotIn("judge:", text)
            self.assertNotIn("calibrat", text)


# --------------------------------------------------------------------------- #
# AUTO-145: the words on screen, counted from the rendered document
# --------------------------------------------------------------------------- #

#: One section world, included at 0-6 s: a title layer for its own 0-2 s,
#: a formula throughout, a label that fades in to 1 at 2 s and out to 0 at
#: 4 s, and a label the engine says the camera never sees.
SCREEN_WORLD = """<scene version="1.3">
  <assets>
    <text id="t-title" text="Rust eats bolts" width="900" height="120"/>
    <text id="t-fade" text="half the load on it" width="900" height="120"/>
    <text id="t-gone" text="never seen by anyone here" width="900" height="120"/>
    <formula id="f-law" tex="F = m a" width="400" height="120"/>
  </assets>
  <symbols>
    <symbol id="world-a" width="1920" height="1080">
      <layer id="l-title" asset="t-title" start="0" end="2"/>
      <layer id="l-law" asset="f-law"/>
      <group id="g-fade">
        <animate property="opacity"><key time="1.5" value="0"/><key time="2" value="1"/><key time="4" value="0"/></animate>
        <layer id="l-fade" asset="t-fade"/>
      </group>
      <layer id="l-gone" asset="t-gone"/>
    </symbol>
  </symbols>
</scene>
"""

SCREEN_SCENE = """<scene version="1.3"><project width="1920" height="1080" fps="30" duration="6"/>
  <composition>
    <include id="shot-1.1" start="0" end="6" src="vfx/v01/a.scene.xml" symbol="world-a"/>
  </composition>
</scene>
"""


def _node(ident, words, *, kind="text", window=(0.0, 60.0), text=None):
    """A text node as the document walk gives it, with `words` words."""
    return {"id": f"shot-1.2/{ident}", "name": ident, "kind": kind, "asset": "",
            "text": text if text is not None else " ".join(f"w{i}" for i in range(words)),
            "window": list(window), "fades": []}


def _texted(label, nodes, t=None):
    """qa.sample(_build()) with `nodes` read on screen at `label`'s still."""
    rows = qa.sample(_build())
    for row in rows:
        if row["label"] == label:
            row["text"] = qa.text_record(qa.screen_text(nodes, t if t is not None
                                                        else row["t"]))
    return rows


def _dense(rows, **kw):
    return [f for f in qa.mechanical_findings(rows, **kw) if f["kind"] == "text-dense"]


class DeliveryGateStage(unittest.TestCase):
    """AUTO-148: QA files READY only over a deliverable gate on the same
    bytes (doctor.gate_problem), and never over a screenplay its story
    reviewer rejected or could not judge (script_blockers)."""

    setUp = StoryStage.setUp
    args = StoryStage.args
    run_qa = StoryStage.run_qa

    def qa_json(self, home=None):
        return json.loads(((home or self.root) / "qa.json").read_text())

    def card(self, home=None):
        return json.loads(((home or self.root) / "qa" / "pass-01" / "scorecard.json")
                          .read_text())

    def sealed(self, home, scene):
        (home / "gate.json").write_text(json.dumps({
            "mode": "technical", "exit": 0,
            "triage": {"verdict": "deliverable", "blocking": []},
            "scene_sha256": doctor.scene_digest(scene)}))

    def test_no_gate_is_not_ready_and_says_why(self):
        self.run_qa()
        why = "no delivery gate has run on this render"
        record = self.qa_json()
        self.assertEqual(record["verdict"], qa.NOT_READY)
        self.assertEqual(record["gate_evidence"], why)
        self.assertEqual(self.card()["gate_evidence"], why)
        self.assertIn(f"delivery gate: {why}", record["reasons"])
        findings = (self.root / "qa" / "pass-01" / "findings.md").read_text()
        self.assertIn(why, findings)

    def test_a_gate_on_these_bytes_is_not_a_reason(self):
        self.sealed(self.root, self.root / "scene.xml")
        self.run_qa()
        self.assertEqual(self.qa_json()["gate_evidence"], "")
        self.assertFalse(any(r.startswith("delivery gate") for r in self.qa_json()["reasons"]))

    def test_a_frames_only_gate_is_named_in_doctors_words(self):
        (self.root / "gate.json").write_text(json.dumps({"mode": "frames", "exit": 0}))
        self.run_qa()
        self.assertEqual(self.qa_json()["gate_evidence"],
                         "the last gate checked decoded frames only; delivery needs "
                         "the technical gate")
        self.assertEqual(self.qa_json()["verdict"], qa.NOT_READY)

    def test_a_rejected_screenplay_is_a_blocking_row(self):
        path = self.root / "script.json"
        script = json.loads(path.read_text())
        script["review"] = {"verdict": "reject",
                            "unresolved": [{"problem": "the train never leaves"}]}
        path.write_text(json.dumps(script))
        self.sealed(self.root, self.root / "scene.xml")
        self.run_qa()
        blocking = self.card()["blocking"]
        self.assertIn("script: story review rejected the screenplay (the train never "
                      "leaves)", blocking)
        self.assertEqual(self.qa_json()["verdict"], qa.NOT_READY)
        # not briefed: the reviewer's prompt is what it was
        self.assertNotIn("story review rejected", self.seen["prompt"])

    def test_standalone_qa_needs_a_gate_beside_the_scene(self):
        loose = self.tmp / "loose"
        loose.mkdir()
        scene = loose / "cut.xml"
        scene.write_text((self.root / "scene.xml").read_text())
        argv = dict(project=None, scene=str(scene), in_build=str(self.root / "build.json"),
                    in_script=str(self.root / "script.json"))
        self.run_qa(**argv)
        self.assertEqual(self.qa_json(loose)["verdict"], qa.NOT_READY)
        self.assertEqual(self.qa_json(loose)["gate_evidence"],
                         "no delivery gate has run on this render")
        # no video.mp4 beside it: the scene's digest is what the gate must hold
        self.sealed(loose, scene)
        self.run_qa(**argv)
        self.assertEqual(self.qa_json(loose)["gate_evidence"], "")


class ScreenText(unittest.TestCase):
    def test_the_visible_words_follow_the_include_the_spans_and_the_keys(self):
        from ytstudio import geometry
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "vfx" / "v01").mkdir(parents=True)
            (root / "vfx" / "v01" / "a.scene.xml").write_text(SCREEN_WORLD, encoding="utf-8")
            nodes = geometry._composed(SCREEN_SCENE, "", root, kinds=geometry.TEXT_KINDS)
        off = ["shot-1.1/l-gone"]
        self.assertEqual(qa.FORMULA_WORDS, 4)

        def words(t):
            return qa.text_record(qa.screen_text(nodes, t, off))
        one, three, five = words(1.0), words(3.0), words(5.0)
        self.assertEqual(sorted(one["ids"]), ["shot-1.1/l-law", "shot-1.1/l-title"])
        self.assertEqual((one["words"], one["nodes"], one["formulas"]), (3 + 4, 2, 1))
        self.assertEqual(sorted(three["ids"]), ["shot-1.1/l-fade", "shot-1.1/l-law"])
        self.assertEqual(three["words"], 5 + 4)
        self.assertEqual(five["ids"], ["shot-1.1/l-law"])
        self.assertEqual(five["words"], 4)
        # without the engine's word the unseen label counts
        self.assertIn("shot-1.1/l-gone", qa.text_record(qa.screen_text(nodes, 1.0))["ids"])
        # outside the include nothing is on screen
        self.assertEqual(qa.text_record(qa.screen_text(nodes, 7.0))["nodes"], 0)

    def test_a_fade_out_and_a_second_fade_in_are_both_read(self):
        node = dict(_node("l-twice", 2), fades=[[[0.0, 0.0], [1.0, 1.0], [2.0, 0.0],
                                                [3.0, 0.0], [4.0, 1.0], [5.0, 0.0]]])
        seen = [t for t in (0.5, 1.5, 2.5, 4.5, 5.5) if qa.screen_text([node], t)]
        self.assertEqual(seen, [1.5, 4.5])
        [shown] = qa.screen_text([node], 4.5)
        self.assertEqual(shown["shown"], [4.0, 5.0])


class TextDense(unittest.TestCase):
    def test_one_node_over_the_wall_of_text_bound_is_text_dense_owned_by_vfx(self):
        from ytstudio import script
        self.assertEqual(script.BOUNDS["wall_of_text"]["value"], 6)
        [f] = _dense(_texted("1.2", [_node("l-long", 9)]))
        self.assertEqual((f["shot"], f["owner"], f["severity"]), ("1.2", "vfx", "minor"))
        self.assertIn("`shot-1.2/l-long`", f["problem"])
        self.assertIn("9 words", f["problem"])
        for key in ("why", "root_cause", "fix", "timestamp", "frame"):
            self.assertTrue(f[key], key)

    def test_twelve_or_more_words_at_once_is_text_dense_and_ten_is_not(self):
        self.assertEqual(qa.SCREEN_WORDS, 12)
        [f] = _dense(_texted("1.2", [_node("a", 4), _node("b", 4), _node("c", 5)]))
        for ident in ("a", "b", "c"):
            self.assertIn(f"`shot-1.2/{ident}`", f["problem"])
        self.assertIn("13 words", f["problem"])
        self.assertEqual(_dense(_texted("1.2", [_node("a", 3), _node("b", 3),
                                                _node("c", 4)])), [])
        # a formula counts as FORMULA_WORDS
        self.assertEqual(len(_dense(_texted("1.2", [_node("a", 4), _node("b", 4),
                                                    _node("f", 0, kind="formula",
                                                          text="E = m c^2")]))), 1)

    def test_a_quote_with_time_to_be_read_passes_and_a_flashed_one_does_not(self):
        quote = " ".join(["word"] * 14)
        long = _node("quote-1", 14, text=quote, window=(5.0, 15.0))
        self.assertEqual(_dense(_texted("1.2", [long])), [])
        flashed = _node("quote-1", 14, text=quote, window=(6.5, 7.5))   # 1.2's still: 7 s
        [f] = _dense(_texted("1.2", [flashed]))
        self.assertIn("`shot-1.2/quote-1`", f["problem"])
        self.assertIn("1.0 s", f["problem"])
        for ident in ("headline-2", "credit-3"):
            self.assertEqual(_dense(_texted("1.2", [_node(ident, 14,
                                                          window=(5.0, 15.0))])), [])

    def test_a_super_the_screenplay_asked_for_says_so(self):
        text = "the paint hid the rust on every joint"
        rows = _texted("1.2", [_node("l-super", 8, text=text)])
        [f] = _dense(rows, supers={"1.2": [text.upper()]})
        self.assertIn("the screenplay asked for this super; rule:wall-of-text already "
                      "flagged it", f["problem"])
        [plain] = _dense(rows)
        self.assertNotIn("screenplay asked", plain["problem"])

    def test_the_placeholder_card_is_not_counted(self):
        card = dict(_node("x", 30), id="shot-1.2", name="shot-1.2", asset="card-1.2")
        self.assertEqual(qa.screen_text([card], 5.0), [])


class Advisory(unittest.TestCase):
    """AUTO-145: no-build and text-dense are always minor and cap nothing
    until their thresholds are calibrated."""

    def findings(self, labels):
        rows = qa.sample(_build())
        for row in rows:
            if row["label"] in labels:
                row["text"] = qa.text_record(qa.screen_text([_node("l-long", 9)], row["t"]))
        return qa.mechanical_findings(rows)

    def test_they_stay_minor_on_an_edge_shot_and_on_three_shots(self):
        self.assertEqual(qa.ADVISORY_KINDS, ("no-build", "text-dense"))
        on_hook = self.findings({"1.1"})
        self.assertEqual([(f["kind"], f["severity"]) for f in on_hook],
                         [("text-dense", "minor")])
        many = self.findings({"1.2", "2.1", "2.2"})
        self.assertEqual([f["severity"] for f in many], ["minor"] * 3)
        scores = {k: 9.0 for k in qa.CATEGORIES}
        capped, overall, which = qa.cap_scores(scores, 9.0, on_hook + many + [
            {"kind": "no-build", "severity": "minor", "shot": "1.1"}])
        self.assertEqual((capped, overall, which), (scores, 9.0, []))
        self.assertNotIn("no-build", qa.CAPPED)

    def test_reown_moves_no_build_to_build_and_leaves_text_dense_with_vfx(self):
        found = [{"kind": "no-build", "owner": "vfx", "shot": "1.2", "severity": "minor",
                  "problem": "effect `fx` is on screen whole"},
                 {"kind": "text-dense", "owner": "vfx", "shot": "1.2", "severity": "minor",
                  "problem": "`shot-1.2/l-long` carries 9 words"}]
        mismatch = {"kind": "proof-mismatch", "owner": "build", "severity": "blocking",
                    "shot": "1.2", "world": "fx-open", "shots": ["1.2"], "problem": "d"}
        self.assertEqual(qa.reown_by_proof(found + [mismatch], [mismatch]), 1)
        self.assertEqual([f["owner"] for f in found], ["build", "vfx"])


class EditorialIdentity(unittest.TestCase):
    """AUTO-146: QA scores the film's identity, under the same 8.5 bar, and a
    pass scored before the category existed still compares on the ones both
    passes scored."""

    def test_the_role_says_what_it_scores(self):
        self.assertIn("editorial_identity", qa.ROLE)
        self.assertIn("swap test", qa.ROLE)
        self.assertIn("editorial_identity", qa.ROLE_LISTENED)
        para = next(p for p in qa.ROLE.split("\n\n") if "editorial_identity" in p)
        self.assertIn("never a look of your own", para)

    def test_the_styles_duty_owns_the_identity(self):
        duty = qa.DUTIES["styles"]
        for word in ("identity", "atmosphere", "personality", "motif", "signature"):
            self.assertIn(word, duty)

    def test_the_scorecard_asks_for_it(self):
        self.assertIn("editorial_identity", qa.SCORECARD_SCHEMA["properties"]["scores"]
                      ["required"])

    def test_eleven_distinct_rotations(self):
        orders = [qa.category_order(seed) for seed in range(len(qa.CATEGORIES))]
        self.assertEqual(len({o[0] for o in orders}), 11)
        for order in orders:
            self.assertEqual(sorted(order), sorted(qa.CATEGORIES))
        doc = qa.category_order.__doc__
        self.assertIn("eleven", doc)
        self.assertIn("1 to 11", doc)

    def test_a_generic_polished_film_is_not_ready(self):
        scores = {k: 9.5 for k in qa.CATEGORIES} | {"editorial_identity": 8.0}
        verdict, reasons = qa.verdict_of(scores, 9.4, blocking=[])
        self.assertEqual(verdict, qa.NOT_READY)
        self.assertTrue(any(r.startswith("editorial_identity 8.0") for r in reasons), reasons)

    def test_a_ten_category_pass_compares_on_the_shared_ones(self):
        old = {k: 8.0 for k in qa.CATEGORIES if k != "editorial_identity"}
        prev = {"pass": "pass-01", "scores": old, "overall": 8.0}
        cur = {"scores": {k: 8.5 for k in qa.CATEGORIES} | {"editorial_identity": 2.0},
               "overall": 7.95}
        diff = qa.compare(prev, cur)
        self.assertIsNone(diff["deltas"]["editorial_identity"])
        self.assertEqual(diff["regressions"], [])
        self.assertEqual(diff["unscored"], ["editorial_identity"])
        self.assertAlmostEqual(diff["overall_delta"], 0.5)
        # and the other way round: no +9 jump from a category one side lacks
        back = qa.compare(dict(cur, **{"pass": "pass-02"}), {"scores": old, "overall": 8.0})
        self.assertIsNone(back["deltas"]["editorial_identity"])
        self.assertAlmostEqual(back["overall_delta"], -0.5)

    def test_the_evidence_carries_the_identity(self):
        ident = {"atmosphere": "a", "personality": "p", "motif": "m", "signature": "s",
                 "belongs_because": "b", "review": {"verdict": "pass"}}
        styles = {"styles": [{"name": "Diagram"}], "identity": ident}
        got = qa.stage_measurements(_build(), None, sound={}, gate=None, geometry={},
                                    styles=styles, reach=None)
        # the five fields only: the styles reviewer's verdict would anchor
        # the judge's score instead of the frames
        self.assertEqual(got["identity"], {k: v for k, v in ident.items() if k != "review"})
        self.assertNotIn("review", got["identity"])
        self.assertEqual(got["treatment"], {"name": "Diagram"})
        plain = qa.stage_measurements(_build(), None, sound={}, gate=None, geometry={},
                                      styles={"styles": [{"name": "Diagram"}]}, reach=None)
        self.assertNotIn("identity", plain)

    def test_context_carries_dimensions(self):
        # AUTO-154: where a section's world is flatter than its shots declare,
        # as context beside the frames; the categories and bars do not move.
        from tests.test_reach import FLAT, film
        styles, script, doc = film([("One", [("1.1", "2d motion graphics")], FLAT),
                                    ("Two", [("2.1", "3d animation")], FLAT)],
                                   styles_first="3d animation")
        gap = qa.dimension_context(styles, script, doc)
        self.assertEqual([r["n"] for r in gap["sections"]], [2])
        self.assertEqual(gap["film"], [{"style": "3d animation", "dimension": "3d"}])
        got = qa.stage_measurements(_build(), None, sound={}, gate=None, geometry={},
                                    styles=styles, reach=doc["reach"], dimensions=gap)
        self.assertEqual(got["dimensions"], gap)
        plain = qa.stage_measurements(_build(), None, sound={}, gate=None, geometry={},
                                      styles=styles, reach=doc["reach"])
        self.assertNotIn("dimensions", plain)
        # no vfx.json: nothing is added, so a film without one asks as before
        self.assertIsNone(qa.dimension_context(styles, script, None))
        self.assertEqual(qa.CATEGORIES, (
            "visual_quality", "animation_vfx", "cinematography", "editing_pacing",
            "graphics_overlays", "voiceover", "sound_design", "music_score",
            "narrative_clarity", "overall_production", "editorial_identity"))

    def test_the_prompt_scores_eleven(self):
        prompt = qa.review_prompt(
            title="t", pass_name="pass-01", sheet_name="s.png", has_narration=False,
            missing=[], total_shots=1, dropped_lines=[], narrated_total=0, story=None,
            evidence=[], mechanical=[], listening=[], unheard={}, silent_film=False,
            measurements={}, blockers=[])
        self.assertIn("score the eleven categories", prompt)
        self.assertNotIn("ten categories", prompt)

    def test_a_still_pair_can_bear_on_it(self):
        self.assertIn("editorial_identity", qa.SIDE_CATEGORIES)
        enum = (qa.SIDE_SCHEMA["properties"]["moments"]["items"]["properties"]["bears_on"]
                ["items"]["enum"])
        self.assertIn("editorial_identity", enum)


class DimensionsInTheRun(unittest.TestCase):
    """AUTO-154 through qa.run: the vfx.json it reads once gives the reviewer
    the dimension gap, and the verdict's bars are what they were."""

    def setUp(self):
        from tests import test_qa_samples as samples
        samples.Samples.setUp(self)
        self.args = lambda **kw: samples.Samples.args(self, **kw)
        self.run_qa = lambda **kw: samples.Samples.run_qa(self, **kw)

    def test_the_review_prompt_carries_the_gap(self):
        from tests.test_reach import FLAT, film
        styles, script, doc = film([("One", [("1.1", "3d animation")], FLAT)],
                                   styles_first="3d animation")
        script["sections"][0]["shots"][0]["narration"] = "A comet."
        (self.root / "script.json").write_text(json.dumps(script))
        (self.root / "styles.json").write_text(json.dumps(styles))
        (self.root / "vfx.json").write_text(json.dumps(doc))
        self.run_qa()
        asked = "\n".join(self.seen["prompts"])
        self.assertIn('"dimensions"', asked)
        self.assertIn("3d animation", asked)
        self.assertIn('"declared": "3d"', asked)
        gapped = json.loads((self.root / "qa" / "pass-01" / "scorecard.json").read_text())
        # the same film with no gap: the verdict, the scores and the blocking
        # list are the same, since a gap is context and never a bar
        styles, script, doc = film([("One", [("1.1", "2d motion graphics")], FLAT)])
        script["sections"][0]["shots"][0]["narration"] = "A comet."
        (self.root / "script.json").write_text(json.dumps(script))
        (self.root / "styles.json").write_text(json.dumps(styles))
        (self.root / "vfx.json").write_text(json.dumps(doc))
        self.run_qa()
        clean = json.loads((self.root / "qa" / "pass-02" / "scorecard.json").read_text())
        for key in ("verdict", "scores", "blocking"):
            self.assertEqual(gapped.get(key), clean.get(key), key)


if __name__ == "__main__":
    unittest.main()
