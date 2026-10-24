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
#: A failed build as sdkkit.build_findings words it: a traceback, on lines.
BUILD_ERROR = ("error: rule:program: `python3 kit.py build 01-open.py` failed: Traceback "
               "(most recent call last):\n  File \"/w/01-open.py\", line 3, in <module>\n"
               "    scene.add_cameraa()\nAttributeError: the SDK has no element cameraa")
#: Round 0's and round 1's documents, told apart by one value, so the prompt
#: can be checked for both programs and the change between them.
FIRST, SECOND = WORLD_DOC, WORLD_DOC.replace("#AAAAAA", "#BBBBBB")
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

    def test_the_error_a_host_that_lost_a_round_twice_leaves_is_no_lesson(self):
        lost = ("error: the host lost 2 frame(s) in two passes: infra: frame 3 timed out; "
                "author it again (yt vfx --sections 1)")
        self.assertEqual(vfx.fixed_errors([rnd(0, errors=[lost]), rnd(1, score=8)]), [])

    def test_a_score_gain_alone_is_no_lesson(self):
        # promotion follows the verdict, and a gain is no fixed error
        self.assertEqual(vfx.fixed_errors([rnd(0, score=4), rnd(1, score=9)]), [])

    def test_an_error_seen_twice_is_one_lesson_from_its_last_round(self):
        got = vfx.fixed_errors([rnd(0, errors=[BROKEN]), rnd(1, errors=[BROKEN]),
                                rnd(2, score=8)])
        self.assertEqual(got, [{"round_from": 1, "round_to": 2, "finding": BROKEN}])


class LessonProblems(unittest.TestCase):
    """The mechanical check, on its own: a lesson is about the engine, never
    the look, and answers one of the fixed findings."""
    NAMES = {"camera", "light"}
    STYLE = dict(STYLE, name="Night Harbour", fonts=["Inter", {"family": "IBM Plex Mono"}])

    def problems(self, tokens=None, fixed=(BROKEN, BUILD_ERROR), **over):
        return vfx.lesson_problems(lesson(**over), fixed=list(fixed), names=self.NAMES,
                                   style=self.STYLE, tokens=tokens or {"ink": "#FFFFFF"})

    def test_a_valid_lesson_has_no_problem(self):
        self.assertEqual(self.problems(), [])

    def test_the_finding_may_be_its_number_or_its_words_with_other_spacing(self):
        for finding in ("1", "2", "#2", " ".join(BUILD_ERROR.split()), BROKEN + "\n"):
            with self.subTest(finding=finding):
                self.assertEqual(self.problems(finding=finding), [])
        for finding in ("3", "0", "error: the camera frames something"):
            with self.subTest(finding=finding):
                self.assertTrue(self.problems(finding=finding))

    def test_the_fixed_finding_an_answer_names(self):
        fixed = [BROKEN, BUILD_ERROR]
        self.assertEqual(vfx.fixed_finding("2", fixed), BUILD_ERROR)
        self.assertEqual(vfx.fixed_finding(" ".join(BUILD_ERROR.split()), fixed), BUILD_ERROR)
        self.assertIsNone(vfx.fixed_finding("9", fixed))

    def test_a_palette_name_is_refused_in_any_case_as_a_whole_word(self):
        for text in ("Keep the camera on the Ink swatch.",
                     "Keep the camera on the ink swatch.",
                     "Light the BASE layer first."):
            with self.subTest(text=text):
                self.assertTrue(any("palette" in p for p in self.problems(text=text)))
        # inside another word it is no name
        self.assertEqual(self.problems(text="A database of cameras is not kept; "
                                            "key the camera at each phase."), [])

    def test_the_styles_own_name_and_its_fonts_are_refused(self):
        for text in ("A night harbour camera holds still.",
                     "Set text in Inter before the camera moves.",
                     "Set labels in ibm plex mono before the camera moves."):
            with self.subTest(text=text):
                self.assertTrue(self.problems(text=text))

    def test_a_token_of_the_stage_is_refused_by_its_name_and_its_value(self):
        tokens = {"glow": "rgb(10, 20, 30)"}
        self.assertTrue(self.problems(tokens=tokens, text="Fill with --glow first."))
        self.assertTrue(self.problems(tokens=tokens,
                                      text="Fill with rgb(10, 20, 30) before the camera moves."))


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

    def author(self, *, broken_first: bool = True, build_broken_first: bool = False,
               during=None, **kw) -> dict:
        """One section whose first round fails the gate (when `broken_first`,
        by a validation error; when `build_broken_first`, by a build that
        fails and saves no document) and whose revision passes it with a
        review over the bar. `during(planner, authored)` runs at each gate."""
        planner = self.planner(**kw)
        self.planner_used = planner
        group = {"section": "Open", "seconds": 15, "effects": EFFECTS, "cuts": []}
        validate = planner.ws.validate
        build = vfx.sdkkit.build_findings

        def authored() -> int:
            return sum(1 for c in FakeEngine.stream_calls if "Write" in c["tools"])

        def gated(name, **kwargs):
            symbols, findings = validate(name, **kwargs)
            if during:
                during(planner, authored())
            if broken_first and authored() == 1:
                return symbols, findings + [BROKEN]
            return symbols, findings

        def built(program, document):
            if build_broken_first and authored() == 1:
                # a program that fails saves nothing
                (Path(program).parent / document).unlink(missing_ok=True)
                return [BUILD_ERROR]
            return build(program, document)
        with patch.object(vfx.render, "engine_validate", lambda data, **k: []), \
                patch.object(vfx.render, "engine_render_still",
                             lambda xml, at, **k: (varied_png(), "rendered")), \
                patch.object(vfx, "encode_findings", lambda *a, **k: []), \
                patch.object(vfx.sdkkit, "build_findings", built), \
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
            answer = Scripted.answers.pop(0) if Scripted.answers else {}
            if isinstance(answer, Exception):
                raise answer
            return answer
        return super().ask_json(prompt, schema, system)


class TheLesson(Case):
    def assert_both_programs(self, prompt: str) -> None:
        """Round 0's program, round 1's, and the change between them."""
        self.assertIn("#AAAAAA", prompt.split("Round 0's program", 1)[1].split("```\n", 1)[0])
        self.assertIn("#BBBBBB", prompt.split("Round 1's program", 1)[1])
        diff = prompt.split("```diff\n", 1)[1].split("```", 1)[0]
        self.assertTrue(any(ln.startswith("-") and "#AAAAAA" in ln
                            for ln in diff.splitlines()), diff)
        self.assertTrue(any(ln.startswith("+") and "#BBBBBB" in ln
                            for ln in diff.splitlines()), diff)

    def test_a_build_that_failed_still_shows_its_program_and_the_change(self):
        # The card's main case: round 0's build fails and saves no document,
        # so keep_round keeps nothing; its program is kept alone for the lesson.
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "fine"}]
        FakeEngine.docs = [FIRST, SECOND]
        Scripted.answers = [lesson(finding="1")]
        doc = self.author(broken_first=False, build_broken_first=True)
        self.assertEqual([r["errors"] for r in doc["rounds"]], [1, 0])
        self.assertEqual(len(Scripted.asked), 1)
        prompt = Scripted.asked[0]
        self.assertNotIn("(not kept)", prompt)
        self.assert_both_programs(prompt)
        r00 = self.planner_used.ws.dir / "rounds" / "01-open" / "r00"
        self.assertTrue((r00 / "01-open.py").is_file())
        # no document: the round is never one to restore
        self.assertFalse((r00 / "01-open.scene.xml").exists())
        self.assertFalse(self.planner_used.ws.restore_round("01-open.scene.xml", 0))
        # the finding answered by its number is logged word for word
        self.assertEqual([r["finding"] for r in self.rows()], [BUILD_ERROR])

    def test_a_finding_given_with_its_spacing_changed_is_the_fixed_one(self):
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "fine"}]
        Scripted.answers = [lesson(finding="  " + " ".join(BUILD_ERROR.split()) + " ")]
        self.author(broken_first=False, build_broken_first=True)
        self.assertEqual(len(Scripted.asked), 1)
        self.assertIn(f"1. round 0 -> round 1: {BUILD_ERROR}", Scripted.asked[0])
        self.assertEqual([r["finding"] for r in self.rows()], [BUILD_ERROR])

    def test_a_reviewer_that_fails_leaves_a_warning_and_the_section_its_record(self):
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "fine"}]
        Scripted.answers = [harness.HarnessError("the reviewer is down")]
        doc = self.author()
        self.assertEqual(self.rows(), [])
        self.assertFalse(vfx.has_errors(doc))
        self.assertTrue(any(f.startswith("warning: lesson") and "the reviewer is down" in f
                            for f in doc["findings"]), doc["findings"])
        record = self.planner_used.ws.read_record("01-open.scene.xml")
        self.assertTrue(record["finished"])
        self.assertEqual(record["lesson"]["problem"], doc["lesson"]["problem"])

    def test_any_failure_of_the_lesson_step_leaves_the_section_its_record(self):
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "fine"}]
        Scripted.answers = [lesson()]
        with patch.object(vfx.VfxPlanner, "_lesson_prompt",
                          side_effect=OSError("a kept round cannot be read")):
            doc = self.author()
        self.assertEqual(self.rows(), [])
        self.assertFalse(vfx.has_errors(doc))
        self.assertTrue(any(f.startswith("warning: lesson") and "cannot be read" in f
                            for f in doc["findings"]), doc["findings"])
        self.assertTrue(self.planner_used.ws.read_record("01-open.scene.xml")["finished"])

    def test_a_section_whose_budget_cannot_pay_two_asks_is_not_asked(self):
        # $3.50 a section pays each session ($1.50 with the $2 review kept back)
        # but not two lesson asks at the $2 review cap.
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "fine"}]
        Scripted.answers = [lesson()]
        doc = self.author(budget_usd=3.5, review_budget_usd=2.0)
        self.assertEqual([r["errors"] for r in doc["rounds"]], [1, 0])
        self.assertEqual(Scripted.asked, [])
        self.assertEqual(self.rows(), [])
        self.assertTrue(any(f.startswith("note: lesson: not asked") for f in doc["findings"]),
                        doc["findings"])

    def test_a_finish_request_asks_no_lesson(self):
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "fine"}]
        Scripted.answers = [lesson()]

        def finish(planner, authored):
            if authored == 2:
                planner.finish.set()
        doc = self.author(during=finish)
        self.assertEqual(Scripted.asked, [])
        self.assertEqual(self.rows(), [])
        self.assertIn("note: lesson: not asked (finish requested)", doc["findings"])

    def test_a_refused_answer_is_forgotten_so_a_rerun_asks_again(self):
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "fine"}]
        Scripted.answers = [lesson(elements=["hologramEmitter"])] * 2
        self.author()
        cache = harness.Harness(self.root, "vfx")
        for ask in ("lesson-01-open", "lesson-01-open-again"):
            self.assertFalse(cache.path(ask).exists(), ask)

    def test_a_lesson_already_in_the_log_is_not_appended_again(self):
        # --fresh asks again whatever the cache holds; the same lesson for the
        # same section is still one row.
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "fine"}]
        Scripted.answers = [lesson()]
        self.author()
        self.assertEqual(len(self.rows()), 1)
        prompt = Scripted.asked[0]
        planner = self.planner()
        reviewer = harness.Harness(self.root, "vfx", model="opus", role=vfx.REVIEW_ROLE,
                                   fresh=True)
        Scripted.answers = [lesson()]
        with patch.object(vfx.VfxPlanner, "_lesson_prompt", lambda *a, **k: prompt):
            got = planner._lesson(reviewer, 1, "01-open.scene.xml",
                                  [{"round_from": 0, "round_to": 1, "finding": BROKEN}],
                                  STYLE, {"ink": "#FFFFFF"})
        self.assertEqual(len(Scripted.asked), 2)        # asked again, not served
        self.assertEqual(got["lesson"]["topic"], "camera framing")
        self.assertEqual(len(self.rows()), 1)

    def test_a_valid_general_lesson_is_logged_once_with_the_engine_version(self):
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "fine"}]
        FakeEngine.docs = [FIRST, SECOND]
        Scripted.answers = [lesson()]
        doc = self.author()
        self.assertEqual([r["errors"] for r in doc["rounds"]], [1, 0])
        self.assertEqual(len(Scripted.asked), 1)
        prompt = Scripted.asked[0]
        self.assertIn(BROKEN, prompt)
        self.assert_both_programs(prompt)
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

    def test_the_environment_check_notes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / vfx.LESSONS_LOG).write_text(json.dumps(dict(lesson(), label=None)) + "\n")
            with patch.object(doctor.render, "hosts", return_value=[]):
                report = doctor.check_environment(work)
        notes = [f for f in report.findings if f.area == "lessons log"]
        self.assertEqual(len(notes), 1, report.findings)
        self.assertIn("1 candidate", notes[0].message)


if __name__ == "__main__":
    unittest.main()
