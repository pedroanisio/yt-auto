"""The screenplay stage fixes its own findings, drafts sections side by side,
and measures only the text a viewer reads.

No model is called: `Screenwriter._ask` is replaced by a fake that answers by
the shape of the schema it is given.
"""

from __future__ import annotations

import contextlib
import copy
import io
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import script  # noqa: E402
from ytstudio.core import Idea  # noqa: E402


def idea():
    return Idea(title="T", description="d", genre="g")


def shot(n, narration, label="", seconds=5, **kw):
    return script.Shot(narration=narration, n=n, label=label or f"1.{n}",
                       seconds=seconds, **kw)


def writer(research="R", **kw):
    w = script.Screenwriter(idea(), research=research, verbose=False, **kw)
    return w


def kind(schema):
    props = schema["properties"]
    for name in ("shots", "fixes", "flags", "edits", "sections"):
        if name in props:
            return name
    return ""


# --------------------------------------------------------------------------- #
# AUTO-7: legibility
# --------------------------------------------------------------------------- #

class Legibility(unittest.TestCase):
    def rows(self, **kw):
        sec = script.Section(heading="h", shots=[
            script.Shot(label="1.1", seconds=5, **kw)])
        return script.legibility([sec])

    DIRECTIONS = ("Lower third, white on a dark bar, fades in at the first "
                  "second, holds, fades out with the cut; the figure counts up "
                  "from zero while the camera pushes in slowly. ")

    def test_visible_strings_alone_are_measured(self):
        self.assertEqual(self.rows(on_screen=self.DIRECTIONS,
                                   on_screen_text=["18 chars of super."]), [])

    def test_quoted_strings_in_the_directions_are_the_visible_text(self):
        self.assertEqual(self.rows(on_screen=self.DIRECTIONS
                                   + 'Reads "18 chars of super."'), [])
        self.assertEqual(self.rows(on_screen=self.DIRECTIONS
                                   + "Reads “18 chars of super.”"), [])

    def test_a_fast_visible_text_is_still_flagged(self):
        rows = self.rows(on_screen=self.DIRECTIONS, on_screen_text=["x" * 100])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["chars"], 100)
        self.assertEqual(rows[0]["cps"], 20.0)

    def test_unquoted_old_screenplays_measure_the_whole_field_as_before(self):
        rows = self.rows(on_screen="y" * 100)
        self.assertEqual(rows[0]["chars"], 100)

    def test_directions_do_not_hide_a_figure_the_voice_never_says(self):
        sec = script.Section(heading="h", shots=[script.Shot(
            label="1.1", seconds=5, narration="plain words",
            on_screen="Count up to 4,200 over two seconds",
            on_screen_text=["Ready"])])
        self.assertEqual(script.unspoken_numbers([sec]), [])

    def test_the_schema_and_prompt_ask_for_the_visible_strings(self):
        self.assertIn("on_screen_text", script.section_schema(False)["properties"]
                      ["shots"]["items"]["properties"])
        seen = []
        w = writer(research="")
        w._ask = lambda name, prompt, schema: seen.append(prompt) or {"shots": []}
        w.write_section({"heading": "A", "seconds": 10, "beats": []}, index=1,
                        total=1, plan={}, written=[], next_heading="",
                        sourced=False)
        self.assertIn("on_screen_text", seen[0])


# --------------------------------------------------------------------------- #
# AUTO-5: verify, then revise
# --------------------------------------------------------------------------- #

def flag(n, severity="important", problem="overstated"):
    return {"n": n, "problem": problem, "severity": severity, "fix": "soften"}


class VerifyThenRevise(unittest.TestCase):
    def make(self, checks, fixes):
        """`checks`: one answer per verify call; `fixes`: per revise call."""
        w = writer()
        calls = {"flags": [], "fixes": []}
        checks, fixes = list(checks), list(fixes)

        def ask(name, prompt, schema):
            which = kind(schema)
            calls[which].append(prompt)
            return (checks if which == "flags" else fixes).pop(0)
        w._ask = ask
        return w, calls

    def shots(self):
        return [shot(1, "Fine line."), shot(2, "It is exactly 9 tons."),
                shot(3, "Also fine.")]

    def test_an_important_finding_is_fixed_inside_one_run(self):
        shots = self.shots()
        w, calls = self.make(
            [{"flags": [flag(2)], "verdict": "no"},
             {"flags": [], "verdict": "yes"}],
            [{"fixes": [{"n": 2, "action": "fix", "narration": "It is about 9 tons.",
                         "why": "source says approximately"}]}])
        check = w.verify_and_revise(shots)
        self.assertEqual(shots[1].narration, "It is about 9 tons.")
        self.assertEqual(shots[1].revised, "source says approximately")
        self.assertEqual(check["flags"], [])
        self.assertEqual(check["revisions"][0]["before"], "It is exactly 9 tons.")
        self.assertEqual(check["revisions"][0]["after"], "It is about 9 tons.")

    def test_only_the_revised_shots_are_checked_again(self):
        w, calls = self.make(
            [{"flags": [flag(2)], "verdict": "no"}, {"flags": [], "verdict": "ok"}],
            [{"fixes": [{"n": 2, "action": "fix", "narration": "About 9 tons.",
                         "why": "w"}]}])
        w.verify_and_revise(self.shots())
        again = calls["flags"][1]
        self.assertIn("About 9 tons.", again)
        self.assertNotIn("Fine line.", again)
        self.assertNotIn("Also fine.", again)

    def test_a_kept_line_stays_flagged_with_its_reason(self):
        shots = self.shots()
        w, calls = self.make(
            [{"flags": [flag(2, "blocker")], "verdict": "no"}],
            [{"fixes": [{"n": 2, "action": "keep", "narration": "",
                         "why": "the source states it exactly"}]}])
        check = w.verify_and_revise(shots)
        self.assertEqual(shots[1].narration, "It is exactly 9 tons.")
        self.assertEqual(len(calls["flags"]), 1)      # nothing changed: no re-check
        self.assertEqual(check["flags"][0]["kept"], "the source states it exactly")
        self.assertEqual(check["kept"][0]["n"], 2)

    def test_minor_flags_and_whole_script_flags_are_not_revised(self):
        w, calls = self.make(
            [{"flags": [flag(1, "minor"), flag(0, "blocker")], "verdict": "v"}], [])
        check = w.verify_and_revise(self.shots())
        self.assertEqual(calls["fixes"], [])
        self.assertEqual(len(check["flags"]), 2)

    def test_rounds_are_bounded_and_the_leftover_is_reported(self):
        persistent = {"flags": [flag(2)], "verdict": "no"}
        w, calls = self.make(
            [persistent] * 3,
            [{"fixes": [{"n": 2, "action": "fix", "narration": f"v{i}", "why": "w"}]}
             for i in range(2)])
        check = w.verify_and_revise(self.shots(), rounds=2)
        self.assertEqual(len(calls["fixes"]), 2)
        self.assertEqual(check["flags"][0]["n"], 2)
        self.assertEqual(check["unresolved"][0]["n"], 2)

    def test_a_failed_revise_call_keeps_the_script_and_the_flags(self):
        shots = self.shots()
        w = writer()

        def ask(name, prompt, schema):
            if kind(schema) == "fixes":
                raise script.ClaudeError("boom")
            return {"flags": [flag(2)], "verdict": "no"}
        w._ask = ask
        check = w.verify_and_revise(shots)
        self.assertEqual(shots[1].narration, "It is exactly 9 tons.")
        self.assertEqual(check["flags"][0]["n"], 2)

    def test_without_research_nothing_is_asked(self):
        w = writer(research="")
        w._ask = lambda *a: self.fail("asked")
        self.assertEqual(w.verify_and_revise(self.shots()), {})

    def test_report_only_leaves_the_old_behaviour(self):
        plan = {"sections": [{"heading": "A", "seconds": 10, "beats": []}]}
        w, calls = self.make([{"flags": [flag(1)], "verdict": "no"}], [])
        w.plan = lambda: plan
        w.write_section = lambda *a, **k: [shot(0, "one", seconds=10)]
        data = w.run(do_verify=True, fix=False)
        self.assertEqual(calls["fixes"], [])
        self.assertEqual(data["check"]["flags"][0]["n"], 1)


# --------------------------------------------------------------------------- #
# AUTO-6: parallel sections, shared ledger
# --------------------------------------------------------------------------- #

PLAN = {"logline": "l", "promise": "p", "payoff": "y", "sections": [
    {"heading": "One", "seconds": 10, "beats": ["It weighs 120 kg"], "claims": []},
    {"heading": "Two", "seconds": 10, "beats": ["Again 120 kg, then 45 minutes"],
     "claims": []},
    {"heading": "Three", "seconds": 10, "beats": ["plain"], "claims": []},
    {"heading": "Four", "seconds": 10, "beats": ["plain"], "claims": []}]}


class Ledger(unittest.TestCase):
    def test_the_first_section_naming_a_figure_owns_it(self):
        ledger = script.Ledger.from_plan(PLAN["sections"])
        self.assertEqual(ledger.owner("120 kg"), 1)
        self.assertEqual(ledger.owner("45"), 2)
        self.assertEqual(ledger.reserved_from(2), ["120 kg"])
        self.assertEqual(ledger.reserved_from(1), ["45"])


class ParallelDrafting(unittest.TestCase):
    def run_writer(self, jobs, answer, **kw):
        w = writer(research="", jobs=jobs, **kw)
        w.plan = lambda: PLAN
        lock = threading.Lock()
        prompts = {}

        def ask(name, prompt, schema):
            which = kind(schema)
            if which == "shots":
                head = prompt.split('Write section ')[1].split('"')[1]
                with lock:
                    prompts[head] = prompt
                return {"shots": answer(head)}
            if which == "fixes":
                with lock:
                    prompts.setdefault("fixes", []).append(prompt)
                return {"fixes": [
                    {"n": int(n), "action": "fix", "narration": "Reworded.", "why": "dup"}
                    for n in w._test_fix_targets]}
            raise AssertionError(which)
        w._ask = ask
        w._test_fix_targets = []
        return w, prompts

    def raw(self, text):
        return [{"visual": "v", "narration": text, "seconds": 10, "asset": "a",
                 "style": "s"}]

    def test_sections_are_drafted_at_the_same_time(self):
        barrier = threading.Barrier(4, timeout=5)

        def answer(head):
            barrier.wait()          # only passes if all four are in flight
            return self.raw(f"line of {head}")
        w, _ = self.run_writer(4, answer)
        data = w.run()
        self.assertEqual([s["heading"] for s in data["sections"]],
                         ["One", "Two", "Three", "Four"])
        self.assertEqual([s["shots"][0]["label"] for s in data["sections"]],
                         ["1.1", "2.1", "3.1", "4.1"])
        self.assertEqual([s["shots"][0]["n"] for s in data["sections"]], [1, 2, 3, 4])
        self.assertEqual([s["shots"][0]["at"] for s in data["sections"]],
                         [0, 10, 20, 30])

    def test_each_prompt_names_the_figures_that_belong_to_other_sections(self):
        w, prompts = self.run_writer(4, lambda head: self.raw("x"))
        w.run()
        self.assertIn("120 kg", prompts["Two"].split("belong to other")[1]
                      if "belong to other" in prompts["Two"] else "")
        self.assertIn("45", prompts["One"].split("belong to other")[1])

    def test_a_figure_spoken_in_two_sections_is_reworded_in_the_later_one(self):
        def answer(head):
            return self.raw({"One": "It weighs 120 kg.", "Two": "Still 120 kg."}
                            .get(head, "Nothing here."))
        w, prompts = self.run_writer(4, answer)
        w._test_fix_targets = [2]
        data = w.run()
        self.assertEqual(data["sections"][0]["shots"][0]["narration"], "It weighs 120 kg.")
        self.assertEqual(data["sections"][1]["shots"][0]["narration"], "Reworded.")
        self.assertEqual(len(prompts["fixes"]), 1)
        self.assertIn("120 kg", prompts["fixes"][0])

    def test_no_repeat_means_no_extra_call(self):
        w, prompts = self.run_writer(4, lambda head: self.raw(f"say {head}"))
        w.run()
        self.assertNotIn("fixes", prompts)

    def test_one_failed_section_does_not_cost_the_rest(self):
        def answer(head):
            if head == "Two":
                raise script.ClaudeError("down")
            return self.raw(f"say {head}")
        w, _ = self.run_writer(4, answer)
        data = w.run()
        self.assertEqual(data["failed_sections"], 1)
        self.assertEqual(data["sections"][1]["shots"], [])
        self.assertEqual(data["sections"][2]["shots"][0]["label"], "3.1")

    def test_one_job_keeps_the_sequential_continuity(self):
        w, prompts = self.run_writer(1, lambda head: self.raw(f"said {head}"))
        w.run()
        self.assertIn("said One", prompts["Two"])

    def test_usage_is_not_lost_across_threads(self):
        w = writer(research="")
        # Threads book into one account, under the harness's lock, drafting
        # and fact-check alike.
        self.assertIs(w.usage, w.harness.usage)
        self.assertIs(w._drafter.usage, w.harness.usage)
        self.assertIs(w._drafter.lock, w.harness.lock)

    def test_repeated_figures_helper(self):
        secs = [script.Section(heading="a", shots=[shot(1, "It is 120 kg.", "1.1")]),
                script.Section(heading="b", shots=[shot(2, "120 kg again.", "2.1"),
                                                   shot(3, "Fresh 7 km.", "2.2")])]
        rows = script.repeated_figures(secs)
        self.assertEqual([(r["figure"].lower(), r["n"]) for r in rows], [("120 kg", 2)])


# --------------------------------------------------------------------------- #
# AUTO-96: the question ledger, briefed to every writer and balanced after
# --------------------------------------------------------------------------- #

QPLAN = {"logline": "l", "promise": "p", "payoff": "y",
         "questions": [
             {"id": "Q1", "text": "Why did the bridge fall?", "opened_in": 1, "closed_in": 3},
             {"id": "Q2", "text": "Who saw the bolt first?", "opened_in": 2, "closed_in": 4},
             {"id": "Q3", "text": "Will the twin bridge stand?", "opened_in": 4,
              "closed_in": 0, "left_open_why": "the next film"}],
         "sections": [
             {"heading": "One", "seconds": 10, "beats": ["b"], "opens": ["Q1"], "closes": []},
             {"heading": "Two", "seconds": 10, "beats": ["b"], "opens": ["Q2"], "closes": []},
             {"heading": "Three", "seconds": 10, "beats": ["b"], "opens": [],
              "closes": ["Q1"]},
             {"heading": "Four", "seconds": 10, "beats": ["b"], "opens": ["Q3"],
              "closes": ["Q2"]}]}

#: What a writer who keeps the plan's ledger answers: two shots a section,
#: each question raised on the first and answered on the second.
BALANCED = {"One": {"opens": [{"id": "Q1", "opens_at": 1}]},
            "Two": {"opens": [{"id": "Q2", "opens_at": 1}]},
            "Three": {"closes": [{"id": "Q1", "closes_at": 2}]},
            "Four": {"opens": [{"id": "Q3", "opens_at": 1}],
                     "closes": [{"id": "Q2", "closes_at": 2}]}}


def two_shots(head):
    return [{"visual": "v", "narration": f"{head} first.", "seconds": 5, "asset": "a",
             "style": "s", "function": "context", "shows": "s"},
            {"visual": "v", "narration": f"{head} second.", "seconds": 5, "asset": "a",
             "style": "s", "function": "context", "shows": "s"}]


def drafted(marks=BALANCED, plan=QPLAN):
    """The plan's sections as a writer answered them, numbered as run() does."""
    sections, n = [], 0
    for index, spec in enumerate(plan["sections"], 1):
        section = script.Section.from_spec(spec)
        shots = [script.Shot.from_raw(r) for r in two_shots(spec["heading"])]
        script.QuestionLedger.from_plan(plan).mark(index, shots,
                                                   marks.get(spec["heading"], {}))
        for i, s in enumerate(shots, 1):
            n += 1
            s.n, s.label = n, f"{index}.{i}"
        section.shots = shots
        sections.append(section)
    return sections


def rule_ids(flags):
    return sorted(f["rule"] for f in flags)


class QuestionLedger(unittest.TestCase):
    def test_the_brief_names_what_a_section_opens_and_closes_and_where_the_rest_is(self):
        ledger = script.QuestionLedger.from_plan(QPLAN)
        two = ledger.brief_for(2)
        self.assertIn("You open Q2 (Who saw the bolt first?); section 4 closes it", two)
        three = ledger.brief_for(3)
        self.assertIn("You close Q1 (Why did the bridge fall?), opened in section 1", three)
        four = ledger.brief_for(4)
        self.assertIn("Q3", four)
        self.assertIn("left open on purpose", four)
        self.assertIn("closes_at", three)

    def test_a_closes_at_index_goes_to_that_shot_and_one_past_the_section_is_a_note(self):
        ledger = script.QuestionLedger.from_plan(QPLAN)
        shots = [script.Shot.from_raw(r) for r in two_shots("Three")]
        ledger.mark(3, shots, {"closes": [{"id": "Q1", "closes_at": 2},
                                          {"id": "Q2", "closes_at": 9}]})
        self.assertEqual([s.closes for s in shots], [[], ["Q1"]])
        self.assertEqual(len(ledger.notes), 1)
        self.assertIn("closes_at 9", ledger.notes[0])


class QuestionFindings(unittest.TestCase):
    def test_a_balanced_ledger_flags_nothing(self):
        self.assertEqual(script.question_findings(QPLAN, drafted()), [])
        cold = {"answered": [{"question_id": "Q1", "shot_index": 6},
                             {"question_id": "Q2", "shot_index": 8}]}
        self.assertEqual(script.question_findings(QPLAN, drafted(), cold), [])

    def test_a_flag_is_a_rule_flag_in_the_check_shape(self):
        marks = dict(BALANCED, Four={})
        flags = script.question_findings(QPLAN, drafted(marks))
        self.assertTrue(flags)
        required = script.CHECK_SCHEMA["properties"]["flags"]["items"]["required"]
        for f in flags:
            for key in required:
                self.assertIn(key, f)
            self.assertTrue(f["problem"].startswith(f["rule"] + ": "), f["problem"])
            self.assertIn(f["tier"], script.TIERS)

    def test_a_plan_question_no_section_closes_is_an_open_loop_on_its_closing_section(self):
        marks = dict(BALANCED, Four={"opens": [{"id": "Q3", "opens_at": 1}]})
        flags = script.question_findings(QPLAN, drafted(marks))
        self.assertEqual(rule_ids(flags), ["rule:open-loop"])
        f = flags[0]
        self.assertEqual((f["severity"], f["tier"], f["question"]),
                         ("important", "important", "Q2"))
        self.assertEqual(f["n"], 8)                 # 4.2, the last shot of section 4
        self.assertIn("Q2", f["problem"])

    def test_a_question_left_open_on_purpose_is_no_loop(self):
        flags = script.question_findings(QPLAN, drafted())
        self.assertNotIn("Q3", " ".join(f["problem"] for f in flags))

    def test_an_answer_before_any_opening_is_closed_unopened(self):
        marks = dict(BALANCED, One={"opens": [{"id": "Q1", "opens_at": 1}],
                                    "closes": [{"id": "Q2", "closes_at": 2}]})
        flags = script.question_findings(QPLAN, drafted(marks))
        self.assertEqual(rule_ids(flags), ["rule:closed-unopened"])
        self.assertEqual((flags[0]["n"], flags[0]["severity"]), (2, "important"))
        unknown = dict(BALANCED, Two={"opens": [{"id": "Q2", "opens_at": 1}],
                                      "closes": [{"id": "Q9", "closes_at": 2}]})
        self.assertEqual(rule_ids(script.question_findings(QPLAN, drafted(unknown))),
                         ["rule:closed-unopened"])

    def test_a_section_that_opens_and_closes_nothing_is_a_minor_flag(self):
        plan = copy.deepcopy(QPLAN)
        plan["sections"].append({"heading": "Five", "seconds": 10, "beats": ["b"],
                                 "opens": [], "closes": []})
        flags = script.question_findings(plan, drafted(plan=plan))
        self.assertEqual(rule_ids(flags), ["rule:raises-nothing"])
        self.assertEqual((flags[0]["severity"], flags[0]["tier"], flags[0]["section"]),
                         ("minor", "minor", 5))
        self.assertNotIn(flags[0]["severity"], script.REVISABLE)

    def test_a_section_the_plans_questions_place_a_question_in_raises_something(self):
        # Q4 is opened in section 2 by the question alone, not by the
        # section's own `opens`; the writer is briefed so, and the section is
        # measured from the same ledger
        plan = copy.deepcopy(QPLAN)
        plan["questions"].append({"id": "Q4", "text": "Who paid?", "opened_in": 5,
                                  "closed_in": 5})
        plan["sections"].append({"heading": "Five", "seconds": 10, "beats": ["b"],
                                 "opens": [], "closes": []})
        marks = dict(BALANCED, Five={"opens": [{"id": "Q4", "opens_at": 1}],
                                     "closes": [{"id": "Q4", "closes_at": 2}]})
        self.assertIn("You open Q4", script.QuestionLedger.from_plan(plan).brief_for(5))
        self.assertEqual(script.question_findings(plan, drafted(marks, plan=plan)), [])

    def test_a_question_no_writer_answered_is_one_open_loop_not_also_the_readers(self):
        marks = dict(BALANCED, Four={"opens": [{"id": "Q3", "opens_at": 1}]})
        cold = {"answered": [{"question_id": "Q1", "shot_index": 6}]}
        flags = script.question_findings(QPLAN, drafted(marks), cold)
        self.assertEqual(rule_ids(flags), ["rule:open-loop"])

    def test_a_closed_question_the_cold_reader_did_not_hear_is_unanswered_by_reader(self):
        cold = {"answered": [{"question_id": "Q1", "shot_index": 6}], "unanswered": ["Q2"]}
        flags = script.question_findings(QPLAN, drafted(), cold)
        self.assertEqual(rule_ids(flags), ["rule:unanswered-by-reader"])
        self.assertEqual((flags[0]["n"], flags[0]["severity"], flags[0]["question"]),
                         (8, "important", "Q2"))
        # no reading, or one that failed, measures nothing
        for none in (None, {}, {"failed": "down"},
                     {"answered": [], "answers_failed": "down"}):
            self.assertEqual(script.question_findings(QPLAN, drafted(), none), [], none)

    def test_an_older_plan_without_a_ledger_is_not_measured(self):
        old = {k: v for k, v in QPLAN.items() if k != "questions"}
        old["sections"] = [{k: v for k, v in s.items() if k not in ("opens", "closes")}
                           for s in QPLAN["sections"]]
        self.assertEqual(script.question_findings(old, drafted({}, plan=old)), [])

    def test_the_questions_block_carries_the_writers_shots_as_labels(self):
        block = script.questions_record(QPLAN, drafted())
        self.assertEqual([(q["id"], q["opened_at"], q["closed_at"]) for q in block],
                         [("Q1", "1.1", "3.2"), ("Q2", "2.1", "4.2"), ("Q3", "4.1", "")])
        self.assertEqual(block[0]["text"], "Why did the bridge fall?")
        self.assertEqual(block[2]["left_open_why"], "the next film")

    def test_the_markdown_ledger_names_the_shots(self):
        lines = script.question_ledger(script.questions_record(QPLAN, drafted()))
        self.assertIn("| id | question | opened in | answered in | raised at | answered at |",
                      lines)
        self.assertIn("| Q1 | Why did the bridge fall? | 1 | 3 | 1.1 | 3.2 |", lines)
        self.assertIn("| Q2 | Who saw the bolt first? | 2 | 4 | 2.1 | 4.2 |", lines)
        # an older script.json, with no labels, keeps its four columns
        old = script.question_ledger(QPLAN["questions"])
        self.assertIn("| Q1 | Why did the bridge fall? | 1 | 3 |", old)

    def test_every_ledger_rule_cites_its_corpus_rule(self):
        for rule in script.LEDGER_RULES:
            bound = script.BOUNDS[rule.bound]
            self.assertTrue(bound["corpus"], rule.id)
            self.assertIn(rule.tier, script.TIERS, rule.id)
            self.assertEqual(rule.corpus, bound["corpus"])
        self.assertFalse({r.id for r in script.LEDGER_RULES} & {r.id for r in script.RULES})

    def test_the_docs_name_every_ledger_rule(self):
        root = Path(__file__).resolve().parent.parent
        for doc in ("README.md", "ytstudio/SKILL.md"):
            text = (root / doc).read_text(encoding="utf-8")
            for rule in script.LEDGER_RULES:
                self.assertIn(f"`{rule.id}`", text, f"{doc} does not name {rule.id}")


class LedgerDrafting(unittest.TestCase):
    """The ledger through the stage: briefed with --jobs 4 and 1 alike, and
    what the writers leave unbalanced sent to revise-ledger with the figure
    repeats."""

    def run_writer(self, jobs, marks=BALANCED, fix_targets=(), keep=(), plan=QPLAN,
                   lines=None, closes=None):
        """`closes` maps a shot number to the questions the writer's fix or
        keep names under `closes`; a number it lacks names none."""
        w = writer(research="", jobs=jobs)
        w.plan = lambda: copy.deepcopy(plan)
        lock = threading.Lock()
        prompts, names = {}, []
        closes = closes or {}

        def named(n):
            return {"closes": list(closes[n])} if n in closes else {}

        def ask(name, prompt, schema):
            with lock:
                names.append(name)
            which = kind(schema)
            if which == "shots":
                head = prompt.split('Write section ')[1].split('"')[1]
                with lock:
                    prompts[head] = prompt
                shots = two_shots(head)
                if lines and head in lines:
                    shots[0]["narration"] = lines[head]
                return {"shots": shots, **copy.deepcopy(marks.get(head, {}))}
            if which == "fixes":
                with lock:
                    prompts.setdefault("fixes", []).append(prompt)
                    prompts.setdefault("schemas", []).append(schema)
                return {"fixes": [{"n": n, "action": "fix", "narration": "Answered here.",
                                   "why": "ledger", **named(n)} for n in fix_targets]
                        + [{"n": n, "action": "keep", "why": "answered by the picture",
                            **named(n)} for n in keep]}
            raise AssertionError(which)
        w._ask = ask
        return w, prompts, names

    def test_every_write_prompt_names_its_opens_and_closes_with_four_jobs_and_one(self):
        for jobs in (4, 1):
            with self.subTest(jobs=jobs):
                w, prompts, _ = self.run_writer(jobs)
                w.run()
                self.assertIn("You open Q1 (Why did the bridge fall?); section 3 closes it",
                              prompts["One"])
                self.assertIn("You open Q2 (Who saw the bolt first?); section 4 closes it",
                              prompts["Two"])
                self.assertIn("You close Q1 (Why did the bridge fall?), opened in section 1",
                              prompts["Three"])
                self.assertIn("You close Q2 (Who saw the bolt first?), opened in section 2",
                              prompts["Four"])

    def test_a_balanced_ledger_asks_no_revision_and_records_the_labels(self):
        w, prompts, names = self.run_writer(4)
        data = w.run()
        self.assertNotIn("fixes", prompts)
        ledger = tuple(r.id for r in script.LEDGER_RULES)
        self.assertFalse([f for f in data["findings"] if any(r in f for r in ledger)],
                         data["findings"])
        self.assertEqual([(q["id"], q["opened_at"], q["closed_at"])
                          for q in data["questions"]],
                         [("Q1", "1.1", "3.2"), ("Q2", "2.1", "4.2"), ("Q3", "4.1", "")])
        shots = data["sections"][2]["shots"]
        self.assertEqual([s["closes"] for s in shots], [[], ["Q1"]])

    def test_an_open_loop_goes_to_revise_ledger_and_the_line_the_writer_names_closes_it(self):
        marks = dict(BALANCED, Four={"opens": [{"id": "Q3", "opens_at": 1}]})
        w, prompts, names = self.run_writer(4, marks, fix_targets=[8], closes={8: ["Q2"]})
        data = w.run()
        self.assertIn("revise-ledger", names)
        self.assertEqual(len(prompts["fixes"]), 1)
        self.assertIn("rule:open-loop", prompts["fixes"][0])
        self.assertIn("Q2", prompts["fixes"][0])
        # the writer is asked to name the questions its line answers
        item = prompts["schemas"][0]["properties"]["fixes"]["items"]["properties"]
        self.assertIn("closes", item)
        self.assertIn("closes", prompts["fixes"][0].split("flagged by", 1)[1])
        self.assertEqual(data["sections"][3]["shots"][1]["narration"], "Answered here.")
        self.assertEqual(data["sections"][3]["shots"][1]["closes"], ["Q2"])
        self.assertEqual(data["questions"][1]["closed_at"], "4.2")
        self.assertFalse([f for f in data["findings"] if "rule:open-loop" in f])

    def test_a_changed_line_that_names_no_answer_leaves_the_open_loop_standing(self):
        # the writer rewords shot 4.2 for the figure it repeats and does not
        # say the line answers Q2: that the line changed is no answer
        plan = copy.deepcopy(QPLAN)
        plan["sections"][0]["beats"] = ["It weighs 120 kg"]
        marks = dict(BALANCED, Four={"opens": [{"id": "Q3", "opens_at": 1}]})
        w, prompts, _ = self.run_writer(
            1, marks, fix_targets=[8], plan=plan,
            lines={"One": "It weighs 120 kg."})
        original = w._ask

        def ask(name, prompt, schema):
            got = original(name, prompt, schema)
            if name == "write-04":
                got["shots"][1]["narration"] = "It still weighs 120 kg."
            return got
        w._ask = ask
        data = w.run()
        self.assertIn("120 kg", prompts["fixes"][0])
        self.assertIn("rule:open-loop", prompts["fixes"][0])
        self.assertEqual(data["sections"][3]["shots"][1]["narration"], "Answered here.")
        self.assertEqual(data["sections"][3]["shots"][1]["closes"], [])
        self.assertEqual(data["questions"][1]["closed_at"], "")
        lines = [f for f in data["findings"] if "rule:open-loop" in f]
        self.assertEqual(len(lines), 1, data["findings"])
        self.assertIn("Q2", lines[0])

    def test_an_answer_before_the_question_goes_to_revise_ledger_and_an_unnamed_line_clears_it(self):
        # section 1 answers Q2 at its second shot, before section 2 raises it
        marks = dict(BALANCED, One={"opens": [{"id": "Q1", "opens_at": 1}],
                                    "closes": [{"id": "Q2", "closes_at": 2}]})
        for named, standing in (({2: []}, 0), ({}, 1)):
            with self.subTest(named=named):
                w, prompts, names = self.run_writer(4, marks, fix_targets=[2],
                                                    closes=named)
                data = w.run()
                self.assertEqual(names.count("revise-ledger"), 1)
                self.assertIn("rule:closed-unopened", prompts["fixes"][0])
                lines = [f for f in data["findings"] if "rule:closed-unopened" in f]
                # a fix naming the line's answers without Q2 takes the mark
                # off; one that names none leaves the mark and the flag
                self.assertEqual(len(lines), standing, data["findings"])
                self.assertEqual(data["questions"][1]["closed_at"],
                                 "4.2" if not standing else "1.2")

    def test_an_open_loop_the_writer_keeps_stands_as_a_warning_with_its_reason(self):
        marks = dict(BALANCED, Four={"opens": [{"id": "Q3", "opens_at": 1}]})
        w, _, _ = self.run_writer(1, marks, keep=[8])
        data = w.run()
        lines = [f for f in data["findings"] if "rule:open-loop" in f]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("warning: rule:open-loop: "), lines[0])
        self.assertIn("kept: answered by the picture", lines[0])
        self.assertEqual(data["questions"][1]["closed_at"], "")

    def test_the_figure_repeats_and_the_questions_share_one_revise_ledger(self):
        plan = copy.deepcopy(QPLAN)
        plan["sections"][0]["beats"] = ["It weighs 120 kg"]
        w, prompts, names = self.run_writer(
            4, dict(BALANCED, Four={}), fix_targets=[3, 8], plan=plan,
            lines={"One": "It weighs 120 kg.", "Two": "Still 120 kg."})
        w.run()
        self.assertEqual(names.count("revise-ledger"), 1)
        self.assertIn("120 kg", prompts["fixes"][0])
        self.assertIn("rule:open-loop", prompts["fixes"][0])

    def test_a_section_raising_nothing_is_a_warning_and_asks_nothing(self):
        plan = copy.deepcopy(QPLAN)
        plan["sections"].append({"heading": "Five", "seconds": 10, "beats": ["b"],
                                 "opens": [], "closes": []})
        w, prompts, names = self.run_writer(4, plan=plan)
        data = w.run()
        self.assertNotIn("revise-ledger", names)
        lines = [f for f in data["findings"] if "rule:raises-nothing" in f]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("warning: "), lines[0])

    def test_a_note_for_a_shot_index_the_section_does_not_have(self):
        marks = dict(BALANCED, Three={"closes": [{"id": "Q1", "closes_at": 5}]})
        w, _, _ = self.run_writer(4, marks, keep=[6])
        data = w.run()
        self.assertTrue([f for f in data["findings"]
                         if f.startswith("note:") and "closes_at 5" in f], data["findings"])

    def test_the_cold_readers_gap_goes_to_revise_ledger_2(self):
        w, prompts, names = self.run_writer(1, fix_targets=[8])
        w.run()                                     # balanced: nothing asked yet
        self.assertNotIn("fixes", prompts)
        sections = drafted()
        shots = [s for sec in sections for s in sec.shots]
        cold = {"answered": [{"question_id": "Q1", "shot_index": 6}]}
        done = w.revise_ledger(sections, shots, QPLAN, cold_read=cold,
                               name="revise-ledger-2")
        self.assertEqual(names[-1], "revise-ledger-2")
        self.assertIn("rule:unanswered-by-reader", prompts["fixes"][-1])
        self.assertEqual(shots[7].narration, "Answered here.")
        # the rewrite named no answer: the reader's gap stands
        self.assertEqual(rule_ids(done["flags"]), ["rule:unanswered-by-reader"])

    def test_the_cold_readers_gap_is_answered_by_a_rewrite_that_names_the_question(self):
        w, prompts, names = self.run_writer(1, fix_targets=[8], closes={8: ["Q2"]})
        sections = drafted()
        shots = [s for sec in sections for s in sec.shots]
        cold = {"answered": [{"question_id": "Q1", "shot_index": 6}]}
        done = w.revise_ledger(sections, shots, QPLAN, cold_read=cold,
                               name="revise-ledger-2")
        self.assertEqual(done["flags"], [])
        # the answer holds for that line only: a section written again in
        # its place is measured afresh
        fresh = drafted()
        sections[3].shots = fresh[3].shots
        self.assertEqual(rule_ids(w.ledger_flags(QPLAN, sections, cold)),
                         ["rule:unanswered-by-reader"])

    def test_a_kept_open_loop_holds_for_its_line_only(self):
        marks = dict(BALANCED, Four={"opens": [{"id": "Q3", "opens_at": 1}]})
        w, prompts, names = self.run_writer(1, keep=[8])
        sections = drafted(marks)
        shots = [s for sec in sections for s in sec.shots]
        w.revise_ledger(sections, shots, QPLAN)
        self.assertEqual(names.count("revise-ledger"), 1)
        self.assertIn("kept: answered by the picture",
                      " ".join(w.ledger_lines(QPLAN, sections)))
        # the cold reader did not hear Q2 either: the line the writer
        # defended is not asked about again under another rule
        cold = {"answered": [{"question_id": "Q1", "shot_index": 6}]}
        w.revise_ledger(sections, shots, QPLAN, cold_read=cold, name="revise-ledger-2",
                        figures=False)
        self.assertNotIn("revise-ledger-2", names)
        # the section written again in its place: its new last line is asked
        sections[3].shots = drafted(marks)[3].shots
        self.assertNotIn("kept:", " ".join(w.ledger_lines(QPLAN, sections)))
        w.revise_ledger(sections, sections[3].shots, QPLAN, name="revise-ledger-3",
                        figures=False)
        self.assertIn("revise-ledger-3", names)


# --------------------------------------------------------------------------- #
# AUTO-94: the narration rules, revised once measured, then recorded
# --------------------------------------------------------------------------- #

RULE_PLAN = {"logline": "l", "promise": "You will see the bolt that brought the bridge down.",
             "payoff": "the bolt",
             "ending": {"payoff_line": "One bolt held up four lanes.", "callback": "",
                        "next_film": ""},
             "sections": [{"heading": "One", "seconds": 10, "beats": []}]}


def bridge():
    return Idea(title="Why the bridge fell", description="d", genre="g")


class Rules(unittest.TestCase):
    """The stage's half of the rules: error and important flags go to revise
    under `revise-rules`, the rules are read again after the change, and
    what stands is a finding; house flags are recorded, never revised."""

    def make(self, lines, fixes=(), *, supers=None):
        w = script.Screenwriter(bridge(), research="", verbose=False, jobs=1)
        w.plan = lambda: RULE_PLAN
        w.write_section = lambda *a, **k: [
            script.Shot(narration=line, seconds=5, function=fn,
                        on_screen_text=list((supers or {}).get(i, [])))
            for i, (line, fn) in enumerate(lines, 1)]
        calls, answers = [], list(fixes)

        def ask(name, prompt, schema):
            calls.append({"name": name, "prompt": prompt, "kind": kind(schema),
                          "schema": schema})
            if kind(schema) == "fixes":
                return answers.pop(0) if answers else {"fixes": []}
            raise AssertionError(name)
        w._ask = ask
        return w, calls

    GREETING = [("Welcome back. The bridge you cross fell at dawn.", "hook"),
                ("One bolt held up four lanes.", "payoff")]

    def test_error_and_important_flags_reach_revise_as_revise_rules(self):
        w, calls = self.make(self.GREETING)
        w.run()
        self.assertEqual([c["name"] for c in calls], ["revise-rules"])
        self.assertIn("rule:filler", calls[0]["prompt"])
        self.assertNotIn("fact-check", calls[0]["prompt"])

    def test_a_fixed_line_is_read_again_and_clears(self):
        w, calls = self.make(self.GREETING, [{"fixes": [
            {"n": 1, "action": "fix", "narration": "The bridge you cross fell at dawn.",
             "why": "the greeting went"}]}])
        data = w.run()
        self.assertEqual(data["sections"][0]["shots"][0]["narration"],
                         "The bridge you cross fell at dawn.")
        self.assertFalse([f for f in data["findings"] if "rule:filler" in f], data["findings"])
        self.assertEqual(data["rules"]["revisions"][0]["n"], 1)
        self.assertEqual(data["rules"]["flags"], [])

    def test_a_fix_that_still_breaks_the_rule_goes_round_again(self):
        w, calls = self.make(self.GREETING, [
            {"fixes": [{"n": 1, "action": "fix",
                        "narration": "Welcome. The bridge you cross fell at dawn.",
                        "why": "shorter greeting"}]},
            {"fixes": [{"n": 1, "action": "fix",
                        "narration": "The bridge you cross fell at dawn.",
                        "why": "the greeting went"}]}])
        data = w.run()
        self.assertEqual([c["name"] for c in calls], ["revise-rules", "revise-rules-2"])
        self.assertIn("Welcome. The bridge", calls[1]["prompt"])   # it reads the fix
        self.assertEqual(data["rules"]["revisions"][-1]["after"],
                         "The bridge you cross fell at dawn.")
        self.assertEqual(data["sections"][0]["shots"][0]["narration"],
                         "The bridge you cross fell at dawn.")
        self.assertFalse([f for f in data["findings"] if "rule:filler" in f])

    def test_a_narration_fix_that_sends_no_supers_keeps_them(self):
        # on_screen_text is optional; an empty list beside a narration-only
        # fix is not an instruction to clear the screen
        w, _ = self.make([("The bridge you cross fell at dawn and "
                           + " ".join(["slowly"] * 22) + ".", "hook"),
                          ("One bolt held up four lanes.", "payoff")],
                         [{"fixes": [{"n": 1, "action": "fix",
                                      "narration": "The bridge you cross fell at dawn.",
                                      "on_screen_text": [], "why": "split"}]}],
                         supers={1: ["Dawn"]})
        data = w.run()
        self.assertEqual(data["sections"][0]["shots"][0]["narration"],
                         "The bridge you cross fell at dawn.")
        self.assertEqual(data["sections"][0]["shots"][0]["on_screen_text"], ["Dawn"])

    def test_a_figure_taken_off_the_screen_clears_it(self):
        # where the rule measured the screen, an empty list is the fix the
        # flag itself suggests: take the figure off
        w, _ = self.make([("The bridge you cross fell at dawn.", "hook"),
                          ("One bolt held up four lanes.", "payoff")],
                         [{"fixes": [{"n": 1, "action": "fix",
                                      "narration": "The bridge you cross fell at dawn.",
                                      "on_screen_text": [], "why": "off the screen"}]}],
                         supers={1: ["97 tonnes"]})
        data = w.run()
        self.assertEqual(data["sections"][0]["shots"][0]["on_screen_text"], [])

    def test_a_kept_error_stands_as_an_error_with_its_reason(self):
        w, _ = self.make(self.GREETING, [{"fixes": [
            {"n": 1, "action": "keep", "narration": "", "why": "the show opens so"}]}])
        data = w.run()
        line = [f for f in data["findings"] if "rule:filler" in f]
        self.assertEqual(len(line), 1, data["findings"])
        self.assertTrue(line[0].startswith("error: rule:filler: shot 1.1"), line[0])
        self.assertIn("kept: the show opens so", line[0])

    def test_a_kept_important_flag_is_a_warning(self):
        w, _ = self.make([("The bridge you cross fell in 1969 and 1970.", "hook"),
                          ("One bolt held up four lanes.", "payoff")],
                         [{"fixes": [{"n": 1, "action": "keep", "narration": "",
                                      "why": "the two years are the point"}]}])
        data = w.run()
        line = [f for f in data["findings"] if "rule:numbers" in f]
        self.assertTrue(line and line[0].startswith("warning: rule:numbers"), data["findings"])

    def test_a_house_flag_is_a_warning_and_never_revised(self):
        w, calls = self.make([("The bridge you cross was built in a year.", "hook"),
                              ("One bolt held up four lanes.", "payoff")])
        data = w.run()
        self.assertEqual(calls, [])                 # nothing revisable
        self.assertTrue(any(f.startswith("warning: house:rule:passive") for f in
                            data["findings"]), data["findings"])

    def test_report_only_records_without_revising(self):
        w, calls = self.make(self.GREETING)
        data = w.run(fix=False)
        self.assertEqual(calls, [])
        line = [f for f in data["findings"] if "rule:filler" in f]
        self.assertTrue(line[0].startswith("error: rule:filler"))
        self.assertIn("not revised (--report-only)", line[0])

    def test_a_super_the_rule_measures_can_be_fixed(self):
        w, calls = self.make([("The bridge you cross fell at dawn.", "hook"),
                              ("One bolt held up four lanes.", "payoff")],
                             [{"fixes": [{"n": 1, "action": "fix",
                                          "narration": "The bridge you cross fell at dawn.",
                                          "on_screen_text": ["Dawn"], "why": "shorter"}]}],
                             supers={1: ["x" * 100]})
        data = w.run()
        self.assertIn("x" * 100, calls[0]["prompt"])          # the reviser sees the super
        self.assertEqual(data["sections"][0]["shots"][0]["on_screen_text"], ["Dawn"])
        self.assertFalse([f for f in data["findings"] if "rule:legibility" in f])

    def test_a_film_in_another_language_records_what_was_not_evidenced(self):
        w, _ = self.make([("A ponte que você cruza caiu.", "hook"),
                          ("Um parafuso segurou quatro pistas.", "payoff")])
        w.lang = "pt-BR"
        data = w.run()
        self.assertIn("not_evidenced: rule:passive (pt-BR)", data["findings"])


class RulesOnTheCommandLine(unittest.TestCase):
    """`yt script` with an `error:` rule finding standing exits 1 and leaves
    narration.txt unwritten, unless --report-only."""

    def setUp(self):
        import tempfile
        from ytstudio import harness
        from tests.test_harness_decisions import FakeEngine
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._engine = harness.ENGINE
        harness.ENGINE = FakeEngine
        self.addCleanup(setattr, harness, "ENGINE", self._engine)
        FakeEngine.json_calls, FakeEngine.texts, FakeEngine.answer = [], [], None
        self.film = self.root / "a-film"
        self.film.mkdir()
        (self.root / "work").mkdir()
        (self.film / "idea.json").write_text(
            '{"title": "Why the bridge fell", "genre": "g", "description": "d"}')

    def run_stage(self, *flags, lines=None):
        import json
        from unittest import mock
        from ytstudio import cli
        from tests.test_harness_decisions import FakeEngine, blank

        def answer(prompt, schema, system=None):
            FakeEngine.json_calls.append({"prompt": prompt, "schema": schema})
            out = blank(schema)
            if "questions" in schema["properties"]:
                out.update({k: v for k, v in RULE_PLAN.items()})
                out["sections"] = [dict(blank(schema["properties"]["sections"]["items"]),
                                        heading="One", seconds=10, beats=["b"])]
            elif "shots" in schema["properties"]:
                out["shots"] = [{"visual": "v", "narration": line, "seconds": 5,
                                 "asset": "", "style": "", "function": fn, "shows": "s"}
                                for line, fn in (lines or Rules.GREETING)]
            elif "fixes" in schema["properties"]:
                out["fixes"] = [{"n": 1, "action": "keep", "narration": "",
                                 "why": "the show opens so"}]
            return out
        args = cli.build_parser().parse_args(
            ["script", "--project", str(self.film), "--work", str(self.root / "work"),
             "--quiet", *flags])
        with mock.patch.object(FakeEngine, "ask_json", staticmethod(answer)), \
                contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = script.run(args)
        record = json.loads((self.film / "script.json").read_text())
        return code, record, out.getvalue() + err.getvalue()

    def test_a_standing_error_exits_1_and_writes_no_narration(self):
        (self.film / "narration.txt").write_text("an older read\n")
        code, record, said = self.run_stage()
        self.assertEqual(code, 1)
        self.assertFalse((self.film / "narration.txt").exists())
        self.assertTrue((self.film / "script.md").exists())
        self.assertTrue(any(f.startswith("error: rule:filler") for f in record["findings"]))
        self.assertIn("error: rule:filler", said)            # print_hints prints it
        # the old keys stay beside the findings for one release
        self.assertIn("legibility", record)
        self.assertIn("unspoken_numbers", record)

    def test_a_clean_portuguese_film_writes_its_narration(self):
        # the case the language declarations exist for: no English lexicon
        # read over Portuguese, so no false `error:` costs narration.txt
        (self.film / "idea.json").write_text(
            '{"title": "Por que a ponte caiu", "genre": "g", "description": "d"}')
        code, record, _ = self.run_stage("--lang", "pt-BR", lines=[
            ("A ponte que você cruza caiu de madrugada.", "hook"),
            ("Um parafuso segurou quatro pistas.", "payoff")])
        self.assertFalse([f for f in record["findings"] if f.startswith("error:")],
                         record["findings"])
        self.assertIn("not_evidenced: rule:passive (pt-BR)", record["findings"])
        self.assertEqual(code, 0)
        self.assertTrue((self.film / "narration.txt").exists())

    def test_report_only_writes_the_narration_and_records_the_error(self):
        code, record, _ = self.run_stage("--report-only")
        self.assertEqual(code, 0)
        self.assertTrue((self.film / "narration.txt").exists())
        self.assertTrue(any(f.startswith("error: rule:filler") for f in record["findings"]))

    def test_a_dossier_film_records_claim_status_and_ceiling_on_every_shot(self):
        # AUTO-132, through the command: the dossier's claim table caps each
        # line, script.md prints it, and with no --review the status check
        # is recorded as not evidenced, never as passed.
        import json
        dossier = {"claims": [
            {"claim": "The bolt rusted", "detail": "", "source_title": "NTSB",
             "source_url": "u", "source_kind": "primary", "confidence": "high",
             "standing": "established"},
            {"claim": "Paint hid it", "detail": "", "source_title": "NTSB",
             "source_url": "u", "source_kind": "primary", "confidence": "high",
             "standing": "limitation"}]}
        (self.film / "dossier.json").write_text(json.dumps(dossier))
        lines = [("The bridge you cross fell at dawn.", "hook", "c1", "established"),
                 ("One bolt held up four lanes.", "payoff", "c2", "established")]
        from unittest import mock
        from tests.test_harness_decisions import FakeEngine, blank

        def answer(prompt, schema, system=None):
            FakeEngine.json_calls.append({"prompt": prompt, "schema": schema})
            out = blank(schema)
            if "questions" in schema["properties"]:
                out.update(RULE_PLAN)
                out["sections"] = [dict(blank(schema["properties"]["sections"]["items"]),
                                        heading="One", seconds=10, beats=["b"])]
            elif "shots" in schema["properties"]:
                out["shots"] = [{"visual": "v", "narration": line, "seconds": 5,
                                 "asset": "", "style": "", "function": fn, "shows": "s",
                                 "source": "NTSB", "claim": cid, "status": status}
                                for line, fn, cid, status in lines]
            elif "fixes" in schema["properties"]:
                out["fixes"] = [{"n": 2, "action": "keep", "narration": "",
                                 "why": "the record shows it"}]
            return out
        from ytstudio import cli
        args = cli.build_parser().parse_args(
            ["script", "--project", str(self.film), "--work", str(self.root / "work"),
             "--quiet", "--verify"])
        with mock.patch.object(FakeEngine, "ask_json", staticmethod(answer)), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            script.run(args)
        record = json.loads((self.film / "script.json").read_text())
        shots = [s for sec in record["sections"] for s in sec["shots"]]
        self.assertEqual([(s["claim"], s["status"], s["ceiling"]) for s in shots],
                         [("c1", "established", "established"),
                          ("c2", "established", "hypothesis")])
        for s in shots:
            effective = script.effective_status(script.Shot(**s))
            self.assertLessEqual(script.LADDER.index(s["ceiling"]),
                                 script.LADDER.index(effective))
        self.assertEqual(record["cited_claims"]["c2"]["standing"], "limitation")
        self.assertEqual(record["check"]["status"]["not_evidenced"], script.STATUS_UNREAD)
        self.assertNotIn("verdict", record["check"]["status"])
        self.assertIn("not_evidenced: verify-status (status check needs a reviewer of "
                      "another family (--review))", record["findings"])
        self.assertTrue(any("rule:unframed-status" in f for f in record["findings"]))
        md = (self.film / "script.md").read_text()
        self.assertIn("**STATUS** — hypothesis (c2, limitation), capped from established", md)
        self.assertIn("**STATUS** — established (c1, established)", md)
        # the writer was shown the ids
        write = next(c["prompt"] for c in FakeEngine.json_calls
                     if "shots" in c["schema"]["properties"])
        self.assertIn("- c2: Paint hid it", write)


# --------------------------------------------------------------------------- #
# AUTO-132: the status of a line, checked by another family and weakened
# --------------------------------------------------------------------------- #

#: c4 is a limitation the paper names; c7 a claim the research rates low.
TABLE = {"c1": {"standing": "established", "confidence": "high", "caveat": "",
                "source_kind": "primary"},
         "c4": {"standing": "limitation", "confidence": "high", "caveat": "",
                "source_kind": "primary"},
         "c7": {"standing": "hypothesis", "confidence": "high", "caveat": "",
                "source_kind": "primary"}}


def status_shots():
    shots = [shot(1, "The core is liquid.", status="established", claim="c1",
                  source="P"),
             shot(2, "The core might be molten.", status="hypothesis", claim="c7",
                  source="P"),
             shot(3, "Scientists have proven the core is molten.", status="hypothesis",
                  claim="c7", source="P")]
    script.cap(shots, TABLE)
    return shots


def status_writer(**kw):
    kw.setdefault("claim_table", copy.deepcopy(TABLE))
    kw.setdefault("reviewer_model", "gpt-5")
    return writer(**kw)


class StatusCheck(unittest.TestCase):
    def asked(self, w, *, checks=None, status=None, fixes=None):
        """Answer `_ask` by decision name and keep every call."""
        calls = []
        checks, status, fixes = list(checks or []), list(status or []), list(fixes or [])

        def ask(name, prompt, schema):
            calls.append({"name": name, "prompt": prompt, "schema": schema})
            if name.startswith("verify-status"):
                return status.pop(0) if status else {"flags": [], "verdict": "ok"}
            if name.startswith("verify"):
                return checks.pop(0) if checks else {"flags": [], "verdict": "ok"}
            return fixes.pop(0) if fixes else {"fixes": []}
        w._ask = ask
        return calls

    def test_the_listing_names_each_lines_status_and_claim(self):
        listing = script.shot_listing(status_shots(), cite=True)
        self.assertIn("[status: hypothesis, claim c7]", listing)
        self.assertIn("[status: established, claim c1]", listing)
        self.assertNotIn("status", script.shot_listing([shot(1, "Plain.")], cite=True))
        self.assertNotIn("status", script.shot_listing(status_shots()))

    def test_status_flags_join_the_fact_check_tagged(self):
        w = status_writer()
        calls = self.asked(w, status=[{"flags": [flag(3, "blocker", "proven on a "
                                                    "hypothesis")], "verdict": "no"}])
        check = w.verify_and_revise(status_shots(), fix=False)
        names = [c["name"] for c in calls]
        self.assertEqual(names, ["verify", "verify-status"])
        self.assertEqual([(f["n"], f.get("check")) for f in check["flags"]], [(3, "status")])
        self.assertEqual(check["status"]["verdict"], "no")
        prompt = calls[1]["prompt"]
        self.assertIn("[status: hypothesis, claim c7]", prompt)
        self.assertIn("marked none or what-if but asserts a fact", prompt)
        self.assertIn("scientists have proven", prompt)
        self.assertIn("R", prompt)          # the research

    def test_it_is_asked_of_the_status_reader_not_the_author(self):
        w = status_writer()
        seen = []

        def record(who):
            def decide(name, prompt, schema, **kw):
                seen.append((who, name))
                return {"flags": [], "verdict": "ok"}
            return decide
        w.harness.decide = record("author")
        w._drafter.decide = record("drafter")
        reader = w._reader("status")
        reader.decide = record("status")
        w.verify_and_revise(status_shots(), fix=False)
        self.assertEqual(seen, [("author", "verify"), ("status", "verify-status")])
        self.assertEqual(reader.model, "gpt-5")
        self.assertEqual(reader.house, "")
        self.assertIn(script.STATUS_ROLE, reader.system)

    def test_without_another_family_it_is_not_evidenced_never_passed(self):
        w = status_writer(reviewer_model=None)
        calls = self.asked(w)
        check = w.verify_and_revise(status_shots())
        self.assertEqual([c["name"] for c in calls], ["verify"])
        self.assertEqual(check["status"]["not_evidenced"],
                         "status check needs a reviewer of another family (--review)")
        self.assertNotIn("verdict", check["status"])

    def test_a_failed_status_call_is_not_evidenced(self):
        w = status_writer()

        def ask(name, prompt, schema):
            if name.startswith("verify-status"):
                raise script.ClaudeError("timed out")
            return {"flags": [], "verdict": "ok"}
        w._ask = ask
        check = w.verify_and_revise(status_shots())
        self.assertIn("timed out", check["status"]["not_evidenced"])

    def test_with_no_claims_nothing_changes(self):
        shots = status_shots()
        w = writer(reviewer_model="gpt-5")
        calls = self.asked(w, checks=[{"flags": [flag(3)], "verdict": "no"}],
                           fixes=[{"fixes": [{"n": 3, "action": "keep", "narration": "",
                                              "why": "fine"}]}])
        check = w.verify_and_revise(shots)
        self.assertEqual([c["name"] for c in calls], ["verify", "revise-1"])
        self.assertIs(calls[1]["schema"], script.REVISE_SCHEMA)
        self.assertNotIn("status", check)

    def test_an_older_screenplay_asks_what_it_always_did(self):
        shots = [shot(1, "Fine."), shot(2, "Exactly 9 tons.", source="P")]
        w = status_writer()
        calls = self.asked(w, checks=[{"flags": [flag(2)], "verdict": "no"}],
                           fixes=[{"fixes": [{"n": 2, "action": "keep", "narration": "",
                                              "why": "fine"}]}])
        w.verify_and_revise(shots)
        self.assertEqual([c["name"] for c in calls], ["verify", "revise-1"])
        self.assertIs(calls[1]["schema"], script.REVISE_SCHEMA)


class StatusRevise(unittest.TestCase):
    def test_the_fact_check_revise_may_weaken_a_status(self):
        shots = status_shots()
        w = status_writer()
        seen = []

        def ask(name, prompt, schema):
            seen.append(schema)
            return {"fixes": [{"n": 2, "action": "fix", "narration": shots[1].narration,
                               "status": "speculation", "why": "one lab only"}]}
        w._ask = ask
        result = w.revise(shots, [flag(2)], name="revise-1")
        self.assertIs(seen[0], script.FACT_REVISE_SCHEMA)
        self.assertEqual(shots[1].status, "speculation")
        self.assertEqual(result["applied"][0]["status_before"], "hypothesis")
        self.assertEqual(result["applied"][0]["status_after"], "speculation")

    def test_a_stronger_status_is_ignored(self):
        shots = status_shots()
        w = status_writer()
        w._ask = lambda name, prompt, schema: {"fixes": [
            {"n": 2, "action": "fix", "narration": shots[1].narration,
             "status": "established", "why": "sure"}]}
        result = w.revise(shots, [flag(2)], name="revise-1")
        self.assertEqual(shots[1].status, "hypothesis")
        self.assertEqual(result["applied"], [])

    def test_a_status_only_fix_clears_its_blocker_after_the_recheck(self):
        shots = status_shots()
        w = status_writer()
        calls = StatusCheck.asked(
            StatusCheck(), w,
            status=[{"flags": [flag(3, "blocker", "proven on a hypothesis")],
                     "verdict": "no"}, {"flags": [], "verdict": "ok"}],
            fixes=[{"fixes": [{"n": 3, "action": "fix", "narration": shots[2].narration,
                               "status": "speculation", "why": "it is a guess"}]}])
        check = w.verify_and_revise(shots)
        names = [c["name"] for c in calls]
        self.assertEqual(names, ["verify", "verify-status", "revise-1", "verify-1",
                                 "verify-status-1"])
        for name in ("verify-1", "verify-status-1"):
            prompt = next(c["prompt"] for c in calls if c["name"] == name)
            self.assertIn("Scientists have proven", prompt)
            self.assertNotIn("The core is liquid.", prompt)
        self.assertEqual(check["flags"], [])
        self.assertEqual(check["unresolved"], [])
        self.assertEqual(check["revisions"][0]["status_after"], "speculation")

    def test_no_other_revise_reads_a_status(self):
        for kw in ({"rules": True, "name": "revise-rules"},
                   {"back": "the operator", "name": "revise-labels"},
                   {"flagged_by": "the story review", "name": "revise-story-1"}):
            shots = status_shots()
            w = status_writer()
            seen = []

            def ask(name, prompt, schema, shots=shots):
                seen.append(schema)
                return {"fixes": [{"n": 2, "action": "fix", "narration": shots[1].narration,
                                   "status": "speculation", "why": "w"}]}
            w._ask = ask
            result = w.revise(shots, [dict(flag(2), rule="rule:unframed-status")], **kw)
            self.assertEqual(shots[1].status, "hypothesis", kw)
            self.assertEqual(result["applied"], [], kw)
            self.assertIsNot(seen[0], script.FACT_REVISE_SCHEMA, kw)

    def test_a_line_read_at_none_is_never_raised_to_established(self):
        # none ranks below established in _doubt, so "weaker than none" must
        # not let the fact-check's revise lift an uncited none line.
        for given, applied in (("established", False), ("hypothesis", False),
                               ("speculation", True), ("what-if", True)):
            shots = [shot(1, "And then the lights went out.", status="none", claim="",
                          source="P")]
            script.cap(shots, TABLE)
            w = status_writer()
            w._ask = lambda name, prompt, schema, given=given: {"fixes": [
                {"n": 1, "action": "fix", "narration": shots[0].narration,
                 "status": given, "why": "w"}]}
            result = w.revise(shots, [flag(1)], name="revise-1")
            self.assertEqual(shots[0].status, given if applied else "none", given)
            self.assertEqual(bool(result["applied"]), applied, given)

    def test_none_is_never_a_weakening_of_a_line_that_states_a_fact(self):
        # An uncited line on a real source has no ceiling, and a cited one
        # its claim's; neither may be hidden behind none by the fact-check.
        for status, claim in (("established", ""), ("hypothesis", "c7")):
            shots = [shot(1, "The core is liquid.", status=status, claim=claim,
                          source="P")]
            script.cap(shots, TABLE)
            w = status_writer()
            w._ask = lambda name, prompt, schema: {"fixes": [
                {"n": 1, "action": "fix", "narration": shots[0].narration,
                 "status": "none", "why": "w"}]}
            result = w.revise(shots, [flag(1)], name="revise-1")
            self.assertEqual(shots[0].status, status, status)
            self.assertEqual(result["applied"], [], status)

    def test_the_rules_revise_names_only_cues_the_rule_accepts(self):
        shots = status_shots()
        w = status_writer()
        seen = []

        def ask(name, prompt, schema):
            seen.append(prompt)
            return {"fixes": []}
        w._ask = ask
        w.revise(shots, [dict(flag(2), rule="rule:unframed-status")], rules=True,
                 name="revise-rules")
        self.assertIn(script.FRAMING_CUES, seen[0])
        self.assertIn(f'"{script.FRAMING_LABEL}"', seen[0])
        self.assertIn(f'"{script.WHATIF_LABEL}"', seen[0])
        self.assertNotIn("one study", seen[0])
        self.assertNotIn("(may,", seen[0])

    def test_a_rules_fix_for_an_unframed_status_may_label_the_screen(self):
        shots = status_shots()
        w = status_writer()
        w._ask = lambda name, prompt, schema: {"fixes": [
            {"n": 2, "action": "fix", "narration": shots[1].narration,
             "on_screen_text": ["Hypothesis"], "why": "label it"}]}
        result = w.revise(shots, [dict(flag(2), rule="rule:unframed-status")],
                          rules=True, name="revise-rules")
        self.assertEqual(shots[1].on_screen_text, ["Hypothesis"])
        self.assertEqual(result["applied"][0]["on_screen_after"], ["Hypothesis"])


class StatusDrafting(unittest.TestCase):
    def test_write_section_caps_each_shot_from_the_claim_table(self):
        w = status_writer()
        w._ask = lambda name, prompt, schema: {"shots": [
            {"visual": "v", "narration": "The core is liquid.", "seconds": 5,
             "asset": "", "style": "", "function": "context", "shows": "s",
             "source": "P", "claim": "c4", "status": "established",
             "ceiling": "established"},
            {"visual": "v", "narration": "It rains iron.", "seconds": 5, "asset": "",
             "style": "", "function": "context", "shows": "s", "source": "P",
             "claim": "c99", "status": "established"}]}
        shots = w.write_section({"heading": "A", "seconds": 10, "beats": []}, index=1,
                                total=1, plan={}, written=[], next_heading="",
                                sourced=True)
        self.assertEqual([s.ceiling for s in shots], ["hypothesis", "speculation"])

    def test_the_write_prompt_names_only_cues_the_rule_accepts(self):
        w = status_writer()
        seen = []

        def ask(name, prompt, schema):
            seen.append(prompt)
            return {"shots": []}
        w._ask = ask
        w.write_section({"heading": "A", "seconds": 10, "beats": []}, index=1, total=1,
                        plan={}, written=[], next_heading="", sourced=True)
        self.assertIn(script.FRAMING_CUES, seen[0])
        self.assertIn(f"a label such as {script.FRAMING_LABEL}", seen[0])

    def test_a_statused_film_drafted_without_the_fact_check_says_so(self):
        plan = {"sections": [{"heading": "A", "seconds": 10, "beats": []}]}
        w = status_writer()
        StatusCheck.asked(StatusCheck(), w)
        w.plan = lambda: plan
        w.write_section = lambda *a, **k: status_shots()
        data = w.run(do_verify=False)
        self.assertIn("not_evidenced: verify-status (needs --verify --review)",
                      data["findings"])
        plain = writer()
        StatusCheck.asked(StatusCheck(), plain)
        plain.plan = lambda: plan
        plain.write_section = lambda *a, **k: [shot(1, "Plain.")]
        self.assertFalse([f for f in plain.run(do_verify=False)["findings"]
                          if "verify-status" in f])

    def test_the_run_records_the_status_check_and_the_cited_claims(self):
        plan = {"sections": [{"heading": "A", "seconds": 10, "beats": []}]}
        w = status_writer(reviewer_model=None)
        StatusCheck.asked(StatusCheck(), w)
        w.plan = lambda: plan
        w.write_section = lambda *a, **k: status_shots()
        data = w.run(do_verify=True)
        self.assertIn("not_evidenced: verify-status (status check needs a reviewer of "
                      "another family (--review))", data["findings"])
        self.assertEqual(sorted(data["cited_claims"]), ["c1", "c7"])
        shot3 = data["sections"][0]["shots"][2]
        self.assertEqual((shot3["claim"], shot3["status"], shot3["ceiling"]),
                         ("c7", "hypothesis", "hypothesis"))
        self.assertTrue(any("rule:unframed-status: shot 1.3" in f
                            for f in data["findings"]), data["findings"])

    def test_the_run_caps_what_the_draft_hands_it(self):
        # write_section caps its own shots; the run caps them again, so a
        # shot that reached it uncapped (or with a stored ceiling) is the
        # dossier's.
        plan = {"sections": [{"heading": "A", "seconds": 10, "beats": []}]}
        w = status_writer(reviewer_model=None)
        StatusCheck.asked(StatusCheck(), w)
        w.plan = lambda: plan
        w.write_section = lambda *a, **k: [
            shot(1, "The core might be liquid.", status="established", claim="c4",
                 source="P", ceiling="established"),
            shot(2, "It may rain iron.", status="hypothesis", claim="c99", source="P")]
        data = w.run(do_verify=True)
        self.assertEqual([s["ceiling"] for s in data["sections"][0]["shots"]],
                         ["hypothesis", "speculation"])

    def test_the_pool_reviewers_record_goes_on_the_status_reading(self):
        from ytstudio import reviewers
        pool = reviewers.Resolved(model="openai/gpt-5.5", maker="openai",
                                  providers=("openai",), has=(), price={},
                                  price_source="pinned", fetched_at=None)
        plan = {"sections": [{"heading": "A", "seconds": 10, "beats": []}]}
        w = status_writer(reviewer=pool)
        StatusCheck.asked(StatusCheck(), w)
        w.plan = lambda: plan
        w.write_section = lambda *a, **k: status_shots()
        data = w.run(do_verify=True)
        self.assertEqual(data["check"]["status"]["model"], "openai/gpt-5.5")
        self.assertEqual(data["check"]["status"]["reviewer"], pool.record())

    def test_a_failed_fact_check_says_the_status_was_not_read(self):
        plan = {"sections": [{"heading": "A", "seconds": 10, "beats": []}]}
        w = status_writer()

        def ask(name, prompt, schema):
            if name.startswith("verify"):
                raise script.ClaudeError("timed out")
            return {"fixes": []}
        w._ask = ask
        w.plan = lambda: plan
        w.write_section = lambda *a, **k: status_shots()
        data = w.run(do_verify=True)
        self.assertIn("not_evidenced: verify-status (fact-check failed)", data["findings"])

    def test_a_film_with_no_status_notes_no_unread_status(self):
        plan = {"sections": [{"heading": "A", "seconds": 10, "beats": []}]}
        w = writer()
        w._ask = lambda name, prompt, schema: (_ for _ in ()).throw(
            script.ClaudeError("timed out")) if name.startswith("verify") else {"fixes": []}
        w.plan = lambda: plan
        w.write_section = lambda *a, **k: [shot(1, "Plain.")]
        data = w.run(do_verify=True)
        self.assertFalse([f for f in data["findings"] if "verify-status" in f])

    def test_a_status_off_the_list_is_noted(self):
        plan = {"sections": [{"heading": "A", "seconds": 10, "beats": []}]}
        w = status_writer(reviewer_model=None)
        StatusCheck.asked(StatusCheck(), w)
        w.plan = lambda: plan
        odd = status_shots()
        odd[0].status = "proven"
        w.write_section = lambda *a, **k: odd
        data = w.run()
        self.assertTrue(any(f.startswith("note: shot 1.1: status 'proven'")
                            for f in data["findings"]), data["findings"])


class StatusLine(unittest.TestCase):
    def test_a_lowered_status_says_capped(self):
        s = script.Shot(narration="x", n=1, status="established", claim="c4", source="P",
                        ceiling="hypothesis")
        line = script.status_line(s, {"cited_claims": TABLE})
        self.assertEqual(line, "hypothesis (c4, limitation), capped from established")

    def test_a_label_read_at_its_ceiling_is_not_called_capped(self):
        for status in ("what-if", "none"):
            s = script.Shot(narration="The sea boils away.", n=1, status=status,
                            claim="c7", source="P", ceiling="hypothesis")
            line = script.status_line(s, {"cited_claims": TABLE})
            self.assertNotIn("capped", line, status)
            self.assertEqual(line, f"hypothesis (c7, hypothesis), read at its claim's "
                                   f"ceiling, labelled {status}")

    def test_an_unchanged_status_says_nothing_more(self):
        s = script.Shot(narration="x", n=1, status="hypothesis", claim="c7", source="P",
                        ceiling="hypothesis")
        self.assertEqual(script.status_line(s, {"cited_claims": TABLE}),
                         "hypothesis (c7, hypothesis)")


if __name__ == "__main__":
    unittest.main()
