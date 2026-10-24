"""What the screenplay's writer and fact-check are shown of the research.

A dossier can hold far more claims than a prompt should carry, so the script
sees a ranked selection. A run once ranked only by confidence: a claim the
outline was built on fell outside the cut, and the fact-check then called
the line resting on it unsourced -- a false blocker. The outline's own claims
always go in.
"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import script  # noqa: E402


def claim(text, confidence="high"):
    return {"claim": text, "detail": "", "source_title": "s",
            "source_url": f"https://h.example/{text.split()[-1]}",
            "source_kind": "primary", "confidence": confidence}


class ResearchBlock(unittest.TestCase):
    def block(self, data, limit):
        with contextlib.redirect_stderr(io.StringIO()):
            return script.research_block(data, limit=limit)

    def test_the_outlines_claims_are_never_dropped(self):
        claims = [claim(f"high claim number {n}") for n in range(10)]
        claims.append(claim("the outline needs this low one", "low"))
        data = {"claims": claims,
                "outline": {"sections": [{"claims_used": [
                    "The outline needs   this low one"]}]}}
        text = self.block(data, limit=3)
        self.assertIn("the outline needs this low one", text)
        for n in range(3):
            self.assertIn(f"high claim number {n}", text)
        self.assertNotIn("high claim number 3", text)

    def test_without_an_outline_the_best_sourced_are_kept(self):
        data = {"claims": [claim("weak one", "low"), claim("strong one", "high")]}
        text = self.block(data, limit=1)
        self.assertIn("strong one", text)
        self.assertNotIn("weak one", text)


# --------------------------------------------------------------------------- #
# AUTO-132: claim ids, the claim table, and the ceiling a claim puts on a line
# --------------------------------------------------------------------------- #

def rated(confidence="high", kind="secondary", standing=None, caveat=""):
    row = {"claim": "x", "detail": "", "source_title": "s", "source_url": "u",
           "source_kind": kind, "confidence": confidence, "caveat": caveat}
    if standing is not None:
        row["standing"] = standing
    return row


class ClaimIds(unittest.TestCase):
    def block(self, data, **kw):
        with contextlib.redirect_stderr(io.StringIO()):
            return script.research_block(data, **kw)

    def test_each_claim_is_listed_by_its_place_in_the_dossier(self):
        claims = [claim("first fact"), claim("second fact"), claim("third fact", "low")]
        data = {"claims": claims,
                "outline": {"sections": [{"claims_used": ["third fact"]}]}}
        text = self.block(data)
        lines = [line for line in text.splitlines() if line.startswith("- c")]
        # the outline's claim is listed first, and keeps its dossier id
        self.assertTrue(lines[0].startswith("- c3: third fact"), lines)
        self.assertTrue(lines[1].startswith("- c1: first fact"), lines)
        self.assertTrue(lines[2].startswith("- c2: second fact"), lines)

    def test_a_claims_standing_is_shown_where_it_has_one(self):
        data = {"claims": [dict(claim("a limit"), standing="limitation"),
                           claim("older claim")]}
        text = self.block(data)
        self.assertIn("standing limitation", text)
        self.assertEqual(text.count("standing"), 1)

    def test_without_ids_the_block_is_todays(self):
        data = {"claims": [claim("first fact"), dict(claim("x"), standing="hypothesis")]}
        text = self.block(data, ids=False)
        self.assertIn("- first fact", text)
        self.assertNotIn("c1:", text)
        self.assertNotIn("standing", text)

    def test_the_claim_table_has_the_same_ids_with_standing(self):
        data = {"claims": [claim("first fact"),
                           dict(claim("second"), standing="hypothesis", caveat="US only")]}
        table = script.claim_table(data)
        self.assertEqual(list(table), ["c1", "c2"])
        self.assertEqual(table["c2"], {"standing": "hypothesis", "confidence": "high",
                                       "caveat": "US only", "source_kind": "primary"})
        self.assertEqual(table["c1"]["standing"], "")
        self.assertEqual(script.claim_table({}), {})


class Ceiling(unittest.TestCase):
    def test_a_scope_caveat_leaves_an_established_finding_established(self):
        self.assertEqual(script.ceiling(rated("high", standing="established",
                                              caveat="US only")), "established")

    def test_medium_confidence_alone_never_caps_a_claim_with_standing(self):
        self.assertEqual(script.ceiling(rated("medium", standing="established")),
                         "established")

    def test_a_hypothesis_is_a_hypothesis_at_any_confidence(self):
        for confidence in ("high", "medium"):
            self.assertEqual(script.ceiling(rated(confidence, standing="hypothesis")),
                             "hypothesis")

    def test_a_limitation_is_a_hypothesis(self):
        self.assertEqual(script.ceiling(rated(standing="limitation")), "hypothesis")

    def test_low_confidence_or_unsourced_is_speculation_whatever_the_standing(self):
        for standing in ("established", "hypothesis", None):
            self.assertEqual(script.ceiling(rated("low", standing=standing)),
                             "speculation")
            self.assertEqual(script.ceiling(rated("high", "unsourced", standing)),
                             "speculation")

    def test_an_older_claim_is_judged_from_confidence_and_caveat(self):
        self.assertEqual(script.ceiling(rated("high")), "established")
        self.assertEqual(script.ceiling(rated("high", caveat="disputed")), "hypothesis")
        self.assertEqual(script.ceiling(rated("medium")), "hypothesis")

    def test_a_standing_off_the_list_is_read_as_none(self):
        self.assertEqual(script.ceiling(rated("high", standing="proven")), "established")
        self.assertEqual(script.ceiling(rated("medium", standing="proven")), "hypothesis")

    def test_no_claim_or_an_unknown_id_is_speculation(self):
        self.assertEqual(script.ceiling(None), "speculation")
        table = script.claim_table({"claims": [claim("one")]})
        self.assertEqual(script.ceiling(table.get("c9")), "speculation")

    def test_the_reason_names_what_capped_it(self):
        self.assertEqual(script.ceiling_reason(rated(standing="limitation")),
                         "the source calls it a limitation")
        self.assertEqual(script.ceiling_reason(rated("low")), "low confidence")
        self.assertEqual(script.ceiling_reason(None), "not in the research")


class Cap(unittest.TestCase):
    TABLE = {"c1": {"standing": "established", "confidence": "high", "caveat": "",
                    "source_kind": "primary"},
             "c7": {"standing": "", "confidence": "low", "caveat": "",
                    "source_kind": "secondary"}}

    def shot(self, **kw):
        return script.Shot(narration="line", **kw)

    def test_a_stored_ceiling_is_never_trusted(self):
        stale = self.shot(claim="c7", status="established", ceiling="established")
        script.cap([stale], self.TABLE)
        self.assertEqual(stale.ceiling, "speculation")

    def test_an_id_not_in_the_table_is_speculation_never_a_near_match(self):
        made_up = self.shot(claim="c71", status="established")
        script.cap([made_up], self.TABLE)
        self.assertEqual(made_up.ceiling, "speculation")

    def test_no_claim_on_an_unsourced_line_is_speculation(self):
        line = self.shot(claim="", source="UNSOURCED", status="established")
        script.cap([line], self.TABLE)
        self.assertEqual(line.ceiling, "speculation")

    def test_no_claim_on_a_cited_line_is_no_cap(self):
        line = self.shot(claim="", source="NASA 2019", status="established")
        script.cap([line], self.TABLE)
        self.assertEqual(line.ceiling, "")

    def test_without_a_dossier_there_is_no_cap(self):
        line = self.shot(claim="c7", status="established", ceiling="hypothesis")
        script.cap([line], None)
        self.assertEqual(line.ceiling, "")

    def test_a_loaded_screenplay_drops_its_stored_ceiling(self):
        data = {"sections": [{"heading": "h", "shots": [
            {"narration": "line", "claim": "c7", "status": "established",
             "ceiling": "established"}]}]}
        shot = script.sections_of(data)[0].shots[0]
        self.assertEqual((shot.claim, shot.status, shot.ceiling),
                         ("c7", "established", ""))


# --------------------------------------------------------------------------- #
# AUTO-149: the people the subject affected, by P id
# --------------------------------------------------------------------------- #

STORY = {"who": "Families of the 43 victims, Genoa",
         "what_happened": "lost relatives when the deck fell",
         "consequence": "Italy revoked the concession in 2020",
         "source": "https://example.org/morandi", "claim": "x"}


class HumanStories(unittest.TestCase):
    def block(self, data, **kw):
        with contextlib.redirect_stderr(io.StringIO()):
            return script.research_block(data, **kw)

    def test_the_kept_stories_are_numbered_beside_the_case(self):
        data = {"claims": [claim("one fact")],
                "identifiable_case": {"who": "The Morandi bridge", "source": "u"},
                "human_stories": [STORY, dict(STORY, who="Truck drivers on the A10",
                                              consequence="")]}
        text = self.block(data)
        self.assertIn("- P1: Families of the 43 victims, Genoa: lost relatives when the "
                      "deck fell; consequence: Italy revoked the concession in 2020 "
                      "(source: https://example.org/morandi)", text)
        self.assertIn("- P2: Truck drivers on the A10: lost relatives when the deck fell "
                      "(source: https://example.org/morandi)", text)
        # next to the identifiable case
        self.assertLess(text.index("An identifiable case"), text.index("- P1:"))

    def test_no_stories_no_block(self):
        self.assertNotIn("P1", self.block({"claims": [claim("one fact")],
                                           "human_stories": []}))


# --------------------------------------------------------------------------- #
# AUTO-149: press coverage by N id, apart from the licensed footage
# --------------------------------------------------------------------------- #

def article(headline, outlet="Reuters", date="03 Mar 2024", n=0):
    return {"kind": "article", "title": f"{headline} ({outlet})",
            "source": f"news/{outlet}", "licence": "All rights reserved",
            "credit_line": f"{outlet}, {date}", "note": "",
            "url": f"https://news.example/{n}", "reference_only": True}


def image(n):
    return {"kind": "image", "title": f"photo {n}", "licence": "CC0",
            "commercial_ok": True, "use_for": "the deck"}


class PressBlock(unittest.TestCase):
    def block(self, data, **kw):
        with contextlib.redirect_stderr(io.StringIO()):
            return script.research_block(data, **kw)

    def test_the_press_is_numbered_and_kept_out_of_the_footage(self):
        data = {"assets": [image(n) for n in range(70)]
                + [article("Genoa bridge inquiry names 59 defendants", n=1),
                   article("Bridge operator loses concession", "AP", "15 Jul 2020", 2)]}
        text = self.block(data)
        self.assertIn("Press coverage you may stage as a recreated headline card "
                      "(typeset, attributed, never a screenshot):\n"
                      "- N1: \"Genoa bridge inquiry names 59 defendants\" — Reuters, "
                      "03 Mar 2024 (https://news.example/1)\n"
                      "- N2: \"Bridge operator loses concession\" — AP, 15 Jul 2020 "
                      "(https://news.example/2)", text)
        footage = text.split("Licensed footage and imagery found by the research:\n", 1)[1]
        footage = footage.split("\n\n", 1)[0]
        self.assertNotIn("[article]", footage)
        self.assertEqual(footage.count("[image]"), 60)

    def test_an_older_dossier_differs_only_in_the_footage_heading(self):
        data = {"claims": [claim("one fact")], "assets": [image(1), image(2)],
                "misconceptions": [{"belief": "b", "source": "s"}],
                "identifiable_case": {"who": "w", "source": "s"}}
        text = self.block(data)
        old = text.replace("Licensed footage and imagery found by the research:",
                           "Footage and imagery already found and licensed:")
        self.assertNotEqual(text, old)
        self.assertNotIn("Press coverage", text)
        self.assertNotIn("P1", text)
        self.assertEqual(old, self.block(data, ids=True).replace(
            "Licensed footage and imagery found by the research:",
            "Footage and imagery already found and licensed:"))

    def test_without_ids_the_block_is_todays_whatever_the_dossier_holds(self):
        # a screenplay written before the ids is revised against the block it
        # was written from, so its cached revisions hold
        data = {"assets": [image(1), article("A headline", n=1)],
                "human_stories": [STORY]}
        text = self.block(data, ids=False)
        self.assertIn("Footage and imagery already found and licensed:", text)
        self.assertIn("[article] A headline (Reuters)", text)
        self.assertNotIn("Press coverage", text)
        self.assertNotIn("P1", text)


if __name__ == "__main__":
    unittest.main()
