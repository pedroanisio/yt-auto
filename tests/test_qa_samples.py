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
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from claude_engine import ClaudeError
from ytstudio import harness, improve, qa, spend
from ytstudio.core import write_json
from tests import test_qa_listening as listening, test_qa_motion as motion
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
        motion.steady_cutting(self)              # AUTO-141: not what this tests
        self.tmp = Path(tempfile.mkdtemp(prefix="qa-samples-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / "work"
        self.root = self.work / "one-take"
        self.root.mkdir(parents=True)
        (self.root / "scene.xml").write_text("<scene/>")
        # Current listening reviews (AUTO-105), so the audio categories are
        # scored like the rest; build.json is written after the files they
        # heard, as `yt build` does. `unheard` removes them.
        (self.root / "sound.json").write_text(json.dumps(listening._sound()))
        (self.root / "voice.json").write_text(json.dumps(listening._voice()))
        (self.root / "build.json").write_text(json.dumps(motion._build(audio_tracks=2)))
        later = (self.root / "build.json").stat().st_mtime + 60
        os.utime(self.root / "build.json", (later, later))
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
        #: An exception here makes the merge decision fail.
        self.merge_error = merge_error = []

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
                    if merge_error:
                        raise merge_error[0]
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

    def card(self, name="pass-01"):
        return json.loads((self.root / "qa" / name / "scorecard.json").read_text())

    def unheard(self):
        """No listening review heard the film: every AUDIO category unheard."""
        for name in ("sound.json", "voice.json"):
            (self.root / name).unlink()

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

    def test_an_unheard_audio_category_stays_null_across_samples(self):
        # AUTO-105 with AUTO-107: with no current listening review the
        # reviewers enter 0 for the audio categories (as the prompt asks)
        # and average them in; none of that becomes a median. Each AUDIO
        # category is null in every sample's recorded scores and in the
        # result, with no spread, and the overall is the mean of the scored
        # medians, the median of the filed overalls kept as overall_filed.
        self.unheard()
        zeros = dict.fromkeys(qa.AUDIO, 0)
        self.answers[:] = [sample(8.0, cinematography=6.5, overall=5.5, **zeros),
                           sample(8.0, cinematography=7.5, overall=5.9, **zeros),
                           sample(8.0, cinematography=7.0, overall=5.7, **zeros)]
        self.run_qa()
        card = self.card()
        for key in qa.AUDIO:
            self.assertIsNone(card["scores"][key])
            self.assertNotIn(key, card["spread"])
            for row in card["scoring"]["samples"]:
                self.assertIsNone(row["scores"][key], row["name"])
        self.assertEqual(sorted(card["audio_not_evidenced"]), sorted(qa.AUDIO))
        self.assertEqual(card["scores"]["cinematography"], 7.0)
        self.assertEqual(card["spread"]["cinematography"], 1.0)
        scored = [card["scores"][k] for k in qa.CATEGORIES if k not in qa.AUDIO]
        self.assertEqual(card["overall"], round(sum(scored) / len(scored), 2))
        # eight scored categories since AUTO-146 added editorial_identity
        self.assertEqual(card["overall"], 7.88)
        self.assertEqual(card["overall_filed"], 5.7)
        # the overall's spread is that of each sample's mean over the scored
        # categories (7.81 .. 7.94), not of the filed overalls (5.5 .. 5.9)
        self.assertAlmostEqual(card["spread"]["overall"], 0.13)
        self.assertNotEqual(card["verdict"], qa.READY)
        self.assertTrue(any("voiceover is not evidenced" in r for r in card["bar"]),
                        card["bar"])
        record = json.loads((self.root / "qa.json").read_text())
        self.assertIsNone(record["scores"]["voiceover"])
        self.assertEqual(record["overall"], 7.88)

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

    def test_a_ready_pass_counts_the_film_in_the_channel_record(self):
        # AUTO-137: work/channel.json is refreshed when qa.json is written,
        # so the stored row says counted as soon as the film is READY.
        self.answers[:] = [sample(9.4), sample(7.0), sample(9.3)]
        self.assertEqual(self.run_qa(), 0)
        rows = json.loads((self.work / "channel.json").read_text())["films"]
        self.assertEqual([(r["slug"], r["counted"]) for r in rows], [("one-take", True)])
        self.answers[:] = [sample(7.0), sample(7.0), sample(7.0)]
        self.run_qa(fresh=True)
        rows = json.loads((self.work / "channel.json").read_text())["films"]
        self.assertEqual([(r["slug"], r["counted"]) for r in rows], [("one-take", False)])

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

    def test_a_blocking_claim_one_sample_makes_blocks_nothing(self):
        # The reviewer lists what it calls blocking beside its findings; one
        # sample's claim is one reading, so it is low-confidence like the
        # finding it names, and the median scores decide the verdict.
        flat = finding("the hook frame is flat", severity="blocking")
        self.answers[:] = [sample(9.5, findings=[flat],
                                  blocking=["the hook frame is flat at 2.0s"]),
                           sample(9.5), sample(9.5)]
        code = self.run_qa()
        card = self.card()
        self.assertEqual(card["blocking"], [])
        self.assertEqual(card["verdict"], qa.READY, card["bar"])
        self.assertEqual(code, 0)
        low = [f["problem"] for f in card["low_confidence"]]
        self.assertIn("the hook frame is flat", low)
        self.assertIn("the hook frame is flat at 2.0s", low)

    def test_a_blocking_claim_two_samples_make_blocks_ready(self):
        self.answers[:] = [sample(9.5, blocking=["the hook frame is flat"]),
                           sample(9.5, blocking=["the opening frame reads flat"]),
                           sample(9.5)]
        self.groups[:] = [["s1.B1", "s2.B1"]]
        code = self.run_qa()
        self.assertEqual(len(self.seen["merges"]), 1)
        self.assertIn("s2.B1", self.seen["merges"][0])
        card = self.card()
        self.assertEqual(card["blocking"], ["the hook frame is flat"])
        self.assertEqual(card["findings"], [])
        self.assertEqual(card["verdict"], qa.NOT_READY)
        self.assertEqual(code, 1)

    def test_a_merge_that_fails_keeps_every_finding_unmerged(self):
        a = finding("the hook frame is flat", severity="blocking")
        b = finding("the opening still has no depth")
        self.answers[:] = [sample(8.0, findings=[a], blocking=["the hook"]),
                           sample(8.0, findings=[b]), sample(8.0)]
        self.merge_error[:] = [ClaudeError("the session ended")]
        self.run_qa()
        self.assertEqual(len(self.seen["merges"]), 1)
        card = self.card()
        self.assertEqual([f["problem"] for f in card["findings"]],
                         [a["problem"], b["problem"]])
        self.assertEqual([f["samples"] for f in card["findings"]], [[1], [2]])
        self.assertEqual(card["low_confidence"], [])
        # unmerged, nothing was out-voted: a claim and a blocking finding still block
        self.assertEqual(card["blocking"], ["the hook", "the hook frame is flat at 2.0s"])
        self.assertIn("session ended", card["scoring"]["merge"]["failed"])
        self.assertTrue(any("could not be merged" in n for n in card["not_evidenced"]))
        text = (self.root / "qa" / "pass-01" / "findings.md").read_text()
        self.assertIn("could not be merged", text)
        record = json.loads((self.root / "qa.json").read_text())
        self.assertEqual(record["verdict"], qa.NOT_READY)
        self.assertEqual(record["overall"], 8.0)

    def test_a_failed_ask_again_keeps_the_first_answer(self):
        gappy = dict(sample(8.0), story=dict(STORY, sections=[]))
        self.answers[:] = [gappy, ClaudeError("the session ended"),
                           sample(8.0), sample(8.0)]
        self.run_qa()
        card = self.card()
        self.assertEqual(card["scoring"]["valid"], 3)
        first = card["scoring"]["samples"][0]
        self.assertTrue(first["valid"])
        self.assertTrue(first["again"])
        self.assertIn("session ended", first["again_failed"])
        self.assertTrue(first["gaps"])
        self.assertNotEqual(card["verdict"], qa.NOT_EVIDENCED)

    def test_a_category_fixed_by_rule_has_no_spread(self):
        (self.root / "build.json").write_text(json.dumps(motion._build(audio_tracks=0)))
        self.answers[:] = [sample(8.0, sound_design=6.0, music_score=5.0),
                           sample(8.0, sound_design=8.0, music_score=7.0),
                           sample(8.0, sound_design=7.0, music_score=6.0)]
        self.run_qa()
        card = self.card()
        self.assertEqual(card["scores"]["sound_design"], qa.SILENT_SCORE)
        self.assertEqual(card["spread"]["sound_design"], 0.0)
        self.assertEqual(card["spread"]["music_score"], 0.0)
        # what the samples decided keeps its spread
        self.assertEqual(card["spread"]["cinematography"], 0.0)
        self.assertIn("overall", card["spread"])

    def test_a_capped_category_has_no_spread(self):
        spread = {"visual_quality": 1.0, "animation_vfx": 0.5, "voiceover": 1.0,
                  "sound_design": 0.5, "overall": 0.4}
        out = qa.rule_spread(spread, silent=True, capped=["visual_quality"])
        self.assertEqual(out, {"visual_quality": 0.0, "animation_vfx": 0.5,
                               "voiceover": 1.0, "sound_design": 0.0,
                               "music_score": 0.0, "overall": 0.4})
        self.assertEqual(spread["visual_quality"], 1.0)        # not changed in place

    def test_a_not_evidenced_pass_is_never_the_previous_one(self):
        self.answers[:] = [sample(8.0), sample(8.0), sample(8.0),
                           ClaudeError("down"), ClaudeError("down"), ClaudeError("down"),
                           sample(8.0, cinematography=6.0), sample(8.0, cinematography=6.0),
                           sample(8.0, cinematography=6.0)]
        for _ in range(3):
            self.run_qa()
        self.assertEqual(self.card("pass-02")["verdict"], qa.NOT_EVIDENCED)
        third = self.card("pass-03")
        self.assertEqual(third["comparison"]["against"], "pass-01")
        self.assertIn("cinematography", third["comparison"]["regressions"])
        # compared in code, after the scoring: the prompt names no pass (AUTO-106)
        self.assertNotIn("pass-01", self.seen["prompts"][-1])
        record = json.loads((self.root / "qa.json").read_text())
        self.assertIn("cinematography", record["regressions"])

    #: What run() asked before the score_evidence seam was pulled out of it
    #: (AUTO-156): every inspection's prompt and system, every merge prompt
    #: and the decisions' names, with the temporary paths written as <root>.
    #: Regenerate it only for a change that means to change the prompt:
    #: YT_QA_GOLDEN=write runs this test and writes the file.
    GOLDEN = Path(__file__).resolve().parent / "fixtures" / "qa-seam" / "prompts.json"

    def _asked(self) -> dict:
        """What the engine was asked since setUp, the paths made stable."""
        def stable(text: str) -> str:
            return text.replace(str(self.tmp), "<root>")
        return {"prompts": [stable(p) for p in self.seen["prompts"]],
                "systems": [stable(s) for s in self.seen.get("systems", [])],
                "merges": [stable(p) for p in self.seen["merges"]]}

    def test_run_prompts_are_unchanged_by_the_score_evidence_seam(self):
        # AUTO-156: run()'s review is pulled out as score_evidence so the
        # calibration set can be judged by the same code. The pin: the
        # prompts, the system and the decisions' names are what they were
        # before the extraction, byte for byte, and score_evidence called
        # directly on the same inputs asks exactly the same.
        systems = self.seen["systems"] = []
        engine = harness.ENGINE

        class Recording(engine):
            def stream(self_, prompt, system=None):
                systems.append(system or "")
                yield from super().stream(prompt, system)
        harness.ENGINE = Recording
        saved = qa.GEOMETRY
        self.addCleanup(setattr, qa, "GEOMETRY", saved)
        qa.GEOMETRY = lambda args, project, scene, shots: {"status": "skipped",
                                                           "why": "a fake geometry check"}
        f1, f2 = finding("the tail is static", t=2.0), finding("the tail never moves", t=2.1)
        self.answers[:] = [sample(8.0, findings=[f1]), sample(8.0, findings=[f2]),
                           sample(8.0)]
        self.groups[:] = [["s1.F1", "s2.F1"]]
        self.run_qa()
        card = self.card()
        got = self._asked() | {"decisions": [d["name"] for d in card["engine"]["decisions"]]}
        if os.environ.get("YT_QA_GOLDEN") == "write":
            self.GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            self.GOLDEN.write_text(json.dumps(got, indent=1, ensure_ascii=False) + "\n",
                                   encoding="utf-8")
        golden = json.loads(self.GOLDEN.read_text(encoding="utf-8"))
        self.assertEqual(got["decisions"], golden["decisions"])
        self.assertEqual(got["merges"], golden["merges"])
        self.assertEqual(got["systems"], golden["systems"])
        self.assertEqual(len(got["prompts"]), len(golden["prompts"]))
        for mine, theirs in zip(got["prompts"], golden["prompts"]):
            self.assertEqual(mine, theirs)

        # score_evidence directly, on a copy of the pass's evidence under a
        # project of its own (so nothing is served from run()'s cache): the
        # same prompts, the same system, the same names.
        from ytstudio import reach
        other = self.tmp / "other"
        before = self.root / "qa" / "pass-01"
        here = other / "qa" / "pass-01"
        shutil.copytree(before / "frames", here / "frames")
        samples = json.loads((before / "samples.json").read_text())
        build = json.loads((self.root / "build.json").read_text())
        script = json.loads((self.root / "script.json").read_text())
        voice = json.loads((self.root / "voice.json").read_text())
        sound = json.loads((self.root / "sound.json").read_text())
        for key in ("prompts", "merges"):
            self.seen[key].clear()
        systems.clear()
        self.answers[:] = [sample(8.0, findings=[f1]), sample(8.0, findings=[f2]),
                           sample(8.0)]
        reviewer = harness.Harness(other, "qa", model="sonnet", role=qa.ROLE,
                                   verbose=False, timeout=1800.0)
        measurements = qa.stage_measurements(
            build, voice, sound=sound, gate=None,
            geometry={"status": "skipped", "why": "a fake geometry check"}, styles={},
            reach=None, rhythm=card["rhythm"])           # AUTO-141: run()'s block
        self.assertIsNone(reach.summary(None))
        direct = qa.score_evidence(
            reviewer, here, title="one take", build=build, script=script, voice=voice,
            samples=samples, frames_dir=here / "frames", listening=card["listening"],
            mechanical=card["mechanical"], diagnostics={}, measurements=measurements,
            blockers=[])
        again = self._asked() | {"decisions": [d["name"] for d in
                                               reviewer.report()["decisions"]]}
        self.assertEqual(again["decisions"], golden["decisions"])
        self.assertEqual(again["merges"], golden["merges"])
        self.assertEqual(again["systems"], golden["systems"])
        self.assertEqual(again["prompts"], golden["prompts"])
        self.assertEqual(direct["scores"]["cinematography"], 8.0)
        self.assertNotIn("gaps", direct)

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


class MergingClaims(unittest.TestCase):
    def test_a_claim_grouped_with_another_samples_finding_makes_it_blocking(self):
        rows = [{"k": 1, "findings": [], "blocking": ["the hook is flat"]},
                {"k": 2, "findings": [finding("flat hook", severity="blocking")]}]
        kept, low = qa.merge_findings(rows, [["s1.B1", "s2.F1"]])
        self.assertEqual(low, [])
        self.assertEqual([(f["problem"], f["severity"]) for f in kept],
                         [("flat hook", "blocking")])
        self.assertFalse(kept[0].get("claim"))


class Keep(unittest.TestCase):
    """improve's keep-or-restore reads a change inside the spread as none."""

    def test_a_gain_inside_the_spread_is_not_kept(self):
        best = dict(improve_card(7.0), spread={"overall": 0.2})
        new = dict(improve_card(7.3), spread={"overall": 0.5})
        verdict = improve.judge(best, new)
        self.assertFalse(verdict["keep"])
        self.assertTrue(any("spread" in r for r in verdict["reasons"]), verdict["reasons"])

    def test_a_gain_beyond_the_spread_is_kept(self):
        best = dict(improve_card(7.0), spread={"overall": 0.2})
        new = dict(improve_card(7.6), spread={"overall": 0.5})
        self.assertTrue(improve.judge(best, new)["keep"])

    def test_a_drop_inside_the_spread_is_no_regression(self):
        best = improve_card(7.0)
        new = dict(improve_card(8.0, scores={"voiceover": 6.2}),
                   spread={"overall": 0.4, "voiceover": 1.0})
        self.assertTrue(improve.judge(best, new)["keep"])
        worse = dict(new, scores=dict(new["scores"], voiceover=5.9))
        verdict = improve.judge(best, worse)
        self.assertFalse(verdict["keep"])
        self.assertTrue(any("voiceover" in r for r in verdict["reasons"]))

    def test_cards_from_before_the_samples_judge_as_before(self):
        self.assertTrue(improve.judge(improve_card(7.0), improve_card(7.1))["keep"])

    def test_an_unscored_category_has_no_delta_and_the_spread_still_reads(self):
        # AUTO-105 with AUTO-107: a pass that only lost its low audio scores
        # is no gain over the categories both scored, and an unscored
        # category is neither a drop nor a gain beside the spreads.
        best = dict(improve_card(7.0, scores={"voiceover": 4.0}), overall=6.7,
                    spread={"overall": 0.2, "voiceover": 0.5})
        lost = dict(improve_card(7.0, scores={"voiceover": None}),
                    spread={"overall": 0.2})          # 7.0 over the nine it scored
        verdict = improve.judge(best, lost)
        self.assertFalse(verdict["keep"], verdict)
        self.assertIsNone(verdict["deltas"]["voiceover"])
        self.assertTrue(any("both passes scored" in r for r in verdict["reasons"]),
                        verdict["reasons"])
        better = dict(improve_card(7.5, scores={"voiceover": None}),
                      spread={"overall": 0.2})          # +0.5 there, past the spread
        self.assertTrue(improve.judge(best, better)["keep"])

    def test_a_not_evidenced_pass_is_never_kept(self):
        new = dict(improve_card(9.5), verdict=qa.NOT_EVIDENCED, scores={}, overall=None)
        verdict = improve.judge(improve_card(7.0), new)
        self.assertFalse(verdict["keep"])


class KeepInTheLoop(ImproveCase):
    def test_improve_does_not_keep_a_pass_whose_gain_is_inside_the_spread(self):
        self.start_pass(dict(improve_card(7.0, mechanical=[mech()]), spread={"overall": 0.3}))
        runner = FakeRunner(self.root, [dict(improve_card(7.4), spread={"overall": 0.6})])
        p = self.improver(runner, max_passes=1).run()["passes"][0]
        self.assertEqual(p["decision"], "revert")
        self.assertTrue(any("spread" in r for r in p["reasons"]), p["reasons"])

    def test_a_not_evidenced_latest_pass_is_scored_again_first(self):
        self.start_pass(dict(improve_card(7.0, mechanical=[mech()]),
                             verdict=qa.NOT_EVIDENCED, scores={}, overall=None))
        runner = FakeRunner(self.root, [improve_card(7.0, mechanical=[mech()]),
                                        improve_card(8.0)])
        self.improver(runner, max_passes=1).run()
        self.assertEqual(runner.stages()[0], "qa")


class NotEvidencedInTheLoop(ImproveCase):
    def not_evidenced(self, cost=0.7):
        return dict(improve_card(7.0, mechanical=[mech()], cost=cost),
                    verdict=qa.NOT_EVIDENCED, scores={}, overall=None)

    def test_a_not_evidenced_pass_mid_loop_is_unjudged_and_scored_again_next_run(self):
        self.start_pass(improve_card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [self.not_evidenced(0.7)])
        record = self.improver(runner, max_passes=1).run()
        p = record["passes"][0]
        self.assertEqual(p["decision"], "unjudged")
        self.assertEqual(record["stop"]["reason"], "qa-failed")
        self.assertTrue((self.root / "improve" / "pass-01" / "snapshot").is_dir())
        # the samples that were asked are paid for, and the pass row says so
        self.assertGreaterEqual(p["cost_usd"], 0.7)
        runner = FakeRunner(self.root, [improve_card(6.0)])
        record = self.improver(runner, max_passes=1).run()
        self.assertEqual(runner.stages(), ["qa"])          # only the judging ran
        p = record["passes"][0]
        self.assertTrue(p["resumed"])
        self.assertEqual(p["decision"], "keep")
        self.assertEqual(p["qa_pass"], "pass-03")

    def test_from_pass_refuses_a_not_evidenced_pass(self):
        self.start_pass(improve_card(5.0, mechanical=[mech()]), "pass-01")
        self.start_pass(self.not_evidenced(), "pass-02")
        out = []
        runner = FakeRunner(self.root, [])
        record = self.improver(runner, max_passes=1, from_pass="pass-02",
                               say=out.append).run()
        self.assertEqual(runner.calls, [])
        self.assertEqual(record["stop"]["reason"], "no-evidence")
        text = "\n".join(out)
        self.assertIn("not evidenced", text)
        self.assertIn("yt qa", text)

    def test_a_dry_run_says_its_pass_is_not_evidenced(self):
        self.start_pass(self.not_evidenced())
        out = []
        self.improver(FakeRunner(self.root, []), dry_run=True, say=out.append).run()
        text = "\n".join(out)
        self.assertIn("pass-01 is not evidenced", text)
        self.assertIn("yt qa", text)
        self.assertNotIn("older than the video", text)


class Estimate(ImproveCase):
    def qa_row(self):
        rows = improve.stage_estimates(self.root, ["sound"])
        return next(r for r in rows if r["stage"] == "qa")

    def test_the_assumed_pass_prices_every_sample(self):
        row = self.qa_row()
        self.assertEqual(row["basis"], "assumed")
        self.assertAlmostEqual(row["usd"], spend.QA_SAMPLE_USD * qa.QA_SAMPLES)
        self.assertEqual(spend.DEFAULT_USD["qa"], round(spend.QA_SAMPLE_USD * qa.QA_SAMPLES, 2))

    def test_a_pass_scored_once_is_priced_at_the_samples(self):
        self.start_pass(dict(improve_card(7.0), usage=spend.record(0.6, "m", 1)))
        row = self.qa_row()
        self.assertEqual(row["basis"], "measured")
        # the samples, and once the side-by-side comparison with that scored
        # pass, which the next `yt qa` makes (AUTO-108)
        self.assertAlmostEqual(row["usd"],
                               round(0.6 * qa.QA_SAMPLES + spend.COMPARE_PASS_USD, 2))
        self.assertEqual(improve._qa_cost(self.root), 0.6)     # what it cost stays read

    def test_a_pass_already_sampled_is_priced_as_measured(self):
        self.start_pass(dict(improve_card(7.0), usage=spend.record(1.8, "m", 3),
                             scoring={"asked": 3, "valid": 3}))
        self.assertAlmostEqual(self.qa_row()["usd"], round(1.8 + spend.COMPARE_PASS_USD, 2))

    def test_the_dry_run_estimate_rises_by_the_samples(self):
        write_json(self.root / "sound.json", {"cues": [], "usage": {"cost_usd": 0.5}})
        self.start_pass(dict(improve_card(5.0, mechanical=[mech(owner="sound", kind="flat")]),
                             usage=spend.record(0.6, "m", 1)))
        out = []
        plan = self.improver(FakeRunner(self.root, []), dry_run=True,
                             say=out.append).run()["plan"]
        by = {r["stage"]: r for r in plan["estimates"]}
        # three samples, and the side-by-side comparison with the kept pass
        # that QA is told to make (AUTO-108), priced once
        self.assertAlmostEqual(by["qa"]["usd"], round(1.8 + spend.COMPARE_PASS_USD, 2))


class Docs(unittest.TestCase):
    """The manual, the README and the brief say what the samples do."""

    ROOT = Path(__file__).resolve().parent.parent

    def flat(self, rel):
        return " ".join((self.ROOT / rel).read_text(encoding="utf-8").split())

    def test_the_manual_names_the_samples_the_spread_and_not_evidenced(self):
        skill = self.flat("ytstudio/SKILL.md")
        for needle in ("`qa.QA_SAMPLES`", "`<pass>-scorecard-s1`", "`spread`",
                       "`low_confidence`", "`<pass>-scorecard-merge`", "`improve.judge`",
                       "`qa.pass_usd`", "`spend.QA_SAMPLE_USD`"):
            self.assertIn(needle, skill)
        self.assertEqual(qa.QA_SAMPLES, 3)       # "(3)" in the manual and the README

    def test_the_readme_and_the_brief_say_it(self):
        readme = self.flat("README.md")
        for needle in ("`qa.QA_SAMPLES` (3)", "pass-NN-scorecard-s1", "`low_confidence`",
                       "`not_evidenced`: no scores, never READY", "samples' spread"):
            self.assertIn(needle, readme)
        self.assertIn("`qa.QA_SAMPLES`", self.flat("docs/production.md"))
        self.assertIn("QA_SAMPLES", qa.__doc__)


if __name__ == "__main__":
    unittest.main()
