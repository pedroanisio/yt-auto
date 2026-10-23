"""gateway_engine.py: the opt-in AI SDK engine behind Vercel AI Gateway (AUTO-124).

The engine runs `node gateway/gateway-engine.mjs` with one JSON request on
stdin and reads one JSON line back. What these tests pin is the Python side
of that contract: the preconditions that refuse a call before any process
starts, the request the program is handed, how an answer and a failure are
booked, which failures are retried, and that the key never reaches a
message. A fake `node` (tests/gateway_fakes.py) answers; no test uses the
network or a model.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import claude_engine  # noqa: E402
import gateway_engine  # noqa: E402
from claude_engine import ClaudeEngine, EngineConfig, Usage  # noqa: E402
from gateway_engine import (GatewayConfig, GatewayEngine,  # noqa: E402
                            GatewayExecutionError, GatewayNotFound)
from gateway_fakes import KEY, MODEL, USAGE, FakeNode, err, ok  # noqa: E402

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}},
          "required": ["answer"]}


class Case(unittest.TestCase):
    def setUp(self):
        self.node = FakeNode(self)

    def media(self, name: str = "clip.mp4", size: int = 64) -> Path:
        path = self.node.dir / name
        path.write_bytes(b"\0" * size)
        return path

    def refused(self, engine=None, **ask) -> GatewayExecutionError:
        """The permanent error the call raises, with no launch of the program."""
        engine = engine or self.node.engine()
        with self.assertRaises(GatewayExecutionError) as caught:
            engine.ask_json("judge it", SCHEMA, **ask)
        self.assertFalse(caught.exception.transient, str(caught.exception))
        self.assertEqual(self.node.count, 0, "a refused call started the program")
        return caught.exception


class Preconditions(Case):
    """Each is checked before the program starts, and names the fix."""

    def test_refuses_without_key(self):
        os.environ.pop("AI_GATEWAY_API_KEY")
        self.assertIn("AI_GATEWAY_API_KEY", str(self.refused()))

    def test_refuses_vmc_model(self):
        for model in ("vmc/house-judge", "claude-code/anthropic/claude-opus-5", "sonnet",
                      "gemini-3.6-flash", "google/"):
            with self.subTest(model=model):
                self.assertIn("provider/model", str(self.refused(self.node.engine(model=model))))

    def test_refuses_old_node(self):
        os.environ["FAKE_NODE_VERSION"] = "v20.11.1"
        self.addCleanup(os.environ.pop, "FAKE_NODE_VERSION", None)
        self.assertIn("22", str(self.refused()))

    def test_refuses_missing_node(self):
        engine = self.node.engine(executable=str(self.node.dir / "no-node-here"))
        with self.assertRaises(GatewayNotFound):
            engine.ask_json("judge it", SCHEMA)
        self.assertIsInstance(GatewayNotFound("x"), claude_engine.ClaudeNotFound)

    def test_node_is_found_through_yt_gateway_node(self):
        engine = self.node.engine(executable="node")
        self.assertEqual(engine.ask_json("judge it", SCHEMA), {"answer": "yes"})
        self.assertEqual(self.node.count, 1)

    def test_refuses_missing_node_modules(self):
        (self.node.program_dir / "node_modules" / "ai" / "package.json").unlink()
        self.assertIn("npm ci --ignore-scripts", str(self.refused()))

    def test_refuses_another_ai_version(self):
        (self.node.program_dir / "node_modules" / "ai" / "package.json").write_text(
            json.dumps({"version": "7.0.149"}))
        message = str(self.refused())
        self.assertIn("7.0.150", message)
        self.assertIn("npm ci --ignore-scripts", message)

    def test_refuses_missing_media(self):
        self.assertIn("gone.mp4", str(self.refused(media=[self.node.dir / "gone.mp4"])))

    def test_refuses_unknown_extension(self):
        self.assertIn(".gif", str(self.refused(media=[self.media("loop.gif")])))

    def test_refuses_media_over_cap(self):
        engine = self.node.engine(max_media_mb=1)
        big = self.media("long.mp4", size=700_000)
        more = self.media("more.mp4", size=700_000)
        self.assertIn("max_media_mb", str(self.refused(engine, media=[big, more])))

    def test_refuses_effort_and_fallback(self):
        for kw in ({"effort": "high"}, {"fallback_model": "google/gemini-3.5-flash"}):
            with self.subTest(**kw):
                self.refused(self.node.engine(**kw))

    def test_refuses_tools(self):
        engine = self.node.engine()
        with self.assertRaises(GatewayExecutionError) as caught:
            engine.ask("look", tools=("Read",))
        self.assertFalse(caught.exception.transient)
        self.assertEqual(self.node.count, 0)

    def test_stream_and_agent_are_refused(self):
        engine = self.node.engine()
        for call in (lambda: list(engine.stream("look")), lambda: engine.agent("look")):
            with self.assertRaises(GatewayExecutionError) as caught:
                call()
            self.assertFalse(caught.exception.transient)
        self.assertEqual(self.node.count, 0)

    def test_the_node_version_is_read_once_per_process(self):
        engine = self.node.engine()
        engine.ask_json("one", SCHEMA)
        os.environ["FAKE_NODE_VERSION"] = "v20.0.0"
        self.addCleanup(os.environ.pop, "FAKE_NODE_VERSION", None)
        engine.ask_json("two", SCHEMA)
        self.assertEqual(self.node.count, 2)


class Request(Case):
    def test_request_shape(self):
        clip = self.media("clip.mp4")
        still = self.media("frame.png")
        cwd = os.getcwd()
        os.chdir(self.node.dir)
        self.addCleanup(os.chdir, cwd)
        engine = self.node.engine(timeout=120.0)
        engine.ask_json("judge it", SCHEMA, system="the house", media=["clip.mp4", still])
        (call,) = self.node.calls()
        self.assertEqual(call["argv"], [str(self.node.program_dir / gateway_engine.PROGRAM)])
        self.assertIn("AI_GATEWAY_API_KEY", call["env"])
        req = json.loads(call["stdin"])
        self.assertEqual(req["model"], MODEL)
        self.assertEqual(req["instructions"], "the house")
        self.assertEqual(req["prompt"], "judge it")
        self.assertEqual(req["schema"], SCHEMA)
        self.assertEqual(req["media"], [{"path": str(clip.resolve()), "mediaType": "video/mp4"},
                                        {"path": str(still.resolve()), "mediaType": "image/png"}])
        self.assertEqual(req["gateway"], {})
        self.assertNotIn("models", json.dumps(req))
        self.assertEqual(req["maxRetries"], 0)
        self.assertEqual(req["timeoutMs"], 120_000)
        self.assertEqual(req["costPoll"], {"tries": 0, "intervalMs": 2000})
        self.assertNotIn("host", req)

    def test_providers_and_has_are_sent_as_only_and_has(self):
        self.node.engine(providers=["google"], has=["structured-output"]).ask_json("q", SCHEMA)
        (req,) = self.node.requests()
        self.assertEqual(req["gateway"], {"only": ["google"], "has": ["structured-output"]})

    def test_the_host_is_sent_only_when_yt_gateway_url_is_set(self):
        os.environ["YT_GATEWAY_URL"] = "https://proxy.example/"
        self.addCleanup(os.environ.pop, "YT_GATEWAY_URL", None)
        self.node.engine().ask_json("q", SCHEMA)
        (req,) = self.node.requests()
        self.assertEqual(req["host"], "https://proxy.example")

    def test_the_process_timeout_covers_the_call_and_the_cost_poll(self):
        engine = self.node.engine(timeout=100.0, cost_poll_tries=5, cost_poll_interval_ms=2000)
        self.assertEqual(engine.process_timeout(), 100.0 + 30 + 10.0)

    def test_yt_gateway_is_not_needed_and_changes_nothing(self):
        # The engine is its own opt-in: YT_GATEWAY reroutes the claude CLI,
        # not this program, and the program is never handed the CLI's variables.
        for switch in (None, "vercel"):
            with self.subTest(switch=switch):
                if switch:
                    os.environ["YT_GATEWAY"] = switch
                    self.addCleanup(os.environ.pop, "YT_GATEWAY", None)
                self.node.engine().ask_json("q", SCHEMA)
                env = self.node.calls()[-1]["env"]
                self.assertFalse([k for k in env if k in ("ANTHROPIC_BASE_URL",
                                                          "ANTHROPIC_AUTH_TOKEN")])


class Booking(Case):
    def test_success_books_usage(self):
        engine = self.node.engine()
        answer = engine.ask("judge it", schema=SCHEMA)
        self.assertEqual(answer.json(), {"answer": "yes"})
        self.assertEqual(answer.model, MODEL)
        u = engine.usage
        self.assertEqual((u.calls, u.input_tokens, u.cache_read_tokens,
                          u.cache_creation_tokens, u.output_tokens), (1, 1000, 150, 50, 300))
        self.assertAlmostEqual(u.cost_usd, 0.0012)
        row = u.by_model[MODEL]
        self.assertEqual(row["cost_gateway_calls"], 1)
        self.assertNotIn("cost_table_calls", row)
        # Usage.add is unchanged: the same payload booked by a plain
        # ClaudeEngine gives the same row, less the counter.
        payload = engine.log[-1]
        self.assertEqual(payload["generation_id"], "gen_01TEST")
        plain = ClaudeEngine(EngineConfig())
        plain._absorb_result(dict(payload))
        mine = u.as_dict()
        mine["by_model"][MODEL].pop("cost_gateway_calls")
        self.assertEqual(mine, plain.usage.as_dict())
        self.assertNotIn("cost_gateway_calls", answer.usage.by_model[MODEL])

    def test_cost_from_generation_only_when_poll_enabled(self):
        self.node.replies(ok(cost=None, generation_cost=0.0031))
        engine = self.node.engine()
        engine.ask_json("q", SCHEMA)
        self.assertNotIn("cost_generation_calls", engine.usage.by_model[MODEL])
        self.assertEqual(engine.usage.by_model[MODEL]["cost_unpriced_calls"], 1)
        polled = self.node.engine(cost_poll_tries=3)
        polled.ask_json("q", SCHEMA)
        self.assertEqual(self.node.requests()[-1]["costPoll"], {"tries": 3, "intervalMs": 2000})
        self.assertEqual(polled.usage.by_model[MODEL]["cost_generation_calls"], 1)
        self.assertAlmostEqual(polled.usage.cost_usd, 0.0031)

    def test_cost_from_table_then_unpriced(self):
        self.node.replies(ok(cost=None))
        row = {"input": 0.75, "cached_input": 0.075, "output": 3.75}
        os.environ["GATEWAY_PRICES"] = json.dumps({MODEL: row})
        self.addCleanup(os.environ.pop, "GATEWAY_PRICES", None)
        engine = self.node.engine()
        engine.ask_json("q", SCHEMA)
        expected = (1000 * 0.75 + 150 * 0.075 + 50 * 0.75 + 300 * 3.75) / 1_000_000
        self.assertAlmostEqual(engine.usage.cost_usd, expected)
        self.assertEqual(engine.usage.by_model[MODEL]["cost_table_calls"], 1)
        # An Anthropic gateway id is priced from spend's own table.
        claude = "anthropic/claude-haiku-4.5"
        self.node.replies(ok(cost=None, model=claude))
        engine = self.node.engine(model=claude)
        engine.ask_json("q", SCHEMA)
        self.assertGreater(engine.usage.cost_usd, 0)
        self.assertEqual(engine.usage.by_model[claude]["cost_table_calls"], 1)
        # Nobody priced it: $0, and said so.
        os.environ.pop("GATEWAY_PRICES")
        self.node.replies(ok(cost=None))
        engine = self.node.engine()
        engine.ask_json("q", SCHEMA)
        self.assertEqual(engine.usage.cost_usd, 0.0)
        self.assertEqual(engine.usage.by_model[MODEL]["cost_unpriced_calls"], 1)

    def test_merge_keeps_cost_counters_numeric(self):
        engine = self.node.engine()
        engine.ask_json("q", SCHEMA)
        claude = Usage()
        claude.add({"usage": {"input_tokens": 5}, "total_cost_usd": 0.5,
                    "modelUsage": {MODEL: {"inputTokens": 5, "costUSD": 0.5}}})
        into_claude = Usage()
        into_claude.merge(claude)
        into_claude.merge(engine.usage)
        into_claude.merge(engine.usage)
        self.assertEqual(into_claude.by_model[MODEL]["cost_gateway_calls"], 2)
        self.assertEqual(into_claude.by_model[MODEL]["calls"], 3)
        into_gateway = Usage()
        into_gateway.merge(engine.usage)
        into_gateway.merge(claude)
        self.assertEqual(into_gateway.by_model[MODEL]["cost_gateway_calls"], 1)
        self.assertAlmostEqual(into_gateway.cost_usd, 0.5012)

    def test_a_budget_on_an_unpriced_model_is_refused(self):
        self.node.replies(ok(cost=None))
        error = self.refused(self.node.engine(max_turns_budget_usd=0.5))
        self.assertIn("GATEWAY_PRICES", str(error))

    def test_a_retry_stops_once_the_booked_cost_reaches_the_budget(self):
        os.environ["GATEWAY_PRICES"] = json.dumps({MODEL: {"input": 1000.0, "output": 1000.0}})
        self.addCleanup(os.environ.pop, "GATEWAY_PRICES", None)
        self.node.replies(err("other", 1, message="socket hang up", usage=USAGE))
        engine = self.node.engine(max_turns_budget_usd=0.5)
        with self.assertRaises(GatewayExecutionError) as caught:
            engine.ask_json("q", SCHEMA)
        self.assertFalse(caught.exception.transient)
        self.assertIn("budget", str(caught.exception))
        self.assertEqual(self.node.count, 1)
        self.assertGreater(engine.usage.cost_usd, 0.5)


class ExistingEngines(unittest.TestCase):
    """Usage.add, Usage.merge and the harness's booking are untouched: a fixed
    claude, codex and bridge payload books exactly what it did before."""

    PAYLOADS = {
        "claude": {"type": "result", "total_cost_usd": 0.25, "duration_ms": 900,
                   "usage": {"input_tokens": 10, "output_tokens": 20,
                             "cache_read_input_tokens": 30, "cache_creation_input_tokens": 40},
                   "modelUsage": {"claude-sonnet-5-5": {
                       "inputTokens": 10, "outputTokens": 20, "cacheReadInputTokens": 30,
                       "cacheCreationInputTokens": 40, "costUSD": 0.25}}},
        "codex": {"type": "result", "total_cost_usd": 0.0125, "duration_ms": 50,
                  "usage": {"input_tokens": 1000, "output_tokens": 100,
                            "cache_read_input_tokens": 0},
                  "modelUsage": {"gpt-5": {"inputTokens": 1000, "outputTokens": 100,
                                           "cacheReadInputTokens": 0, "costUSD": 0.0125}}},
        "bridge": {"type": "result", "subtype": "success", "result": "ok",
                   "model": "active-chat-session", "total_cost_usd": None,
                   "unmetered": True, "duration_ms": 7},
    }
    BOOKED = {
        "claude": {"input_tokens": 10, "output_tokens": 20, "cache_read_tokens": 30,
                   "cache_creation_tokens": 40, "cost_usd": 0.25, "calls": 1,
                   "unmetered_calls": 0, "duration_ms": 900,
                   "by_model": {"claude-sonnet-5-5": {
                       "calls": 1, "input_tokens": 10, "output_tokens": 20,
                       "cache_read_tokens": 30, "cache_creation_tokens": 40,
                       "cost_usd": 0.25}},
                   "timed_out_calls": 0, "timed_out_tokens": {}, "timed_out_cost_usd": 0.0,
                   "timed_out_cost_estimated": False},
        "codex": {"input_tokens": 1000, "output_tokens": 100, "cache_read_tokens": 0,
                  "cache_creation_tokens": 0, "cost_usd": 0.0125, "calls": 1,
                  "unmetered_calls": 0, "duration_ms": 50,
                  "by_model": {"gpt-5": {
                      "calls": 1, "input_tokens": 1000, "output_tokens": 100,
                      "cache_read_tokens": 0, "cache_creation_tokens": 0,
                      "cost_usd": 0.0125}},
                  "timed_out_calls": 0, "timed_out_tokens": {}, "timed_out_cost_usd": 0.0,
                  "timed_out_cost_estimated": False},
        "bridge": {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
                   "cache_creation_tokens": 0, "cost_usd": 0.0, "calls": 1,
                   "unmetered_calls": 1, "duration_ms": 7, "by_model": {},
                   "timed_out_calls": 0, "timed_out_tokens": {}, "timed_out_cost_usd": 0.0,
                   "timed_out_cost_estimated": False},
    }

    def test_existing_engines_book_identically(self):
        import codex_engine
        from bridge_engine import BridgeEngine
        for name, cls in (("claude", ClaudeEngine), ("codex", codex_engine.CodexEngine),
                          ("bridge", BridgeEngine)):
            with self.subTest(engine=name):
                engine = cls.__new__(cls)
                ClaudeEngine.__init__(engine, EngineConfig())
                engine._absorb_result(dict(self.PAYLOADS[name]))
                self.assertEqual(engine.usage.as_dict(), self.BOOKED[name])
                merged = Usage()
                merged.merge(engine.usage)
                self.assertEqual(merged.as_dict(), self.BOOKED[name])


class ServedModel(Case):
    def test_served_model_mismatch_permanent_and_booked(self):
        self.node.replies(ok(model="google/gemini-3.5-flash"))
        engine = self.node.engine()
        with self.assertRaises(GatewayExecutionError) as caught:
            engine.ask_json("q", SCHEMA)
        self.assertFalse(caught.exception.transient)
        self.assertIn("gemini-3.5-flash", str(caught.exception))
        self.assertEqual(self.node.count, 1)
        self.assertEqual(engine.usage.calls, 1)
        self.assertAlmostEqual(engine.usage.cost_usd, 0.0012)
        self.assertIn("google/gemini-3.5-flash", engine.usage.by_model)

    def test_routing_attempt_other_model_refused(self):
        routing = {"finalProvider": "google", "modelAttempts": [
            {"canonicalSlug": "anthropic/claude-opus-5"}, {"canonicalSlug": MODEL}]}
        self.node.replies(ok(routing=routing))
        engine = self.node.engine()
        with self.assertRaises(GatewayExecutionError) as caught:
            engine.ask_json("q", SCHEMA)
        self.assertFalse(caught.exception.transient)
        self.assertIn("claude-opus-5", str(caught.exception))
        self.assertEqual(engine.usage.calls, 1)

    def test_provider_switch_same_model_passes(self):
        routing = {"resolvedProvider": "vertex", "finalProvider": "google",
                   "modelAttempts": [{"canonicalSlug": MODEL}]}
        for served in (MODEL, "gemini-3.6-flash"):
            with self.subTest(served=served):
                self.node.replies(ok(model=served, routing=routing))
                self.assertEqual(self.node.engine().ask_json("q", SCHEMA), {"answer": "yes"})
        # Anthropic writes versions with dashes, the gateway with dots: one model.
        self.node.replies(ok(model="anthropic/claude-haiku-4-5"))
        engine = self.node.engine(model="anthropic/claude-haiku-4.5")
        self.assertEqual(engine.ask_json("q", SCHEMA), {"answer": "yes"})


class Errors(Case):
    """One case per row of the error table: the transient flag and how many
    times the program ran (max_retries=2, so a transient failure runs 3)."""

    def outcome(self, *replies, **kw) -> tuple[GatewayExecutionError, int]:
        self.node.replies(*replies)
        engine = self.node.engine(**kw)
        with self.assertRaises(GatewayExecutionError) as caught:
            engine.ask_json("q", SCHEMA)
        self.engine = engine
        return caught.exception, self.node.count

    def gateway(self, status, retryable=True, message="", code=3):
        return err("gateway", code, status=status, retryable=retryable,
                   message=message or f"HTTP {status}")

    def test_429_retryable(self):
        error, runs = self.outcome(self.gateway(429, message="Rate limit"))
        self.assertTrue(error.transient)
        self.assertEqual(runs, 3)

    def test_429_then_success(self):
        self.node.replies(self.gateway(429), ok())
        engine = self.node.engine()
        self.assertEqual(engine.ask_json("q", SCHEMA), {"answer": "yes"})
        self.assertEqual(self.node.count, 2)
        self.assertEqual(engine.usage.calls, 1)

    def test_503_retryable(self):
        error, runs = self.outcome(self.gateway(503, message="Service unavailable"))
        self.assertTrue(error.transient)
        self.assertEqual(runs, 3)

    def test_4xx_permanent(self):
        for status in (401, 402, 403, 404):
            with self.subTest(status=status):
                (self.node.dir / "count").unlink(missing_ok=True)
                error, runs = self.outcome(self.gateway(status, retryable=True,
                                                        message=f"failed with {status}"))
                self.assertFalse(error.transient)
                self.assertEqual(runs, 1)
                self.assertEqual(error.status, status)

    def test_retryable_false_is_permanent(self):
        error, runs = self.outcome(self.gateway(503, retryable=False))
        self.assertFalse(error.transient)
        self.assertEqual(runs, 1)

    def test_budget_500_is_permanent(self):
        error, runs = self.outcome(self.gateway(
            500, retryable=True, message="Internal error: quota_for_entity_exceeded"))
        self.assertFalse(error.transient)
        self.assertEqual(runs, 1)

    def test_providers_unavailable(self):
        error, runs = self.outcome(
            err("providers_unavailable", 4, message="no_providers_available"),
            providers=["google", "vertex"])
        self.assertFalse(error.transient)
        self.assertEqual(runs, 1)
        self.assertIn("google, vertex", str(error))
        self.assertIn("providers", str(error))

    def test_schema(self):
        error, runs = self.outcome(err("schema", 2, message="No object generated",
                                       usage=USAGE))
        self.assertFalse(error.transient)
        self.assertEqual(runs, 1)
        self.assertEqual((self.engine.usage.calls, self.engine.usage.output_tokens), (1, 300))

    def test_no_output(self):
        error, runs = self.outcome(err("no_output", 2, message="No output generated"))
        self.assertFalse(error.transient)
        self.assertEqual(runs, 1)

    def test_timeout_in_node(self):
        error, runs = self.outcome(err("other", 1, timeout=True,
                                       message="The operation was aborted due to timeout"))
        self.assertTrue(error.transient)
        self.assertEqual(runs, 3)

    def test_timeout_of_the_process(self):
        real = subprocess.run

        def run(argv, *a, **kw):
            if argv[1:] == ["--version"]:
                return real(argv, *a, **kw)
            raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
        engine = self.node.engine()
        with mock.patch.object(gateway_engine.subprocess, "run", side_effect=run) as fake, \
                self.assertRaises(GatewayExecutionError) as caught:
            engine.ask_json("q", SCHEMA)
        self.assertTrue(caught.exception.transient)
        self.assertEqual(len([c for c in fake.call_args_list
                              if c.args[0][1:] != ["--version"]]), 3)

    def test_module_not_found(self):
        error, runs = self.outcome({"stderr": "node:internal/modules/esm/resolve:873\n"
                                              "Error [ERR_MODULE_NOT_FOUND]: Cannot find "
                                              "package 'ai' imported from gateway-engine.mjs",
                                    "code": 1})
        self.assertFalse(error.transient)
        self.assertEqual(runs, 1)
        self.assertIn("cd gateway && npm ci --ignore-scripts", str(error))

    def test_unknown_crash(self):
        error, runs = self.outcome({"stderr": "Segmentation fault", "code": 139})
        self.assertTrue(error.transient)
        self.assertEqual(runs, 3)

    def test_an_unknown_crash_with_a_permanent_marker(self):
        error, runs = self.outcome({"stderr": "Error: permission denied, open clip.mp4",
                                    "code": 1})
        self.assertFalse(error.transient)
        self.assertEqual(runs, 1)

    def test_key_never_in_errors(self):
        error, _ = self.outcome({"stderr": "fetch failed for key $KEY", "code": 1})
        self.assertNotIn(KEY, str(error))
        self.assertNotIn(KEY, error.stderr)
        self.assertIn("***", error.stderr)
        error, _ = self.outcome(self.gateway(401, message="bad key $KEY"))
        self.assertNotIn(KEY, str(error))
        self.assertIn("***", str(error))


class Cli(Case):
    def test_json_command_prints_the_answer(self):
        schema = self.node.dir / "schema.json"
        schema.write_text(json.dumps(SCHEMA))
        clip = self.media("clip.mp4")
        out = self.node.dir / "out.txt"
        with mock.patch.object(gateway_engine.sys, "stdout", out.open("w")) as stdout, \
                contextlib.redirect_stderr(io.StringIO()) as said:
            code = gateway_engine.main(["--executable", str(self.node.exe), "json", "judge it",
                                        "--schema", str(schema), "--model", MODEL,
                                        "--media", str(clip), "--provider", "google"])
            stdout.close()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.read_text()), {"answer": "yes"})
        self.assertIn("gen_01TEST", said.getvalue())
        (req,) = self.node.requests()
        self.assertEqual(req["gateway"], {"only": ["google"]})
        self.assertEqual(req["media"][0]["mediaType"], "video/mp4")


class Documented(unittest.TestCase):
    """An operator can find the engine and what it needs: the README and
    SKILL.md name the file, the variables it reads, the install step, and
    that it is chosen per stage and answers decisions only."""

    def test_the_readme_and_the_skill_name_the_engine(self):
        for doc in ("README.md", "ytstudio/SKILL.md"):
            flat = " ".join((ROOT / doc).read_text(encoding="utf-8").split())
            for needle in ("gateway_engine.py", gateway_engine.NODE_ENV,
                           gateway_engine.PRICES_ENV, "npm ci --ignore-scripts",
                           'engine="gateway"', "never `inspect`"):
                with self.subTest(doc=doc, needle=needle):
                    self.assertIn(needle, flat)


if __name__ == "__main__":
    unittest.main()
