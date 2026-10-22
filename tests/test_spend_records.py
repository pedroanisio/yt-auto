"""AUTO-51: every stage records its spend and its model in one shape.

A record carries `usage` = {cost_usd, model, calls}, named once in
ytstudio/spend.py; improve reads only that (old records still read). No
network, no model, no ssh: fake engines and hand-written records.
"""

from __future__ import annotations

import inspect
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import claude_engine  # noqa: E402
from claude_engine import Usage  # noqa: E402
from ytstudio import (assess, cli, gate, harness, improve, keyframe, pilot, props,  # noqa: E402
                      qa, research, retime, review, script, spend, styles, vfx, voice)
from ytstudio.core import Idea, write_json  # noqa: E402

import test_wordless  # noqa: E402
from test_props_decision import FakeEngine as PropsEngine, Decided  # noqa: E402

KEYS = {"cost_usd", "model", "calls"}


def shaped(case, doc):
    case.assertIsInstance(doc.get("usage"), dict, "no usage in the record")
    case.assertTrue(KEYS <= set(doc["usage"]), doc["usage"])
    case.assertIsInstance(doc["usage"]["cost_usd"], (int, float))
    case.assertIsInstance(doc["usage"]["model"], str)
    case.assertIsInstance(doc["usage"]["calls"], int)
    return doc["usage"]


class Helper(unittest.TestCase):
    def test_a_record_has_the_three_keys(self):
        self.assertEqual(spend.record(0.123456, "sonnet", 3),
                         {"cost_usd": 0.1235, "model": "sonnet", "calls": 3})
        self.assertEqual(spend.record(None, None, None),
                         {"cost_usd": 0.0, "model": "", "calls": 0})

    def test_from_a_harness_report(self):
        got = spend.from_report({"model": "opus", "cost_usd": 1.5, "calls": 4,
                                 "by_model": {"opus": {"cost_usd": 1.5}}})
        self.assertEqual((got["cost_usd"], got["model"], got["calls"]), (1.5, "opus", 4))

    def test_from_an_engine_usage_keeps_its_counters(self):
        u = Usage(cost_usd=2.0, calls=5, input_tokens=9)
        got = spend.from_usage(u, "sonnet")
        self.assertEqual((got["cost_usd"], got["model"], got["calls"]), (2.0, "sonnet", 5))
        self.assertEqual(got["input_tokens"], 9)
        Usage(**{k: v for k, v in got.items() if k != "model"})     # still a Usage

    def test_stamp_fills_a_usage_dict_without_losing_it(self):
        out = spend.stamp({"usage": {"cost_usd": 3.0, "calls": 2, "input_tokens": 7}}, "haiku")
        self.assertEqual(out["usage"]["model"], "haiku")
        self.assertEqual(out["usage"]["input_tokens"], 7)
        self.assertEqual(spend.stamp({}, "x")["usage"], spend.record(0, "x", 0))

    def test_cost_in_reads_the_new_shape_and_every_old_one(self):
        self.assertEqual(spend.cost_in({"usage": spend.record(1.25, "m", 1)}), 1.25)
        self.assertEqual(spend.cost_in({"usage": {"cost_usd": 2.0}}), 2.0)
        self.assertEqual(spend.cost_in({"cost_usd": 3.0}), 3.0)
        self.assertEqual(spend.cost_in({"engine": {"cost_usd": 4.0}}), 4.0)
        self.assertEqual(spend.cost_in({"engine": "kokoro"}), None)
        self.assertEqual(spend.cost_in({"usage": {"cost_usd": 1.0}, "synth": {"cost_usd": 0.5}}),
                         1.5)
        self.assertIsNone(spend.cost_in({}))
        self.assertIsNone(spend.cost_in(None))


class Prices(unittest.TestCase):
    """AUTO-73: every model has a price, so a call cut off by its timeout is
    billed, not booked at $0. The table is spend's; the engine reads a copy."""

    CURRENT = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5",
               "claude-fable-5-1", "claude-opus-5", "claude-sonnet-5")

    def test_the_table_prices_the_models_the_cli_reports(self):
        for model in self.CURRENT:
            row = spend.PRICES[model]
            self.assertEqual(set(row), {"input", "cached_input", "output", "cache_creation"},
                             model)
            self.assertTrue(all(v > 0 for v in row.values()), model)
            self.assertLess(row["cached_input"], row["input"], model)
            self.assertLess(row["input"], row["output"], model)
        self.assertEqual(spend.PRICES["claude-sonnet-5-5"]["input"], 2.0)
        self.assertEqual(spend.PRICES["claude-sonnet-5-5"]["output"], 10.0)
        self.assertEqual(spend.PRICES["claude-opus-5-5"]["output"], 20.0)

    def test_an_alias_prices_as_the_generation_the_cli_resolves_it_to(self):
        self.assertEqual(set(spend.ALIASES), {"opus", "sonnet", "haiku"})
        for alias, model in spend.ALIASES.items():
            self.assertIn(model, self.CURRENT)
            self.assertEqual(spend.PRICES[alias], spend.PRICES[model])

    def test_the_table_is_installed_into_the_engine_when_harness_is_imported(self):
        # ytstudio.harness is imported at the top of this module.
        for model, row in spend.PRICES.items():
            self.assertEqual(claude_engine.PRICES.get(model), row, model)
        self.assertAlmostEqual(
            claude_engine._partial_cost({"output_tokens": 1_000_000}, "claude-sonnet-5-5"),
            10.0)

    def test_the_environment_still_overrides_and_an_unknown_model_has_none(self):
        override = {"claude-sonnet-5-5": {"input": 0, "cached_input": 0, "output": 1_000_000}}
        with mock.patch.dict(os.environ, {"CLAUDE_PRICES": json.dumps(override)}):
            self.assertEqual(spend.price_for("claude-sonnet-5-5")["output"], 1_000_000)
            self.assertAlmostEqual(
                claude_engine._partial_cost({"output_tokens": 1}, "claude-sonnet-5-5"), 1.0)
        self.assertEqual(spend.price_for("claude-sonnet-5-5"), spend.PRICES["claude-sonnet-5-5"])
        self.assertIsNone(spend.price_for("a-model-nobody-priced"))
        self.assertIsNone(spend.price_for(""))


SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}},
          "required": ["answer"]}


class FailedDecision(unittest.TestCase):
    """AUTO-73: a decision that raised still cost money. It is a `failed` row
    in the stage's report, so the record bills it and `yt improve` sizes the
    next pass from what was really spent."""

    class Down:
        """Spends, then the CLI fails."""

        def __init__(self, config):
            self.usage = Usage(cost_usd=0.3, calls=1)

        def ask_json(self, prompt, schema, system=None):
            raise harness.ClaudeError("the model is down")

    class Wordless(Down):
        """Spends twice, and never gives the required field."""

        def ask_json(self, prompt, schema, system=None):
            return {"nothing": True}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._engine = harness.ENGINE
        self.addCleanup(setattr, harness, "ENGINE", self._engine)

    def failing(self, engine):
        harness.ENGINE = engine
        h = harness.Harness(self.root, "build", model="sonnet", verbose=False)
        with self.assertRaises(harness.HarnessError):
            h.decide("edit", "p", SCHEMA)
        return h.report()

    def test_an_engine_error_is_a_failed_row_that_the_record_bills(self):
        report = self.failing(self.Down)
        self.assertEqual([(d["name"], d["kind"], d["cached"]) for d in report["decisions"]],
                         [("edit", "failed", False)])
        self.assertAlmostEqual(report["decisions"][0]["cost_usd"], 0.3)
        self.assertAlmostEqual(report["cost_usd"], 0.3)
        self.assertAlmostEqual(spend.from_report(report)["cost_usd"], 0.3)
        self.assertFalse((self.root / "decisions").exists())      # a failure is never served

    def test_an_answer_off_its_schema_twice_is_a_failed_row_too(self):
        report = self.failing(self.Wordless)
        self.assertEqual([d["kind"] for d in report["decisions"]], ["failed"])
        self.assertAlmostEqual(report["cost_usd"], 0.3)


class Writers(unittest.TestCase):
    def test_styles_show_their_spend(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "styles.json"
        idea = Idea(title="T", genre="g", description="d")
        styles.write_json(str(path), idea, {}, [], usage=spend.record(0.75, "sonnet", 2))
        self.assertEqual(shaped(self, json.loads(path.read_text()))["cost_usd"], 0.75)

    def test_props_show_their_spend_when_the_model_chose(self):
        class Charging(PropsEngine):
            def ask_json(self, prompt, schema, system=None):
                self.usage.cost_usd += 0.25
                self.usage.calls += 1
                return super().ask_json(prompt, schema, system)

        class Case(Decided):
            def runTest(self):
                harness.ENGINE = Charging
                PropsEngine.phrases, PropsEngine.keep_titles = ["vintage"], []
                PropsEngine.calls = []
                assert self.run_stage() == 0
                self.got = self.manifest()
        case = Case()
        case.setUp()
        try:
            case.runTest()
        finally:
            case.tearDown()
        usage = shaped(self, case.got)
        self.assertEqual(usage["calls"], 2)
        self.assertAlmostEqual(usage["cost_usd"], 0.5)

    def test_voice_wordless_shows_its_spend(self):
        class Charging(test_wordless.FakeEngine):
            def ask_json(self, prompt, schema, system=None):
                self.usage.cost_usd += 0.4
                self.usage.calls += 1
                return super().ask_json(prompt, schema, system)

        class Case(test_wordless.WordlessRun):
            def runTest(self):
                harness.ENGINE = Charging
                assert voice.run(self.args()) == 0
                self.got = json.loads((self.root / "voice.json").read_text(encoding="utf-8"))
        case = Case()
        case.setUp()
        try:
            case.runTest()
        finally:
            case.tearDown()
        usage = shaped(self, case.got)
        self.assertAlmostEqual(usage["cost_usd"], 0.4)
        self.assertEqual(case.got["engine"], "none")        # still the TTS engine's name

    def test_every_record_writer_goes_through_the_one_helper(self):
        """The writers that need a model, an ssh host or a render to run."""
        writers = [(retime.run, "retime"), (review.run, "review"), (gate.run, "gate"),
                   (qa.run, "qa"), (pilot.run, "pilot"), (research.write_dossier, "research"),
                   (script.Screenwriter, "script"), (voice.run, "voice"),
                   (voice.run_wordless, "voice wordless"), (styles.run, "styles"),
                   (props.run, "props"), (assess.write_report, "assess")]
        for fn, name in writers:
            self.assertIn("spend.", inspect.getsource(fn), f"{name} writes no shared usage")
        self.assertIn("spend.", inspect.getsource(vfx))
        self.assertIn("spend.", inspect.getsource(keyframe))


class Improve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_a_pass_bills_styles_voice_and_props(self):
        want = {"styles": 0.5, "voice": 0.25, "props": 0.125}
        for stage, usd in want.items():
            write_json(self.root / improve.MANIFEST[stage],
                       {"usage": spend.record(usd, "sonnet", 1)})
        for stage, usd in want.items():
            self.assertEqual(improve.stage_cost(self.root, stage, {}), usd, stage)

    def test_old_records_still_bill(self):
        write_json(self.root / "retime.json", {"engine": {"cost_usd": 0.7}})
        write_json(self.root / "dossier.json", {"usage": {"cost_usd": 1.1}})
        write_json(self.root / "keyframe.json", {"cost_usd": 0.3})
        self.assertEqual(improve.stage_cost(self.root, "retime", {}), 0.7)
        self.assertEqual(improve.stage_cost(self.root, "research", {}), 1.1)
        self.assertEqual(improve.stage_cost(self.root, "keyframe", {}), 0.3)

    def test_the_estimate_reads_old_and_new_records(self):
        write_json(self.root / "styles.json", {"usage": spend.record(0.9, "m", 1)})
        write_json(self.root / "voice.json", {"engine": "kokoro", "cost_usd": 0.2})
        rows = {r["stage"]: r for r in improve.stage_estimates(self.root, ["styles", "voice"])}
        self.assertEqual(rows["styles"]["usd"], 0.9)
        self.assertEqual(rows["voice"]["usd"], 0.2)

    def test_the_qa_cost_reads_the_scorecard_usage(self):
        write_json(self.root / "qa.json", {"pass": "pass-01"})
        (self.root / "qa" / "pass-01").mkdir(parents=True)
        write_json(self.root / "qa" / "pass-01" / "scorecard.json",
                   {"usage": spend.record(0.6, "m", 1), "engine": {"cost_usd": 0.6}})
        self.assertEqual(improve._qa_cost(self.root), 0.6)


if __name__ == "__main__":
    unittest.main()
