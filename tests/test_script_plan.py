"""AUTO-90: the plan is a story brief the later stages can check.

The beat sheet used to hold a logline, a promise, a payoff and a list of
beats, so the writer wrote facts in order and nothing could be checked
against stakes, questions or re-hooks. The plan now carries the train (one
sentence: who pursues what, against which obstacle, which question), the
stakes, the audience, the reveal and the constraint before it, a ledger of
questions with where each opens and closes, a connective between sections
and the ending; every shot carries its `function` in the story and what its
picture `shows` (docs/narrative: judging 13; structure 1, 7, 8, 14, 22, 36;
attention 7, 9-10; direction 4, 8).

No model is called: either `Screenwriter._ask` is a fake that answers by the
schema's shape, or `harness.ENGINE` is test_harness_decisions' FakeEngine.
"""

from __future__ import annotations

import copy
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import harness, script  # noqa: E402
from ytstudio.core import Idea  # noqa: E402

from tests.test_harness_decisions import FakeEngine, blank  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "scripts"


def idea():
    return Idea(title="Why the bridge fell", description="d", genre="explainer")


def kind(schema):
    """What a schema asks for, so a fake can answer it: the plan (it has a
    question ledger), the outline's plan fields (a train but no logline), a
    section's shots (each with a function), or the review passes."""
    props = schema["properties"]
    if "questions" in props:
        return "plan" if "logline" in props else "plan-fields"
    if "shots" in props:
        return "function" if "function" in props["shots"]["items"]["properties"] else "shots"
    for name in ("fixes", "flags", "edits", "sections"):
        if name in props:
            return name
    return ""


PLAN = {
    "logline": "A bridge that should have stood.",
    "promise": "You will see the one bolt that brought it down.",
    "payoff": "The bolt, and why nobody checked it.",
    "train": "Engineers race to learn whether one bolt, hidden by paint, "
             "doomed the bridge before the next one opens?",
    "stakes": "A twin bridge opens in a month with the same bolts.",
    "viewer_stake": "You drive over a bridge like it every week.",
    "misconception": "[M1] Bridges fail because they are old.",
    "misconception_why": "",
    "audience": "lay",
    "reveal": {"section": 3, "constraint_section": 2},
    "questions": [
        {"id": "Q1", "text": "What brought the bridge down?", "opened_in": 1, "closed_in": 3},
        {"id": "Q2", "text": "Why did no inspection see it?", "opened_in": 2, "closed_in": 3},
    ],
    "ending": {"payoff_line": "One bolt, painted over, held up four lanes.",
               "callback": "Back to the empty river from the first shot.",
               "next_film": "The bolt that saved the Golden Gate"},
    "sections": [
        {"heading": "The fall", "purpose": "open", "seconds": 20, "beats": ["it falls"],
         "opens": ["Q1"], "closes": [], "connective": "open", "rehook": "",
         "escalation": "", "anchor_visual": "the span in the river"},
        {"heading": "The paint", "purpose": "constraint", "seconds": 20,
         "beats": ["paint hides steel"], "opens": ["Q2"], "closes": [],
         "connective": "but", "rehook": "Q1", "escalation": "the twin opens soon",
         "anchor_visual": "a paint layer under a microscope"},
        {"heading": "The bolt", "purpose": "reveal", "seconds": 20, "beats": ["the bolt"],
         "opens": [], "closes": ["Q1", "Q2"], "connective": "therefore", "rehook": "",
         "escalation": "", "anchor_visual": "the sheared bolt"},
    ],
}


def shots_for(n_section, functions=("hook", "payoff")):
    return {"shots": [{"visual": f"v{n_section}.{i}", "narration": f"line {n_section}.{i}",
                       "seconds": 5, "asset": "make", "style": "s",
                       "function": fn, "shows": f"shows {n_section}.{i}"}
                      for i, fn in enumerate(functions, 1)]}


def writer(**kw):
    kw.setdefault("research", "")
    return script.Screenwriter(idea(), verbose=False, jobs=1, minutes=1, **kw)


def faked(w, plan=PLAN, functions=("context", "rehook"), fields=None):
    """Answer by the schema; every prompt and decision name is kept."""
    calls = []

    def ask(name, prompt, schema):
        calls.append({"name": name, "prompt": prompt, "kind": kind(schema)})
        which = kind(schema)
        if which == "plan":
            return copy.deepcopy(plan)
        if which == "plan-fields":
            return copy.deepcopy(fields)
        if which == "function":
            return shots_for(int(name.split("-")[1]), functions)
        return {}
    w._ask = ask
    return calls


# --------------------------------------------------------------------------- #
# the schema
# --------------------------------------------------------------------------- #

class Schema(unittest.TestCase):
    def test_the_plan_requires_the_story(self):
        required = set(script.PLAN_SCHEMA["required"])
        for name in ("train", "stakes", "viewer_stake", "audience", "reveal",
                     "questions", "ending"):
            self.assertIn(name, required)
        section = script.PLAN_SCHEMA["properties"]["sections"]["items"]
        for name in ("opens", "closes", "connective", "anchor_visual"):
            self.assertIn(name, section["required"])
        # asked for, not retried
        for name in ("rehook", "escalation"):
            self.assertIn(name, section["properties"])
            self.assertNotIn(name, section["required"])
        self.assertIn("misconception", script.PLAN_SCHEMA["properties"])
        self.assertEqual(section["properties"]["connective"]["enum"],
                         ["open", "and", "but", "therefore", "because"])
        self.assertEqual(script.PLAN_SCHEMA["properties"]["audience"]["enum"],
                         ["lay", "enthusiast", "expert"])
        props = script.PLAN_SCHEMA["properties"]
        self.assertEqual(set(props["reveal"]["required"]), {"section", "constraint_section"})
        self.assertEqual(set(props["questions"]["items"]["required"]),
                         {"id", "text", "opened_in", "closed_in"})
        self.assertEqual(set(props["ending"]["required"]),
                         {"payoff_line", "callback", "next_film"})
        self.assertNotIn("tail_seconds", json.dumps(script.PLAN_SCHEMA))

    def test_every_shot_states_its_function_and_what_it_shows(self):
        for sourced in (False, True):
            items = script.section_schema(sourced)["properties"]["shots"]["items"]
            self.assertIn("function", items["required"])
            self.assertIn("shows", items["required"])
            self.assertEqual(items["properties"]["function"]["enum"],
                             ["hook", "context", "setup", "reveal", "rehook",
                              "payoff", "handoff"])

    def test_the_descriptions_say_what_happens_to_an_empty_or_strange_value(self):
        props = script.PLAN_SCHEMA["properties"]
        self.assertIn("pre-check", props["train"]["description"])
        connective = props["sections"]["items"]["properties"]["connective"]
        self.assertIn("recorded", connective["description"])


# --------------------------------------------------------------------------- #
# through the harness: retried once, then a failure
# --------------------------------------------------------------------------- #

class ThroughTheHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._engine = harness.ENGINE
        harness.ENGINE = FakeEngine
        self.addCleanup(setattr, harness, "ENGINE", self._engine)
        FakeEngine.json_calls, FakeEngine.texts, FakeEngine.answer = [], [], None

    def writer(self, **kw):
        return script.Screenwriter(idea(), root=self.root, verbose=False, minutes=1, **kw)

    def test_a_plan_without_a_train_is_asked_again_then_fails(self):
        answer = blank(script.PLAN_SCHEMA)
        del answer["train"]
        FakeEngine.answer = answer
        with self.assertRaises(harness.HarnessError) as caught:
            self.writer().plan()
        self.assertIn("train", str(caught.exception))
        self.assertEqual(len(FakeEngine.json_calls), 2)
        self.assertIn("train is missing", FakeEngine.json_calls[1]["prompt"])

    def test_a_plan_without_a_rehook_is_not_asked_again(self):
        answer = blank(script.PLAN_SCHEMA)
        answer["sections"] = [blank(script.PLAN_SCHEMA["properties"]["sections"]["items"])]
        self.assertNotIn("rehook", answer["sections"][0])
        FakeEngine.answer = answer
        self.writer().plan()
        self.assertEqual(len(FakeEngine.json_calls), 1)

    def test_a_connective_off_the_list_is_asked_again_then_recorded(self):
        answer = copy.deepcopy(PLAN)
        answer["sections"][1]["connective"] = "so"
        FakeEngine.answer = answer
        w = self.writer()
        w.plan()
        self.assertEqual(len(FakeEngine.json_calls), 2)
        self.assertTrue(any("connective 'so' not in the schema" in f and "section 2" in f
                            for f in w.findings), w.findings)
        self.assertTrue(all(f.startswith("note:") for f in w.findings))

    def test_the_outline_plan_fields_are_a_kept_decision(self):
        answer = blank(script.PLAN_FIELDS_SCHEMA)
        answer["sections"] = [blank(script.PLAN_FIELDS_SCHEMA["properties"]["sections"]
                                    ["items"])]
        FakeEngine.answer = answer
        w = self.writer(outline={"working_title": "W", "sections": [
            {"heading": "A", "seconds": 30, "beats": ["b"]}]})
        w.plan()
        self.assertTrue((self.root / "decisions" / "script" / "plan-fields.json").is_file())


# --------------------------------------------------------------------------- #
# the prompts
# --------------------------------------------------------------------------- #

class PlanPrompt(unittest.TestCase):
    def test_the_plan_is_asked_as_a_story_in_the_crews_english(self):
        w = writer(lang="pt-BR")
        calls = faked(w)
        w.plan()
        text = calls[0]["prompt"]
        for words in ("train", "obstacle", "question", "stakes", "misconception",
                      "reveal", "constraint", "ledger", "connective", "BUT",
                      "THEREFORE", "next_film"):
            self.assertIn(words, text)
        self.assertIn("English", text)
        self.assertIn("whatever language", text)
        self.assertNotIn("tail_seconds", text)


class SectionPrompt(unittest.TestCase):
    def prompts(self, audience="lay"):
        plan = copy.deepcopy(PLAN)
        plan["audience"] = audience
        w = writer()
        calls = faked(w, plan=plan)
        w.run()
        return {c["name"]: c["prompt"] for c in calls if c["kind"] == "function"}

    def test_each_writer_is_handed_its_questions_connective_and_the_train(self):
        prompts = self.prompts()
        second = prompts["write-02"]
        self.assertIn("Q2", second)
        self.assertIn("Why did no inspection see it?", second)
        self.assertIn("BUT", second)
        self.assertIn(PLAN["train"], second)
        self.assertIn(PLAN["stakes"], second)
        self.assertIn("Q1", second)                     # the re-hook it re-opens
        self.assertIn("the twin opens soon", second)    # the escalation
        self.assertIn("a paint layer under a microscope", second)
        third = prompts["write-03"]
        self.assertIn("THEREFORE", third)
        self.assertIn("reveal", third)

    def test_the_audience_brief_reaches_the_writer(self):
        lay = self.prompts("lay")["write-01"]
        self.assertIn("define every term", lay)
        self.assertIn("misconception", lay.lower())
        expert = self.prompts("expert")["write-01"]
        self.assertIn("skip the basics", expert)
        self.assertNotIn("define every term", expert)

    def test_only_the_last_section_is_handed_the_ending(self):
        prompts = self.prompts()
        ending = PLAN["ending"]
        for key in ("payoff_line", "callback", "next_film"):
            self.assertIn(ending[key], prompts["write-03"])
            self.assertNotIn(ending[key], prompts["write-01"])

    def test_the_writer_is_asked_for_each_shots_function_and_what_it_shows(self):
        prompt = self.prompts()["write-01"]
        self.assertIn("function", prompt)
        self.assertIn("shows", prompt)
        self.assertIn("rehook", prompt)


class Outline(unittest.TestCase):
    OUTLINE = {"working_title": "The bridge", "sections": [
        {"heading": "The fall", "seconds": 30, "beats": ["it falls"],
         "claims_used": ["c"], "assets_used": ["a"]},
        {"heading": "The bolt", "seconds": 30, "beats": ["the bolt"]}]}

    def fields(self):
        fields = {k: copy.deepcopy(v) for k, v in PLAN.items()
                  if k not in ("logline", "sections")}
        fields["sections"] = [
            {k: v for k, v in s.items()
             if k in ("opens", "closes", "connective", "rehook", "escalation",
                      "anchor_visual")}
            for s in PLAN["sections"][:2]]
        return fields

    def test_outline_asks_the_plan_fields_instead_of_blanking_them(self):
        w = writer(outline=self.OUTLINE)
        calls = faked(w, fields=self.fields())
        plan = w.plan()
        self.assertEqual([c["name"] for c in calls], ["plan-fields"])
        self.assertIn("it falls", calls[0]["prompt"])          # over the outline
        self.assertEqual(plan["promise"], PLAN["promise"])
        self.assertEqual(plan["payoff"], PLAN["payoff"])
        self.assertEqual(plan["train"], PLAN["train"])
        self.assertEqual(plan["logline"], "The bridge")
        self.assertEqual([s["heading"] for s in plan["sections"]], ["The fall", "The bolt"])
        self.assertEqual(plan["sections"][1]["connective"], "but")
        self.assertEqual(plan["sections"][0]["claims"], ["c"])

    def test_outline_plan_fields_for_another_number_of_sections_fail(self):
        fields = self.fields()
        fields["sections"] = fields["sections"][:1]
        w = writer(outline=self.OUTLINE)
        faked(w, fields=fields)
        with self.assertRaises(script.ClaudeError):
            w.plan()

    def test_the_script_carries_promise_payoff_and_train(self):
        w = writer(outline=self.OUTLINE)
        faked(w, fields=self.fields())
        data = w.run()
        for key in ("promise", "payoff", "train"):
            self.assertEqual(data[key], PLAN[key])


# --------------------------------------------------------------------------- #
# what the screenplay keeps
# --------------------------------------------------------------------------- #

class Kept(unittest.TestCase):
    def run_it(self, functions=("hook", "payoff")):
        w = writer()
        faked(w, functions=functions)
        return w, w.run()

    def test_script_json_round_trips_every_field(self):
        _, data = self.run_it()
        again = json.loads(json.dumps(data))
        for key in ("train", "stakes", "viewer_stake", "misconception",
                    "misconception_why", "audience", "reveal", "questions", "ending"):
            self.assertEqual(again[key], PLAN[key], key)
        sections = script.sections_of(again)
        self.assertEqual(sections[1].opens, ["Q2"])
        self.assertEqual(sections[1].connective, "but")
        self.assertEqual(sections[1].rehook, "Q1")
        self.assertEqual(sections[1].escalation, "the twin opens soon")
        self.assertEqual(sections[2].closes, ["Q1", "Q2"])
        self.assertEqual(sections[0].anchor_visual, "the span in the river")
        self.assertEqual(sections[0].shots[0].function, "hook")
        self.assertEqual(sections[0].shots[1].shows, "shows 1.2")

    def test_a_function_off_the_list_is_recorded_and_kept_as_written(self):
        w, data = self.run_it(functions=("hook", "climax"))
        found = [f for f in data["findings"] if "climax" in f]
        self.assertEqual(len(found), 3, data["findings"])      # one per section
        self.assertTrue(found[0].startswith("note: shot 1.2: function 'climax' "
                                            "not in the schema"), found[0])
        self.assertIn("body", found[0])
        self.assertEqual(script.sections_of(data)[0].shots[1].function, "climax")

    def test_markdown_prints_the_train_stakes_ledger_and_ending(self):
        _, data = self.run_it()
        sections = script.sections_of(data)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "script.md"
            script.write_md(str(path), idea(), data, sections)
            md = path.read_text(encoding="utf-8")
        for text in (PLAN["train"], PLAN["stakes"], PLAN["viewer_stake"],
                     "What brought the bridge down?", "Q2",
                     PLAN["ending"]["payoff_line"], PLAN["ending"]["next_film"],
                     PLAN["ending"]["callback"], "shows 1.2", "payoff"):
            self.assertIn(text, md)
        ledger = md.split("Question ledger", 1)[1]
        self.assertIn("| Q1 | What brought the bridge down? | 1 | 3 |", ledger)

    def test_the_shot_list_has_function_and_shows_columns(self):
        _, data = self.run_it()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shotlist.csv"
            script.write_csv(str(path), script.sections_of(data))
            with open(path, encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
        self.assertEqual(rows[0]["function"], "hook")
        self.assertEqual(rows[1]["shows"], "shows 1.2")


class OlderScripts(unittest.TestCase):
    def test_every_older_fixture_loads_with_empty_function_and_shows(self):
        files = sorted(FIXTURES.glob("*.json"))
        self.assertTrue(files)
        for path in files:
            sections, data = script.load_script(str(path))
            shots = [s for sec in sections for s in sec.shots]
            self.assertTrue(shots, path.name)
            self.assertTrue(all(s.function == "" and s.shows == "" for s in shots))
            self.assertTrue(all(sec.opens == [] and sec.connective == ""
                                for sec in sections))

    def test_an_older_script_writes_its_markdown_and_shot_list(self):
        sections, data = script.load_script(str(sorted(FIXTURES.glob("*.json"))[0]))
        with tempfile.TemporaryDirectory() as tmp:
            script.write_md(f"{tmp}/s.md", Idea.from_raw(data["idea"]), data, sections)
            script.write_csv(f"{tmp}/s.csv", sections)


# --------------------------------------------------------------------------- #
# the pilot keeps the shot that pays the promise
# --------------------------------------------------------------------------- #

class Pilot(unittest.TestCase):
    LABELS = ("1.1", "1.2", "2.1", "2.2")

    def gather(self, functions):
        from ytstudio import pilot, workspace
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scene.xml").write_text("<scene/>")
            shots = [{"label": label, "visual": "v", "narration": f"line {label}",
                      "seconds": 5, "function": fn}
                     for label, fn in zip(self.LABELS, functions)]
            (root / "script.json").write_text(json.dumps({"sections": [
                {"heading": "A", "shots": shots[:2]}, {"heading": "B", "shots": shots[2:]}]}))
            (root / "build.json").write_text(json.dumps({"shots": [
                {"label": label, "at": n * 5.0, "seconds": 5.0, "asset": "effect"}
                for n, label in enumerate(self.LABELS)]}))
            beats, build = pilot.gather(workspace.Project(root, standalone=True))
            ranked = pilot.rank(beats)
            first_two = {b.label for b in ranked[:2]}
        return {b.label: b.traits for b in beats}, build, first_two

    def test_the_payoff_function_shot_is_kept_not_the_last(self):
        traits, build, kept = self.gather(("hook", "context", "payoff", "handoff"))
        self.assertIn("payoff", traits["2.1"])
        self.assertNotIn("payoff", traits["2.2"])
        self.assertIn("hook", traits["1.1"])
        self.assertEqual(kept, {"1.1", "2.1"})
        self.assertFalse(build.get("beat_notes"))

    def test_a_rehook_is_a_trait(self):
        traits, _, _ = self.gather(("hook", "rehook", "payoff", "handoff"))
        self.assertIn("rehook", traits["1.2"])

    def test_without_functions_the_last_shot_is_the_payoff_and_a_note_says_so(self):
        traits, build, kept = self.gather(("", "", "", ""))
        self.assertIn("payoff", traits["2.2"])
        self.assertIn("hook", traits["1.1"])
        self.assertEqual(kept, {"1.1", "2.2"})
        notes = build["beat_notes"]
        self.assertTrue(notes and all(n.startswith("note:") for n in notes), notes)
        self.assertTrue(any("payoff" in n and "2.2" in n for n in notes), notes)


# --------------------------------------------------------------------------- #
# the author is told what the picture must demonstrate
# --------------------------------------------------------------------------- #

class VfxBrief(unittest.TestCase):
    def test_script_sections_pass_shows_and_function_when_written(self):
        from ytstudio import vfx
        w = writer()
        faked(w, functions=("hook", "payoff"))
        data = w.run()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "script.json"
            path.write_text(json.dumps(data))
            rows = vfx.script_sections(str(path))
        shot = rows[0]["shots"][1]
        self.assertEqual(shot["shows"], "shows 1.2")
        self.assertEqual(shot["function"], "payoff")

    def test_an_older_script_adds_no_key(self):
        from ytstudio import vfx
        rows = vfx.script_sections(str(FIXTURES / "before-auto-90.json"))
        self.assertNotIn("shows", rows[0]["shots"][0])
        self.assertNotIn("function", rows[0]["shots"][0])

    def test_the_author_brief_names_what_the_picture_demonstrates(self):
        from ytstudio import vfx
        from tests.test_vfx_world import EFFECTS, SECTION, STYLE
        section = copy.deepcopy(SECTION)
        section["shots"][0]["shows"] = "the strand snaps where the tension peaks"
        with tempfile.TemporaryDirectory() as tmp:
            ws = vfx.Workspace(Path(tmp), version="v01")
            path = ws.brief(1, idea=idea(), house_rules="", style=STYLE,
                            tokens={"ink": "#FFFFFF"}, frame=(1920, 1080), section=section,
                            effects=EFFECTS, words={}, assets=[],
                            worlds=vfx.world_symbols(section, EFFECTS))
            text = path.read_text(encoding="utf-8")
        self.assertIn("What the picture must demonstrate of the line: the strand snaps "
                      "where the tension peaks", text)


if __name__ == "__main__":
    unittest.main()
