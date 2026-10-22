"""The screenplay stage fixes its own findings, drafts sections side by side,
and measures only the text a viewer reads.

No model is called: `Screenwriter._ask` is replaced by a fake that answers by
the shape of the schema it is given.
"""

from __future__ import annotations

import contextlib
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


if __name__ == "__main__":
    unittest.main()
