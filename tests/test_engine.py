"""claude_engine.py: every `claude -p` flag is a config field, and a field
left None is filled from set_defaults(), then the environment, then not
passed at all (the CLI's own settings decide).

No model is run: the tests look at the argv the engine would launch.
"""

import json
import os
import unittest
from unittest import mock

import claude_engine
from claude_engine import ClaudeEngine, EngineConfig


def argv_for(config: EngineConfig, **kw) -> list[str]:
    kw.setdefault("output_format", "json")
    kw.setdefault("resume", False)
    kw.setdefault("tools", ...)
    kw.setdefault("system", None)
    return ClaudeEngine(config)._build_argv(**kw)


def flag(argv: list[str], name: str):
    """The value after `name`, True when it is a bare switch, None if absent."""
    if name not in argv:
        return None
    i = argv.index(name)
    nxt = argv[i + 1] if i + 1 < len(argv) else None
    return True if nxt is None or nxt.startswith("--") else nxt


class EngineCase(unittest.TestCase):
    def setUp(self):
        claude_engine.DEFAULTS.clear()
        self._env = {k: os.environ.pop(k) for k in list(os.environ)
                     if k.startswith("CLAUDE_ENGINE_")}
        self.cfg = lambda **kw: EngineConfig(executable="python3", **kw)

    def tearDown(self):
        claude_engine.DEFAULTS.clear()
        os.environ.update(self._env)


class Defaults(EngineCase):
    def test_nothing_set_passes_no_model_flags(self):
        argv = argv_for(self.cfg())
        for name in ("--model", "--fallback-model", "--effort"):
            self.assertIsNone(flag(argv, name), name)

    def test_set_defaults_fills_a_field_left_none(self):
        claude_engine.set_defaults(model="opus", effort="high")
        argv = argv_for(self.cfg())
        self.assertEqual(flag(argv, "--model"), "opus")
        self.assertEqual(flag(argv, "--effort"), "high")

    def test_an_explicit_value_beats_the_default(self):
        claude_engine.set_defaults(model="opus")
        self.assertEqual(flag(argv_for(self.cfg(model="haiku")), "--model"), "haiku")

    def test_the_environment_fills_a_field_after_set_defaults(self):
        with mock.patch.dict(os.environ, {"CLAUDE_ENGINE_EFFORT": "max",
                                          "CLAUDE_ENGINE_FALLBACK_MODEL": "sonnet"}):
            argv = argv_for(self.cfg())
            self.assertEqual(flag(argv, "--effort"), "max")
            self.assertEqual(flag(argv, "--fallback-model"), "sonnet")
            claude_engine.set_defaults(effort="low")
            self.assertEqual(flag(argv_for(self.cfg()), "--effort"), "low")

    def test_set_defaults_refuses_a_field_it_cannot_fill(self):
        with self.assertRaises(TypeError):
            claude_engine.set_defaults(timeout=5)

    def test_none_clears_a_default(self):
        claude_engine.set_defaults(model="opus")
        claude_engine.set_defaults(model=None)
        self.assertIsNone(flag(argv_for(self.cfg()), "--model"))


class Flags(EngineCase):
    def test_every_model_and_prompt_flag_is_passed(self):
        argv = argv_for(self.cfg(
            model="fable", fallback_model="opus,sonnet", effort="xhigh",
            betas=("b1", "b2"), system_prompt="S", append_system_prompt="A",
            system_prompt_snapshot="off", exclude_dynamic_system_prompt_sections=True,
            agent="reviewer"))
        self.assertEqual(flag(argv, "--model"), "fable")
        self.assertEqual(flag(argv, "--fallback-model"), "opus,sonnet")
        self.assertEqual(flag(argv, "--effort"), "xhigh")
        self.assertEqual(flag(argv, "--betas"), "b1,b2")
        self.assertEqual(flag(argv, "--system-prompt"), "S")
        self.assertEqual(flag(argv, "--append-system-prompt"), "A")
        self.assertEqual(flag(argv, "--system-prompt-snapshot"), "off")
        self.assertIn("--exclude-dynamic-system-prompt-sections", argv)
        self.assertEqual(flag(argv, "--agent"), "reviewer")

    def test_workspace_and_session_flags(self):
        argv = argv_for(self.cfg(
            add_dirs=("/a", "/b"), mcp_config=("m.json",), agents="agents.json",
            plugin_dirs=("p1",), plugin_urls=("https://x/p.zip",),
            settings="s.json", setting_sources="user,project", safe_mode=True,
            bare=True, name="run-7", autocompact="200k", max_turns_budget_usd=1.5,
            restricted=True, disable_slash_commands=True))
        self.assertEqual([argv[i + 1] for i, a in enumerate(argv) if a == "--add-dir"],
                         ["/a", "/b"])
        self.assertEqual(flag(argv, "--mcp-config"), "m.json")
        self.assertIn("--strict-mcp-config", argv)
        self.assertEqual(flag(argv, "--agents"), "agents.json")
        self.assertEqual(flag(argv, "--plugin-dir"), "p1")
        self.assertEqual(flag(argv, "--plugin-url"), "https://x/p.zip")
        self.assertEqual(flag(argv, "--settings"), "s.json")
        self.assertEqual(flag(argv, "--setting-sources"), "user,project")
        for switch in ("--safe-mode", "--bare", "--restricted",
                       "--disable-slash-commands"):
            self.assertIn(switch, argv, switch)
        self.assertEqual(flag(argv, "--name"), "run-7")
        self.assertEqual(flag(argv, "--autocompact"), "200k")
        self.assertEqual(flag(argv, "--max-budget-usd"), "1.5")

    def test_skip_permissions_replaces_the_no_prompt_guard(self):
        plain = argv_for(self.cfg())
        self.assertEqual(flag(plain, "--permission-prompts"), "none")
        self.assertNotIn("--dangerously-skip-permissions", plain)
        skip = argv_for(self.cfg(skip_permissions=True))
        self.assertIn("--dangerously-skip-permissions", skip)
        self.assertIsNone(flag(skip, "--permission-prompts"))

    def test_stream_only_flags_stay_out_of_json_mode(self):
        cfg = self.cfg(include_partial_messages=True, include_hook_events=True,
                       forward_subagent_text=True)
        extras = ("--include-partial-messages", "--include-hook-events",
                  "--forward-subagent-text")
        json_argv = argv_for(cfg)
        stream_argv = argv_for(cfg, output_format="stream-json")
        for e in extras:
            self.assertNotIn(e, json_argv, e)
            self.assertIn(e, stream_argv, e)

    def test_fork_session_only_with_a_resume(self):
        cfg = self.cfg(fork_session=True)
        self.assertNotIn("--fork-session", argv_for(cfg))
        resumed = argv_for(cfg, resume="abc")
        self.assertEqual(flag(resumed, "--resume"), "abc")
        self.assertIn("--fork-session", resumed)

    def test_debug_forms(self):
        self.assertNotIn("--debug", argv_for(self.cfg()))
        bare = argv_for(self.cfg(debug=True))
        self.assertTrue(flag(bare, "--debug"))
        self.assertEqual(flag(argv_for(self.cfg(debug="api,hooks")), "--debug"),
                         "api,hooks")
        filed = argv_for(self.cfg(debug_file="/tmp/x.log"))
        self.assertEqual(flag(filed, "--debug-file"), "/tmp/x.log")
        self.assertNotIn("--debug", filed)

    def test_the_cli_parser_reaches_every_field(self):
        args = claude_engine.build_parser().parse_args([
            "--model", "opus", "--fallback-model", "sonnet", "--effort", "high",
            "--betas", "b", "--system-prompt", "S", "--agent", "a",
            "--add-dir", "/d", "--mcp-config", "m", "--inherit-mcp", "--agents", "ag",
            "--plugin-dir", "p", "--settings", "s", "--setting-sources", "user",
            "--safe-mode", "--bare", "--restricted", "--no-skills", "--name", "n",
            "--no-persist", "--autocompact", "auto", "--budget", "2",
            "--debug", "api", "ask", "hi"])
        with mock.patch.object(claude_engine, "ClaudeEngine", wraps=ClaudeEngine) as made:
            claude_engine._engine_from_args(args)
        cfg = made.call_args.args[0]
        self.assertEqual((cfg.model, cfg.fallback_model, cfg.effort), ("opus", "sonnet", "high"))
        self.assertEqual(cfg.betas, ["b"])
        self.assertEqual(cfg.agent, "a")
        self.assertEqual(cfg.add_dirs, ["/d"])
        self.assertFalse(cfg.strict_mcp_config)
        self.assertEqual(cfg.plugin_dirs, ["p"])
        self.assertTrue(cfg.safe_mode and cfg.bare and cfg.restricted
                        and cfg.disable_slash_commands)
        self.assertFalse(cfg.persist_session)
        self.assertEqual(cfg.name, "n")
        self.assertEqual(cfg.autocompact, "auto")
        self.assertEqual(cfg.debug, "api")


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- #
# the process layer, against a fake `claude`
# --------------------------------------------------------------------------- #

import stat
import tempfile
import time
from pathlib import Path

FAKE_CLAUDE = r'''#!/usr/bin/env python3
"""A stand-in for `claude -p`: logs its argv, then acts out one step of a plan.

Step n of FAKE_PLAN (a JSON list) is the n-th launch; the last step repeats.
A step may carry: "stderr_flood" (bytes to write to stderr before anything
else), "stream" (stream-json lines to print), "result" (one JSON result to
print), "hang" (seconds to sleep after printing), "stderr" (text), "exit",
"barrier" (wait until this many launches have started, else fail after
"barrier_wait" seconds, default 5: only launches that run at the same time
can ever meet it).
"""
import json, os, sys, time
log = os.environ["FAKE_LOG"]
with open(log, "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
with open(log) as f:
    n = sum(1 for _ in f) - 1
plan = json.load(open(os.environ["FAKE_PLAN"]))
step = plan[min(n, len(plan) - 1)]
sys.stdin.read()
if step.get("barrier"):
    deadline = time.monotonic() + step.get("barrier_wait", 5.0)
    while sum(1 for _ in open(log)) < step["barrier"]:
        if time.monotonic() > deadline:
            sys.stderr.write("barrier never met: launches are not concurrent")
            sys.exit(3)
        time.sleep(0.02)
if step.get("stderr_flood"):
    sys.stderr.write("x" * step["stderr_flood"])
    sys.stderr.flush()
for line in step.get("stream", []):
    print(json.dumps(line)); sys.stdout.flush()
if "result" in step:
    print(json.dumps(step["result"])); sys.stdout.flush()
if step.get("hang"):
    time.sleep(step["hang"])
if step.get("stderr"):
    sys.stderr.write(step["stderr"])
sys.exit(step.get("exit", 0))
'''

INIT = {"type": "system", "subtype": "init", "session_id": "s-fake"}
HELLO = {"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}}


def ok_result(**extra) -> dict:
    base = {"type": "result", "subtype": "success", "is_error": False,
            "result": "hello", "session_id": "s-fake", "num_turns": 1,
            "usage": {"input_tokens": 2, "output_tokens": 4}, "total_cost_usd": 0.01,
            "duration_ms": 5}
    base.update(extra)
    return base


class FakeClaudeCase(EngineCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.exe = root / "claude"
        self.exe.write_text(FAKE_CLAUDE)
        self.exe.chmod(self.exe.stat().st_mode | stat.S_IEXEC)
        self.log = root / "argv.log"
        self.plan = root / "plan.json"
        self._envpatch = mock.patch.dict(os.environ, {
            "FAKE_LOG": str(self.log), "FAKE_PLAN": str(self.plan)})
        self._envpatch.start()

    def tearDown(self):
        self._envpatch.stop()
        self._tmp.cleanup()
        super().tearDown()

    def engine(self, plan: list, **kw) -> ClaudeEngine:
        self.plan.write_text(__import__("json").dumps(plan))
        kw.setdefault("retry_backoff", 0.0)
        kw.setdefault("max_retries", 0)
        kw.setdefault("timeout", 10.0)
        return ClaudeEngine(EngineConfig(executable=str(self.exe), **kw))

    def launches(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [__import__("json").loads(l) for l in self.log.read_text().splitlines()]

    def session_ids(self) -> list[str]:
        return [flag(argv, "--session-id") for argv in self.launches()]


class Retries(FakeClaudeCase):
    """Proposal 2: a retry is a fresh launch, and only for a transient fault."""

    def test_each_attempt_gets_a_fresh_session_id(self):
        eng = self.engine([{"exit": 1, "stderr": "API Error: 529 overloaded"},
                           {"result": ok_result()}], max_retries=1)
        answer = eng.ask("hi")
        self.assertEqual(answer.text, "hello")
        ids = self.session_ids()
        self.assertEqual(len(ids), 2)
        self.assertTrue(all(ids))
        self.assertNotEqual(ids[0], ids[1])
        self.assertEqual(eng.session_id, "s-fake")

    def test_the_pinned_id_is_known_before_the_launch_finishes(self):
        eng = self.engine([{"hang": 30}], timeout=0.5)
        with self.assertRaises(claude_engine.ClaudeExecutionError):
            eng.ask("hi")
        self.assertEqual(eng.pending_session_id, self.session_ids()[0])

    def test_a_timeout_is_transient(self):
        eng = self.engine([{"hang": 30}, {"result": ok_result()}],
                          timeout=0.5, max_retries=1)
        self.assertEqual(eng.ask("hi").text, "hello")
        self.assertEqual(len(self.launches()), 2)

    def test_a_session_clash_is_not_retried(self):
        eng = self.engine([{"exit": 1, "stderr": "Error: Session ID abc is already in use."},
                           {"result": ok_result()}], max_retries=3)
        with self.assertRaises(claude_engine.ClaudeExecutionError) as caught:
            eng.ask("hi")
        self.assertFalse(caught.exception.transient)
        self.assertEqual(len(self.launches()), 1)

    def test_a_usage_error_is_not_retried(self):
        eng = self.engine([{"exit": 1, "stderr": "error: unknown option '--bogus'"},
                           {"result": ok_result()}], max_retries=3)
        with self.assertRaises(claude_engine.ClaudeExecutionError):
            eng.ask("hi")
        self.assertEqual(len(self.launches()), 1)

    def test_a_budget_stop_is_not_retried(self):
        stop = ok_result(is_error=True, subtype="error_max_budget_usd", result="over")
        eng = self.engine([{"result": stop}, {"result": ok_result()}], max_retries=3)
        with self.assertRaises(claude_engine.ClaudeExecutionError) as caught:
            eng.ask("hi")
        self.assertFalse(caught.exception.transient)
        self.assertEqual(len(self.launches()), 1)

    def test_a_transient_error_payload_is_retried_on_a_new_session(self):
        flaky = ok_result(is_error=True, subtype="error_during_execution",
                          result="API Error: 500 Internal server error")
        eng = self.engine([{"result": flaky}, {"result": ok_result()}], max_retries=1)
        self.assertEqual(eng.ask("hi").text, "hello")
        ids = self.session_ids()
        self.assertEqual(len(ids), 2)
        self.assertNotEqual(ids[0], ids[1])

    def test_a_resume_never_mints_an_id(self):
        eng = self.engine([{"result": ok_result()}])
        eng.ask("hi", resume="s-earlier")
        argv = self.launches()[0]
        self.assertEqual(flag(argv, "--resume"), "s-earlier")
        self.assertIsNone(flag(argv, "--session-id"))

    def test_classification(self):
        transient = claude_engine.is_transient
        self.assertTrue(transient("claude timed out after 1s"))
        self.assertTrue(transient("API Error: 529 {\"type\":\"overloaded_error\"}"))
        self.assertTrue(transient("rate limit exceeded"))
        self.assertTrue(transient("fetch failed: ECONNRESET"))
        self.assertTrue(transient("claude exited with code 1"))   # unknown: try again
        self.assertFalse(transient("Session ID x is already in use"))
        self.assertFalse(transient("error: unknown option '--x'"))
        self.assertFalse(transient("Invalid API key · Please run /login"))
        self.assertFalse(transient("Not logged in"))
        self.assertFalse(transient("error_max_budget_usd"))
        self.assertFalse(transient("error_max_turns"))


class Streaming(FakeClaudeCase):
    """Proposal 1: the stream has a real deadline, drains stderr, and retries
    a launch that failed before it said anything."""

    def test_a_silent_child_is_killed_at_the_deadline(self):
        eng = self.engine([{"stream": [INIT], "hang": 30}], timeout=0.5)
        started = time.monotonic()
        with self.assertRaises(claude_engine.ClaudeExecutionError) as caught:
            list(eng.stream("hi"))
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertIn("timed out", str(caught.exception))
        self.assertTrue(caught.exception.transient)

    def test_a_stderr_flood_does_not_deadlock(self):
        eng = self.engine([{"stderr_flood": 2_000_000,
                            "stream": [INIT, HELLO, ok_result()]}], timeout=10)
        events = list(eng.stream("hi"))
        self.assertEqual(events[-1].type, "result")
        self.assertEqual([e.text for e in events if e.type == "assistant"], ["hello"])

    def test_a_launch_failure_is_retried(self):
        eng = self.engine([{"exit": 1, "stderr": "API Error: 529 overloaded"},
                           {"stream": [INIT, HELLO, ok_result()]}], max_retries=1)
        events = list(eng.stream("hi"))
        self.assertEqual(events[-1].type, "result")
        ids = self.session_ids()
        self.assertEqual(len(ids), 2)
        self.assertNotEqual(ids[0], ids[1])

    def test_a_failure_after_only_the_init_message_is_retried(self):
        eng = self.engine([{"stream": [INIT], "exit": 1, "stderr": "overloaded"},
                           {"stream": [INIT, HELLO, ok_result()]}], max_retries=1)
        events = list(eng.stream("hi"))
        self.assertEqual(events[-1].type, "result")
        self.assertEqual(len(self.launches()), 2)
        # The consumer saw the retried run's init once, not twice.
        self.assertEqual(sum(1 for e in events if e.type == "system"), 1)

    def test_a_failure_after_content_was_delivered_is_not_retried(self):
        eng = self.engine([{"stream": [INIT, HELLO], "exit": 1, "stderr": "overloaded"},
                           {"stream": [INIT, HELLO, ok_result()]}], max_retries=3)
        seen = []
        with self.assertRaises(claude_engine.ClaudeExecutionError):
            for event in eng.stream("hi"):
                seen.append(event)
        self.assertEqual(len(self.launches()), 1)
        self.assertEqual([e.text for e in seen if e.type == "assistant"], ["hello"])

    def test_a_permanent_launch_failure_is_not_retried(self):
        eng = self.engine([{"exit": 1, "stderr": "Not logged in"},
                           {"stream": [INIT, HELLO, ok_result()]}], max_retries=3)
        with self.assertRaises(claude_engine.ClaudeExecutionError):
            list(eng.stream("hi"))
        self.assertEqual(len(self.launches()), 1)

    def test_agent_reports_usage_once_after_a_retry(self):
        eng = self.engine([{"exit": 1, "stderr": "overloaded"},
                           {"stream": [INIT, HELLO, ok_result()]}], max_retries=1)
        answer = eng.agent("hi")
        self.assertEqual(answer.text, "hello")
        self.assertEqual(eng.usage.calls, 1)


class Payload(FakeClaudeCase):
    """Proposal 3: the result payload already says what was answered and by whom."""

    def test_ask_json_prefers_the_validated_structured_output(self):
        chatty = ok_result(result='Sure! {"n": 8} -- hope that helps',
                           structured_output={"n": 9})
        eng = self.engine([{"result": chatty}])
        self.assertEqual(eng.ask_json("q", {"type": "object"}), {"n": 9})
        answer = eng.ask("q", schema={"type": "object"})
        self.assertEqual(answer.structured, {"n": 9})
        self.assertEqual(answer.json(), {"n": 9})

    def test_json_falls_back_to_the_text_when_there_is_no_structured_output(self):
        eng = self.engine([{"result": ok_result(result='```json\n{"n": 8}\n```')}])
        self.assertEqual(eng.ask_json("q", {"type": "object"}), {"n": 8})

    def test_the_answer_names_the_model_that_actually_answered(self):
        usage = {"claude-haiku-5-5": {"inputTokens": 2, "outputTokens": 4,
                                      "cacheReadInputTokens": 10,
                                      "cacheCreationInputTokens": 3,
                                      "costUSD": 0.001}}
        eng = self.engine([{"result": ok_result(modelUsage=usage)}], model="opus")
        answer = eng.ask("q")
        self.assertEqual(answer.model, "claude-haiku-5-5")
        self.assertEqual(answer.usage.by_model["claude-haiku-5-5"]["cost_usd"], 0.001)

    def test_with_several_models_the_one_that_wrote_the_most_wins(self):
        usage = {"claude-opus-5-5": {"outputTokens": 1, "costUSD": 0.5},
                 "claude-sonnet-5-5": {"outputTokens": 40, "costUSD": 0.1}}
        eng = self.engine([{"result": ok_result(modelUsage=usage)}])
        self.assertEqual(eng.ask("q").model, "claude-sonnet-5-5")

    def test_usage_accumulates_per_model_across_calls(self):
        usage = {"claude-haiku-5-5": {"outputTokens": 4, "inputTokens": 2,
                                      "costUSD": 0.001}}
        eng = self.engine([{"result": ok_result(modelUsage=usage)}])
        eng.ask("a"); eng.ask("b")
        by = eng.usage.by_model["claude-haiku-5-5"]
        self.assertEqual(by["calls"], 2)
        self.assertEqual(by["output_tokens"], 8)
        self.assertAlmostEqual(by["cost_usd"], 0.002)
        self.assertIsNone(ClaudeEngine(EngineConfig(executable="python3")).usage.by_model.get("x"))

    def test_an_answer_without_model_usage_has_no_model(self):
        eng = self.engine([{"result": ok_result()}])
        self.assertIsNone(eng.ask("q").model)


class Merging(unittest.TestCase):
    """Usage adds up, per-model rows included."""

    def test_merge_sums_totals_and_per_model_rows(self):
        a, b = claude_engine.Usage(), claude_engine.Usage()
        a.add(ok_result(modelUsage={"m1": {"outputTokens": 4, "costUSD": 0.1}}))
        b.add(ok_result(modelUsage={"m1": {"outputTokens": 6, "costUSD": 0.2},
                                    "m2": {"outputTokens": 1, "costUSD": 0.05}}))
        a.merge(b)
        self.assertEqual(a.calls, 2)
        self.assertEqual(a.output_tokens, 8)
        self.assertEqual(a.by_model["m1"]["output_tokens"], 10)
        self.assertEqual(a.by_model["m1"]["calls"], 2)
        self.assertAlmostEqual(a.by_model["m1"]["cost_usd"], 0.3)
        self.assertEqual(a.by_model["m2"]["calls"], 1)
        self.assertEqual(b.by_model["m1"]["calls"], 1)   # the source is untouched

    def test_as_dict_is_json_serialisable(self):
        u = claude_engine.Usage()
        u.add(ok_result(modelUsage={"m1": {"outputTokens": 4, "costUSD": 0.1}}))
        json.dumps(u.as_dict())
        self.assertEqual(u.as_dict()["by_model"]["m1"]["output_tokens"], 4)


class Runner(FakeClaudeCase):
    """TaskRunner: serial by default; with jobs>1, independent tasks run at
    once on spawned workers and the parent keeps the whole account."""

    def tasks(self, *specs) -> list:
        return [claude_engine.Task(**s) for s in specs]

    def test_serial_runs_in_dependency_order_on_the_engine(self):
        eng = self.engine([{"result": ok_result()}])
        seen = []
        runner = claude_engine.TaskRunner(eng, on_progress=lambda n, m: seen.append((n, m)))
        results = runner.run(self.tasks(
            {"name": "b", "prompt": "from {a}", "depends_on": ["a"]},
            {"name": "a", "prompt": "first"}))
        self.assertTrue(results["a"].ok and results["b"].ok)
        self.assertEqual([n for n, m in seen if m == "start"], ["a", "b"])
        self.assertEqual(eng.usage.calls, 2)
        self.assertGreater(results["a"].seconds, 0.0)

    def three_at_a_barrier(self, jobs: int, wait: float = 5.0) -> tuple:
        eng = self.engine([{"barrier": 3, "barrier_wait": wait, "result": ok_result()}])
        results = claude_engine.TaskRunner(eng, jobs=jobs).run(self.tasks(
            {"name": "x", "prompt": "1"}, {"name": "y", "prompt": "2"},
            {"name": "z", "prompt": "3"}))
        return eng, results

    def test_independent_tasks_overlap_on_their_own_sessions(self):
        eng, results = self.three_at_a_barrier(jobs=3)
        self.assertTrue(all(r.ok for r in results.values()),
                        {n: r.error for n, r in results.items()})
        self.assertEqual(eng.usage.calls, 3)          # merged back into the parent
        self.assertEqual(len(eng.log), 3)
        self.assertEqual(len(set(self.session_ids())), 3)   # one session per worker

    def test_the_barrier_cannot_be_met_serially(self):
        # The control: the same tasks on one lane never meet, so the test
        # above can only pass when the launches really overlap.
        eng, results = self.three_at_a_barrier(jobs=1, wait=0.5)
        self.assertEqual(set(results), {"x"})
        self.assertFalse(results["x"].ok)
        self.assertIn("exited with code 3", results["x"].error)

    def test_dependents_start_only_after_their_dependencies_end(self):
        eng = self.engine([{"result": ok_result(), "hang": 0.2}])
        seen = []
        claude_engine.TaskRunner(eng, jobs=3, on_progress=lambda n, m: seen.append((n, m))).run(
            self.tasks({"name": "a", "prompt": "root"},
                       {"name": "b", "prompt": "{a}", "depends_on": ["a"]},
                       {"name": "c", "prompt": "{a}", "depends_on": ["a"]}))
        order = [f"{n}:{m}" for n, m in seen]
        self.assertLess(order.index("a:ok"), order.index("b:start"))
        self.assertLess(order.index("a:ok"), order.index("c:start"))

    def test_resume_continues_the_last_dependency_session(self):
        eng = self.engine([{"result": ok_result()}])
        claude_engine.TaskRunner(eng, jobs=2).run(self.tasks(
            {"name": "a", "prompt": "open"},
            {"name": "b", "prompt": "go on", "depends_on": ["a"], "resume": True}))
        resumes = [flag(argv, "--resume") for argv in self.launches()]
        self.assertEqual(resumes, [None, "s-fake"])

    def test_a_failed_dependency_blocks_its_dependents_without_running_them(self):
        eng = self.engine([{"result": ok_result()}])
        results = claude_engine.TaskRunner(eng, jobs=2).run(self.tasks(
            {"name": "a", "prompt": "bad", "check": lambda o: False},
            {"name": "b", "prompt": "{a}", "depends_on": ["a"]},
            {"name": "c", "prompt": "free"}), stop_on_error=False)
        self.assertFalse(results["a"].ok)
        self.assertEqual(results["a"].error, "check() rejected the output")
        self.assertFalse(results["b"].ok)
        self.assertIn("depends on 'a'", results["b"].error)
        self.assertTrue(results["c"].ok)
        self.assertEqual(len(self.launches()), 2)     # b never launched

    def test_serial_stop_on_error_runs_nothing_more(self):
        eng = self.engine([{"result": ok_result()}])
        results = claude_engine.TaskRunner(eng).run(self.tasks(
            {"name": "a", "prompt": "bad", "check": lambda o: False},
            {"name": "c", "prompt": "free"}))
        self.assertEqual(set(results), {"a"})

    def test_parallel_stop_on_error_keeps_running_work_and_starts_nothing_new(self):
        eng = self.engine([{"result": ok_result()}])
        slow_ok = lambda o: time.sleep(0.5) or True    # c is still running when a fails
        results = claude_engine.TaskRunner(eng, jobs=2).run(self.tasks(
            {"name": "a", "prompt": "bad", "check": lambda o: False},
            {"name": "c", "prompt": "free", "check": slow_ok},
            {"name": "d", "prompt": "{c}", "depends_on": ["c"]}))
        self.assertFalse(results["a"].ok)
        self.assertTrue(results["c"].ok)              # already running: finished and kept
        self.assertNotIn("d", results)                # never started

    def test_jobs_must_be_positive(self):
        with self.assertRaises(ValueError):
            claude_engine.TaskRunner(self.engine([{"result": ok_result()}]), jobs=0)


class Workers(EngineCase):
    """spawn() copies the configuration; absorb() folds a worker back in."""

    def test_spawn_shares_config_but_not_session_or_usage(self):
        eng = ClaudeEngine(self.cfg(model="haiku"))
        eng.session_id = "parent"
        worker = eng.spawn(name="section-3")
        self.assertEqual(worker.config.model, "haiku")
        self.assertEqual(worker.config.name, "section-3")
        self.assertIsNone(eng.config.name)
        self.assertIsNone(worker.session_id)
        worker.usage.add(ok_result())
        self.assertEqual(eng.usage.calls, 0)
        eng.absorb(worker)
        self.assertEqual(eng.usage.calls, 1)
        self.assertAlmostEqual(eng.usage.cost_usd, 0.01)

    def test_full_tools_names_the_agent_tool(self):
        self.assertIn("Agent", claude_engine.FULL_TOOLS)
        self.assertNotIn("Task", claude_engine.FULL_TOOLS)


# --------------------------------------------------------------------------- #
# a turn cap (AUTO-116)
# --------------------------------------------------------------------------- #

from dataclasses import replace

#: What claude -p prints when --max-turns stops it: an is_error result whose
#: subtype names the cap and whose `errors` says which, billed like any other.
MAX_TURNS_STOP = ok_result(is_error=True, subtype="error_max_turns", result="",
                           num_turns=4, total_cost_usd=0.02,
                           errors=["Reached maximum number of turns (3)"])
READ = {"type": "assistant", "message": {"content": [
    {"type": "tool_use", "name": "Read", "input": {"file_path": "a.png"}}]}}

#: The launch a config with no cap makes today, flag for flag, pinned so a
#: cap left unset changes nothing.
PLAIN_JSON = ["-p", "--output-format", "json", "--tools", "",
              "--permission-mode", "dontAsk", "--permission-prompts", "none",
              "--strict-mcp-config"]
PLAIN_STREAM = ["-p", "--output-format", "stream-json", "--tools", "Read",
                "--allowed-tools", "Read", "--permission-mode", "dontAsk",
                "--permission-prompts", "none", "--strict-mcp-config"]


class MaxTurns(FakeClaudeCase):
    """EngineConfig.max_turns caps an agentic call by turns as
    max_turns_budget_usd caps it by dollars; the CLI's stop is permanent."""

    def test_a_cap_is_passed_as_max_turns(self):
        self.assertEqual(flag(argv_for(self.cfg(max_turns=5)), "--max-turns"), "5")
        stream = argv_for(self.cfg(max_turns=5), output_format="stream-json")
        self.assertEqual(flag(stream, "--max-turns"), "5")

    def test_no_cap_passes_nothing_and_the_launch_is_what_it_was(self):
        for config in (self.cfg(), self.cfg(max_turns=None)):
            self.assertEqual(argv_for(config)[1:], PLAIN_JSON)
            self.assertEqual(argv_for(config, output_format="stream-json",
                                      tools=("Read",))[1:], PLAIN_STREAM)

    def test_the_cli_takes_a_cap(self):
        for given, expected in ((["--max-turns", "4"], 4), ([], None)):
            args = claude_engine.build_parser().parse_args([*given, "ask", "hi"])
            with mock.patch.object(claude_engine, "ClaudeEngine", wraps=ClaudeEngine) as made:
                claude_engine._engine_from_args(args)
            self.assertEqual(made.call_args.args[0].max_turns, expected)

    def test_a_capped_ask_is_a_permanent_named_error(self):
        for code in (0, 1):
            with self.subTest(exit=code):
                self.log.unlink(missing_ok=True)
                eng = self.engine([{"result": MAX_TURNS_STOP, "exit": code},
                                   {"result": ok_result()}], max_turns=3, max_retries=3)
                with self.assertRaises(claude_engine.ClaudeExecutionError) as caught:
                    eng.ask("hi")
                self.assertFalse(caught.exception.transient)
                self.assertIn("error_max_turns", str(caught.exception))
                self.assertEqual(len(self.launches()), 1)
                self.assertEqual(flag(self.launches()[0], "--max-turns"), "3")

    def test_a_capped_stream_stops_with_a_permanent_named_error_and_books_it(self):
        for code in (0, 1):
            with self.subTest(exit=code):
                self.log.unlink(missing_ok=True)
                eng = self.engine([{"stream": [INIT, READ, MAX_TURNS_STOP], "exit": code},
                                   {"stream": [INIT, HELLO, ok_result()]}],
                                  max_turns=3, max_retries=3)
                seen = []
                with self.assertRaises(claude_engine.ClaudeExecutionError) as caught:
                    for event in eng.stream("hi"):
                        seen.append(event)
                self.assertFalse(caught.exception.transient)
                self.assertIn("error_max_turns", str(caught.exception))
                self.assertIn("Reached maximum number of turns (3)", str(caught.exception))
                self.assertEqual(len(self.launches()), 1)
                self.assertEqual(seen[-1].subtype, "error_max_turns")   # delivered first
                self.assertAlmostEqual(eng.usage.cost_usd, 0.02)        # and paid

    def test_agent_raises_the_cap_too(self):
        eng = self.engine([{"stream": [INIT, READ, MAX_TURNS_STOP]}], max_turns=3)
        with self.assertRaises(claude_engine.ClaudeExecutionError) as caught:
            eng.agent("hi")
        self.assertIn("error_max_turns", str(caught.exception))

    def test_an_error_result_other_than_the_cap_is_left_as_it_was(self):
        # Only the cap's own stop raises from a stream that exits 0: any
        # other is_error result there is still the consumer's to read, as it
        # was before the cap existed.
        other = ok_result(is_error=True, subtype="error_during_execution", result="x")
        eng = self.engine([{"stream": [INIT, HELLO, other]}])
        events = list(eng.stream("hi"))
        self.assertEqual(events[-1].subtype, "error_during_execution")


class CappedInspect(FakeClaudeCase):
    """Acceptance: a capped inspect stops at the cap with a permanent, named
    error and a billed engine row, launched once, with nothing saved."""

    def test_the_cap_stops_the_inspection_and_bills_it(self):
        from ytstudio import harness
        cwd = Path(self._tmp.name) / "proj" / "qa"
        cwd.mkdir(parents=True)
        (cwd / "a.png").write_bytes(b"png")
        self.plan.write_text(json.dumps([{"stream": [INIT, READ, MAX_TURNS_STOP], "exit": 1},
                                         {"stream": [INIT, HELLO, ok_result()]}]))
        exe = str(self.exe)

        def engine(config):
            return ClaudeEngine(replace(config, executable=exe, max_retries=3,
                                        retry_backoff=0.0))

        schema = {"type": "object", "properties": {"answer": {"type": "string"}},
                  "required": ["answer"]}
        with mock.patch.object(harness, "ENGINE", engine):
            h = harness.Harness(cwd.parent, "vfx", model="sonnet", role="You look.",
                                verbose=False)
            with self.assertRaises(harness.HarnessError) as caught:
                h.inspect("look", "Look at a.png.", schema, cwd=cwd, max_turns=3)
        self.assertIn("error_max_turns", str(caught.exception))
        self.assertEqual(len(self.launches()), 1)                 # not retried
        self.assertEqual(flag(self.launches()[0], "--max-turns"), "3")
        report = h.report()
        self.assertEqual([(d["name"], d["kind"], d["cached"], d["tool_calls"])
                          for d in report["decisions"]], [("look", "failed", False, 1)])
        self.assertAlmostEqual(report["cost_usd"], 0.02)
        self.assertFalse((cwd.parent / "decisions").exists())
