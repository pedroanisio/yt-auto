"""yt sound --review fixes its own findings, like the screenplay's verify.

No model is called: `SoundDesigner._ask` is replaced by a fake that answers by
the shape of the schema it is given.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import sound  # noqa: E402
from ytstudio.core import Idea  # noqa: E402


def designer():
    return sound.SoundDesigner(Idea(title="T", description="d", genre="g"),
                               runtime=60, verbose=False)


def cues():
    out = [sound.Cue(kind="music", name="bed", at=0, seconds=30, purpose="p",
                     file="sound/a.mp3", licence="CC0", credit="x", level="loud",
                     fade_in=1.0, fade_out=2.0),
           sound.Cue(kind="sfx", name="hit", at=10, seconds=2, purpose="q",
                     level="-18 dBFS peak"),
           sound.Cue(kind="ambience", name="room", at=0, seconds=60, purpose="r",
                     level="-40 dBFS", fade_out=3.0)]
    return sound.number_cues(out)


def flag(cue, severity="important", problem="too loud"):
    return {"cue": cue, "problem": problem, "severity": severity, "fix": "lower"}


def fix(level="quiet", action="fix", why="ok"):
    return {"action": action, "level": level, "why": why}


def is_review(schema):
    return "verdict" in schema["properties"]


def rig(reviews, fixes):
    d = designer()
    calls = {"review": [], "revise": [], "order": []}
    reviews, fixes = list(reviews), list(fixes)

    def ask(name, prompt, schema):
        which = "review" if is_review(schema) else "revise"
        calls[which].append(prompt)
        calls["order"].append(name)
        queue = reviews if which == "review" else fixes
        if not queue:
            raise AssertionError(f"unexpected {which} call")
        return queue.pop(0)
    d._ask = ask
    return d, calls


def m1(c):
    return [x for x in c if x.cue_id == "M1"][0]


def stats(c):
    return sound.coverage(c, 60)


class ReviewThenRevise(unittest.TestCase):
    def test_an_important_flag_is_fixed_in_one_run(self):
        c = cues()
        d, calls = rig([{"flags": [flag("M1")], "verdict": "no"},
                        {"flags": [], "verdict": "ok"}], [fix("quiet")])
        out = d.review_and_revise(c, stats(c))
        self.assertEqual(m1(c).level, "quiet")
        self.assertEqual(out["unresolved"], [])
        self.assertEqual(out["revisions"][0]["cue"], "M1")
        self.assertEqual(out["revisions"][0]["before"]["level"], "loud")
        self.assertEqual(out["flags"], [])
        self.assertEqual(out["recheck"], "ok")
        self.assertEqual(len(calls["review"]), 2)

    def test_file_contract_and_ids_are_never_touched(self):
        c = cues()
        d, _ = rig([{"flags": [flag("M1")], "verdict": "no"},
                    {"flags": [], "verdict": "ok"}],
                   [dict(fix(), file="evil.wav", licence="x", cue_id="Z9")])
        d.review_and_revise(c, stats(c))
        self.assertEqual((m1(c).file, m1(c).licence, m1(c).cue_id),
                         ("sound/a.mp3", "CC0", "M1"))

    def test_one_revise_call_per_important_flag_minor_ignored(self):
        c = cues()
        d, calls = rig([{"flags": [flag("M1"), flag("S1", "blocker"),
                                   flag("A1", "minor")], "verdict": "no"},
                        {"flags": [], "verdict": "ok"}],
                       [fix("a"), fix("b")])
        d.review_and_revise(c, stats(c))
        self.assertEqual(len(calls["revise"]), 2)

    def test_recheck_asks_only_about_the_revised_cues(self):
        c = cues()
        d, calls = rig([{"flags": [flag("M1"), flag("S1", "minor")],
                         "verdict": "no"},
                        {"flags": [flag("A1")], "verdict": "ok"}], [fix("a")])
        out = d.review_and_revise(c, stats(c), rounds=1)
        tail = calls["review"][1].split("Judge only")[1]
        self.assertIn("M1", tail)
        self.assertNotIn("S1", tail)
        # a flag for a cue that was not revised is not taken from the re-check
        self.assertEqual([f["cue"] for f in out["flags"]], ["S1"])

    def test_a_cue_that_cannot_be_fixed_is_kept_with_its_reason(self):
        c = cues()
        d, calls = rig([{"flags": [flag("M1")], "verdict": "no"}],
                       [fix(action="keep", why="deliberately loud")])
        out = d.review_and_revise(c, stats(c))
        self.assertEqual(m1(c).level, "loud")
        self.assertEqual(out["kept"], [{"cue": "M1", "why": "deliberately loud"}])
        self.assertEqual(out["flags"][0]["kept"], "deliberately loud")
        self.assertEqual(out["unresolved"], [])
        self.assertEqual(len(calls["review"]), 1)   # nothing changed: no re-check

    def test_a_failed_revise_call_keeps_the_flag(self):
        c = cues()
        d = designer()

        def ask(name, prompt, schema):
            if is_review(schema):
                return {"flags": [flag("M1")], "verdict": "no"}
            raise sound.ClaudeError("down")
        d._ask = ask
        out = d.review_and_revise(c, stats(c))
        self.assertEqual(m1(c).level, "loud")
        self.assertTrue(out["flags"][0]["kept"])

    def test_the_round_limit_stops_and_keeps_what_is_left(self):
        c = cues()
        again = {"flags": [flag("M1")], "verdict": "still"}
        d, calls = rig([{"flags": [flag("M1")], "verdict": "no"}, again, again],
                       [fix("a"), fix("b"), fix("c")])
        out = d.review_and_revise(c, stats(c), rounds=2)
        self.assertEqual(len(calls["revise"]), 2)
        self.assertEqual(len(calls["review"]), 3)
        self.assertEqual(len(out["revisions"]), 2)
        self.assertEqual(len(out["unresolved"]), 1)
        self.assertIn("2 round", out["unresolved"][0]["kept"])

    def test_report_only_changes_nothing(self):
        c = cues()
        d, calls = rig([{"flags": [flag("M1")], "verdict": "no"}], [])
        out = d.review_and_revise(c, stats(c), fix=False)
        self.assertEqual(m1(c).level, "loud")
        self.assertEqual(calls["revise"], [])
        self.assertEqual(len(out["flags"]), 1)
        self.assertNotIn("revisions", out)

    def test_timing_edits_are_clamped_to_the_runtime(self):
        c = cues()
        d, _ = rig([{"flags": [flag("S1")], "verdict": "no"},
                    {"flags": [], "verdict": "ok"}],
                   [{"action": "fix", "at": 500, "seconds": 500, "why": "move"}])
        d.review_and_revise(c, stats(c))
        sfx = [x for x in c if x.cue_id == "S1"][0]
        self.assertLessEqual(sfx.out, 60)

    def test_run_revises_and_refreshes_coverage(self):
        d = designer()
        d.plan = lambda: {}
        d.spot_whole = cues
        reviews = [{"flags": [flag("M1")], "verdict": "no"},
                   {"flags": [], "verdict": "ok"}]

        def ask(name, prompt, schema):
            if is_review(schema):
                return reviews.pop(0)
            return {"action": "fix", "seconds": 10, "why": "shorter"}
        d._ask = ask
        data = d.run(do_review=True)
        by_id = {c["cue_id"]: c for c in data["cues"]}
        self.assertEqual(by_id["M1"]["seconds"], 10)
        self.assertEqual(by_id["M1"]["file"], "sound/a.mp3")
        self.assertEqual(data["coverage"]["music_seconds"], 10)
        self.assertEqual(data["review"]["revisions"][0]["cue"], "M1")

    def test_run_report_only_is_the_old_behaviour(self):
        d = designer()
        d.plan = lambda: {}
        d.spot_whole = cues

        def ask(name, prompt, schema):
            if is_review(schema):
                return {"flags": [flag("M1")], "verdict": "no"}
            self.fail("revised")
        d._ask = ask
        data = d.run(do_review=True, fix=False)
        self.assertEqual(len(data["review"]["flags"]), 1)


class RuleFindings(unittest.TestCase):
    """The sheet's own rules (sound.rule_findings) are answered by `revise`
    before the model is asked to listen back; a rule kept is unresolved."""

    def sheet(self):
        c = cues()
        c.append(sound.Cue(kind="music", name="second bed", at=20, seconds=20,
                           purpose="s", level="-30 dBFS", fade_out=2.0))
        return sound.number_cues(c)

    def test_rule_flags_are_revised_before_the_first_review(self):
        c = self.sheet()
        d, calls = rig([{"flags": [], "verdict": "ok"}],
                       [{"action": "fix", "at": 30, "why": "after the first bed"}])
        out = d.review_and_revise(c, stats(c))
        self.assertEqual(calls["order"][:2], ["revise-M2", "review"])
        self.assertIn("rule:overlap", calls["revise"][0])
        self.assertEqual([x.at for x in c if x.cue_id == "M2"], [30])
        self.assertEqual(out["revisions"][0]["cue"], "M2")
        self.assertEqual(out["revisions"][0]["rule"], "rule:overlap")
        self.assertEqual(out["unresolved"], [])
        self.assertEqual(out["flags"], [])

    def test_the_review_sees_the_sheet_after_the_rules_were_answered(self):
        c = self.sheet()
        d, calls = rig([{"flags": [], "verdict": "ok"}],
                       [{"action": "fix", "at": 30, "why": "after the first bed"}])
        d.review_and_revise(c, stats(c))
        self.assertIn("0:30-0:50", calls["review"][0])

    def test_a_kept_rule_flag_is_unresolved_with_its_reason(self):
        c = self.sheet()
        d, calls = rig([{"flags": [], "verdict": "ok"}],
                       [fix(action="keep", why="the overlap is the handover")])
        out = d.review_and_revise(c, stats(c))
        self.assertEqual(len(calls["review"]), 1)
        self.assertEqual([(f["cue"], f["rule"], f["kept"]) for f in out["unresolved"]],
                         [("M2", "rule:overlap", "the overlap is the handover")])
        self.assertEqual([f["cue"] for f in out["flags"]], ["M2"])
        self.assertEqual(out["kept"], [{"cue": "M2", "why": "the overlap is the handover",
                                        "rule": "rule:overlap"}])

    def test_a_fix_that_still_breaks_the_rule_stays_unresolved(self):
        c = self.sheet()
        d, _ = rig([{"flags": [], "verdict": "ok"}],
                   [{"action": "fix", "at": 25, "why": "nudged"}])
        out = d.review_and_revise(c, stats(c))
        self.assertEqual(len(out["revisions"]), 1)
        self.assertEqual([f["rule"] for f in out["unresolved"]], ["rule:overlap"])
        self.assertIn("still", out["unresolved"][0]["kept"])

    def bare_bed(self):
        # a bed with no level usually has no fade either: two rules on M1
        c = cues()
        m1(c).level, m1(c).fade_out = "", 0.0
        return c

    def test_two_rules_on_one_cue_cleared_by_one_change_are_asked_once(self):
        c = self.bare_bed()
        d, calls = rig([{"flags": [], "verdict": "ok"}],
                       [{"action": "fix", "level": "-24 dBFS", "fade_out": 2.0,
                         "why": "levelled and faded"}])
        out = d.review_and_revise(c, stats(c))
        self.assertEqual(calls["order"], ["revise-M1", "review"])
        self.assertEqual([r["rule"] for r in out["revisions"]], ["rule:level"])
        self.assertEqual(out["kept"], [])
        self.assertEqual(out["unresolved"], [])

    def test_a_rule_the_change_left_standing_is_still_asked_on_the_sheet_as_it_is(self):
        c = self.bare_bed()
        d, calls = rig([{"flags": [], "verdict": "ok"}],
                       [{"action": "fix", "level": "-24 dBFS", "why": "levelled"},
                        {"action": "fix", "fade_out": 2.0, "why": "faded"}])
        out = d.review_and_revise(c, stats(c))
        self.assertEqual(calls["order"], ["revise-M1", "revise-M1", "review"])
        self.assertIn("rule:fade", calls["revise"][1])
        self.assertIn("'level': '-24 dBFS'", calls["revise"][1])
        self.assertEqual([r["rule"] for r in out["revisions"]], ["rule:level", "rule:fade"])
        self.assertEqual(out["unresolved"], [])

    def test_the_review_is_told_what_the_rules_kept_and_why(self):
        c = self.sheet()
        d, calls = rig([{"flags": [], "verdict": "ok"}],
                       [fix(action="keep", why="the overlap is the handover")])
        d.review_and_revise(c, stats(c))
        self.assertIn("M2 rule:overlap: the overlap is the handover", calls["review"][0])
        self.assertNotIn("by the timecodes alone", calls["review"][0])

    def test_a_kept_rule_does_not_stop_the_models_own_flag_on_that_cue(self):
        c = self.sheet()
        d, calls = rig([{"flags": [flag("M2")], "verdict": "no"},
                        {"flags": [], "verdict": "ok"}],
                       [fix(action="keep", why="handover"), fix("quiet")])
        out = d.review_and_revise(c, stats(c))
        self.assertEqual(len(calls["revise"]), 2)
        self.assertEqual([x.level for x in c if x.cue_id == "M2"], ["quiet"])
        self.assertEqual([f["rule"] for f in out["unresolved"]], ["rule:overlap"])

    def test_a_models_revision_that_clears_a_kept_rule_resolves_it(self):
        # the rules are read again after the model's own loop: M2 kept its
        # overlap at the rule stage, then the model's flag on M2 moved it clear
        c = self.sheet()
        d, calls = rig([{"flags": [flag("M2")], "verdict": "no"},
                        {"flags": [], "verdict": "ok"}],
                       [fix(action="keep", why="handover"),
                        {"action": "fix", "at": 30, "why": "moved after the first bed"}])
        out = d.review_and_revise(c, stats(c))
        self.assertEqual(len(calls["revise"]), 2)
        self.assertEqual([x.at for x in c if x.cue_id == "M2"], [30])
        self.assertEqual(out["unresolved"], [])
        self.assertEqual(out["flags"], [])
        self.assertEqual(out["kept"], [])     # the keep's problem is gone with it

    def test_a_models_revision_that_breaks_a_rule_is_unresolved_and_named(self):
        # no rule broken on the sheet, then the model's own fix for M2 moves it
        # onto M1: nothing asked the model about that overlap, and build must
        # still see it
        c = cues()
        c.append(sound.Cue(kind="music", name="second bed", at=30, seconds=20,
                           purpose="s", level="-30 dBFS", fade_out=2.0))
        c = sound.number_cues(c)
        d, calls = rig([{"flags": [flag("M2")], "verdict": "no"},
                        {"flags": [], "verdict": "ok"}],
                       [{"action": "fix", "at": 20, "why": "earlier"}])
        out = d.review_and_revise(c, stats(c))
        self.assertEqual(len(calls["revise"]), 1)
        self.assertEqual([(f["cue"], f["rule"]) for f in out["unresolved"]],
                         [("M2", "rule:overlap")])
        self.assertEqual(out["unresolved"][0]["kept"],
                         "introduced by a revision; not answered")
        self.assertEqual([f["rule"] for f in out["flags"]], ["rule:overlap"])

    def test_report_only_lists_the_rule_flags_and_revises_nothing(self):
        c = self.sheet()
        d, calls = rig([{"flags": [flag("M1")], "verdict": "no"}], [])
        out = d.review_and_revise(c, stats(c), fix=False)
        self.assertEqual(calls["revise"], [])
        self.assertEqual([(f["cue"], f.get("rule")) for f in out["flags"]],
                         [("M1", None), ("M2", "rule:overlap")])

    def test_a_failed_review_still_reports_what_the_rules_changed(self):
        c = self.sheet()
        d = designer()

        def ask(name, prompt, schema):
            if is_review(schema):
                raise sound.ClaudeError("down")
            return {"action": "fix", "at": 30, "why": "moved"}
        d._ask = ask
        out = d.review_and_revise(c, stats(c))
        self.assertEqual(out["revisions"][0]["cue"], "M2")
        self.assertEqual(out["unresolved"], [])

    def test_report_only_lists_the_rule_flags_when_the_review_fails(self):
        c = self.sheet()
        d = designer()

        def ask(name, prompt, schema):
            raise sound.ClaudeError("down")
        d._ask = ask
        out = d.review_and_revise(c, stats(c), fix=False)
        self.assertEqual([(f["cue"], f.get("rule")) for f in out["flags"]],
                         [("M2", "rule:overlap")])
        self.assertEqual(out["verdict"], "")

    def test_run_carries_rule_results_into_the_record(self):
        d = designer()
        d.plan = lambda: {}
        d.spot_whole = self.sheet

        def ask(name, prompt, schema):
            if is_review(schema):
                return {"flags": [], "verdict": "ok"}
            return {"action": "keep", "why": "handover"}
        d._ask = ask
        data = d.run(do_review=True)
        self.assertEqual([f["rule"] for f in data["review"]["unresolved"]],
                         ["rule:overlap"])

    def test_the_cli_has_report_only(self):
        import argparse
        p = argparse.ArgumentParser()
        sub = p.add_subparsers()
        sound.register(sub)
        self.assertTrue(p.parse_args(["sound", "--report-only"]).report_only)


if __name__ == "__main__":
    unittest.main()
