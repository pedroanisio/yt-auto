"""qa.py: each pass is judged against the previous kept cut side by side,
and the tally is filed beside the absolute scores (AUTO-108).

Two absolute scores about 0.5 apart cannot say whether a revision improved
the film when the run-to-run noise is about ±0.5 (AUTO-107). Shown the same
moment of the previous cut and the new one, a reviewer can say which is
better and why. So `yt qa` pairs every sampled still with the previous kept
cut's still at the same timestamp (reused when that pass sampled the same
moment, rendered from the previous cut's own document when it did not),
asks one `<pass>-compare-pass` inspection over the pairs, each labelled A
and B in an order recorded per moment, and files the tally (better, worse,
same per category each moment bears on) beside the absolute scores.
`improve.judge` reads it (tests/test_improve_side_by_side.py). No model,
no network, no host: a fake engine answers, a fake renderer draws.
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
from ytstudio import harness, qa
from tests import test_qa_listening as listening, test_qa_motion as motion

STORY = {"premise_restated": "a comet's tail is blown by a wind",
         "sections": [{"section": 1, "viewer_has_at_end": "the tail points away"}],
         "satisfaction": "4 satisfied", "satisfaction_why": "the payoff lands"}

#: What marks the comparison's prompt apart from a scoring sample's.
COMPARE = "two cuts"


def sample(score=7.0):
    return {"scores": {k: score for k in qa.CATEGORIES}, "overall": score,
            "verdict": qa.NOT_READY, "findings": [], "blocking": [], "not_evidenced": [],
            "rationale": "read it", "story": STORY}


def ids_in(prompt: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r'"id": "(m\d+)"', prompt)))


class Pass(unittest.TestCase):
    """Two QA passes over one project: pass-01, a changed cut, pass-02."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qa-side-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / "work"
        self.root = self.work / "one-take"
        self.root.mkdir(parents=True)
        (self.root / "scene.xml").write_text("<scene cut='1'/>")
        (self.root / "sound.json").write_text(json.dumps(listening._sound()))
        (self.root / "voice.json").write_text(json.dumps(listening._voice()))
        self.build(motion._build(audio_tracks=2))
        (self.root / "script.json").write_text(json.dumps({"sections": [
            {"heading": "One", "shots": [{"label": "1.1", "narration": "A comet.",
                                          "shows": "the tail away from the sun"}]}]}))
        saved = harness.ENGINE, qa.RENDERER
        self.addCleanup(setattr, qa, "RENDERER", saved[1])
        self.addCleanup(setattr, harness, "ENGINE", saved[0])
        seen = self.seen = {"prompts": [], "compares": [], "renders": [], "cwd": []}
        #: The comparison's answer: a callable of its prompt, or an exception.
        self.compare = compare = [lambda prompt: {"moments": [
            {"id": i, "better": "same", "why": "no difference", "bears_on": []}
            for i in ids_in(prompt)]}]

        class Reader(motion.FakeEngine):
            def stream(self_, prompt, system=None):
                from claude_engine import Event
                if COMPARE in prompt:
                    seen["compares"].append(prompt)
                    seen["cwd"].append(Path(self_.config.cwd))
                    answer = compare[0]
                    if isinstance(answer, Exception):
                        raise answer
                    answer = answer(prompt)
                else:
                    seen["prompts"].append(prompt)
                    answer = sample()
                yield Event(type="assistant", text="```json\n" + json.dumps(answer) + "\n```")
                yield Event(type="result", raw={})

        harness.ENGINE = Reader

        def render(args, scene):
            text = Path(scene).read_text()
            # Each cut draws its square at its own place, so a still says
            # which cut it is of.
            x = 100 + 7 * int((re.search(r"cut='(\d+)'", text) or [0, "0"])[1])

            def job(j):
                seen["renders"].append({"scene": str(scene), "text": text, **j})
                out = j["out"]
                if "frames" in j:
                    a, b = (int(v) for v in j["frames"].split(".."))
                    for f in range(a, b):
                        motion._square(Path(out.replace("%04d", f"{f:04d}")), x + f // 2)
                else:
                    motion._square(Path(out), x)
                return 0
            return job
        qa.RENDERER = render

    def build(self, data):
        (self.root / "build.json").write_text(json.dumps(data))
        later = (self.root / "build.json").stat().st_mtime + 60
        os.utime(self.root / "build.json", (later, later))

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
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return qa.run(self.args(**kw))

    def card(self, name="pass-02"):
        return json.loads((self.root / "qa" / name / "scorecard.json").read_text())

    def two_passes(self, **kw):
        """pass-01 on cut 1, then pass-02 on cut 2 (the same shots)."""
        self.run_qa()
        (self.root / "scene.xml").write_text("<scene cut='2'/>")
        return self.run_qa(**kw)


class Pairs(Pass):
    def test_with_no_previous_pass_nothing_is_compared(self):
        self.run_qa()
        self.assertEqual(self.seen["compares"], [])
        self.assertEqual(len(self.seen["prompts"]), qa.QA_SAMPLES)
        side = self.card("pass-01")["side_by_side"]
        self.assertEqual(side["status"], "skipped")
        self.assertIsNone(side["against"])
        self.assertEqual(side["entries"], [])
        self.assertIn("no earlier scored pass", side["why"])

    def test_the_stills_are_paired_at_identical_timestamps_and_both_are_in_the_prompt(self):
        self.two_passes()
        side = self.card()["side_by_side"]
        self.assertEqual(side["status"], "done")
        self.assertEqual(side["against"], "pass-01")
        samples = self.card()["samples"]
        self.assertEqual([m["t"] for m in side["moments"]], [s["t"] for s in samples])
        self.assertTrue(all(m["previous"] == "reused" for m in side["moments"]))
        # the previous cut was not rendered again: every still came from pass-01
        self.assertFalse([r for r in self.seen["renders"] if "previous" in r["out"]])
        prompt = self.seen["compares"][0]
        cwd = self.seen["cwd"][0]
        one = self.root / "qa" / "pass-01" / "frames"
        two = self.root / "qa" / "pass-02" / "frames"
        for m in side["moments"]:
            self.assertIn(f"{m['id']}-A.png", prompt)
            self.assertIn(f"{m['id']}-B.png", prompt)
            for slot in ("A", "B"):
                cut = one if m["order"][slot] == "previous" else two
                self.assertEqual((cwd / f"{m['id']}-{slot}.png").read_bytes(),
                                 (cut / m["frame"]).read_bytes(), (m, slot))
        self.assertNotEqual((one / samples[0]["frame"]).read_bytes(),
                            (two / samples[0]["frame"]).read_bytes())

    def test_the_comparison_is_its_own_decision_and_leaves_the_scoring_prompts_as_they_were(self):
        self.two_passes()
        names = [d["name"] for d in self.card()["engine"]["decisions"]]
        self.assertEqual(names[:qa.QA_SAMPLES],
                         [f"pass-02-scorecard-s{k}" for k in range(1, qa.QA_SAMPLES + 1)])
        self.assertIn("pass-02-compare-pass", names)
        for prompt in self.seen["prompts"]:
            self.assertNotIn(COMPARE, prompt)
            self.assertNotIn("-A.png", prompt)

    def test_the_prompt_does_not_say_which_cut_is_new(self):
        self.two_passes()
        prompt = self.seen["compares"][0]
        for word in ("pass-01", "pass-02", "previous", "earlier", "newer", "scorecard",
                     '"overall"', '"scores"', "7.0"):
            self.assertNotIn(word, prompt)
        self.assertNotIn("pass-", self.seen["cwd"][0].name)

    def test_the_ab_order_is_recorded_and_varies(self):
        self.two_passes()
        side = self.card()["side_by_side"]
        firsts = [m["order"]["A"] for m in side["moments"]]
        self.assertEqual(set(firsts), {"previous", "new"})
        for m in side["moments"]:
            self.assertEqual({m["order"]["A"], m["order"]["B"]}, {"previous", "new"})
        self.assertEqual(firsts, qa.side_order("pass-02", len(firsts)))
        self.assertEqual(firsts, qa.side_order("pass-02", len(firsts)))  # deterministic

    def test_an_unchanged_cut_is_not_compared(self):
        self.run_qa()
        self.run_qa()                        # nothing in the cut changed
        side = self.card()["side_by_side"]
        self.assertEqual(side["status"], "skipped")
        self.assertIn("same cut", side["why"])
        self.assertEqual(self.seen["compares"], [])

    def test_a_reused_pass_is_not_compared(self):
        self.two_passes()
        self.run_qa(reuse="pass-02", fresh=True)
        side = self.card()["side_by_side"]
        self.assertEqual(side["status"], "skipped")
        self.assertIn("--reuse", side["why"])


class Moved(Pass):
    """The cut's timing changed: the previous pass has no still at a new moment."""

    def moved(self):
        shifted = motion._build(audio_tracks=2)
        for shot in shifted["shots"][1:]:
            shot["at"] += 1.0
        shifted["scene"]["duration"] += 1.0
        return shifted

    def test_a_moved_moment_is_rendered_from_the_previous_cut_at_the_new_timestamp(self):
        self.run_qa()
        # what yt improve keeps before a pass: the kept cut's document
        snap = self.root / "improve" / "pass-01" / "snapshot"
        snap.mkdir(parents=True)
        shutil.copyfile(self.root / "scene.xml", snap / "scene.xml")
        (self.root / "scene.xml").write_text("<scene cut='2'/>")
        self.build(self.moved())
        self.run_qa()
        side = self.card()["side_by_side"]
        self.assertEqual(side["status"], "done")
        by = {m["previous"] for m in side["moments"]}
        self.assertEqual(by, {"reused", "rendered"})          # 1.1 did not move
        rendered = [m for m in side["moments"] if m["previous"] == "rendered"]
        jobs = [r for r in self.seen["renders"] if f"{os.sep}previous{os.sep}" in r["out"]]
        self.assertEqual(sorted(j["still"] for j in jobs), sorted(m["t"] for m in rendered))
        for job in jobs:
            self.assertEqual(job["text"], "<scene cut='1'/>")   # the previous cut's own
            self.assertEqual(Path(job["scene"]).parent, self.root)
            self.assertFalse(Path(job["scene"]).exists())       # the copy was removed
        self.assertEqual(len(side["moments"]), len(self.card()["samples"]))

    def test_without_the_previous_document_a_moved_moment_is_unpaired(self):
        self.run_qa()
        (self.root / "scene.xml").write_text("<scene cut='2'/>")
        self.build(self.moved())
        self.run_qa()
        side = self.card()["side_by_side"]
        self.assertEqual(side["status"], "done")
        self.assertTrue(side["unpaired"])
        self.assertTrue(all("document" in u["why"] for u in side["unpaired"]))
        self.assertTrue(all(m["previous"] == "reused" for m in side["moments"]))
        self.assertTrue(any("side by side" in n for n in self.card()["not_evidenced"]))

    def test_a_previous_document_whose_includes_changed_is_not_rendered(self):
        (self.root / "vfx").mkdir()
        (self.root / "vfx" / "a.include.scene.xml").write_text("<scene look='1'/>")
        (self.root / "scene.xml").write_text(
            "<scene cut='1'><include src=\"vfx/a.include.scene.xml\"/></scene>")
        self.run_qa()
        snap = self.root / "improve" / "pass-01" / "snapshot"
        snap.mkdir(parents=True)
        shutil.copyfile(self.root / "scene.xml", snap / "scene.xml")
        (self.root / "vfx" / "a.include.scene.xml").write_text("<scene look='2'/>")
        (self.root / "scene.xml").write_text(
            "<scene cut='2'><include src=\"vfx/a.include.scene.xml\"/></scene>")
        self.build(self.moved())
        self.run_qa()
        side = self.card()["side_by_side"]
        self.assertFalse([r for r in self.seen["renders"] if "previous" in r["out"]])
        self.assertTrue(side["unpaired"])


class Tally(Pass):
    def answer(self, pick):
        """Answer each moment by `pick(order of A)`, bearing on two categories."""
        def reply(prompt):
            ids = ids_in(prompt)
            order = qa.side_order("pass-02", len(ids))
            return {"moments": [{"id": i, "better": pick(a), "why": "the key reads",
                                 "bears_on": ["visual_quality", "cinematography"]}
                                for i, a in zip(ids, order)]}
        self.compare[0] = reply

    def test_a_moment_the_new_cut_wins_is_better_for_each_category_it_bears_on(self):
        self.answer(lambda a: "A" if a == "new" else "B")
        self.two_passes()
        side = self.card()["side_by_side"]
        n = len(side["moments"])
        self.assertEqual(side["tally"]["visual_quality"], {"better": n, "worse": 0, "same": 0})
        self.assertEqual(side["tally"]["cinematography"]["better"], n)
        self.assertEqual(side["tally"]["total"], {"better": 2 * n, "worse": 0, "same": 0})
        self.assertNotIn("animation_vfx", side["tally"])
        self.assertEqual(len(side["entries"]), 2 * n)
        for e in side["entries"]:
            self.assertEqual((e["result"], e["source"]), ("better", "stills"))
            self.assertIn(e["category"], qa.SIDE_CATEGORIES)
        self.assertEqual(sum(side["picked"].values()), n)

    def test_a_moment_the_previous_cut_wins_is_worse(self):
        self.answer(lambda a: "A" if a == "previous" else "B")
        self.two_passes()
        total = self.card()["side_by_side"]["tally"]["total"]
        self.assertEqual((total["better"], total["worse"]), (0, total["worse"]))
        self.assertGreater(total["worse"], 0)

    def test_the_audio_categories_are_not_judged_from_stills(self):
        for key in qa.AUDIO:
            self.assertNotIn(key, qa.SIDE_CATEGORIES)
        items = qa.SIDE_SCHEMA["properties"]["moments"]["items"]
        self.assertEqual(items["properties"]["bears_on"]["items"]["enum"],
                         list(qa.SIDE_CATEGORIES))

    def test_the_tally_reads_entries_of_any_source(self):
        entries = [{"category": "visual_quality", "result": "better", "source": "video"},
                   {"category": "visual_quality", "result": "same", "source": "stills"},
                   {"category": "editing_pacing", "result": "worse", "source": "video"},
                   {"category": "editing_pacing", "result": "sideways", "source": "video"}]
        got = qa.tally(entries)
        self.assertEqual(got["visual_quality"], {"better": 1, "worse": 0, "same": 1})
        self.assertEqual(got["total"], {"better": 1, "worse": 1, "same": 1})

    def test_a_failed_comparison_writes_no_tally_and_is_billed(self):
        self.compare[0] = ClaudeError("the session ended")
        code = self.two_passes()
        card = self.card()
        side = card["side_by_side"]
        self.assertEqual(side["status"], "failed")
        self.assertEqual(side["entries"], [])
        self.assertIn("the session ended", side["why"])
        rows = [d for d in card["engine"]["decisions"] if d["name"] == "pass-02-compare-pass"]
        self.assertEqual([r["kind"] for r in rows], ["failed"])
        self.assertIn(code, (0, 1))                     # qa finished and filed its pass
        self.assertTrue(card["scores"])

    def test_findings_md_shows_the_tally(self):
        self.answer(lambda a: "A" if a == "new" else "B")
        self.two_passes()
        text = (self.root / "qa" / "pass-02" / "findings.md").read_text()
        self.assertIn("## Side by side", text)
        self.assertIn("Against pass-01", text)


class Previous(Pass):
    def test_previous_names_the_pass_compared_with(self):
        self.run_qa()
        (self.root / "scene.xml").write_text("<scene cut='2'/>")
        self.run_qa()
        (self.root / "scene.xml").write_text("<scene cut='3'/>")
        self.run_qa(previous="pass-01")
        card = self.card("pass-03")
        self.assertEqual(card["side_by_side"]["against"], "pass-01")
        self.assertEqual(card["comparison"]["against"], "pass-01")

    def test_a_previous_pass_that_is_not_there_stops_before_anything_is_rendered(self):
        self.run_qa()
        before = len(self.seen["renders"])
        self.assertEqual(self.run_qa(previous="pass-09"), 2)
        self.assertEqual(len(self.seen["renders"]), before)
        self.assertFalse((self.root / "qa" / "pass-02").exists())

    def test_the_flag_is_on_yt_qa(self):
        from ytstudio import cli
        ns = cli.build_parser().parse_args(["qa", "--previous", "pass-03"])
        self.assertEqual(ns.previous, "pass-03")


class Docs(unittest.TestCase):
    """The manual, the README, the production brief and the docstrings say it."""

    ROOT = Path(__file__).resolve().parent.parent

    def flat(self, rel):
        return " ".join((self.ROOT / rel).read_text(encoding="utf-8").split())

    def test_the_manual_the_readme_and_the_brief_say_it(self):
        from ytstudio import improve
        for rel in ("ytstudio/SKILL.md", "README.md"):
            text = self.flat(rel)
            for needle in ("`side_by_side`", "`--previous PASS`", "`<pass>-compare-pass`",
                           "`spend.COMPARE_PASS_USD`", "`qa.tally`", "AUTO-108"):
                self.assertIn(needle, text, rel)
        production = self.flat("docs/production.md")
        for needle in ("side by side", "pass-01/side-by-side/"):
            self.assertIn(needle, production)
        self.assertIn("AUTO-108", qa.__doc__)
        self.assertIn("side by side", improve.__doc__)
        self.assertIn("--previous PASS", qa.__doc__)


if __name__ == "__main__":
    unittest.main()
