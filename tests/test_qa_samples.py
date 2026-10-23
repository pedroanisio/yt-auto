"""qa.py: a pass is scored from the median of QA_SAMPLES readings (AUTO-107).

One scoring reading a pass put the run-to-run noise (about ±0.5 a category)
at the size of the steps read as progress. The scorecard is now asked
QA_SAMPLES times on the same evidence, each its own decision with the
categories in its own order; a category's score is the median of the valid
samples and its spread (max - min) is recorded; a finding stands when two
samples report it and is low-confidence when one does; fewer than two valid
samples is not_evidenced. improve reads a change inside the spread as no
change, and the estimate prices every sample. No model, no network: a fake
engine answers each inspection in turn, as in test_qa.StoryStage.
"""

import argparse
import contextlib
import io
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from claude_engine import ClaudeError
from ytstudio import harness, improve, qa, spend
from ytstudio.core import write_json
from tests import test_qa_motion as motion
from tests.test_improve import FakeRunner, ImproveCase, card as improve_card, mech

STORY = {"premise_restated": "a comet's tail is blown by a wind",
         "sections": [{"section": 1, "viewer_has_at_end": "the tail points away"}],
         "satisfaction": "4 satisfied", "satisfaction_why": "the payoff lands"}


def sample(score=7.0, *, findings=(), blocking=(), overall=None, **scores):
    """One reviewer's answer: every category at `score` but those named."""
    s = {k: score for k in qa.CATEGORIES}
    s.update(scores)
    return {"scores": s, "overall": score if overall is None else overall,
            "verdict": qa.NOT_READY, "findings": list(findings),
            "blocking": list(blocking), "not_evidenced": [], "rationale": "read it",
            "story": STORY}


def finding(problem, t=2.0, severity="minor", owner="vfx"):
    return {"timestamp": t, "frame": "", "problem": problem, "why": "it reads flat",
            "root_cause": "the rig", "owner": owner, "fix": "light it",
            "severity": severity}


class Samples(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qa-samples-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / "work"
        self.root = self.work / "one-take"
        self.root.mkdir(parents=True)
        (self.root / "scene.xml").write_text("<scene/>")
        (self.root / "build.json").write_text(json.dumps(motion._build(audio_tracks=2)))
        (self.root / "script.json").write_text(json.dumps({"sections": [
            {"heading": "One", "shots": [{"label": "1.1", "narration": "A comet."}]}]}))
        saved = harness.ENGINE, qa.RENDERER
        self.addCleanup(setattr, qa, "RENDERER", saved[1])
        self.addCleanup(setattr, harness, "ENGINE", saved[0])
        seen = self.seen = {"prompts": [], "merges": []}
        #: One answer per inspection, in order; an exception is a failed call.
        self.answers = answers = []
        #: The merge decision's groups, as the reviewer would file them.
        self.groups = groups = []

        class Reader(motion.FakeEngine):
            def stream(self_, prompt, system=None):
                from claude_engine import Event
                seen["prompts"].append(prompt)
                answer = answers.pop(0) if answers else sample()
                if isinstance(answer, Exception):
                    raise answer
                yield Event(type="assistant", text="```json\n" + json.dumps(answer) + "\n```")
                yield Event(type="result", raw={})

            def ask_json(self_, prompt, schema, system=None):
                if "groups" in schema.get("properties", {}):
                    seen["merges"].append(prompt)
                    return {"groups": [{"findings": g, "why": "same defect"} for g in groups]}
                return super().ask_json(prompt, schema, system)

        harness.ENGINE = Reader

        def render(args, scene):
            def job(j):
                out = j["out"]
                if "frames" in j:
                    a, b = (int(v) for v in j["frames"].split(".."))
                    for f in range(a, b):
                        motion._square(Path(out.replace("%04d", f"{f:04d}")), 100 + f // 2)
                else:
                    motion._square(Path(out), 100)
                return 0
            return job
        qa.RENDERER = render

    def args(self, **kw):
        base = dict(project=str(self.root), title=None, index=None, source=None,
                    stdin=None, genre="short", description="one take", work=str(self.work),
                    scene=None, frame_limit=4, quality="preview", reuse=None,
                    host=None, local=False, fresh=False, quiet=True, model="sonnet",
                    in_build=None, in_script=None, in_voice=None, in_sound=None,
                    in_styles=None, in_gate=None, house=None, no_house=True)
        base.update(kw)
        return argparse.Namespace(**base)

    def run_qa(self, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return qa.run(self.args(**kw))

    def card(self):
        return json.loads((self.root / "qa" / "pass-01" / "scorecard.json").read_text())

    def test_three_samples_give_the_median_and_the_spread(self):
        self.answers[:] = [sample(8.0, cinematography=6.5, overall=7.6),
                           sample(8.0, cinematography=7.5, overall=7.8),
                           sample(8.0, cinematography=7.0, overall=7.7)]
        self.run_qa()
        self.assertEqual(len(self.seen["prompts"]), qa.QA_SAMPLES)
        card = self.card()
        self.assertEqual(card["scores"]["cinematography"], 7.0)
        self.assertEqual(card["spread"]["cinematography"], 1.0)
        self.assertEqual(card["spread"]["editing_pacing"], 0.0)
        self.assertEqual(card["overall"], 7.7)
        self.assertAlmostEqual(card["spread"]["overall"], 0.2)
        self.assertEqual((card["scoring"]["asked"], card["scoring"]["valid"]), (3, 3))
        names = [s["name"] for s in card["scoring"]["samples"]]
        self.assertEqual(names, ["pass-01-scorecard-s1", "pass-01-scorecard-s2",
                                 "pass-01-scorecard-s3"])
        decided = [d["name"] for d in card["engine"]["decisions"]]
        for name in names:
            self.assertIn(name, decided)
        # qa.json carries the median and the spread for doctor and improve
        record = json.loads((self.root / "qa.json").read_text())
        self.assertEqual(record["scores"]["cinematography"], 7.0)
        self.assertEqual(record["spread"]["cinematography"], 1.0)
        text = (self.root / "qa" / "pass-01" / "findings.md").read_text()
        self.assertIn("Spread", text)
        self.assertIn("median of 3", text)

    def test_each_sample_sees_the_categories_in_its_own_order(self):
        self.run_qa()
        orders = []
        for prompt in self.seen["prompts"]:
            line = next(ln for ln in prompt.splitlines() if ln.startswith("Score the categories"))
            orders.append(tuple(re.findall(r"[a-z_]+(?=[,.])", line.split(":", 1)[1])))
        self.assertEqual(len(orders), 3)
        self.assertEqual(len(set(orders)), 3, orders)
        self.assertEqual(len({o[0] for o in orders}), 3, orders)
        for order in orders:
            self.assertEqual(sorted(order), sorted(qa.CATEGORIES))
        # the evidence is the same: the prompts differ in that line alone
        rest = {"\n".join(ln for ln in p.splitlines()
                          if not ln.startswith("Score the categories")).split("```json")[0]
                .split("JSON schema")[0] for p in self.seen["prompts"]}
        self.assertEqual(len(rest), 1)
        for seed, order in enumerate(orders, 1):
            schema = qa.sample_schema(seed)
            self.assertEqual(tuple(schema["properties"]["scores"]["required"]), order)
        self.assertEqual(qa.sample_schema(0), qa.SCORECARD_SCHEMA)

    def test_one_failed_sample_of_three_still_scores_from_two(self):
        self.answers[:] = [sample(8.0, cinematography=6.5),
                           ClaudeError("the session ended"),
                           sample(8.0, cinematography=7.5)]
        self.run_qa()
        card = self.card()
        self.assertEqual(card["scores"]["cinematography"], 7.0)
        self.assertEqual(card["spread"]["cinematography"], 1.0)
        self.assertEqual((card["scoring"]["asked"], card["scoring"]["valid"]), (3, 2))
        failed = card["scoring"]["samples"][1]
        self.assertFalse(failed["valid"])
        self.assertIn("session ended", failed["failed"])
        # a failed call is a failed engine row, billed, never a score
        rows = [d for d in card["engine"]["decisions"] if d["name"] == failed["name"]]
        self.assertEqual([d["kind"] for d in rows], ["failed"])
        self.assertNotEqual(card["verdict"], qa.NOT_EVIDENCED)

    def test_two_failed_samples_are_not_evidenced_never_a_pass(self):
        self.answers[:] = [sample(9.6), ClaudeError("down"), ClaudeError("down")]
        code = self.run_qa()
        self.assertEqual(code, 1)
        card = self.card()
        self.assertEqual(card["verdict"], qa.NOT_EVIDENCED)
        self.assertEqual(card["scores"], {})
        self.assertIsNone(card["overall"])
        self.assertEqual(card["scoring"]["valid"], 1)
        self.assertTrue(any("fewer than two" in r for r in card["bar"]), card["bar"])
        record = json.loads((self.root / "qa.json").read_text())
        self.assertEqual(record["verdict"], qa.NOT_EVIDENCED)
        self.assertNotEqual(record["verdict"], qa.READY)

    def test_the_verdict_reads_the_median_scores(self):
        # one sample under the bar everywhere is out-voted by the two above it
        self.answers[:] = [sample(9.4), sample(7.0), sample(9.3)]
        code = self.run_qa()
        card = self.card()
        self.assertEqual(card["scores"]["cinematography"], 9.3)
        self.assertEqual(card["verdict"], qa.READY, card["bar"])
        self.assertEqual(code, 0)

    def test_a_finding_in_one_sample_of_three_is_low_confidence(self):
        twice_a = finding("the hook frame is flat", severity="blocking")
        twice_b = finding("the opening still has no depth", severity="blocking")
        once = finding("a super overlaps the caption band", t=9.0)
        self.answers[:] = [sample(8.0, findings=[twice_a, once]),
                           sample(8.0, findings=[twice_b]),
                           sample(8.0)]
        self.groups[:] = [["s1.F1", "s2.F1"]]
        self.run_qa()
        self.assertEqual(len(self.seen["merges"]), 1)
        self.assertIn("s1.F2", self.seen["merges"][0])
        self.assertIn("the opening still has no depth", self.seen["merges"][0])
        card = self.card()
        self.assertEqual([f["problem"] for f in card["findings"]], [twice_a["problem"]])
        self.assertEqual(card["findings"][0]["samples"], [1, 2])
        self.assertEqual(card["findings"][0]["severity"], "blocking")
        self.assertEqual([f["problem"] for f in card["low_confidence"]], [once["problem"]])
        self.assertEqual(card["low_confidence"][0]["samples"], [1])
        # a single-sample finding is not routed and blocks nothing
        self.assertFalse(any("caption band" in b for b in card["blocking"]))
        text = (self.root / "qa" / "pass-01" / "findings.md").read_text()
        self.assertIn("Low confidence", text)
        self.assertIn("caption band", text)

    def test_a_blocking_call_one_sample_makes_is_minor_when_merged(self):
        self.answers[:] = [sample(8.0, findings=[finding("flat hook", severity="blocking")]),
                           sample(8.0, findings=[finding("the hook is flat")]),
                           sample(8.0)]
        self.groups[:] = [["s1.F1", "s2.F1"]]
        self.run_qa()
        card = self.card()
        self.assertEqual(card["findings"][0]["severity"], "minor")

    def test_no_merge_is_asked_when_no_two_samples_have_findings(self):
        self.answers[:] = [sample(8.0, findings=[finding("a")]), sample(8.0), sample(8.0)]
        self.run_qa()
        self.assertEqual(self.seen["merges"], [])
        card = self.card()
        self.assertEqual(card["findings"], [])
        self.assertEqual(len(card["low_confidence"]), 1)


class Merging(unittest.TestCase):
    """The merge itself, from the samples and the reviewer's groups."""

    def rows(self, *lists):
        return [{"k": k, "findings": [finding(p) for p in problems]}
                for k, problems in enumerate(lists, 1)]

    def test_unknown_and_repeated_ids_are_ignored(self):
        kept, low = qa.merge_findings(self.rows(["a"], ["b"], ["c"]),
                                      [["s1.F1", "s2.F1", "s9.F4"], ["s2.F1", "s3.F1"]])
        self.assertEqual([f["problem"] for f in kept], ["a"])
        self.assertEqual([f["problem"] for f in low], ["c"])

    def test_two_findings_of_one_sample_are_one_sample(self):
        kept, low = qa.merge_findings(self.rows(["a", "a again"], []), [["s1.F1", "s1.F2"]])
        self.assertEqual(kept, [])
        self.assertEqual(len(low), 1)


if __name__ == "__main__":
    unittest.main()
