"""AUTO-91: the narrative rubric, one tuple read by the writer and the reviewer.

`ytstudio/narrative.py` holds the ten criteria a story is judged on (train,
hook, stakes, open loops, reveal setup, tension, ending, honesty, voice, devices), each with four
labelled levels, a worked example, the strength of its lead rule from
docs/narrative/README.md and the corpus rules it is drawn from; the review's
schema in the shape of script coverage (judging 12); the plan review's schema;
the level order permuted per sample (judging 8); and the check that runs past
the schema (a recommend carrying a Poor, a consider with nothing to fix, a
flag on no criterion).

tests/fixtures/narrative/ starts the calibration set (judging 10): plans and
scripts seeded with one known fault each (among them an aside that serves no
claim, graded on voice), the good pair, and the good script with one joke
that makes the point (claim-joke), which expects recommend with voice no
lower than Good. Each case's
`expected.json` names the criterion it fails, the best verdict a correct
reviewer may give it, where the fault sits, and a review the schema can
express that says so.

No model is called: the schemas are answered by test_harness_decisions'
`blank()` or by the reviews written in the fixtures.
"""

from __future__ import annotations

import copy
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ytstudio import narrative, script  # noqa: E402
from ytstudio.harness import enum_violations, missing_fields, type_errors  # noqa: E402

from tests.test_doc_narrative import CORPUS, FILES, rules_of, strengths  # noqa: E402
from tests.test_harness_decisions import blank  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "narrative"

#: Words that would name a look, a colour or a treatment (invariant 2).
LOOKS = re.compile(r"\b(colou?rs?|palette|typography|fonts?|2D|3D|cinematic|neon|pastel)\b", re.I)
#: A window written as a number (structure 10 and 13 make it the house's choice).
SECONDS = re.compile(r"\b\d+\s*(s|sec|secs|seconds)\b|\b\d+:\d\d\b|\b\d+\s*[-–]\s*\d+\s*s\b")


def problems(answer, schema) -> list[str]:
    """What the harness would ask again about, past which the stage records it."""
    return (missing_fields(answer, schema) + type_errors(answer, schema)
            + enum_violations(answer, schema))


def corpus_ids() -> dict[str, tuple[str, ...]]:
    """{rule id: its Stages: tag} across the four files."""
    return {r.id: r.stages for name in FILES for r in rules_of(CORPUS / name)}


def every_text() -> list[str]:
    out = []
    for c in narrative.RUBRIC:
        out += [c.question, c.example, *c.levels.values()]
    return out


class Rubric(unittest.TestCase):
    def test_nine_criteria_with_the_named_keys(self):
        # AUTO-136: tension, rising stakes across sections, after reveal_setup;
        # AUTO-138 added voice, the narrator's register, and AUTO-135 devices,
        # after AUTO-136's rows, last
        self.assertEqual([c.key for c in narrative.RUBRIC],
                         ["train", "hook", "stakes", "open_loops", "reveal_setup",
                          "tension", "ending", "honesty", "voice", "devices"])
        self.assertEqual(narrative.KEYS, tuple(c.key for c in narrative.RUBRIC))

    def test_every_criterion_has_four_labelled_one_line_levels_and_an_example(self):
        self.assertEqual(narrative.LEVELS, ("Excellent", "Good", "Fair", "Poor"))
        for c in narrative.RUBRIC:
            with self.subTest(criterion=c.key):
                self.assertTrue(c.question.strip())
                self.assertEqual(tuple(c.levels), narrative.LEVELS)
                for level, line in c.levels.items():
                    self.assertTrue(line.strip(), f"{c.key} {level} has no text")
                    self.assertNotIn("\n", line, f"{c.key} {level} is not one line")
                self.assertTrue(c.example.strip(), f"{c.key} has no worked example")

    def test_every_criterion_cites_rules_that_exist_and_bear_on_script_or_qa(self):
        known = corpus_ids()
        for c in narrative.RUBRIC:
            with self.subTest(criterion=c.key):
                self.assertTrue(c.rules, f"{c.key} cites no rule")
                for rid in c.rules:
                    self.assertIn(rid, known, f"{c.key} cites {rid}, which is no corpus rule")
                    self.assertTrue({"script", "qa"} & set(known[rid]),
                                    f"{rid} is not tagged for script or qa")

    def test_the_strength_is_the_readme_word_for_the_lead_rule(self):
        table = strengths()
        for c in narrative.RUBRIC:
            with self.subTest(criterion=c.key):
                self.assertIn(c.strength, ("peer-reviewed", "platform", "practitioner", "house"))
                self.assertEqual(c.strength, table[c.rules[0]], f"{c.key} leads with {c.rules[0]}")

    def test_no_score_no_seconds_window_and_no_look(self):
        for text in every_text():
            with self.subTest(text=text[:60]):
                self.assertIsNone(SECONDS.search(text), text)
                self.assertIsNone(LOOKS.search(text), text)
                self.assertNotRegex(text, r"\b(1|0)\s*[-–]\s*(10|100)\b")

    def test_the_hook_window_is_a_parameter_in_words(self):
        hook = narrative.criterion("hook")
        self.assertIn("{window}", hook.question + "".join(hook.levels.values()))
        text = narrative.rubric_text("reviewer", keys=["hook"])
        self.assertIn(narrative.OPENING_WINDOW, text)
        self.assertNotIn("{window}", text)
        self.assertIn("the first beat", narrative.rubric_text("writer", keys=["hook"],
                                                              window="the first beat"))

    def test_stakes_allows_a_film_that_withholds_the_danger_as_its_twist(self):
        # attention 8: state the risk first "whenever possible"; withhold it
        # only when a twist is the point, and the Check exempts a declared
        # twist. The grades below Good must not catch that film.
        stakes = narrative.criterion("stakes")
        for text in (stakes.question, stakes.levels["Fair"], stakes.levels["Poor"]):
            with self.subTest(text=text[:40]):
                self.assertIn("twist", text)

    def test_the_hook_levels_are_no_stronger_than_structure_10_and_6(self):
        # structure 10: the key noun is spoken *or* on screen; structure 6
        # restates the promise early, so a paraphrase of it is not Poor.
        hook = narrative.criterion("hook")
        self.assertIn("on screen or in words", hook.levels["Excellent"])
        self.assertIn("only restates the title", hook.levels["Poor"])
        self.assertIn("no question beyond it", hook.levels["Poor"])

    def test_a_section_that_only_sets_up_the_next_is_below_good(self):
        # structure 20: a section that only sets up a later one is cut or merged.
        reveal = narrative.criterion("reveal_setup")
        self.assertNotIn("only sets up", reveal.levels["Excellent"] + reveal.levels["Good"])
        self.assertIn("only sets up", reveal.levels["Fair"])

    def test_the_ending_grades_the_return_to_the_viewer_s_scale(self):
        # structure 34, cited by the question: a large subject comes back to
        # the viewer's scale; the writer's "Aim for" is the Excellent line.
        ending = narrative.criterion("ending")
        self.assertIn("viewer's scale", ending.levels["Excellent"])
        self.assertTrue(any("viewer's scale" in ending.levels[level]
                            for level in ("Good", "Fair", "Poor")))
        self.assertIn("viewer's scale", narrative.rubric_text("writer", keys=["ending"]))

    def test_an_unknown_criterion_is_refused_by_name(self):
        with self.assertRaises(KeyError):
            narrative.criterion("pacing")
        with self.assertRaises(KeyError):
            narrative.rubric_text("writer", keys=["pacing"])


class Text(unittest.TestCase):
    def test_rubric_text_names_every_key_and_says_longer_is_not_better(self):
        for audience in narrative.AUDIENCES:
            with self.subTest(audience=audience):
                text = narrative.rubric_text(audience)
                for key in narrative.KEYS:
                    self.assertIn(key, text)
                self.assertIn("longer is not better", text.lower())
                self.assertIsNone(re.search(r"\bPass\b", text))

    def test_the_reviewer_reads_every_level_the_writer_the_top_one(self):
        reviewer = narrative.rubric_text("reviewer")
        writer = narrative.rubric_text("writer")
        for c in narrative.RUBRIC:
            for level in narrative.LEVELS:
                self.assertIn(c.levels[level].replace("{window}", narrative.OPENING_WINDOW),
                              reviewer)
            self.assertIn(c.levels["Excellent"].replace("{window}", narrative.OPENING_WINDOW),
                          writer)
            self.assertNotIn(c.levels["Poor"].replace("{window}", narrative.OPENING_WINDOW),
                             writer)
        with self.assertRaises(ValueError):
            narrative.rubric_text("viewer")

    def test_rubric_text_keeps_only_the_rows_asked_for(self):
        text = narrative.rubric_text("reviewer", keys=["train", "ending"])
        self.assertIn("train", text)
        self.assertNotIn("reveal_setup", text)

    def test_the_reviewer_role_tags_strength_and_states_the_two_rules(self):
        role = narrative.reviewer_role()
        for c in narrative.RUBRIC:
            self.assertRegex(role, rf"{c.key}\b[^\n]*\[{re.escape(c.strength)}\]")
        self.assertIn("longer is not better", role.lower())
        self.assertIn("a Poor on a house-strength criterion yields consider, never reject", role)
        for verdict in narrative.VERDICTS:
            self.assertIn(verdict, role)
        self.assertIsNone(re.search(r"\bPass\b", role))

    def test_the_role_grades_absolutely_and_explains_the_revision_round(self):
        # The grades are absolute (judging 7); the one comparison a delta
        # schema asks for is `previous`, and the role says how to fill it.
        role = narrative.reviewer_role()
        self.assertNotIn("Judge each criterion on its own, absolutely, never against another draft",
                         role)
        self.assertIn("`previous`", role)
        for word in ("better", "same", "worse", "resolved", "persists", "regressed"):
            self.assertIn(word, role)


class Schema(unittest.TestCase):
    def test_the_grades_are_the_four_levels_for_every_key(self):
        grades = narrative.REVIEW_SCHEMA["properties"]["grades"]
        self.assertEqual(sorted(grades["required"]), sorted(narrative.KEYS))
        for key in narrative.KEYS:
            self.assertEqual(grades["properties"][key]["enum"], list(narrative.LEVELS))

    def test_the_verdicts_are_reject_consider_recommend(self):
        props = narrative.REVIEW_SCHEMA["properties"]
        self.assertEqual(props["verdict"]["enum"], ["reject", "consider", "recommend"])
        self.assertEqual(narrative.VERDICTS, ("reject", "consider", "recommend"))
        flag = props["flags"]["items"]
        self.assertEqual(flag["properties"]["severity"]["enum"], ["blocker", "important", "minor"])
        self.assertEqual(flag["properties"]["criterion"]["enum"], list(narrative.KEYS))
        for field in ("n", "section", "criterion", "problem", "severity", "fix"):
            self.assertIn(field, flag["required"])

    def test_a_flag_quotes_its_words_and_may_name_the_line_it_contradicts(self):
        # AUTO-112: the words a flag is about, copied exactly, and for a
        # contradiction the other line too
        flag = narrative.REVIEW_SCHEMA["properties"]["flags"]["items"]
        self.assertIn("quote", flag["required"])
        self.assertEqual(flag["properties"]["quote"]["type"], "string")
        self.assertIn("exactly", flag["properties"]["quote"]["description"])
        other = flag["properties"]["conflicts_with"]
        self.assertNotIn("conflicts_with", flag["required"])
        self.assertEqual(other["type"], "object")
        self.assertEqual(sorted(other["required"]), ["n", "quote"])
        self.assertEqual(other["properties"]["n"]["type"], "integer")
        self.assertIn("exactly", other["properties"]["quote"]["description"])
        # a revision's schema, with its own level order, carries them
        later = narrative.review_schema(delta=True, seed=2)["properties"]["flags"]["items"]
        self.assertIn("quote", later["required"])
        self.assertEqual(later["properties"]["conflicts_with"], other)
        # the plan has no narration to quote yet
        self.assertNotIn("quote", json.dumps(narrative.PLAN_REVIEW_SCHEMA))
        self.assertNotIn("conflicts_with", json.dumps(narrative.PLAN_REVIEW_SCHEMA))

    def test_the_story_role_says_a_flag_quotes_exactly_and_the_plan_role_does_not(self):
        role = " ".join(narrative.reviewer_role().split())
        self.assertIn("never paraphrase", role)
        self.assertIn("exactly", role)
        plan = " ".join(narrative.reviewer_role(plan=True).split())
        self.assertNotIn("paraphrase", plan)

    def test_coverage_shape_with_the_synopsis_before_the_grades(self):
        props = list(narrative.REVIEW_SCHEMA["properties"])
        for field in ("logline", "synopsis", "grades", "verdict", "flags",
                      "first_minute", "answered"):
            self.assertIn(field, props)
        self.assertLess(props.index("synopsis"), props.index("grades"))
        first = narrative.REVIEW_SCHEMA["properties"]["first_minute"]
        self.assertEqual(sorted(first["required"]), ["gap", "promise_match",
                                                     "question_and_stakes", "trigger"])
        answered = narrative.REVIEW_SCHEMA["properties"]["answered"]["items"]
        self.assertEqual(sorted(answered["properties"]), ["question_id", "shot_index"])
        self.assertNotIn("score", props)

    def test_a_revision_must_carry_the_previous_round(self):
        first = narrative.review_schema(delta=False)
        later = narrative.review_schema(delta=True)
        self.assertNotIn("previous", first["required"])
        self.assertIn("previous", later["required"])
        prev = later["properties"]["previous"]["properties"]
        self.assertEqual(prev["verdict"]["enum"], ["better", "same", "worse"])
        self.assertIn("status", prev["findings"]["items"]["required"])

    def test_grade_levels_is_a_permutation_that_differs_per_sample(self):
        self.assertEqual(narrative.grade_levels(0), narrative.LEVELS)
        orders = [narrative.grade_levels(seed) for seed in range(1, 4)]
        for order in orders:
            self.assertEqual(sorted(order), sorted(narrative.LEVELS))
        self.assertEqual(len(set(orders)), 3)
        self.assertEqual(len({o[0] for o in orders}), 3, "each sample leads with a different level")
        self.assertEqual(narrative.grade_levels(5), narrative.grade_levels(5))

    def test_a_seeded_schema_carries_that_order_and_leaves_the_constant_alone(self):
        seeded = narrative.review_schema(delta=False, seed=2)
        for key in narrative.KEYS:
            self.assertEqual(seeded["properties"]["grades"]["properties"][key]["enum"],
                             list(narrative.grade_levels(2)))
        self.assertEqual(narrative.REVIEW_SCHEMA["properties"]["grades"]["properties"]
                         ["train"]["enum"], list(narrative.LEVELS))
        self.assertIn("previous", narrative.review_schema(delta=True, seed=2)["required"])

    def test_the_house_window_reaches_the_schema_as_it_reaches_the_role(self):
        # One sample's role and schema use the same words for the hook's window.
        said = narrative.review_schema(delta=False, window="the first beat")
        hook = said["properties"]["grades"]["properties"]["hook"]["description"]
        self.assertIn("the first beat", hook)
        self.assertIn("the first beat", narrative.reviewer_role(window="the first beat"))
        seeded = narrative.review_schema(delta=True, seed=2, window="the first beat")
        self.assertIn("the first beat",
                      seeded["properties"]["grades"]["properties"]["hook"]["description"])
        self.assertIn(narrative.OPENING_WINDOW,
                      narrative.REVIEW_SCHEMA["properties"]["grades"]["properties"]["hook"]
                      ["description"])

    def test_a_blank_answer_validates_against_every_schema(self):
        for name, schema in (("review", narrative.REVIEW_SCHEMA),
                             ("review delta", narrative.review_schema(delta=True)),
                             ("review seeded", narrative.review_schema(delta=False, seed=3)),
                             ("plan review", narrative.PLAN_REVIEW_SCHEMA)):
            with self.subTest(schema=name):
                self.assertEqual(problems(blank(schema), schema), [])

    def test_the_plan_review_shape(self):
        props = narrative.PLAN_REVIEW_SCHEMA["properties"]
        self.assertEqual(props["verdict"]["enum"], ["accept", "revise", "reject"])
        for field in ("train_ok", "reorder_survives", "sections_advancing_none",
                      "payoff_concrete", "stakes_concrete", "questions_unanswered",
                      "problems"):
            self.assertIn(field, narrative.PLAN_REVIEW_SCHEMA["required"])
        self.assertEqual(sorted(props["problems"]["items"]["required"]),
                         ["fix", "problem", "section"])
        # AUTO-161: one judged field, never a grade, so "give no grade" holds
        required = narrative.PLAN_REVIEW_SCHEMA["required"]
        self.assertEqual(required.index("stakes_concrete"),
                         required.index("payoff_concrete") + 1)
        self.assertEqual(props["stakes_concrete"]["type"], "boolean")
        said = props["stakes_concrete"]["description"]
        for words in ("concrete consequence the viewer can picture",
                      "before the first section that explains a mechanism",
                      "withheld as its twist"):
            self.assertIn(words, said)
        self.assertNotIn("grades", props)


def _grounds(text: str) -> tuple[str, str]:
    """The reject sentence and the revise sentence of a plan-review text."""
    flat = " ".join(text.split())
    reject = flat[flat.index("Only the train"):]
    reject = reject[:reject.index(".") + 1]
    revise = flat[flat.index("Those four, and stakes"):]
    revise = revise[:revise.index(".") + 1]
    return reject, revise


class StakesGround(unittest.TestCase):
    """AUTO-161: a plan with no reason to care before its mechanism is
    grounds to revise, never to reject, in the role, the attack and the
    contradictions alike."""

    REJECT = ("Only the train, the order, a section that moves nothing and the payoff "
              "are grounds to reject.")
    #: AUTO-157 ends the sentence on the form, in the role and the attack alike
    REVISE = ("Those four, and stakes, are grounds to revise, and a form that does not "
              "follow from the subject is a ground to revise only, never to reject.")

    def check_text(self, text):
        flat = " ".join(text.split())
        reject, revise = _grounds(text)
        self.assertEqual(reject, self.REJECT)
        self.assertNotIn("stakes", reject)
        self.assertEqual(revise, self.REVISE)
        self.assertIn("withheld", flat)
        self.assertIn("twist", flat)
        # the old sentence that made stakes never a ground is gone
        self.assertNotIn("grounds to revise or reject", flat)
        self.assertNotIn("A weak stakes line", flat)
        return flat

    def test_the_plan_role_names_stakes_as_a_revise_ground_never_a_reject_ground(self):
        flat = self.check_text(narrative.reviewer_role(plan=True))
        self.assertIn("an accept has stakes_concrete true", flat)
        # still no grade: one named field, not a level
        self.assertIn("give no grade", flat)

    def test_plan_attack_names_stakes_as_a_revise_ground_never_a_reject_ground(self):
        flat = self.check_text(script.PLAN_ATTACK)
        self.assertIn("its stakes (before the first section that explains a mechanism, "
                      "does the viewer hear a concrete consequence they can picture, or "
                      "does the plan say the danger is withheld as its twist?)", flat)
        # the system prompt and the user prompt of one call agree on the grounds
        self.assertEqual(_grounds(script.PLAN_ATTACK),
                         _grounds(narrative.reviewer_role(plan=True)))

    @staticmethod
    def accept(**change):
        return dict({"verdict": "accept", "train_ok": True, "reorder_survives": False,
                     "sections_advancing_none": [], "payoff_concrete": True,
                     "stakes_concrete": True, "questions_unanswered": [],
                     "problems": []}, **change)

    def test_an_accept_without_stakes_is_a_contradiction(self):
        lines = narrative.check_plan_review(self.accept(stakes_concrete=False))
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("stakes_concrete", lines[0])
        self.assertEqual(narrative.check_plan_review(self.accept()), [])
        old = self.accept()
        del old["stakes_concrete"]       # an old stub or cached answer reads as today
        self.assertEqual(narrative.check_plan_review(old), [])

    def test_a_reject_on_stakes_alone_is_a_contradiction(self):
        problem = [{"section": 1, "problem": "no reason to care", "fix": "open on 1989"}]
        alone = self.accept(verdict="reject", stakes_concrete=False, problems=problem)
        lines = narrative.check_plan_review(alone)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("stakes alone is grounds to revise", lines[0])
        for ground in ({"reorder_survives": True}, {"train_ok": False},
                       {"payoff_concrete": False}, {"sections_advancing_none": [2]}):
            with self.subTest(ground=ground):
                self.assertEqual(narrative.check_plan_review(dict(alone, **ground)), [])
        # a revise on stakes alone is what the role asks for
        self.assertEqual(narrative.check_plan_review(dict(alone, verdict="revise")), [])
        missing = dict(alone)
        del missing["stakes_concrete"]
        self.assertEqual(narrative.check_plan_review(missing), [])


class Form(unittest.TestCase):
    """AUTO-157: the film's form (its macro shape) is read by the plan review
    alone. It is a plan-only row, outside RUBRIC, so the story review's
    grades, its medians and qa's rubric are what they were; a form that does
    not follow from the subject is a ground to revise only."""

    @staticmethod
    def clean(**change):
        return dict({"verdict": "revise", "train_ok": True, "reorder_survives": False,
                     "sections_advancing_none": [], "payoff_concrete": True,
                     "stakes_concrete": True, "questions_unanswered": [],
                     "form_fits": False, "form_problem": "a mystery any topic could wear",
                     "problems": []}, **change)

    def test_the_rubric_and_its_keys_are_unchanged(self):
        self.assertNotIn("form", narrative.KEYS)
        self.assertNotIn("form", [c.key for c in narrative.RUBRIC])
        self.assertEqual(len(narrative.RUBRIC), 10)
        self.assertNotIn("form", narrative.REVIEW_SCHEMA["properties"]["grades"]["required"])
        self.assertNotIn("form", narrative.REVIEW_SCHEMA["properties"]["flags"]["items"]
                         ["properties"]["criterion"]["enum"])
        from ytstudio import qa
        self.assertNotIn("form", qa.STORY_RUBRIC)

    def test_the_form_row_is_plan_only_practitioner_and_drawn_from_structure_20(self):
        self.assertEqual([c.key for c in narrative.PLAN_ONLY], ["form"])
        form = narrative.criterion("form")
        self.assertIs(form, narrative.PLAN_ONLY[0])
        self.assertEqual(tuple(form.levels), narrative.LEVELS)
        self.assertEqual(form.strength, "practitioner")
        self.assertEqual(form.rules, ("structure 20",))
        self.assertEqual(strengths()[form.rules[0]], form.strength)
        self.assertIn("script", corpus_ids()["structure 20"])
        for level, line in form.levels.items():
            self.assertTrue(line.strip(), level)
            self.assertNotIn("\n", line)
        self.assertIn("any topic could wear", form.levels["Poor"])
        self.assertIn("mysteries are engaging", form.example)
        self.assertIn("Biermann", form.example)

    def test_only_the_plan_review_reads_the_form_row(self):
        self.assertNotIn(". form [", narrative.rubric_text("reviewer"))
        self.assertNotIn("form:", narrative.rubric_text("writer"))
        self.assertIn("form", script.PLAN_CRITERIA)
        self.assertEqual(script.PLAN_CRITERIA[-1], "form")
        text = narrative.rubric_text("reviewer", keys=script.PLAN_CRITERIA)
        self.assertIn(". form [practitioner]", text)
        for line in narrative.criterion("form").levels.values():
            self.assertIn(line, text)

    def test_the_plan_review_schema_requires_form_fits_and_form_problem(self):
        props = narrative.PLAN_REVIEW_SCHEMA["properties"]
        for name in ("form_fits", "form_problem"):
            self.assertIn(name, narrative.PLAN_REVIEW_SCHEMA["required"])
            self.assertIn(name, narrative.plan_review_schema(True)["required"])
        self.assertEqual(props["form_fits"]["type"], "boolean")
        self.assertEqual(props["form_problem"]["type"], "string")
        self.assertIn("true when the plan names no form", props["form_fits"]["description"])
        # a form repeat is not a repeats ground
        self.assertEqual(narrative.REPEATS_PROPERTY["items"]["properties"]["dimension"]["enum"],
                         ["opening", "structure", "closing"])
        self.assertEqual(problems(blank(narrative.PLAN_REVIEW_SCHEMA),
                                  narrative.PLAN_REVIEW_SCHEMA), [])

    def test_check_plan_review_names_the_two_form_contradictions(self):
        accept = self.clean(verdict="accept")
        lines = narrative.check_plan_review(accept)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("form_fits is false", lines[0])
        empty = self.clean(form_problem="  ",
                           problems=[{"section": 0, "problem": "p", "fix": "f"}])
        lines = narrative.check_plan_review(empty)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("form_problem", lines[0])
        # a revise whose form field names the fault has named a problem
        self.assertEqual(narrative.check_plan_review(self.clean()), [])
        self.assertEqual(narrative.check_plan_review(self.clean(verdict="accept",
                                                                form_fits=True,
                                                                form_problem="")), [])
        # an old stub or cached answer carries no form_fits and reads as before
        old = self.clean(verdict="accept")
        del old["form_fits"], old["form_problem"]
        self.assertEqual(narrative.check_plan_review(old), [])

    def test_a_reject_on_the_form_alone_is_a_contradiction(self):
        problem = [{"section": 0, "problem": "the form fits no subject", "fix": "f"}]
        alone = self.clean(verdict="reject", problems=problem)
        lines = narrative.check_plan_review(alone)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("form alone is grounds to revise", lines[0])
        self.assertEqual(narrative.check_plan_review(dict(alone, reorder_survives=True)), [])

    def test_form_only_revise_is_true_only_when_every_other_ground_is_clean(self):
        self.assertTrue(narrative.form_only_revise(self.clean()))
        self.assertTrue(narrative.form_only_revise(dict(self.clean(), repeats=[])))
        for change in ({"verdict": "accept"}, {"verdict": "reject"}, {"form_fits": True},
                       {"train_ok": False}, {"reorder_survives": True},
                       {"sections_advancing_none": [2]}, {"questions_unanswered": ["Q1"]},
                       {"payoff_concrete": False}, {"stakes_concrete": False},
                       {"repeats": [{"film": "a", "dimension": "closing", "problem": "p"}]}):
            with self.subTest(change=change):
                self.assertFalse(narrative.form_only_revise(self.clean(**change)))
        old = self.clean()
        del old["form_fits"]
        self.assertFalse(narrative.form_only_revise(old))
        self.assertFalse(narrative.form_only_revise(None))

    def test_form_only_revise_needs_a_named_settled_problem_about_the_form(self):
        # A reading that still contradicts itself after the again ask, or
        # that names no form problem, has not said the form is all that is
        # wrong; nor has one whose problems name another fault no field
        # mirrors (an ending, a rehook), which would stop without the form.
        self.assertFalse(narrative.form_only_revise(self.clean(form_problem=" ")))
        self.assertFalse(narrative.form_only_revise(
            self.clean(), unsettled=["form_fits is false and form_problem is empty"]))
        self.assertTrue(narrative.form_only_revise(self.clean(), unsettled=[]))
        about_form = {"section": 0, "problem": "the form is a label any topic could wear",
                      "fix": "follow the evidence in the order Biermann had it"}
        named = {"section": 0, "problem": "a mystery nothing in this subject keeps back",
                 "fix": "f"}
        mirrored = {"section": 0, "problem": "a mystery any topic could wear", "fix": "f"}
        other = {"section": 3, "problem": "the rehook repeats section 2's question",
                 "fix": "ask what the photographs could not show"}
        ending = {"section": 0, "problem": "the ending recaps instead of paying off",
                  "fix": "end on the tail"}
        self.assertTrue(narrative.form_only_revise(self.clean(problems=[about_form])))
        self.assertTrue(narrative.form_only_revise(self.clean(problems=[named]),
                                                   form="mystery"))
        self.assertFalse(narrative.form_only_revise(self.clean(problems=[named])))
        self.assertTrue(narrative.form_only_revise(self.clean(problems=[mirrored])))
        for fault in (other, ending):
            with self.subTest(problem=fault["problem"]):
                self.assertFalse(narrative.form_only_revise(
                    self.clean(problems=[about_form, fault]), form="mystery"))

    def test_the_plan_role_names_the_form_a_ground_to_revise_only(self):
        for channel in (False, True):
            with self.subTest(channel=channel):
                role = " ".join(narrative.reviewer_role(plan=True, channel=channel).split())
                self.assertIn("a form that does not follow from the subject is a ground to "
                              "revise only", role)
                self.assertIn("give no grade", role)
                self.assertIn("form_fits", role)
        self.assertNotIn("form that does not follow", narrative.reviewer_role())


def review(verdict="recommend", grades=None, flags=()) -> dict:
    g = {k: "Good" for k in narrative.KEYS}
    g.update(grades or {})
    return {"logline": "l", "synopsis": "s", "grades": g, "verdict": verdict,
            "flags": list(flags),
            "first_minute": {"promise_match": "yes", "question_and_stakes": "yes"}}


def flag(criterion="ending", **kw) -> dict:
    return dict({"n": 0, "section": 4, "criterion": criterion, "problem": "p",
                 "severity": "important", "fix": "f"}, **kw)


class CheckReview(unittest.TestCase):
    def test_a_clean_review_has_no_problem(self):
        self.assertEqual(narrative.check_review(review()), [])
        self.assertEqual(narrative.check_review(review("consider", flags=[flag()])), [])
        self.assertEqual(narrative.check_review(review("reject", {"train": "Poor"})), [])

    def test_a_recommend_carrying_a_poor_is_refused_by_name(self):
        found = narrative.check_review(review("recommend", {"stakes": "Poor"}))
        self.assertEqual(len(found), 1)
        self.assertIn("recommend", found[0])
        self.assertIn("stakes", found[0])

    def test_a_consider_with_nothing_to_fix_is_refused(self):
        found = narrative.check_review(review("consider"))
        self.assertEqual(len(found), 1)
        self.assertIn("consider", found[0])
        self.assertIn("flag", found[0])

    def test_a_recommend_carrying_a_blocker_is_refused_by_name(self):
        # The verdict's own description: recommend means no Poor and no blocker.
        found = narrative.check_review(review("recommend",
                                              flags=[flag("hook", severity="blocker")]))
        self.assertEqual(len(found), 1, found)
        self.assertIn("recommend", found[0])
        self.assertIn("blocker", found[0])
        self.assertIn("hook", found[0])
        self.assertEqual(narrative.check_review(review("recommend",
                                                       flags=[flag(severity="minor")])), [])

    def test_a_flag_on_no_criterion_is_refused_by_name(self):
        found = narrative.check_review(review("consider", flags=[flag("pacing")]))
        self.assertEqual(len(found), 1)
        self.assertIn("pacing", found[0])

    def test_each_illegal_shape_is_named_once(self):
        found = narrative.check_review(review("recommend", {"hook": "Poor", "ending": "Poor"},
                                              [flag("vibes")]))
        self.assertEqual(len(found), 2, found)
        self.assertIn("hook", found[0])
        self.assertIn("ending", found[0])

    def test_a_shapeless_answer_is_the_schema_check_s_business(self):
        # Missing fields are the harness's to ask again about; this check
        # reads what is there and does not raise on what is not.
        self.assertEqual(narrative.check_review({}), [])
        self.assertEqual(narrative.check_review({"verdict": "consider", "flags": None}),
                         ["a consider names problems to fix before voicing: it carries no flag"])


class Tension(unittest.TestCase):
    """AUTO-136: a row grades rising stakes across the sections."""

    def test_tension_follows_reveal_setup_and_leads_with_attention_9(self):
        keys = list(narrative.KEYS)
        self.assertEqual(keys.index("tension"), keys.index("reveal_setup") + 1)
        tension = narrative.criterion("tension")
        self.assertEqual(tension.strength, "practitioner")
        self.assertEqual(tension.rules, ("attention 9", "structure 23", "structure 25",
                                         "structure 26"))
        self.assertIn("re-hook", tension.question)
        # the final section resolves; it is not asked to raise the stakes
        self.assertIn("final section resolves", tension.question)

    def test_the_reviewer_reads_its_four_levels_and_its_lead_rule(self):
        text = narrative.rubric_text("reviewer", keys=["tension"])
        tension = narrative.criterion("tension")
        for level in narrative.LEVELS:
            self.assertIn(tension.levels[level], text)
        self.assertIn("attention 9", text)
        self.assertIn("rehook", tension.levels["Excellent"])
        self.assertIn("plateaus", tension.levels["Good"])
        self.assertIn("flat", tension.levels["Fair"])
        self.assertIn("list", tension.levels["Poor"])

    def test_a_poor_tension_is_never_a_reject_ground(self):
        # The four criteria that decide a film stay four; tension is not one.
        role = " ".join(narrative.reviewer_role().split())
        self.assertIn("(train, stakes, open_loops, ending)", role)
        deciding = role[role.index("one of the four"):role.index("is Poor")]
        self.assertNotIn("tension", deciding)
        self.assertNotIn("tension", narrative.reviewer_role(plan=True))


class Devices(unittest.TestCase):
    """AUTO-135: the analogies, visual metaphors, guesses and thought
    experiments a film uses to get the viewer into a mechanism, graded on
    whether each maps correctly; never a quota."""

    def test_devices_leads_with_attention_2_a_house_rule(self):
        devices = narrative.criterion("devices")
        self.assertEqual(devices.rules[0], "attention 2")
        self.assertEqual(devices.rules, ("attention 2", "direction 24"))
        self.assertEqual(strengths()["attention 2"], "house")
        self.assertEqual(devices.strength, "house")

    def test_one_row_grades_the_guess_and_the_anchor_visual_is_not_cited(self):
        self.assertNotIn("attention 2", narrative.criterion("reveal_setup").rules)
        self.assertEqual(narrative.criterion("reveal_setup").rules,
                         ("structure 22", "structure 20"))
        self.assertNotIn("direction 4", narrative.criterion("devices").rules)
        graders = [c.key for c in narrative.RUBRIC if "attention 2" in c.rules]
        self.assertEqual(graders, ["devices"])

    def test_the_reviewer_role_tags_devices_house(self):
        role = narrative.reviewer_role()
        self.assertRegex(role, r"\. devices \[house\]")
        # HOUSE_POOR finally governs a row: the role still says it
        self.assertIn(narrative.HOUSE_POOR, role)

    def test_the_review_schema_requires_the_grade_and_flags_may_name_it(self):
        props = narrative.REVIEW_SCHEMA["properties"]
        self.assertIn("devices", props["grades"]["required"])
        self.assertEqual(props["grades"]["properties"]["devices"]["enum"],
                         list(narrative.LEVELS))
        self.assertIn("devices", props["flags"]["items"]["properties"]["criterion"]["enum"])
        seeded = narrative.review_schema(delta=True, seed=3)
        self.assertIn("devices", seeded["properties"]["grades"]["required"])

    def test_the_plan_review_reads_the_row(self):
        text = narrative.rubric_text("reviewer", keys=script.PLAN_CRITERIA)
        self.assertIn(". devices [house]", text)
        self.assertIn("devices", script.PLAN_CRITERIA)
        # the plan review still gives no grade, and its schema is unchanged
        self.assertNotIn("grades", narrative.PLAN_REVIEW_SCHEMA["properties"])

    def test_the_question_names_mapping_breaking_point_guess_and_shown_directly(self):
        devices = narrative.criterion("devices")
        q = devices.question
        for words in ("map", "where it breaks", "guess", "before the main reveal",
                      "scored by that reveal", "showing it directly", "something false"):
            self.assertIn(words, q)
        # a fault honesty already grades is not graded twice
        self.assertIn("unframed imagining", q)
        self.assertIn("is honesty's, not this row's", q)
        self.assertIn("shown directly", devices.levels["Excellent"])
        self.assertIn("decoration that maps nothing", devices.levels["Fair"])
        self.assertIn("something false", devices.levels["Poor"])
        self.assertIn("windsock", devices.example)
        self.assertIn("smoke behind a train", devices.example)

    def test_no_level_asks_for_a_count_of_devices(self):
        # A missing device is never a fault: a mechanism shown directly
        # reaches Excellent with none.
        for level, line in narrative.criterion("devices").levels.items():
            with self.subTest(level=level):
                self.assertIsNone(re.search(r"\b(at least|every section|one per)\b", line),
                                  line)

    def test_a_poor_devices_alone_is_a_consider_never_a_recommend(self):
        grades = {k: "Good" for k in narrative.KEYS}
        grades.update(devices="Poor", honesty="Excellent")
        said = review("consider", grades, [flag("devices", section=3, n=7, quote="q")])
        said["first_minute"].update(trigger="question", gap="g")
        self.assertEqual(problems(said, narrative.REVIEW_SCHEMA), [])
        self.assertEqual(narrative.check_review(said), [])
        found = narrative.check_review(dict(said, verdict="recommend"))
        self.assertEqual(len(found), 1, found)
        self.assertIn("devices", found[0])
        # devices is not one of the four criteria that decide a film
        role = " ".join(narrative.reviewer_role().split())
        deciding = role[role.index("one of the four"):role.index("is Poor")]
        self.assertNotIn("devices", deciding)


class Trigger(unittest.TestCase):
    """AUTO-136: the opening's curiosity trigger (attention 1)."""

    def test_the_vocabulary_lives_here_and_heard_adds_none_and_not_heard(self):
        self.assertEqual(narrative.HOOK_TRIGGERS,
                         ("question", "paradox", "unexpected_observation",
                          "unresolved_outcome", "someone_knows"))
        self.assertEqual(narrative.HEARD_TRIGGERS,
                         narrative.HOOK_TRIGGERS + ("none", "not_heard"))

    def test_the_hook_question_names_the_triggers_and_cites_attention_1(self):
        hook = narrative.criterion("hook")
        for words in ("curiosity trigger", "a question", "a paradox",
                      "an unexpected observation", "an unresolved outcome",
                      "something someone knows that the viewer does not"):
            self.assertIn(words, hook.question)
        self.assertIn("the trigger is concrete within the first three sentences",
                      hook.levels["Excellent"])
        self.assertIn("or the first three sentences hold no trigger", hook.levels["Fair"])
        self.assertIn("attention 1", hook.rules)
        self.assertEqual(hook.rules[0], "structure 10")      # the strength stays platform
        self.assertEqual(hook.strength, "platform")

    def test_the_first_minute_names_the_trigger_and_the_gap_it_heard(self):
        first = narrative.REVIEW_SCHEMA["properties"]["first_minute"]
        for name in ("promise_match", "question_and_stakes", "trigger", "gap"):
            self.assertIn(name, first["required"])
        self.assertEqual(first["properties"]["trigger"]["enum"],
                         list(narrative.HEARD_TRIGGERS))
        self.assertIn("not_heard", first["properties"]["trigger"]["description"])
        self.assertIn("first three spoken sentences",
                      first["properties"]["trigger"]["description"])
        role = " ".join(narrative.reviewer_role().split())
        self.assertIn("trigger", role[role.index("Read the first minute twice"):])

    def test_a_heard_none_contradicts_a_good_hook(self):
        def heard(trigger, hook):
            said = review("recommend", {"hook": hook})
            said["first_minute"].update(trigger=trigger, gap="")
            return narrative.check_review(said)
        for hook in ("Excellent", "Good"):
            found = heard("none", hook)
            self.assertEqual(len(found), 1, found)
            self.assertIn("none", found[0])
            self.assertIn("hook", found[0])
        self.assertEqual(heard("none", "Fair"), [])
        # a plan-only review hears nothing, and may grade the hook from the plan
        self.assertEqual(heard("not_heard", "Excellent"), [])
        self.assertEqual(heard("paradox", "Excellent"), [])


#: Every mention of humour a voice level may make: humour that is there and
#: lands on the point, a joke that serves no claim, and humour at someone's
#: expense. None is about humour that is absent.
HUMOUR = re.compile(r"\b(humou?r\w*|jokes?|funny|asides?|wit|witty)\b", re.I)
HUMOUR_ALLOWED = ("any humour lands on the point being made",
                  "a joke or aside that serves no claim",
                  "humour at the expense of the viewer or of people the subject harmed")


class Voice(unittest.TestCase):
    """AUTO-138: the narrator's register, graded before voice is paid for."""

    def test_voice_follows_honesty_and_leads_with_attention_25_at_its_readme_strength(self):
        # AUTO-135's devices row comes after it
        self.assertEqual(list(narrative.KEYS[-3:]), ["honesty", "voice", "devices"])
        voice = narrative.criterion("voice")
        self.assertEqual(voice.rules, ("attention 25", "attention 18", "direction 24",
                                       "attention 21"))
        self.assertEqual(voice.strength, strengths()["attention 25"])
        self.assertEqual(voice.strength, "peer-reviewed")
        for words in ("alongside the viewer", "asking aloud the question they are about "
                      "to ask", "flagging the step most likely to lose them",
                      "a discovery land as a discovery", "does any humour serve the point"):
            self.assertIn(words, voice.question)
        self.assertIn("Comet tails are oriented anti-sunward", voice.example)
        self.assertIn("A tail that leads? That looks backwards.", voice.example)

    def test_absent_humour_never_lowers_a_grade(self):
        # The levels grade humour only when there is some: no level asks for
        # a joke, and every mention is one of the three allowed phrasings.
        voice = narrative.criterion("voice")
        for level, line in voice.levels.items():
            with self.subTest(level=level):
                rest = line
                for allowed in HUMOUR_ALLOWED:
                    rest = rest.replace(allowed, "")
                self.assertIsNone(HUMOUR.search(rest), f"{level}: {line}")
                self.assertNotRegex(line, r"(?i)\b(no|without|lacks?|never|not)\s+"
                                          r"(any\s+)?(humou?r|jokes?|funny)|humou?rless")
        self.assertIn("any humour", voice.levels["Excellent"])
        self.assertNotRegex(voice.levels["Good"], HUMOUR)
        self.assertIn("a joke or aside that serves no claim", voice.levels["Fair"])
        for c in narrative.RUBRIC:
            if c.key != "voice":
                with self.subTest(criterion=c.key):
                    self.assertIsNone(HUMOUR.search(" ".join(c.levels.values())))

    def test_a_poor_voice_is_a_consider_and_never_flagged_on_shot_0(self):
        role = " ".join(narrative.reviewer_role().split())
        self.assertIn("a Poor voice yields consider, never reject on its own", role)
        self.assertIn("A voice flag names the shot whose line reads as a lecture and "
                      "quotes it; never flag voice on shot 0.", role)
        # the four criteria that decide a film are unchanged
        self.assertIn("(train, stakes, open_loops, ending)", role)
        deciding = role[role.index("one of the four"):role.index("is Poor")]
        self.assertNotIn("voice", deciding)
        self.assertNotIn("voice", narrative.reviewer_role(plan=True))

    def test_the_writer_reads_the_voice_line_and_the_plan_review_does_not(self):
        voice = narrative.criterion("voice")
        writer = narrative.rubric_text("writer")
        self.assertIn(f"voice: {voice.question} Aim for: {voice.levels['Excellent']}", writer)
        self.assertIn("humour", writer)
        self.assertNotIn("voice", script.PLAN_CRITERIA)
        self.assertNotIn("voice", narrative.rubric_text("reviewer", keys=script.PLAN_CRITERIA))

    def test_check_review_holds_a_voice_flag_to_a_named_shot(self):
        said = "Comets are observed to move away from the Sun with the tail leading."
        on_shot = flag("voice", n=3, section=1, quote=said)
        self.assertEqual(narrative.check_review(review("consider", {"voice": "Fair"},
                                                       [on_shot])), [])
        self.assertEqual(script.Screenwriter._sample_problems(
            review("consider", {"voice": "Fair"}, [on_shot]), narrative.REVIEW_SCHEMA,
            {1: "a", 2: "b", 3: said}, {1: [1, 2, 3]}), [])
        # a recommend grading voice Poor breaks the sample like any Poor
        found = narrative.check_review(review("recommend", {"voice": "Poor"}))
        self.assertEqual(len(found), 1, found)
        self.assertIn("voice", found[0])
        # a voice flag on shot 0, for a section or the whole script, is asked again
        for section in (1, 0):
            with self.subTest(section=section):
                found = narrative.check_review(review(
                    "consider", {"voice": "Fair"}, [flag("voice", n=0, section=section)]))
                self.assertEqual(len(found), 1, found)
                self.assertIn("voice", found[0])
                self.assertIn("shot 0", found[0])
        # another criterion's flag on shot 0 is still a section's or the plan's
        self.assertEqual(narrative.check_review(review("consider", flags=[flag("ending")])),
                         [])

    def test_a_plan_read_before_any_line_may_carry_voice_on_shot_0(self):
        # a calibration case with no script has no line to name: a voice
        # flag can only sit on shot 0, and spending the -again call on it
        # would make the case not_evidenced for the prompt's own shape
        plan_only = review("consider", {"voice": "Fair"}, [flag("voice", n=0, section=2)])
        plan_only["first_minute"]["trigger"] = "not_heard"
        self.assertEqual(narrative.check_review(plan_only), [])
        self.assertEqual(script.Screenwriter._sample_problems(
            plan_only, narrative.REVIEW_SCHEMA, {}, {}), [])
        # told there is no draft, the shape decides nothing else
        heard = copy.deepcopy(plan_only)
        heard["first_minute"]["trigger"] = "question"
        self.assertEqual(narrative.check_review(heard, drafted=False), [])
        self.assertEqual(len(narrative.check_review(heard)), 1)
        # a drafted script that says not_heard is still held to a named shot
        found = script.Screenwriter._sample_problems(
            plan_only, narrative.REVIEW_SCHEMA, {1: "Comets fly tail first."}, {1: [1]})
        self.assertTrue(any("voice" in p and "shot 0" in p for p in found), found)


# --------------------------------------------------------------------------- #
# the seeded calibration set
# --------------------------------------------------------------------------- #

def cases() -> dict[str, Path]:
    return {p.name: p for p in sorted(FIXTURES.iterdir()) if p.is_dir()}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def at(doc: dict, dotted: str):
    """The value a dotted path names: `plan.sections.1.connective`."""
    value = doc
    for part in dotted.split("."):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


#: The faults the card names that the story review catches, each with the
#: criterion it fails on.
SEEDED = {"list-plan": "train", "topic-train": "train", "empty-payoff": "ending",
          "and-chain": "train", "reveal-first": "reveal_setup",
          "recap-ending": "ending", "hook-enumerates": "hook",
          "flat-tension": "tension", "no-trigger": "hook", "lecture-voice": "voice",
          "bad-metaphor": "devices", "aside-joke": "voice", "no-stakes": "stakes"}

#: The cases that expect recommend: the good pair, and the good script whose
#: one joke makes the point (AUTO-159), which must not be graded down for it.
GOOD = ("good", "claim-joke")

#: The faults only the cold read catches: a premise a first-time viewer
#: could not restate, and terms a lay audience is never given. judging 14
#: puts them under the pre-voice checks that are not story checks, and the
#: stage files the cold read's mismatch as a flag of its own without
#: touching the story verdict, so the story review is not judged on them.
COLD_READ = ("no-premise", "jargon")

#: The cases with no script: the plan is the whole of what is read.
PLAN_ONLY = ("list-plan", "topic-train", "empty-payoff", "and-chain", "no-stakes")


class Fixtures(unittest.TestCase):
    def test_the_set_holds_every_seeded_fault_and_the_good_cases(self):
        names = cases()
        self.assertEqual(sorted(names), sorted([*SEEDED, *COLD_READ, *GOOD]))
        for name in GOOD:
            with self.subTest(case=name):
                good = names[name]
                self.assertTrue((good / "plan.json").is_file()
                                and (good / "script.json").is_file())
                expected = load(good / "expected.json")
                self.assertEqual(expected["verdict"], "recommend")
                self.assertNotIn("criterion", expected)
        for name, criterion in SEEDED.items():
            with self.subTest(case=name):
                expected = load(names[name] / "expected.json")
                self.assertEqual(expected["criterion"], criterion)
                self.assertIn(expected["verdict"], ("reject", "consider"))
                self.assertTrue(expected["why"].strip())
                self.assertNotIn("story", expected)

    def test_a_cold_read_case_holds_the_story_review_to_nothing(self):
        # The rubric has no criterion for these faults, so a case that named
        # one would fail a reviewer for following the rubric.
        for name in COLD_READ:
            expected = load(FIXTURES / name / "expected.json")
            with self.subTest(case=name):
                self.assertIs(expected["story"], False)
                self.assertNotIn("criterion", expected)
                self.assertNotIn("verdict", expected)
                self.assertTrue(expected["cold_read"])
                self.assertTrue(expected["why"].strip())
                self.assertIn("judging 14", expected["judging_10"])
                self.assertTrue((FIXTURES / name / "script.json").is_file())

    def test_every_plan_and_script_is_shaped_like_the_screenplay(self):
        for name, path in cases().items():
            plan = load(path / "plan.json")
            with self.subTest(case=name, file="plan.json"):
                self.assertEqual(missing_fields(plan, script.PLAN_SCHEMA), [])
                self.assertEqual(type_errors(plan, script.PLAN_SCHEMA), [])
            if (path / "script.json").is_file():
                screenplay = load(path / "script.json")
                schema = script.section_schema(False)
                for section in screenplay["sections"]:
                    with self.subTest(case=name, section=section["heading"]):
                        self.assertEqual(missing_fields({"shots": section["shots"]}, schema), [])

    def test_the_fault_is_where_the_case_says(self):
        good = {"plan": load(FIXTURES / "good" / "plan.json"),
                "script": load(FIXTURES / "good" / "script.json")}
        for name, path in cases().items():
            expected = load(path / "expected.json")
            if name in GOOD:
                self.assertNotIn("evidence", expected)
                continue
            docs = {"plan": load(path / "plan.json")}
            if (path / "script.json").is_file():
                docs["script"] = load(path / "script.json")
            evidence = expected["evidence"]
            with self.subTest(case=name, where=evidence["in"]):
                value = at(docs, evidence["in"])
                if evidence["in"].endswith(".connective"):
                    # A connective is one word: "and" must not match "band".
                    self.assertEqual(value, evidence["says"])
                elif evidence["says"]:
                    self.assertIn(evidence["says"].lower(), str(value).lower())
                else:
                    self.assertEqual(str(value).strip(), "")
                # The seeded change is the fault: the good pair differs there.
                self.assertNotEqual(at(good, evidence["in"]), value)
                if evidence["says"]:
                    self.assertNotIn(evidence["says"].lower(),
                                     str(at(good, evidence["in"])).lower())

    def test_the_example_review_flags_the_fault_where_it_sits(self):
        # A script path names a section and a shot; a plan section names its
        # section; a field of the whole plan is section 0.
        for name, criterion in SEEDED.items():
            path = FIXTURES / name
            expected = load(path / "expected.json")
            where = expected["evidence"]["in"].split(".")
            if where[0] == "script":
                section = int(where[2]) + 1
                n = load(path / "script.json")["sections"][int(where[2])]["shots"][int(where[4])]["n"]
            elif where[1] == "sections":
                section, n = int(where[2]) + 1, 0
            else:
                section, n = 0, 0
            on = [(f["section"], f["n"]) for f in expected["review"]["flags"]
                  if f["criterion"] == criterion]
            with self.subTest(case=name):
                self.assertIn((section, n), on)

    def test_a_case_held_below_reject_says_why_judging_10_is_not_its_bar(self):
        # judging 10 asks the calibration run for reject on every seeded case.
        # A case whose one fault is a Fair, or a Poor on a criterion that does
        # not decide a film, is a consider by judging 11 and 12; it records
        # that, and what the calibration fails the reviewer on instead.
        for name in SEEDED:
            expected = load(FIXTURES / name / "expected.json")
            with self.subTest(case=name):
                if expected["verdict"] == "reject":
                    self.assertNotIn("judging_10", expected)
                    continue
                note = expected.get("judging_10", "")
                self.assertIn("judging 10", note)
                self.assertIn("recommend", note)

    def test_a_plan_only_case_is_reviewed_from_its_own_plan(self):
        # No script ships with these cases, so the review reads the plan: its
        # first minute says so, and nothing in it is the good film's script.
        for name in PLAN_ONLY:
            path = FIXTURES / name
            self.assertFalse((path / "script.json").is_file())
            said = load(path / "expected.json")["review"]
            with self.subTest(case=name):
                for key in ("promise_match", "question_and_stakes"):
                    self.assertIn("plan", said["first_minute"][key])
        said = load(FIXTURES / "list-plan" / "expected.json")["review"]
        self.assertNotIn("Quebec", json.dumps(said))
        for key in ("hook", "stakes"):
            self.assertIn(said["grades"][key], ("Fair", "Poor"))
            self.assertIn(key, [f["criterion"] for f in said["flags"]])

    def test_a_topic_train_has_no_question_and_a_good_one_has(self):
        def asks(train):
            return "?" in train or bool(re.search(r"\b(whether|why|how)\b", train, re.I))
        self.assertFalse(asks(load(FIXTURES / "topic-train" / "plan.json")["train"]))
        self.assertTrue(asks(load(FIXTURES / "good" / "plan.json")["train"]))

    def test_the_rubric_can_express_each_failure(self):
        rank = {v: i for i, v in enumerate(narrative.VERDICTS)}
        for name, criterion in SEEDED.items():
            expected = load(FIXTURES / name / "expected.json")
            said = expected["review"]
            with self.subTest(case=name):
                self.assertEqual(problems(said, narrative.REVIEW_SCHEMA), [])
                self.assertEqual(narrative.check_review(said), [])
                self.assertIn(said["grades"][criterion], ("Fair", "Poor"))
                self.assertLessEqual(rank[said["verdict"]], rank[expected["verdict"]])
                self.assertIn(criterion, [f["criterion"] for f in said["flags"]])

    def test_a_plan_fault_is_expressible_to_the_plan_review(self):
        for name in PLAN_ONLY:
            said = load(FIXTURES / name / "expected.json")["plan_review"]
            with self.subTest(case=name):
                self.assertEqual(problems(said, narrative.PLAN_REVIEW_SCHEMA), [])
                self.assertIn(said["verdict"], ("revise", "reject"))
        said = {n: load(FIXTURES / n / "expected.json")["plan_review"]
                for n in ("list-plan", "topic-train", "empty-payoff")}
        self.assertTrue(said["list-plan"]["reorder_survives"])
        self.assertTrue(said["list-plan"]["sections_advancing_none"])
        self.assertFalse(said["topic-train"]["train_ok"])
        self.assertFalse(said["empty-payoff"]["payoff_concrete"])
        # AUTO-161: no reason to care before the mechanism is sent back to be
        # planned again, never rejected on that alone
        stakes = load(FIXTURES / "no-stakes" / "expected.json")["plan_review"]
        self.assertIs(stakes["stakes_concrete"], False)
        self.assertEqual(stakes["verdict"], "revise")
        self.assertEqual(narrative.check_plan_review(stakes), [])
        self.assertEqual([p["section"] for p in stakes["problems"]], [1])
        # every canned plan review says whether the plan has stakes
        for name, path in cases().items():
            plan_review = load(path / "expected.json").get("plan_review")
            if plan_review is not None:
                with self.subTest(case=name):
                    self.assertIn(plan_review["stakes_concrete"], (True, False))
                    self.assertEqual(problems(plan_review, narrative.PLAN_REVIEW_SCHEMA), [])
                    self.assertIs(plan_review["stakes_concrete"], name != "no-stakes")

    def test_the_no_stakes_plan_is_the_good_one_with_its_reason_to_care_taken_out(self):
        good = load(FIXTURES / "good" / "plan.json")
        plan = load(FIXTURES / "no-stakes" / "plan.json")
        for key in ("stakes", "viewer_stake"):
            self.assertIn("matters for science", plan[key].lower())
            self.assertNotIn("grid", plan[key].lower())
        first = " ".join(plan["sections"][0]["beats"]).lower()
        self.assertTrue(plan["sections"][0]["beats"][0].lower().startswith("sunlight"), first)
        self.assertNotIn("earth", first)
        # no twist is declared anywhere, so nothing excuses the late consequence
        self.assertNotRegex(json.dumps(plan).lower(), r"twist|withheld")
        rest = {k: v for k, v in plan.items() if k not in ("stakes", "viewer_stake",
                                                             "sections")}
        self.assertEqual(rest, {k: v for k, v in good.items()
                                if k not in ("stakes", "viewer_stake", "sections")})
        self.assertEqual(plan["sections"][1:], good["sections"][1:])
        expected = load(FIXTURES / "no-stakes" / "expected.json")
        self.assertEqual((expected["criterion"], expected["verdict"]), ("stakes", "reject"))
        self.assertEqual(expected["review"]["grades"]["stakes"], "Poor")

    def test_every_review_grades_devices(self):
        # AUTO-135: the good case shows its mechanism directly, with no
        # device, and still grades it Excellent: devices are never a quota
        for name, path in cases().items():
            with self.subTest(case=name):
                self.assertIn(load(path / "expected.json")["review"]["grades"]["devices"],
                              narrative.LEVELS)
        good = load(FIXTURES / "good" / "expected.json")["review"]
        self.assertEqual(good["grades"]["devices"], "Excellent")
        self.assertEqual(good["verdict"], "recommend")
        self.assertNotIn("devices", json.dumps(load(FIXTURES / "good" / "plan.json")))

    def test_the_bad_metaphor_case_is_good_with_one_wrong_mapping(self):
        good = {n: load(FIXTURES / "good" / n) for n in ("plan.json", "script.json")}
        bad = {n: load(FIXTURES / "bad-metaphor" / n) for n in ("plan.json", "script.json")}
        plan = bad["plan.json"]
        devices = plan["sections"][2].pop("devices")
        self.assertEqual(plan, good["plan.json"])
        self.assertEqual([d["kind"] for d in devices], ["analogy"])
        self.assertIn("path", devices[0]["maps"])
        self.assertTrue(devices[0]["breaks_at"].strip())
        # the narration speaks the analogy the plan declares, in the reveal
        screenplay = bad["script.json"]["sections"]
        self.assertEqual(screenplay[:2], good["script.json"]["sections"][:2])
        self.assertEqual(screenplay[3], good["script.json"]["sections"][3])
        reveal = screenplay[2]["shots"][0]["narration"]
        self.assertIn("like smoke behind a train", reveal)
        # the fault is added to the reveal, never in place of it: the good
        # film's statement of the wind stays, word for word, so only devices
        # can fairly grade low
        good_reveal = good["script.json"]["sections"][2]["shots"][0]["narration"]
        self.assertTrue(reveal.startswith(good_reveal), reveal)
        self.assertEqual(sum(s["seconds"] for s in screenplay[2]["shots"]),
                         sum(s["seconds"] for s in good["script.json"]["sections"][2]["shots"]))
        self.assertEqual([s["n"] for s in screenplay[2]["shots"]], [7, 8])
        self.assertIn("flew through that stream", screenplay[2]["shots"][1]["narration"])
        expected = load(FIXTURES / "bad-metaphor" / "expected.json")
        self.assertEqual((expected["criterion"], expected["verdict"]), ("devices", "consider"))
        grades = expected["review"]["grades"]
        self.assertEqual(grades["devices"], "Poor")
        self.assertEqual(grades["honesty"], "Excellent")
        self.assertFalse([k for k, v in grades.items()
                          if k != "devices" and v not in ("Excellent", "Good")], grades)
        # the plan review names the wrong mapping and still accepts
        plan_review = expected["plan_review"]
        self.assertEqual(plan_review["verdict"], "accept")
        self.assertEqual(problems(plan_review, narrative.PLAN_REVIEW_SCHEMA), [])
        self.assertEqual(narrative.check_plan_review(plan_review), [])
        self.assertTrue([p for p in plan_review["problems"]
                         if p["section"] == 3 and "analogy" in p["problem"]], plan_review)

    def test_every_review_grades_tension_and_names_the_trigger_it_heard(self):
        # AUTO-136: a plan-only case has no narration to hear
        for name, path in cases().items():
            said = load(path / "expected.json")["review"]
            with self.subTest(case=name):
                self.assertIn(said["grades"]["tension"], narrative.LEVELS)
                # AUTO-138: and every one grades the narrator's voice
                self.assertEqual(sorted(said["grades"]), sorted(narrative.KEYS))
                first = said["first_minute"]
                self.assertIn(first["trigger"], narrative.HEARD_TRIGGERS)
                if name in PLAN_ONLY:
                    self.assertEqual((first["trigger"], first["gap"]), ("not_heard", ""))
                else:
                    self.assertNotEqual(first["trigger"], "not_heard")
        self.assertEqual(load(FIXTURES / "good" / "expected.json")["review"]
                         ["first_minute"]["trigger"], "unexpected_observation")
        self.assertEqual(load(FIXTURES / "no-trigger" / "expected.json")["review"]
                         ["first_minute"]["trigger"], "none")

    def test_every_plan_declares_its_hook(self):
        for name, path in cases().items():
            hook = load(path / "plan.json")["hook"]
            with self.subTest(case=name):
                self.assertIn(hook["trigger"], narrative.HOOK_TRIGGERS)
                self.assertTrue(hook["gap"].strip())

    def test_the_two_tension_and_trigger_cases_are_good_with_one_fault(self):
        good = {n: load(FIXTURES / "good" / n) for n in ("plan.json", "script.json")}
        flat = {n: load(FIXTURES / "flat-tension" / n) for n in ("plan.json", "script.json")}
        sections = flat["plan.json"]["sections"]
        self.assertEqual(sections[1]["escalation"], sections[2]["escalation"])
        # the hook and the ending are good's
        for i in (0, 3):
            self.assertEqual(flat["script.json"]["sections"][i],
                             good["script.json"]["sections"][i])
        quiet = {n: load(FIXTURES / "no-trigger" / n) for n in ("plan.json", "script.json")}
        self.assertEqual(quiet["plan.json"], good["plan.json"])
        self.assertEqual(quiet["script.json"]["sections"][1:],
                         good["script.json"]["sections"][1:])
        opening = " ".join(s["narration"] for s in
                           quiet["script.json"]["sections"][0]["shots"])
        self.assertNotIn("?", opening)
        self.assertTrue(opening.startswith("Comets are made of ice and dust"))

    def test_the_lecture_voice_case_is_the_good_film_read_as_a_lecture(self):
        # AUTO-138: the good plan, and the good script's facts and structure in
        # an impersonal register: no question, no word to the viewer
        good = {n: load(FIXTURES / "good" / n) for n in ("plan.json", "script.json")}
        lecture = {n: load(FIXTURES / "lecture-voice" / n) for n in ("plan.json", "script.json")}
        self.assertEqual(lecture["plan.json"], good["plan.json"])
        self.assertEqual(lecture["script.json"]["idea"], good["script.json"]["idea"])
        a = [s for sec in good["script.json"]["sections"] for s in sec["shots"]]
        b = [s for sec in lecture["script.json"]["sections"] for s in sec["shots"]]
        self.assertEqual([s["heading"] for s in lecture["script.json"]["sections"]],
                         [s["heading"] for s in good["script.json"]["sections"]])
        for x, y in zip(a, b, strict=True):
            with self.subTest(n=x["n"]):
                self.assertEqual({k: v for k, v in y.items() if k != "narration"},
                                 {k: v for k, v in x.items() if k != "narration"})
                self.assertEqual(script.figures_in(y["narration"]),
                                 script.figures_in(x["narration"]))
                self.assertNotEqual(y["narration"], x["narration"])
        said = " ".join(s["narration"] for s in b)
        self.assertNotIn("?", said)
        self.assertIsNone(re.search(r"\b(you|your|we|us|our|let's)\b", said, re.I), said)
        expected = load(FIXTURES / "lecture-voice" / "expected.json")
        self.assertEqual((expected["criterion"], expected["verdict"]), ("voice", "consider"))
        self.assertIn(expected["review"]["grades"]["voice"], ("Fair", "Poor"))
        self.assertNotIn("Poor", [v for k, v in expected["review"]["grades"].items()
                                  if k != "voice"])

    def test_the_two_humour_cases_differ_from_good_in_one_shot(self):
        # AUTO-159: no other fixture holds a joke, so the voice row's humour
        # clause could fail either way unnoticed. Each case is the good film
        # with one shot's narration changed, so voice is the only row the
        # joke can fairly move.
        good = {n: load(FIXTURES / "good" / n) for n in ("plan.json", "script.json")}

        def shots(doc):
            return [s for sec in doc["sections"] for s in sec["shots"]]
        for name, n in (("aside-joke", 4), ("claim-joke", 5)):
            case = {f: load(FIXTURES / name / f) for f in ("plan.json", "script.json")}
            with self.subTest(case=name):
                self.assertFalse((FIXTURES / name / "padded.json").exists())
                self.assertEqual(case["plan.json"], good["plan.json"])
                self.assertEqual({k: v for k, v in case["script.json"].items()
                                  if k != "sections"},
                                 {k: v for k, v in good["script.json"].items()
                                  if k != "sections"})
                self.assertEqual([s["heading"] for s in case["script.json"]["sections"]],
                                 [s["heading"] for s in good["script.json"]["sections"]])
                for x, y in zip(shots(good["script.json"]), shots(case["script.json"]),
                                strict=True):
                    self.assertEqual({k: v for k, v in y.items() if k != "narration"},
                                     {k: v for k, v in x.items() if k != "narration"})
                    if x["n"] == n:
                        self.assertNotEqual(y["narration"], x["narration"])
                    else:
                        self.assertEqual(y["narration"], x["narration"], x["n"])
        good_said = load(FIXTURES / "good" / "expected.json")["review"]
        # the aside is added to shot 4, never in place of what it says
        before = shots(good["script.json"])[3]["narration"]
        after = shots(load(FIXTURES / "aside-joke" / "script.json"))[3]["narration"]
        self.assertIn(before, after)
        self.assertIn("alarm clock", after)
        aside = load(FIXTURES / "aside-joke" / "expected.json")
        self.assertEqual((aside["criterion"], aside["verdict"]), ("voice", "consider"))
        self.assertNotIn("at_least", aside)
        self.assertEqual(aside["review"]["grades"], dict(good_said["grades"], voice="Fair"))
        self.assertIn("attention 21", aside["why"])
        self.assertEqual(aside["evidence"], {"in": "script.sections.1.shots.0.narration",
                                             "says": "alarm clock"})
        [flag] = aside["review"]["flags"]
        self.assertEqual((flag["n"], flag["section"], flag["criterion"], flag["severity"]),
                         (4, 2, "voice", "important"))
        self.assertIn(flag["quote"], after)
        # the punchline is the shot's claim: sunlight is far too weak for the
        # gas tail, so the joke is kept and voice stays where good has it
        joke = load(FIXTURES / "claim-joke" / "expected.json")
        self.assertEqual(joke["at_least"], {"voice": "Good"})
        self.assertIn("attention 21", joke["why"])
        self.assertEqual(joke["review"]["grades"], good_said["grades"])
        self.assertEqual(joke["review"]["grades"]["voice"], "Excellent")
        self.assertEqual(joke["review"]["flags"], [])
        self.assertEqual(problems(joke["review"], narrative.REVIEW_SCHEMA), [])
        self.assertEqual(narrative.check_review(joke["review"]), [])
        punchline = shots(load(FIXTURES / "claim-joke" / "script.json"))[4]["narration"]
        self.assertIn("Far too fast for sunlight alone.", punchline)
        # the joke itself is in shot 5, and good's shot 5 has none
        joke_line = "blowing out a candle from the next town"
        self.assertIn(joke_line, punchline)
        self.assertNotIn(joke_line, shots(good["script.json"])[4]["narration"])
        # the canned synopsis is good's, with only the candle comparison added
        step = "sunlight is too weak to explain the gas tail"
        self.assertIn(step, good_said["synopsis"])
        self.assertEqual(joke["review"]["synopsis"].replace(
            ", like blowing out a candle from the next town", ""), good_said["synopsis"])

    def test_the_good_pair_can_be_recommended(self):
        expected = load(FIXTURES / "good" / "expected.json")
        self.assertEqual(expected["verdict"], "recommend")
        said = expected["review"]
        self.assertEqual(problems(said, narrative.REVIEW_SCHEMA), [])
        self.assertEqual(narrative.check_review(said), [])
        self.assertEqual(said["verdict"], "recommend")
        self.assertNotIn("Poor", said["grades"].values())
        # its narration finds things out with the viewer (AUTO-138)
        self.assertEqual(said["grades"]["voice"], "Excellent")
        plan = expected["plan_review"]
        self.assertEqual(problems(plan, narrative.PLAN_REVIEW_SCHEMA), [])
        self.assertEqual(plan["verdict"], "accept")


def _sha(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


#: reviewer_role(plan=True) and PLAN_REVIEW_SCHEMA as develop wrote them
#: before AUTO-137 (with AUTO-161's stakes ground, merged first), by digest:
#: a film with no counted earlier film is asked exactly what it was.
#: AUTO-157 changed both on purpose, for every film (the form as a ground to
#: revise only in the role, form_fits and form_problem in the schema), so
#: the values are taken after it; the channel still changes neither.
ROLE_BEFORE_AUTO_137 = "8b02c0e2c1261eb712b4d8490ad8a0d69732a7bb90239c6fb5ca2a77ed542b9c"
SCHEMA_BEFORE_AUTO_137 = "ecc0e94eb5cae4479b7c5024ed297f3fbcc753cd0cd6d2c74f8fb4fce5e1db82"


def accept_review(**kw):
    return {"verdict": "accept", "train_ok": True, "reorder_survives": False,
            "sections_advancing_none": [], "payoff_concrete": True,
            "questions_unanswered": [], "problems": [], "stakes_concrete": True,
            "form_fits": True, "form_problem": "", **kw}


class Channel(unittest.TestCase):
    """AUTO-137: a repeat of an earlier channel film is a ground to revise,
    never to reject, and only when there are counted earlier films."""

    def test_without_earlier_films_the_role_and_schema_are_todays(self):
        self.assertEqual(_sha(narrative.reviewer_role(plan=True)), ROLE_BEFORE_AUTO_137)
        self.assertEqual(narrative.reviewer_role(plan=True, channel=False),
                         narrative.reviewer_role(plan=True))
        self.assertIs(narrative.plan_review_schema(False), narrative.PLAN_REVIEW_SCHEMA)
        self.assertEqual(_sha(json.dumps(narrative.PLAN_REVIEW_SCHEMA, sort_keys=True)),
                         SCHEMA_BEFORE_AUTO_137)

    def test_with_earlier_films_the_role_names_the_sameness_ground_as_revise_only(self):
        role = " ".join(narrative.reviewer_role(plan=True, channel=True).split())
        self.assertIn("Those four, and stakes, are grounds to revise, and so is a repeat of "
                      "an earlier channel film that a returning viewer would notice, which "
                      "is never a ground to reject, and a form that does not follow from "
                      "the subject is a ground to revise only, never to reject.", role)
        self.assertNotEqual(role, " ".join(narrative.reviewer_role(plan=True).split()))

    def test_with_earlier_films_the_schema_requires_repeats(self):
        schema = narrative.plan_review_schema(True)
        self.assertIsNot(schema, narrative.PLAN_REVIEW_SCHEMA)
        self.assertIn("repeats", schema["required"])
        item = schema["properties"]["repeats"]["items"]
        self.assertEqual(set(item["required"]), {"film", "dimension", "problem"})
        self.assertEqual(item["properties"]["dimension"]["enum"],
                         ["opening", "structure", "closing"])
        self.assertNotIn("repeats", narrative.PLAN_REVIEW_SCHEMA["properties"])
        self.assertIs(narrative.plan_review_schema(True), schema)
        self.assertEqual(problems(accept_review(repeats=[]), schema), [])

    def test_an_accept_with_repeats_contradicts_itself(self):
        review = accept_review(repeats=[{"film": "bridge", "dimension": "closing",
                                         "problem": "ends on the same callback"}],
                               problems=[{"section": 0, "problem": "repeats bridge",
                                          "fix": "end on an image"}])
        found = narrative.check_plan_review(review, earlier_slugs=["bridge"])
        self.assertTrue(any("accept" in line and "repeats" in line for line in found))

    def test_a_repeat_naming_an_unknown_film_contradicts_itself(self):
        review = accept_review(verdict="revise", repeats=[
            {"film": "nowhere", "dimension": "opening", "problem": "same paradox"}],
            problems=[{"section": 0, "problem": "it opens like nowhere", "fix": "a question"}])
        found = narrative.check_plan_review(review, earlier_slugs=["bridge"])
        self.assertTrue(any("nowhere" in line for line in found))

    def test_a_repeat_with_no_matching_problem_contradicts_itself(self):
        repeat = {"film": "bridge", "dimension": "structure", "problem": "same shape"}
        review = accept_review(verdict="revise", repeats=[repeat], problems=[
            {"section": 2, "problem": "section 2 moves nothing", "fix": "cut it"}])
        found = narrative.check_plan_review(review, earlier_slugs=["bridge"])
        self.assertTrue(any("bridge" in line and "problems" in line for line in found))
        mirrored = accept_review(verdict="revise", repeats=[repeat], problems=[
            {"section": 0, "problem": "it unfolds as bridge did", "fix": "reveal earlier"}])
        self.assertEqual(narrative.check_plan_review(mirrored, earlier_slugs=["bridge"]), [])

    def test_without_repeats_the_checks_are_todays(self):
        review = accept_review()
        self.assertEqual(narrative.check_plan_review(review), [])
        self.assertEqual(narrative.check_plan_review(review, earlier_slugs=["bridge"]), [])


class Docs(unittest.TestCase):
    def test_the_corpus_readme_and_skill_name_the_module(self):
        readme = (CORPUS / "README.md").read_text(encoding="utf-8")
        item = readme[readme.index("3. **The narrative rubric.**"):readme.index("4. **The evidence loop.**")]
        self.assertIn("ytstudio/narrative.py", item)
        self.assertIn("reject", item)
        self.assertIsNone(re.search(r"verdict Pass", item))
        skill = (ROOT / "ytstudio" / "SKILL.md").read_text(encoding="utf-8")
        row = next(line for line in skill.splitlines() if line.startswith("| `yt script` |"))
        self.assertIn("narrative.py", row)

    def test_the_docs_say_judging_10_is_not_the_bar_for_every_case(self):
        readme = (CORPUS / "README.md").read_text(encoding="utf-8")
        item = readme[readme.index("3. **The narrative rubric.**"):readme.index("4. **The evidence loop.**")]
        item = " ".join(item.split())
        self.assertIn("judging 10 asks the calibration run for reject", item)
        self.assertIn("held to consider", item)

    def test_no_doc_says_the_stage_judges_the_story_before_it_does(self):
        # The stage table is the operator's contract: until script.py renders
        # the rubric and runs the review, no doc says it does.
        source = (ROOT / "ytstudio" / "script.py").read_text(encoding="utf-8")
        if re.search(r"^\s*(from \.|from ytstudio)[^\n]*\bnarrative\b", source, re.M):
            self.skipTest("script.py uses the rubric; the docs may say so")
        skill = (ROOT / "ytstudio" / "SKILL.md").read_text(encoding="utf-8")
        row = next(line for line in skill.splitlines() if line.startswith("| `yt script` |"))
        self.assertNotIn("is judged", row)
        self.assertNotIn("feeds", row)
        self.assertIn("not yet", row)
        readme = (CORPUS / "README.md").read_text(encoding="utf-8")
        item = readme[readme.index("3. **The narrative rubric.**"):readme.index("4. **The evidence loop.**")]
        self.assertNotIn("One tuple is rendered", item)
        self.assertIn("not yet", item)


if __name__ == "__main__":
    unittest.main()
