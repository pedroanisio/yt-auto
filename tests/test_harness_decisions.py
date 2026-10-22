"""AUTO-49: the structured judgements of styles, script, sound, research, the
vfx planner, ideas and assess go through `Harness.decide`.

So each one gets what the delivery stages already have: an answer kept under
`decisions/<stage>/`, served again while the question is unchanged, checked for
its required fields, and listed with its cost in the stage's engine report.
The model is never called: `harness.ENGINE` is a fake.
"""

import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from claude_engine import ClaudeError, EngineConfig, Event, Usage
from ytstudio import assess, harness, ideas, research, script, sound, styles, vfx
from ytstudio.core import Idea
from ytstudio.research import Dossier

ROOT = Path(__file__).resolve().parent.parent


def blank(schema: dict):
    """An answer that satisfies `schema` and says nothing."""
    kind = schema.get("type")
    if schema.get("enum"):         # a value outside the enum is asked again
        return schema["enum"][0]
    if kind == "object":
        return {k: blank(v) for k, v in (schema.get("properties") or {}).items()
                if k in schema.get("required", [])}
    return {"array": [], "string": "", "number": 0, "integer": 0, "boolean": False}.get(kind)


class FakeEngine:
    json_calls: list = []
    texts: list = []
    answer = None            # a dict to return instead of a blank one

    def __init__(self, config):
        self.config = config
        self.usage = Usage()
        self.usage.cost_usd = 0.01
        self.usage.calls = 1

    def ask_json(self, prompt, schema, system=None):
        FakeEngine.json_calls.append({"prompt": prompt, "schema": schema, "system": system})
        return FakeEngine.answer if FakeEngine.answer is not None else blank(schema)

    def stream(self, prompt, system=None):
        for text in FakeEngine.texts:
            yield Event(type="assistant", text=text)
        yield Event(type="result", raw={})


class DecisionCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._engine = harness.ENGINE
        harness.ENGINE = FakeEngine
        self.addCleanup(setattr, harness, "ENGINE", self._engine)
        FakeEngine.json_calls, FakeEngine.texts, FakeEngine.answer = [], [], None
        self.idea = Idea(title="A test film", genre="explainer", description="one take")

    def kept(self, stage: str) -> list[str]:
        return sorted(p.stem for p in (self.root / "decisions" / stage).glob("*.json"))

    def decisions(self, report: dict) -> dict:
        return {d["name"]: d for d in report["decisions"]}


class SourceTest(unittest.TestCase):
    def test_only_the_harness_and_the_sdk_kit_call_ask_json(self):
        found = sorted(p.name for p in (ROOT / "ytstudio").glob("*.py")
                       if "ask_json" in p.read_text(encoding="utf-8"))
        self.assertIn("harness.py", found)
        self.assertLessEqual(set(found), {"harness.py", "sdkkit.py"})


class StylesTest(DecisionCase):
    def director(self):
        return styles.Director(self.idea, root=self.root, verbose=False, count=1)

    def test_the_choice_is_kept_and_served_again(self):
        first = self.director()
        first.choose([("Flat", "flat diagrams")])
        second = self.director()
        second.choose([("Flat", "flat diagrams")])
        self.assertEqual(len(FakeEngine.json_calls), 1)
        self.assertEqual(self.kept("styles"), ["choose"])
        self.assertTrue(self.decisions(second.harness.report())["choose"]["cached"])
        self.assertEqual(second.usage.cost_usd, 0)

    def test_the_report_carries_the_cost(self):
        director = self.director()
        director.choose([("Flat", "flat diagrams")])
        report = director.harness.report()
        self.assertAlmostEqual(report["cost_usd"], 0.01)
        self.assertEqual(report["decisions"][0]["name"], "choose")
        self.assertAlmostEqual(director.usage.cost_usd, 0.01)

    def test_independent_scores_are_one_decision_each(self):
        director = self.director()
        director.modes = {"independent"}
        director.shortlist([("A", ""), ("B", "")])
        self.assertEqual(len(self.kept("styles")), 2)

    def test_an_answer_that_lacks_a_field_is_an_error(self):
        FakeEngine.answer = {}
        with self.assertRaises(ClaudeError):
            self.director().choose([("Flat", "")])

    def test_the_open_revision_notes_still_reach_the_model(self):
        feedback = self.root / harness.FEEDBACK_DIR
        feedback.mkdir()
        (feedback / "styles.md").write_text("Make the colours quieter.", encoding="utf-8")
        self.director().choose([("Flat", "")])
        self.assertIn("Make the colours quieter.", FakeEngine.json_calls[0]["system"])


class ScriptTest(DecisionCase):
    def writer(self, **kw):
        return script.Screenwriter(self.idea, root=self.root, verbose=False, **kw)

    def test_polish_is_kept_and_served_again(self):
        self.writer().polish([])
        again = self.writer()
        again.polish([])
        self.assertEqual(len(FakeEngine.json_calls), 1)
        self.assertEqual(self.kept("script"), ["polish"])
        self.assertTrue(self.decisions(again.harness.report())["polish"]["cached"])

    def test_distinct_questions_keep_distinct_names(self):
        writer = self.writer(research="notes")
        writer.polish([])
        writer.verify([])
        self.assertEqual(self.kept("script"), ["polish", "verify"])

    def test_drafting_keeps_its_own_effort_in_one_report(self):
        writer = self.writer(draft_effort="low")
        writer._ask("write-1", "p", {"type": "object", "properties": {"shots": {}},
                                      "required": []})
        writer._ask("plan", "p", {"type": "object", "required": []})
        names = [d["name"] for d in writer.harness.report()["decisions"]]
        self.assertEqual(names, ["write-1", "plan"])
        self.assertEqual(writer.usage.calls, 2)

    def test_a_failed_check_degrades_as_before(self):
        FakeEngine.answer = {}
        self.assertEqual(self.writer(research="notes").verify([]), {})


class SoundTest(DecisionCase):
    def test_the_palette_is_kept_and_served_again(self):
        sound.SoundDesigner(self.idea, root=self.root, verbose=False).plan()
        again = sound.SoundDesigner(self.idea, root=self.root, verbose=False)
        again.plan()
        self.assertEqual(len(FakeEngine.json_calls), 1)
        self.assertEqual(self.kept("sound"), ["palette"])
        self.assertTrue(self.decisions(again.harness.report())["palette"]["cached"])

    def test_the_listen_back_verdicts_are_decisions(self):
        designer = sound.SoundDesigner(self.idea, root=self.root, verbose=False)
        cue = sound.Cue(cue_id="M1", kind="music", name="bed", at=0, seconds=5)
        stats = sound.coverage([cue], 10)
        designer.review([cue], stats)
        designer.revise([cue], {"cue": "M1", "severity": "important", "problem": "p"})
        self.assertEqual(len(self.kept("sound")), 2)
        self.assertEqual(designer.harness.report()["cost_usd"], 0.02)


class ResearchTest(DecisionCase):
    def researcher(self):
        return research.Researcher(self.idea, Dossier(self.root, self.idea, at=self.root),
                                   verbose=False)

    def test_the_questions_are_kept_under_the_project_and_served_again(self):
        first = self.researcher()
        first.questions()
        (self.root / "raw" / "01-questions.json").unlink()      # the dossier forgets it
        again = self.researcher()
        again.questions()
        self.assertEqual(len(FakeEngine.json_calls), 1)
        self.assertEqual(self.kept("research"), ["01-questions"])
        self.assertTrue(self.decisions(again.harness.report())["01-questions"]["cached"])

    def test_fresh_asks_again(self):
        self.researcher().questions()
        again = research.Researcher(self.idea, Dossier(self.root, self.idea, at=self.root),
                                    verbose=False, fresh=True)
        again.questions()
        self.assertEqual(len(FakeEngine.json_calls), 2)

    def test_the_vetting_and_the_agents_cost_are_both_counted(self):
        researcher = self.researcher()
        researcher._vet_assets([{"kind": "image", "title": "t", "source": "s",
                                 "licence": "CC0", "url": "u", "use_for": "x"}])
        self.assertEqual(self.kept("research"), ["assets-vet"])
        researcher._agent_usage.cost_usd += 0.5
        self.assertAlmostEqual(researcher.usage.cost_usd, 0.51)


class VfxPlannerTest(DecisionCase):
    def planner(self):
        bundle = {"idea": self.idea, "outline": {}, "claims": [], "assets": [], "premise": ""}
        return vfx.VfxPlanner(bundle, Dossier(self.root, self.idea, at=self.root),
                              vfx.Workspace(self.root), model="sonnet", verbose=False)

    def test_the_outline_is_kept_and_served_again(self):
        self.planner().outline()
        (self.root / "raw" / "09-outline.json").unlink()
        again = self.planner()
        again.outline()
        self.assertEqual(len(FakeEngine.json_calls), 1)
        self.assertIn("plan-outline", self.kept("vfx"))
        self.assertTrue(self.decisions(again.harness.report())["plan-outline"]["cached"])

    def test_the_planner_account_and_the_harness_are_one(self):
        planner = self.planner()
        planner.outline()
        planner._account(Usage(cost_usd=0.5))
        self.assertAlmostEqual(planner.usage.cost_usd, 0.51)


class IdeasTest(DecisionCase):
    def test_a_batch_is_recorded_and_never_served_from_cache(self):
        generator = ideas.Generator(EngineConfig(tools=()), root=self.root, verbose=False)
        generator._batch(3, ("a",), [])
        generator._batch(3, ("a",), [])
        self.assertEqual(len(FakeEngine.json_calls), 2)
        self.assertEqual(len(generator.harness.report()["decisions"]), 2)
        self.assertTrue(self.kept("ideas"))
        self.assertAlmostEqual(generator.usage.cost_usd, 0.02)

    def test_ranking_is_a_decision(self):
        generator = ideas.Generator(EngineConfig(tools=()), root=self.root, verbose=False)
        ideas.rank(generator.harness, [self.idea])
        self.assertEqual(len(FakeEngine.json_calls), 1)
        self.assertEqual(generator.harness.report()["decisions"][0]["name"], "rank")


class AssessTest(DecisionCase):
    def test_the_verdict_extraction_is_a_decision(self):
        FakeEngine.texts = ["It can be done."]
        ws = SimpleNamespace(dir=self.root / "assess", root=self.root)
        ws.dir.mkdir()
        verdict = assess.investigate(ws, model="sonnet", budget_usd=1.0, verbose=False)
        self.assertEqual(self.kept("assess"), ["verdict"])
        self.assertIn("feasible", verdict)
        self.assertEqual(verdict["engine"]["decisions"][0]["name"], "verdict")


if __name__ == "__main__":
    unittest.main()
