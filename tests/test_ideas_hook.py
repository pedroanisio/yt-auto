"""AUTO-100: an idea starts from a premise the script can keep, not a title.

`yt ideas` used to ask only for a title that is "click-worthy but honest", so
a plan began from a title rather than a question. With `--rich` each idea now
also states the question its title raises and does not answer, the stakes,
what a typical viewer already believes, and the promise (title plus
thumbnail concept in one sentence), and `--rank` labels each idea's curiosity
gap and whether its title names a concrete subject beside the score
(docs/narrative: attention 1, 3, 4; structure 5, 6, 13). Concreteness and
curiosity are judgements, so they are labelled fields of the rank decision,
never a regex; the sort key stays `score`. The script's brief prints the
planned question, stakes and belief, and holds the promise frozen.

No model is called: `rank` is handed a fake decider, and the screenwriter's
`_ask` is replaced by one that records the prompt.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import ideas, script  # noqa: E402
from ytstudio.core import Idea, compact  # noqa: E402

PLANNED = ("question", "stakes", "viewer_belief", "promise")


def planned_idea(**extra) -> Idea:
    return Idea(
        title="Why the Tacoma Narrows bridge twisted itself apart",
        genre="explainer", description="Wind, not weight, killed it.",
        length="10 min",
        question="What made a steady 40 mph wind tear a steel bridge apart?",
        stakes="The same flutter limits every long span built since.",
        viewer_belief="Bridges fall because they are overloaded.",
        promise="Title plus a thumbnail of the deck twisted mid-air: you will "
                "see the wind make the bridge fly.", **extra)


class Decider:
    """Stands in for the harness: answers `rank` with the rows given."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.prompts: list[str] = []
        self.schemas: list[dict] = []

    def decide(self, name: str, prompt: str, schema: dict) -> dict:
        self.prompts.append(prompt)
        self.schemas.append(schema)
        return {"ranked": self.rows}


class RichSchema(unittest.TestCase):
    def test_the_rich_schema_requires_the_four_plan_fields(self):
        item = ideas.idea_schema(True)["properties"]["ideas"]["items"]
        for name in PLANNED:
            self.assertIn(name, item["properties"])
            self.assertEqual(item["properties"][name]["type"], "string")
            self.assertIn(name, item["required"])

    def test_without_rich_the_fields_are_not_asked_for(self):
        item = ideas.idea_schema(False)["properties"]["ideas"]["items"]
        for name in PLANNED:
            self.assertNotIn(name, item["properties"])
            self.assertNotIn(name, item["required"])

    def test_the_rich_prompt_asks_for_them(self):
        prompt = ideas.build_prompt(5, niche=None, genres=("explainer",), used=[],
                                    inspo=[], rich=True)
        low = prompt.lower()
        self.assertIn("question", low)
        self.assertIn("not answer", low)          # the title raises it, never answers it
        self.assertIn("stakes", low)
        self.assertIn("already believe", low)     # attention 3: what the viewer half-knows
        self.assertIn("promise", low)
        self.assertIn("thumbnail", low)           # structure 5: title plus thumbnail

    def test_the_plain_prompt_does_not(self):
        prompt = ideas.build_prompt(5, niche=None, genres=("explainer",), used=[],
                                    inspo=[], rich=False).lower()
        for word in ("stakes", "already believe", "promise"):
            self.assertNotIn(word, prompt)


class IdeaCarriesThem(unittest.TestCase):
    def test_from_raw_reads_and_trims_them(self):
        idea = Idea.from_raw({"title": "t", "genre": "g", "description": "d",
                              "question": "  why?  ", "stakes": " s ",
                              "viewer_belief": " b ", "promise": " p ",
                              "curiosity": " sustained ", "title_concrete": " concrete "})
        self.assertEqual((idea.question, idea.stakes, idea.viewer_belief, idea.promise),
                         ("why?", "s", "b", "p"))
        self.assertEqual((idea.curiosity, idea.title_concrete), ("sustained", "concrete"))

    def test_a_planned_idea_round_trips(self):
        idea = planned_idea(curiosity="brief", title_concrete="abstract")
        self.assertEqual(Idea.from_raw(compact(idea)), idea)


class RankLabels(unittest.TestCase):
    def test_rank_schema_has_the_two_labels_as_bounded_sets(self):
        row = ideas.RANK_SCHEMA["properties"]["ranked"]["items"]
        for name, labels in (("curiosity", ideas.CURIOSITY),
                             ("title_concrete", ideas.TITLE_CONCRETE)):
            self.assertIn(name, row["required"])
            self.assertEqual(row["properties"][name]["enum"], list(labels))
        self.assertEqual(len(ideas.CURIOSITY), 3)
        self.assertEqual(len(ideas.TITLE_CONCRETE), 2)
        self.assertEqual(set(ideas.TITLE_CONCRETE), {"concrete", "abstract"})

    def test_the_prompt_has_a_clause_for_each_label(self):
        decider = Decider([])
        ideas.rank(decider, [planned_idea()])
        prompt = decider.prompts[0]
        self.assertIn("curiosity", prompt)
        self.assertIn("title_concrete", prompt)
        for label in ideas.CURIOSITY + ideas.TITLE_CONCRETE:
            self.assertIn(label, prompt)
        # the gap is judged against what the idea plans, so it is shown
        self.assertIn("What made a steady 40 mph wind", prompt)
        self.assertIn("Bridges fall because they are overloaded", prompt)

    def test_labels_are_recorded_and_the_sort_stays_on_score(self):
        a = Idea(title="A", genre="g", description="d")
        b = Idea(title="B", genre="g", description="d")
        c = Idea(title="C", genre="g", description="d")
        decider = Decider([
            {"n": 1, "score": 3, "why": "w", "curiosity": "sustained",
             "title_concrete": "concrete"},
            {"n": 2, "score": 9, "why": "w", "curiosity": "none",
             "title_concrete": "abstract"},
            {"n": 3, "score": 6, "why": "w", "curiosity": "brief",
             "title_concrete": "concrete"},
        ])
        ranked = ideas.rank(decider, [a, b, c])
        self.assertEqual([i.title for i in ranked], ["B", "C", "A"])
        self.assertEqual((b.curiosity, b.title_concrete), ("none", "abstract"))
        self.assertEqual((a.curiosity, a.title_concrete), ("sustained", "concrete"))

    def test_a_label_outside_the_set_is_kept_as_written_and_said(self):
        a = Idea(title="A", genre="g", description="d")
        decider = Decider([{"n": 1, "score": 5, "why": "w", "curiosity": "huge",
                            "title_concrete": "concrete"}])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ideas.rank(decider, [a])
        self.assertEqual(a.curiosity, "huge")
        self.assertIn("huge", err.getvalue())
        self.assertIn("curiosity", err.getvalue())


class Printed(unittest.TestCase):
    def test_markdown_shows_the_plan_and_the_labels(self):
        idea = planned_idea(score=7, why="w", curiosity="sustained",
                            title_concrete="concrete")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ideas.md"
            ideas.write_md(str(path), [idea], niche=None, inspo=[])
            text = path.read_text(encoding="utf-8")
        for value in (idea.question, idea.stakes, idea.viewer_belief, idea.promise,
                      "sustained", "concrete"):
            self.assertIn(value, text)


class ScriptBrief(unittest.TestCase):
    def writer(self, idea: Idea, tmp: str, **kw) -> script.Screenwriter:
        return script.Screenwriter(idea, minutes=1, verbose=False, root=tmp, **kw)

    def test_the_brief_shows_question_stakes_belief_and_frozen_promise(self):
        idea = planned_idea()
        with tempfile.TemporaryDirectory() as tmp:
            brief = self.writer(idea, tmp)._brief()
        self.assertIn(f"Planned question: {idea.question}", brief)
        self.assertIn(f"Stakes: {idea.stakes}", brief)
        self.assertIn(idea.viewer_belief, brief)
        self.assertIn(idea.promise, brief)
        self.assertIn("frozen", brief.lower())

    def test_an_idea_without_them_keeps_the_old_brief(self):
        idea = Idea(title="t", genre="g", description="d")
        with tempfile.TemporaryDirectory() as tmp:
            brief = self.writer(idea, tmp)._brief()
        for word in ("Planned question", "Stakes", "believes", "romise"):
            self.assertNotIn(word, brief)

    def test_plan_tells_the_writer_the_promise_is_frozen(self):
        prompts = []

        def ask(name, prompt, schema):
            prompts.append(prompt)
            return {"sections": []}

        with tempfile.TemporaryDirectory() as tmp:
            writer = self.writer(planned_idea(), tmp)
            writer._ask = ask
            writer.plan()
            plain = self.writer(Idea(title="t", genre="g", description="d"), tmp)
            plain._ask = ask
            plain.plan()
        self.assertIn(script.FROZEN_PROMISE, prompts[0])
        self.assertNotIn(script.FROZEN_PROMISE, prompts[1])

    def test_the_outline_path_is_told_too(self):
        prompts = []

        def ask(name, prompt, schema):
            prompts.append(prompt)
            return {"promise": "the model's own", "sections": [{"connective": "open"}]}

        outline = {"sections": [{"heading": "h", "seconds": 60, "beats": ["b"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            writer = self.writer(planned_idea(), tmp, outline=outline)
            writer._ask = ask
            plan = writer.plan()
        self.assertIn(script.FROZEN_PROMISE, prompts[0])
        # the plan's promise stays the model's: nothing copies the idea's over it
        self.assertEqual(plan["promise"], "the model's own")


if __name__ == "__main__":
    unittest.main()
