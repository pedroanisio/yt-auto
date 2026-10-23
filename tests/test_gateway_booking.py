"""What a call through Vercel AI Gateway is booked at (AUTO-121).

The claude CLI's `total_cost_usd` and `modelUsage[...].costUSD` are its own
estimate at Anthropic's rates, not the gateway's bill. Under
`YT_GATEWAY=vercel` a completed call is booked at the price row times the
tokens its `modelUsage` reports, the CLI's figure kept beside it as
`cli_cost_usd`; a model with no row, or a row with no tokens, is booked at
the CLI's figure and counted (`cost_cli_calls`) for `yt doctor`. With the
switch unset the booking is exactly what it was.

A gateway budget's 402 is permanent (a retry is refused the same way and
the request that crossed the cap was still paid); its 429 stays transient.

No model, no network: a fake `claude` prints the result a test hands it.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import claude_engine  # noqa: E402
import codex_engine  # noqa: E402
from claude_engine import ClaudeEngine, ClaudeExecutionError, EngineConfig, Usage  # noqa: E402
from ytstudio import doctor, harness, sdkkit, spend, workspace  # noqa: E402
from ytstudio.core import write_json  # noqa: E402

KEY = "vck_test_secret_0123456789"

#: Every variable the gateway reads or writes, cleared from the parent env.
GATEWAY_VARS = ("YT_GATEWAY", "YT_GATEWAY_AUTH", "YT_GATEWAY_URL", "AI_GATEWAY_API_KEY",
                "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
                "ANTHROPIC_CUSTOM_HEADERS", "CLAUDE_CODE_USE_BEDROCK",
                "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_PRICES")

#: A stand-in for `claude -p`: counts its launches, then prints FAKE_RESULT
#: (or fails with FAKE_STDERR and exit 1), exiting FAKE_EXIT (0 by default).
FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:]}) + "\n")
sys.stdin.read()
if os.environ.get("FAKE_STDERR"):
    sys.stderr.write(os.environ["FAKE_STDERR"])
    sys.exit(1)
result = json.loads(os.environ["FAKE_RESULT"])
if "stream-json" in sys.argv:
    print(json.dumps({"type": "system", "subtype": "init", "session_id": "s-fake"}))
print(json.dumps(result))
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
'''

MODEL = "claude-opus-5-5"
#: USD per 1M tokens, set for the test so the expected figure is exact.
ROW = {"input": 4.0, "cached_input": 0.20, "output": 20.0, "cache_creation": 5.0}
TOKENS = {"inputTokens": 1000, "outputTokens": 2000, "cacheReadInputTokens": 3000,
          "cacheCreationInputTokens": 400}
#: 1000*4 + 2000*20 + 3000*0.20 + 400*5, per million.
TABLE_USD = 0.0466
CLI_USD = 0.5

#: The by_model row a direct call has always written.
DIRECT_ROW_KEYS = {"calls", "input_tokens", "output_tokens", "cache_read_tokens",
                   "cache_creation_tokens", "cost_usd"}
#: Usage's fields before this card; a direct record's `usage` keeps exactly these.
USAGE_FIELDS = {"input_tokens", "output_tokens", "cache_read_tokens",
                "cache_creation_tokens", "cost_usd", "calls", "unmetered_calls",
                "duration_ms", "by_model", "timed_out_calls", "timed_out_tokens",
                "timed_out_cost_usd", "timed_out_cost_estimated"}


def result(model_usage: dict | None = None, total: float = CLI_USD) -> dict:
    """A successful result the CLI would print, its cost the CLI's own."""
    out = {"type": "result", "subtype": "success", "is_error": False, "result": "ok",
           "session_id": "s-fake", "num_turns": 1, "total_cost_usd": total,
           "usage": {"input_tokens": 1000, "output_tokens": 2000,
                     "cache_read_input_tokens": 3000, "cache_creation_input_tokens": 400},
           "duration_ms": 1}
    if model_usage is None:
        model_usage = {MODEL: {**TOKENS, "costUSD": total}}
    if model_usage:
        out["modelUsage"] = model_usage
    return out


def on(**extra: str) -> dict[str, str]:
    return {"YT_GATEWAY": "vercel", "AI_GATEWAY_API_KEY": KEY, **extra}


class Case(unittest.TestCase):
    def setUp(self):
        claude_engine.DEFAULTS.clear()
        self._saved = {k: os.environ.pop(k) for k in list(os.environ)
                       if k in GATEWAY_VARS or k.startswith("CLAUDE_ENGINE_")}
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.exe = root / "claude"
        self.exe.write_text(FAKE_CLAUDE)
        self.exe.chmod(self.exe.stat().st_mode | stat.S_IEXEC)
        self.log = root / "launches.log"
        self._patches = [mock.patch.dict(os.environ, {"FAKE_LOG": str(self.log)}),
                         mock.patch.dict(claude_engine.PRICES, {MODEL: dict(ROW)})]
        for p in self._patches:
            p.start()
        self.give(result())

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()
        os.environ.update(self._saved)
        claude_engine.DEFAULTS.clear()

    def give(self, payload: dict) -> None:
        os.environ["FAKE_RESULT"] = json.dumps(payload)

    def engine(self, **kw) -> ClaudeEngine:
        kw.setdefault("max_retries", 0)
        kw.setdefault("retry_backoff", 0.0)
        kw.setdefault("timeout", 10.0)
        return ClaudeEngine(EngineConfig(executable=str(self.exe), **kw))

    def launches(self) -> int:
        return len(self.log.read_text().splitlines()) if self.log.exists() else 0


class Booking(Case):
    def test_gateway_booking_uses_table(self):
        with mock.patch.dict(os.environ, on()):
            eng = self.engine()
            answer = eng.ask("hi")
        self.assertAlmostEqual(eng.usage.cost_usd, TABLE_USD, places=10)
        self.assertAlmostEqual(answer.usage.cost_usd, TABLE_USD, places=10)
        row = eng.usage.by_model[MODEL]
        self.assertAlmostEqual(row["cost_usd"], TABLE_USD, places=10)
        self.assertEqual(row["cli_cost_usd"], CLI_USD)
        self.assertEqual(row["cost_table_calls"], 1)
        self.assertNotIn("cost_cli_calls", row)
        # The call's own result says what was booked and what the CLI said.
        self.assertAlmostEqual(answer.raw["total_cost_usd"], TABLE_USD, places=10)
        self.assertEqual(answer.raw["cli_cost_usd"], CLI_USD)
        self.assertEqual(answer.raw["cost_source"], "table")

    def test_a_gateway_id_takes_its_direct_names_row(self):
        # AUTO-120's lookup: anthropic/claude-opus-5.5 is claude-opus-5-5.
        self.give(result({"anthropic/claude-opus-5.5": {**TOKENS, "costUSD": CLI_USD}}))
        with mock.patch.dict(os.environ, on()):
            eng = self.engine()
            eng.ask("hi")
        self.assertAlmostEqual(eng.usage.cost_usd, TABLE_USD, places=10)
        self.assertEqual(eng.usage.by_model["anthropic/claude-opus-5.5"]["cost_table_calls"], 1)

    def test_subscription_mode_is_the_gateway_too(self):
        with mock.patch.dict(os.environ, on(YT_GATEWAY_AUTH="subscription")):
            eng = self.engine()
            eng.ask("hi")
        self.assertAlmostEqual(eng.usage.cost_usd, TABLE_USD, places=10)

    def test_the_stream_path_books_the_table_figure(self):
        with mock.patch.dict(os.environ, on()):
            eng = self.engine()
            events = list(eng.stream("hi"))
        self.assertAlmostEqual(events[-1].raw["total_cost_usd"], TABLE_USD, places=10)
        self.assertAlmostEqual(eng.usage.cost_usd, TABLE_USD, places=10)
        self.assertEqual(eng.usage.by_model[MODEL]["cli_cost_usd"], CLI_USD)

    def test_gateway_booking_falls_back_and_flags_unpriced(self):
        # No row: a 1M-token context, which neither route prices.
        long = MODEL + "[1m]"
        self.give(result({long: {**TOKENS, "costUSD": 0.3},
                          MODEL: {**TOKENS, "costUSD": 0.2}}))
        with mock.patch.dict(os.environ, on()):
            eng = self.engine()
            answer = eng.ask("hi")
        unpriced, priced = eng.usage.by_model[long], eng.usage.by_model[MODEL]
        self.assertEqual(unpriced["cost_usd"], 0.3)
        self.assertEqual(unpriced["cli_cost_usd"], 0.3)
        self.assertEqual(unpriced["cost_cli_calls"], 1)
        self.assertNotIn("cost_table_calls", unpriced)
        self.assertEqual(priced["cost_table_calls"], 1)
        self.assertAlmostEqual(eng.usage.cost_usd, 0.3 + TABLE_USD, places=10)
        self.assertEqual(answer.raw["cost_source"], "cli")
        self.assertEqual(answer.raw["cli_cost_usd"], CLI_USD)

    def test_a_row_with_no_tokens_falls_back(self):
        self.give(result({MODEL: {"costUSD": CLI_USD}}))
        with mock.patch.dict(os.environ, on()):
            eng = self.engine()
            eng.ask("hi")
        self.assertEqual(eng.usage.cost_usd, CLI_USD)
        self.assertEqual(eng.usage.by_model[MODEL]["cost_cli_calls"], 1)

    def test_no_model_usage_books_the_cli_figure(self):
        self.give(result({}))
        with mock.patch.dict(os.environ, on()):
            eng = self.engine()
            answer = eng.ask("hi")
        self.assertEqual(eng.usage.cost_usd, CLI_USD)
        self.assertEqual(answer.raw["cost_source"], "cli")

    def test_web_searches_are_booked_per_search(self):
        # The CLI's costUSD carries its server-side web searches too, at
        # Anthropic's $10 per 1000; the table's tokens alone would leave
        # them out of the ledger (the research stage searches).
        self.give(result({MODEL: {**TOKENS, "webSearchRequests": 15, "costUSD": CLI_USD}}))
        with mock.patch.dict(os.environ, on()):
            eng = self.engine()
            answer = eng.ask("hi")
        want = TABLE_USD + 15 * claude_engine.WEB_SEARCH_USD
        self.assertEqual(claude_engine.WEB_SEARCH_USD, 0.01)
        self.assertAlmostEqual(eng.usage.cost_usd, want, places=10)
        self.assertEqual(eng.usage.by_model[MODEL]["cost_table_calls"], 1)
        self.assertEqual(answer.raw["cost_source"], "table")

    def test_a_server_tool_the_table_does_not_price_falls_back(self):
        # Any other per-request counter is a charge the row cannot see:
        # the CLI's figure is booked and doctor told (cost_cli_calls).
        self.give(result({MODEL: {**TOKENS, "codeExecutionRequests": 2, "costUSD": CLI_USD}}))
        with mock.patch.dict(os.environ, on()):
            eng = self.engine()
            answer = eng.ask("hi")
        self.assertEqual(eng.usage.cost_usd, CLI_USD)
        self.assertEqual(eng.usage.by_model[MODEL]["cost_cli_calls"], 1)
        self.assertEqual(answer.raw["cost_source"], "cli")

    def test_a_zero_counter_is_no_charge(self):
        self.give(result({MODEL: {**TOKENS, "webSearchRequests": 0,
                                  "codeExecutionRequests": 0, "costUSD": CLI_USD}}))
        with mock.patch.dict(os.environ, on()):
            eng = self.engine()
            eng.ask("hi")
        self.assertAlmostEqual(eng.usage.cost_usd, TABLE_USD, places=10)
        self.assertEqual(eng.usage.by_model[MODEL]["cost_table_calls"], 1)

    def test_a_prompt_above_a_models_tier_falls_back(self):
        # Haiku 5.5 bills 5x above 100K prompt tokens, which its row does
        # not model: such a call is booked at the CLI's figure, flagged.
        haiku = {"input": 0.10, "cached_input": 0.01, "output": 0.50, "cache_creation": 0.125}
        cases = (("claude-haiku-5-5", {"inputTokens": 200000, "outputTokens": 1000}, "cli"),
                 ("anthropic/claude-haiku-5.5",
                  {"inputTokens": 60000, "cacheReadInputTokens": 50000}, "cli"),
                 ("claude-haiku-5-5", {"inputTokens": 50000, "cacheReadInputTokens": 30000,
                                       "cacheCreationInputTokens": 10000,
                                       "outputTokens": 90000}, "table"))
        for model, tokens, source in cases:
            with self.subTest(model=model, tokens=tokens), \
                    mock.patch.dict(claude_engine.PRICES, {"claude-haiku-5-5": haiku}), \
                    mock.patch.dict(os.environ, on()):
                self.give(result({model: {**tokens, "costUSD": 0.1}}, total=0.1))
                eng = self.engine()
                answer = eng.ask("hi")
                self.assertEqual(answer.raw["cost_source"], source)
                self.assertEqual(eng.usage.by_model[model][f"cost_{source}_calls"], 1)
                if source == "cli":
                    self.assertEqual(eng.usage.cost_usd, 0.1)
                else:
                    self.assertNotEqual(eng.usage.cost_usd, 0.1)

    def test_direct_booking_unchanged(self):
        for env in ({}, {"YT_GATEWAY": "off", "AI_GATEWAY_API_KEY": KEY}):
            with self.subTest(env=env), mock.patch.dict(os.environ, env):
                eng = self.engine()
                answer = eng.ask("hi")
                self.assertEqual(eng.usage.cost_usd, CLI_USD)
                self.assertEqual(answer.usage.cost_usd, CLI_USD)
                self.assertEqual(eng.usage.by_model[MODEL], {
                    "calls": 1, "input_tokens": 1000, "output_tokens": 2000,
                    "cache_read_tokens": 3000, "cache_creation_tokens": 400,
                    "cost_usd": CLI_USD})
                self.assertEqual(answer.raw, result())

    def test_ledger_by_model_keys_stable(self):
        eng = self.engine()
        eng.ask("hi")
        self.assertEqual(set(eng.usage.by_model[MODEL]), DIRECT_ROW_KEYS)
        self.assertEqual({f.name for f in fields(Usage)}, USAGE_FIELDS)
        self.assertEqual(set(spend.from_usage(eng.usage, MODEL)), USAGE_FIELDS | {"model"})
        # A gateway row merges with a direct one: the counters add, the
        # direct keys keep their meaning.
        with mock.patch.dict(os.environ, on()):
            routed = self.engine()
            routed.ask("hi")
        total = Usage()
        total.merge(eng.usage)
        total.merge(routed.usage)
        row = total.by_model[MODEL]
        self.assertEqual(row["calls"], 2)
        self.assertAlmostEqual(row["cost_usd"], CLI_USD + TABLE_USD, places=10)
        self.assertEqual(row["cli_cost_usd"], CLI_USD)
        self.assertEqual(row["cost_table_calls"], 1)

    def test_codex_under_the_switch_keeps_its_own_booking(self):
        # Codex already books from its price table; the claude CLI's
        # repricing is not applied to its turns. A launch under the switch
        # decides it, so the launch's env is built first, over a flag a
        # previous claude launch would have left on.
        eng = codex_engine.CodexEngine(codex_engine.CodexConfig())
        eng._gateway_booked = True
        payload = {"type": "result", "total_cost_usd": 0.01, "usage": {},
                   "modelUsage": {"openai/gpt-5": {"inputTokens": 5, "costUSD": 0.01}}}
        with mock.patch.dict(os.environ, on()):
            eng._popen_kwargs()
            eng._absorb_result(payload)
        self.assertFalse(eng._gateway_booked)
        self.assertEqual(eng.usage.cost_usd, 0.01)
        self.assertNotIn("cli_cost_usd", eng.usage.by_model["openai/gpt-5"])
        self.assertNotIn("cli_cost_usd", payload)

    def test_the_gateway_engine_keeps_its_own_booking(self):
        # The AI SDK program prices its own calls (COST_SOURCES); a call it
        # makes under the switch is not repriced as a claude CLI call.
        from gateway_fakes import FakeNode
        import gateway_engine
        fake = FakeNode(self)
        with mock.patch.dict(os.environ, {"YT_GATEWAY": "vercel"}):
            eng = fake.engine()
            answer = eng.ask("hi")
        self.assertEqual(answer.raw["cost_source"], "gateway")
        self.assertNotIn("cli_cost_usd", answer.raw)
        row = eng.usage.by_model[answer.raw["model"]]
        self.assertEqual(row["cost_gateway_calls"], 1)
        self.assertNotIn("cli_cost_usd", row)
        self.assertNotIn("cost_table_calls", row)
        self.assertIsInstance(eng, gateway_engine.GatewayEngine)


class Budget(Case):
    def test_the_stream_meter_counts_the_table_figure(self):
        meter = sdkkit._Meter(10.0, None)
        with mock.patch.dict(os.environ, on()):
            for event in self.engine().stream("hi"):
                meter.note(event)
        self.assertAlmostEqual(meter.done, TABLE_USD, places=10)

    def test_the_stream_meter_counts_the_cli_figure_directly(self):
        meter = sdkkit._Meter(10.0, None)
        for event in self.engine().stream("hi"):
            meter.note(event)
        self.assertEqual(meter.done, CLI_USD)

    def test_the_cli_budget_stays_a_backstop(self):
        with mock.patch.dict(os.environ, on()):
            argv = self.engine(max_turns_budget_usd=2.0)._build_argv(
                output_format="json", resume=False, tools=..., system=None, schema=None)
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "2.0")


#: How the gateway words a budget's 402, by scope.
KEY_402 = ('402 {"error":{"type":"quota_for_entity_exceeded","message":'
           '"Quota limit exceeded for \\"api_key_id_abc123\\""}}')
PROJECT_402 = "API Error: 402 Project budget exceeded. Current spend: $10.02"
TEAM_402 = "Team budget exceeded. Current spend: $50.00"
RATE_429 = ('API Error: 429 {"error":{"type":"rate_limit_exceeded",'
            '"message":"Too many requests"}} retry-after: 3')


class Classification(Case):
    def classify(self, text: str) -> tuple[bool, bool]:
        return claude_engine.is_transient(text), codex_engine.is_transient(text)

    def test_402_key_budget_message_is_permanent(self):
        self.assertEqual(self.classify(KEY_402), (False, False))
        self.assertEqual(self.classify('Quota limit exceeded for "api_key_id_x"'),
                         (False, False))
        self.assertEqual(self.classify("quota_for_entity_exceeded"), (False, False))

    def test_402_project_budget_message_is_permanent(self):
        self.assertEqual(self.classify(PROJECT_402), (False, False))
        self.assertEqual(self.classify(TEAM_402), (False, False))

    def test_429_is_transient(self):
        self.assertEqual(self.classify(RATE_429), (True, True))
        self.assertEqual(self.classify("429 Too Many Requests"), (True, True))

    def test_no_broad_word_is_a_marker(self):
        # 'credits' and 'insufficient' could match unrelated failure text.
        for marker in claude_engine.PERMANENT_MARKERS:
            self.assertNotIn("credit", marker)
            self.assertNotIn("insufficient", marker)

    def test_a_key_budget_402_ends_the_call_at_the_first_attempt(self):
        os.environ["FAKE_STDERR"] = KEY_402
        try:
            with mock.patch.dict(os.environ, on()):
                with self.assertRaises(ClaudeExecutionError) as caught:
                    self.engine(max_retries=3).ask("hi")
        finally:
            del os.environ["FAKE_STDERR"]
        self.assertFalse(caught.exception.transient)
        self.assertIn("quota_for_entity_exceeded", caught.exception.stderr)
        self.assertEqual(self.launches(), 1)

    def test_a_key_budget_402_in_the_result_ends_the_call_at_the_first_attempt(self):
        # The CLI more often reports an API error as an is_error result on
        # stdout, exiting 1, than on stderr.
        self.give({"type": "result", "subtype": "success", "is_error": True,
                   "result": f"API Error: {KEY_402}", "session_id": "s-fake"})
        os.environ["FAKE_EXIT"] = "1"
        try:
            with mock.patch.dict(os.environ, on()):
                with self.assertRaises(ClaudeExecutionError) as caught:
                    self.engine(max_retries=3).ask("hi")
        finally:
            del os.environ["FAKE_EXIT"]
        self.assertFalse(caught.exception.transient)
        self.assertIn("quota_for_entity_exceeded", caught.exception.payload.get("result", ""))
        self.assertEqual(self.launches(), 1)

    def test_a_429_in_the_result_is_tried_again(self):
        self.give({"type": "result", "subtype": "success", "is_error": True,
                   "result": RATE_429, "session_id": "s-fake"})
        os.environ["FAKE_EXIT"] = "1"
        try:
            with mock.patch.dict(os.environ, on()):
                with self.assertRaises(ClaudeExecutionError) as caught:
                    self.engine(max_retries=1).ask("hi")
        finally:
            del os.environ["FAKE_EXIT"]
        self.assertTrue(caught.exception.transient)
        self.assertEqual(self.launches(), 2)

    def test_a_429_is_tried_again(self):
        os.environ["FAKE_STDERR"] = RATE_429
        try:
            with mock.patch.dict(os.environ, on()):
                with self.assertRaises(ClaudeExecutionError) as caught:
                    self.engine(max_retries=1).ask("hi")
        finally:
            del os.environ["FAKE_STDERR"]
        self.assertTrue(caught.exception.transient)
        self.assertEqual(self.launches(), 2)


class Doctor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        write_json(self.root / "idea.json", {"title": "T", "genre": "g", "description": "d"})

    def findings(self, row: dict) -> list:
        write_json(self.root / "script.json", {"usage": spend.record(
            0.5, MODEL, 1, by_model={MODEL: {"calls": 1, "cost_usd": 0.5, **row}})})
        report = doctor.check_project(workspace.Project(self.root))
        return [f for f in report.findings if f.area == "spend"]

    def test_a_table_booked_row_is_quiet(self):
        self.assertEqual(self.findings({"cli_cost_usd": 0.6, "cost_table_calls": 1}), [])

    def test_a_cli_booked_row_is_reported(self):
        (warned,) = self.findings({"cli_cost_usd": 0.5, "cost_cli_calls": 1})
        self.assertEqual(warned.level, "warn")
        self.assertIn(MODEL, warned.message)
        self.assertIn("CLI", warned.message)


class Docs(unittest.TestCase):
    def test_the_readme_and_the_skill_name_the_booking(self):
        for path in (ROOT / "README.md", ROOT / "ytstudio" / "SKILL.md"):
            text = path.read_text(encoding="utf-8")
            for word in ("cli_cost_usd", "cost_cli_calls", "quota_for_entity_exceeded"):
                with self.subTest(path=path.name, word=word):
                    self.assertIn(word, text)


if __name__ == "__main__":
    unittest.main()
