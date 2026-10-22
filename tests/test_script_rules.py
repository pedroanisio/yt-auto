"""The narration's rule findings (AUTO-94): what the corpus lets a lint
measure in the text is measured before voice is paid for.

Each rule is pinned on a small film: a clean sheet and a sheet that breaks
it. The flags are shaped like the fact-check's (CHECK_SCHEMA), so `revise`
can answer them, and every rule names the corpus rule and the strength its
bound stands on (docs/narrative/README.md, the strength table). A rule whose
languages exclude the film's is recorded as not evidenced, never skipped
silently. Modelled on tests/test_sound_rules.py.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ytstudio import script  # noqa: E402

TITLE = "Why the bridge fell"
PLAN = {"idea": {"title": TITLE},
        "promise": "You will see the one bolt that brought the bridge down.",
        "payoff": "The bolt, and why nobody checked it.",
        "ending": {"payoff_line": "One bolt, painted over, held up four lanes."}}


def film(*sections, seconds=5):
    """Sections from lists of lines; a line is a narration string or a
    (narration, Shot keywords) pair. Shots are numbered and labelled."""
    out, n = [], 0
    for i, lines in enumerate(sections, 1):
        shots = []
        for j, line in enumerate(lines, 1):
            line, kw = line if isinstance(line, tuple) else (line, {})
            n += 1
            kw = {"seconds": seconds, **kw}
            shots.append(script.Shot(narration=line, n=n, label=f"{i}.{j}", **kw))
        out.append(script.Section(heading=f"S{i}", shots=shots))
    return out


def clean(seconds=5, **over):
    """A film every rule passes: the subject and the viewer in the hook, a
    pronoun for the viewer in every section, short sentences, the payoff
    line last and nothing after it."""
    parts = {
        "hook": ["The bridge you drive over every week fell at dawn.",
                 "Nobody saw the bolt that brought it down."],
        "body": ["We scraped the paint off one joint.",
                 "Under it, the steel had rusted through."],
        "end": ["Here is what you came for.",
                ("One bolt, painted over, held up four lanes.", {"function": "payoff"})],
    }
    parts.update(over)
    return film(parts["hook"], parts["body"], parts["end"], seconds=seconds)


def rules(sections, plan=PLAN, wpm=150, lang=""):
    return script.rule_findings(plan, sections, wpm, lang)


def by_rule(flags, rule):
    return [f for f in flags if f["rule"] == rule]


RULE_IDS = {r.id for r in script.RULES}


def strength_table() -> dict:
    text = (ROOT / "docs" / "narrative" / "README.md").read_text(encoding="utf-8")
    return dict(re.findall(r"^\| ((?:structure|attention|direction|judging) \d+) \| "
                           r"(\w[\w-]*) \|$", text, re.M))


class Shape(unittest.TestCase):
    def test_a_rule_flag_is_a_check_flag_that_opens_with_its_rule(self):
        flags = rules(clean(hook=["Welcome back. The bridge you cross fell at dawn.",
                                  "Nobody saw the bolt."]))
        flag = by_rule(flags, "rule:filler")[0]
        required = script.CHECK_SCHEMA["properties"]["flags"]["items"]["required"]
        for key in required:
            self.assertIn(key, flag)
        severities = (script.CHECK_SCHEMA["properties"]["flags"]["items"]["properties"]
                      ["severity"]["enum"])
        for f in flags:
            self.assertIn(f["severity"], severities)
            self.assertIn(f["rule"], RULE_IDS)
            self.assertTrue(f["problem"].startswith(f["rule"] + ": "), f["problem"])
            self.assertIn(f["tier"], script.TIERS)

    def test_a_clean_film_has_no_findings(self):
        self.assertEqual(rules(clean()), [])

    def test_every_rule_declares_a_tier_its_languages_and_a_cited_bound(self):
        table = strength_table()
        self.assertTrue(table)
        self.assertEqual(len(RULE_IDS), len(script.RULES))
        for rule in script.RULES:
            self.assertIn(rule.tier, script.TIERS, rule.id)
            self.assertIsInstance(rule.langs, tuple, rule.id)
            self.assertIn(rule.bound, script.BOUNDS, rule.id)
            bound = script.BOUNDS[rule.bound]
            self.assertIn("value", bound, rule.id)
            self.assertTrue(bound["corpus"], rule.id)
            for cited in bound["corpus"]:
                self.assertIn(cited, table, f"{rule.id} cites {cited}")
            # a bound is as strong as a cited rule, or it is the house's own
            words = {table[c] for c in bound["corpus"]} | {"house"}
            self.assertIn(bound["strength"], words, rule.id)
            self.assertEqual(rule.corpus, bound["corpus"])

    def test_the_tiers_the_card_names(self):
        tiers = {r.id: r.tier for r in script.RULES}
        self.assertEqual({k for k, v in tiers.items() if v == "error"},
                         {"rule:filler", "rule:sentence"})
        self.assertEqual({k for k, v in tiers.items() if v == "important"},
                         {"rule:unspoken-number", "rule:legibility", "rule:numbers",
                          "rule:hook-subject", "rule:hook-answer", "rule:payoff-last"})
        self.assertEqual({k for k, v in tiers.items() if v == "minor"}, {"rule:last-line"})
        self.assertEqual({k for k, v in tiers.items() if v == "house"},
                         {"rule:clause", "rule:passive", "rule:person", "rule:pace",
                          "rule:density", "rule:hook-promise", "rule:hook-stakes",
                          "rule:end-signal", "rule:recap"})

    def test_only_the_error_and_important_tiers_are_revisable(self):
        flags = rules(film(["Welcome. " + " ".join(["word"] * 30) + "."]))
        for f in flags:
            revisable = f["severity"] in script.REVISABLE
            self.assertEqual(revisable, f["tier"] in ("error", "important"), f)

    def test_no_threshold_the_corpus_calls_folklore(self):
        # the seven-word anecdote and the 160 wpm figure are folklore; the
        # 3/5/8/15 s hook windows are vendor numbers (structure, Contested)
        values = [b["value"] for b in script.BOUNDS.values()]
        self.assertNotIn(7, values)
        self.assertNotIn(160, values)
        self.assertEqual(script.BOUNDS["hook_window"]["value"], 30)

    def test_the_docs_name_every_rule(self):
        for doc in ("README.md", "ytstudio/SKILL.md"):
            text = (ROOT / doc).read_text(encoding="utf-8")
            for rule in RULE_IDS:
                self.assertIn(f"`{rule}`", text, f"{doc} does not name {rule}")


class Languages(unittest.TestCase):
    def test_a_rule_outside_its_languages_is_not_evidenced_once(self):
        lines = script.not_evidenced("pt-BR")
        self.assertIn("not_evidenced: rule:passive (pt-BR)", lines)
        self.assertEqual(len(lines), len(set(lines)))
        self.assertNotIn("not_evidenced: rule:filler (pt-BR)", lines)
        self.assertNotIn("not_evidenced: rule:sentence (pt-BR)", lines)

    def test_english_and_no_language_evidence_every_rule(self):
        self.assertEqual(script.not_evidenced(""), [])
        self.assertEqual(script.not_evidenced("en-GB"), [])

    def test_a_language_with_no_lexicon_keeps_only_the_measured_rules(self):
        lines = script.not_evidenced("de")
        self.assertIn("not_evidenced: rule:filler (de)", lines)
        self.assertNotIn("not_evidenced: rule:sentence (de)", lines)
        self.assertNotIn("not_evidenced: rule:legibility (de)", lines)


class Filler(unittest.TestCase):
    def test_welcome_back_in_shot_one_is_an_error(self):
        flags = by_rule(rules(clean(hook=["Welcome back to the channel.",
                                          "The bridge you cross fell at dawn."])),
                        "rule:filler")
        self.assertEqual([(f["n"], f["tier"], f["severity"]) for f in flags],
                         [(1, "error", "blocker")])
        self.assertIn("1.1", flags[0]["problem"])
        self.assertIn("welcome", flags[0]["problem"].lower())

    def test_every_filler_sentence_before_the_first_content_one_is_flagged(self):
        flags = by_rule(rules(clean(hook=["Hi, my name is Ana. Subscribe now.",
                                          "The bridge you cross fell at dawn. Subscribe."])),
                        "rule:filler")
        self.assertEqual(len(flags), 2)
        self.assertEqual({f["n"] for f in flags}, {1})

    def test_a_sign_off_after_the_content_starts_is_not_filler(self):
        flags = rules(clean(hook=["The bridge you cross fell at dawn.",
                                  "Subscribe, and you will see why."]))
        self.assertEqual(by_rule(flags, "rule:filler"), [])

    def test_portuguese_is_read_with_its_own_words(self):
        sheet = film(["Bem-vindos de volta ao canal.", "A ponte caiu de madrugada."])
        flags = by_rule(rules(sheet, plan={}, lang="pt-BR"), "rule:filler")
        self.assertEqual([f["n"] for f in flags], [1])

    def test_a_language_it_does_not_cover_is_not_flagged_but_recorded(self):
        sheet = film(["Welcome back to the channel.", "Die Brücke fiel."])
        self.assertEqual(by_rule(rules(sheet, plan={}, lang="de"), "rule:filler"), [])
        self.assertIn("not_evidenced: rule:filler (de)", script.not_evidenced("de"))


class Sentence(unittest.TestCase):
    def line(self, words):
        return "The bridge " + " ".join(["fell"] * (words - 2)) + "."

    def test_over_twenty_five_words_is_an_error(self):
        flags = by_rule(rules(clean(body=[self.line(26), "We scraped it."])),
                        "rule:sentence")
        self.assertEqual([(f["n"], f["tier"]) for f in flags], [(3, "error")])
        self.assertIn("26 words", flags[0]["problem"])

    def test_twenty_five_words_is_not(self):
        self.assertEqual(by_rule(rules(clean(body=[self.line(25), "We scraped it."])),
                                 "rule:sentence"), [])

    def test_it_measures_every_language(self):
        sheet = film(["Die " + " ".join(["Brücke"] * 30) + "."])
        self.assertEqual(len(by_rule(rules(sheet, plan={}, lang="de"), "rule:sentence")), 1)


class Numbers(unittest.TestCase):
    def test_two_numerals_in_one_sentence(self):
        flags = by_rule(rules(clean(body=["In 1969, 400,000 people built it.",
                                          "We scraped it."])), "rule:numbers")
        self.assertEqual([(f["n"], f["tier"]) for f in flags], [(3, "important")])

    def test_one_numeral_a_sentence_is_fine(self):
        self.assertEqual(by_rule(rules(clean(body=["It opened in 1969.",
                                                   "We scraped 400,000 bolts."])),
                                 "rule:numbers"), [])


class UnspokenNumber(unittest.TestCase):
    def test_a_figure_on_screen_the_voice_never_says(self):
        sheet = clean(body=[("We scraped the paint off one joint.",
                             {"on_screen_text": ["4,200 joints"]}), "Under it, rust."])
        flags = by_rule(rules(sheet), "rule:unspoken-number")
        self.assertEqual([(f["n"], f["tier"]) for f in flags], [(3, "important")])
        self.assertIn("4,200", flags[0]["problem"])

    def test_a_figure_said_in_words_is_spoken(self):
        sheet = clean(body=[("We scraped twelve joints.", {"on_screen_text": ["12"]}),
                            "Under it, rust."])
        self.assertEqual(by_rule(rules(sheet), "rule:unspoken-number"), [])

    def test_the_old_function_is_kept(self):
        sheet = clean(body=[("We scraped one.", {"on_screen_text": ["4,200"]})])
        self.assertEqual(script.unspoken_numbers(sheet)[0]["number"], "4,200")


class Legibility(unittest.TestCase):
    def test_a_super_nobody_can_finish_reading(self):
        sheet = clean(body=[("We scraped the paint off one joint.",
                             {"on_screen_text": ["x" * 100]}), "Under it, rust."])
        flags = by_rule(rules(sheet), "rule:legibility")
        self.assertEqual([(f["n"], f["tier"]) for f in flags], [(3, "important")])
        self.assertIn("20.0", flags[0]["problem"])
        self.assertEqual(script.legibility(sheet)[0]["cps"], 20.0)   # kept


class HookSubject(unittest.TestCase):
    def test_a_hook_that_names_the_subject_passes(self):
        self.assertEqual(by_rule(rules(clean()), "rule:hook-subject"), [])

    def test_a_hook_that_opens_on_a_definition_is_flagged(self):
        sheet = clean(hook=["A truss is a frame of triangles you trust.",
                            "Engineers have used it for centuries."])
        flags = by_rule(rules(sheet), "rule:hook-subject")
        self.assertEqual([(f["n"], f["tier"]) for f in flags], [(1, "important")])
        self.assertIn("bridge", flags[0]["problem"])

    def test_the_subject_on_screen_counts(self):
        sheet = clean(hook=[("A truss is a frame of triangles you trust.",
                             {"on_screen_text": ["The bridge"]}), "It held."])
        self.assertEqual(by_rule(rules(sheet), "rule:hook-subject"), [])

    def test_the_subject_after_the_thirty_second_intro_is_too_late(self):
        sheet = film([("A truss is a frame of triangles you trust.", {"seconds": 31}),
                      "The bridge fell."])
        self.assertEqual(len(by_rule(rules(sheet), "rule:hook-subject")), 1)


class HookAnswer(unittest.TestCase):
    def test_the_payoff_line_said_in_the_hook_is_flagged(self):
        sheet = clean(hook=["The bridge you cross fell. One bolt, painted over, held "
                            "up four lanes.", "Nobody saw it."])
        flags = by_rule(rules(sheet), "rule:hook-answer")
        self.assertEqual([(f["n"], f["tier"]) for f in flags], [(1, "important")])

    def test_a_hook_that_withholds_it_passes(self):
        self.assertEqual(by_rule(rules(clean()), "rule:hook-answer"), [])


class PayoffLast(unittest.TestCase):
    def test_a_last_section_that_carries_the_payoff_line_passes(self):
        self.assertEqual(by_rule(rules(clean()), "rule:payoff-last"), [])

    def test_a_last_section_without_it_is_flagged_on_its_last_line(self):
        sheet = clean(end=["Here is what you came for.", "Engineers argue still."])
        flags = by_rule(rules(sheet), "rule:payoff-last")
        self.assertEqual([(f["n"], f["tier"]) for f in flags], [(6, "important")])

    def test_no_payoff_line_no_rule(self):
        sheet = clean(end=["Here is what you came for.", "Engineers argue still."])
        self.assertEqual(by_rule(rules(sheet, plan={"idea": {"title": TITLE}}),
                                 "rule:payoff-last"), [])


class Clause(unittest.TestCase):
    def test_three_clauses_in_a_sentence_are_a_house_warning(self):
        sheet = clean(body=["We found the bolt, which held the deck that the crew "
                            "painted.", "Under it, rust."])
        flags = by_rule(rules(sheet), "rule:clause")
        self.assertEqual([(f["n"], f["tier"]) for f in flags], [(3, "house")])
        self.assertNotIn(flags[0]["severity"], script.REVISABLE)

    def test_one_relative_clause_is_fine(self):
        self.assertEqual(by_rule(rules(clean()), "rule:clause"), [])


class Passive(unittest.TestCase):
    def test_passives_over_a_tenth_of_the_sentences(self):
        sheet = clean(body=["The paint was scraped off one joint.",
                            "The rust was hidden by it."])
        flags = by_rule(rules(sheet), "rule:passive")
        self.assertTrue(flags)
        self.assertEqual({f["tier"] for f in flags}, {"house"})

    def test_english_only(self):
        sheet = film(["A tinta foi raspada.", "A ferrugem foi escondida."])
        self.assertEqual(by_rule(rules(sheet, plan={}, lang="pt-BR"), "rule:passive"), [])
        self.assertIn("not_evidenced: rule:passive (pt-BR)", script.not_evidenced("pt-BR"))


class Person(unittest.TestCase):
    def test_a_section_that_never_addresses_the_viewer(self):
        sheet = clean(body=["The paint came off one joint.", "Under it, the steel rusted."])
        flags = by_rule(rules(sheet), "rule:person")
        self.assertEqual([(f["n"], f["tier"]) for f in flags], [(3, "house")])
        self.assertIn("S2", flags[0]["problem"])


class Pace(unittest.TestCase):
    def sheet(self):
        # 30 words over two narrated 5 s shots: 12 s at 150 wpm, 9 s at 200
        words = " ".join(["word"] * 13)
        return clean(body=[f"We scraped {words}.", f"You saw {words}."])

    def test_more_words_than_the_shots_hold_at_the_wpm(self):
        flags = by_rule(rules(self.sheet(), wpm=150), "rule:pace")
        self.assertEqual([(f["n"], f["tier"]) for f in flags], [(3, "house")])
        self.assertIn("150 wpm", flags[0]["problem"])
        self.assertIn("180 wpm", flags[0]["problem"])      # the planned pace
        self.assertIn("retime", flags[0]["problem"])

    def test_the_same_words_fit_at_a_faster_read(self):
        self.assertEqual(by_rule(rules(self.sheet(), wpm=200), "rule:pace"), [])

    def test_a_silent_shot_is_not_voiced_time(self):
        sheet = clean(body=["We scraped " + " ".join(["word"] * 18) + ".", ""])
        self.assertEqual(len(by_rule(rules(sheet), "rule:pace")), 1)


class Density(unittest.TestCase):
    def test_a_section_thick_with_names_and_numbers(self):
        sheet = clean(body=["We met Ana Silva and Rui Costa in 1969.",
                            "You saw Lisbon, Porto and Braga in 1971."])
        flags = by_rule(rules(sheet), "rule:density")
        self.assertEqual([(f["n"], f["tier"]) for f in flags], [(3, "house")])


class HookPromise(unittest.TestCase):
    # 15 s shots: the hook section alone fills the 30 s window
    def test_a_hook_that_never_touches_the_promise(self):
        sheet = clean(seconds=15, hook=["The bridge you cross fell at dawn.", "It was cold."])
        flags = by_rule(rules(sheet), "rule:hook-promise")
        self.assertEqual([f["tier"] for f in flags], ["house"])

    def test_a_hook_that_paraphrases_it(self):
        self.assertEqual(by_rule(rules(clean(seconds=15)), "rule:hook-promise"), [])


class HookStakes(unittest.TestCase):
    def test_no_viewer_addressed_line_in_ninety_seconds(self):
        sheet = film(["The bridge fell at dawn."], ["Nobody saw the bolt."],
                     ["You will see it now."], seconds=50)
        flags = by_rule(rules(sheet), "rule:hook-stakes")
        self.assertEqual([f["tier"] for f in flags], ["house"])

    def test_a_viewer_addressed_line_in_the_window(self):
        self.assertEqual(by_rule(rules(clean()), "rule:hook-stakes"), [])


class EndSignal(unittest.TestCase):
    def test_a_summary_after_the_payoff(self):
        sheet = clean(end=[("One bolt, painted over, held up four lanes.",
                            {"function": "payoff"}), "In summary, you saw a bolt."])
        flags = by_rule(rules(sheet), "rule:end-signal")
        self.assertEqual([(f["n"], f["tier"]) for f in flags], [(6, "house")])

    def test_an_end_signal_building_toward_the_payoff(self):
        sheet = clean(end=["Before we go, you should see one thing.",
                           ("One bolt, painted over, held up four lanes.",
                            {"function": "payoff"})])
        self.assertEqual(by_rule(rules(sheet), "rule:end-signal"), [])

    def test_finally_after_the_payoff(self):
        sheet = clean(end=[("One bolt, painted over, held up four lanes.",
                            {"function": "payoff"}), "Finally, you saw a bolt."])
        self.assertEqual(len(by_rule(rules(sheet), "rule:end-signal")), 1)

    def test_finally_in_an_enumeration_is_not_an_end_signal(self):
        sheet = clean(end=[("One bolt, painted over, held up four lanes.",
                            {"function": "payoff"}),
                           "First you scrape, then you look, and finally you find it."])
        self.assertEqual(by_rule(rules(sheet), "rule:end-signal"), [])


class Recap(unittest.TestCase):
    def test_a_sign_off_after_the_payoff(self):
        sheet = clean(end=[("One bolt, painted over, held up four lanes.",
                            {"function": "payoff"}), "Thanks for watching."])
        flags = by_rule(rules(sheet), "rule:recap")
        self.assertEqual([(f["n"], f["tier"]) for f in flags], [(6, "house")])

    def test_more_than_twenty_five_words_after_the_payoff(self):
        tail = " ".join(["You saw it."] * 9)
        sheet = clean(end=[("One bolt, painted over, held up four lanes.",
                            {"function": "payoff"}), tail])
        self.assertEqual(len(by_rule(rules(sheet), "rule:recap")), 1)

    def test_ending_on_the_payoff(self):
        self.assertEqual(by_rule(rules(clean()), "rule:recap"), [])


class LastLine(unittest.TestCase):
    def test_a_long_last_line_is_minor(self):
        last = "One bolt, painted over, held up four lanes " + " ".join(["and"] * 13) + " more."
        sheet = clean(end=["Here is what you came for.", (last, {"function": "payoff"})])
        flags = by_rule(rules(sheet), "rule:last-line")
        self.assertEqual([(f["n"], f["tier"], f["severity"]) for f in flags],
                         [(6, "minor", "minor")])


class Lines(unittest.TestCase):
    """What script.json's `findings` records for each tier (sdkkit's prefixes)."""

    def flags(self):
        return rules(clean(hook=["Welcome back. The bridge you cross fell.",
                                 "In 1969, 400,000 people crossed it."],
                           body=["The paint came off one joint.", "The steel rusted."]))

    def test_each_tier_has_its_prefix(self):
        lines = script.rule_lines(self.flags(), kept={}, lang="")
        self.assertTrue(any(l.startswith("error: rule:filler: ") for l in lines), lines)
        self.assertTrue(any(l.startswith("warning: rule:numbers: ") for l in lines), lines)
        self.assertTrue(any(l.startswith("warning: house:rule:person: ") for l in lines),
                        lines)
        self.assertFalse(any(l.startswith("error: house") or l.startswith("error: rule:numbers")
                             for l in lines), lines)

    def test_a_kept_flag_stands_with_its_reason(self):
        flags = self.flags()
        kept = {("rule:filler", 1): "the greeting is the show's"}
        lines = script.rule_lines(flags, kept=kept, lang="")
        line = next(l for l in lines if l.startswith("error: rule:filler"))
        self.assertIn("kept: the greeting is the show's", line)

    def test_not_evidenced_lines_follow_the_language(self):
        lines = script.rule_lines([], kept={}, lang="pt-BR")
        self.assertIn("not_evidenced: rule:passive (pt-BR)", lines)


FINDINGS = ["error: rule:filler: shot 1.1: \"Welcome back.\" (welcome) comes before the "
            "first sentence about the subject; kept: the show opens so",
            "warning: rule:numbers: shot 1.2: 2 numerals in one sentence (1969, 1970)",
            "warning: house:rule:person: shot 2.1: section \"S2\" never addresses the viewer",
            "not_evidenced: rule:passive (pt-BR)"]


class Doctor(unittest.TestCase):
    """`yt doctor` reads script.json's findings: an `error:` fails under
    `script` with the command that fixes it, a `warning:` warns, and what was
    not evidenced is a note."""

    def setUp(self):
        import json
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "film"
        self.root.mkdir()
        (self.root / "idea.json").write_text(json.dumps(
            {"title": TITLE, "genre": "g", "description": "d"}))
        (self.root / "script.json").write_text(json.dumps({
            "target_seconds": 10, "findings": FINDINGS, "sections": [
                {"heading": "S1", "shots": [{"label": "1.1", "narration": "x",
                                             "seconds": 10}]}]}))

    def report(self):
        from ytstudio import doctor, workspace
        return [f for f in doctor.check_project(workspace.Project(self.root)).findings
                if f.area == "script"]

    def test_an_error_finding_fails_with_the_fixing_command(self):
        fails = [f for f in self.report() if f.level == "fail"]
        self.assertEqual(len(fails), 1, fails)
        self.assertIn("rule:filler", fails[0].message)
        self.assertIn("yt script --project", fails[0].fix)
        self.assertIn("--notes", fails[0].fix)

    def test_a_warning_warns_and_a_house_one_too(self):
        warns = [f.message for f in self.report() if f.level == "warn"]
        self.assertTrue(any("rule:numbers" in m for m in warns), warns)
        self.assertTrue(any("house:rule:person" in m for m in warns), warns)

    def test_not_evidenced_is_a_note(self):
        notes = [f.message for f in self.report() if f.level == "note"]
        self.assertTrue(any("rule:passive (pt-BR)" in m for m in notes), notes)


class Build(unittest.TestCase):
    """`yt build` carries a standing script `error:` into build.json's
    findings, where qa.mechanical_blockers lifts it; warnings stay in the
    screenplay's record."""

    def test_only_the_errors_are_carried(self):
        from ytstudio import build
        carried = build.script_errors({"findings": FINDINGS})
        self.assertEqual(len(carried), 1)
        self.assertTrue(carried[0].startswith("error: script: rule:filler"), carried)

    def test_the_manifest_carries_them_and_qa_lifts_them(self):
        import tempfile
        from tests.test_build_audio import _idea, _sections
        from ytstudio import build, qa
        with tempfile.TemporaryDirectory() as tmp:
            _, manifest = build.SceneBuilder(
                _idea(), _sections(), Path(tmp), verbose=False, audio=False,
                edit={"coverage": [], "captions_off": [], "tail_seconds": 0.5,
                      "notes": "", "blocking": []},
                script_findings=build.script_errors({"findings": FINDINGS})).build()
        self.assertIn(build.script_errors({"findings": FINDINGS})[0], manifest["findings"])
        lifted = qa.mechanical_blockers(manifest, gate=None)
        self.assertTrue(any("rule:filler" in b for b in lifted), lifted)

    def test_an_older_screenplay_carries_nothing(self):
        from ytstudio import build
        self.assertEqual(build.script_errors({}), [])
        self.assertEqual(build.script_errors(None), [])


if __name__ == "__main__":
    unittest.main()
