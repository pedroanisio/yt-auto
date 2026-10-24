"""AUTO-111: a gate error that a revision fixed becomes a candidate engine
lesson. The run asks the section's reviewer once for the lesson (validated
mechanically, asked once more, else a finding), appends a general one to
work/lessons-log.jsonl, and an operator promotes a row into
kit/engine_facts.json with `yt vfx --promote-lesson`, stamped with the
engine it was learned on. Nothing is learned from a score or a verdict. The
model is never called and the render host is a stub."""

import inspect
import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from ytstudio import cli, doctor, harness, sdkkit, vfx
from ytstudio.core import Idea
from ytstudio.research import Dossier
from tests.test_vfx_world import EFFECTS, SECTION, STYLE, FakeEngine, WORLD_DOC, varied_png

BROKEN = "error: the camera frames nothing"
MANIFEST = {"engine": {"name": "scene-render", "version": "0.7.0"}, "schema": "1",
            "entries": []}


def rnd(n, *, errors=(), score=None, timed_out=False, infra=()):
    """A round as `gate` records it."""
    return {"round": n, "errors": len(errors), "score": score, "timed_out": timed_out,
            "findings": list(errors) + list(infra) + ["warning: something minor"]}


def lesson(**over):
    out = {"topic": "camera framing", "elements": ["camera"],
           "text": "A camera keyed only at the section's start holds that framing for every "
                   "phase; key the camera at each phase's offset.",
           "cause": "authoring", "finding": BROKEN, "general": True}
    out.update(over)
    return out


class FixedErrors(unittest.TestCase):
    def test_an_error_a_later_passing_round_lacks_is_fixed(self):
        got = vfx.fixed_errors([rnd(0, errors=[BROKEN]), rnd(1, score=8)])
        self.assertEqual(got, [{"round_from": 0, "round_to": 1, "finding": BROKEN}])

    def test_nothing_is_fixed_while_the_error_persists_or_the_round_is_not_judged(self):
        for later in (rnd(1, errors=[BROKEN], score=None),     # still there
                      rnd(1),                                   # no score: the host lost it
                      rnd(1, score=8, timed_out=True)):         # cut off
            with self.subTest(later=later):
                self.assertEqual(vfx.fixed_errors([rnd(0, errors=[BROKEN]), later]), [])

    def test_infra_findings_are_the_hosts_and_never_a_lesson(self):
        rounds = [rnd(0, infra=["infra: the host lost a frame"]), rnd(1, score=8)]
        self.assertEqual(vfx.fixed_errors(rounds), [])

    def test_a_score_gain_alone_is_no_lesson(self):
        # promotion follows the verdict, and a gain is no fixed error
        self.assertEqual(vfx.fixed_errors([rnd(0, score=4), rnd(1, score=9)]), [])

    def test_an_error_seen_twice_is_one_lesson_from_its_last_round(self):
        got = vfx.fixed_errors([rnd(0, errors=[BROKEN]), rnd(1, errors=[BROKEN]),
                                rnd(2, score=8)])
        self.assertEqual(got, [{"round_from": 1, "round_to": 2, "finding": BROKEN}])


class Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name)
        self.root = self.work / "film"
        self.root.mkdir()
        self.log = self.work / vfx.LESSONS_LOG
        self._engine = harness.ENGINE
        Scripted.answers, Scripted.asked = [], []
        harness.ENGINE = Scripted
        FakeEngine.stream_calls, FakeEngine.json_calls, FakeEngine.reviews = [], [], []
        FakeEngine.doc, FakeEngine.docs = WORLD_DOC, []
        FakeEngine.time_out_on = FakeEngine.crash_on = 0
        self.idea = Idea(title="T", genre="g", description="d")
        version = patch.object(vfx.sdkkit, "engine_version", lambda: "0.7.0")
        version.start()
        self.addCleanup(version.stop)

    def tearDown(self):
        harness.ENGINE = self._engine
        self._tmp.cleanup()

    def planner(self, **kw) -> vfx.VfxPlanner:
        bundle = {"idea": self.idea, "outline": {}, "claims": [], "assets": [], "premise": ""}
        kw.setdefault("revisions", 2)
        kw.setdefault("lessons_log", self.log)
        return vfx.VfxPlanner(bundle, Dossier(self.root, self.idea, at=self.root),
                              vfx.Workspace(self.root), model="sonnet", sections=[SECTION],
                              author_bar=7.0, verbose=False, jobs=1, **kw)

    def author(self, *, broken_first: bool = True, **kw) -> dict:
        """One section whose first round fails the gate (when `broken_first`)
        and whose revision passes it with a review over the bar."""
        planner = self.planner(**kw)
        self.planner_used = planner
        group = {"section": "Open", "seconds": 15, "effects": EFFECTS, "cuts": []}
        validate = planner.ws.validate

        def gated(name, **kwargs):
            symbols, findings = validate(name, **kwargs)
            authored = sum(1 for c in FakeEngine.stream_calls if "Write" in c["tools"])
            if broken_first and authored == 1:
                return symbols, findings + [BROKEN]
            return symbols, findings
        with patch.object(vfx.render, "engine_validate", lambda data, **k: []), \
                patch.object(vfx.render, "engine_render_still",
                             lambda xml, at, **k: (varied_png(), "rendered")), \
                patch.object(vfx, "encode_findings", lambda *a, **k: []), \
                patch.object(planner.ws, "validate", gated):
            return planner.author({"sections": [SECTION]}, [group], STYLE,
                                  {"ink": "#FFFFFF"})[0]

    def rows(self) -> list[dict]:
        if not self.log.is_file():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line]


class Scripted(FakeEngine):
    """The fake engine, with the lesson decisions answered from a script."""
    answers: list = []
    asked: list = []

    def ask_json(self, prompt, schema, system=None):
        if schema is vfx.LESSON_SCHEMA:
            Scripted.asked.append(prompt)
            return Scripted.answers.pop(0) if Scripted.answers else {}
        return super().ask_json(prompt, schema, system)


class TheLesson(Case):
    def test_a_valid_general_lesson_is_logged_once_with_the_engine_version(self):
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "fine"}]
        Scripted.answers = [lesson()]
        doc = self.author()
        self.assertEqual([r["errors"] for r in doc["rounds"]], [1, 0])
        self.assertEqual(len(Scripted.asked), 1)
        prompt = Scripted.asked[0]
        self.assertIn(BROKEN, prompt)
        self.assertIn("world-open", prompt)              # round 0's program is shown
        rows = self.rows()
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual(row["engine"], "0.7.0")
        self.assertIsNone(row["label"])
        self.assertEqual((row["slug"], row["section"], row["round_from"], row["round_to"]),
                         ("film", 1, 0, 1))
        self.assertEqual(row["doc"], "01-open.scene.xml")
        self.assertEqual(row["finding"], BROKEN)
        self.assertEqual(row["elements"], ["camera"])
        for key in ("topic", "text", "cause", "general"):
            self.assertEqual(row[key], lesson()[key])
        # the section's record carries it
        self.assertEqual(doc["lesson"]["lesson"]["topic"], "camera framing")
        self.assertEqual(doc["lesson"]["fixed"], [{"round_from": 0, "round_to": 1,
                                                   "finding": BROKEN}])
        self.assertEqual(doc["lesson"]["logged"], str(self.log))

    def test_a_lesson_for_this_world_only_is_recorded_and_not_logged(self):
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "fine"}]
        Scripted.answers = [lesson(general=False)]
        doc = self.author()
        self.assertEqual(self.rows(), [])
        self.assertFalse(doc["lesson"]["lesson"]["general"])
        self.assertIsNone(doc["lesson"]["logged"])

    def test_no_log_is_written_without_a_log_path(self):
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "fine"}]
        Scripted.answers = [lesson()]
        doc = self.author(lessons_log=None)
        self.assertEqual(self.rows(), [])
        self.assertTrue(doc["lesson"]["lesson"]["general"])

    def test_an_element_the_sdk_lacks_is_asked_once_more_then_a_finding(self):
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "fine"}]
        Scripted.answers = [lesson(elements=["hologramEmitter"]),
                            lesson(elements=["camera", "warpField"])]
        doc = self.author()
        self.assertEqual(len(Scripted.asked), 2)
        self.assertIn("hologramEmitter", Scripted.asked[1])     # the problem is named
        self.assertEqual(self.rows(), [])
        self.assertIsNone(doc["lesson"]["lesson"])
        self.assertTrue(any(f.startswith("warning: lesson") and "warpField" in f
                            for f in doc["findings"]), doc["findings"])
        self.assertFalse(vfx.has_errors(doc))            # a lesson never blocks the film

    def test_a_second_answer_that_passes_is_the_lesson(self):
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "fine"}]
        Scripted.answers = [lesson(elements=["hologramEmitter"]), lesson()]
        self.author()
        self.assertEqual(len(self.rows()), 1)

    def test_a_colour_or_a_style_token_in_the_text_is_refused(self):
        token = vfx.tokens_from(STYLE)["background"]
        for text in ("Set the key light to #FF8800 so the set reads.",
                     f"A camera framing reads best on {token.lower()}.",
                     "Fill the backdrop with var(--background) before the camera moves."):
            with self.subTest(text=text):
                self.setUp()
                FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "fine"}]
                Scripted.answers = [lesson(text=text), lesson(text=text)]
                doc = self.author()
                self.assertEqual(len(Scripted.asked), 2)
                self.assertEqual(self.rows(), [])
                self.assertIsNone(doc["lesson"]["lesson"])
                self.assertTrue(any(f.startswith("warning: lesson") for f in doc["findings"]))
                self.tearDown()

    def test_a_finding_that_was_not_fixed_is_refused(self):
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "fine"}]
        made_up = lesson(finding="error: something nobody reported")
        Scripted.answers = [made_up, made_up]
        doc = self.author()
        self.assertEqual(self.rows(), [])
        self.assertIsNone(doc["lesson"]["lesson"])

    def test_a_section_with_no_fixed_error_asks_nothing_and_pays_nothing(self):
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "fine"}]
        Scripted.answers = [lesson()]
        doc = self.author(broken_first=False)
        self.assertEqual(Scripted.asked, [])
        self.assertIsNone(doc["lesson"])
        self.assertEqual(self.rows(), [])
        self.assertFalse(any(f.startswith("warning: lesson") for f in doc["findings"]))

    def test_a_score_gain_without_an_error_is_no_lesson(self):
        FakeEngine.docs = [WORLD_DOC, WORLD_DOC.replace("#AAAAAA", "#BBBBBB")]
        FakeEngine.reviews = [{"frames": [], "score": 5, "summary": "under"},
                              {"frames": [], "score": 9, "summary": "over",
                               "previous": {"verdict": "better", "findings": []}}]
        doc = self.author(broken_first=False)
        self.assertEqual([r["score"] for r in doc["rounds"]], [5, 9])
        self.assertEqual(Scripted.asked, [])
        self.assertIsNone(doc["lesson"])

    def test_the_lesson_decision_is_booked_on_the_section(self):
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "fine"}]
        Scripted.answers = [lesson()]
        with_lesson = self.author()["cost_usd"]
        self.tearDown()
        self.setUp()
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "fine"}]
        Scripted.answers = [lesson()]
        # the same run, the lesson's call left out: one fake call less
        with patch.object(vfx, "fixed_errors", lambda rounds: []):
            without = self.author()["cost_usd"]
        self.assertGreater(with_lesson, without)

    def test_the_cached_lesson_is_not_logged_twice(self):
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "fine"}]
        Scripted.answers = [lesson()]
        self.author()
        self.assertEqual(len(self.rows()), 1)
        planner = self.planner()
        reviewer = harness.Harness(self.root, "vfx", model="opus", role=vfx.REVIEW_ROLE)
        fixed = [{"round_from": 0, "round_to": 1, "finding": BROKEN}]
        prompt = Scripted.asked[0]
        # asked again with the same inputs, the answer comes from the cache
        with patch.object(vfx.VfxPlanner, "_lesson_prompt", lambda *a, **k: prompt):
            planner._lesson(reviewer, 1, "01-open.scene.xml", fixed, STYLE,
                            {"ink": "#FFFFFF"})
        self.assertEqual(len(self.rows()), 1)


class Promote(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name)
        self.facts = self.work / "engine_facts.json"
        shutil.copy(sdkkit.FACTS_FILE, self.facts)
        patcher = patch.object(sdkkit, "FACTS_FILE", self.facts)
        patcher.start()
        self.addCleanup(patcher.stop)
        row = dict(lesson(), engine="0.7.0", slug="film", section=3, doc="03-x.scene.xml",
                   round_from=0, round_to=1, label=None)
        (self.work / vfx.LESSONS_LOG).write_text(json.dumps(row) + "\n")

    def tearDown(self):
        self._tmp.cleanup()

    def cli(self, *argv) -> tuple[int, str]:
        args = cli.build_parser().parse_args(["vfx", "--work", str(self.work), *argv])
        out = io.StringIO()
        with patch.object(vfx, "planner_from_args",
                          lambda *a, **k: self.fail("the planner must not run")), \
                patch.object(vfx.workspace, "resolve",
                             lambda *a, **k: self.fail("no project is needed")), \
                redirect_stdout(out), redirect_stderr(out):
            code = vfx.run(args)
        return code, out.getvalue()

    def test_lessons_lists_the_candidate_rows(self):
        code, out = self.cli("--lessons")
        self.assertEqual(code, 0)
        self.assertIn("0", out)
        self.assertIn("camera framing", out)
        self.assertIn("film section 3 r0->1", out)

    def test_promote_appends_the_row_with_its_source_and_its_own_engine(self):
        before = json.loads(self.facts.read_text())
        code, out = self.cli("--promote-lesson", "0")
        self.assertEqual(code, 0, out)
        after = json.loads(self.facts.read_text())
        self.assertEqual(after["verified_on"], before["verified_on"])
        self.assertEqual(after["facts"][:-1], before["facts"])
        fact = after["facts"][-1]
        self.assertEqual(fact, {"topic": "camera framing", "text": lesson()["text"],
                                "elements": ["camera"],
                                "source": "yt vfx film section 3 r0->1",
                                "verified_on": "0.7.0"})
        # promoting it again adds nothing
        self.assertEqual(self.cli("--promote-lesson", "0")[0], 0)
        self.assertEqual(len(json.loads(self.facts.read_text())["facts"]), len(after["facts"]))
        # and --lessons says so
        self.assertIn("promoted", self.cli("--lessons")[1])

    def test_a_row_out_of_range_is_refused(self):
        code, out = self.cli("--promote-lesson", "4")
        self.assertEqual(code, 2)
        self.assertIn("4", out)
        self.assertEqual(json.loads(self.facts.read_text()),
                         json.loads(sdkkit.FACTS_FILE.read_text()))

    def test_the_promoted_fact_reaches_engine_md_stamped_with_its_own_version(self):
        self.cli("--promote-lesson", "0")
        text = sdkkit.engine_text({"manifest": MANIFEST}, [])
        facts = text.split("## Facts measured on the engine", 1)[1].split("\n## ", 1)[0]
        line = next(ln for ln in facts.splitlines() if "camera framing" in ln)
        self.assertIn("yt vfx film section 3 r0->1", line)
        self.assertIn("verified on 0.7.0", line)
        self.assertNotIn("re-check", line)              # its own engine is this one
        # the hand-written facts are still flagged against the file's stamp
        self.assertIn("this engine is 0.7.0", facts.split(line)[0])
        moved = dict(MANIFEST, engine={"name": "scene-render", "version": "0.8.0"})
        text = sdkkit.engine_text({"manifest": moved}, [])
        line = next(ln for ln in text.splitlines() if "camera framing" in ln)
        self.assertIn("re-check", line)

    def test_a_file_with_no_per_fact_stamps_prints_as_before(self):
        facts = {"verified_on": "0.7.0", "facts": [{"topic": "t", "text": "a fact",
                                                    "source": "s"}]}
        self.assertEqual(sdkkit.facts_lines("0.7.0", facts),
                         ["", "## Facts measured on the engine", "",
                          "Facts verified on 0.7.0.", "", "- **t**: a fact (source: s)"])

    def test_the_facts_file_keeps_its_layout_when_written_back(self):
        text = sdkkit.FACTS_FILE.read_text(encoding="utf-8")
        self.assertEqual(vfx.facts_json(json.loads(text)), text)

    def test_the_authors_kit_has_no_command_that_writes_the_facts(self):
        for source in (sdkkit.KIT_CLI, inspect.getsource(sdkkit.main)):
            self.assertNotIn("promote", source)
            self.assertNotIn("FACTS_FILE", source)
            self.assertNotIn("engine_facts.json", source)


class Doctor(unittest.TestCase):
    def test_the_lessons_log_is_named_as_evidence_to_copy_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / vfx.LESSONS_LOG).write_text(
                json.dumps(dict(lesson(), label=None)) + "\n"
                + json.dumps(dict(lesson(topic="other"), label=None)) + "\n")
            report = doctor.Report("environment")
            doctor.lessons_log(report, work)
            notes = [f for f in report.findings if f.area == "lessons log"]
            self.assertEqual(len(notes), 1, report.findings)
            self.assertEqual(notes[0].level, "note")
            self.assertIn("2 candidate", notes[0].message)
            self.assertIn(vfx.LESSONS_LOG, notes[0].message)
            self.assertIn("copy", notes[0].fix + notes[0].message)
            self.assertIn("work/", notes[0].fix + notes[0].message)
            self.assertIn("--promote-lesson", notes[0].fix)

    def test_no_log_no_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = doctor.Report("environment")
            doctor.lessons_log(report, Path(tmp))
            self.assertEqual([f for f in report.findings if f.area == "lessons log"], [])

    def test_the_environment_check_calls_it(self):
        self.assertIn("lessons_log(report, work)", inspect.getsource(doctor.check_environment))


if __name__ == "__main__":
    unittest.main()
