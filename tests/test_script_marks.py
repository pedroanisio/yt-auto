"""Delivery marks in the screenplay (AUTO-139, part A): each shot may carry
the word to stress per sentence, the words a pause lands before and a
pronouncer per hard name (direction 27, 28), and every revision keeps them
pointing at words the line still says.

No model is called: `Screenwriter._ask` is replaced by a fake.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import script  # noqa: E402
from ytstudio.core import Idea  # noqa: E402


def writer():
    return script.Screenwriter(Idea(title="T", description="d", genre="g"),
                               research="", verbose=False)


def marked(n=1, narration="Schrödinger put a cat in a box. The cat was fine.", **kw):
    marks = {"emphasis": ["cat", "fine"], "pauses": ["box"],
             "pronounce": [{"term": "Schrödinger", "say": "SHROH-ding-er"}]}
    marks.update(kw)
    return script.Shot(narration=narration, n=n, label=f"1.{n}", seconds=6, **marks)


class Schema(unittest.TestCase):
    def test_the_three_fields_are_offered_and_none_is_required(self):
        shots = script.section_schema(False)["properties"]["shots"]["items"]
        props = shots["properties"]
        self.assertEqual(props["emphasis"]["type"], "array")
        self.assertEqual(props["pauses"]["items"], {"type": "string"})
        row = props["pronounce"]["items"]
        self.assertEqual(set(row["properties"]), {"term", "say"})
        for key in ("emphasis", "pauses", "pronounce"):
            self.assertIn("description", props[key])
            self.assertNotIn(key, shots["required"])
            self.assertNotIn(key, script.section_schema(True)["properties"]["shots"]
                             ["items"]["required"])

    def test_the_writer_is_asked_for_them_with_the_rules_that_want_them(self):
        seen = []
        w = writer()
        w._ask = lambda name, prompt, schema: seen.append(prompt) or {"shots": []}
        w.write_section({"heading": "A", "seconds": 10, "beats": []}, index=1,
                        total=1, plan={}, written=[], next_heading="", sourced=False)
        for needle in ("- emphasis:", "- pauses:", "- pronounce:", "direction 27",
                       "direction 28", "SHROH-ding-er"):
            self.assertIn(needle, seen[0])


class FromRaw(unittest.TestCase):
    def test_the_marks_round_trip_and_malformed_pronouncers_are_dropped(self):
        shot = script.Shot.from_raw({
            "narration": "Bohr met Schrödinger.", "seconds": 4,
            "emphasis": [" met ", "", None], "pauses": ["Schrödinger"],
            "pronounce": [{"term": " Schrödinger ", "say": "SHROH-ding-er"},
                          {"term": "", "say": "BOHR"},
                          {"say": "BOHR"},
                          {"term": "Bohr", "say": "BOHR is"},
                          {"term": "Bohr", "say": ""},
                          "Bohr"]})
        self.assertEqual(shot.emphasis, ["met"])
        self.assertEqual(shot.pauses, ["Schrödinger"])
        # An empty `say` is kept for rule:marks to name; a row with no term,
        # or a `say` of more than one token, is not a pronouncer at all.
        self.assertEqual(shot.pronounce, [{"term": "Schrödinger", "say": "SHROH-ding-er"},
                                          {"term": "Bohr", "say": ""}])

    def test_an_older_shot_has_none(self):
        shot = script.Shot.from_raw({"narration": "Plain.", "seconds": 3})
        self.assertEqual((shot.emphasis, shot.pauses, shot.pronounce), ([], [], []))

    def test_load_script_keeps_them(self):
        data = {"sections": [{"heading": "One", "shots": [
            {"label": "1.1", "n": 1, "narration": "Bohr met Schrödinger.", "seconds": 4,
             "emphasis": ["met"], "pauses": ["Schrödinger"],
             "pronounce": [{"term": "Schrödinger", "say": "SHROH-ding-er"},
                           {"term": "", "say": "X"}]},
            {"label": "1.2", "n": 2, "narration": "Old shot.", "seconds": 3}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "script.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            sections, _ = script.load_script(str(path))
        first, second = sections[0].shots
        self.assertEqual(first.emphasis, ["met"])
        self.assertEqual(first.pauses, ["Schrödinger"])
        self.assertEqual(first.pronounce, [{"term": "Schrödinger", "say": "SHROH-ding-er"}])
        self.assertEqual(second.pronounce, [])


class Prune(unittest.TestCase):
    def test_a_mark_whose_word_left_the_line_is_dropped_and_returned(self):
        shot = marked(narration="Schrödinger put a cat in a crate. The cat was fine.")
        dropped = script.prune_marks(shot)
        self.assertEqual(dropped, [{"kind": "pause", "word": "box"}])
        self.assertEqual(shot.pauses, [])
        self.assertEqual(shot.emphasis, ["cat", "fine"])
        self.assertEqual(len(shot.pronounce), 1)

    def test_case_and_punctuation_and_a_possessive_still_match(self):
        shot = marked(narration="schrödinger's CAT went in the box, and was fine!")
        self.assertEqual(script.prune_marks(shot), [])

    def test_a_pronouncer_whose_name_is_gone(self):
        shot = marked(narration="A cat in a box. The cat was fine.")
        self.assertEqual(script.prune_marks(shot),
                         [{"kind": "pronounce", "term": "Schrödinger",
                           "say": "SHROH-ding-er"}])


class Polish(unittest.TestCase):
    def test_a_rewritten_line_reports_the_marks_it_dropped(self):
        shot = marked()
        w = writer()
        w._ask = lambda name, prompt, schema: {"edits": [
            {"n": 1, "narration": "Schrödinger put a cat in a crate. The cat was fine.",
             "why": "box is said twice"}]}
        self.assertEqual(w.polish([shot]), 1)
        self.assertEqual(w.polish_applied, [
            {"n": 1, "label": "1.1", "marks_dropped": [{"kind": "pause", "word": "box"}]}])
        self.assertEqual(shot.pauses, [])


class Revise(unittest.TestCase):
    def flags(self):
        return [{"n": 1, "rule": "rule:marks", "tier": "important",
                 "severity": "important",
                 "problem": "rule:marks: shot 1.1: emphasis \"dog\" is not a word of the line",
                 "fix": "mark a word the line says"}]

    def test_a_rule_marks_fix_rewrites_the_shots_emphasis(self):
        shot = marked(emphasis=["dog", "fine"])
        w = writer()
        seen = []

        def ask(name, prompt, schema):
            seen.append((prompt, schema))
            return {"fixes": [{"n": 1, "action": "fix", "narration": shot.narration,
                               "emphasis": ["cat", "fine"], "why": "dog is not said"}]}
        w._ask = ask
        result = w.revise([shot], self.flags(), rules=True, name="revise-rules")
        self.assertEqual(shot.emphasis, ["cat", "fine"])
        row = result["applied"][0]
        self.assertEqual(row["marks_before"]["emphasis"], ["dog", "fine"])
        self.assertEqual(row["marks_after"]["emphasis"], ["cat", "fine"])
        # the writer sees the marks it is asked to fix, and may answer them
        prompt, schema = seen[0]
        brief = prompt.split("The delivery marks of the lines rule:marks flagged")[1]
        self.assertIn('"dog"', brief)
        self.assertIn('"SHROH-ding-er"', brief)
        props = schema["properties"]["fixes"]["items"]["properties"]
        for key in ("emphasis", "pauses", "pronounce"):
            self.assertIn(key, props)

    def test_an_empty_marks_field_in_the_fix_leaves_the_marks_alone(self):
        shot = marked(emphasis=["dog", "fine"])
        w = writer()
        w._ask = lambda name, prompt, schema: {"fixes": [
            {"n": 1, "action": "fix", "narration": shot.narration, "emphasis": [],
             "pauses": [], "why": "w"}]}
        result = w.revise([shot], self.flags(), rules=True, name="revise-rules")
        self.assertEqual(shot.emphasis, ["dog", "fine"])
        self.assertEqual(shot.pauses, ["box"])
        self.assertEqual(result["applied"], [])

    def test_a_fact_fix_that_drops_a_word_drops_its_mark(self):
        shot = marked()
        w = writer()
        w._ask = lambda name, prompt, schema: {"fixes": [
            {"n": 1, "action": "fix",
             "narration": "Schrödinger put a cat in a crate. The cat was fine.", "why": "w"}]}
        result = w.revise([shot], [{"n": 1, "severity": "important", "problem": "p",
                                    "fix": "f"}])
        self.assertEqual(result["applied"][0]["marks_dropped"],
                         [{"kind": "pause", "word": "box"}])
        self.assertEqual(shot.pauses, [])

    def test_marks_in_a_fix_for_another_rule_are_ignored(self):
        shot = marked()
        w = writer()
        w._ask = lambda name, prompt, schema: {"fixes": [
            {"n": 1, "action": "fix", "narration": shot.narration, "emphasis": ["box"],
             "why": "w"}]}
        w.revise([shot], [{"n": 1, "rule": "rule:clause", "tier": "important",
                           "severity": "important", "problem": "p", "fix": "f"}],
                 rules=True, name="revise-rules")
        self.assertEqual(shot.emphasis, ["cat", "fine"])


if __name__ == "__main__":
    unittest.main()
