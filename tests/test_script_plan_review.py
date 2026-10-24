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
from tests.test_script_plan import (ANALOGY, FRAMED, PAPER_CLAIMS, PAPER_DIGEST,  # noqa: E402
                                    PAPER_URL, PLAN, PREDICTION, framing, idea, shots_for,
                                    with_devices)


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


def verdict(word, problems=(), stakes=True, form_fits=True, form_problem=""):
    return {"verdict": word, "train_ok": word != "reject", "reorder_survives": word == "reject",
            "sections_advancing_none": [], "payoff_concrete": word != "reject",
            "stakes_concrete": stakes, "questions_unanswered": [],
            "form_fits": form_fits, "form_problem": form_problem,
            "problems": [{"section": s, "problem": p, "fix": f} for s, p, f in problems]}


#: A plan that explains before it gives a reason to care (AUTO-161).
NO_STAKES = (1, "section 1 explains the bolts before the viewer has any reason to care",
             "open on the 1989 Quebec blackout")

#: What review_field_lines says of a plan with no stakes.
STAKES_LINE = ("the whole plan: no concrete consequence the viewer can picture comes "
               "before the mechanism (or a declared twist)")


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


class Stakes(unittest.TestCase):
    """AUTO-161: no reason to care before the mechanism is a revise ground.
    It takes gate_plan's existing path (plan-2, plan-review-2, a stop), and
    the verdict stays the reviewer's."""

    def test_an_accept_without_stakes_is_asked_again(self):
        w = writer()
        calls = faked(w, [verdict("accept", stakes=False),
                          verdict("revise", [NO_STAKES], stakes=False), verdict("accept")])
        data = w.run(review=True)
        order = names(calls)
        self.assertEqual(order[:4], ["plan", "plan-review", "plan-review-again", "plan-2"])
        again = calls[2]["prompt"]
        self.assertIn("an accept gives a reason to care before the mechanism", again)
        self.assertIn("stakes_concrete is false", again)
        # the second answer stands: it sent the plan back
        first = data["plan_review"]["rounds"][0]
        self.assertEqual((first["verdict"], first["again"], first["unsettled"]),
                         ("revise", True, []))

    def test_an_accept_still_without_stakes_is_kept_unsettled(self):
        w = writer()
        faked(w, [verdict("accept", stakes=False), verdict("accept", stakes=False)])
        data = w.run(review=True)
        record = data["plan_review"]
        self.assertEqual(record["verdict"], "accept")
        self.assertEqual(len(record["unsettled"]), 1, record["unsettled"])
        self.assertIn("stakes_concrete", record["unsettled"][0])

    def test_a_reject_on_stakes_alone_is_asked_again_and_kept_unsettled(self):
        alone = dict(verdict("reject", [NO_STAKES], stakes=False), train_ok=True,
                     reorder_survives=False, payoff_concrete=True)
        w = writer()
        calls = faked(w, [alone, alone])
        with self.assertRaises(ClaudeError):
            w.run(review=True)
        self.assertEqual(names(calls), ["plan", "plan-review", "plan-review-again"])
        self.assertIn("stakes alone is grounds to revise", calls[2]["prompt"])
        stop = w.plan_review_stop
        self.assertEqual(stop["verdict"], "reject")
        self.assertTrue([u for u in stop["unsettled"] if "stakes alone" in u], stop)

    def test_a_revise_on_stakes_replans_with_the_fix(self):
        w = writer()
        calls = faked(w, [verdict("revise", [NO_STAKES], stakes=False), verdict("accept")])
        data = w.run(review=True)
        order = names(calls)
        self.assertEqual(order[:4], ["plan", "plan-review", "plan-2", "plan-review-2"])
        again = calls[2]["prompt"]
        self.assertIn("open on the 1989 Quebec blackout", again)
        self.assertIn(STAKES_LINE, again)
        self.assertTrue(any(n.startswith("write-") for n in order))
        record = data["plan_review"]
        self.assertEqual([r["verdict"] for r in record["rounds"]], ["revise", "accept"])
        self.assertTrue(record["retried"])

    def test_the_stakes_line_reaches_the_second_plan_with_a_thin_problem_list(self):
        w = writer()
        calls = faked(w, [verdict("revise", [(0, "thin", "fix it")], stakes=False),
                          verdict("accept")])
        w.run(review=True)
        self.assertIn(STAKES_LINE, calls[2]["prompt"])
        self.assertEqual(script.review_field_lines(verdict("accept")), [])
        old = verdict("accept")
        del old["stakes_concrete"]
        self.assertEqual(script.review_field_lines(old), [])

    def test_a_second_revise_on_stakes_stops_with_the_record(self):
        w = writer()
        calls = faked(w, [verdict("revise", [NO_STAKES], stakes=False),
                          verdict("revise", [NO_STAKES], stakes=False)])
        with self.assertRaises(ClaudeError) as caught:
            w.run(review=True)
        self.assertEqual(names(calls), ["plan", "plan-review", "plan-2", "plan-review-2"])
        self.assertFalse([n for n in names(calls) if n.startswith("write-")])
        self.assertIn(STAKES_LINE, str(caught.exception))
        self.assertIn("after planning once more", str(caught.exception))
        stop = w.plan_review_stop
        self.assertEqual([r["verdict"] for r in stop["rounds"]], ["revise", "revise"])
        self.assertEqual(stop["verdict"], "revise")

    def test_each_round_and_the_record_keep_their_field_lines(self):
        # The stop's record says why the plan stopped even when the problems
        # list is thin: the field lines go to plan_review.json, not only to
        # stderr.
        thin = verdict("revise", [(0, "thin", "fix it")], stakes=False)
        w = writer()
        faked(w, [thin, thin])
        with self.assertRaises(ClaudeError):
            w.run(review=True)
        stop = w.plan_review_stop
        self.assertEqual([r["field_lines"] for r in stop["rounds"]],
                         [[STAKES_LINE], [STAKES_LINE]])
        self.assertEqual(stop["field_lines"], [STAKES_LINE])
        md = "\n".join(script.plan_review_lines(stop))
        self.assertIn(STAKES_LINE, md)
        w = writer()
        faked(w, [verdict("accept")])
        record = w.run(review=True)["plan_review"]
        self.assertEqual((record["field_lines"], record["rounds"][0]["field_lines"]), ([], []))

    def test_a_revise_whose_fields_name_the_stakes_is_not_asked_again(self):
        # The fields already tell plan-2 what to fix, so an empty problems
        # list is no reason for a second call.
        w = writer()
        calls = faked(w, [verdict("revise", stakes=False), verdict("accept")])
        w.run(review=True)
        self.assertEqual(names(calls)[:3], ["plan", "plan-review", "plan-2"])
        self.assertIn(STAKES_LINE, calls[2]["prompt"])
        self.assertNotIn("the reviewer named no problem", calls[2]["prompt"])

    def test_report_only_drafts_on_after_a_stakes_revise(self):
        w = writer()
        calls = faked(w, [verdict("revise", [NO_STAKES], stakes=False)])
        data = w.run(review=True, fix=False)
        self.assertNotIn("plan-2", names(calls))
        self.assertEqual(names(calls).count("plan-review"), 1)
        self.assertNotIn("plan-review-again", names(calls))
        self.assertEqual(data["plan_review"]["verdict"], "revise")
        self.assertTrue(any(n.startswith("write-") for n in names(calls)))

    def test_without_review_nothing_reads_the_stakes(self):
        w = writer(reviewer_model=None)
        calls = faked(w, [])
        data = w.run()
        self.assertFalse([n for n in names(calls) if n.startswith("plan-review")])
        self.assertNotIn("plan_review", data)

    def test_a_paper_plan_names_framing_as_where_stakes_are_read(self):
        def prompt_for(plan):
            w = writer()
            calls = faked(w, [verdict("accept")], plan=plan)
            w.run(review=True)
            return next(c["prompt"] for c in calls if c["name"] == "plan-review")
        framed_prompt = prompt_for(FRAMED)
        self.assertIn(script.PAPER_STAKES_NOTE, framed_prompt)
        self.assertIn("framing's significance, implications and consequences",
                      script.PAPER_STAKES_NOTE)
        # the note follows the attack
        self.assertLess(framed_prompt.index(script.PLAN_ATTACK),
                        framed_prompt.index(script.PAPER_STAKES_NOTE))
        plain = prompt_for(PLAN)
        self.assertNotIn(script.PAPER_STAKES_NOTE, plain)
        self.assertNotIn("framing", plain)

    def test_the_docs_name_stakes_as_a_revise_ground_only(self):
        root = Path(__file__).resolve().parent.parent
        said = ("no concrete consequence the viewer can picture before its mechanism, "
                "and no declared twist, is grounds to revise, never on its own to reject")
        for doc in (root / "README.md", root / "ytstudio" / "SKILL.md"):
            with self.subTest(doc=doc.name):
                flat = " ".join(doc.read_text(encoding="utf-8").split())
                self.assertIn(said, flat)
                self.assertIn("`stakes_concrete`", flat)

    def test_the_plan_and_the_story_review_are_untouched(self):
        # AUTO-161 changes the plan review's reading only: the plan's schema,
        # STORY_BRIEF, the plan prompt and the story review's role and schema
        # hash as they did on develop before it, so their decisions stay cached
        # (the plan schema's value is AUTO-137's, which added ending.device).
        # AUTO-157 changed the plan schema, STORY_BRIEF and the plan prompt on
        # purpose (the plan's `form`); the story review's role and schema are
        # still develop's.
        import hashlib

        def digest(text):
            return hashlib.sha256(text.encode()).hexdigest()
        self.assertEqual(digest(json.dumps(script.PLAN_SCHEMA, sort_keys=True)),
                         "b8059726e85dd2e753e08a5ff504acab9dca4e15383f8b51a4b4853821f70109")
        self.assertEqual(digest(script.STORY_BRIEF),
                         "9653fea2a559647af4ec355399ffeed22dd998f43ba9a8addb860c743b972290")
        self.assertEqual(digest(json.dumps(narrative.REVIEW_SCHEMA, sort_keys=True)),
                         "7814e025c70fc932451f27deb712c9d7f6296dcb3d09004f69403bd96f7a5649")
        self.assertEqual(digest("".join(narrative.reviewer_role(seed=s) for s in range(4))),
                         "ece140eaea46c14e8984a8ef8b71d032db39b968065e0338881d55049a0ad58f")
        w = writer()
        calls = faked(w, [verdict("revise", [(2, "p", "f")]), verdict("accept")])
        w.run(review=True)
        plan_prompt = next(c["prompt"] for c in calls if c["name"] == "plan")
        self.assertEqual(digest(plan_prompt),
                         "f735c4f9250e90e169340553ea9639411f3c3c4718c81441c2741b8ec70f4370")


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

    def test_a_revise_names_a_problem_through_a_ground_field(self):
        check = narrative.check_plan_review
        for field, value in (("train_ok", False), ("reorder_survives", True),
                             ("payoff_concrete", False), ("stakes_concrete", False),
                             ("sections_advancing_none", [2])):
            self.assertEqual(check(dict(verdict("revise"), **{field: value})), [], field)
        # an open question is no ground to revise, so it names no problem
        self.assertEqual(len(check(dict(verdict("revise"), questions_unanswered=["q1"]))), 1)
        # a reject still names its problems: the operator's --notes need them
        self.assertEqual(len(check(dict(verdict("reject"), stakes_concrete=False))), 1)

    def test_a_reject_on_stakes_is_alone_only_when_its_four_grounds_are_clean(self):
        check = narrative.check_plan_review
        clean = dict(verdict("reject", [NO_STAKES], stakes=False), train_ok=True,
                     reorder_survives=False, payoff_concrete=True)
        self.assertEqual(len(check(clean)), 1)
        # A field the review leaves out is no contradiction: an old or
        # malformed answer that omits a reject ground is not "stakes alone".
        for field in ("train_ok", "reorder_survives", "payoff_concrete",
                      "sections_advancing_none"):
            partial = dict(clean)
            del partial[field]
            self.assertEqual(check(partial), [], field)

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
        for key in ("train", "stakes", "open_loops", "reveal_setup", "ending", "devices"):
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

    def test_a_rehook_matches_the_ledger_whatever_its_case(self):
        # the writer may say q1 where the ledger holds Q1: the same question
        plan = timed(["", "q1", " Q1 "])
        self.assertEqual(checks(plan), [])
        plan["questions"][0]["id"] = "q1"
        plan["sections"][2]["rehook"] = "Q1"
        self.assertEqual(checks(plan), [])
        plan = timed(["", "Q1", "Q1", "q2"])
        plan["questions"].append({"id": "Q2", "text": "How?", "opened_in": 1, "closed_in": 2})
        self.assertIn("rule: section 4's rehook names q2, which was closed in section 2, "
                      "before it: there is nothing left to re-open (structure 23, "
                      "structure 8)", checks(plan))

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

    def test_a_long_one_section_plan_is_asked_for_more_sections(self):
        # A section's rehook is read at its start, so no rehook can break a
        # one-section film's gap: the line asks for the section it lacks.
        for rehook in ("", "Q1"):
            with self.subTest(rehook=rehook):
                found = checks(timed([rehook], seconds=240))
                self.assertEqual(found, ["house: a one-section plan runs 240 s with no "
                                         "rehook after its opening: split it so a later "
                                         "section re-opens a question at least every "
                                         "180 s (structure 23)"])
        self.assertEqual(checks(timed([""], seconds=180)), [])

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
# the narrator's temperament, as the plan names it (AUTO-158)
# --------------------------------------------------------------------------- #

class Persona(unittest.TestCase):
    """A persona that names no temperament or no reason is a house prior for
    the reviewer to weigh; a plan with none has no line, so the pre-checks
    of older plans and the calibration fixtures do not move."""

    LINE = ("house: the persona names no temperament or no reason: the narrator's "
            "temperament is chosen from the subject and said why")

    def persona_lines(self, plan):
        return [c for c in checks(plan, 60) if "persona" in c]

    def test_an_empty_persona_is_a_house_pre_check(self):
        for persona in ({"temperament": "", "why": "a story of loss"},
                        {"temperament": "tender", "why": "  \n "},
                        {"temperament": " "}):
            with self.subTest(persona=persona):
                plan = dict(copy.deepcopy(PLAN), persona=persona)
                self.assertEqual(self.persona_lines(plan), [self.LINE])

    def test_a_plan_with_no_persona_or_a_full_one_has_no_line(self):
        self.assertEqual(self.persona_lines(PLAN), [])
        full = dict(copy.deepcopy(PLAN), persona={"temperament": "tender",
                                                  "why": "a story of loss"})
        self.assertEqual(checks(full), checks(PLAN))

    def test_the_line_reaches_the_plan_reviewer(self):
        plan = dict(copy.deepcopy(PLAN), persona={"temperament": "", "why": ""})
        prompt = Prompt.review_prompt(self, plan)
        self.assertIn("- " + self.LINE, prompt)
        self.assertIn('"persona"', prompt)

    def test_the_story_review_reads_the_persona_through_the_plan(self):
        # no rubric or role change: the story reviewer sees it in the plan
        plan = dict(copy.deepcopy(PLAN), persona={"temperament": "tender",
                                                  "why": "a story of loss"})
        w = writer()
        self.assertIn('"temperament": "tender"', w._story_prompt([], plan))
        self.assertNotIn("persona", w._story_prompt([], PLAN))


# --------------------------------------------------------------------------- #
# the film's form, chosen for its subject (AUTO-157)
# --------------------------------------------------------------------------- #

NARRATIVE = Path(__file__).resolve().parent / "fixtures" / "narrative"

#: What a reviewer says of a form that does not follow from the subject.
MISFIT = "a mystery any subject could wear; nothing in the bridge is kept back"
FORM_LINE = "the whole plan: the form does not follow from the subject: " + MISFIT


def comet(why=None, **form):
    """The calibration set's good comet-tail plan, with its form changed."""
    plan = json.loads((NARRATIVE / "good" / "plan.json").read_text(encoding="utf-8"))
    if why is not None:
        plan["form"]["why_this_subject"] = why
    plan["form"].update(form)
    return plan


def form_lines(plan, title="Why a comet flies tail first", **kw):
    return [c for c in script.plan_prechecks(plan, 130, title, **kw)
            if "form" in c and not c.startswith("channel:")]


def form_only(word="revise"):
    """A reading whose fields find only the form wrong."""
    return dict(verdict(word, [(0, MISFIT, "follow the inspection's evidence")]),
                train_ok=True, reorder_survives=False, payoff_concrete=True,
                form_fits=False, form_problem=MISFIT)


class Form(unittest.TestCase):
    """The film's form follows from its subject: a generic reason is a
    house: pre-check, the reviewer of another family reads a form row, and
    a form that does not fit is a ground to revise only, so a second
    reading that objects to the form alone drafts on with an advisory."""

    GENERIC = "house: the form's reason names only the form, so any topic could wear it"

    def test_the_good_comet_plan_has_no_form_line(self):
        self.assertEqual(form_lines(comet()), [])

    def test_a_generic_form_reason_is_a_house_pre_check(self):
        for why in ("mystery", "it is a mystery story", "mysteries are engaging"):
            with self.subTest(why=why):
                lines = form_lines(comet(why, name="mystery"))
                self.assertEqual(lines, [f"{self.GENERIC} (\"{why}\")"])
                self.assertTrue(lines[0].startswith("house:"))
        self.assertEqual(form_lines(comet("nobody knows why comet tails outrun sunlight",
                                          name="mystery")), [])

    def test_an_other_form_with_no_name_and_an_empty_reason_are_flagged(self):
        self.assertEqual(form_lines(comet("  ")),
                         ["house: the form has no why_this_subject (structure 20)"])
        self.assertIn("house: the form is other and names none",
                      form_lines(comet(name="other", other="")))
        self.assertEqual(form_lines(comet(name="other", other="a heist")), [])
        # the name given under other is a form word, not a reason
        self.assertEqual(form_lines(comet("a heist", name="other", other="a heist")),
                         [f"{self.GENERIC} (\"a heist\")"])
        # an off-list name is read as no form, and an older plan as before
        self.assertEqual(form_lines(comet("", name="heist")), [])
        older = comet()
        del older["form"]
        self.assertEqual(form_lines(older), [])

    def test_the_house_fixes_the_form(self):
        from ytstudio import house
        default = house.load().for_stage("script")
        self.assertIn("- Form:", default)
        self.assertEqual(script.house_form(default), "")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "house.md").write_text(
                "## format\n- Form: countdown, every film.\n", encoding="utf-8")
            block = house.load(project_root=Path(tmp)).for_stage("script")
        self.assertEqual(script.house_form(block), "countdown")
        self.assertEqual(script.house_form("- Form: other"), "")
        self.assertEqual(script.house_form("- Form: heist"), "")
        line = "house: the channel's house fixes the form as countdown; this plan uses journey"
        self.assertIn(line, form_lines(comet(name="journey"), house_form="countdown"))
        self.assertFalse([c for c in form_lines(comet(name="countdown"), house_form="countdown")
                          if "fixes" in c])
        # through the stage: the plan prompt carries the house, the reviewer the line
        plan = copy.deepcopy(PLAN)
        plan["form"]["name"] = "journey"
        w = writer(house=block)
        calls = faked(w, [verdict("accept")], plan=plan)
        w.run(review=True)
        asked = {c["name"]: c["prompt"] for c in calls}
        self.assertIn("- Form: countdown", asked["plan"])
        self.assertIn("- " + line, asked["plan-review"])

    def test_the_plan_review_reads_the_form_row(self):
        prompt = Prompt.review_prompt(self, PLAN)
        self.assertIn(". form [practitioner]", prompt)
        for level in narrative.criterion("form").levels.values():
            self.assertIn(level, prompt)
        self.assertIn('"form"', prompt)
        self.assertIn("its form (does the declared form follow from something in this "
                      "subject", prompt)

    def test_an_accept_with_form_fits_false_is_asked_again(self):
        w = writer()
        calls = faked(w, [verdict("accept", form_fits=False, form_problem=MISFIT),
                          verdict("accept")])
        data = w.run(review=True)
        self.assertEqual(names(calls)[:3], ["plan", "plan-review", "plan-review-again"])
        self.assertIn("form_fits is false", calls[2]["prompt"])
        self.assertEqual(data["plan_review"]["unsettled"], [])
        # still contradicting after the again ask, it is kept under unsettled
        w = writer()
        faked(w, [verdict("accept", form_fits=False, form_problem=MISFIT)] * 2)
        record = w.run(review=True)["plan_review"]
        self.assertTrue(any("form_fits" in line for line in record["unsettled"]))

    def test_a_form_only_revise_goes_to_plan_2_with_the_form_problem(self):
        w = writer()
        calls = faked(w, [form_only(), verdict("accept")])
        data = w.run(review=True)
        self.assertEqual(names(calls)[:4], ["plan", "plan-review", "plan-2", "plan-review-2"])
        self.assertIn(FORM_LINE, calls[2]["prompt"])
        self.assertEqual(data["plan_review"]["verdict"], "accept")
        self.assertNotIn("advisory", data["plan_review"])
        self.assertEqual(data["plan_review"]["rounds"][0]["field_lines"], [FORM_LINE])

    def test_a_second_form_only_revise_drafts_on_with_an_advisory(self):
        w = writer()
        calls = faked(w, [form_only(), form_only()])
        data = w.run(review=True)
        order = names(calls)
        self.assertEqual(order[:4], ["plan", "plan-review", "plan-2", "plan-review-2"])
        self.assertIn("write-01", order)
        record = data["plan_review"]
        self.assertEqual((record["verdict"], record["advisory"], record["retried"]),
                         ("revise", ["form"], True))
        self.assertIn("note: plan review: the form still does not fit after planning once "
                      f"more ({MISFIT}); drafted on, since form is a revise-only ground",
                      data["findings"])
        md = "\n".join(script.plan_review_lines(record))
        self.assertIn("- advisory: form", md)
        self.assertIsNone(getattr(w, "plan_review_stop", None))

    def test_a_second_revise_on_form_and_order_still_stops(self):
        both = dict(form_only(), reorder_survives=True)
        w = writer()
        calls = faked(w, [form_only(), both])
        with self.assertRaises(ClaudeError) as caught:
            w.run(review=True)
        self.assertIn("the beat sheet did not pass review", str(caught.exception))
        self.assertEqual(names(calls), ["plan", "plan-review", "plan-2", "plan-review-2"])
        self.assertNotIn("advisory", w.plan_review_stop)
        # a reject is unchanged, and report-only drafts on as before
        w = writer()
        faked(w, [form_only("reject"), form_only("reject")])
        with self.assertRaises(ClaudeError):
            w.run(review=True)
        w = writer()
        calls = faked(w, [form_only()])
        data = w.run(review=True, fix=False)
        self.assertNotIn("plan-2", names(calls))
        self.assertNotIn("advisory", data["plan_review"])

    def test_a_reject_on_the_form_alone_is_asked_again(self):
        w = writer()
        calls = faked(w, [form_only("reject"), form_only(), verdict("accept")])
        w.run(review=True)
        self.assertEqual(names(calls)[:4], ["plan", "plan-review", "plan-review-again",
                                            "plan-2"])
        self.assertIn("the form alone is grounds to revise", calls[2]["prompt"])

    def test_both_prompts_name_form_as_revise_only(self):
        for channel in (False, True):
            with self.subTest(channel=channel):
                attack = " ".join(script.plan_attack(channel).split())
                self.assertIn("its form (", attack)
                self.assertIn("a form that does not follow from the subject is a ground to "
                              "revise only, never to reject", attack)
                role = " ".join(narrative.reviewer_role(plan=True, channel=channel).split())
                self.assertIn("a form that does not follow from the subject is a ground to "
                              "revise only", role)

    def test_the_form_is_read_by_the_reviewer_of_another_family(self):
        engine = harness.ENGINE
        harness.ENGINE = ByModel
        self.addCleanup(setattr, harness, "ENGINE", engine)
        ByModel.seen, ByModel.fail_review = [], False
        with tempfile.TemporaryDirectory() as tmp:
            script.Screenwriter(idea(), root=Path(tmp), verbose=False, minutes=1, jobs=1,
                                model="sonnet", reviewer_model="opus").run(review=True)
        review = [c for c in ByModel.seen if c["kind"] == "plan-review"]
        self.assertEqual([c["model"] for c in review], ["opus"])
        self.assertIn(narrative.FORM_GROUND.strip(", "), " ".join(review[0]["system"].split()))
        authored = {c["model"] for c in ByModel.seen if c["kind"] == "plan"}
        self.assertEqual(authored, {"sonnet"})


# --------------------------------------------------------------------------- #
# the devices, measured from the plan (AUTO-135)
# --------------------------------------------------------------------------- #

class Devices(unittest.TestCase):
    """A device that cannot be checked as declared is a line for the reviewer
    to confirm; a plan with no device has none, since a missing device is
    never a finding."""

    def device_lines(self, plan):
        return [c for c in checks(plan, 60) if "device" in c or "analogy" in c
                or "metaphor" in c or "prediction" in c or "thought_experiment" in c]

    def test_a_plan_with_no_device_or_a_complete_one_has_no_line(self):
        self.assertEqual(self.device_lines(PLAN), [])
        self.assertEqual(checks(with_devices(**{"2": [ANALOGY], "1": [PREDICTION]}), 60), [])
        good = script.read_json(NARRATIVE / "good" / "plan.json")
        self.assertEqual(script.plan_prechecks(good, 130), [])
        # the seeded wrong mapping is declared in full: only a reader sees it
        bad = script.read_json(NARRATIVE / "bad-metaphor" / "plan.json")
        self.assertEqual(script.plan_prechecks(bad, 130), [])

    def test_an_analogy_or_metaphor_without_maps_or_breaks_at_is_a_rule_line(self):
        for kind_ in ("analogy", "visual_metaphor"):
            for missing in ("maps", "breaks_at"):
                plan = with_devices(**{"2": [dict(ANALOGY, kind=kind_, **{missing: " "})]})
                with self.subTest(kind=kind_, missing=missing):
                    found = self.device_lines(plan)
                    self.assertEqual(len(found), 1, found)
                    self.assertTrue(found[0].startswith(f"rule: section 2's {kind_} has no "
                                                        f"{missing}"), found[0])
        plan = with_devices(**{"2": [dict(ANALOGY, maps="", breaks_at="")]})
        found = self.device_lines(plan)
        self.assertEqual(len(found), 1, found)
        self.assertIn("no maps and no breaks_at", found[0])

    def test_a_thought_experiment_with_no_framing_is_a_rule_line(self):
        imagined = {"kind": "thought_experiment", "device": "a bridge with no paint",
                    "maps": "", "breaks_at": "", "framing": "", "scored_in": 0}
        found = self.device_lines(with_devices(**{"3": [imagined]}))
        self.assertEqual(found, ["rule: section 3's thought_experiment has no framing: an "
                                 "imagined case is said to be imagined (\"imagine...\", "
                                 "\"suppose...\") (attention 20)"])
        imagined["framing"] = "suppose the paint were glass"
        self.assertEqual(self.device_lines(with_devices(**{"3": [imagined]})), [])

    def test_a_prediction_not_scored_by_the_reveal_is_a_house_line(self):
        # PLAN's reveal is section 3
        for scored_in, why in ((0, "names no section that scores it"),
                               (1, "is scored in section 1, before it is asked"),
                               (2, "is scored in section 2, before the reveal in section 3"),
                               (9, "names section 9, which the plan does not have"),
                               (2.0, "is scored in section 2, before the reveal in section 3")):
            plan = with_devices(**{"2": [dict(PREDICTION, scored_in=scored_in)]})
            with self.subTest(scored_in=scored_in):
                found = self.device_lines(plan)
                self.assertEqual(len(found), 1, found)
                self.assertTrue(found[0].startswith(f"house: section 2's prediction {why}"),
                                found[0])
                self.assertIn("(attention 2)", found[0])
        plan = with_devices(**{"1": [dict(PREDICTION, scored_in=3)]})
        self.assertEqual(self.device_lines(plan), [])
        # an integral float is an integer to the harness, so it is one here
        plan = with_devices(**{"2": [dict(PREDICTION, scored_in=3.0)]})
        self.assertEqual(self.device_lines(plan), [])
        # a plan without a reveal holds the prediction only to its own section
        plan = with_devices(**{"2": [dict(PREDICTION, scored_in=2)]})
        plan["reveal"] = {"section": 0, "constraint_section": 0}
        self.assertEqual(self.device_lines(plan), [])

    def test_an_off_list_kind_is_not_checked(self):
        # it is recorded as a note and ignored, so it is held to nothing
        plan = with_devices(**{"2": [dict(ANALOGY, kind="simile", maps="", breaks_at="")]})
        self.assertEqual(self.device_lines(plan), [])

    def test_the_lines_reach_the_plan_reviewer_with_the_devices(self):
        plan = with_devices(**{"2": [dict(ANALOGY, breaks_at="")]})
        w = script.Screenwriter(idea(), verbose=False, jobs=1, minutes=1, research="",
                                reviewer_model="opus")
        calls = faked(w, [verdict("accept")], plan=plan)
        w.run(review=True)
        prompt = next(c["prompt"] for c in calls if c["name"] == "plan-review")
        self.assertIn("- rule: section 2's analogy has no breaks_at", prompt)
        self.assertIn(ANALOGY["maps"], prompt)
        self.assertIn(". devices [house]", prompt)
        devices = narrative.criterion("devices")
        self.assertIn(" ".join(devices.question.split()), " ".join(prompt.split()))
        self.assertEqual(script.plan_view(plan)["sections"][1]["devices"],
                         plan["sections"][1]["devices"])
        self.assertNotIn("devices", script.plan_view(PLAN)["sections"][1])

    def test_a_wrong_mapping_is_a_problem_and_the_accept_stands(self):
        said = verdict("accept", [(2, "devices: the analogy maps the paint onto the "
                                      "steel's strength, so it teaches the paint holds",
                                   "map the paint onto what it hides")])
        self.assertEqual(narrative.check_plan_review(said), [])
        w = writer()
        calls = faked(w, [said], plan=with_devices(**{"2": [ANALOGY]}))
        data = w.run(review=True)
        self.assertNotIn("plan-review-again", names(calls))
        self.assertNotIn("plan-2", names(calls))
        self.assertEqual(data["plan_review"]["verdict"], "accept")


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
        # stakes is grounds to revise, never to reject (AUTO-161; see
        # test_narrative_rubric.StakesGround)
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
        self.prompts = {}

        def ask(self_, name, prompt, schema):
            which = kind(schema)
            asked.append(name)
            self.prompts[name] = prompt
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

    def test_a_second_stakes_revise_exits_1_with_plan_review_json(self):
        code, asked, said = self.run_stage(
            "--review", "--model", "sonnet",
            verdicts=[verdict("revise", [NO_STAKES], stakes=False),
                      verdict("revise", [NO_STAKES], stakes=False)])
        self.assertEqual(code, 1)
        self.assertFalse([n for n in asked if n.startswith("write-")])
        self.assertIn(STAKES_LINE, said)
        self.assertFalse((self.film / "script.json").exists())
        stopped = json.loads((self.film / script.PLAN_REVIEW_FILE).read_text())
        record = stopped["plan_review"]
        self.assertEqual([r["verdict"] for r in record["rounds"]], ["revise", "revise"])
        self.assertIn("1989 Quebec blackout", json.dumps(record["problems"]))
        self.assertEqual(record["field_lines"], [STAKES_LINE])

    def test_a_stakes_revise_then_accept_records_both_rounds(self):
        code, asked, said = self.run_stage(
            "--review", "--model", "sonnet",
            verdicts=[verdict("revise", [NO_STAKES], stakes=False), verdict("accept")])
        self.assertEqual(code, 0, said)
        record = json.loads((self.film / "script.json").read_text())["plan_review"]
        self.assertEqual([r["verdict"] for r in record["rounds"]], ["revise", "accept"])
        self.assertIn("plan-2", asked)
        # stakes is what sent it back: plan-2 is told the field's line, and
        # the first round keeps it in script.json
        self.assertIn(STAKES_LINE, self.prompts["plan-2"])
        self.assertEqual(record["rounds"][0]["field_lines"], [STAKES_LINE])

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

    def test_a_paper_dossier_hands_its_digest_and_claims_to_the_screenwriter(self):
        seen = {}

        def run(self_, **kw):
            seen["paper"], seen["claims"] = self_.paper, self_.paper_claims
            raise ClaudeError("stop here")
        for scope, paper in (("paper", PAPER_DIGEST), ("explainer", None)):
            (self.film / "dossier.json").write_text(json.dumps(
                {"scope": scope, "paper": PAPER_DIGEST, "claims": PAPER_CLAIMS,
                 "idea": {"title": "Why the bridge fell", "paper": PAPER_URL}}))
            with mock.patch.object(script.Screenwriter, "run", run):
                self.run_stage()
            self.assertEqual(seen["paper"], paper, scope)
            self.assertEqual(seen["claims"], PAPER_CLAIMS if paper else None, scope)


# --------------------------------------------------------------------------- #
# paper mode: where the framing lands (AUTO-131)
# --------------------------------------------------------------------------- #

def framed(mechanism=3, **sections):
    plan = copy.deepcopy(FRAMED)
    plan["framing"] = framing(**sections)
    plan["mechanism_section"] = mechanism
    return plan


class PaperPrechecks(unittest.TestCase):
    def checks(self, plan):
        return script.paper_prechecks(plan, PAPER_CLAIMS, PAPER_URL)

    def test_a_well_ordered_fully_cited_plan_has_none(self):
        self.assertEqual(self.checks(FRAMED), [])

    def test_each_misplacement_is_its_own_line(self):
        cases = (
            (framed(mechanism=2, problem=3, significance=1, implications=1,
                    contribution=2),
             "rule: the paper's problem (section 3) does not come before the mechanism "
             "(section 2)"),
            (framed(significance=3),
             "rule: the paper's significance (section 3) does not come before the "
             "mechanism (section 3)"),
            (framed(limitations=0),
             "rule: framing.limitations is placed in no section: the film says the "
             "paper's limits on air"),
            (framed(mechanism=1, problem=1, significance=1, implications=1),
             "rule: mechanism_section is missing or outside 2..3"),
            (framed(mechanism=4), "rule: mechanism_section is missing or outside 2..3"),
        )
        for plan, line in cases:
            self.assertEqual(self.checks(plan), [line])

    def test_a_quote_that_is_not_a_claim_and_a_contribution_off_the_paper(self):
        plan = framed()
        plan["framing"]["consequences"]["claims"] = ["A claim nobody made."]
        self.assertEqual(self.checks(plan), [
            "rule: framing.consequences cites \"A claim nobody made.\", which is not a "
            "research claim"])
        plan = framed()
        plan["framing"]["contribution"]["claims"] = [PAPER_CLAIMS[1]["claim"]]
        self.assertEqual(self.checks(plan),
                         ["house: the contribution cites no claim from the paper itself"])
        plan = framed()
        plan["framing"]["problem"]["claims"] = []
        plan["framing"]["significance"]["said"] = " "
        self.assertEqual(self.checks(plan), ["rule: framing.problem cites no research claim",
                                             "rule: framing.significance is empty"])

    def test_a_quote_differing_in_case_and_whitespace_is_a_claim(self):
        plan = framed()
        plan["framing"]["contribution"]["claims"] = [" the transformer  DROPS recurrence "
                                                     "entirely. "]
        self.assertEqual(self.checks(plan), [])


def paper_writer(**kw):
    kw.setdefault("research", "")
    kw.setdefault("reviewer_model", "opus")
    return script.Screenwriter(idea(), verbose=False, jobs=1, minutes=1,
                               paper=PAPER_DIGEST, claims=PAPER_CLAIMS, **kw)


class PaperReview(unittest.TestCase):
    MISORDERED = framed(mechanism=2, problem=3, significance=1, implications=1,
                        contribution=2)
    LINE = ("the paper's problem (section 3) does not come before the mechanism "
            "(section 2)")

    def test_the_review_reads_the_framing_and_its_pre_checks(self):
        w = paper_writer()
        calls = faked(w, [verdict("accept")], plan=self.MISORDERED)
        data = w.run(review=True)
        prompt = next(c["prompt"] for c in calls if c["name"] == "plan-review")
        self.assertIn("the film's significance", prompt)
        self.assertIn('"mechanism_section": 2', prompt)
        self.assertIn(script.PRECHECK_NOTE, prompt)
        self.assertIn(f"- rule: {self.LINE}", prompt)
        self.assertIn(f"rule: {self.LINE}", data["plan_review"]["prechecks"])

    def test_script_md_lists_the_paper_pre_check_under_plan_review(self):
        w = paper_writer()
        faked(w, [verdict("accept")], plan=self.MISORDERED)
        data = w.run(review=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "script.md"
            script.write_md(str(path), idea(), data, script.sections_of(data))
            md = path.read_text()
        review = md.split("## Plan review", 1)[1].split("\n## ", 1)[0]
        self.assertIn(f"- pre-check: `rule` {self.LINE}", review)

    def test_a_local_papers_folder_reaches_no_script_prompt(self):
        folder = "/home/someone/papers"
        w = script.Screenwriter(
            script.Idea(title="Why the bridge fell", description="d", genre="explainer",
                        paper=f"{folder}/attention.pdf"),
            verbose=False, jobs=1, minutes=1, research="", reviewer_model="opus",
            paper=PAPER_DIGEST, claims=PAPER_CLAIMS)
        calls = faked(w, [verdict("accept")], plan=FRAMED)
        w.run(review=True)
        self.assertTrue(calls)
        for call in calls:
            self.assertNotIn(folder, call["prompt"], call["name"])
        # The script names the paper by its digest's citation, never the idea's path.
        self.assertTrue(any(PAPER_URL in c["prompt"] for c in calls))

    def test_run_records_the_rule_lines_as_findings_and_doctor_warns(self):
        plan = copy.deepcopy(self.MISORDERED)
        plan["framing"]["contribution"]["claims"] = [PAPER_CLAIMS[1]["claim"]]
        w = paper_writer(reviewer_model=None)
        faked(w, [], plan=plan)
        data = w.run()
        paper = [f for f in data["findings"] if "paper framing" in f]
        self.assertEqual(paper, [f"warning: paper framing: {self.LINE}"])
        self.assertEqual(data["framing"], plan["framing"])
        self.assertEqual(data["mechanism_section"], 2)
        from ytstudio import doctor, workspace
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "film"
            root.mkdir()
            (root / "idea.json").write_text(json.dumps(
                {"title": "Why the bridge fell", "genre": "g", "description": "d"}))
            (root / "script.json").write_text(json.dumps(data))
            warns = [f.message for f in doctor.check_project(workspace.Project(root)).findings
                     if f.area == "script" and f.level == "warn"]
        self.assertIn(f"paper framing: {self.LINE}", warns)

    def test_a_film_without_a_paper_records_no_framing(self):
        w = script.Screenwriter(idea(), verbose=False, jobs=1, minutes=1, research="")
        faked(w, [], plan=self.MISORDERED)
        data = w.run()
        self.assertFalse([f for f in data["findings"] if "paper framing" in f])


# --------------------------------------------------------------------------- #
# the channel: a plan compared with the channel's earlier films (AUTO-137)
# --------------------------------------------------------------------------- #

def _sha(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


#: The plan as develop's fixture held it before AUTO-137 added ending.device,
#: and the digests of what develop asked over it: the plan prompt, the
#: plan-review prompt, the reviewer's system and PLAN_ATTACK. A film with no
#: counted earlier film is asked exactly this (AUTO-161's stakes ground, merged
#: first, is in the plan-review prompt, the system and the attack).
#: AUTO-157 changed all four on purpose: the plan prompt asks for the form,
#: and the attack and the system name the form a ground to revise only. The
#: old plan has no `form` either, so its review carries no form line.
OLD_PLAN = {**{k: v for k, v in copy.deepcopy(PLAN).items() if k != "form"},
            "ending": {k: v for k, v in PLAN["ending"].items() if k != "device"}}
BEFORE_AUTO_137 = {
    "plan": "f735c4f9250e90e169340553ea9639411f3c3c4718c81441c2741b8ec70f4370",
    "plan-review": "75e3c3092436d0e1e2a6ce91507d102d30f5469470517cbbf89d9be09b071959",
    "system": "45c3e51e269949435933222dd401f1d4d255ef869df71fece30bf8e289c02f36",
    "attack": "d02ba66b70a8ff2195897fc321e5b7333dba6386c650237dafea4f0564d98414",
}

#: One counted earlier film that opened, unfolded and closed as PLAN does.
EARLIER = [{"slug": "old-bridge", "title": "The old bridge", "counted": True,
            "at": "2026-10-01T00:00:00", "opening": "unexpected_observation",
            "closing": "callback",
            "shape": {"count": 3, "tokens": ["open", "constraint", "reveal"],
                      "functions": [["context"], ["context"], ["payoff"]]},
            "treatment": "", "palette": {}, "typography": []}]

REPEAT = {"film": "old-bridge", "dimension": "closing",
          "problem": "it closes on the same callback as the film before"}
MIRRORED = (0, "it closes as old-bridge did", "end on the sheared bolt, not the river")
#: A review's fields when they find nothing wrong with the story itself.
STORY_HOLDS = {"train_ok": True, "reorder_survives": False, "payoff_concrete": True,
               "sections_advancing_none": []}


def channel_faked(w, verdicts, plan=PLAN):
    """faked(), keeping each decision's schema too."""
    calls = faked(w, verdicts, plan=plan)
    ask = w._ask

    def keep(name, prompt, schema):
        out = ask(name, prompt, schema)
        calls[-1]["schema"] = schema
        return out
    w._ask = keep
    return calls


class Channel(unittest.TestCase):
    def test_the_plan_names_its_closing_device(self):
        self.assertEqual(PLAN["ending"]["device"], "callback")

    def test_prechecks_carry_the_channel_lines_only_with_earlier_films(self):
        today = script.plan_prechecks(PLAN, 60, "t")
        self.assertFalse([c for c in today if c.startswith("channel:")])
        with_channel = script.plan_prechecks(PLAN, 60, "t", earlier=EARLIER)
        self.assertEqual(with_channel[:len(today)], today)
        added = with_channel[len(today):]
        self.assertEqual([line.split(":")[1].strip() for line in added],
                         ["opening", "structure", "closing"])
        self.assertTrue(all("old-bridge" in line for line in added))

    def test_with_no_earlier_film_every_prompt_role_and_schema_is_todays(self):
        self.assertIs(script.plan_attack(False), script.PLAN_ATTACK)
        self.assertEqual(_sha(script.PLAN_ATTACK), BEFORE_AUTO_137["attack"])
        w = writer()
        calls = channel_faked(w, [verdict("accept")], plan=OLD_PLAN)
        w.run(review=True)
        asked = {c["name"]: c for c in calls}
        self.assertEqual(_sha(asked["plan"]["prompt"]), BEFORE_AUTO_137["plan"])
        self.assertEqual(_sha(asked["plan-review"]["prompt"]), BEFORE_AUTO_137["plan-review"])
        self.assertIs(asked["plan-review"]["schema"], narrative.PLAN_REVIEW_SCHEMA)
        self.assertEqual(_sha(w.reviewer.system), BEFORE_AUTO_137["system"])

    def test_with_earlier_films_the_plan_and_its_review_are_told_of_them(self):
        w = writer(earlier=EARLIER)
        calls = channel_faked(w, [verdict("accept")])
        w.run(review=True)
        asked = {c["name"]: c for c in calls}
        plan = asked["plan"]["prompt"]
        self.assertIn("How the channel's earlier films opened, unfolded and closed", plan)
        self.assertIn("old-bridge", plan)
        self.assertIn("unless the house names one as recurring", plan)
        review = asked["plan-review"]["prompt"]
        self.assertIn("its sameness", review)
        self.assertIn("could a returning viewer predict", review)
        self.assertIn("a ground to revise, never to reject", review)
        self.assertIn("channel: closing:", review)
        self.assertIn("old-bridge", review)
        self.assertIs(asked["plan-review"]["schema"], narrative.plan_review_schema(True))
        self.assertIn("a repeat of an earlier channel film", w.reviewer.system)
        # the sentence naming the grounds to revise names the repeat itself,
        # so no ground follows a sentence that has already listed them
        self.assertNotIn("Those four, and stakes, are grounds to revise.", review)
        self.assertNotIn("Those four, and stakes, are grounds to revise.",
                         script.plan_attack(True))
        self.assertIn("Those four, and stakes, are grounds to revise, and a repeat of "
                      "an earlier film", script.plan_attack(True))
        # the sixth objection follows AUTO-161's stakes, in one list
        self.assertIn("?), its stakes (", script.plan_attack(True))
        self.assertIn("withheld as its twist?), its form (", script.plan_attack(True))
        # and the form (AUTO-157) is the last before the sameness
        self.assertIn("any topic could wear?) and its sameness (", script.plan_attack(True))
        # the drafting writers are not handed the channel
        writes = [c["prompt"] for c in calls if c["name"].startswith("write-")]
        self.assertTrue(writes)
        self.assertFalse([p for p in writes if "old-bridge" in p])

    def test_a_revise_with_repeats_sends_plan_2_the_repeat(self):
        w = writer(earlier=EARLIER)
        sent = dict(verdict("revise", [MIRRORED]), repeats=[REPEAT])
        calls = channel_faked(w, [sent, dict(verdict("accept"), repeats=[])])
        data = w.run(review=True)
        order = names(calls)
        self.assertEqual(order[:4], ["plan", "plan-review", "plan-2", "plan-review-2"])
        again = calls[2]["prompt"]
        self.assertIn(REPEAT["problem"], again)
        self.assertIn("end on the sheared bolt", again)
        first = data["plan_review"]["rounds"][0]
        self.assertEqual(first["repeats"], [REPEAT])

    def test_each_contradicting_repeat_is_asked_again(self):
        cases = {
            "an accept": dict(verdict("accept", [MIRRORED]), repeats=[REPEAT]),
            "an unknown film": dict(verdict("revise", [(0, "like nowhere", "change it")]),
                                    repeats=[dict(REPEAT, film="nowhere")]),
            "no matching problem": dict(verdict("revise", [SWAPPABLE]), repeats=[REPEAT]),
            # a repeat is a ground to revise only: a reject whose every
            # problem is the repeat would take the film out on sameness
            "a reject on repeats alone": dict(verdict("reject", [MIRRORED]),
                                              repeats=[REPEAT], **STORY_HOLDS),
        }
        for case, first in cases.items():
            with self.subTest(case=case):
                w = writer(earlier=EARLIER)
                calls = channel_faked(w, [first, dict(verdict("accept"), repeats=[])])
                w.run(review=True)
                self.assertEqual(names(calls)[:3], ["plan", "plan-review",
                                                    "plan-review-again"])

    def test_a_reject_on_a_story_ground_beside_a_repeat_stands(self):
        story = (1, "the train names a topic, not a pursuer", "give it a pursuer")
        review = dict(verdict("reject", [MIRRORED, story]), repeats=[REPEAT], **STORY_HOLDS)
        self.assertEqual(narrative.check_plan_review(review, earlier_slugs=["old-bridge"]),
                         [])
        # the review's own fields are a story ground too: here the train fails
        fields = dict(verdict("reject", [MIRRORED]), repeats=[REPEAT],
                      **dict(STORY_HOLDS, train_ok=False))
        self.assertEqual(narrative.check_plan_review(fields, earlier_slugs=["old-bridge"]),
                         [])
        alone = dict(verdict("reject", [MIRRORED]), repeats=[REPEAT], **STORY_HOLDS)
        found = narrative.check_plan_review(alone, earlier_slugs=["old-bridge"])
        self.assertEqual(len(found), 1, found)
        self.assertIn("a repeat is a ground to revise, never to reject", found[0])

    def test_without_review_the_channel_lines_are_notes(self):
        w = writer(earlier=EARLIER, reviewer_model=None)
        calls = faked(w, [])
        data = w.run()
        self.assertNotIn("plan-review", names(calls))
        notes = [f for f in data["findings"] if f.startswith("note: channel:")]
        self.assertEqual(len(notes), 3, data["findings"])
        w = writer(reviewer_model=None)
        faked(w, [])
        self.assertFalse([f for f in w.run()["findings"] if "channel:" in f])

    def test_script_md_shows_the_repeats_or_their_dismissal(self):
        record = {"verdict": "revise", "model": "opus", "problems": [], "rounds": [],
                  "prechecks": ["channel: closing: this film closes on callback, as "
                                "old-bridge did (1 of the last 1 counted films close on it)"],
                  "repeats": [REPEAT],
                  "dismissed": [{"film": "old-bridge", "dimension": "opening",
                                 "why": "a paradox opening is the subject's own"}]}
        md = "\n".join(script.plan_review_lines(record))
        self.assertIn("- repeat of old-bridge (closing): it closes on the same callback", md)
        self.assertIn("- dismissed: old-bridge (opening): a paradox opening is the "
                      "subject's own", md)
        self.assertIn("pre-check: `channel`", md)


class ChannelCommand(unittest.TestCase):
    """`yt script` reads the channel from the work root and records the film."""

    run_stage = Command.run_stage

    def setUp(self):
        Command.setUp(self)
        old = self.root / "work" / "old-bridge"
        old.mkdir()
        drafted = copy.deepcopy(PLAN)
        for section, fn in zip(drafted["sections"], ("context", "context", "payoff")):
            section["shots"] = [{"n": 1, "function": fn}]
        (old / "script.json").write_text(json.dumps(drafted))
        (old / "qa.json").write_text(json.dumps({"verdict": "READY TO PUBLISH"}))

    def test_a_run_reads_the_earlier_films_and_records_this_one(self):
        code, asked, said = self.run_stage("--model", "sonnet")
        self.assertEqual(code, 0, said)
        record = json.loads((self.film / "script.json").read_text())
        notes = [f for f in record["findings"] if f.startswith("note: channel:")]
        self.assertEqual(len(notes), 4, record["findings"])
        # the shared form is one advisory line (AUTO-157)
        self.assertIn("note: channel: form: 'investigation' is shared by 1 of the last 1 "
                      "counted films (old-bridge) (advisory: a form may recur when it fits "
                      "the subject)", notes)
        rows = json.loads((self.root / "work" / "channel.json").read_text())["films"]
        self.assertEqual({r["slug"]: r["counted"] for r in rows},
                         {"old-bridge": True, "a-film": False})
        # the row is read from the script.json a real run wrote, not a fixture
        mine = next(r for r in rows if r["slug"] == "a-film")
        self.assertEqual(mine["opening"], PLAN["hook"]["trigger"])
        self.assertEqual(mine["closing"], "callback")
        self.assertEqual(mine["form"], "investigation")
        self.assertEqual(mine["shape"]["tokens"], ["open", "constraint", "reveal"])

    def test_a_reviewed_run_shows_the_reviewer_the_channel(self):
        code, asked, said = self.run_stage(
            "--review", "--model", "sonnet",
            verdicts=[dict(verdict("accept"), repeats=[],
                           dismissed=[{"film": "old-bridge", "dimension": "closing",
                                       "why": "w"}])])
        self.assertEqual(code, 0, said)
        md = (self.film / "script.md").read_text()
        self.assertIn("pre-check: `channel`", md)
        self.assertIn("- dismissed: old-bridge (closing): w", md)


if __name__ == "__main__":
    unittest.main()
