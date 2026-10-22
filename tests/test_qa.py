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
from pathlib import Path

from ytstudio import harness, narrative, qa
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
        # nothing caps the rehooks a screenplay marks; the default limit
        # still keeps the hook, the ending and every section (sample's promise)
        shots = []
        for n in range(20):
            section = f"S{n // 4 + 1}"
            function = ("hook" if n == 0 else "context" if n % 4 == 0 else "rehook")
            shots.append({"label": f"{n // 4 + 1}.{n % 4 + 1}", "at": n * 5.0,
                          "seconds": 5, "asset": "effect", "section": section,
                          "function": function})
        build = {"scene": {"duration": 100}, "shots": shots}
        samples = qa.sample(build)
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

    def test_mechanical_blockers_come_from_the_manifests(self):
        blockers = qa.mechanical_blockers(_build(), gate={"exit": 1, "triage": {
            "blocking": [{"what": "loudness -16 LUFS"}]}})
        joined = " ".join(blockers)
        self.assertIn("3.1", joined)
        self.assertIn("loudness", joined)
        self.assertEqual(qa.mechanical_blockers({"placeholders": [], "findings": []},
                                                gate=None), [])


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
                   "next_film": "the heliopause"},
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

    def test_the_categories_stay_ten(self):
        self.assertEqual(len(qa.CATEGORIES), 10)
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
        seen = self.seen = {}
        card = dict(motion.SCORECARD, story=STORY)

        class Reader(motion.FakeEngine):
            def stream(self_, prompt, system=None):
                from claude_engine import Event
                cwd = Path(self_.config.cwd)
                seen["prompt"], seen["system"] = prompt, system
                seen["narration"] = ((cwd / "narration.txt").read_text()
                                     if (cwd / "narration.txt").is_file() else None)
                yield Event(type="assistant", text="```json\n" + json.dumps(card) + "\n```")
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

    def run_qa(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return qa.run(self.args())

    def test_the_prompt_carries_the_story_and_the_narration_is_evidence(self):
        self.run_qa()
        prompt = self.seen["prompt"]
        self.assertIn("The screenplay's story", prompt)
        self.assertIn("A comet's tail must point somewhere", prompt)
        self.assertIn("It is showing you the wind.", prompt)
        self.assertIn("narration.txt", prompt)
        self.assertIn(qa.ROLE, self.seen["system"])
        # written before the inspection, so the digest of its evidence covers it
        self.assertIsNotNone(self.seen["narration"])
        self.assertIn("This comet is leaving the Sun.", self.seen["narration"])
        pass_dir = self.root / "qa" / "pass-01"
        self.assertTrue((pass_dir / "narration.txt").is_file())
        card = json.loads((pass_dir / "scorecard.json").read_text())
        self.assertEqual(card["story"]["premise_restated"], STORY["premise_restated"])
        self.assertTrue(any("satisfaction" in n for n in card["not_evidenced"]))

    def test_a_changed_narration_is_new_evidence(self):
        self.run_qa()
        pass_dir = self.root / "qa" / "pass-01"
        before = harness._tree_digest(pass_dir)
        (pass_dir / "narration.txt").write_text("another read")
        self.assertNotEqual(harness._tree_digest(pass_dir), before)

    def test_satisfaction_is_kept_once_the_channel_has_retention(self):
        (self.work / qa.RETENTION).write_text("{}")
        self.run_qa()
        card = json.loads((self.root / "qa" / "pass-01" / "scorecard.json").read_text())
        self.assertFalse(any("satisfaction" in n for n in card["not_evidenced"]))


if __name__ == "__main__":
    unittest.main()
