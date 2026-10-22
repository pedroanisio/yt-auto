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
        calls = faked(w, [verdict("reject")])
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
        self.assertIn(narrative.reviewer_role().split(".")[0], review[0]["system"])
        others = {c["model"] for c in ByModel.seen if c["kind"] != "plan-review"}
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


if __name__ == "__main__":
    unittest.main()
