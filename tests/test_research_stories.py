"""Research keeps the sourced human stories of its subject (AUTO-149, part A).

The plan was never asked who a subject affected or what followed, so a film
explained a mechanism with nobody in it. The explainer's plan and web pass
now ask for the people affected and the dated consequences, and the outline
returns up to three `human_stories`. None is trusted on the model's word: a
story is kept only when it quotes a dossier claim and its source is that
claim's or a page the research opened. The rest are dropped, each with a
warning, since no story is better than an invented one.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import research  # noqa: E402
from tests.test_research_levels import _Researched  # noqa: E402

CLAIM = {"claim": "The Morandi bridge collapsed on 14 August 2018, killing 43 people.",
         "source_url": "https://example.org/morandi", "confidence": "high"}
OTHER = {"claim": "Its stays were encased in concrete in the 1990s.",
         "source_url": "https://example.org/stays", "confidence": "medium"}
CLAIMS = [CLAIM, OTHER]
OPENED = ["https://example.org/inquiry", "https://example.org/morandi"]


def story(**kw):
    row = {"who": "Families of the 43 victims, Genoa",
           "what_happened": "lost relatives when the deck fell",
           "consequence": "Italy revoked Autostrade's concession in 2020",
           "source": "https://example.org/morandi",
           "claim": "the  Morandi bridge COLLAPSED on 14 August 2018, killing 43 people."}
    row.update(kw)
    return row


class Prompts(unittest.TestCase):
    def test_the_explainer_plan_asks_about_the_people_affected(self):
        plan = research.SCOPES["explainer"]["plan"].lower()
        self.assertIn("people the subject affected", plan)
        self.assertIn("what followed", plan)

    def test_the_explainer_outline_asks_for_human_stories_or_none(self):
        outline = research.SCOPES["explainer"]["outline"]
        self.assertIn("human_stories", outline)
        self.assertIn("leave the list empty rather than name a story no source "
                      "documents", outline)
        # the paper film is an explainer's outline with more
        self.assertIn("human_stories", research.SCOPES["paper"]["outline"])

    def test_the_cinematic_outline_returns_an_empty_list(self):
        outline = research.SCOPES["cinematic"]["outline"]
        self.assertIn("an empty human_stories", outline)

    def test_the_schema_requires_human_stories(self):
        self.assertIn("human_stories", research.OUTLINE_SCHEMA["required"])
        items = research.OUTLINE_SCHEMA["properties"]["human_stories"]
        self.assertEqual(items.get("maxItems"), 3)
        self.assertEqual(set(items["items"]["required"]),
                         {"who", "what_happened", "consequence", "source", "claim"})


class Kept(unittest.TestCase):
    def kept(self, *rows, opened=OPENED):
        return research.human_stories_of({"human_stories": list(rows)}, CLAIMS, opened)

    def test_a_story_on_a_dossier_claim_and_its_source_is_kept(self):
        kept, dropped = self.kept(story(), opened=[])
        self.assertEqual(dropped, [])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["who"], "Families of the 43 victims, Genoa")
        self.assertEqual(kept[0]["source"], "https://example.org/morandi")

    def test_a_story_whose_source_was_opened_is_kept(self):
        kept, dropped = self.kept(story(source="https://example.org/inquiry"))
        self.assertEqual((len(kept), dropped), (1, []))

    def test_a_story_on_no_claim_of_the_dossier_is_dropped(self):
        kept, dropped = self.kept(story(claim="Forty-four people died."))
        self.assertEqual(kept, [])
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["why"], "claim not in the dossier")

    def test_a_story_whose_source_was_never_opened_is_dropped(self):
        kept, dropped = self.kept(story(source="https://example.org/blog"))
        self.assertEqual(kept, [])
        self.assertEqual(dropped[0]["why"], "source never opened")

    def test_a_story_with_no_one_named_is_dropped(self):
        kept, dropped = self.kept(story(who="  "))
        self.assertEqual(kept, [])
        self.assertEqual(len(dropped), 1)

    def test_at_most_three_are_kept(self):
        kept, dropped = self.kept(*[story(who=f"Person {n}") for n in range(5)])
        self.assertEqual([k["who"] for k in kept], ["Person 0", "Person 1", "Person 2"])
        self.assertEqual(len(dropped), 2)

    def test_an_older_outline_has_none(self):
        self.assertEqual(research.human_stories_of({"sections": []}, CLAIMS, OPENED),
                         ([], []))
        self.assertEqual(research.human_stories_of(None, CLAIMS, OPENED), ([], []))


class InTheDossier(_Researched):
    def run_with(self, stories):
        r = self.researcher(jobs=1, parties=1)
        answer = self.harness.decide

        def decide(name, prompt, schema):
            row = answer(name, prompt, schema)
            if name == "03-claims":
                row["claims"] = CLAIMS
            if name == "07-outline":
                row["human_stories"] = stories
            return row
        self.harness.decide = decide
        r.visited = list(OPENED)
        return r.run()

    def test_the_dossier_keeps_the_backed_ones_and_warns_of_the_rest(self):
        data = self.run_with([story(), story(who="A blogger",
                                             source="https://example.org/blog")])
        self.assertEqual([s["who"] for s in data["human_stories"]],
                         ["Families of the 43 victims, Genoa"])
        self.assertIn("human story dropped: A blogger: source never opened",
                      data["warnings"])
        self.assertEqual(sum("human story dropped" in w for w in data["warnings"]), 1)

    def test_the_markdown_shows_people_and_consequences(self):
        data = self.run_with([story()])
        research.write_dossier(research.Dossier(self.root, self.idea, at=self.root),
                               data, [])
        md = (self.root / "README.md").read_text(encoding="utf-8")
        self.assertIn("## People and consequences", md)
        self.assertIn("Families of the 43 victims, Genoa", md)
        self.assertIn("Italy revoked Autostrade's concession in 2020", md)
        # a dropped story is not a provenance warning about the claims
        self.assertNotIn("human story dropped", md.split("## People", 1)[0])

    def test_none_found_is_an_empty_list_and_no_warning(self):
        data = self.run_with([])
        self.assertEqual(data["human_stories"], [])
        self.assertFalse([w for w in data["warnings"] if "human story" in w])


class TheWebPass(_Researched):
    def test_it_asks_for_accounts_of_the_people_affected_and_what_followed(self):
        from unittest import mock
        from claude_engine import Usage
        asked = []

        class Engine:
            usage = Usage()

            def stream(self, prompt):
                asked.append(prompt)
                return iter(())
        r = self.researcher(jobs=1, parties=1)
        with mock.patch.object(research.harness, "build_engine",
                               lambda *a, **k: Engine()):
            r._research_one({"question": "Who paid for the collapse?"})
        self.assertIn("named people affected", asked[0])
        self.assertIn("dated consequences", asked[0])
        self.assertIn("Flag anything you could not verify", asked[0])

if __name__ == "__main__":
    unittest.main()
