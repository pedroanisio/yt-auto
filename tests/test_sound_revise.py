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
                     file="sound/a.mp3", licence="CC0", credit="x", level="loud"),
           sound.Cue(kind="sfx", name="hit", at=10, seconds=2, purpose="q"),
           sound.Cue(kind="ambience", name="room", at=0, seconds=60, purpose="r")]
    return sound.number_cues(out)


def flag(cue, severity="important", problem="too loud"):
    return {"cue": cue, "problem": problem, "severity": severity, "fix": "lower"}


def fix(level="quiet", action="fix", why="ok"):
    return {"action": action, "level": level, "why": why}


def is_review(schema):
    return "verdict" in schema["properties"]


def rig(reviews, fixes):
    d = designer()
    calls = {"review": [], "revise": []}
    reviews, fixes = list(reviews), list(fixes)

    def ask(name, prompt, schema):
        which = "review" if is_review(schema) else "revise"
        calls[which].append(prompt)
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

    def test_the_cli_has_report_only(self):
        import argparse
        p = argparse.ArgumentParser()
        sub = p.add_subparsers()
        sound.register(sub)
        self.assertTrue(p.parse_args(["sound", "--report-only"]).report_only)


if __name__ == "__main__":
    unittest.main()
