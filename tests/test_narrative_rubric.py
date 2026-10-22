"""AUTO-91: the narrative rubric, one tuple read by the writer and the reviewer.

`ytstudio/narrative.py` holds the seven criteria a story is judged on (train,
hook, stakes, open loops, reveal setup, ending, honesty), each with four
labelled levels, a worked example, the strength of its lead rule from
docs/narrative/README.md and the corpus rules it is drawn from; the review's
schema in the shape of script coverage (judging 12); the plan review's schema;
the level order permuted per sample (judging 8); and the check that runs past
the schema (a recommend carrying a Poor, a consider with nothing to fix, a
flag on no criterion).

tests/fixtures/narrative/ starts the calibration set (judging 10): plans and
scripts seeded with one known fault each, and one good pair. Each case's
`expected.json` names the criterion it fails, the best verdict a correct
reviewer may give it, where the fault sits, and a review the schema can
express that says so.

No model is called: the schemas are answered by test_harness_decisions'
`blank()` or by the reviews written in the fixtures.
"""

from __future__ import annotations

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
    def test_seven_criteria_with_the_named_keys(self):
        self.assertEqual([c.key for c in narrative.RUBRIC],
                         ["train", "hook", "stakes", "open_loops", "reveal_setup",
                          "ending", "honesty"])
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

    def test_coverage_shape_with_the_synopsis_before_the_grades(self):
        props = list(narrative.REVIEW_SCHEMA["properties"])
        for field in ("logline", "synopsis", "grades", "verdict", "flags",
                      "first_minute", "answered"):
            self.assertIn(field, props)
        self.assertLess(props.index("synopsis"), props.index("grades"))
        first = narrative.REVIEW_SCHEMA["properties"]["first_minute"]
        self.assertEqual(sorted(first["required"]), ["promise_match", "question_and_stakes"])
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
                      "payoff_concrete", "questions_unanswered", "problems"):
            self.assertIn(field, narrative.PLAN_REVIEW_SCHEMA["required"])
        self.assertEqual(sorted(props["problems"]["items"]["required"]),
                         ["fix", "problem", "section"])


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


#: The faults the card names, each with the criterion it fails on.
SEEDED = {"list-plan": "train", "topic-train": "train", "empty-payoff": "ending",
          "reveal-first": "reveal_setup", "recap-ending": "ending",
          "hook-enumerates": "hook"}


class Fixtures(unittest.TestCase):
    def test_the_set_holds_every_seeded_fault_and_one_good_pair(self):
        names = cases()
        self.assertEqual(sorted(names), sorted([*SEEDED, "good"]))
        good = names["good"]
        self.assertTrue((good / "plan.json").is_file() and (good / "script.json").is_file())
        for name, criterion in SEEDED.items():
            with self.subTest(case=name):
                expected = load(names[name] / "expected.json")
                self.assertEqual(expected["criterion"], criterion)
                self.assertIn(expected["verdict"], ("reject", "consider"))
                self.assertTrue(expected["why"].strip())

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
            if name == "good":
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
        for name in ("list-plan", "topic-train", "empty-payoff"):
            path = FIXTURES / name
            self.assertFalse((path / "script.json").is_file())
            said = load(path / "expected.json")["review"]
            with self.subTest(case=name):
                for text in said["first_minute"].values():
                    self.assertIn("plan", text)
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
        for name in ("list-plan", "topic-train", "empty-payoff"):
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

    def test_the_good_pair_can_be_recommended(self):
        expected = load(FIXTURES / "good" / "expected.json")
        self.assertEqual(expected["verdict"], "recommend")
        said = expected["review"]
        self.assertEqual(problems(said, narrative.REVIEW_SCHEMA), [])
        self.assertEqual(narrative.check_review(said), [])
        self.assertEqual(said["verdict"], "recommend")
        self.assertNotIn("Poor", said["grades"].values())
        plan = expected["plan_review"]
        self.assertEqual(problems(plan, narrative.PLAN_REVIEW_SCHEMA), [])
        self.assertEqual(plan["verdict"], "accept")


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
