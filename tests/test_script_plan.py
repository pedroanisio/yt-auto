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

from ytstudio import harness, narrative, script  # noqa: E402
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
    "hook": {"trigger": "unexpected_observation",
             "gap": "why a bridge built to stand a century fell in its twentieth year"},
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
        {"heading": "The bolt", "purpose": "answer", "seconds": 20, "beats": ["the bolt"],
         "opens": [], "closes": ["Q1", "Q2"], "connective": "therefore", "rehook": "",
         "escalation": "", "anchor_visual": "the sheared bolt"},
    ],
}


#: AUTO-135's devices, as a plan section declares them.
ANALOGY = {"kind": "analogy",
           "device": "paint on a bolt is a plaster cast on a broken arm",
           "maps": "the cast -> the paint; the break under it -> the crack in the bolt",
           "breaks_at": "a cast is taken off to look; paint never is",
           "framing": "", "scored_in": 0}
PREDICTION = {"kind": "prediction", "device": "ask which part failed first",
              "maps": "", "breaks_at": "",
              "framing": "the span itself, not one bolt", "scored_in": 3}


#: section_story_lines for PLAN's three sections as develop wrote them
#: before AUTO-135, one string a section.
BEFORE_AUTO_135 = [
    "It opens the film.\nThe picture this section stands on: the span in the river",
    "It joins the section before with BUT: it turns against what that section "
    "established.\nRe-open Q1 (What brought the bridge down?) partway through, so the "
    "viewer stays for its answer.\nWhat gets harder or costlier here: the twin opens "
    "soon\nThe picture this section stands on: a paint layer under a microscope\nThis "
    "section shows why the reveal in section 3 is hard; do not give the reveal away.",
    "It joins the section before with THEREFORE: it follows from what that section "
    "established, as its consequence.\nThe picture this section stands on: the sheared "
    "bolt\nThis section holds the film's main reveal; section 2 showed why it is hard."
    "\nThis is the last section. Its last narrated line pays the promise, in the film's "
    "language: \"One bolt, painted over, held up four lanes.\". It mirrors the opening: "
    "Back to the empty river from the first shot.. It hands off to one next film: The "
    "bolt that saved the Golden Gate.",
]

NARRATIVE = Path(__file__).resolve().parent / "fixtures" / "narrative"

#: AUTO-158: the narrator's temperament for one film, as the plan names it.
PERSONA = {"temperament": "tender", "why": "a story of loss"}

#: story_lines(PLAN) as develop wrote it before AUTO-158: a plan with no
#: persona briefs every writer exactly this.
STORY_LINES_BEFORE_AUTO_158 = [
    "The film's train: Engineers race to learn whether one bolt, hidden by paint, "
    "doomed the bridge before the next one opens?\nWhat is at stake: A twin bridge "
    "opens in a month with the same bolts.\nWhy the viewer cares: You drive over a "
    "bridge like it every week.\nThe belief the film breaks: [M1] Bridges fail "
    "because they are old.",
    "The audience is lay: define every term the first time it is said, in plain "
    "words, and say the misconception out loud before you break it.",
]


def with_persona(persona=PERSONA):
    return {**copy.deepcopy(PLAN), "persona": copy.deepcopy(persona)}


def with_devices(**by_section):
    """PLAN with each section number's devices, e.g. with_devices(**{"2": [..]})."""
    plan = copy.deepcopy(PLAN)
    for n, devices in by_section.items():
        plan["sections"][int(n) - 1]["devices"] = copy.deepcopy(devices)
    return plan


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
        # AUTO-136: rehook and escalation are present in every section; the
        # value may be empty where the description allows (section 1, and
        # the payoff section's escalation)
        for name in ("opens", "closes", "connective", "rehook", "escalation",
                     "anchor_visual"):
            self.assertIn(name, section["required"])
        self.assertEqual(script.SECTION_STORY_REQUIRED,
                         ["opens", "closes", "connective", "rehook", "escalation",
                          "anchor_visual"])
        self.assertIn("empty only for section 1",
                      section["properties"]["rehook"]["description"])
        self.assertIn("final payoff section",
                      section["properties"]["escalation"]["description"])
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

    def test_the_plan_declares_its_hooks_trigger_and_gap(self):
        # AUTO-136: the trigger vocabulary is narrative's, never a copy here
        self.assertIn("hook", script.STORY_REQUIRED)
        self.assertIn("hook", script.PLAN_SCHEMA["required"])
        self.assertIn("hook", script.PLAN_FIELDS_SCHEMA["required"])
        hook = script.PLAN_SCHEMA["properties"]["hook"]
        self.assertIs(hook, script.STORY_PROPERTIES["hook"])
        self.assertEqual(sorted(hook["required"]), ["gap", "trigger"])
        self.assertEqual(hook["properties"]["trigger"]["enum"],
                         list(narrative.HOOK_TRIGGERS))
        self.assertIn("recorded", hook["properties"]["trigger"]["description"])
        self.assertIn("pre-check", hook["properties"]["gap"]["description"])
        self.assertFalse(hasattr(script, "HOOK_TRIGGERS"))
        self.assertFalse(hasattr(script, "HEARD_TRIGGERS"))
        for words in ("hook", "trigger", "gap", "paradox"):
            self.assertIn(words, script.STORY_BRIEF)
        # the per-section sentence says when rehook and escalation may be empty
        brief = " ".join(script.STORY_BRIEF.split())
        self.assertIn("empty only for section 1", brief)
        self.assertIn("the final payoff section", brief)

    def test_a_section_may_declare_devices_and_none_is_valid(self):
        # AUTO-135: an analogy, a visual metaphor, a prediction or a thought
        # experiment, each with what it maps and where it breaks; optional,
        # so --outline and an older plan still validate
        self.assertEqual(script.DEVICE_KINDS,
                         ("analogy", "visual_metaphor", "prediction", "thought_experiment"))
        for schema in (script.PLAN_SCHEMA, script.PLAN_FIELDS_SCHEMA):
            section = schema["properties"]["sections"]["items"]
            devices = section["properties"]["devices"]
            self.assertNotIn("devices", section["required"])
            item = devices["items"]
            self.assertEqual(item["properties"]["kind"]["enum"], list(script.DEVICE_KINDS))
            # the drift wording is shared, as _ENUM_DRIFT is for a role
            self.assertTrue(item["properties"]["kind"]["description"].endswith(
                " " + script._DEVICE_DRIFT))
            for name in ("kind", "device", "maps", "breaks_at", "framing", "scored_in"):
                self.assertIn(name, item["properties"])
            self.assertEqual(item["properties"]["scored_in"]["type"], "integer")
            self.assertIn("->", item["properties"]["maps"]["description"])
        self.assertNotIn("devices", script.SECTION_STORY_REQUIRED)
        from ytstudio.harness import enum_violations, missing_fields
        plan = copy.deepcopy(PLAN)
        for section in plan["sections"]:
            section.pop("devices", None)
        self.assertEqual(missing_fields(plan, script.PLAN_SCHEMA), [])
        plan["sections"][1]["devices"] = []
        self.assertEqual(missing_fields(plan, script.PLAN_SCHEMA), [])
        plan["sections"][1]["devices"] = [ANALOGY]
        self.assertEqual(missing_fields(plan, script.PLAN_SCHEMA)
                         + enum_violations(plan, script.PLAN_SCHEMA), [])

    def test_persona_is_asked_but_never_required(self):
        # AUTO-158: optional, so older plans, fixtures and outline plan
        # fields still validate and are never asked again for it
        from ytstudio.harness import missing_fields
        for schema in (script.PLAN_SCHEMA, script.PLAN_FIELDS_SCHEMA):
            self.assertIn("persona", schema["properties"])
            self.assertNotIn("persona", schema["required"])
        self.assertNotIn("persona", script.STORY_REQUIRED)
        persona = script.PLAN_SCHEMA["properties"]["persona"]
        self.assertEqual(set(persona["required"]), {"temperament", "why"})
        self.assertNotIn("enum", persona["properties"]["temperament"])
        self.assertIn("examples, not a list", persona["description"])
        self.assertEqual(missing_fields(PLAN, script.PLAN_SCHEMA), [])
        self.assertEqual(missing_fields(with_persona(), script.PLAN_SCHEMA), [])

    def test_the_plan_view_carries_the_persona_and_is_todays_without_one(self):
        self.assertEqual(script.plan_view(with_persona())["persona"], PERSONA)
        view = script.plan_view(PLAN)
        self.assertEqual(set(view), {
            "audience", "ending", "hook", "logline", "misconception",
            "misconception_why", "payoff", "promise", "questions", "reveal",
            "sections", "stakes", "train", "viewer_stake"})
        self.assertEqual({k: v for k, v in view.items() if k != "sections"},
                         {k: v for k, v in PLAN.items() if k != "sections"})

    def test_the_plan_view_passes_the_hook_on(self):
        self.assertEqual(script.plan_view(PLAN)["hook"], PLAN["hook"])

    def test_every_shot_states_its_function_and_what_it_shows(self):
        for sourced in (False, True):
            items = script.section_schema(sourced)["properties"]["shots"]["items"]
            self.assertIn("function", items["required"])
            self.assertIn("shows", items["required"])
            self.assertEqual(items["properties"]["function"]["enum"],
                             ["hook", "context", "setup", "reveal", "rehook",
                              "payoff", "handoff"])

    def test_a_question_left_open_on_purpose_is_declared_not_invented(self):
        # structure 8: a question deliberately left open is recorded as a
        # choice, so closed_in need not be made up
        item = script.PLAN_SCHEMA["properties"]["questions"]["items"]
        self.assertIn("0", item["properties"]["closed_in"]["description"])
        self.assertIn("left_open_why", item["properties"])
        self.assertNotIn("left_open_why", item["required"])
        self.assertIn("left open", script.STORY_BRIEF)
        self.assertIn("same section or a later one", script.STORY_BRIEF)
        ledger = "\n".join(script.question_ledger([
            {"id": "Q3", "text": "Will the twin stand?", "opened_in": 3, "closed_in": 0,
             "left_open_why": "the inspection is not done"}]))
        self.assertIn("| Q3 | Will the twin stand? | 3 | left open: the inspection "
                      "is not done |", ledger)

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

    def test_a_plan_without_a_rehook_key_is_asked_again_then_fails(self):
        # AUTO-136 reverses AUTO-90 here on purpose: the key is required in
        # every section, so a plan that leaves it out is asked again.
        answer = blank(script.PLAN_SCHEMA)
        answer["sections"] = [blank(script.PLAN_SCHEMA["properties"]["sections"]["items"])]
        del answer["sections"][0]["rehook"]
        FakeEngine.answer = answer
        with self.assertRaises(harness.HarnessError) as caught:
            self.writer().plan()
        self.assertIn("rehook", str(caught.exception))
        self.assertEqual(len(FakeEngine.json_calls), 2)

    def test_a_plan_without_an_escalation_key_is_asked_again_then_fails(self):
        answer = blank(script.PLAN_SCHEMA)
        answer["sections"] = [blank(script.PLAN_SCHEMA["properties"]["sections"]["items"])]
        del answer["sections"][0]["escalation"]
        FakeEngine.answer = answer
        with self.assertRaises(harness.HarnessError) as caught:
            self.writer().plan()
        self.assertIn("escalation", str(caught.exception))
        self.assertEqual(len(FakeEngine.json_calls), 2)
        self.assertIn("escalation", FakeEngine.json_calls[1]["prompt"])

    def test_an_empty_rehook_in_section_1_is_accepted(self):
        answer = blank(script.PLAN_SCHEMA)
        answer["sections"] = [blank(script.PLAN_SCHEMA["properties"]["sections"]["items"])]
        self.assertEqual(answer["sections"][0]["rehook"], "")
        FakeEngine.answer = answer
        self.writer().plan()
        self.assertEqual(len(FakeEngine.json_calls), 1)

    def test_a_hook_trigger_off_the_list_is_asked_again_then_recorded(self):
        answer = copy.deepcopy(PLAN)
        answer["hook"]["trigger"] = "teaser"
        FakeEngine.answer = answer
        w = self.writer()
        w.plan()
        self.assertEqual(len(FakeEngine.json_calls), 2)
        self.assertIn("trigger", FakeEngine.json_calls[1]["prompt"])
        self.assertIn("note: hook trigger 'teaser' not in the schema; it is not compared "
                      "with the heard opening", w.findings)
        self.assertEqual(script.planned_trigger(answer), None)
        self.assertEqual(script.planned_trigger(PLAN), "unexpected_observation")
        self.assertEqual(script.planned_trigger({}), None)

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

    def test_a_plan_with_no_device_is_not_asked_again(self):
        FakeEngine.answer = copy.deepcopy(PLAN)
        w = self.writer()
        w.plan()
        self.assertEqual(len(FakeEngine.json_calls), 1)
        self.assertEqual(w.findings, [])

    def test_a_device_kind_off_the_list_is_asked_again_recorded_and_ignored(self):
        answer = with_devices(**{"2": [dict(ANALOGY, kind="simile"), ANALOGY]})
        FakeEngine.answer = answer
        w = self.writer()
        plan = w.plan()
        self.assertEqual(len(FakeEngine.json_calls), 2)
        self.assertIn("kind", FakeEngine.json_calls[1]["prompt"])
        self.assertIn("note: section 2: device kind 'simile' not in the schema; the "
                      "device is ignored", w.findings)
        self.assertEqual(len(w.findings), 1, w.findings)
        lines = "\n".join(script.section_story_lines(plan["sections"][1], plan,
                                                      index=2, total=3))
        self.assertEqual(lines.count(ANALOGY["device"]), 1)
        self.assertNotIn("simile", lines)

    def test_an_audience_off_the_list_is_asked_again_then_recorded(self):
        answer = copy.deepcopy(PLAN)
        answer["audience"] = "kids"
        FakeEngine.answer = answer
        w = self.writer()
        plan = w.plan()
        self.assertEqual(len(FakeEngine.json_calls), 2)
        self.assertIn("audience", FakeEngine.json_calls[1]["prompt"])
        self.assertTrue(any(f.startswith("note: audience 'kids'") for f in w.findings),
                        w.findings)
        # no audience brief: none of AUDIENCE_BRIEF's texts is handed on
        lines = "\n".join(script.story_lines(plan))
        for text in script.AUDIENCE_BRIEF.values():
            self.assertNotIn(text, lines)

    OUTLINE = {"working_title": "W", "sections": [
        {"heading": "A", "seconds": 30, "beats": ["a"]},
        {"heading": "B", "seconds": 30, "beats": ["b"]}]}

    def fields(self, rows):
        answer = blank(script.PLAN_FIELDS_SCHEMA)
        answer["sections"] = [blank(script.PLAN_FIELDS_SCHEMA["properties"]["sections"]
                                    ["items"]) for _ in range(rows)]
        return answer

    def queue(self, *answers):
        """FakeEngine answering each ask with the next of `answers`."""
        pending = list(answers)

        class Queued(FakeEngine):
            def ask_json(self, prompt, schema, system=None):
                FakeEngine.json_calls.append({"prompt": prompt, "schema": schema})
                return pending.pop(0)
        harness.ENGINE = Queued

    def kept(self):
        return sorted(p.stem for p in (self.root / "decisions" / "script").glob("*.json"))

    def test_plan_fields_for_another_number_of_sections_are_asked_again(self):
        self.queue(self.fields(1), self.fields(2))
        plan = self.writer(outline=self.OUTLINE).plan()
        self.assertEqual(len(plan["sections"]), 2)
        self.assertEqual(len(FakeEngine.json_calls), 2)
        again = FakeEngine.json_calls[1]["prompt"]
        self.assertIn("you answered 1 section(s) for an outline of 2", again)
        self.assertIn("plan-fields-retry", self.kept())
        # a rerun is served the same two answers and asks nothing
        self.queue()
        self.assertEqual(len(self.writer(outline=self.OUTLINE).plan()["sections"]), 2)
        self.assertEqual(len(FakeEngine.json_calls), 2)

    def test_plan_fields_wrong_twice_fail_and_leave_nothing_kept(self):
        self.queue(self.fields(1), self.fields(3))
        with self.assertRaises(script.ClaudeError) as caught:
            self.writer(outline=self.OUTLINE).plan()
        self.assertIn("3 section(s) for an outline of 2", str(caught.exception))
        self.assertEqual(len(FakeEngine.json_calls), 2)
        self.assertEqual(self.kept(), [])
        # the next run asks again rather than replaying the bad answer
        self.queue(self.fields(2))
        self.assertEqual(len(self.writer(outline=self.OUTLINE).plan()["sections"]), 2)
        self.assertEqual(len(FakeEngine.json_calls), 3)

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

    def test_the_plan_asks_for_each_device_kind_and_none_where_none_is_needed(self):
        brief = " ".join(script.STORY_BRIEF.split())
        for kind_ in script.DEVICE_KINDS:
            self.assertIn(kind_, brief)
        for words in ("maps", "breaks_at", "scored_in", "framing", "imagined",
                      "at most one prediction",
                      "a mechanism shown directly needs no device; no device is better "
                      "than one that maps nothing"):
            self.assertIn(words, brief)


    def test_the_plan_asks_for_this_films_persona(self):
        w = writer()
        calls = faked(w)
        w.plan()
        text = " ".join(calls[0]["prompt"].split())
        for words in ("- persona:", "temperament", "THIS film",
                      "When the house format names a temperament, use that one and say "
                      "so in why."):
            self.assertIn(words, text)


class SectionPrompt(unittest.TestCase):
    def prompts(self, audience="lay", plan=PLAN):
        plan = copy.deepcopy(plan)
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

    def test_the_reveal_and_its_constraint_are_named_beats(self):
        prompts = self.prompts()
        self.assertIn("main reveal", prompts["write-03"])
        self.assertIn("section 2 showed why it is hard", prompts["write-03"])
        self.assertIn("do not give the reveal away", prompts["write-02"])
        self.assertIn("in section 3", prompts["write-02"])
        for text in ("main reveal", "reveal away", "reveal in section"):
            self.assertNotIn(text, prompts["write-01"])

    def test_the_plans_misconception_reaches_every_writer(self):
        for prompt in self.prompts().values():
            self.assertIn(PLAN["misconception"], prompt)

    def test_the_plans_persona_reaches_every_writer(self):
        prompts = self.prompts(plan=with_persona())
        self.assertEqual(sorted(prompts), ["write-01", "write-02", "write-03"])
        for name, prompt in prompts.items():
            self.assertIn("The narrator's temperament for this film: tender "
                          "(a story of loss).", prompt, name)
            self.assertIn("never replaces it", prompt, name)

    def test_a_plan_without_persona_briefs_the_writer_as_before(self):
        self.assertEqual(script.story_lines(PLAN), STORY_LINES_BEFORE_AUTO_158)
        for persona in ({}, {"temperament": "  ", "why": "w"}, "wry", None):
            with self.subTest(persona=persona):
                self.assertEqual(script.story_lines(with_persona(persona)),
                                 STORY_LINES_BEFORE_AUTO_158)
        self.assertNotIn("temperament", self.prompts()["write-01"])

    def test_a_persona_with_no_reason_is_named_alone(self):
        self.assertEqual(script.story_lines(with_persona({"temperament": "wry", "why": ""}))[-1],
                         "The narrator's temperament for this film: wry. Every line "
                         "speaks in it; it colours the proxy narrator, never replaces it.")

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

    def device_prompts(self, plan):
        w = writer()
        calls = faked(w, plan=plan)
        w.run()
        return {c["name"]: c["prompt"] for c in calls if c["kind"] == "function"}

    def test_an_analogy_reaches_its_own_sections_writer_only(self):
        prompts = self.device_prompts(with_devices(**{"2": [ANALOGY]}))
        second = prompts["write-02"]
        self.assertIn(f"Use this analogy: {ANALOGY['device']}.", second)
        self.assertIn(f"It maps {ANALOGY['maps']}", second)
        self.assertIn(f"say where it breaks: {ANALOGY['breaks_at']}", second)
        for name in ("write-01", "write-03"):
            self.assertNotIn(ANALOGY["device"], prompts[name])
            self.assertNotIn("analogy", prompts[name])

    def test_a_prediction_is_asked_in_its_section_and_scored_in_the_reveal(self):
        prompts = self.device_prompts(with_devices(**{"2": [PREDICTION]}))
        guess = PREDICTION["framing"]
        self.assertIn(f"Ask the viewer to guess {guess}", prompts["write-02"])
        self.assertIn("section 3's reveal scores it", prompts["write-02"])
        self.assertIn(f"Section 2 asked the viewer to guess {guess}; say whether that "
                      "guess was right when this section's reveal lands.",
                      prompts["write-03"])
        self.assertNotIn("guess", prompts["write-01"])
        self.assertNotIn("say whether that guess was right", prompts["write-02"])

    def test_a_guess_asked_and_scored_in_the_reveal_is_told_to_be_scored(self):
        # asked in the reveal section and scored by it: the one writer both
        # asks and must say whether the guess was right
        lines = "\n".join(script.section_story_lines(
            dict(PLAN["sections"][2], devices=[PREDICTION]), PLAN, index=3, total=3))
        self.assertIn("this section's reveal scores it; say whether that guess was right "
                      "when the reveal lands.", lines)

    def test_a_scored_in_past_the_last_section_names_no_section(self):
        # section 9 of a 3-section plan: no writer would be told to score it
        plan = with_devices(**{"2": [dict(PREDICTION, scored_in=9)]})
        prompts = self.device_prompts(plan)
        self.assertIn("; the reveal scores it.", prompts["write-02"])
        self.assertNotIn("section 9", prompts["write-02"])

    def test_an_integral_float_scored_in_is_read_as_its_section(self):
        # the harness's type check passes 3.0 as an integer, so the stage does
        plan = with_devices(**{"2": [dict(PREDICTION, scored_in=3.0)]})
        lines = "\n".join(script.section_story_lines(plan["sections"][2], plan, index=3,
                                                     total=3))
        self.assertIn(f"Section 2 asked the viewer to guess {PREDICTION['framing']}; say "
                      "whether that guess was right when this section's reveal lands.", lines)
        self.assertEqual(script._scored_in({"scored_in": 3.0}), 3)
        self.assertEqual(script._scored_in({"scored_in": 2.5}), 0)
        self.assertEqual(script._scored_in({"scored_in": True}), 0)

    def test_a_thought_experiment_and_a_visual_metaphor_are_briefed(self):
        imagined = {"kind": "thought_experiment", "device": "a bridge with no paint at all",
                    "maps": "", "breaks_at": "", "framing": "imagine the same bridge bare",
                    "scored_in": 0}
        picture = dict(ANALOGY, kind="visual_metaphor", device="the paint drawn as a veil")
        lines = "\n".join(script.section_story_lines(
            with_devices(**{"1": [imagined, picture]})["sections"][0], PLAN,
            index=1, total=3))
        self.assertIn("thought experiment", lines)
        self.assertIn("imagine the same bridge bare", lines)
        self.assertIn("visual metaphor", lines)
        self.assertIn("the paint drawn as a veil", lines)

    def test_a_plan_without_devices_briefs_exactly_as_before(self):
        # BEFORE_AUTO_135 is what develop told PLAN's writers before devices
        # existed, so a constant device line would fail here
        plan = copy.deepcopy(PLAN)
        for n, spec in enumerate(plan["sections"], 1):
            for devices in ({}, {"devices": []}):
                with self.subTest(section=n, devices=devices):
                    self.assertEqual(script.section_story_lines(
                        dict(spec, **devices), plan, index=n, total=3),
                        [BEFORE_AUTO_135[n - 1]])
        good = script.read_json(str(NARRATIVE / "good" / "plan.json"))
        for n, spec in enumerate(good["sections"], 1):
            text = "\n".join(script.section_story_lines(spec, good, index=n,
                                                        total=len(good["sections"])))
            for word in ("guess", "analogy", "metaphor", "thought experiment"):
                self.assertNotIn(word, text, (n, word))

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
        calls = faked(w, fields=fields)
        with self.assertRaises(script.ClaudeError):
            w.plan()
        self.assertEqual([c["name"] for c in calls], ["plan-fields", "plan-fields-retry"])

    def test_outline_plan_fields_carry_the_persona_to_the_plan_and_the_script(self):
        fields = dict(self.fields(), persona=copy.deepcopy(PERSONA))
        w = writer(outline=self.OUTLINE)
        faked(w, fields=fields)
        self.assertEqual(w.plan()["persona"], PERSONA)
        w = writer(outline=self.OUTLINE)
        faked(w, fields=fields)
        self.assertEqual(w.run()["persona"], PERSONA)

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
    def run_it(self, functions=("hook", "payoff"), plan=PLAN):
        w = writer()
        faked(w, plan=plan, functions=functions)
        return w, w.run()

    def test_script_json_round_trips_every_field(self):
        _, data = self.run_it(plan=with_persona())
        again = json.loads(json.dumps(data))
        for key in ("train", "stakes", "viewer_stake", "misconception",
                    "misconception_why", "audience", "reveal", "ending"):
            self.assertEqual(again[key], PLAN[key], key)
        self.assertEqual(again["persona"], PERSONA)
        self.assertNotIn("persona", self.run_it()[1])
        # the plan's ledger, each question with the shots that raise and
        # answer it beside it (AUTO-96)
        self.assertEqual([{k: v for k, v in q.items() if k not in ("opened_at", "closed_at")}
                          for q in again["questions"]], PLAN["questions"])
        for q in again["questions"]:
            self.assertIn("opened_at", q)
            self.assertIn("closed_at", q)
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

    def test_markdown_prints_the_persona(self):
        def md(plan):
            _, data = self.run_it(plan=plan)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "script.md"
                script.write_md(str(path), idea(), data, script.sections_of(data))
                return path.read_text(encoding="utf-8")
        text = md(with_persona())
        self.assertIn("**Narrator.** tender: a story of loss", text)
        self.assertLess(text.index("**Audience.**"), text.index("**Narrator.**"))
        self.assertNotIn("**Narrator.**", md(PLAN))

    def test_the_printed_script_lists_its_findings(self):
        import contextlib
        import io
        w, data = self.run_it(functions=("hook", "climax"))
        self.assertTrue(data.get("findings"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            script.print_script(idea(), data, script.sections_of(data))
        text = out.getvalue()
        self.assertIn("FINDINGS", text)
        for finding in data["findings"]:
            self.assertIn(finding[:40], text)

    def test_the_shot_list_has_function_and_shows_columns(self):
        _, data = self.run_it()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shotlist.csv"
            script.write_csv(str(path), script.sections_of(data))
            with open(path, encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
        self.assertEqual(rows[0]["function"], "hook")
        self.assertEqual(rows[1]["shows"], "shows 1.2")


class Devices(unittest.TestCase):
    """AUTO-135: a section's devices are kept, and an older one reads as none."""

    def test_from_spec_round_trips_devices_and_an_older_section_has_none(self):
        spec = dict(PLAN["sections"][1], devices=[ANALOGY, PREDICTION])
        section = script.Section.from_spec(spec)
        self.assertEqual(section.devices, [ANALOGY, PREDICTION])
        again = script.Section.from_spec(json.loads(json.dumps(
            {**{k: v for k, v in vars(section).items() if k != "shots"}, "shots": []})))
        self.assertEqual(again.devices, [ANALOGY, PREDICTION])
        self.assertEqual(script.Section.from_spec(PLAN["sections"][1]).devices, [])
        self.assertEqual(script.Section.from_spec(dict(PLAN["sections"][1],
                                                       devices=None)).devices, [])

    def test_script_json_and_markdown_carry_a_sections_devices(self):
        w = writer()
        faked(w, plan=with_devices(**{"2": [ANALOGY]}), functions=("hook", "payoff"))
        data = w.run()
        sections = script.sections_of(json.loads(json.dumps(data)))
        self.assertEqual(sections[1].devices, [ANALOGY])
        self.assertEqual(sections[0].devices, [])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "script.md"
            script.write_md(str(path), idea(), data, sections)
            md = path.read_text(encoding="utf-8")
        line = next(l for l in md.splitlines() if "stands on: a paint layer" in l)
        self.assertIn(f"analogy: {ANALOGY['device']}", line)

    def test_every_older_fixture_reads_with_no_devices(self):
        for path in sorted(FIXTURES.glob("*.json")):
            sections, _ = script.load_script(str(path))
            with self.subTest(fixture=path.name):
                self.assertTrue(all(sec.devices == [] for sec in sections))


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

    def test_the_beat_note_is_printed_apart_from_the_sdks_findings(self):
        import contextlib
        import io
        from ytstudio import pilot
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            pilot.print_findings(["note: the screenplay marks no payoff shot; the last "
                                  "shot, 2.2, stands for it"],
                                 ["warning: a scene check"])
        text = out.getvalue()
        self.assertIn("the SDK raised 1 finding(s)", text)
        head, sdk = text.split("the SDK raised", 1)
        self.assertIn("the screenplay's beats", head)
        self.assertIn("2.2, stands for it", head)
        self.assertNotIn("stands for it", sdk)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            pilot.print_findings(["note: n"], [])
        self.assertNotIn("SDK", out.getvalue())

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


# --------------------------------------------------------------------------- #
# paper mode (AUTO-131)
# --------------------------------------------------------------------------- #

PAPER_URL = "https://arxiv.org/pdf/1706.03762"

#: The dossier's claims a paper film quotes, and its digest.
PAPER_CLAIMS = [
    {"claim": "The Transformer drops recurrence entirely.", "source_url": PAPER_URL},
    {"claim": "Training a translation model took days of GPU time.",
     "source_url": "https://example.org/cost"},
    {"claim": "It reached 28.4 BLEU on English-German.", "source_url": PAPER_URL + "v5"},
    {"claim": "Most large language models since build on it.",
     "source_url": "https://example.org/review"},
]
PAPER_DIGEST = {
    "citation": {"title": "Attention Is All You Need", "authors": "Vaswani et al.",
                 "year": "2017", "url": PAPER_URL},
    **{key: {"text": f"the digest's {key}", "claims": [PAPER_CLAIMS[n]["claim"]]}
       for key, n in (("problem", 1), ("contribution", 0), ("significance", 2),
                      ("implications", 3), ("consequences", 3), ("limitations", 0))},
}


def framing(**sections):
    """A framing over PLAN's three sections, the mechanism in section 3."""
    at = {"problem": 1, "significance": 2, "implications": 2, "contribution": 3,
          "consequences": 3, "limitations": 3, **sections}
    quote = {"problem": 1, "contribution": 0, "significance": 2, "implications": 3,
             "consequences": 3, "limitations": 0}
    return {key: {"said": f"the film's {key}", "claims": [PAPER_CLAIMS[quote[key]]["claim"]],
                  "section": at[key]} for key in at}


FRAMED = {**copy.deepcopy(PLAN), "framing": framing(), "mechanism_section": 3}


def paper_writer(**kw):
    return writer(paper=PAPER_DIGEST, claims=PAPER_CLAIMS, **kw)


def asked(w, plan=FRAMED, fields=None):
    """Answer by the schema; keep each decision's prompt and schema."""
    calls = []
    queue = list(fields or [])

    def ask(name, prompt, schema):
        calls.append({"name": name, "prompt": prompt, "schema": schema})
        which = kind(schema)
        if which == "plan":
            return copy.deepcopy(plan)
        if which == "plan-fields":
            return copy.deepcopy(queue.pop(0))
        if which == "function":
            return shots_for(int(name.split("-")[1]))
        return {"verdict": "unlisted"}
    w._ask = ask
    return calls


def outline_fields(rows=2, paper=False):
    fields = {k: copy.deepcopy(v) for k, v in PLAN.items() if k not in ("logline", "sections")}
    fields["sections"] = [{k: v for k, v in s.items()
                           if k in ("opens", "closes", "connective", "rehook", "escalation",
                                    "anchor_visual")} for s in PLAN["sections"][:rows]]
    if paper:
        fields["framing"] = framing(significance=1, implications=1, contribution=2,
                                    consequences=2, limitations=2)
        fields["mechanism_section"] = 2
    return fields


OUTLINE_2 = {"working_title": "The bridge", "sections": [
    {"heading": "The fall", "seconds": 30, "beats": ["it falls"]},
    {"heading": "The bolt", "seconds": 30, "beats": ["the bolt"]}]}

SENT_BACK = {"problems": [{"section": 2, "problem": "p", "fix": "f"}], "train_ok": False}


def every_plan_ask(w_scratch, w_outline, paper=False) -> list[dict]:
    """plan, plan-2, plan-fields, plan-fields-retry and plan-fields-2, each asked once."""
    calls = asked(w_scratch)
    w_scratch.plan()
    w_scratch.plan(previous=PLAN, review=SENT_BACK)
    out = asked(w_outline, fields=[outline_fields(1, paper), outline_fields(2, paper),
                                   outline_fields(2, paper)])
    w_outline.plan()
    w_outline.plan(previous=PLAN, review=SENT_BACK)
    return calls + out


class PaperSchema(unittest.TestCase):
    def test_without_a_paper_the_schemas_are_todays_objects(self):
        self.assertIs(script.plan_schema(False), script.PLAN_SCHEMA)
        self.assertIs(script.plan_fields_schema(False), script.PLAN_FIELDS_SCHEMA)

    def test_with_a_paper_framing_and_the_mechanism_are_required(self):
        for schema in (script.plan_schema(True), script.plan_fields_schema(True)):
            self.assertIn("framing", schema["required"])
            self.assertIn("mechanism_section", schema["required"])
            frame = schema["properties"]["framing"]
            self.assertEqual(set(frame["required"]), set(script.PAPER_FRAMING))
            for key in script.PAPER_FRAMING:
                self.assertEqual(set(frame["properties"][key]["required"]),
                                 {"said", "claims", "section"})
            self.assertEqual(schema["properties"]["mechanism_section"]["type"], "integer")
        self.assertEqual(script.PAPER_FRAMING, ("problem", "contribution", "significance",
                                                "implications", "consequences",
                                                "limitations"))
        self.assertNotIn("framing", script.PLAN_SCHEMA["properties"])
        self.assertNotIn("framing", script.PLAN_FIELDS_SCHEMA["required"])


class PaperThroughTheHarness(unittest.TestCase):
    setUp = ThroughTheHarness.setUp
    writer = ThroughTheHarness.writer

    def test_a_paper_plan_without_framing_is_asked_again_then_fails(self):
        answer = blank(script.plan_schema(True))
        del answer["framing"]
        FakeEngine.answer = answer
        with self.assertRaises(harness.HarnessError) as caught:
            self.writer(paper=PAPER_DIGEST, claims=PAPER_CLAIMS).plan()
        self.assertIn("framing", str(caught.exception))
        self.assertEqual(len(FakeEngine.json_calls), 2)


class PaperPrompts(unittest.TestCase):
    def test_every_plan_ask_carries_the_paper_schema_the_brief_and_the_digest(self):
        calls = every_plan_ask(paper_writer(), paper_writer(outline=OUTLINE_2), paper=True)
        self.assertEqual([c["name"] for c in calls],
                         ["plan", "plan-2", "plan-fields", "plan-fields-retry",
                          "plan-fields-2"])
        for call in calls:
            self.assertIn("framing", call["schema"]["required"], call["name"])
            self.assertIn("mechanism_section", call["schema"]["required"], call["name"])
            prompt = call["prompt"]
            self.assertIn(script.PAPER_BRIEF, prompt, call["name"])
            self.assertLess(prompt.index(script.STORY_BRIEF), prompt.index(script.PAPER_BRIEF))
            self.assertIn("The paper this film explains", prompt, call["name"])
            self.assertIn("the digest's significance", prompt, call["name"])

    def test_the_paper_brief_says_where_each_item_lands(self):
        text = script.PAPER_BRIEF
        for words in ("before mechanism_section", "contribution may land at "
                      "mechanism_section", "section 1 or later", "quotes the research "
                      "claims", "apart from what others say it means"):
            self.assertIn(words, text)

    def test_a_plan_without_a_paper_never_mentions_framing(self):
        calls = every_plan_ask(writer(), writer(outline=OUTLINE_2))
        for call in calls:
            # AUTO-135's devices have a `framing` of their own (how a thought
            # experiment is marked as imagined, the guess a prediction asks
            # for), so the word is in STORY_BRIEF; the paper's framing is not
            self.assertNotIn(script.PAPER_BRIEF, call["prompt"], call["name"])
            self.assertNotIn("framing: for each of problem", call["prompt"], call["name"])
            self.assertNotIn("framing.", call["prompt"], call["name"])
            self.assertNotIn("mechanism_section", call["prompt"], call["name"])
            self.assertNotIn("framing", call["schema"]["properties"], call["name"])

    def test_an_outline_paper_plan_keeps_framing_and_the_mechanism(self):
        w = paper_writer(outline=OUTLINE_2)
        asked(w, fields=[outline_fields(2, paper=True)])
        plan = w.plan()
        self.assertEqual(plan["framing"], outline_fields(2, paper=True)["framing"])
        self.assertEqual(plan["mechanism_section"], 2)
        self.assertEqual(script.paper_prechecks(plan, PAPER_CLAIMS, PAPER_URL), [])

    def test_an_outline_plan_without_a_paper_keeps_neither(self):
        w = writer(outline=OUTLINE_2)
        asked(w, fields=[outline_fields(2, paper=True)])
        plan = w.plan()
        self.assertNotIn("framing", plan)
        self.assertNotIn("mechanism_section", plan)

    def test_each_section_is_handed_the_framing_items_it_carries(self):
        specs = FRAMED["sections"]
        first = "\n".join(script.section_story_lines(specs[0], FRAMED, index=1, total=3))
        self.assertIn("This section carries the paper's problem: the film's problem", first)
        self.assertIn(f"resting on: {PAPER_CLAIMS[1]['claim']}", first)
        self.assertNotIn("significance", first)
        second = "\n".join(script.section_story_lines(specs[1], FRAMED, index=2, total=3))
        self.assertIn("the paper's significance: the film's significance", second)
        self.assertIn("the paper's implications", second)
        self.assertNotIn("the paper's problem", second)
        bare = "\n".join(script.section_story_lines(specs[0], PLAN, index=1, total=3))
        self.assertNotIn("paper", bare)

    def test_the_plan_view_carries_framing_and_the_mechanism(self):
        view = script.plan_view(FRAMED)
        self.assertEqual(view["framing"], FRAMED["framing"])
        self.assertEqual(view["mechanism_section"], 3)
        self.assertNotIn("framing", script.plan_view(PLAN))


def plan_prompts() -> dict[str, str]:
    """Every plan prompt and schema a film without a paper is asked, by
    decision name, the plan review's included."""
    w = writer(research="R", reviewer_model="opus")
    calls = asked(w)
    w.plan()
    w.plan(previous=PLAN, review=SENT_BACK)
    w.review_plan(PLAN)
    o = writer(research="R", outline=OUTLINE_2)
    more = asked(o, fields=[outline_fields(1), outline_fields(2), outline_fields(2)])
    o.plan()
    o.plan(previous=PLAN, review=SENT_BACK)
    calls += more
    out = {c["name"]: c["prompt"] for c in calls}
    out.update({f"{c['name']}:schema": json.dumps(c["schema"], sort_keys=True) for c in calls})
    return out


#: sha256 of each plan prompt and schema, taken on develop 57fd5a5 before
#: AUTO-131: a film without a paper is asked exactly what it was. A later
#: card that changes the plan's prompts on purpose takes new values here:
#: AUTO-136 did (the hook's trigger and gap, each section's rehook and
#: escalation), and AUTO-135 did (each section's optional devices, and the
#: devices row of the rubric in the plan review), and AUTO-158 did (the
#: plan's optional persona, in STORY_BRIEF and the two plan schemas; the
#: plan review's prompt is unchanged for a plan without one).
#: PLAN_REVIEW_SCHEMA is unchanged by all three. AUTO-161 changed only the
#: plan review's prompt and schema (stakes as a revise ground, and the
#: `stakes_concrete` field); every plan prompt and schema kept its value.
PLAN_PROMPTS = {
    "plan":
        "3878df073210e90fc0fda6d366107d0d72c14b7f11f7d802e44a96c43bc0260e",
    "plan-2":
        "1b5395c9fe872794a6d72f21013a57e4b49853ec302f79a5989b1b39d1fa012f",
    "plan-2:schema":
        "236f0d4b71ec8b56f7594abca16d6a59af4e5297d734ee3efc085515194215e6",
    "plan-fields":
        "a12b86cd957ee7d0f49f349ad657dcb9483df7023830ab404d16803f9c31962d",
    "plan-fields-2":
        "8c7a6ed71257c05ed603eaf925b3ead6c21b601d6ca6488a81e2f38145ce805d",
    "plan-fields-2:schema":
        "77508224eb2a27046c7408b44a54e422ddf631c4a207a9f20ad9aa1ead7ce4f8",
    "plan-fields-retry":
        "1cd23624d935136be97611de561b685543d44eeb159c29f083e16bb625056c8a",
    "plan-fields-retry:schema":
        "77508224eb2a27046c7408b44a54e422ddf631c4a207a9f20ad9aa1ead7ce4f8",
    "plan-fields:schema":
        "77508224eb2a27046c7408b44a54e422ddf631c4a207a9f20ad9aa1ead7ce4f8",
    "plan-review":
        "d54037ff86762bec8147f79c1479264a8da24dec686d028e9f0cb6e0dd07c271",
    "plan-review:schema":
        "d78a2db03a07add01992161c942750505a3d2b296c5bce3738b5ea707d6b03d0",
    "plan:schema":
        "236f0d4b71ec8b56f7594abca16d6a59af4e5297d734ee3efc085515194215e6",
}


class PaperUnchanged(unittest.TestCase):
    def test_a_film_without_a_paper_is_asked_todays_plan_prompts_and_schemas(self):
        import hashlib
        got = {k: hashlib.sha256(v.encode()).hexdigest() for k, v in plan_prompts().items()}
        self.assertEqual(got, PLAN_PROMPTS)


# --------------------------------------------------------------------------- #
# AUTO-132: a sourced shot names its claim and its status
# --------------------------------------------------------------------------- #

def _sha(value) -> str:
    import hashlib
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()


#: sha256 of today's schemas and of the unsourced write prompt, taken on
#: develop 29d523a before AUTO-132. Harness.decide hashes the schema into
#: the cache key, so a change here would make every cached decision miss.
FROZEN = {
    "REVISE_SCHEMA": "bd7066142cbe40fa86325e73b7b6bb56292f852cafcf119305e0d9e5158347b3",
    "RULE_REVISE_SCHEMA": "879be68a7fa5eabae7668c65173dff1e31bb73d67781f32c4b8a8346219154ef",
    "BACK_REVISE_SCHEMA": "bc7b69345440a4ed9f84669e5f150b13b7da0357208d8cbf46431c4446c2b9bc",
    "section_schema(False)":
        "4679dcf7bb0ce9bd03dd8aa6a60e749824d1ae0748a4c3bd25431edbf61b59db",
    "write prompt": "eb62109106e78f40334deaf9d08b3417da8dfa55b095714aea53d505ec58eb1f",
}


class StatusSchema(unittest.TestCase):
    def write_prompt(self, sourced):
        seen = []
        w = writer(research="R" if sourced else "")
        w._ask = lambda name, prompt, schema: seen.append(prompt) or {"shots": []}
        w.write_section({"heading": "A", "seconds": 10, "beats": ["b"]}, index=1, total=1,
                        plan={}, written=[], next_heading="", sourced=sourced)
        return seen[0]

    def test_a_sourced_shot_must_name_its_claim_and_status(self):
        items = script.section_schema(True)["properties"]["shots"]["items"]
        self.assertIn("claim", items["required"])
        self.assertIn("status", items["required"])
        status = items["properties"]["status"]
        self.assertEqual(status["enum"],
                         ["established", "hypothesis", "speculation", "what-if", "none"])
        self.assertIn("asked again once", status["description"])
        self.assertNotIn("ceiling", items["properties"])

    def test_the_unsourced_schema_and_the_revise_schemas_are_todays(self):
        self.assertEqual(_sha(script.section_schema(False)), FROZEN["section_schema(False)"])
        for name in ("REVISE_SCHEMA", "RULE_REVISE_SCHEMA", "BACK_REVISE_SCHEMA"):
            self.assertEqual(_sha(getattr(script, name)), FROZEN[name], name)

    def test_the_fact_revise_schema_adds_only_an_optional_status(self):
        fact = copy.deepcopy(script.FACT_REVISE_SCHEMA)
        item = fact["properties"]["fixes"]["items"]
        self.assertNotIn("status", item["required"])
        self.assertEqual(item["properties"].pop("status")["enum"], list(script.STATUSES))
        self.assertEqual(fact, script.REVISE_SCHEMA)

    def test_the_sourced_prompt_asks_for_the_weakest_claim_and_the_status(self):
        prompt = self.write_prompt(True)
        self.assertIn("weakest claim", prompt)
        self.assertIn("- status:", prompt)
        self.assertIn("says so aloud or on screen", prompt)

    def test_the_unsourced_prompt_is_todays(self):
        self.assertEqual(_sha(self.write_prompt(False)), FROZEN["write prompt"])


if __name__ == "__main__":
    unittest.main()
