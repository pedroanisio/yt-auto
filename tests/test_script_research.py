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
        self.assertNotIn("Press coverage", text)
        self.assertNotIn("P1", text)
        # develop's research_block for this dossier before AUTO-149, verbatim:
        # with the old footage heading swapped in, nothing else differs
        self.assertEqual(
            text.replace("Licensed footage and imagery found by the research:",
                         "Footage and imagery already found and licensed:"),
            "Every claim you may put on camera, with the source to cite for it and its "
            "id:\n- c1: one fact --  (source: s, https://h.example/fact; primary, high "
            "confidence)\n\nFootage and imagery already found and licensed:\n"
            "- [image] photo 1 (CC0, commercial ok) -- for the deck\n"
            "- [image] photo 2 (CC0, commercial ok) -- for the deck\n\n"
            "Misconceptions to correct out loud, with where people are documented "
            "believing them:\n- M1: b (source: s)\n\n"
            "An identifiable case the film can follow: w (source: s)")
        self.assertIn("Licensed footage and imagery found by the research:", text)

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


# --------------------------------------------------------------------------- #
# AUTO-133: the fact-check is on with a dossier, and read by another family
# --------------------------------------------------------------------------- #

import copy  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import tempfile  # noqa: E402
from unittest import mock  # noqa: E402

from ytstudio import cli, harness  # noqa: E402
from tests.test_script_plan import PLAN, kind, shots_for  # noqa: E402

DOSSIER = {"claims": [claim("the bolt was painted over")]}


class FactCheckByDefault(unittest.TestCase):
    """`yt script` against fake harnesses: every decision is answered by
    `Harness.decide` below, which records which harness (its role and its
    model) was asked. No engine is reached."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.film = self.root / "a-film"
        self.film.mkdir()
        (self.root / "work").mkdir()
        (self.film / "idea.json").write_text(
            '{"title": "Why the bridge fell", "genre": "g", "description": "d"}')
        #: what each verify* decision answers, in order
        self.checks: list[list[dict]] = []
        self.asked: list[tuple[str, str, str]] = []

    def dossier(self):
        (self.film / "dossier.json").write_text(json.dumps(DOSSIER))

    def who(self, h) -> str:
        return ("fact" if h.role == script.FACT_ROLE else
                "author" if h.role == script.SYSTEM else "other")

    def run_stage(self, *flags):
        test = self

        def decide(h, name, prompt, schema, **kw):
            test.asked.append((name, test.who(h), h.model))
            which = kind(schema)
            if which == "plan":
                return copy.deepcopy(PLAN)
            if which in ("shots", "function"):
                return shots_for(int(name.split("-")[1]), ("context", "payoff"))
            if "flags" in schema["properties"]:
                if name.startswith("verify-status"):
                    return {"flags": [], "verdict": "ok"}
                return {"flags": test.checks.pop(0) if test.checks else [],
                        "verdict": "defensible"}
            if "fixes" in schema["properties"]:
                if name == "revise-1":
                    return {"fixes": [{"n": 3, "action": "fix", "why": "w",
                                       "narration": "A bolt, painted over, held it."}]}
                return {"fixes": []}
            return {k: [] if v.get("type") == "array" else ""
                    for k, v in schema["properties"].items() if k in schema.get("required", [])}
        args = cli.build_parser().parse_args(
            ["script", "--project", str(self.film), "--work", str(self.root / "work"),
             "--quiet", *flags])
        with mock.patch.object(harness.Harness, "decide", decide), \
                contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = script.run(args)
        return code, out.getvalue() + err.getvalue()

    def names(self) -> list[str]:
        return [name for name, _, _ in self.asked]

    # -- the default ---------------------------------------------------------

    def test_with_a_dossier_and_no_flag_the_fact_check_runs(self):
        self.dossier()
        code, said = self.run_stage()
        self.assertEqual(code, 0, said)
        self.assertIn("verify", self.names())

    def test_no_verify_turns_it_off(self):
        self.dossier()
        code, said = self.run_stage("--no-verify")
        self.assertEqual(code, 0, said)
        self.assertFalse([n for n in self.names() if n.startswith("verify")], self.names())

    def test_without_a_dossier_nothing_is_checked_and_nothing_is_said(self):
        code, said = self.run_stage()
        self.assertEqual(code, 0, said)
        self.assertFalse([n for n in self.names() if n.startswith("verify")])
        self.assertNotIn("--verify needs research", said)

    def test_an_explicit_verify_with_no_dossier_still_warns(self):
        code, said = self.run_stage("--verify")
        self.assertIn("warning: --verify needs research", said)

    def test_the_flag_parses_three_ways(self):
        parse = cli.build_parser().parse_args
        self.assertIsNone(parse(["script"]).verify)
        self.assertTrue(parse(["script", "--verify"]).verify)
        self.assertFalse(parse(["script", "--no-verify"]).verify)

    # -- who reads it ----------------------------------------------------------

    def test_the_fact_check_is_asked_of_the_reviewer_and_the_revise_of_the_author(self):
        self.dossier()
        self.checks = [[{"n": 3, "severity": "important", "problem": "overstated",
                         "fix": "hedge"}], []]
        code, said = self.run_stage("--model", "sonnet")
        self.assertEqual(code, 0, said)
        by_name = {name: (who, model) for name, who, model in self.asked}
        self.assertEqual(by_name["verify"], ("fact", "opus"))
        self.assertEqual(by_name["verify-1"], ("fact", "opus"))
        self.assertEqual(by_name["revise-1"], ("author", "sonnet"))
        self.assertEqual(by_name["write-01"][1], "sonnet")
        record = json.loads((self.film / "script.json").read_text())
        self.assertEqual(record["check"]["model"], "opus")

    def test_the_reviewer_model_names_the_reader(self):
        self.dossier()
        code, said = self.run_stage("--model", "sonnet", "--reviewer-model", "haiku")
        self.assertEqual(code, 0, said)
        self.assertIn(("verify", "fact", "haiku"), self.asked)

    def test_without_a_dossier_the_fact_reviewer_is_never_built(self):
        seen = {}

        def run(self_, **kw):
            seen["fact"] = self_.fact_reviewer
            raise script.ClaudeError("stop here")
        with mock.patch.object(script.Screenwriter, "run", run):
            self.run_stage()
        self.assertIsNone(seen["fact"])
        # nor with an explicit --verify: there is nothing to check against
        with mock.patch.object(script.Screenwriter, "run", run):
            self.run_stage("--verify")
        self.assertIsNone(seen["fact"])
        self.dossier()
        with mock.patch.object(script.Screenwriter, "run", run):
            self.run_stage()
        self.assertEqual(seen["fact"].model, "opus")
        # with YT_GATEWAY unset it is a claude/codex CLI harness, as every
        # other reviewer of this stage is
        self.assertIsNone(seen["fact"].engine)
        self.assertIsNone(seen["fact"].gateway)

    def test_with_the_gateway_unset_the_fact_check_is_a_plain_cli_call(self):
        # The fact reviewer launches exactly what a plain CLI reviewer at
        # --reviewer-model launches: the same engine and the same config, so
        # its argv and child env are today's; the author's harness is
        # untouched by it. No engine is started.
        import claude_engine
        seen = {}

        def run(self_, **kw):
            seen["writer"] = self_
            raise script.ClaudeError("stop here")
        self.dossier()
        clean = {k: v for k, v in os.environ.items()
                 if not k.startswith(("YT_GATEWAY", "AI_GATEWAY")) and k != "YT_ENGINE"}
        with mock.patch.dict(os.environ, clean, clear=True), \
                mock.patch.object(script.Screenwriter, "run", run):
            self.run_stage("--model", "sonnet")
            fact, author = seen["writer"].fact_reviewer, seen["writer"].harness
            plain = harness.Harness(str(self.film), "script", model="opus",
                                    role=script.FACT_ROLE, verbose=False)
            got, want = fact._engine(tools=()), plain._engine(tools=())
        self.assertIsInstance(got, claude_engine.ClaudeEngine)
        self.assertEqual(got.config, want.config)
        self.assertEqual((fact.engine, fact.gateway), (None, None))
        self.assertEqual((author.model, author.role), ("sonnet", script.SYSTEM))
        self.assertIsNot(fact, author)

    # -- the family refusal ------------------------------------------------------

    def test_an_opus_author_with_a_dossier_is_refused_before_anything_is_paid(self):
        self.dossier()
        code, said = self.run_stage("--model", "opus")
        self.assertEqual(code, 2, said)
        self.assertEqual(self.asked, [])
        self.assertIn("the fact-check (on by default with a dossier) needs a reviewer of "
                      "another family than --model opus", said)
        self.assertIn("--reviewer-model", said)
        self.assertIn("--no-verify", said)
        self.assertFalse((self.film / "decisions").exists())

    def test_an_opus_author_without_the_fact_check_runs(self):
        self.dossier()
        code, said = self.run_stage("--model", "opus", "--no-verify")
        self.assertEqual(code, 0, said)
        self.assertIn("write-01", self.names())

    def test_an_explicit_verify_is_refused_in_the_review_wording(self):
        self.dossier()
        code, said = self.run_stage("--model", "opus", "--verify")
        self.assertEqual(code, 2, said)
        self.assertEqual(self.asked, [])
        self.assertIn("does not grade its own work", said)

    def test_review_with_the_authors_family_is_refused_as_today(self):
        self.dossier()
        code, said = self.run_stage("--model", "opus", "--reviewer-model", "opus",
                                    "--review", "--no-verify")
        self.assertEqual(code, 2, said)
        self.assertEqual(self.asked, [])
        self.assertIn("does not grade its own work", said)
        self.assertNotIn("on by default", said)

    def test_an_explicit_verify_with_no_dossier_is_not_refused(self):
        # nothing is fact-checked, so no reviewer is needed for it
        code, said = self.run_stage("--model", "opus", "--verify")
        self.assertEqual(code, 0, said)
        self.assertIn("warning: --verify needs research", said)
        self.assertNotIn("grade its own work", said)

    def test_a_revise_refuses_no_verify_with_a_dossier(self):
        # --revise always fact-checks its changed lines against the dossier;
        # --no-verify would read as turning that off, so it is refused, and
        # before the family refusal, which would offer only --reviewer-model.
        self.dossier()
        (self.film / "script.json").write_text(json.dumps({"sections": []}))
        for model in ("sonnet", "opus"):
            code, said = self.run_stage("--model", model, "--revise", "1.1", "--no-verify")
            self.assertEqual(code, 2, said)
            self.assertEqual(self.asked, [])
            self.assertIn("--revise always fact-checks the changed lines", said)
            self.assertIn("--no-verify", said)

    def test_a_revise_with_a_dossier_is_refused_for_the_authors_family(self):
        self.dossier()
        (self.film / "script.json").write_text(json.dumps({"sections": []}))
        code, said = self.run_stage("--model", "opus", "--revise", "1.1")
        self.assertEqual(code, 2, said)
        self.assertEqual(self.asked, [])
        self.assertIn("--reviewer-model", said)


if __name__ == "__main__":
    unittest.main()
