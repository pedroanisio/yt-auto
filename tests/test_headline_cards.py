"""A press headline staged as a recreated card (AUTO-149, part B).

A shot that sets `asset` to "headline [Nk]" quotes press item Nk
(`assets.press_items`) verbatim with its "outlet, date" source line. The
narration rules would shorten a 12-word headline (rule:wall-of-text, six
words a string) and a verbatim check would then flag the shortened text, so
with the press list given the rules leave the cited headline and its source
line alone, and `headline_findings` checks them instead: verbatim, the outlet
named, held long enough to read. Without a press list every rule is as it
was. No model is called: `Screenwriter._ask` is a fake.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import assets, cli, script  # noqa: E402
from ytstudio.core import Idea, write_json  # noqa: E402

HEADLINE = "Inquiry finds Genoa bridge operator skipped inspections for years before the collapse"
SOURCE = "Reuters, 03 Mar 2024"
RECORD = {"kind": "article", "title": f"{HEADLINE} (Reuters)", "source": "news/Reuters",
          "licence": assets.NEWS_REFERENCE, "credit_line": SOURCE,
          "note": "Sun, 03 Mar 2024 08:00:00 GMT", "url": "https://news.example/1",
          "page_url": "https://news.example/1", "reference_only": True}
PRESS = assets.press_items([RECORD])
PLAN = {"logline": "l", "promise": "You will see the bolt that brought the bridge down.",
        "payoff": "the bolt",
        "ending": {"payoff_line": "One bolt held up four lanes.", "callback": "",
                   "next_film": ""},
        "sections": [{"heading": "One", "seconds": 10, "beats": []}]}
CARD_RULES = ("rule:wall-of-text", "rule:legibility", "rule:unspoken-number",
              "source:headline")


def card(supers=(HEADLINE, SOURCE), seconds=8, asset="headline [N1]", n=1):
    return script.Shot(narration="The bridge you cross fell at dawn.", n=n, label=f"1.{n}",
                       seconds=seconds, asset=asset, on_screen_text=list(supers),
                       function="hook")


def sections(*shots):
    return [script.Section(heading="One", shots=list(shots))]


def found(shots, press=PRESS):
    secs = sections(*shots)
    flags = script.rule_findings({"idea": {"title": "Why the bridge fell"}}, secs,
                                 press=press)
    if press is not None:
        flags += script.headline_findings(secs, press, 150)
    return [f for f in flags if f["rule"] in CARD_RULES]


class Data(unittest.TestCase):
    def test_the_fixture_headline_is_twelve_words_and_n1(self):
        self.assertEqual(len(HEADLINE.split()), 12)
        self.assertEqual(PRESS[0]["id"], "N1")
        self.assertEqual(PRESS[0]["headline"], HEADLINE)

    def test_the_cited_headline_is_read_from_the_asset(self):
        self.assertEqual(script.cited_headline(card(), PRESS)["headline"], HEADLINE)
        self.assertIsNone(script.cited_headline(card(asset="a photo"), PRESS))
        self.assertIsNone(script.cited_headline(card(asset="headline [N9]"), PRESS))
        self.assertIsNone(script.cited_headline(card(), None))


class Rules(unittest.TestCase):
    def test_a_verbatim_attributed_card_held_long_enough_raises_nothing(self):
        self.assertEqual(found([card()]), [])

    def test_curly_quotes_and_spacing_are_still_verbatim(self):
        self.assertEqual(found([card(supers=(f"“{HEADLINE.replace(' ', '  ', 2)}”",
                                             SOURCE))]), [])

    def test_without_a_press_list_the_wall_of_text_flag_is_todays(self):
        flags = found([card()], press=None)
        self.assertEqual([f["rule"] for f in flags],
                         ["rule:unspoken-number", "rule:unspoken-number",
                          "rule:wall-of-text"])
        self.assertIn("12 words on screen, over 6", flags[-1]["problem"])

    def test_figures_in_a_cited_headline_are_not_unspoken_numbers(self):
        # a quotation keeps its figures; "take it off the screen" would break it
        record = dict(RECORD, title="Genoa inquiry names 59 defendants (Reuters)")
        press = assets.press_items([record])
        self.assertEqual(found([card(supers=("Genoa inquiry names 59 defendants", SOURCE))],
                               press=press), [])
        # a figure in another string on the card is still measured
        flags = found([card(supers=("Genoa inquiry names 59 defendants", SOURCE, "43"))],
                      press=press)
        self.assertEqual([f["rule"] for f in flags], ["rule:unspoken-number"])

    def test_without_a_press_list_rule_findings_is_unchanged_byte_for_byte(self):
        secs = sections(card(), card(supers=("Nine words that are far too many to read",),
                                     n=2, asset=""))
        plan = {"idea": {"title": "Why the bridge fell"}}
        self.assertEqual(script.rule_findings(plan, secs),
                         script.rule_findings(plan, secs, press=None))
        self.assertEqual(script.legibility(secs), script.legibility(secs, press=None))

    def test_other_strings_on_the_card_are_still_measured(self):
        flags = found([card(supers=(HEADLINE, SOURCE,
                                    "a label far longer than six words on screen"))])
        self.assertEqual([f["rule"] for f in flags], ["rule:wall-of-text"])
        self.assertIn("a label far longer", flags[0]["problem"])

    def test_a_paraphrased_headline_is_one_flag_quoting_the_headline(self):
        flags = found([card(supers=("Genoa operator skipped checks", SOURCE))])
        self.assertEqual([f["rule"] for f in flags], ["source:headline"])
        self.assertIn(f"\"{HEADLINE}\"", flags[0]["problem"])
        self.assertEqual((flags[0]["tier"], flags[0]["severity"], flags[0]["n"]),
                         ("important", "important", 1))
        self.assertTrue(flags[0]["problem"].startswith("source:headline: shot 1.1: "))

    def test_a_missing_outlet_is_one_flag(self):
        flags = found([card(supers=(HEADLINE,))])
        self.assertEqual([f["rule"] for f in flags], ["source:headline"])
        self.assertIn("Reuters", flags[0]["problem"])

    def test_a_hold_too_short_is_one_flag_that_never_asks_for_a_shorter_headline(self):
        flags = found([card(seconds=4)])
        self.assertEqual([f["rule"] for f in flags], ["source:headline"])
        self.assertIn("never shorten the headline", flags[0]["fix"])
        # and without the press list, today's legibility flag says shorten it
        self.assertIn("rule:legibility", [f["rule"] for f in found([card(seconds=4)],
                                                                    press=None)])

    def test_a_shot_citing_nothing_gets_no_headline_flag(self):
        shot = card(supers=("Dawn",), asset="a photo of the deck")
        self.assertEqual(script.headline_findings(sections(shot), PRESS, 150), [])

    def test_flag_lines_renders_a_headline_flag(self):
        flags = found([card(seconds=4)])
        lines = script.flag_lines(flags)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("warning: source:headline: shot 1.1: "), lines)

    def test_the_headline_flag_may_change_the_screen(self):
        self.assertIn("source:headline", script.SCREEN_RULES)


class Unknown(unittest.TestCase):
    def test_an_unknown_id_is_cleared_from_the_asset_with_one_line(self):
        shots = [card(asset="headline [N9]"), card(n=2)]
        lines = script.clear_unknown_headlines(shots, PRESS)
        self.assertEqual(lines, ["warning: source:headline: shot 1.1 cited N9, which the "
                                 "dossier does not hold; citation removed"])
        self.assertEqual(shots[0].asset, "headline")
        self.assertEqual(shots[1].asset, "headline [N1]")
        self.assertEqual(script.clear_unknown_headlines(shots, PRESS), [])

    def test_without_a_press_list_nothing_is_cleared(self):
        shots = [card(asset="headline [N9]")]
        self.assertEqual(script.clear_unknown_headlines(shots, None), [])
        self.assertEqual(shots[0].asset, "headline [N9]")


class Stage(unittest.TestCase):
    """The stage's own loop: revise_rules and the final re-read."""

    def make(self, shots, fixes=(), press=PRESS):
        w = script.Screenwriter(Idea(title="Why the bridge fell", description="d",
                                     genre="g"),
                                research="", verbose=False, jobs=1, press=press)
        w.plan = lambda: PLAN
        payoff = script.Shot(narration="One bolt held up four lanes.", seconds=5,
                             function="payoff")
        w.write_section = lambda *a, **k: [script.Shot(**{**s.__dict__}) for s in shots] \
            + [payoff]
        calls, answers = [], list(fixes)

        def ask(name, prompt, schema):
            calls.append((name, prompt))
            if "fixes" in schema["properties"]:
                return answers.pop(0) if answers else {"fixes": []}
            raise AssertionError(name)
        w._ask = ask
        return w, calls

    def test_revise_rules_restores_a_paraphrased_headline(self):
        paraphrase = card(supers=("Genoa operator skipped checks", SOURCE))
        w, calls = self.make([paraphrase], [{"fixes": [
            {"n": 1, "action": "fix", "narration": paraphrase.narration,
             "on_screen_text": [HEADLINE, SOURCE], "why": "the headline verbatim"}]}])
        data = w.run()
        self.assertEqual([c[0] for c in calls], ["revise-rules"])
        self.assertIn(f"\"{HEADLINE}\"", calls[0][1])
        self.assertEqual(data["sections"][0]["shots"][0]["on_screen_text"],
                         [HEADLINE, SOURCE])
        standing = [f for f in data["findings"]
                    if "source:headline" in f or "wall-of-text" in f]
        self.assertEqual(standing, [])
        # the restored 12 words never went back as a wall of text
        self.assertFalse(any("rule:wall-of-text" in c[1] for c in calls))

    def test_a_revise_that_keeps_it_leaves_a_standing_warning(self):
        paraphrase = card(supers=("Genoa operator skipped checks", SOURCE))
        w, _ = self.make([paraphrase])
        data = w.run()
        lines = [f for f in data["findings"] if "source:headline" in f]
        self.assertEqual(len(lines), 1, data["findings"])
        self.assertTrue(lines[0].startswith("warning: source:headline: shot 1.1"))

    def test_an_unknown_id_is_removed_and_reported(self):
        w, _ = self.make([card(asset="headline [N9]")])
        data = w.run()
        self.assertEqual(data["sections"][0]["shots"][0]["asset"], "headline")
        self.assertIn("warning: source:headline: shot 1.1 cited N9, which the dossier "
                      "does not hold; citation removed", data["findings"])
        # its strings are under the normal rules again
        self.assertTrue(any("rule:wall-of-text" in f for f in data["findings"]))

    def test_without_press_the_shot_fields_are_todays_byte_for_byte(self):
        # sha256 of the field list as develop wrote it before AUTO-149: the
        # write decisions of a film with no press are asked, and cached, as
        # they were
        import hashlib
        for press in (None, []):
            w, _ = self.make([card()], press=press)
            self.assertEqual(hashlib.sha256(w._shot_fields().encode()).hexdigest(),
                             "bf59f7cfba08e6dfae019b20e724e80d818dcbcfc7432f82616a1d9b64b6df70")

    def test_the_write_prompt_tells_how_to_stage_a_headline_only_with_press(self):
        w, _ = self.make([card()])
        self.assertIn("headline [Nk]", w._shot_fields())
        w, _ = self.make([card()], press=None)
        self.assertNotIn("headline [Nk]", w._shot_fields())


class Revise(unittest.TestCase):
    """`yt script --revise` re-reads the changed shots with the press list."""

    def setUp(self):
        from tests import test_script_revise_labels as labels
        self.labels = labels
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name) / "work"
        self.film = self.work / "the-bridge"
        self.film.mkdir(parents=True)
        (self.film / "idea.json").write_text(json.dumps(
            {"title": labels.TITLE, "genre": "g", "description": "d"}))
        data = labels.screenplay()
        first = data["sections"][0]["shots"][0]
        first.update(asset="headline [N1]", on_screen_text=[HEADLINE, SOURCE], seconds=8)
        write_json(self.film / "script.json", data)
        write_json(self.film / "dossier.json", {"idea": {"title": labels.TITLE},
                                                "claims": [], "assets": [RECORD]})
        self.calls = []

    def run_stage(self, supers):
        args = cli.build_parser().parse_args(
            ["script", "--project", str(self.film), "--work", str(self.work), "--quiet",
             "--revise", "1.1,1.3"])
        calls, labels = self.calls, self.labels

        def ask(writer, name, prompt, schema):
            calls.append(name)
            if "fixes" in schema["properties"]:
                # 1.1's card changes; 1.3's long sentence (an error-tier rule
                # in the fixture) is fixed so the stage exits 0
                return {"fixes": [{"n": 1, "action": "fix", "narration": "",
                                   "on_screen_text": supers, "why": "shorter"},
                                  {"n": 3, "action": "fix", "narration": labels.FIXED,
                                   "why": "one breath"}]
                        if name == "revise-labels" else []}
            return {"flags": [], "verdict": "defensible"}
        with mock.patch.object(script.Screenwriter, "_ask", ask), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return script.run(args)

    def test_a_changed_shot_that_broke_its_headline_is_flagged(self):
        self.assertEqual(self.run_stage(["Operator skipped checks", SOURCE]), 0)
        data = json.loads((self.film / "script.json").read_text())
        lines = [f for f in data["findings"] if "source:headline" in f]
        self.assertEqual(len(lines), 1, data["findings"])
        self.assertIn("shot 1.1", lines[0])
        self.assertIn("source:headline", [f["rule"] for f in data["rules"]["flags"]])

    def test_a_changed_shot_that_keeps_its_headline_is_not(self):
        self.assertEqual(self.run_stage([HEADLINE, SOURCE, "Genoa"]), 0)
        data = json.loads((self.film / "script.json").read_text())
        self.assertFalse([f for f in data["findings"]
                          if "source:headline" in f or "wall-of-text" in f],
                         data["findings"])


if __name__ == "__main__":
    unittest.main()
