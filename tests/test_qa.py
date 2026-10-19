"""qa.py: the review-and-improve harness over the rendered film.

The deterministic half is what these pin: where the evidence is sampled
(never at a shot's boundary, always across the runtime), how passes are
numbered, what the publication bar is, and how one pass is compared with the
one before it. The scoring itself is the model's, through the harness.
"""

import tempfile
import unittest
from pathlib import Path

from ytstudio import qa


def _build():
    shots = []
    t = 0.0
    for n, (label, seconds, asset) in enumerate([
            ("1.1", 4, "effect"), ("1.2", 6, "effect"), ("2.1", 10, "image"),
            ("2.2", 8, "effect"), ("3.1", 5, "placeholder"), ("3.2", 7, "effect")]):
        shots.append({"label": label, "at": t, "seconds": seconds, "asset": asset,
                      "section": "Open" if label[0] == "1" else
                                 "Middle" if label[0] == "2" else "Close"})
        t += seconds
    return {"scene": {"duration": t}, "shots": shots, "placeholders": ["3.1"],
            "findings": []}


class Sampling(unittest.TestCase):
    def test_samples_sit_at_shot_midpoints_across_the_runtime(self):
        samples = qa.sample(_build())
        times = [s["t"] for s in samples]
        self.assertEqual(times, sorted(times))
        mids = {round(s["at"] + s["seconds"] / 2, 3) for s in _build()["shots"]}
        self.assertTrue(all(t in mids for t in times), times)
        why = " ".join(s["why"] for s in samples)
        for word in ("hook", "section", "25%", "50%", "75%", "ending", "effect"):
            self.assertIn(word, why)

    def test_the_hook_and_the_ending_survive_a_tight_limit(self):
        samples = qa.sample(_build(), limit=3)
        labels = [s["label"] for s in samples]
        self.assertEqual(len(samples), 3)
        self.assertIn("1.1", labels)
        self.assertIn("3.2", labels)

    def test_frame_names_carry_the_timestamp(self):
        self.assertEqual(qa.frame_name(83.456), "t_01m23s456.png")
        self.assertEqual(qa.frame_name(0), "t_00m00s000.png")


class Passes(unittest.TestCase):
    def test_passes_are_numbered_in_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(qa.next_pass(root).name, "pass-01")
            (root / "qa" / "pass-01").mkdir(parents=True)
            (root / "qa" / "pass-03").mkdir(parents=True)
            self.assertEqual(qa.next_pass(root).name, "pass-04")
            self.assertEqual(qa.latest_pass(root).name, "pass-03")


class Bar(unittest.TestCase):
    def scores(self, **over):
        base = {k: 9.2 for k in qa.CATEGORIES}
        base.update(over)
        return base

    def test_ready_needs_overall_nine_and_no_category_below_eight_and_a_half(self):
        verdict, reasons = qa.verdict_of(self.scores(), 9.1, blocking=[])
        self.assertEqual(verdict, qa.READY)
        self.assertEqual(reasons, [])
        verdict, reasons = qa.verdict_of(self.scores(voiceover=8.4), 9.1, blocking=[])
        self.assertEqual(verdict, qa.NOT_READY)
        self.assertTrue(any("voiceover" in r for r in reasons))
        verdict, reasons = qa.verdict_of(self.scores(), 8.9, blocking=[])
        self.assertEqual(verdict, qa.NOT_READY)
        verdict, reasons = qa.verdict_of(self.scores(), 9.5, blocking=["a placeholder"])
        self.assertEqual(verdict, qa.NOT_READY)

    def test_mechanical_blockers_come_from_the_manifests(self):
        blockers = qa.mechanical_blockers(_build(), gate={"exit": 1, "triage": {
            "blocking": [{"what": "loudness -16 LUFS"}]}})
        joined = " ".join(blockers)
        self.assertIn("3.1", joined)
        self.assertIn("loudness", joined)
        self.assertEqual(qa.mechanical_blockers({"placeholders": [], "findings": []},
                                                gate=None), [])


class Comparison(unittest.TestCase):
    def test_regressions_are_categories_that_dropped(self):
        prev = {"scores": {k: 9.0 for k in qa.CATEGORIES}, "overall": 9.0}
        cur = {"scores": {k: 9.0 for k in qa.CATEGORIES} | {"editing_pacing": 8.3,
                                                             "voiceover": 9.4},
               "overall": 8.9}
        diff = qa.compare(prev, cur)
        self.assertEqual(diff["regressions"], ["editing_pacing"])
        self.assertAlmostEqual(diff["deltas"]["voiceover"], 0.4)
        self.assertAlmostEqual(diff["overall_delta"], -0.1)
        self.assertEqual(qa.compare(None, cur)["regressions"], [])

    def test_findings_markdown_lists_every_finding_with_its_owner(self):
        card = {"scores": {k: 9.0 for k in qa.CATEGORIES}, "overall": 9.0,
                "verdict": qa.READY, "rationale": "fine",
                "findings": [{"timestamp": 12.5, "frame": "t_00m12s500.png",
                              "problem": "caption enters the stage band",
                              "why": "collides with the figure", "root_cause": "cue layout",
                              "owner": "build", "fix": "captions_off for 2.1",
                              "severity": "blocking"}],
                "blocking": [], "not_evidenced": ["music_score"]}
        text = qa.findings_md("pass-02", card, [{"t": 12.5, "why": "50%", "label": "2.1",
                                                 "section": "Middle"}],
                              {"regressions": [], "deltas": {}, "overall_delta": 0.0},
                              reasons=[])
        self.assertIn("# pass-02", text)
        self.assertIn("00:12.500", text)
        self.assertIn("build", text)
        self.assertIn("music_score", text)


if __name__ == "__main__":
    unittest.main()
