"""AUTO-23: a budget on Codex either holds or the run refuses to start."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import codex_engine                                    # noqa: E402
from claude_engine import ClaudeExecutionError         # noqa: E402
from test_codex_engine import CodexCase, ok_lines      # noqa: E402

UNPRICED = "chatgpt-plan-default"      # a model id with no row in PRICES
BIG = {"input_tokens": 9000, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
       "output_tokens": 1000, "reasoning_output_tokens": 0}


class UnpricedBudget(CodexCase):
    def test_a_dollar_budget_on_an_unpriced_model_refuses_to_start(self):
        self.fake.plan([{"lines": ok_lines()}])
        eng = self.engine(model=UNPRICED, max_turns_budget_usd=1.0)
        with self.assertRaises(codex_engine.CodexBudgetRefused) as caught:
            eng.ask("x")
        self.assertIn("CODEX_PRICES", str(caught.exception))
        self.assertIn("CODEX_BUDGET_TOKENS", str(caught.exception))
        self.assertEqual(self.fake.calls(), [], "codex must not be launched")

    def test_stream_refuses_too_and_a_refusal_is_not_transient(self):
        self.fake.plan([{"lines": ok_lines()}])
        eng = self.engine(model=UNPRICED, max_turns_budget_usd=1.0, max_retries=2)
        with self.assertRaises(ClaudeExecutionError) as caught:
            list(eng.stream("x"))
        self.assertFalse(caught.exception.transient)
        self.assertEqual(self.fake.calls(), [])

    def test_a_token_cap_lets_an_unpriced_model_run_under_it(self):
        eng = self.engine(model=UNPRICED, max_turns_budget_usd=1.0,
                          max_budget_tokens=20000)
        _, answer, _ = self.run_ok(eng)
        self.assertEqual(answer.text, "ok")

    def test_a_token_cap_stops_a_call_that_passes_it(self):
        self.fake.plan([{"lines": ok_lines(usage=BIG)}])
        eng = self.engine(model=UNPRICED, max_turns_budget_usd=1.0,
                          max_budget_tokens=5000, max_retries=2)
        with self.assertRaises(ClaudeExecutionError) as caught:
            eng.ask("x")
        self.assertIn("error_max_budget_tokens", str(caught.exception))
        self.assertEqual(len(self.fake.calls()), 1, "a budget stop is never retried")

    def test_the_cap_can_come_from_the_environment(self):
        self.fake.plan([{"lines": ok_lines(usage=BIG)}])
        eng = self.engine(model=UNPRICED, max_turns_budget_usd=1.0,
                          env={"CODEX_BUDGET_TOKENS": "5000"})
        with self.assertRaises(ClaudeExecutionError) as caught:
            eng.ask("x")
        self.assertIn("error_max_budget_tokens", str(caught.exception))

    def test_a_priced_model_keeps_its_dollar_budget_and_no_budget_runs_free(self):
        _, answer, _ = self.run_ok(self.engine(model="gpt-5", max_turns_budget_usd=5.0))
        self.assertEqual(answer.text, "ok")
        _, answer, _ = self.run_ok(self.engine(model=UNPRICED))
        self.assertEqual(answer.text, "ok")

    def test_configured_prices_make_the_model_budgetable(self):
        prices = {UNPRICED: {"input": 1.0, "output": 2.0}}
        _, answer, _ = self.run_ok(self.engine(model=UNPRICED, prices=prices,
                                               max_turns_budget_usd=1.0))
        self.assertEqual(answer.text, "ok")


if __name__ == "__main__":
    unittest.main()
