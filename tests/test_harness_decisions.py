"""AUTO-49: the structured judgements of styles, script, sound, research, the
vfx planner, ideas and assess go through `Harness.decide`.

So each one gets what the delivery stages already have: an answer kept under
`decisions/<stage>/`, served again while the question is unchanged, checked for
its required fields, and listed with its cost in the stage's engine report.
The model is never called: `harness.ENGINE` is a fake.
"""

import contextlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from claude_engine import ClaudeError, EngineConfig, Event, Usage
from ytstudio import assess, harness, house, ideas, research, script, sound, styles, vfx
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


class DecisionModel(DecisionCase):
    def test_each_row_names_the_model_that_answered_it(self):
        # AUTO-92: an author and its reviewer share one account and one list
        # of decisions; the report says whose answer each row was.
        author = harness.Harness(self.root, "script", model="sonnet")
        reviewer = harness.Harness(self.root, "script", model="opus", usage=author.usage,
                                   decisions=author.decisions, lock=author.lock)
        schema = {"type": "object", "properties": {"a": {"type": "string"}},
                  "required": ["a"]}
        author.decide("plan", "p", schema)
        reviewer.decide("plan-review", "r", schema)
        rows = {d["name"]: d["model"] for d in author.report()["decisions"]}
        self.assertEqual(rows, {"plan": "sonnet", "plan-review": "opus"})


class SourceTest(unittest.TestCase):
    def test_only_the_harness_and_the_sdk_kit_call_ask_json(self):
        found = sorted(p.name for p in (ROOT / "ytstudio").glob("*.py")
                       if "ask_json" in p.read_text(encoding="utf-8"))
        self.assertIn("harness.py", found)
        self.assertLessEqual(set(found), {"harness.py", "sdkkit.py"})


def shipped(stage: str) -> tuple[str, dict, str]:
    """The block and digest a stage gets from the shipped house alone, and one
    sentence of that stage's own section (AUTO-89)."""
    block, digest = house.for_args(SimpleNamespace(work=None, house=None, no_house=False),
                                   None, stage)
    own = house.load().sections[stage]
    return block, digest, re.split(r"(?<=\.)\s+", own)[0]


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


    def test_the_choice_is_asked_under_the_shipped_styles_section(self):
        block, digest, sentence = shipped("styles")
        director = styles.Director(self.idea, root=self.root, verbose=False, count=1,
                                   house=block, house_digest=digest)
        director.choose([("Flat", "flat diagrams")])
        self.assertIn(sentence, FakeEngine.json_calls[0]["prompt"])
        self.assertEqual(director.harness.report()["house"]["sources"],
                         ["ytstudio/house/default.md"])


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

    def test_the_plan_is_asked_under_the_shipped_script_section(self):
        block, digest, sentence = shipped("script")
        writer = self.writer(house=block, house_digest=digest)
        writer.plan()
        self.assertIn(sentence, FakeEngine.json_calls[0]["prompt"])
        self.assertEqual(writer.harness.report()["house"]["sources"],
                         ["ytstudio/house/default.md"])

    def test_the_stage_records_the_shipped_house_in_script_json(self):
        # The stage's own call, not a block built by the test: with no house
        # file anywhere, script.json names the shipped default and the block
        # the script stage is handed.
        from ytstudio import cli

        def answer(prompt, schema, system=None):
            FakeEngine.json_calls.append({"prompt": prompt, "schema": schema, "system": system})
            # A whole story brief (AUTO-90): the plan's story fields are
            # required, and a plan without them is asked again, then refused.
            if "sections" in schema.get("required", []):
                return {"logline": "l", "promise": "p", "payoff": "p",
                        "train": "Someone must learn whether it moves before it falls?",
                        "stakes": "s", "viewer_stake": "v", "audience": "lay",
                        # AUTO-136: the opening's trigger and gap
                        "hook": {"trigger": "question", "gap": "whether it moves"},
                        "reveal": {"section": 1, "constraint_section": 1},
                        "questions": [{"id": "Q1", "text": "Does it move?",
                                       "opened_in": 1, "closed_in": 1}],
                        "ending": {"payoff_line": "It moves.", "callback": "c",
                                   "next_film": "n"},
                        "sections": [{"heading": "One", "purpose": "p", "seconds": 10,
                                      "beats": ["b"], "opens": ["Q1"], "closes": ["Q1"],
                                      "connective": "open", "rehook": "", "escalation": "",
                                      "anchor_visual": "a drawing"}]}
            if "shots" in schema.get("required", []):
                return {"shots": [{"visual": "a drawing", "narration": "It moves.",
                                   "seconds": 4, "asset": "", "style": "",
                                   "function": "payoff", "shows": "the drawing moving"}]}
            return blank(schema)

        film, work = self.root / "a-film", self.root / "work"
        film.mkdir()
        work.mkdir()
        (film / "idea.json").write_text(json.dumps(
            {"title": "A test film", "genre": "explainer", "description": "one take"}))
        args = cli.build_parser().parse_args(
            ["script", "--project", str(film), "--work", str(work), "--quiet"])
        with mock.patch.object(FakeEngine, "ask_json", staticmethod(answer)), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            script.run(args)
        record = json.loads((film / "script.json").read_text())
        # AUTO-136: script.json carries the plan's hook and each section's
        # rehook and escalation, as the plan answered them.
        self.assertEqual(record["hook"], {"trigger": "question", "gap": "whether it moves"})
        self.assertEqual({k: record["sections"][0][k] for k in ("rehook", "escalation")},
                         {"rehook": "", "escalation": ""})
        self.assertEqual(record["house"]["sources"], ["ytstudio/house/default.md"])
        self.assertEqual(record["house"]["stage"], "script")
        self.assertEqual(record["house"], house.load(work, film).digest("script"))
        self.assertIn(shipped("script")[2], FakeEngine.json_calls[0]["prompt"])

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
