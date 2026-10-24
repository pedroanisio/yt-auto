"""AUTO-156: the QA judge is run on labelled fixture films before its READY is read as calibrated.

The READY bar is applied to the medians of an LLM judge's scores, and
nothing checked that those scores tell a good film from a bad one. This is
the check, as narrative.calibrate is for the story review:

- tests/fixtures/qa/<case>/ holds exactly what qa.score_evidence reads
  (build.json, script.json, voice.json, samples.json, frames/ and
  frames/burst/) and expected.json: the verdict, the categories that must
  be under or over the bar, the moments a finding must land in, and how far
  a category must drop against the base case;
- `python -m ytstudio.qa calibrate tests/fixtures/qa` scores every case
  through score_evidence and judges the raw card (`qa.judge_case`), with no
  cap, no mechanical blocker and no silence rule, caching under
  <work>/calibration-qa/<case>/ and reporting to
  <work>/calibration-qa/report.json;
- the seeded cases are drawn by scripts/seed_qa_fixtures.py, which gives
  byte-identical files on every run.

No model, no render, no ssh: a scripted judge answers each inspection from
the case's own expected.json (or as a test says), and the renderer and the
geometry check fail the test if anything reaches them.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from claude_engine import Event, Usage  # noqa: E402
from ytstudio import harness, qa  # noqa: E402
from ytstudio.core import read_json  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "qa"
SEED = ROOT / "scripts" / "seed_qa_fixtures.py"
CASES = ("good", "good-b", "slide-drift", "wall-of-text", "band-violation",
         "illegible-type", "placeholder-moving", "tells-not-shows")
SIDE = qa.SIDE_CATEGORIES


def expected_of(case: str) -> dict:
    return json.loads((FIXTURES / case / "expected.json").read_text(encoding="utf-8"))


def story_for(script: dict) -> dict:
    return {"premise_restated": "a comet's tail points away from the sun",
            "sections": [{"section": n, "viewer_has_at_end": "something new"}
                         for n, _ in enumerate(script.get("sections") or [], 1)],
            "satisfaction": "4 satisfied", "satisfaction_why": "it lands"}


def card_for(scores: dict, script: dict, findings=()) -> dict:
    """One sample's answer: the scores given, 0 for the audio the prompt
    says nothing heard, the overall the mean of the side categories."""
    full = {k: 0.0 for k in qa.CATEGORIES} | dict(scores)
    side = [full[k] for k in SIDE]
    return {"scores": full, "overall": round(sum(side) / len(side), 2),
            "verdict": qa.NOT_READY, "findings": list(findings), "blocking": [],
            "not_evidenced": [], "rationale": "read it", "story": story_for(script)}


def agreeing(case: str, expected: dict, script: dict, k: int) -> dict:
    """What a judge that agrees with every label answers: 9.2 except 6.0
    on the under-bar categories, and a finding in each must_find range,
    blocking where the case expects NOT READY."""
    scores = {c: 9.2 for c in SIDE} | {c: 6.0 for c in expected.get("under_bar") or []}
    blocking = expected.get("verdict") == "NOT_READY"
    findings = []
    for want in expected.get("must_find") or []:
        a, b = want["t_range"]
        findings.append({"timestamp": round((a + b) / 2, 3), "frame": "",
                         "problem": f"the fault at {a:g}-{b:g}", "why": "it shows",
                         "root_cause": "seeded", "owner": (want.get("owners") or ["vfx"])[0],
                         "fix": "remove it", "severity": "blocking" if blocking else "minor"})
    return card_for(scores, script, findings)


class Judge:
    """A scripted QA judge. `answer(case, expected, script, k)` gives each
    scorecard sample; the merge groups the findings filed at one timestamp
    by different samples. Every call is recorded; `cost` is billed each."""

    calls: list = []
    answer = staticmethod(agreeing)
    cost = 0.0
    fixtures = FIXTURES

    def __init__(self, config):
        self.config = config
        self.usage = Usage()

    def stream(self, prompt, system=None):
        cwd = Path(self.config.cwd)
        # the case's directory is named by a key, never by the case: the
        # judge's working directory must not say "placeholder-moving"
        keys = {qa.case_key(p.name): p.name for p in Judge.fixtures.iterdir() if p.is_dir()}
        case = keys[cwd.parent.parent.name]
        Judge.calls.append(("inspect", case, prompt, str(cwd)))
        self.usage.cost_usd += Judge.cost
        script = json.loads((cwd.parent.parent / "script.json").read_text())
        k = len([c for c in Judge.calls if c[0] == "inspect" and c[1] == case])
        answer = Judge.answer(case, expected_of_dir(Judge.fixtures, case), script, k)
        yield Event(type="assistant", text="```json\n" + json.dumps(answer) + "\n```")
        yield Event(type="result", raw={})

    def ask_json(self, prompt, schema, system=None):
        Judge.calls.append(("decide", None, prompt, ""))
        rows = json.loads(prompt.split("by reading:\n", 1)[1])
        by_t: dict = {}
        for row in rows:
            by_t.setdefault((row["timestamp"], row["kind"]), []).append(row["id"])
        return {"groups": [{"findings": ids, "why": "the same defect"}
                           for ids in by_t.values() if len(ids) > 1]}


def expected_of_dir(directory: Path, case: str) -> dict:
    return json.loads((directory / case / "expected.json").read_text(encoding="utf-8"))


class Calibrating(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qa-calibrate-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / "work"
        saved = harness.ENGINE, qa.RENDERER, qa.GEOMETRY
        self.addCleanup(setattr, harness, "ENGINE", saved[0])
        self.addCleanup(setattr, qa, "RENDERER", saved[1])
        self.addCleanup(setattr, qa, "GEOMETRY", saved[2])
        harness.ENGINE = Judge
        Judge.calls, Judge.answer, Judge.cost, Judge.fixtures = [], staticmethod(agreeing), 0.0, \
            FIXTURES

        def never(*a, **kw):
            raise AssertionError("calibrate reached the renderer or the geometry check")
        qa.RENDERER = qa.GEOMETRY = never

    def calibrate(self, directory=FIXTURES, **kw) -> dict:
        Judge.fixtures = Path(directory)
        return qa.calibrate(directory, work=self.work, verbose=False, **kw)

    def rows(self, report) -> dict:
        return {r["name"]: r for r in report["cases"]}

    def owner_copy(self) -> Path:
        """The committed set with good and good-b labelled by the owner: what
        the set is once real films are captured."""
        tree = self.tmp / "cases"
        shutil.copytree(FIXTURES, tree)
        for case in ("good", "good-b"):
            path = tree / case / "expected.json"
            data = json.loads(path.read_text())
            path.write_text(json.dumps(data | {"label": "owner"}))
        return tree


class Fixtures(unittest.TestCase):
    def test_every_case_loads_through_the_qa_readers(self):
        found = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())
        self.assertEqual(found, sorted(CASES))
        tmp = Path(tempfile.mkdtemp(prefix="qa-fixture-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        for case in CASES:
            path = FIXTURES / case
            build = read_json(path / "build.json", warn=False)
            script = read_json(path / "script.json", warn=False)
            samples = read_json(path / "samples.json", warn=False, shape=list)
            voice = read_json(path / "voice.json", warn=False) if (
                path / "voice.json").is_file() else None
            self.assertTrue(build and build.get("shots"), case)
            self.assertTrue(script and script.get("sections"), case)
            self.assertTrue(samples, case)
            for row in samples:
                self.assertTrue((path / "frames" / row["frame"]).is_file(), (case, row["frame"]))
                for name in (row.get("burst") or {}).get("files") or []:
                    self.assertTrue((path / "frames" / name).is_file(), (case, name))
            self.assertTrue(qa.narration_text(script, build, voice), case)
            self.assertIsNotNone(qa.story_brief(script, build), case)
            sheet = qa.contact_sheet(samples, path / "frames", tmp / f"{case}.png")
            self.assertTrue(sheet.is_file())
            loaded = qa.load_case(path)
            self.assertEqual(loaded["name"], case)
            self.assertEqual(loaded["expected"], expected_of(case))
            for key in ("label", "judge_only", "verdict", "under_bar", "over_bar"):
                self.assertIn(key, loaded["expected"], (case, key))
            self.assertIn(loaded["expected"]["verdict"], ("READY", "NOT_READY"))
            for cat in loaded["expected"]["under_bar"] + loaded["expected"]["over_bar"] + list(
                    loaded["expected"].get("drop") or {}):
                self.assertIn(cat, SIDE, (case, cat))
            for want in loaded["expected"].get("must_find") or []:
                for owner in want.get("owners") or []:
                    self.assertIn(owner, qa.OWNERS, (case, owner))
        # a case without samples.json, or one naming a frame it lacks, fails with its path
        broken = tmp / "broken"
        shutil.copytree(FIXTURES / "good", broken)
        frame = json.loads((broken / "samples.json").read_text())[0]["frame"]
        (broken / "frames" / frame).unlink()
        with self.assertRaises(ValueError) as caught:
            qa.load_case(broken)
        self.assertIn(str(broken / "frames" / frame), str(caught.exception))
        (broken / "samples.json").unlink()
        with self.assertRaises(ValueError) as caught:
            qa.load_case(broken)
        self.assertIn(str(broken / "samples.json"), str(caught.exception))

    def test_fixture_tree_is_under_budget(self):
        total = 0
        for case in CASES:
            size = sum(p.stat().st_size for p in (FIXTURES / case).rglob("*") if p.is_file())
            self.assertLessEqual(size, qa.FIXTURE_CASE_BYTES, case)
            total += size
            for png in (FIXTURES / case).rglob("*.png"):
                with Image.open(png) as im:
                    self.assertLessEqual(im.width, qa.FIXTURE_WIDTH, png)
        self.assertLessEqual(total, qa.FIXTURE_TREE_BYTES)
        self.assertEqual(qa.FIXTURE_WIDTH, qa.SHEET_TILE[0])

    def test_judge_only_cases_have_no_mechanical_finding_on_their_fault(self):
        # A judge-only case is one no mechanical check sees: its frames, as
        # measured, file no mechanical finding at all, and the build proves
        # nothing blocks (a moving placeholder is not in build.placeholders).
        judged = [c for c in CASES if expected_of(c)["judge_only"]]
        self.assertEqual(sorted(judged), sorted(["slide-drift", "wall-of-text", "band-violation",
                                                 "illegible-type", "placeholder-moving",
                                                 "tells-not-shows"]))
        for case in judged + ["good", "good-b"]:
            path = FIXTURES / case
            samples = qa.measure(json.loads((path / "samples.json").read_text()),
                                 path / "frames")
            found = qa.mechanical_findings(samples)
            self.assertEqual(found, [], (case, [(f["shot"], f["kind"]) for f in found]))
            for row in samples:
                self.assertEqual(row["motion"]["verdict"], "moving", (case, row["frame"]))
            build = json.loads((path / "build.json").read_text())
            self.assertEqual(qa.mechanical_blockers(build, gate=None), [], case)

    def test_seeded_cases_differ_from_good_where_they_say(self):
        good = FIXTURES / "good"
        frames = lambda case: {p.relative_to(FIXTURES / case): p.read_bytes()  # noqa: E731
                               for p in (FIXTURES / case / "frames").rglob("*.png")}
        base = frames("good")
        for case in ("slide-drift", "wall-of-text", "band-violation", "illegible-type"):
            mine = frames(case)
            self.assertEqual(set(mine), set(base), case)
            self.assertTrue(all(mine[k] != base[k] for k in base), case)
        # one shot's frames are the placeholder, every other frame is good's
        placeholder = frames("placeholder-moving")
        changed = {k for k in base if placeholder[k] != base[k]}
        self.assertTrue(changed and changed != set(base))
        # tells-not-shows changes the words and not one pixel
        self.assertEqual(frames("tells-not-shows"), base)
        said = json.loads((FIXTURES / "tells-not-shows" / "script.json").read_text())
        self.assertNotEqual(said, json.loads((good / "script.json").read_text()))
        for case in CASES:
            if case.startswith("good"):
                continue
            self.assertEqual(expected_of(case)["base"], "good", case)
            self.assertTrue(expected_of(case).get("drop"), case)
            self.assertEqual(expected_of(case)["label"], "seeded", case)
        self.assertEqual(json.loads((FIXTURES / "placeholder-moving" / "build.json")
                                    .read_text())["placeholders"], [])

    def test_seed_script_is_deterministic(self):
        tmp = Path(tempfile.mkdtemp(prefix="qa-seed-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        base = [] if expected_of("good")["label"] != "owner" else [
            "--base", str(FIXTURES / "good")]
        trees = []
        for run in ("one", "two"):
            out = tmp / run
            done = subprocess.run([sys.executable, str(SEED), "--out", str(out), *base],
                                  capture_output=True, text=True, cwd=tmp)
            self.assertEqual(done.returncode, 0, done.stderr)
            trees.append({p.relative_to(out).as_posix(): p.read_bytes()
                          for p in sorted(out.rglob("*")) if p.is_file()})
        self.assertEqual(trees[0], trees[1])
        committed = {p.relative_to(FIXTURES).as_posix(): p.read_bytes()
                     for p in sorted(FIXTURES.rglob("*")) if p.is_file()}
        self.assertEqual(sorted(trees[0]), sorted(committed))
        for name, data in committed.items():
            self.assertEqual(trees[0][name], data, name)


def good_card(**scores) -> dict:
    """A combined card a READY judge files: 9.2 on every side category,
    the audio unheard (None), nothing blocking."""
    s = {c: 9.2 for c in SIDE} | {c: None for c in qa.AUDIO} | scores
    side = [s[c] for c in SIDE if s[c] is not None]
    return {"scores": s, "overall": round(sum(side) / len(side), 2), "spread": {},
            "findings": [], "low_confidence": [], "blocking": [], "not_evidenced": [],
            "verdict": qa.READY, "scoring": {"asked": 3, "valid": 3, "samples": []}}


def finding(t, owner="vfx", severity="minor", **extra):
    return {"timestamp": t, "frame": "", "problem": "a placeholder card", "why": "seen",
            "root_cause": "seeded", "owner": owner, "fix": "draw it",
            "severity": severity, **extra}


class JudgeCase(unittest.TestCase):
    def test_judge_case_passes_matching_card(self):
        expected = {"label": "seeded", "judge_only": True, "verdict": "NOT_READY",
                    "under_bar": ["graphics_overlays"], "over_bar": ["cinematography"],
                    "must_find": [{"t_range": [4.0, 9.0], "owners": ["build", "vfx"]}]}
        card = good_card(graphics_overlays=6.0)
        card["findings"] = [finding(5.5, owner="build", samples=[1, 2])]
        self.assertEqual(qa.judge_case(expected, card), [])
        self.assertEqual(qa.judge_verdict(card)[0], qa.NOT_READY)

    def test_judge_case_names_the_under_bar_category_it_missed(self):
        expected = {"verdict": "NOT_READY", "under_bar": ["graphics_overlays"],
                    "over_bar": [], "judge_only": True, "label": "seeded"}
        card = good_card(cinematography=7.0)
        failures = qa.judge_case(expected, card)
        self.assertTrue(any("graphics_overlays" in f and "under_bar" in f for f in failures),
                        failures)
        self.assertFalse(any("cinematography" in f for f in failures), failures)
        # the verdict matched (cinematography is under the bar): only the category failed
        self.assertFalse(any(f.startswith("verdict") for f in failures), failures)

    def test_over_bar_category_named_when_scored_low(self):
        expected = {"verdict": "READY", "under_bar": [], "over_bar": ["cinematography"],
                    "judge_only": False, "label": "owner"}
        failures = qa.judge_case(expected, good_card(cinematography=8.0))
        self.assertTrue(any("over_bar" in f and "cinematography" in f for f in failures))
        self.assertTrue(any(f.startswith("verdict") for f in failures), failures)

    def test_must_find_needs_a_two_sample_finding_in_range(self):
        expected = {"verdict": "NOT_READY", "under_bar": [], "over_bar": [],
                    "judge_only": True, "label": "seeded",
                    "must_find": [{"t_range": [4.0, 9.0], "owners": ["vfx"]}]}
        card = good_card()
        card["blocking"] = ["a placeholder at 5s"]
        # one sample's finding is low confidence: it does not count
        card["low_confidence"] = [finding(5.0, samples=[2], confidence="low")]
        self.assertTrue(any("must_find" in f for f in qa.judge_case(expected, card)))
        # two samples', outside the range: still missed
        card["findings"] = [finding(9.5, samples=[1, 3])]
        self.assertTrue(any("must_find" in f for f in qa.judge_case(expected, card)))
        # in the range but owned by another stage: missed
        card["findings"] = [finding(5.0, owner="script", samples=[1, 3])]
        self.assertTrue(any("must_find" in f for f in qa.judge_case(expected, card)))
        card["findings"] = [finding(5.0, samples=[1, 3])]
        self.assertEqual(qa.judge_case(expected, card), [])
        # without owners, any owner in the range counts
        loose = copy.deepcopy(expected)
        loose["must_find"][0].pop("owners")
        card["findings"] = [finding(4.0, owner="script", samples=[1, 3])]
        self.assertEqual(qa.judge_case(loose, card), [])

    def test_drop_against_the_base_case(self):
        expected = {"verdict": "NOT_READY", "under_bar": ["graphics_overlays"],
                    "over_bar": [], "judge_only": True, "label": "seeded", "base": "good",
                    "drop": {"graphics_overlays": 1.0}}
        base = good_card(graphics_overlays=8.0)
        self.assertEqual(qa.judge_case(expected, good_card(graphics_overlays=6.5), base), [])
        flat = qa.judge_case(expected, good_card(graphics_overlays=7.5), base)
        self.assertTrue(any(f.startswith("drop") and "graphics_overlays" in f for f in flat),
                        flat)
        missing = qa.judge_case(expected, good_card(graphics_overlays=6.5), None)
        self.assertTrue(any(f.startswith("drop") and "good" in f for f in missing), missing)

    def test_audio_none_does_not_force_not_ready_in_judge_verdict(self):
        card = good_card()
        self.assertTrue(all(card["scores"][k] is None for k in qa.AUDIO))
        verdict, reasons = qa.judge_verdict(card)
        self.assertEqual(verdict, qa.READY, reasons)
        # the bar run() applies would not call it READY: the audio is unheard
        self.assertEqual(qa.verdict_of(card["scores"], card["overall"], blocking=[])[0],
                         qa.NOT_READY)
        # blocking: the judge's own two-sample claims and its blocking findings
        self.assertEqual(qa.judge_verdict(card | {"blocking": ["a claim"]})[0], qa.NOT_READY)
        self.assertEqual(qa.judge_verdict(card | {"findings": [
            finding(3.0, severity="blocking", samples=[1, 2])]})[0], qa.NOT_READY)
        # a low-confidence blocking finding blocks nothing
        self.assertEqual(qa.judge_verdict(card | {"low_confidence": [
            finding(3.0, severity="blocking", samples=[1])]})[0], qa.READY)
        # the overall is the mean of the side categories
        low = good_card(cinematography=0.0)
        self.assertEqual(qa.judge_verdict(low)[0], qa.NOT_READY)
        self.assertEqual(qa.judge_verdict(card | {"verdict": qa.NOT_EVIDENCED,
                                                  "scores": {}})[0], qa.NOT_EVIDENCED)


class Calibrate(Calibrating):
    def test_an_agreeing_judge_passes_every_case_and_the_set_needs_an_owner_label(self):
        report = self.calibrate()
        rows = self.rows(report)
        self.assertEqual(sorted(rows), sorted(CASES))
        for name, row in rows.items():
            self.assertTrue(row["passed"], (name, row["failures"]))
            self.assertEqual(row["judge_only"], expected_of(name)["judge_only"])
            self.assertEqual(row["expected"], expected_of(name))
            self.assertIn(row["got"]["verdict"], (qa.READY, qa.NOT_READY))
            for key in ("scores", "spread", "findings"):
                self.assertIn(key, row["got"])
        # the committed good cases are drawn stand-ins, not an owner's film:
        # the set does not pass until one is captured and labelled
        self.assertFalse(report["passed"])
        self.assertTrue(any("owner" in f for f in report["failures"]), report["failures"])
        self.assertEqual(report["false_positive"], {})
        self.assertEqual(report["false_negative"], {})
        owned = self.calibrate(self.owner_copy())
        self.assertTrue(owned["passed"], owned["failures"])
        self.assertEqual(owned["model"], "sonnet")
        self.assertEqual(owned["digest"], qa.judge_digest("sonnet", owned["house"]))

    def test_the_judge_sees_no_label(self):
        self.calibrate()
        for kind, case, prompt, cwd in Judge.calls:
            if kind != "inspect":
                continue
            for name in CASES:
                if name != "good":
                    self.assertNotIn(name, prompt, (case, name))
                self.assertNotIn(name, cwd, (case, name))
            self.assertNotIn("under_bar", prompt)
            self.assertNotIn("must_find", prompt)
        for case in CASES:
            here = qa.case_dir(self.work, case) / "qa" / "pass-01"
            self.assertEqual(here.parent.parent.parent, self.work / qa.CALIBRATION_QA)
            self.assertFalse((here / "expected.json").exists())
            self.assertTrue((here / "contact-sheet.png").is_file())
            self.assertTrue((here / "narration.txt").is_file())

    def test_over_bar_category_scored_low_is_a_false_positive(self):
        def harsh_on_good(case, expected, script, k):
            card = agreeing(case, expected, script, k)
            if case.startswith("good"):
                card["scores"]["cinematography"] = 7.0
            return card
        Judge.answer = staticmethod(harsh_on_good)
        report = self.calibrate()
        over = [c for c in ("good", "good-b") if "cinematography" in expected_of(c)["over_bar"]]
        self.assertTrue(over)
        self.assertEqual(report["false_positive"], {"cinematography": len(over)})
        for case in over:
            self.assertFalse(self.rows(report)[case]["passed"])

    def test_an_under_bar_category_scored_high_is_a_false_negative(self):
        def lenient(case, expected, script, k):
            card = agreeing(case, expected, script, k)
            if case == "wall-of-text":
                card["scores"]["graphics_overlays"] = 9.0
            return card
        Judge.answer = staticmethod(lenient)
        report = self.calibrate()
        self.assertEqual(report["false_negative"], {"graphics_overlays": 1})
        self.assertFalse(self.rows(report)["wall-of-text"]["passed"])

    def test_drop_against_base_fails_when_scores_are_flat(self):
        Judge.answer = staticmethod(lambda case, expected, script, k: card_for(
            {c: 6.0 for c in SIDE}, script))
        report = self.calibrate()
        for name, row in self.rows(report).items():
            self.assertFalse(row["passed"], name)
            if name.startswith("good"):
                self.assertTrue(any(f.startswith("over_bar") for f in row["failures"]),
                                row["failures"])
            else:
                self.assertTrue(any(f.startswith("drop") for f in row["failures"]),
                                (name, row["failures"]))

    def test_judge_that_rejects_everything_fails_the_set(self):
        Judge.answer = staticmethod(lambda case, expected, script, k: card_for(
            {c: 3.0 for c in SIDE}, script))
        report = self.calibrate(self.owner_copy())
        self.assertFalse(report["passed"])
        for case in ("good", "good-b"):
            row = self.rows(report)[case]
            self.assertFalse(row["passed"])
            self.assertTrue(any(f.startswith("verdict") for f in row["failures"]))

    def test_mechanical_caps_are_recorded_not_judged(self):
        tree = self.tmp / "cases"
        shutil.copytree(FIXTURES / "good", tree / "good")
        samples = json.loads((tree / "good" / "samples.json").read_text())
        hook = samples[0]
        for name in [hook["frame"], *hook["burst"]["files"]]:
            Image.new("RGB", (480, 270), (90, 90, 90)).save(tree / "good" / "frames" / name)
        report = self.calibrate(tree)
        row = self.rows(report)["good"]
        kinds = {(f["kind"], f["severity"]) for f in row["mechanical"]}
        self.assertIn(("flat", "blocking"), kinds)
        self.assertEqual(row["got"]["scores"]["animation_vfx"], 9.2)    # not capped
        self.assertEqual(row["got"]["verdict"], qa.READY)
        self.assertTrue(row["passed"], row["failures"])
        # the mechanical findings still reached the judge, as in run()
        prompt = next(c[2] for c in Judge.calls if c[0] == "inspect")
        self.assertIn("nearly flat frame", prompt)

    def test_second_run_makes_no_engine_call(self):
        first = self.calibrate()
        asked = len(Judge.calls)
        self.assertGreater(asked, 0)
        Judge.calls = []
        second = self.calibrate()
        self.assertEqual(Judge.calls, [])
        self.assertEqual(second["cost_usd"], 0)
        self.assertEqual(first, second)

    def test_report_and_cache_paths(self):
        report = self.calibrate()
        path = self.work / qa.CALIBRATION_QA / "report.json"
        self.assertEqual(qa.CALIBRATION_QA, "calibration-qa")
        self.assertEqual(json.loads(path.read_text()), report)
        self.assertFalse((self.work / "calibration").exists())
        for case in CASES:
            decided = qa.case_dir(self.work, case) / "decisions" / "qa"
            names = sorted(p.name for p in decided.glob("*.json"))
            self.assertIn("pass-01-scorecard-s1.json", names, case)
        self.assertEqual(report["directory"], str(FIXTURES))

    def test_budget_stops_further_cases_and_unread_cases_fail(self):
        Judge.cost = 0.2
        report = self.calibrate(budget_usd=0.5)
        rows = report["cases"]
        read = [r for r in rows if r["ran"]]
        self.assertEqual(len(read), 1)
        for row in rows[1:]:
            self.assertFalse(row["passed"])
            self.assertTrue(any("not read" in f for f in row["failures"]), row["failures"])
        self.assertFalse(report["passed"])
        self.assertGreaterEqual(report["cost_usd"], 0.5)

    def test_the_command_prints_one_row_a_case_and_the_counts(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = qa.main(["calibrate", str(FIXTURES), "--work", str(self.work)])
        self.assertEqual(code, 1)          # no owner-labelled case yet
        text = out.getvalue()
        for case in CASES:
            self.assertTrue(any(line.startswith(case + " ") for line in text.splitlines()),
                            case)
        self.assertIn("false positives", text)
        self.assertIn("false negatives", text)
        self.assertTrue((self.work / qa.CALIBRATION_QA / "report.json").is_file())
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(qa.main(["calibrate", str(self.tmp / "nowhere")]), 2)


class Digest(unittest.TestCase):
    def test_judge_digest_ignores_narration_and_changes_with_role(self):
        house = {"block_sha256": "abc"}
        first = qa.judge_digest("sonnet", house)
        self.assertEqual(first, qa.judge_digest("sonnet", house))
        # a fixture's narration is not part of it
        tmp = Path(tempfile.mkdtemp(prefix="qa-digest-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        shutil.copytree(FIXTURES / "good", tmp / "good")
        script = json.loads((tmp / "good" / "script.json").read_text())
        script["sections"][0]["shots"][0]["narration"] = "Something else entirely."
        (tmp / "good" / "script.json").write_text(json.dumps(script))
        self.assertEqual(qa.judge_digest("sonnet", house), first)
        self.assertNotEqual(qa.judge_digest("opus", house), first)
        self.assertNotEqual(qa.judge_digest("sonnet", {"block_sha256": "def"}), first)
        for name, value in (("ROLE", qa.ROLE + " Be kind."), ("CATEGORY_BAR", 8.0),
                            ("OVERALL_BAR", 8.0), ("QA_SAMPLES", 5)):
            saved = getattr(qa, name)
            setattr(qa, name, value)
            try:
                self.assertNotEqual(qa.judge_digest("sonnet", house), first, name)
            finally:
                setattr(qa, name, saved)
        self.assertEqual(qa.judge_digest("sonnet", house), first)


class Capture(unittest.TestCase):
    def test_capture_keeps_eight_spread_samples_three_burst_frames_at_tile_width(self):
        tmp = Path(tempfile.mkdtemp(prefix="qa-capture-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        project = tmp / "film"
        pass_dir = project / "qa" / "pass-03"
        build = {"scene": {"duration": 60.0, "fps": 30, "width": 1920, "height": 1080},
                 "shots": [{"label": f"1.{n}", "at": 5.0 * (n - 1), "seconds": 5.0,
                            "asset": "effect", "section": "One"} for n in range(1, 13)],
                 "placeholders": [], "findings": [], "audio_tracks": 2}
        samples = qa.sample(build)
        self.assertEqual(len(samples), 12)
        for row in samples:
            for name in [row["frame"], *row["burst"]["files"]]:
                path = pass_dir / "frames" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (1920, 1080), (40, 60, 90)).save(path)
        (pass_dir / "samples.json").write_text(json.dumps(samples))
        (project / "build.json").write_text(json.dumps(build))
        (project / "script.json").write_text(json.dumps({"sections": [{"heading": "One",
            "shots": [{"label": s["label"], "narration": "A line."} for s in build["shots"]]}]}))
        case = tmp / "case"
        summary = qa.capture_fixture(pass_dir, case)
        kept = json.loads((case / "samples.json").read_text())
        self.assertEqual(len(kept), qa.FIXTURE_SAMPLES)
        times = [r["t"] for r in kept]
        self.assertEqual(times, sorted(times))
        self.assertLess(times[0], 10.0)
        self.assertGreater(times[-1], 50.0)
        for row in kept:
            self.assertEqual(len(row["burst"]["files"]), 3)
            for name in [row["frame"], *row["burst"]["files"]]:
                with Image.open(case / "frames" / name) as im:
                    self.assertEqual(im.size, (480, 270))
        self.assertTrue((case / "build.json").is_file())
        self.assertTrue((case / "script.json").is_file())
        self.assertFalse((case / "voice.json").exists())
        self.assertFalse((case / "expected.json").exists())   # the owner writes the label
        self.assertEqual(summary["samples"], qa.FIXTURE_SAMPLES)
        self.assertGreater(summary["bytes"], 0)


if __name__ == "__main__":
    unittest.main()
