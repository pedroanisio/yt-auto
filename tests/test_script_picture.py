"""AUTO-98: say it, show it. The screenplay's picture is read as well as its
words before voice is paid for.

Two halves. A rule the text can measure: `rule:wall-of-text`, an on-screen
string over six words or one that repeats more than four words of its own
line (docs/narrative direction 2: on-screen text is labels and two-to-three
word keywords, never the narration; attention 23). And a judgement it
cannot: `picture-check`, asked of the reviewer's model under `--review`,
reads only what the screenplay puts on screen (each shot's `visual`,
`shows` and `on_screen_text`) with the plan's beat for each section, and
must reconstruct each section's claim from the pictures alone (direction 4,
Fong's picture-off test). A section it cannot reconstruct, a large number
left bare (attention 15), a mechanism a line claims that no picture shows,
and a list nothing signposts (attention 22) become flags revised under
`revise-picture`, the only revision allowed to change a shot's picture.

No model is called: `Screenwriter._ask` is a fake that answers by the
schema's shape, as in test_script_plan_review.
"""

from __future__ import annotations

import copy
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import narrative, script  # noqa: E402

from tests.test_script_plan import PLAN, idea  # noqa: E402
from tests.test_script_plan_review import verdict  # noqa: E402
from tests.test_script_rules import by_rule, clean, rules  # noqa: E402


# --------------------------------------------------------------------------- #
# the rule: a wall of text
# --------------------------------------------------------------------------- #

class WallOfText(unittest.TestCase):
    def test_an_on_screen_string_over_six_words_is_flagged(self):
        sheet = clean(body=[("We scraped the paint off one joint.",
                             {"on_screen_text": ["rust ate the steel under every coat"]}),
                            "Under it, the steel had rusted through."])
        flags = by_rule(rules(sheet), "rule:wall-of-text")
        self.assertEqual([(f["n"], f["tier"], f["severity"]) for f in flags],
                         [(3, "important", "important")])
        self.assertIn("7 words", flags[0]["problem"])

    def test_a_super_that_prints_the_line_is_flagged(self):
        # five words, under the length bound, but the narration verbatim
        sheet = clean(body=[("We scraped the paint off one joint.",
                             {"on_screen_text": ["scraped the paint off one"]}),
                            "Under it, the steel had rusted through."])
        flags = by_rule(rules(sheet), "rule:wall-of-text")
        self.assertEqual([f["n"] for f in flags], [3])
        self.assertIn("narration", flags[0]["problem"])

    def test_labels_and_short_keywords_pass(self):
        sheet = clean(body=[("We scraped the paint off one joint.",
                             {"on_screen_text": ["paint", "rusted joint", "one joint"]}),
                            "Under it, the steel had rusted through."])
        self.assertEqual(by_rule(rules(sheet), "rule:wall-of-text"), [])

    def test_exactly_six_words_and_exactly_four_echoed_words_pass(self):
        # the bounds are "over six" and "more than four"
        sheet = clean(body=[("We scraped the paint off one joint.",
                             {"on_screen_text": ["rust ate steel under every coat",
                                                 "scraped the paint off"]}),
                            "Under it, the steel had rusted through."])
        self.assertEqual(by_rule(rules(sheet), "rule:wall-of-text"), [])

    def test_a_long_numeral_is_one_word(self):
        # direction 2 allows numbers on screen; separators do not make words
        sheet = clean(body=[("We scraped the paint off one joint.",
                             {"on_screen_text": ["Earth: 4,540,000,000 years old",
                                                 "$12,500,000.50 lost"]}),
                            "Under it, the steel had rusted through."])
        self.assertEqual(by_rule(rules(sheet), "rule:wall-of-text"), [])

    def test_an_older_shots_quoted_super_is_read(self):
        # no on_screen_text: the strings quoted in `on_screen` are the supers
        sheet = clean(body=[("We scraped the paint off one joint.",
                             {"on_screen_text": [],
                              "on_screen": 'a card reads "rust ate the steel under '
                                           'every coat" over the joint'}),
                            "Under it, the steel had rusted through."])
        flags = by_rule(rules(sheet), "rule:wall-of-text")
        self.assertEqual([f["n"] for f in flags], [3])
        self.assertIn("7 words", flags[0]["problem"])

    def test_its_bound_cites_direction_two_and_the_screen_may_be_revised(self):
        rule = next(r for r in script.RULES if r.id == "rule:wall-of-text")
        self.assertEqual(rule.tier, "important")
        self.assertEqual(rule.langs, ())
        self.assertIn("direction 2", rule.corpus)
        self.assertEqual(script.BOUNDS[rule.bound]["value"], 6)
        self.assertIn("rule:wall-of-text", script.SCREEN_RULES)

    def test_a_standing_one_is_a_warning_in_script_json_findings(self):
        w = script.Screenwriter(idea(), verbose=False, jobs=1, minutes=1, research="")
        wall = "the paint hid the rust on every joint"

        def ask(name, prompt, schema):
            props = schema["properties"]
            if "questions" in props:
                return copy.deepcopy(PLAN)
            if "shots" in props:
                return {"shots": [{"visual": "v", "narration": "The bridge fell.",
                                   "seconds": 5, "on_screen_text": [wall],
                                   "function": "context", "shows": "s"}]}
            return {}
        w._ask = ask
        data = w.run()
        self.assertTrue(any(f.startswith("warning: rule:wall-of-text") for f in data["findings"]),
                        data["findings"])


# --------------------------------------------------------------------------- #
# the judgement: the picture-check
# --------------------------------------------------------------------------- #

def kind(schema):
    props = schema["properties"]
    if schema is narrative.PICTURE_SCHEMA:
        return "picture-check"
    if "verdict" in props and "problems" in props:
        return "plan-review"
    if "questions" in props:
        return "plan"
    if "shots" in props:
        return "shots"
    if "fixes" in props:
        return "fixes"
    return ""


def section_shots(n):
    """Two shots a section, each with a picture, what it shows and a super;
    the narration is marked so the test can look for it in a prompt."""
    return {"shots": [{"visual": f"PICTURE {n}.{i}", "narration": f"SPOKEN LINE {n}.{i}.",
                       "seconds": 5, "asset": "make", "style": "s",
                       "function": "context", "shows": f"DEMONSTRATES {n}.{i}",
                       "on_screen_text": [f"LABEL {n}{i}"]}
                      for i in (1, 2)]}


def picture(sections=None, reports=()):
    """A picture-check answer: every section reconstructed, unless given."""
    rows = sections if sections is not None else [
        {"section": n, "claim": f"claim {n}", "carries_beat": True, "missing": "",
         "confused_at": []} for n in (1, 2, 3)]
    return {"sections": rows,
            "reports": [{"n": 0, "bare_number": "", "unshown_claim": "",
                         "unsignposted_list": "", **r} for r in reports]}


def writer(**kw):
    kw.setdefault("research", "")
    kw.setdefault("reviewer_model", "opus")
    return script.Screenwriter(idea(), verbose=False, jobs=1, minutes=1, **kw)


def faked(w, check, fixes=None):
    """Answer by the schema: the plan accepted, two shots a section, the
    picture-check `check`, and `fixes` for the revise named `revise-picture`.
    `check` given as a list is one answer per picture-check call, in order;
    an exception, there or as `fixes`, is raised instead of answering."""
    calls = []
    answers = list(check) if isinstance(check, list) else None

    def ask(name, prompt, schema):
        which = kind(schema)
        calls.append({"name": name, "prompt": prompt, "kind": which, "schema": schema})
        if which == "plan-review":
            return verdict("accept")
        if which == "plan":
            return copy.deepcopy(PLAN)
        if which == "shots":
            return section_shots(int(name.split("-")[1]))
        if which == "picture-check":
            got = answers.pop(0) if answers is not None else check
            if isinstance(got, Exception):
                raise got
            return copy.deepcopy(got)
        if which == "fixes" and name == "revise-picture":
            if isinstance(fixes, Exception):
                raise fixes
            return {"fixes": copy.deepcopy(fixes or [])}
        return {}
    w._ask = ask
    return calls


def named(calls, name):
    return [c for c in calls if c["name"] == name]


def shot(data, label):
    return next(s for sec in data["sections"] for s in sec["shots"] if s["label"] == label)


class Prompt(unittest.TestCase):
    def test_the_check_reads_the_pictures_and_never_the_narration(self):
        w = writer()
        calls = faked(w, picture())
        w.run(review=True)
        [check] = named(calls, "picture-check")
        prompt = check["prompt"]
        for n in (1, 2, 3):
            for i in (1, 2):
                self.assertIn(f"PICTURE {n}.{i}", prompt)
                self.assertIn(f"DEMONSTRATES {n}.{i}", prompt)
                self.assertIn(f"LABEL {n}{i}", prompt)
                self.assertNotIn(f"SPOKEN LINE {n}.{i}", prompt)
        self.assertNotIn("SPOKEN", prompt)
        # the plan's beat for each section, to reconstruct against
        for spec in PLAN["sections"]:
            for beat in spec["beats"]:
                self.assertIn(beat, prompt)

    def test_it_is_asked_after_drafting_and_only_with_review(self):
        w = writer()
        calls = faked(w, picture())
        w.run(review=True)
        order = [c["name"] for c in calls]
        last_write = max(i for i, n in enumerate(order) if n.startswith("write-"))
        self.assertGreater(order.index("picture-check"), last_write)

        w = writer(reviewer_model=None)
        calls = faked(w, picture())
        data = w.run()
        self.assertFalse(named(calls, "picture-check"))
        self.assertNotIn("picture", data)

    def test_it_is_the_reviewers_model_under_a_role_of_its_own(self):
        w = writer()
        self.assertEqual(w.picture_reviewer.model, "opus")
        self.assertIn("never the narration", w.picture_reviewer.role)
        with mock.patch.object(w.picture_reviewer, "decide",
                               return_value=picture()) as decide:
            script.Screenwriter._ask(w, "picture-check", "p", narrative.PICTURE_SCHEMA)
        decide.assert_called_once()

    def test_the_role_and_schema_name_no_treatment(self):
        # invariant 2: what to show is judged; how it looks is the treatment's
        text = narrative.picture_role() + str(narrative.PICTURE_SCHEMA)
        found = re.findall(r"\b(blue|red|green|yellow|highlight|arrow|zoom|glow)\b",
                           text, re.I)
        self.assertEqual(found, [])


def without(*numbers):
    """A picture-check answer that reads every section but `numbers`."""
    return picture([{"section": n, "claim": f"claim {n}", "carries_beat": True,
                     "missing": "", "confused_at": []} for n in (1, 2, 3)
                    if n not in numbers])


class ValidateRetryFail(unittest.TestCase):
    def test_a_section_left_unread_is_asked_again_by_its_number(self):
        w = writer()
        calls = faked(w, [without(2), picture()])
        data = w.run(review=True)
        [again] = named(calls, "picture-check-again")
        self.assertIn("section(s) 2", again["prompt"])
        self.assertIs(again["schema"], narrative.PICTURE_SCHEMA)
        self.assertTrue(data["picture"]["again"])
        self.assertEqual(data["picture"]["unread"], [])
        self.assertFalse(any("read no claim" in f for f in data["findings"]))

    def test_still_unread_after_asking_again_is_a_note(self):
        w = writer()
        calls = faked(w, [without(2), without(2)])
        data = w.run(review=True)
        self.assertEqual(len(named(calls, "picture-check-again")), 1)   # once, no more
        self.assertEqual(data["picture"]["unread"], [2])
        self.assertTrue(any(f.startswith("note: picture-check read no claim for "
                                         "section(s) 2") for f in data["findings"]),
                        data["findings"])

    def test_a_failed_check_is_a_warning_and_changes_nothing(self):
        w = writer()
        calls = faked(w, script.ClaudeError("the reviewer is down"))
        data = w.run(review=True)
        record = data["picture"]
        self.assertIn("the reviewer is down", record["failed"])
        self.assertEqual(record["flags"], [])
        self.assertFalse(named(calls, "revise-picture"))
        self.assertTrue(any(f.startswith("warning: picture-check failed")
                            for f in data["findings"]), data["findings"])
        self.assertEqual(shot(data, "1.1")["visual"], "PICTURE 1.1")


class Flags(unittest.TestCase):
    MISSED = [{"section": 1, "claim": "a river", "carries_beat": True, "missing": "",
               "confused_at": []},
              {"section": 2, "claim": "a wall is painted", "carries_beat": False,
               "missing": "nothing shows the paint hiding the steel", "confused_at": [4]},
              {"section": 3, "claim": "a bolt", "carries_beat": True, "missing": "",
               "confused_at": []}]

    @staticmethod
    def sections():
        """The plan's three sections, two shots each, numbered 1 to 6."""
        sections = [script.Section.from_spec(s) for s in PLAN["sections"]]
        for i, sec in enumerate(sections):
            sec.shots = [script.Shot(n=2 * i + 1, label=f"{i + 1}.1"),
                         script.Shot(n=2 * i + 2, label=f"{i + 1}.2")]
        return sections

    def test_each_finding_is_a_flag_with_its_severity(self):
        sections = self.sections()
        review = picture(self.MISSED, [
            {"n": 1, "bare_number": "12,000 tonnes on screen with nothing to weigh it against"},
            {"n": 5, "unshown_claim": "shows says the bolt shears; the picture is a still bolt"},
            {"n": 6, "unsignposted_list": "three causes appear with no count or labels"}])
        flags = script.picture_flags(review, sections)
        got = {(f["n"], f["kind"], f["severity"]) for f in flags}
        self.assertEqual(got, {(4, "picture:section", "important"),
                               (1, "picture:bare-number", "important"),
                               (5, "picture:unshown-claim", "important"),
                               (6, "picture:unsignposted-list", "minor")})
        section = next(f for f in flags if f["kind"] == "picture:section")
        self.assertIn("paint hides steel", section["problem"])         # the plan's beat
        self.assertIn("a wall is painted", section["problem"])         # what was read
        self.assertTrue(all(f["problem"].startswith(f["kind"] + ": ") for f in flags))

    def test_a_section_missed_with_no_shot_named_binds_its_first_shot(self):
        sections = self.sections()
        rows = copy.deepcopy(self.MISSED)
        rows[1]["confused_at"] = [99]                     # not a shot of section 2
        flags = script.picture_flags(picture(rows), sections)
        self.assertEqual([f["n"] for f in flags], [3])

    def test_the_flags_are_revised_under_revise_picture_and_a_visual_fix_lands(self):
        w = writer()
        check = picture(self.MISSED, [{"n": 1, "bare_number": "12,000 bolts, bare"},
                                      {"n": 6, "unsignposted_list": "three parts, no count"}])
        calls = faked(w, check, fixes=[
            {"n": 4, "action": "fix", "narration": "SPOKEN LINE 2.2.", "why": "show the paint",
             "visual": "a paint layer peeled back from the rusted steel",
             "shows": "the paint hid the rust"},
            {"n": 1, "action": "keep", "narration": "SPOKEN LINE 1.1.",
             "why": "the next shot compares it"},
            {"n": 6, "action": "fix", "narration": "SPOKEN LINE 3.2.", "why": "count them",
             "on_screen_text": ["three parts"]}])
        data = w.run(review=True)
        [revise] = named(calls, "revise-picture")
        self.assertIn("12,000 bolts, bare", revise["prompt"])
        self.assertIn("three parts, no count", revise["prompt"])        # the minor one too
        self.assertIn("SPOKEN LINE 2.2", revise["prompt"])              # the writer sees the line
        fixed = shot(data, "2.2")
        self.assertEqual(fixed["visual"], "a paint layer peeled back from the rusted steel")
        self.assertEqual(fixed["shows"], "the paint hid the rust")
        self.assertEqual(shot(data, "3.2")["on_screen_text"], ["three parts"])
        record = data["picture"]
        row = next(r for r in record["revisions"] if r["n"] == 4)
        self.assertEqual(row["visual_before"], "PICTURE 2.2")
        self.assertEqual(record["model"], "opus")
        self.assertEqual([s["section"] for s in record["sections"]], [1, 2, 3])
        # The writer, who sees the line, answered the kept flag: the reviewer
        # read no narration, so its bare number may be compared aloud
        # (attention 15 accepts a spoken comparison). It is recorded with its
        # reason and binds nothing; the fixed ones are gone.
        self.assertEqual(record["flags"], [])
        [kept] = record["kept"]
        self.assertEqual((kept["n"], kept["kind"], kept["kept"]),
                         (1, "picture:bare-number", "the next shot compares it"))
        self.assertTrue(any(f.startswith("note: picture:bare-number")
                            and "kept by the writer: the next shot compares it" in f
                            for f in data["findings"]), data["findings"])
        self.assertFalse(any(f.startswith("warning: picture:") for f in data["findings"]),
                         data["findings"])

    def test_a_kept_flag_binds_nothing_downstream(self):
        from ytstudio import vfx
        w = writer()
        faked(w, picture(reports=[{"n": 1, "bare_number": "12,000 bolts, bare"},
                                  {"n": 3, "unshown_claim": "the line says it shears"}]),
              fixes=[{"n": 1, "action": "keep", "narration": "SPOKEN LINE 1.1.",
                      "why": "the line compares it to a city"},
                     {"n": 3, "action": "keep", "narration": "SPOKEN LINE 2.1.",
                      "why": "the line carries the claim, not the picture"}])
        data = w.run(review=True)
        rows = vfx.picture_constraints(data)
        self.assertEqual([r for r in rows if r["source"] == "picture"], [])
        self.assertNotIn("## Story constraints", "\n".join(vfx.constraint_lines(rows)))

    def test_an_answer_that_changes_nothing_stands_and_binds(self):
        from ytstudio import vfx
        w = writer()
        faked(w, picture(reports=[{"n": 1, "bare_number": "12,000 bolts, bare"}]),
              fixes=[{"n": 1, "action": "fix", "narration": "SPOKEN LINE 1.1.",
                      "why": "nothing to change"}])
        data = w.run(review=True)
        self.assertEqual([f["n"] for f in data["picture"]["flags"]], [1])
        self.assertTrue(any(f.startswith("warning: picture:bare-number")
                            and "still standing" in f for f in data["findings"]))
        rows = [r for r in vfx.picture_constraints(data) if r["source"] == "picture"]
        self.assertEqual([r["label"] for r in rows], ["1.1"])

    def test_a_changed_shot_answers_its_flag_without_a_second_reading(self):
        # The current rule, stated in the manual: any change revise-picture
        # makes to a flagged shot answers that shot's flags, here a narration
        # change to a section flag; the pictures are not read again.
        w = writer()
        calls = faked(w, picture(self.MISSED), fixes=[
            {"n": 4, "action": "fix", "narration": "The paint hid the rust.",
             "why": "the line now names what the picture shows"}])
        data = w.run(review=True)
        self.assertEqual(shot(data, "2.2")["narration"], "The paint hid the rust.")
        self.assertEqual(shot(data, "2.2")["visual"], "PICTURE 2.2")
        self.assertEqual(data["picture"]["flags"], [])
        self.assertEqual(len(named(calls, "picture-check")), 1)
        self.assertFalse(named(calls, "picture-check-again"))

    def test_a_failed_revision_leaves_every_flag_standing(self):
        w = writer()
        faked(w, picture(self.MISSED), fixes=script.ClaudeError("the writer is down"))
        data = w.run(review=True)
        self.assertEqual([f["n"] for f in data["picture"]["flags"]], [4])
        self.assertTrue(any(f.startswith("warning: picture:section")
                            and "revise-picture failed" in f for f in data["findings"]),
                        data["findings"])

    def test_script_md_records_the_check(self):
        import tempfile
        w = writer()
        faked(w, picture(self.MISSED, [{"n": 1, "bare_number": "12,000 bolts, bare"},
                                       {"n": 6, "unsignposted_list": "three parts"}]),
              fixes=[{"n": 1, "action": "keep", "narration": "SPOKEN LINE 1.1.",
                      "why": "the next shot compares it"},
                     {"n": 4, "action": "fix", "narration": "SPOKEN LINE 2.2.",
                      "why": "show the paint", "visual": "the paint peeled back"}])
        data = w.run(review=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "script.md"
            script.write_md(str(path), idea(), data, script.sections_of(data))
            text = path.read_text(encoding="utf-8")
        md = text.split("## Picture check", 1)[1].split("\n## ", 1)[0]
        self.assertIn("section 2: misses its beat: a wall is painted", md)
        self.assertIn("section 1: carries its beat", md)
        self.assertIn("revised 2.2: show the paint", md)
        self.assertIn("(kept: the next shot compares it)", md)
        self.assertIn("**minor** picture:unsignposted-list: shot 3.2", md)

    def test_report_only_records_the_flags_and_revises_nothing(self):
        w = writer()
        calls = faked(w, picture(self.MISSED))
        data = w.run(review=True, fix=False)
        self.assertFalse(named(calls, "revise-picture"))
        self.assertEqual([f["n"] for f in data["picture"]["flags"]], [4])
        self.assertTrue(any(f.startswith("warning: picture:section") for f in data["findings"]))

    def test_a_clean_check_asks_no_revision(self):
        w = writer()
        calls = faked(w, picture())
        data = w.run(review=True)
        self.assertFalse(named(calls, "revise-picture"))
        self.assertEqual(data["picture"]["flags"], [])


class OnlyThePictureRevisionChangesThePicture(unittest.TestCase):
    def shots(self):
        return [script.Shot(n=1, label="1.1", visual="a still bolt", shows="the bolt shears",
                            narration="The bolt sheared.", on_screen_text=["bolt"])]

    def revise(self, name):
        w = writer()
        shots = self.shots()
        w._ask = lambda *_: {"fixes": [{"n": 1, "action": "fix",
                                        "narration": "The bolt sheared at dawn.",
                                        "why": "w", "visual": "the bolt shearing in two",
                                        "shows": "it shears", "on_screen_text": ["sheared"]}]}
        flags = [{"n": 1, "severity": "important", "problem": "p", "fix": "f"}]
        w.revise(shots, flags, name=name)
        return shots[0]

    def test_the_fact_checks_revise_rewrites_the_narration_only(self):
        for name in ("revise-1", "revise-2", "revise-ledger"):
            s = self.revise(name)
            self.assertEqual(s.narration, "The bolt sheared at dawn.")
            self.assertEqual((s.visual, s.shows, s.on_screen_text),
                             ("a still bolt", "the bolt shears", ["bolt"]), name)

    def test_revise_picture_changes_the_picture(self):
        s = self.revise("revise-picture")
        self.assertEqual((s.visual, s.shows, s.on_screen_text),
                         ("the bolt shearing in two", "it shears", ["sheared"]))

    def test_the_picture_fields_are_in_the_picture_revisions_schema_only(self):
        item = script.PICTURE_REVISE_SCHEMA["properties"]["fixes"]["items"]
        for key in ("visual", "shows", "on_screen_text"):
            self.assertIn(key, item["properties"])
            self.assertNotIn(key, item["required"])
        self.assertNotIn("visual", script.REVISE_SCHEMA["properties"]["fixes"]["items"]
                         ["properties"])


class Spend(unittest.TestCase):
    def test_the_review_flag_prices_the_picture_check_too(self):
        from ytstudio import spend
        increment, decision = spend.FLAG_USD["script"]["--review"]
        self.assertEqual(decision, "plan-review")
        self.assertEqual(increment, spend.PLAN_REVIEW_USD + spend.PICTURE_CHECK_USD)
        self.assertGreater(spend.PICTURE_CHECK_USD, 0)


class Corpus(unittest.TestCase):
    def test_the_picture_check_cites_rules_the_corpus_has(self):
        from tests.test_script_rules import strength_table
        table = strength_table()
        for rule in narrative.PICTURE_RULES:
            self.assertIn(rule, table, rule)


if __name__ == "__main__":
    unittest.main()
