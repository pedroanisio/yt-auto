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
import importlib.util
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
         "illegible-type", "placeholder-moving", "tells-not-shows", "generic-identity")
SIDE = qa.SIDE_CATEGORIES


def seed_module():
    """scripts/seed_qa_fixtures.py as a module: its stand-in's geometry and
    words, so a test can say where each seeded change may and may not be."""
    spec = importlib.util.spec_from_file_location("seed_qa_fixtures", SEED)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rgb(path: Path) -> Image.Image:
    with Image.open(path) as im:
        return im.convert("RGB")


def changed_outside(a: Image.Image, b: Image.Image, boxes, tolerance: int) -> tuple | None:
    """The bounding box of the pixels that differ by more than `tolerance`
    in any channel between `a` and `b`, the `boxes` (padded by 2 px) left
    out; None when nothing outside them changed."""
    from PIL import ImageChops, ImageDraw
    r, g, b_ = ImageChops.difference(a, b).split()
    diff = ImageChops.lighter(ImageChops.lighter(r, g), b_).point(
        lambda v: 255 if v > tolerance else 0)
    px = ImageDraw.Draw(diff)
    for box in boxes:
        x0, y0, x1, y1 = box
        px.rectangle([x0 - 2, y0 - 2, x1 + 2, y1 + 2], fill=0)
    return diff.getbbox()


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

    def test_a_frame_samples_json_does_not_name_fails_the_case(self):
        # the judge is told to read every PNG under frames/: one no sample
        # names (a stand-in's still left behind by a capture) would be judged
        # as part of the film
        tmp = Path(tempfile.mkdtemp(prefix="qa-fixture-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        for stale in ("t_09m00s000.png", "burst/t_09m00s000_0001.png"):
            case = tmp / stale.replace("/", "-")
            shutil.copytree(FIXTURES / "good", case)
            Image.new("RGB", (480, 270), (40, 60, 90)).save(case / "frames" / stale)
            with self.assertRaises(ValueError) as caught:
                qa.load_case(case)
            self.assertIn(str(case / "frames" / stale), str(caught.exception))

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
                                                 "tells-not-shows", "generic-identity"]))
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
        # generic-identity (AUTO-146) changes the look and not one word: every
        # frame is a stock explainer template of the same subject, and the
        # only category it must sink is editorial_identity
        generic = frames("generic-identity")
        self.assertEqual(set(generic), set(base))
        self.assertTrue(all(generic[k] != base[k] for k in base))
        for name in ("script.json", "voice.json", "samples.json", "build.json"):
            self.assertEqual((FIXTURES / "generic-identity" / name).read_bytes(),
                             (good / name).read_bytes(), name)
        self.assertEqual(expected_of("generic-identity")["under_bar"], ["editorial_identity"])
        self.assertEqual(expected_of("generic-identity")["drop"], {"editorial_identity": 1.0})
        for case in CASES:
            if case.startswith("good"):
                continue
            self.assertEqual(expected_of(case)["base"], "good", case)
            self.assertTrue(expected_of(case).get("drop"), case)
            self.assertEqual(expected_of(case)["label"], "seeded", case)
        self.assertEqual(json.loads((FIXTURES / "placeholder-moving" / "build.json")
                                    .read_text())["placeholders"], [])
        # the prompt places "a picture that does not show what its line says"
        # with whoever draws it, and a story fault with script: a judge that
        # follows it may file the told lines under either, so both count
        for want in expected_of("tells-not-shows")["must_find"]:
            self.assertEqual(sorted(want["owners"]), ["script", "vfx"])

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


class Seeded(unittest.TestCase):
    """Each seeded case changes the one thing its expected.json names, and
    nothing a judge could mark down for another reason."""

    #: How far a stand-in pixel outside the changed boxes may move: the
    #: frames are stored quantized, and a seeded frame's palette is its own.
    #: The committed set needs 4 (band-violation) and 7 (slide-drift's
    #: caption); a seam or a washed band moves pixels by tens.
    TOLERANCE = 8

    def setUp(self):
        self.seed = seed_module()
        self.good = read_json(FIXTURES / "good" / "samples.json", warn=False, shape=list)
        build = read_json(FIXTURES / "good" / "build.json", warn=False)
        self.starts = self.seed.section_starts(build)
        self.headings = {str(s["label"]): s["section"] for s in build["shots"]}
        self.terms = self.seed.terms()

    def frames(self):
        for row in self.good:
            label = str(row["label"])
            heading = self.headings[label] if label in self.starts else None
            for name, _ in self.seed.frames_of(row):
                yield name, label, heading, self.terms[label]

    def test_tells_not_shows_lines_claim_what_the_frames_do_not_show(self):
        # the card's fault: a line describing a process the picture never
        # shows, so the judge must hold the picture against the line; a line
        # that claims nothing ("too hard to show you") is caught from the text
        told = json.loads((FIXTURES / "tells-not-shows" / "script.json").read_text())
        good = json.loads((FIXTURES / "good" / "script.json").read_text())
        shot = lambda script, label: next(  # noqa: E731
            sh for sec in script["sections"] for sh in sec["shots"] if sh["label"] == label)
        self.assertEqual(sorted(self.seed.TOLD), ["2.1", "2.2", "2.3"])
        for label, claim in self.seed.TOLD_CLAIMS.items():
            line = shot(told, label)["narration"]
            self.assertEqual(line, self.seed.TOLD[label])
            self.assertNotEqual(line, shot(good, label)["narration"])
            self.assertIn(claim, line.lower(), label)
            drawn = shot(good, label)
            for text in (drawn["shows"], drawn["visual"], drawn["narration"],
                         self.terms[label]):
                self.assertNotIn(claim, text.lower(), (label, text))
            for vague in ("complicated", "invisible", "important", "word for it", "too hard"):
                self.assertNotIn(vague, line.lower(), label)
        # nothing else of the script changed
        for sec_t, sec_g in zip(told["sections"], good["sections"]):
            for a, b in zip(sec_t["shots"], sec_g["shots"]):
                if a["label"] not in self.seed.TOLD:
                    self.assertEqual(a, b)

    def test_with_a_captured_base_the_sample_lines_say_the_rewritten_narration(self):
        # a real pass's samples carry each line's text: rewritten with it
        tmp = Path(tempfile.mkdtemp(prefix="qa-seed-base-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        base = tmp / "captured"
        shutil.copytree(FIXTURES / "good", base)
        expected = json.loads((base / "expected.json").read_text())
        (base / "expected.json").write_text(json.dumps(expected | {"label": "owner"}))
        script = json.loads((base / "script.json").read_text())
        said = {sh["label"]: sh["narration"] for sec in script["sections"] for sh in sec["shots"]}
        samples = json.loads((base / "samples.json").read_text())
        for row in samples:
            line = said[row["label"]]
            row["lines"] = [{"at": row["at"], "text": line, "frame": row["frame"]}]
            row["why"] = f"{row['why']}; {qa._line_why(line)}"
        (base / "samples.json").write_text(json.dumps(samples))
        out = tmp / "out"
        done = subprocess.run([sys.executable, str(SEED), "--out", str(out), "--base", str(base)],
                              capture_output=True, text=True, cwd=tmp)
        self.assertEqual(done.returncode, 0, done.stderr)
        rows = json.loads((out / "tells-not-shows" / "samples.json").read_text())
        rewritten = [r for r in rows if r["label"] in self.seed.TOLD]
        self.assertTrue(rewritten)
        for row in rows:
            want = self.seed.TOLD.get(row["label"], said[row["label"]])
            self.assertEqual([ln["text"] for ln in row["lines"]], [want], row["label"])
            self.assertIn(qa._line_why(want), row["why"])
            if row["label"] in self.seed.TOLD:
                self.assertNotIn(qa._line_why(said[row["label"]]), row["why"])
        for case in ("good", "good-b", *self.seed.SEEDED):
            qa.load_case(out / case)

    def test_band_violation_moves_the_overlays_and_leaves_the_picture_whole(self):
        # supers in the middle band, captions in the top one: the picture
        # itself is the good film's, with no seam where bands were swapped
        moved_super = 0
        for name, label, heading, term in self.frames():
            good = rgb(FIXTURES / "good" / "frames" / name)
            bad = rgb(FIXTURES / "band-violation" / "frames" / name)
            was = self.seed.overlay_boxes(heading, term, "good")
            now = self.seed.overlay_boxes(heading, term, "violation")
            self.assertIsNone(changed_outside(good, bad, [*was.values(), *now.values()],
                                              self.TOLERANCE), name)
            self.assertLess(now["caption"][3], self.seed.TOP)
            self.assertIsNotNone(changed_outside(good, bad, [], self.TOLERANCE), name)
            if heading:
                x0, y0, x1, y1 = now["super"]
                self.assertGreater(y0, self.seed.TOP)
                self.assertLess(y1, self.seed.BOTTOM)
                moved_super += 1
        self.assertGreater(moved_super, 0)

    def test_illegible_type_changes_only_the_overlay_boxes(self):
        for name, label, heading, term in self.frames():
            good = rgb(FIXTURES / "good" / "frames" / name)
            bad = rgb(FIXTURES / "illegible-type" / "frames" / name)
            boxes = self.seed.overlay_boxes(heading, term, "good")
            self.assertIsNone(changed_outside(good, bad, boxes.values(), self.TOLERANCE), name)
            # the caption box itself is redrawn
            self.assertIsNotNone(changed_outside(good, bad, [], self.TOLERANCE), name)

    def test_slide_drift_is_a_slide_of_the_shots_own_words(self):
        said = {sh["label"]: sh["narration"] for sec in
                json.loads((FIXTURES / "good" / "script.json").read_text())["sections"]
                for sh in sec["shots"]}
        for label, line in said.items():
            bullets = self.seed.slide_bullets(line)
            self.assertTrue(1 <= len(bullets) <= 3, (label, bullets))
            words = [w.strip(".,:;?!").lower() for w in line.split()]
            for bullet in bullets:
                for word in bullet.split():
                    self.assertIn(word.strip(".,:;?!").lower(), words, (label, bullet))
        from PIL import ImageChops, ImageStat
        for name, label, heading, term in self.frames():
            good = rgb(FIXTURES / "good" / "frames" / name)
            slide = rgb(FIXTURES / "slide-drift" / "frames" / name)
            caption = self.seed.overlay_boxes(None, term, "good")["caption"]
            # the caption is the base's, where it was
            self.assertIsNone(changed_outside(good.crop(caption), slide.crop(caption), [],
                                              self.TOLERANCE), name)
            # and the film under it is gone: the bottom band is the slide's
            strip = (caption[2] + 4, self.seed.BOTTOM, self.seed.W, self.seed.H)
            diff = ImageStat.Stat(ImageChops.difference(good.crop(strip),
                                                        slide.crop(strip))).mean
            self.assertGreater(max(diff), 8.0, name)


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

    def test_a_one_reading_finding_left_unmerged_does_not_count(self):
        # combine_samples keeps every finding unmerged, each with samples=[k],
        # when the merge decision fails: one reading's finding is then under
        # `findings`, and it is not one two samples agree on
        expected = {"verdict": "NOT_READY", "under_bar": [], "over_bar": [],
                    "judge_only": True, "label": "seeded",
                    "must_find": [{"t_range": [4.0, 9.0], "owners": ["vfx"]}]}
        card = good_card()
        card["blocking"] = ["a placeholder at 5s"]
        for alone in (finding(5.0, samples=[2]), finding(5.0)):
            card["findings"] = [alone]
            self.assertTrue(any("must_find" in f for f in qa.judge_case(expected, card)), alone)
        # nor does one reading's blocking finding block the judge's verdict
        self.assertEqual(qa.judge_verdict(good_card() | {"findings": [
            finding(3.0, severity="blocking", samples=[1])]})[0], qa.READY)
        # a card whose merge failed is a failure of its own: its blocking
        # claims are every reading's, unmerged, so nothing tells what two agree on
        failed = good_card() | {"scoring": {"asked": 3, "valid": 3, "samples": [],
                                            "merge": {"failed": "no answer"}}}
        self.assertTrue(any(f.startswith("merge:") for f in qa.judge_case(
            {"verdict": "READY", "under_bar": [], "over_bar": []}, failed)))

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

    def test_a_failed_merge_fails_the_cases_that_needed_one(self):
        def no_merge(self, prompt, schema, system=None):
            raise harness.HarnessError("the merge was not answered")
        saved = Judge.ask_json
        Judge.ask_json = no_merge
        self.addCleanup(setattr, Judge, "ask_json", saved)
        report = self.calibrate(self.owner_copy())
        self.assertFalse(report["passed"])
        for name, row in self.rows(report).items():
            if expected_of(name).get("must_find"):
                self.assertFalse(row["passed"], name)
                self.assertTrue(any(f.startswith("must_find") for f in row["failures"]),
                                (name, row["failures"]))
                self.assertTrue(any(f.startswith("merge") for f in row["failures"]),
                                (name, row["failures"]))
            else:
                # nothing to merge: no finding was filed
                self.assertTrue(row["passed"], (name, row["failures"]))

    def test_no_report_is_there_while_the_judge_reads(self):
        # the judge's cwd is under <work>/calibration-qa/, beside report.json,
        # which maps every case folder to its labels: it is not there while
        # any case is scored, so a judge that reads past its cwd finds none
        self.calibrate()
        report = self.work / qa.CALIBRATION_QA / qa.CALIBRATION_REPORT
        self.assertTrue(report.is_file())
        seen = []
        stream = Judge.stream

        def watching(judge, prompt, system=None):
            seen.append(report.exists())
            yield from stream(judge, prompt, system)
        Judge.stream = watching
        self.addCleanup(setattr, Judge, "stream", stream)
        self.calibrate(fresh=True)
        self.assertTrue(seen)
        self.assertFalse(any(seen))
        self.assertTrue(report.is_file())

    def test_the_report_names_the_fixture_set_it_ran(self):
        report = self.calibrate()
        self.assertEqual(report["fixture_set"], qa.fixture_set_digest(FIXTURES))
        self.assertEqual(qa.fixture_set_digest(FIXTURES), qa.fixture_set_digest(qa.QA_FIXTURES))
        owned = self.calibrate(self.owner_copy())
        self.assertNotEqual(owned["fixture_set"], report["fixture_set"])
        # one easy case is not the set
        single = self.tmp / "single"
        shutil.copytree(FIXTURES / "good", single / "good")
        self.assertNotEqual(qa.fixture_set_digest(single), report["fixture_set"])

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


class Digest(Calibrating):
    def test_judge_digest_ignores_narration_and_changes_with_role(self):
        house = {"block_sha256": "abc"}
        first = qa.judge_digest("sonnet", house)
        self.assertEqual(first, qa.judge_digest("sonnet", house))
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

    def test_two_sets_that_differ_in_narration_calibrate_the_same_judge(self):
        # the narration reaches the judge (it is in the pass's narration.txt
        # and the story brief) and not the digest: the PROBE prompt carries
        # no film's words
        said = "Something else entirely, about a kettle."
        trees = []
        for name, line in (("a", None), ("b", said)):
            tree = self.tmp / name
            shutil.copytree(FIXTURES, tree)
            if line:
                path = tree / "good" / "script.json"
                script = json.loads(path.read_text())
                script["sections"][0]["shots"][0]["narration"] = line
                path.write_text(json.dumps(script))
                path = tree / "good" / "voice.json"
                voice = json.loads(path.read_text())
                voice["takes"][0]["text"] = line
                path.write_text(json.dumps(voice))
            trees.append(tree)
        reports = []
        for name, tree in zip("ab", trees):
            Judge.fixtures = tree
            reports.append(qa.calibrate(tree, work=self.tmp / f"work-{name}", verbose=False))
        self.assertEqual(reports[0]["digest"], reports[1]["digest"])
        heard = (qa.case_dir(self.tmp / "work-b", "good") / "qa" / "pass-01"
                 / qa.NARRATION).read_text()
        self.assertIn(said, heard)
        good = json.loads((FIXTURES / "good" / "script.json").read_text())
        probe = qa.review_prompt(**qa.PROBE)
        for sec in good["sections"]:
            for shot in sec["shots"]:
                self.assertNotIn(shot["narration"], probe)
        self.assertNotIn(said, probe)


class JudgeCalibration(unittest.TestCase):
    """qa.json's judge_calibration against a report on disk."""

    house = {"block_sha256": "abc"}

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="qa-judge-calibration-"))
        self.addCleanup(shutil.rmtree, self.work, True)

    def report(self, **fields):
        path = self.work / qa.CALIBRATION_QA / qa.CALIBRATION_REPORT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "passed": True, "model": "sonnet", "digest": qa.judge_digest("sonnet", self.house),
            "fixture_set": qa.fixture_set_digest(qa.QA_FIXTURES), "cases": []} | fields))

    def test_a_matching_report_is_calibration(self):
        self.report()
        got = qa.judge_calibration(self.work, "sonnet", self.house)
        self.assertIs(got["fixtures"], True, got["why"])

    def test_a_listened_or_watched_pass_is_not_the_judge_calibrated(self):
        # calibrate runs the unheard judge (ROLE, no listener's or watcher's
        # brief): a pass that listened or watched asked another judge
        self.report()
        for flag, word in (("listened", "listened"), ("watched", "watched")):
            got = qa.judge_calibration(self.work, "sonnet", self.house, **{flag: True})
            self.assertIs(got["fixtures"], False, flag)
            self.assertIn(word, got["why"])
            self.assertIn("unheard", got["why"])

    def test_a_report_on_another_fixture_set_is_not_calibration(self):
        for fields in ({"fixture_set": "a single easy case"}, {"fixture_set": None}):
            self.report(**fields)
            got = qa.judge_calibration(self.work, "sonnet", self.house)
            self.assertIs(got["fixtures"], False, fields)
            self.assertIn("fixture set", got["why"])


class Docs(unittest.TestCase):
    def test_the_manual_readme_and_spec_name_the_calibration(self):
        for path in (ROOT / "ytstudio" / "SKILL.md", ROOT / "README.md",
                     ROOT / "docs" / "production.md"):
            text = " ".join(path.read_text(encoding="utf-8").split())
            self.assertIn("python -m ytstudio.qa calibrate", text, path)
            self.assertIn("capture-fixture", text, path)
            self.assertIn("judge_calibration", text, path)
            self.assertIn("gates nothing", text, path)
        # the per-case folder is a key, not the case's name, and the docs say so
        for path in (ROOT / "ytstudio" / "SKILL.md", ROOT / "README.md"):
            text = " ".join(path.read_text(encoding="utf-8").split())
            self.assertIn("case_key", text, path)
            self.assertIn("fixture_set", text, path)
        skill = (ROOT / "ytstudio" / "SKILL.md").read_text(encoding="utf-8")
        for case in CASES:
            self.assertIn(f"`{case}`", skill)
        self.assertIn("judge: fixture-calibrated (<model>); 0 owner-labelled films", skill)
        self.assertIn("judge: not calibrated against the QA fixtures (<why>); "
                      "0 owner-labelled films", skill)
        # §12 says what `fixtures` does and does not mean, and the re-run rule
        spec = " ".join((ROOT / "docs" / "production.md").read_text(encoding="utf-8").split())
        twelve = spec.split("## 12.", 1)[1].split("## 13.", 1)[0]
        self.assertIn("`fixtures` true means", twelve)
        self.assertIn("does not mean", twelve)
        self.assertIn("re-run rule", twelve)
        for card in ("AUTO-141", "AUTO-145", "AUTO-146", "AUTO-148"):
            self.assertIn(card, twelve)
        # the re-run rule sits above judge_digest
        source = (ROOT / "ytstudio" / "qa.py").read_text(encoding="utf-8")
        above = source.split("def judge_digest(")[0].rsplit("\n\n\n", 1)[-1]
        self.assertIn("The re-run rule", above)


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

    def test_capture_into_a_used_case_leaves_nothing_of_the_old_one(self):
        # README's workflow captures into tests/fixtures/qa/good, which holds
        # the drawn stand-in: none of its frames, samples or manifests may
        # stay beside the real film's
        tmp = Path(tempfile.mkdtemp(prefix="qa-capture-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        pass_dir = self.project(tmp)
        case = tmp / "good"
        shutil.copytree(FIXTURES / "good", case)
        label = (case / "expected.json").read_bytes()
        qa.capture_fixture(pass_dir, case)
        kept = json.loads((case / "samples.json").read_text())
        named = {name for row in kept for name in [row["frame"], *row["burst"]["files"]]}
        have = {p.relative_to(case / "frames").as_posix()
                for p in (case / "frames").rglob("*") if p.is_file()}
        self.assertEqual(have, named)
        self.assertFalse((case / "voice.json").exists())       # the stand-in's; the film has none
        self.assertEqual((case / "expected.json").read_bytes(), label)   # the owner relabels it
        loaded = qa.load_case(case)
        self.assertEqual(len(loaded["samples"]), qa.FIXTURE_SAMPLES)

    def project(self, tmp: Path) -> Path:
        project = tmp / "film"
        pass_dir = project / "qa" / "pass-03"
        build = {"scene": {"duration": 60.0, "fps": 30, "width": 1920, "height": 1080},
                 "shots": [{"label": f"1.{n}", "at": 5.0 * (n - 1), "seconds": 5.0,
                            "asset": "effect", "section": "One"} for n in range(1, 13)],
                 "placeholders": [], "findings": [], "audio_tracks": 2}
        samples = qa.sample(build)
        for row in samples:
            for name in [row["frame"], *row["burst"]["files"]]:
                path = pass_dir / "frames" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (1920, 1080), (40, 60, 90)).save(path)
        (pass_dir / "samples.json").write_text(json.dumps(samples))
        (project / "build.json").write_text(json.dumps(build))
        (project / "script.json").write_text(json.dumps({"sections": [{"heading": "One",
            "shots": [{"label": s["label"], "narration": "A line."} for s in build["shots"]]}]}))
        return pass_dir


if __name__ == "__main__":
    unittest.main()
