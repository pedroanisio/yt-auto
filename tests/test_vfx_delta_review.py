"""AUTO-78: the vfx reviewer of a revision sees the round before it -- its
score, its findings and its proof frames -- and judges the revision better,
same or worse; promotion follows that chain, not the absolute score, whose
round-to-round noise (6.5 against 5.5 on the same look) is as large as the
gains. A revision judged the same or worse ends the loop: nothing more is
paid for after a zero-delta round. The listing an author gets leads with what
persists. The model is never called and the render host is a stub."""

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ytstudio import harness, vfx
from ytstudio.core import Idea
from ytstudio.research import Dossier
from tests.test_vfx_world import EFFECTS, SECTION, STYLE, WORLD_DOC, FakeEngine, varied_png

FINDING = {"category": "cinematography", "problem": "the hero is a sliver",
           "fix": "bring the camera to 60 cm behind the hero", "severity": "blocking"}


def review(score, verdict=None, status=None, findings=()):
    """A scripted review; with `verdict` it carries the delta the schema asks
    for from round 1 on."""
    out = {"frames": [{"frame": "frames/a.png", "shot": "1.2", "seen": "a wedge",
                       "findings": list(findings)}],
           "score": score, "summary": f"scored {score}"}
    if verdict:
        out["previous"] = {"verdict": verdict,
                           "findings": [{"problem": FINDING["problem"],
                                         "status": status or "persists"}]}
    return out


class Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._engine = harness.ENGINE
        harness.ENGINE = FakeEngine
        FakeEngine.stream_calls, FakeEngine.json_calls, FakeEngine.reviews = [], [], []
        FakeEngine.doc, FakeEngine.docs = WORLD_DOC, []
        FakeEngine.time_out_on = FakeEngine.crash_on = 0
        self.idea = Idea(title="T", genre="g", description="d")
        self.schemas: list[tuple[str, dict]] = []
        inspect = harness.Harness.inspect

        def seen(h, name, prompt, schema, **kw):
            self.schemas.append((name, schema))
            return inspect(h, name, prompt, schema, **kw)
        patcher = patch.object(harness.Harness, "inspect", seen)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        harness.ENGINE = self._engine
        self._tmp.cleanup()

    def planner(self, **kw) -> vfx.VfxPlanner:
        bundle = {"idea": self.idea, "outline": {}, "claims": [], "assets": [], "premise": ""}
        kw.setdefault("revisions", 2)
        return vfx.VfxPlanner(bundle, Dossier(self.root, self.idea, at=self.root),
                              vfx.Workspace(self.root), model="sonnet", sections=[SECTION],
                              author_bar=7.0, verbose=False, jobs=1, **kw)

    def author(self, planner) -> dict:
        group = {"section": "Open", "seconds": 15, "effects": EFFECTS, "cuts": []}
        with patch.object(vfx.render, "engine_validate", lambda data, **kw: []), \
                patch.object(vfx.render, "engine_render_still",
                             lambda xml, at, **kw: (varied_png(), "rendered")), \
                patch.object(vfx, "encode_findings", lambda *a, **k: []):
            return planner.author({"sections": [SECTION]}, [group], STYLE,
                                  {"ink": "#FFFFFF"})[0]

    def authors(self):
        return [c for c in FakeEngine.stream_calls if "Write" in c["tools"]]

    def reviews(self):
        return [c for c in FakeEngine.stream_calls if "Write" not in c["tools"]]


class TheBrief(Case):
    def test_the_second_review_sees_the_previous_rounds_score_findings_and_frames(self):
        FakeEngine.reviews = [review(5, findings=[FINDING]), review(8, "better", "resolved")]
        doc = self.author(self.planner())
        first, second = self.reviews()
        self.assertNotIn(vfx.PREVIOUS_ROUND, first["prompt"])
        self.assertIn(vfx.PREVIOUS_ROUND, second["prompt"])
        self.assertIn("the hero is a sliver", second["prompt"])
        self.assertIn('"score": 5', second["prompt"])
        # the kept frames of round 0, where the reviewer can read them
        kept = re.findall(r"rounds/01-open/r00/frames/[\w.-]+\.png", second["prompt"])
        self.assertTrue(kept, second["prompt"])
        self.assertTrue(all((self.root / "vfx" / "v01" / k).is_file() for k in kept))
        self.assertEqual(doc["score"], 8)

    def test_only_a_revisions_review_must_carry_the_verdict(self):
        FakeEngine.reviews = [review(5), review(8, "better", "resolved")]
        self.author(self.planner())
        by_name = dict(self.schemas)
        self.assertNotIn("previous", by_name["review-v01-01-r0"]["required"])
        self.assertIn("previous", by_name["review-v01-01-r1"]["required"])
        self.assertIn("previous", vfx.REVIEW_SCHEMA["properties"])
        self.assertNotIn("previous", vfx.REVIEW_SCHEMA["required"])
        verdict = vfx.REVIEW_SCHEMA["properties"]["previous"]["properties"]["verdict"]
        self.assertEqual(verdict["enum"], ["better", "same", "worse"])

    def test_a_revisions_review_without_a_verdict_is_no_review(self):
        # invariant 3: the answer is validated; a reviewer that skipped the
        # delta is asked again by the harness and, failing that, scores nothing
        class Silent(FakeEngine):
            verdict_for = staticmethod(lambda review, prompt: review)
        harness.ENGINE = Silent
        FakeEngine.reviews = [review(5), review(8)]
        doc = self.author(self.planner(revisions=1))
        self.assertEqual([r["score"] for r in doc["rounds"]], [5, None])
        self.assertEqual(doc["best_round"], 0)


class Promotion(Case):
    def test_a_revision_is_kept_only_when_judged_better(self):
        FakeEngine.docs = [WORLD_DOC, WORLD_DOC.replace("#AAAAAA", "#BBBBBB")]
        # a higher score the reviewer calls the same look does not win
        FakeEngine.reviews = [review(5, findings=[FINDING]), review(6, "same")]
        doc = self.author(self.planner())
        self.assertEqual([r["score"] for r in doc["rounds"]], [5, 6])
        self.assertEqual(doc["best_round"], 0)
        self.assertEqual(doc["score"], 5)
        self.assertIn("#AAAAAA", (self.root / doc["file"]).read_text())

    def test_a_revision_judged_better_wins_whatever_the_score_says(self):
        FakeEngine.docs = [WORLD_DOC, WORLD_DOC.replace("#AAAAAA", "#BBBBBB")]
        FakeEngine.reviews = [review(6.5, findings=[FINDING]), review(6, "better", "resolved")]
        doc = self.author(self.planner(revisions=1))
        self.assertEqual(doc["best_round"], 1)
        self.assertEqual(doc["score"], 6)
        self.assertIn("#BBBBBB", (self.root / doc["file"]).read_text())

    def test_the_chain_runs_through_every_better_round(self):
        FakeEngine.reviews = [review(5, findings=[FINDING]), review(5.5, "better"),
                              review(6, "better", "resolved")]
        doc = self.author(self.planner(revisions=2))
        self.assertEqual(len(self.authors()), 3)
        self.assertEqual(doc["best_round"], 2)
        self.assertIsNone(doc["budget"]["stopped"])

    def test_best_round_follows_the_verdicts_and_falls_back_to_the_score(self):
        rounds = [{"round": 0, "score": 5, "errors": 0, "review": review(5)},
                  {"round": 1, "score": 6, "errors": 0, "review": review(6, "same")},
                  {"round": 2, "score": 4, "errors": 0, "review": review(4, "better")},
                  {"round": 3, "score": 9, "errors": 1, "review": None},
                  {"round": 4, "score": 9, "errors": 0, "review": review(9), "timed_out": True}]
        self.assertEqual(vfx.best_round(rounds)["round"], 2)
        # a record from before the verdict existed is judged on its scores
        old = [{"round": 0, "score": 5, "errors": 0, "review": review(5)},
               {"round": 1, "score": 6, "errors": 0, "review": review(6)},
               {"round": 2, "score": 6, "errors": 0, "review": review(6)}]
        self.assertEqual(vfx.best_round(old)["round"], 1)
        self.assertIsNone(vfx.best_round([{"round": 0, "score": None, "errors": 2}]))


class TheStop(Case):
    def test_a_revision_judged_the_same_ends_the_loop(self):
        FakeEngine.reviews = [review(5, findings=[FINDING]), review(5, "same"), review(9, "better")]
        doc = self.author(self.planner(revisions=2))
        self.assertEqual(len(self.authors()), 2)              # revision 2 is not paid for
        self.assertEqual(len(doc["rounds"]), 2)
        self.assertEqual(doc["budget"]["stopped"], "same")
        self.assertEqual(doc["stopped"], "same")
        self.assertEqual(doc["best_round"], 0)
        self.assertEqual(len(FakeEngine.reviews), 1)          # the third review went unread

    def test_a_revision_judged_the_same_but_over_the_bar_still_stops_as_same(self):
        # the score contradicts the verdict, the case the card calls common:
        # done is judged on the round the section stands on, not the last one,
        # so the stop is recorded and the record agrees with itself
        FakeEngine.docs = [WORLD_DOC, WORLD_DOC.replace("#AAAAAA", "#BBBBBB")]
        FakeEngine.reviews = [review(6, findings=[FINDING]), review(7.5, "same")]
        doc = self.author(self.planner(revisions=2))
        self.assertEqual([r["score"] for r in doc["rounds"]], [6, 7.5])
        self.assertEqual(len(self.authors()), 2)
        self.assertEqual((doc["best_round"], doc["score"]), (0, 6))
        self.assertEqual(doc["stopped"], "same")
        self.assertEqual(doc["budget"]["stopped"], "same")
        self.assertIn("#AAAAAA", (self.root / doc["file"]).read_text())

    def test_a_revision_judged_worse_ends_the_loop_on_the_incumbent(self):
        FakeEngine.docs = [WORLD_DOC, WORLD_DOC.replace("#AAAAAA", "#BBBBBB")]
        FakeEngine.reviews = [review(5, findings=[FINDING]), review(4, "worse", "regressed")]
        doc = self.author(self.planner(revisions=2))
        self.assertEqual(len(self.authors()), 2)
        self.assertEqual(doc["budget"]["stopped"], "worse")
        self.assertEqual(doc["best_round"], 0)
        self.assertIn("#AAAAAA", (self.root / doc["file"]).read_text())

    def test_a_round_the_gate_failed_is_still_revised(self):
        # a verdict is only given to a reviewed round; an error round goes back as before
        FakeEngine.reviews = [review(5, findings=[FINDING]), review(8, "better", "resolved")]
        calls = {"n": 0}

        def still(xml, at, **kw):
            calls["n"] += 1
            return (varied_png(), "rendered")
        planner = self.planner(revisions=3)
        group = {"section": "Open", "seconds": 15, "effects": EFFECTS, "cuts": []}
        validate = planner.ws.validate

        def flaky(name, **kw):
            symbols, findings = validate(name, **kw)
            if len(self.authors()) == 2:          # the first revision's build is broken
                return symbols, findings + ["error: the camera frames nothing"]
            return symbols, findings
        with patch.object(vfx.render, "engine_validate", lambda data, **kw: []), \
                patch.object(vfx.render, "engine_render_still", still), \
                patch.object(vfx, "encode_findings", lambda *a, **k: []), \
                patch.object(planner.ws, "validate", flaky):
            doc = planner.author({"sections": [SECTION]}, [group], STYLE, {"ink": "#FFFFFF"})[0]
        self.assertEqual([r["score"] for r in doc["rounds"]], [5, None, 8])
        self.assertEqual(doc["best_round"], 2)
        # the reviewer of round 2 was shown round 0, the round the section stood on
        self.assertIn("rounds/01-open/r00/frames/", self.reviews()[1]["prompt"])


class TheListing(Case):
    def test_the_listing_leads_with_what_persists(self):
        rev = review(5, "same", findings=[
            {"category": "visual_quality", "problem": "the paper is opaque",
             "fix": "set transmission to 0.35", "severity": "major"}])
        rev["previous"]["findings"] = [
            {"problem": "the hero is a sliver", "status": "persists"},
            {"problem": "the floor was black", "status": "resolved"},
            {"problem": "the key light flickers", "status": "regressed"}]
        listing = vfx.VfxPlanner._review_listing(rev)
        self.assertIn("same", listing)
        lines = [ln for ln in listing.splitlines() if ln.startswith("- ")]
        self.assertIn("persists", lines[0])
        self.assertIn("the hero is a sliver", lines[0])
        self.assertIn("regressed", lines[1])
        self.assertIn("the paper is opaque", lines[2])
        self.assertNotIn("the floor was black", listing)      # resolved: not a requirement

    def test_the_author_of_the_second_revision_gets_the_delta(self):
        FakeEngine.reviews = [review(5, findings=[FINDING]), review(5.5, "better"),
                              review(8, "better", "resolved")]
        self.author(self.planner(revisions=2))
        prompt = self.authors()[2]["prompt"]
        self.assertIn("persists", prompt)
        self.assertIn("better", prompt)


if __name__ == "__main__":
    unittest.main()
