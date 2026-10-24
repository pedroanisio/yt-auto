"""AUTO-92: the beat sheet is reviewed before any section is paid for.

Nothing stood between `plan()` and the drafting: the first story judgement
was `yt qa` on the rendered film, after voice, keyframe and vfx were paid
for. Reviewing a plan costs one decision; drafting a bad one costs every
write-NN and the voice. With `yt script --review` a reviewer of another
model family reads the plan (`plan-review`, under
narrative.PLAN_REVIEW_SCHEMA): accept drafts, revise asks the plan once
more (`plan-2`) with the problems in the prompt, and a reject or a second
revise stops the stage before any section is written (docs/narrative:
judging 10, 13; structure 1, 3, 4, 22, 23).

No model is called: `Screenwriter._ask` is a fake that answers by the
schema's shape, or `harness.ENGINE` is a fake engine.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_engine import ClaudeError  # noqa: E402
from ytstudio import cli, harness, narrative, script  # noqa: E402

from tests.test_harness_decisions import FakeEngine, blank  # noqa: E402
from tests.test_script_plan import PLAN, idea, shots_for  # noqa: E402


def kind(schema):
    """What a schema asks for: the plan review (a verdict and problems),
    the plan, the outline's plan fields, a section's shots, or another pass."""
    props = schema["properties"]
    if schema is narrative.PICTURE_SCHEMA:
        return "picture-check"
    if "verdict" in props and "problems" in props:
        return "plan-review"
    if "questions" in props:
        return "plan" if "logline" in props else "plan-fields"
    if "shots" in props:
        return "shots"
    for name in ("fixes", "flags", "edits"):
        if name in props:
            return name
    return ""


def verdict(word, problems=()):
    return {"verdict": word, "train_ok": word != "reject", "reorder_survives": word == "reject",
            "sections_advancing_none": [], "payoff_concrete": word != "reject",
            "questions_unanswered": [],
            "problems": [{"section": s, "problem": p, "fix": f} for s, p, f in problems]}


SWAPPABLE = (2, "sections 2 and 3 could swap without loss", "make 3 follow from 2's paint")


def writer(**kw):
    kw.setdefault("research", "")
    kw.setdefault("reviewer_model", "opus")
    return script.Screenwriter(idea(), verbose=False, jobs=1, minutes=1, **kw)


def faked(w, verdicts, plan=PLAN, fields=None):
    """Answer by the schema; the plan reviews answer `verdicts` in order.
    Every decision's name and prompt is kept, in the order asked."""
    calls = []
    queue = list(verdicts)

    def ask(name, prompt, schema):
        which = kind(schema)
        calls.append({"name": name, "prompt": prompt, "kind": which})
        if which == "plan-review":
            return copy.deepcopy(queue.pop(0))
        if which == "plan":
            return copy.deepcopy(plan)
        if which == "plan-fields":
            return copy.deepcopy(fields)
        if which == "shots":
            return shots_for(int(name.split("-")[1]), ("context", "payoff"))
        return {}
    w._ask = ask
    return calls


def names(calls):
    return [c["name"] for c in calls]


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #

class Gate(unittest.TestCase):
    def test_accept_drafts_and_the_review_precedes_every_section(self):
        w = writer()
        calls = faked(w, [verdict("accept")])
        data = w.run(review=True)
        order = names(calls)
        self.assertEqual(order[:2], ["plan", "plan-review"])
        writes = [i for i, n in enumerate(order) if n.startswith("write-")]
        self.assertEqual(len(writes), 3)
        self.assertLess(order.index("plan-review"), min(writes))
        self.assertEqual(data["plan_review"]["verdict"], "accept")
        self.assertEqual(data["plan_review"]["problems"], [])
        self.assertFalse(data["plan_review"]["retried"])
        self.assertEqual(data["plan_review"]["model"], "opus")

    def test_without_review_no_plan_review_is_asked(self):
        w = writer()
        calls = faked(w, [])
        data = w.run()
        self.assertNotIn("plan-review", names(calls))
        self.assertNotIn("plan_review", data)

    def test_revise_asks_the_plan_once_more_with_the_problems_and_drafts(self):
        w = writer()
        calls = faked(w, [verdict("revise", [SWAPPABLE]), verdict("accept")])
        data = w.run(review=True)
        order = names(calls)
        self.assertEqual(order[:4], ["plan", "plan-review", "plan-2", "plan-review-2"])
        self.assertEqual(order.count("plan-2"), 1)
        again = calls[2]["prompt"]
        self.assertIn("sections 2 and 3 could swap without loss", again)
        self.assertIn("make 3 follow from 2's paint", again)
        self.assertTrue(any(n.startswith("write-") for n in order))
        record = data["plan_review"]
        self.assertTrue(record["retried"])
        self.assertEqual(record["verdict"], "accept")
        self.assertEqual([r["verdict"] for r in record["rounds"]], ["revise", "accept"])

    def test_reject_stops_before_any_section_is_written(self):
        w = writer()
        calls = faked(w, [verdict("reject", [(0, "the train names a topic", "name the obstacle")])])
        with self.assertRaises(ClaudeError) as caught:
            w.run(review=True)
        self.assertIn("the beat sheet did not pass review", str(caught.exception))
        self.assertIn("the train names a topic", str(caught.exception))
        self.assertFalse([n for n in names(calls) if n.startswith("write-")])
        self.assertNotIn("plan-2", names(calls))

    def test_a_second_revise_stops_before_any_section_is_written(self):
        w = writer()
        calls = faked(w, [verdict("revise", [SWAPPABLE]), verdict("revise", [SWAPPABLE])])
        with self.assertRaises(ClaudeError) as caught:
            w.run(review=True)
        self.assertIn("the beat sheet did not pass review", str(caught.exception))
        self.assertEqual(names(calls), ["plan", "plan-review", "plan-2", "plan-review-2"])

    def test_report_only_does_not_replan_and_still_stops_on_reject(self):
        w = writer()
        calls = faked(w, [verdict("revise", [SWAPPABLE])])
        data = w.run(review=True, fix=False)
        self.assertNotIn("plan-2", names(calls))
        self.assertEqual(data["plan_review"]["verdict"], "revise")
        self.assertEqual(data["plan_review"]["problems"][0]["problem"],
                         "sections 2 and 3 could swap without loss")
        self.assertFalse(data["plan_review"]["retried"])
        self.assertTrue(any(n.startswith("write-") for n in names(calls)))

        w = writer()
        calls = faked(w, [verdict("reject", [(0, "the train names a topic", "name it")])])
        with self.assertRaises(ClaudeError):
            w.run(review=True, fix=False)
        self.assertFalse([n for n in names(calls) if n.startswith("write-")])

    def test_an_outline_plan_is_reviewed_after_its_plan_fields(self):
        outline = {"sections": [{"heading": s["heading"], "seconds": 20, "beats": s["beats"]}
                                for s in PLAN["sections"]]}
        fields = {k: v for k, v in PLAN.items() if k not in ("logline", "sections")}
        fields["sections"] = [{k: s[k] for k in ("heading", *script.SECTION_STORY) if k in s}
                              for s in PLAN["sections"]]
        w = writer(outline=outline)
        calls = faked(w, [verdict("revise", [SWAPPABLE]), verdict("accept")], fields=fields)
        w.run(review=True)
        order = names(calls)
        self.assertEqual(order[:4], ["plan-fields", "plan-review", "plan-fields-2",
                                     "plan-review-2"])
        self.assertIn("sections 2 and 3 could swap without loss", calls[2]["prompt"])


    def test_a_second_plans_drift_notes_replace_the_firsts(self):
        # The first plan's off-list audience is a note about a plan that was
        # sent back; script.json carries the notes of the plan it drafted.
        first = copy.deepcopy(PLAN)
        first["audience"] = "kids"
        w = writer()
        calls = faked(w, [verdict("revise", [SWAPPABLE]), verdict("accept")])
        plans = [first, PLAN]
        ask = w._ask

        def two_plans(name, prompt, schema):
            if kind(schema) == "plan":
                calls.append({"name": name, "prompt": prompt, "kind": "plan"})
                return copy.deepcopy(plans.pop(0))
            return ask(name, prompt, schema)
        w._ask = two_plans
        data = w.run(review=True)
        self.assertEqual(names(calls)[:2], ["plan", "plan-review"])
        self.assertFalse([f for f in data["findings"] if "audience" in f], data["findings"])


class SelfContradiction(unittest.TestCase):
    """A plan review whose fields contradict its verdict is asked once more
    under `<name>-again` with what contradicts; the verdict stays the
    model's (narrative.check_plan_review)."""

    def test_check_plan_review_names_each_contradiction(self):
        check = narrative.check_plan_review
        self.assertEqual(check(verdict("accept")), [])
        self.assertEqual(check(verdict("revise", [SWAPPABLE])), [])
        self.assertEqual(len(check(verdict("revise"))), 1)
        self.assertEqual(len(check(verdict("reject"))), 1)
        bad = dict(verdict("accept"), train_ok=False, reorder_survives=True,
                   payoff_concrete=False, sections_advancing_none=[2])
        lines = check(bad)
        self.assertEqual(len(lines), 4, lines)
        for needle in ("train_ok", "reorder_survives", "payoff_concrete", "section 2"):
            self.assertTrue(any(needle in line for line in lines), needle)
        self.assertEqual(check(None), [])

    def test_an_accept_that_says_the_order_is_a_list_is_asked_again(self):
        w = writer()
        listed = dict(verdict("accept"), reorder_survives=True)
        calls = faked(w, [listed, verdict("revise", [SWAPPABLE]), verdict("accept")])
        data = w.run(review=True)
        order = names(calls)
        self.assertEqual(order[:4], ["plan", "plan-review", "plan-review-again", "plan-2"])
        again = calls[2]["prompt"]
        self.assertIn("reorder_survives", again)
        self.assertTrue(data["plan_review"]["retried"])
        self.assertTrue(data["plan_review"]["rounds"][0]["again"])

    def test_a_revise_with_no_problem_is_asked_again_and_a_second_is_recorded(self):
        w = writer()
        calls = faked(w, [verdict("revise"), verdict("revise"), verdict("accept")])
        data = w.run(review=True)
        order = names(calls)
        self.assertEqual(order[:4], ["plan", "plan-review", "plan-review-again", "plan-2"])
        self.assertIn("the reviewer named no problem", calls[3]["prompt"])
        first = data["plan_review"]["rounds"][0]
        self.assertTrue(first["unsettled"])
        self.assertEqual(first["verdict"], "revise")
        md = "\n".join(script.plan_review_lines(
            dict(data["plan_review"], unsettled=first["unsettled"])))
        self.assertIn("- unsettled: a revise names the problems", md)

    def test_an_off_list_verdict_fails_the_stage_and_is_not_kept(self):
        # The harness asks once more and then lets an enum value through
        # (invariant 3); no verdict was given, so the stage stops.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        engine = harness.ENGINE
        harness.ENGINE = OffList
        self.addCleanup(setattr, harness, "ENGINE", engine)
        ByModel.seen, ByModel.fail_review = [], False
        w = script.Screenwriter(idea(), root=root, verbose=False, minutes=1, jobs=1,
                                model="sonnet", reviewer_model="opus")
        with self.assertRaises(ClaudeError) as caught:
            w.run(review=True)
        self.assertIn("'maybe'", str(caught.exception))
        self.assertFalse((root / "decisions" / "script" / "plan-review.json").exists())
        self.assertFalse([c for c in ByModel.seen if c["kind"] == "shots"])


# --------------------------------------------------------------------------- #
# what the reviewer is shown
# --------------------------------------------------------------------------- #

class Prompt(unittest.TestCase):
    def review_prompt(self, plan, minutes=1):
        w = script.Screenwriter(idea(), verbose=False, jobs=1, minutes=minutes, research="",
                                reviewer_model="opus")
        calls = faked(w, [verdict("accept")], plan=plan)
        w.run(review=True)
        return next(c["prompt"] for c in calls if c["name"] == "plan-review")

    def test_the_prompt_carries_the_plan_and_the_plan_rows_of_the_rubric(self):
        prompt = self.review_prompt(PLAN)
        self.assertIn(PLAN["train"], prompt)
        self.assertIn(PLAN["ending"]["payoff_line"], prompt)
        for key in ("train", "stakes", "open_loops", "reveal_setup", "ending"):
            self.assertIn(f". {key} [", prompt, key)
        for key in ("hook", "honesty"):
            self.assertNotIn(f". {key} [", prompt, key)
        self.assertIn("Attack", prompt)

    def test_a_clean_plan_carries_no_pre_check(self):
        prompt = self.review_prompt(PLAN)
        self.assertNotIn("- rule:", prompt)
        self.assertNotIn("- house:", prompt)

    def test_rule_pre_checks_reach_the_prompt(self):
        plan = copy.deepcopy(PLAN)
        plan["ending"]["payoff_line"] = ""
        plan["sections"][2]["connective"] = "and"
        plan["questions"].append({"id": "Q3", "text": "Who paid?", "opened_in": 2})
        plan["reveal"] = {"section": 2, "constraint_section": 3}
        prompt = self.review_prompt(plan)
        self.assertIn("- rule: the payoff line is empty", prompt)
        self.assertIn("- rule: section 3 joins the one before with AND", prompt)
        self.assertIn("- rule: question Q3", prompt)
        self.assertIn("- rule: the constraint (section 3) does not come before the "
                      "reveal (section 2)", prompt)

    def test_a_generic_payoff_line_is_a_rule_pre_check(self):
        plan = copy.deepcopy(PLAN)
        plan["ending"]["payoff_line"] = "And that is the whole story."
        prompt = self.review_prompt(plan)
        self.assertIn("- rule: the payoff line names nothing", prompt)

    def test_a_payoff_line_that_only_echoes_the_payoff_field_is_a_rule_pre_check(self):
        # The model writes the payoff line from the payoff field, so sharing
        # a word with it shows nothing; the train, the promise and the title
        # are what the line must name.
        plan = copy.deepcopy(PLAN)
        plan["payoff"] = "The rivet nobody inspected."
        plan["ending"]["payoff_line"] = "The rivet nobody inspected."
        prompt = self.review_prompt(plan)
        self.assertIn("- rule: the payoff line names nothing", prompt)

    def test_house_pre_checks_for_a_long_film_without_a_rehook_and_a_train_without_a_question(self):
        plan = copy.deepcopy(PLAN)
        for section in plan["sections"]:
            section["rehook"] = ""
        plan["train"] = "Engineers study one bolt hidden by paint on the bridge."
        prompt = self.review_prompt(plan, minutes=8)
        self.assertIn("- house: a film over seven minutes with no rehook", prompt)
        self.assertIn("- house: the train asks no question", prompt)
        # a short film is not held to the re-hook
        self.assertNotIn("seven minutes", self.review_prompt(plan, minutes=1))

    def test_two_middle_sections_with_the_same_escalation_reach_the_prompt(self):
        # AUTO-136: flat stakes reach the plan reviewer as a rule: line
        plan = copy.deepcopy(PLAN)
        plan["sections"].insert(2, dict(copy.deepcopy(plan["sections"][1]),
                                        heading="More paint", rehook="Q2",
                                        escalation="  The twin opens SOON "))
        plan["sections"][3]["rehook"] = "Q1"
        prompt = self.review_prompt(plan)
        self.assertIn("- rule: middle sections 2 and 3 state the same escalation", prompt)

    def test_the_pre_checks_never_stop_the_stage(self):
        plan = copy.deepcopy(PLAN)
        plan["ending"]["payoff_line"] = ""
        plan["sections"][2]["connective"] = "and"
        w = writer()
        calls = faked(w, [verdict("accept")], plan=plan)
        data = w.run(review=True)
        self.assertTrue(any(n.startswith("write-") for n in names(calls)))
        self.assertTrue(any(p.startswith("rule:") for p in data["plan_review"]["prechecks"]))


# --------------------------------------------------------------------------- #
# the opening and the tension, measured from the plan (AUTO-136)
# --------------------------------------------------------------------------- #

NARRATIVE = Path(__file__).resolve().parent / "fixtures" / "narrative"


def timed(rehooks, seconds=60, escalations=None):
    """A plan of len(rehooks) sections of `seconds` each, the ledger's Q1
    open from section 1 to the last; each middle section escalates on its
    own clause unless `escalations` says otherwise."""
    count = len(rehooks)
    plan = copy.deepcopy(PLAN)
    plan["questions"] = [{"id": "Q1", "text": "Why?", "opened_in": 1, "closed_in": count}]
    plan["reveal"] = ({"section": count, "constraint_section": 1} if count > 1
                      else {"section": 0, "constraint_section": 0})
    plan["sections"] = [
        {"heading": f"S{n}", "purpose": "p", "seconds": seconds, "beats": ["b"],
         "opens": ["Q1"] if n == 1 else [], "closes": ["Q1"] if n == count else [],
         "connective": "open" if n == 1 else "but", "rehook": rehook,
         "escalation": (escalations[n - 1] if escalations else
                        "" if n in (1, count) else f"the cost rises to level {n}"),
         "anchor_visual": "a"}
        for n, rehook in enumerate(rehooks, 1)]
    return plan


def checks(plan, seconds=None):
    total = sum(s["seconds"] for s in plan["sections"]) if seconds is None else seconds
    return script.plan_prechecks(plan, total)


class OpeningAndTension(unittest.TestCase):
    def test_a_clean_plan_has_no_pre_check(self):
        self.assertEqual(checks(PLAN), [])
        self.assertEqual(checks(timed(["", "Q1", "Q1"])), [])
        self.assertEqual(script.plan_prechecks(script.read_json(NARRATIVE / "good" / "plan.json"),
                                               130), [])

    def test_an_empty_middle_escalation_is_a_rule_line_and_the_ends_are_exempt(self):
        plan = timed(["", "Q1", "Q1", "Q1"])
        plan["sections"][1]["escalation"] = " "
        found = checks(plan)
        self.assertEqual([c for c in found if "escalation" in c],
                         ["rule: middle section 2 has an empty escalation: each middle "
                          "section puts more at risk than the one before (attention 9)"])
        plan = timed(["", "Q1", "Q1", "Q1"])
        plan["sections"][0]["escalation"] = plan["sections"][3]["escalation"] = ""
        self.assertEqual(checks(plan), [])

    def test_equal_middle_escalations_are_flat_stakes_and_the_last_is_exempt(self):
        plan = timed(["", "Q1", "Q1", "Q1", "Q1"])
        plan["sections"][3]["escalation"] = "The cost  rises to LEVEL 2"
        found = [c for c in checks(plan) if "same escalation" in c]
        self.assertEqual(found, ["rule: middle sections 2 and 4 state the same escalation: "
                                 "the stakes stay flat (attention 9)"])
        plan = timed(["", "Q1", "Q1", "Q1"])
        plan["sections"][3]["escalation"] = plan["sections"][2]["escalation"]
        self.assertEqual(checks(plan), [])

    def test_a_rehook_must_name_a_question_still_open(self):
        plan = timed(["", "Q1", "Q9"])
        self.assertIn("rule: section 3's rehook names Q9, which is not in the questions "
                      "ledger: there is nothing to re-open (structure 23, structure 8)",
                      checks(plan))
        plan = timed(["", "Q1", "Q1", "Q2"])
        plan["questions"].append({"id": "Q2", "text": "How?", "opened_in": 1, "closed_in": 2})
        self.assertIn("rule: section 4's rehook names Q2, which was closed in section 2, "
                      "before it: there is nothing left to re-open (structure 23, "
                      "structure 8)", checks(plan))
        # closed in the same section is re-opened and answered there; left
        # open (closed_in 0) is still open
        plan["questions"][1]["closed_in"] = 4
        self.assertEqual(checks(plan), [])
        plan["questions"][1].update(closed_in=0, left_open_why="the inquiry is open")
        self.assertEqual(checks(plan), [])

    def test_section_2_with_no_rehook_is_a_house_line(self):
        found = checks(timed(["", "", "Q1"]))
        self.assertIn("house: section 2 has no rehook, at the boundary where the opening "
                      "hands over (structure 23)", found)
        self.assertEqual(checks(timed([""])), [])

    def test_a_rehook_gap_over_three_minutes_is_a_house_line(self):
        self.assertEqual(script.REHOOK_EVERY_SECONDS, 180)
        found = checks(timed(["", "Q1", "", "", ""]))
        self.assertIn("house: 240 s pass without a rehook, from section 2 to the end of "
                      "the film: plan one at least every 180 s (structure 23)", found)
        found = checks(timed(["", "", "", "", "Q1"]))
        self.assertIn("house: 240 s pass without a rehook, from section 1 to section 5: "
                      "plan one at least every 180 s (structure 23)", found)
        self.assertFalse([c for c in checks(timed(["", "Q1", "", ""]))
                          if "without a rehook" in c])

    def test_a_long_film_with_no_rehook_gets_the_seven_minute_line_alone(self):
        found = checks(timed(["", "", "", ""], seconds=120))
        self.assertTrue([c for c in found if "over seven minutes" in c], found)
        self.assertFalse([c for c in found if "without a rehook" in c], found)
        # the seven-minute line already asks for one where the opening hands
        # over, so the section-2 line would be a second line for one fault
        self.assertFalse([c for c in found if "section 2 has no rehook" in c], found)

    def test_an_empty_hook_gap_is_a_rule_line(self):
        plan = copy.deepcopy(PLAN)
        plan["hook"]["gap"] = "  "
        self.assertEqual(checks(plan), ["rule: hook.gap is empty: the opening names the "
                                        "specific gap it opens (attention 1)"])

    def test_a_plan_with_no_hook_object_names_no_gap_either(self):
        # a plan from before AUTO-136, kept in an older script.json, has no
        # hook at all: it names no gap, so it gets the same rule: line
        for hook in (None, "a question"):
            plan = copy.deepcopy(PLAN)
            if hook is None:
                del plan["hook"]
            else:
                plan["hook"] = hook
            with self.subTest(hook=hook):
                self.assertEqual(checks(plan), ["rule: hook.gap is empty: the opening "
                                                "names the specific gap it opens "
                                                "(attention 1)"])

    def test_each_seeded_plan_carries_only_the_lines_of_its_own_fault(self):
        good = script.plan_prechecks(script.read_json(NARRATIVE / "good" / "plan.json"), 130)
        for path in sorted(NARRATIVE.iterdir()):
            plan = script.read_json(path / "plan.json")
            title = (script.read_json(path / "script.json") or {}).get("idea", {}).get(
                "title", "") if (path / "script.json").is_file() else ""
            found = script.plan_prechecks(plan, 130, title)
            new = [c for c in found if any(w in c for w in (
                "escalation", "rehook", "hook.gap"))]
            with self.subTest(case=path.name):
                if path.name in ("and-chain", "list-plan"):
                    self.assertEqual(new, [
                        "rule: middle section 2 has an empty escalation: each middle "
                        "section puts more at risk than the one before (attention 9)",
                        "rule: middle section 3 has an empty escalation: each middle "
                        "section puts more at risk than the one before (attention 9)"])
                elif path.name == "flat-tension":
                    self.assertEqual(new, ["rule: middle sections 2 and 3 state the same "
                                           "escalation: the stakes stay flat (attention 9)"])
                else:
                    self.assertEqual(new, [], path.name)
        self.assertEqual(good, [])


# --------------------------------------------------------------------------- #
# the reviewer harness
# --------------------------------------------------------------------------- #

class ByModel(FakeEngine):
    """Answers by the schema and records each call's model and cap."""
    seen: list = []
    fail_review = False

    def ask_json(self, prompt, schema, system=None):
        which = kind(schema)
        ByModel.seen.append({"kind": which, "model": self.config.model,
                             "cap": self.config.max_turns_budget_usd, "system": system})
        if which == "plan-review":
            if ByModel.fail_review:
                raise ClaudeError("the reviewer timed out")
            return verdict("accept")
        if which == "plan":
            return copy.deepcopy(PLAN)
        if which == "shots":
            return shots_for(1, ("context", "payoff"))
        return blank(schema)


class OffList(ByModel):
    """A reviewer whose verdict is never on the list, even when asked again."""

    def ask_json(self, prompt, schema, system=None):
        if kind(schema) == "plan-review":
            return dict(verdict("accept"), verdict="maybe")
        return super().ask_json(prompt, schema, system)


class Reviewer(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._engine = harness.ENGINE
        harness.ENGINE = ByModel
        self.addCleanup(setattr, harness, "ENGINE", self._engine)
        ByModel.seen, ByModel.fail_review = [], False

    def writer(self):
        return script.Screenwriter(idea(), root=self.root, verbose=False, minutes=1, jobs=1,
                                   model="sonnet", reviewer_model="opus",
                                   review_budget_usd=1.5)

    def test_the_review_is_asked_of_the_reviewer_under_its_own_cap(self):
        data = self.writer().run(review=True)
        review = [c for c in ByModel.seen if c["kind"] == "plan-review"]
        self.assertEqual([(c["model"], c["cap"]) for c in review], [("opus", 1.5)])
        # The plan's role, not the script review's: its verdicts are accept,
        # revise and reject, and nothing in it asks for a synopsis or grades.
        self.assertIn(narrative.reviewer_role(plan=True), review[0]["system"])
        self.assertNotIn("recommend", review[0]["system"])
        # The same reviewer reads the drafted pictures (AUTO-98), under its cap.
        pictures = [c for c in ByModel.seen if c["kind"] == "picture-check"]
        self.assertTrue(pictures)
        self.assertEqual({(c["model"], c["cap"]) for c in pictures}, {("opus", 1.5)})
        self.assertIn(narrative.picture_role(), pictures[0]["system"])
        # Everything the author writes is the author's model; the story
        # review that follows the draft (AUTO-93) is the reviewer's too.
        others = {c["model"] for c in ByModel.seen
                  if c["kind"] in ("plan", "plan-fields", "shots", "edits", "fixes")}
        self.assertEqual(others, {"sonnet"})
        kept = json.loads((self.root / "decisions" / "script" / "plan-review.json").read_text())
        self.assertEqual(kept["model"], "opus")
        rows = {d["name"]: d for d in data["engine"]["decisions"]}
        self.assertEqual(rows["plan-review"]["model"], "opus")
        self.assertEqual(rows["plan"]["model"], "sonnet")

    def test_a_failed_review_is_an_error_and_a_billed_failed_row(self):
        ByModel.fail_review = True
        w = self.writer()
        with self.assertRaises(ClaudeError):
            w.run(review=True)
        rows = [d for d in w.harness.report()["decisions"] if d["name"] == "plan-review"]
        self.assertEqual([r["kind"] for r in rows], ["failed"])
        self.assertGreater(rows[0]["cost_usd"], 0)
        self.assertFalse([c for c in ByModel.seen if c["kind"] == "shots"])


class PlanRole(unittest.TestCase):
    def test_the_plan_role_replaces_the_script_reviews_verdict_rules(self):
        role = narrative.reviewer_role(plan=True)
        for word in ("accept", "revise", "reject", "plan"):
            self.assertIn(word, role)
        for word in ("recommend", "consider", "synopsis", "first minute"):
            self.assertNotIn(word, role)
        # stakes is a problem to name, never on its own grounds to send it back
        self.assertIn("stakes", role)
        self.assertNotEqual(role, narrative.reviewer_role())


class Rounds(unittest.TestCase):
    """`--rounds` is the fact-check's revision rounds (`--verify`)."""

    def revisions(self, rounds):
        w = script.Screenwriter(idea(), verbose=False, jobs=1, minutes=1, research="R",
                                rounds=rounds)
        asked = []

        def ask(name, prompt, schema):
            which = kind(schema)
            asked.append(name)
            if which == "plan":
                return copy.deepcopy(PLAN)
            if which == "shots":
                return shots_for(int(name.split("-")[1]), ("context", "payoff"))
            if which == "flags":
                return {"flags": [{"n": 1, "severity": "important", "problem": "p",
                                   "fix": "f"}], "verdict": "no"}
            if which == "fixes":
                return {"fixes": [{"n": 1, "action": "fix", "narration": f"line {name}",
                                   "why": "w"}]}
            return {}
        w._ask = ask
        w.run(do_verify=True)
        return [n for n in asked if n.startswith("revise-") and n[7:].isdigit()]

    def test_the_rounds_bound_the_fact_checks_revisions(self):
        self.assertEqual(self.revisions(0), [])
        self.assertEqual(self.revisions(1), ["revise-1"])
        self.assertEqual(self.revisions(3), ["revise-1", "revise-2", "revise-3"])


# --------------------------------------------------------------------------- #
# the command
# --------------------------------------------------------------------------- #

class Command(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.film = self.root / "a-film"
        self.film.mkdir()
        (self.root / "work").mkdir()
        (self.film / "idea.json").write_text(
            '{"title": "Why the bridge fell", "genre": "g", "description": "d"}')

    def run_stage(self, *flags, verdicts=()):
        queue = list(verdicts)
        asked = []

        def ask(self_, name, prompt, schema):
            which = kind(schema)
            asked.append(name)
            if which == "plan-review":
                return copy.deepcopy(queue.pop(0))
            if which == "plan":
                return copy.deepcopy(PLAN)
            if which == "shots":
                return shots_for(int(name.split("-")[1]), ("context", "payoff"))
            if which == "fixes":
                return {"fixes": []}
            return {}
        args = cli.build_parser().parse_args(
            ["script", "--project", str(self.film), "--work", str(self.root / "work"),
             "--quiet", *flags])
        with mock.patch.object(script.Screenwriter, "_ask", ask), \
                contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = script.run(args)
        return code, asked, out.getvalue() + err.getvalue()

    def test_the_flags_parse_with_their_defaults(self):
        ns = cli.build_parser().parse_args(["script"])
        self.assertFalse(ns.review)
        self.assertEqual(ns.reviewer_model, "opus")
        self.assertEqual(ns.review_budget_usd, 2.0)
        self.assertEqual(ns.rounds, 2)

    def test_the_authors_own_family_is_refused_before_any_decision(self):
        for argv in (("--model", "opus"), ("--model", "claude-opus-5-5"),
                     ("--model", "sonnet", "--reviewer-model", "sonnet")):
            code, asked, said = self.run_stage("--review", *argv)
            self.assertEqual(code, 2, argv)
            self.assertEqual(asked, [])
            self.assertIn("does not grade its own work", said)
        self.assertFalse((self.film / "decisions").exists())

    def test_without_review_the_authors_family_is_not_refused(self):
        code, asked, _ = self.run_stage("--model", "opus")
        self.assertIn("plan", asked)
        self.assertNotIn("plan-review", asked)

    def test_the_review_is_recorded_in_script_json_and_script_md(self):
        code, asked, _ = self.run_stage(
            "--review", "--model", "sonnet",
            verdicts=[verdict("revise", [SWAPPABLE]), verdict("accept")])
        record = json.loads((self.film / "script.json").read_text())
        self.assertEqual(record["plan_review"]["verdict"], "accept")
        self.assertTrue(record["plan_review"]["retried"])
        md = (self.film / "script.md").read_text()
        self.assertIn("## Plan review", md)
        self.assertIn("sections 2 and 3 could swap without loss", md)

    def test_a_reject_exits_1_with_the_reason_and_drafts_nothing(self):
        for extra in ((), ("--report-only",)):
            code, asked, said = self.run_stage(
                "--review", "--model", "sonnet", *extra,
                verdicts=[verdict("reject", [(0, "the train names a topic", "name it")])])
            self.assertEqual(code, 1, extra)
            self.assertIn("the beat sheet did not pass review", said)
            self.assertIn("the train names a topic", said)
            self.assertFalse([n for n in asked if n.startswith("write-")], extra)
            self.assertFalse((self.film / "script.json").exists())
            self.assertFalse((self.film / "narration.txt").exists())
            # The stop leaves its reading and its spend where `yt run` does
            # not take the stage as done.
            stopped = json.loads((self.film / script.PLAN_REVIEW_FILE).read_text())
            self.assertEqual(stopped["plan_review"]["verdict"], "reject", extra)
            self.assertEqual(stopped["plan_review"]["problems"][0]["problem"],
                             "the train names a topic")
            # (the `_ask` fake books nothing; Reviewer pins the rows)
            self.assertIn("decisions", stopped["engine"])
            self.assertIn("cost_usd", stopped["usage"])

    def test_a_passed_review_clears_the_record_of_an_earlier_stop(self):
        self.run_stage("--review", "--model", "sonnet",
                       verdicts=[verdict("reject", [(0, "the train names a topic", "x")])])
        self.assertTrue((self.film / script.PLAN_REVIEW_FILE).exists())
        code, _, said = self.run_stage("--review", "--model", "sonnet",
                                       verdicts=[verdict("accept")])
        self.assertEqual(code, 0, said)
        self.assertFalse((self.film / script.PLAN_REVIEW_FILE).exists())
        self.assertIn("plan review: accept", said)

    def test_rounds_reach_the_screenwriter(self):
        seen = {}

        def run(self_, **kw):
            seen["rounds"] = self_.rounds
            raise ClaudeError("stop here")
        with mock.patch.object(script.Screenwriter, "run", run):
            self.run_stage("--verify", "--rounds", "3")
        self.assertEqual(seen["rounds"], 3)


if __name__ == "__main__":
    unittest.main()
