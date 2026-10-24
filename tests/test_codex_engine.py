"""codex_engine.py: the ClaudeEngine API, driven by `codex exec`.

No model is run. A fake `codex` (a Python script written into a temp dir and
selected with `executable=`) records every argv, stdin, cwd and output-schema
file it is launched with, then prints JSONL. The JSONL under
tests/fixtures/codex/ was recorded from real `codex exec --json` calls
(codex-cli 0.163.0):

    ask_developer_instructions.jsonl  "reply with the word ok" with
                                      -c developer_instructions=... -> BANANA
    schema_ok.jsonl                   --output-schema on a strict 2-field schema
    schema_rejected.jsonl             --output-schema on a non-strict schema:
                                      the API refuses it (invalid_json_schema)
    agent_tools.jsonl                 workspace-write: one shell command, one
                                      file_change, two agent messages

Observed events: thread.started{thread_id}, turn.started,
item.started/item.completed{item: agent_message | command_execution |
file_change}, turn.completed{usage: input_tokens, cached_input_tokens,
cache_write_input_tokens, output_tokens, reasoning_output_tokens}, error,
turn.failed{error.message}.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import warnings
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import claude_engine                                   # noqa: E402
import codex_engine                                    # noqa: E402
from claude_engine import ClaudeExecutionError, EngineConfig, Event   # noqa: E402
from codex_engine import CodexConfig, CodexEngine      # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "codex"

FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, sys, time
argv = sys.argv[1:]
stdin = sys.stdin.read()
state = pathlib.Path(os.environ["FAKE_CODEX_STATE"])
n = len(list(state.glob("call-*.json")))
schema = None
if "--output-schema" in argv:
    schema = json.loads(pathlib.Path(argv[argv.index("--output-schema") + 1]).read_text())
(state / f"call-{n}.json").write_text(json.dumps(
    {"argv": argv, "stdin": stdin, "cwd": os.getcwd(), "schema": schema}))
plan = json.loads((state / "plan.json").read_text())
step = plan[min(n, len(plan) - 1)] if plan else {}
lines = list(step.get("lines", []))
if step.get("fixture"):
    lines = pathlib.Path(step["fixture"]).read_text().splitlines()
for line in lines:
    print(line if isinstance(line, str) else json.dumps(line), flush=True)
if step.get("stderr"):
    sys.stderr.write(step["stderr"])
if step.get("hang"):
    time.sleep(step["hang"])
sys.exit(step.get("exit", 0))
'''


def ok_lines(text="ok", *, thread="t-1", usage=None):
    """A minimal successful turn."""
    usage = usage or {"input_tokens": 1000, "cached_input_tokens": 400,
                      "cache_write_input_tokens": 0, "output_tokens": 50,
                      "reasoning_output_tokens": 10}
    return [{"type": "thread.started", "thread_id": thread},
            {"type": "turn.started"},
            {"type": "item.completed",
             "item": {"id": "item_0", "type": "agent_message", "text": text}},
            {"type": "turn.completed", "usage": usage}]


class FakeCodex:
    """The fake executable plus the directory it reports into."""

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="fake-codex-"))
        self.exe = self.dir / "codex"
        self.exe.write_text(FAKE)
        self.exe.chmod(0o755)
        self.state = self.dir / "state"
        self.state.mkdir()
        self.home = self.dir / "home"          # CODEX_HOME: no real config read
        self.home.mkdir()
        self.plan([])

    def plan(self, steps):
        (self.state / "plan.json").write_text(json.dumps(steps))
        for old in self.state.glob("call-*.json"):
            old.unlink()

    def calls(self):
        out = sorted(self.state.glob("call-*.json"),
                     key=lambda p: int(p.stem.split("-")[1]))
        return [json.loads(p.read_text()) for p in out]

    def env(self):
        return {"FAKE_CODEX_STATE": str(self.state), "CODEX_HOME": str(self.home)}

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def flag(argv, name):
    if name not in argv:
        return None
    i = argv.index(name)
    return argv[i + 1] if i + 1 < len(argv) else True


def configs(argv):
    """Every `-c key=value` override, as a dict of raw values."""
    out = {}
    for i, a in enumerate(argv):
        if a == "-c":
            key, _, value = argv[i + 1].partition("=")
            out[key] = value
    return out


class CodexCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCodex()
        codex_engine.DEFAULTS.clear()
        claude_engine.DEFAULTS.clear()
        self._env = {k: os.environ.pop(k) for k in list(os.environ)
                     if k.startswith(("CLAUDE_ENGINE_", "CODEX_ENGINE_", "CODEX_PRICES"))}
        codex_engine._WARNED.clear()
        self._quiet = warnings.catch_warnings()
        self._quiet.__enter__()
        warnings.simplefilter("ignore", codex_engine.CodexEngineWarning)

    def tearDown(self):
        self._quiet.__exit__(None, None, None)
        self.fake.close()
        codex_engine.DEFAULTS.clear()
        os.environ.update(self._env)

    def engine(self, **kw):
        kw.setdefault("max_retries", 0)
        kw.setdefault("retry_backoff", 0.0)
        env = self.fake.env()
        env.update(kw.pop("env", {}) or {})
        return CodexEngine(CodexConfig(executable=str(self.fake.exe), env=env, **kw))

    def run_ok(self, engine=None, lines=None, **ask):
        self.fake.plan([{"lines": lines or ok_lines()}])
        engine = engine or self.engine()
        return engine, engine.ask("hello", **ask), self.fake.calls()[-1]


# --------------------------------------------------------------------------- #
# argv: every mapped field
# --------------------------------------------------------------------------- #

class Argv(CodexCase):
    def test_a_plain_ask_is_a_json_exec_reading_the_prompt_on_stdin(self):
        _, answer, call = self.run_ok()
        argv = call["argv"]
        self.assertEqual(argv[0], "exec")
        self.assertIn("--json", argv)
        self.assertIn("--skip-git-repo-check", argv)
        self.assertEqual(argv[-1], "-")
        self.assertEqual(call["stdin"], "hello")
        self.assertEqual(answer.text, "ok")
        self.assertEqual(answer.session_id, "t-1")

    def test_no_tools_runs_read_only(self):
        _, _, call = self.run_ok(self.engine(tools=()))
        self.assertEqual(flag(call["argv"], "-s"), "read-only")

    def test_read_only_tools_run_read_only_in_cwd(self):
        cwd = self.fake.dir
        _, _, call = self.run_ok(self.engine(tools=("Read", "Glob", "Grep"), cwd=cwd))
        self.assertEqual(flag(call["argv"], "-s"), "read-only")
        self.assertEqual(flag(call["argv"], "-C"), str(cwd))
        self.assertEqual(Path(call["cwd"]).resolve(), cwd.resolve())

    def test_writing_tools_run_workspace_write_with_add_dirs_and_network(self):
        extra = self.fake.dir / "extra"
        extra.mkdir()
        eng = self.engine(tools=("Read", "Write", "Edit", "Bash"), cwd=self.fake.dir,
                          add_dirs=(extra,))
        _, _, call = self.run_ok(eng)
        argv = call["argv"]
        self.assertEqual(flag(argv, "-s"), "workspace-write")
        self.assertEqual(flag(argv, "--add-dir"), str(extra))
        self.assertEqual(configs(argv).get("sandbox_workspace_write.network_access"), "true")

    def test_edit_tools_without_bash_get_no_network(self):
        _, _, call = self.run_ok(self.engine(tools=("Read", "Edit")))
        self.assertEqual(flag(call["argv"], "-s"), "workspace-write")
        self.assertNotIn("sandbox_workspace_write.network_access", configs(call["argv"]))

    def test_full_access_only_when_skip_permissions_is_set(self):
        _, _, call = self.run_ok(self.engine(tools=claude_engine.FULL_TOOLS,
                                             permission_mode="bypassPermissions"))
        self.assertEqual(flag(call["argv"], "-s"), "workspace-write")
        _, _, call = self.run_ok(self.engine(tools=(), skip_permissions=True))
        self.assertEqual(flag(call["argv"], "-s"), "danger-full-access")
        for c in self.fake.calls():
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", c["argv"])

    def test_plan_mode_is_read_only_whatever_the_tools(self):
        _, _, call = self.run_ok(self.engine(tools=("Write", "Bash"), permission_mode="plan"))
        self.assertEqual(flag(call["argv"], "-s"), "read-only")

    def test_per_call_tools_override_the_config(self):
        eng = self.engine(tools=())
        _, _, call = self.run_ok(eng, tools=("Bash",))
        self.assertEqual(flag(call["argv"], "-s"), "workspace-write")

    def test_model_is_passed_with_dash_m(self):
        _, _, call = self.run_ok(self.engine(model="gpt-5"))
        self.assertEqual(flag(call["argv"], "-m"), "gpt-5")

    def test_a_claude_model_name_is_not_passed_and_warns_once(self):
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            _, _, call = self.run_ok(self.engine(model="sonnet"))
            self.run_ok(self.engine(model="sonnet"))
        self.assertNotIn("-m", call["argv"])
        hits = [w for w in seen if "sonnet" in str(w.message)]
        self.assertEqual(len(hits), 1)

    def test_codex_engine_model_env_replaces_a_claude_alias(self):
        with mock.patch.dict(os.environ, {"CODEX_ENGINE_MODEL": "gpt-5-mini"}):
            _, _, call = self.run_ok(self.engine(model="opus"))
        self.assertEqual(flag(call["argv"], "-m"), "gpt-5-mini")

    def test_effort_maps_to_model_reasoning_effort(self):
        _, _, call = self.run_ok(self.engine(effort="medium"))
        self.assertEqual(configs(call["argv"])["model_reasoning_effort"], '"medium"')

    def test_xhigh_and_max_clamp_to_high_unless_the_model_lists_them(self):
        _, _, call = self.run_ok(self.engine(effort="max", model="gpt-5"))
        self.assertEqual(configs(call["argv"])["model_reasoning_effort"], '"high"')
        (self.fake.home / "models_cache.json").write_text(json.dumps({"models": [
            {"slug": "gpt-x", "priority": 1, "visibility": "list",
             "supported_reasoning_levels": [{"effort": e} for e in
                                            ("low", "medium", "high", "xhigh", "max")]}]}))
        _, _, call = self.run_ok(self.engine(effort="xhigh", model="gpt-x"))
        self.assertEqual(configs(call["argv"])["model_reasoning_effort"], '"xhigh"')

    def test_system_prompts_become_developer_instructions(self):
        eng = self.engine(system_prompt='Be "terse".\nAlways.',
                          append_system_prompt="And kind.")
        _, _, call = self.run_ok(eng)
        raw = configs(call["argv"])["developer_instructions"]
        import tomllib
        value = tomllib.loads(f"v = {raw}")["v"]
        self.assertEqual(value, 'Be "terse".\nAlways.\n\nAnd kind.')
        self.assertEqual(call["stdin"], "hello")

    def test_per_call_system_replaces_the_configured_one(self):
        _, _, call = self.run_ok(self.engine(system_prompt="A"), system="B")
        self.assertEqual(configs(call["argv"])["developer_instructions"], '"B"')

    def test_a_huge_system_prompt_is_prepended_to_the_prompt_instead(self):
        big = "x" * (codex_engine.INSTRUCTIONS_ARG_LIMIT + 1)
        _, _, call = self.run_ok(self.engine(system_prompt=big))
        self.assertNotIn("developer_instructions", configs(call["argv"]))
        self.assertTrue(call["stdin"].startswith(codex_engine.SYSTEM_HEADING))
        self.assertTrue(call["stdin"].endswith("hello"))

    def test_images_are_attached_after_the_prompt_marker(self):
        img = self.fake.dir / "a.png"
        img.write_bytes(b"\x89PNG")
        _, _, call = self.run_ok(self.engine(images=(img,)))
        argv = call["argv"]
        self.assertEqual(argv[argv.index("-") + 1:], ["-i", str(img)])

    def test_persist_session_false_is_ephemeral(self):
        _, _, call = self.run_ok(self.engine(persist_session=False))
        self.assertIn("--ephemeral", call["argv"])
        _, _, call = self.run_ok(self.engine())
        self.assertNotIn("--ephemeral", call["argv"])

    def test_resume_uses_exec_resume_with_config_overrides(self):
        eng = self.engine(tools=("Write",), cwd=self.fake.dir, add_dirs=("/tmp",))
        self.run_ok(eng)
        self.fake.plan([{"lines": ok_lines("again", thread="t-1")}])
        answer = eng.ask("more", resume=True)
        argv = self.fake.calls()[-1]["argv"]
        self.assertEqual(argv[:3], ["exec", "resume", "t-1"])
        self.assertNotIn("-s", argv)
        self.assertNotIn("-C", argv)
        cfg = configs(argv)
        self.assertEqual(cfg["sandbox_mode"], '"workspace-write"')
        self.assertEqual(cfg["sandbox_workspace_write.writable_roots"], '["/tmp"]')
        self.assertEqual(answer.text, "again")

    def test_resume_a_named_session_and_fork(self):
        _, _, call = self.run_ok(resume="abc")
        self.assertEqual(call["argv"][:3], ["exec", "resume", "abc"])
        _, _, call = self.run_ok(self.engine(fork_session=True), resume="abc")
        self.assertEqual(call["argv"][:3], ["exec", "fork", "abc"])

    def test_extra_codex_config_pairs_are_passed(self):
        _, _, call = self.run_ok(self.engine(codex_config={"model_verbosity": "low"}))
        self.assertEqual(configs(call["argv"])["model_verbosity"], '"low"')

    def test_env_reaches_the_child(self):
        self.fake.plan([{"lines": ok_lines()}])
        self.engine(env={"SOME_VAR": "1"}).ask("x")
        self.assertTrue(self.fake.calls())

    def test_a_missing_executable_is_codex_not_found(self):
        with self.assertRaises(claude_engine.ClaudeNotFound):
            CodexEngine(CodexConfig(executable="/nope/codex")).ask("x")

    def test_a_plain_engine_config_runs_codex_not_claude(self):
        cfg = EngineConfig(tools=(), timeout=5)
        eng = CodexEngine(cfg)
        self.assertEqual(Path(eng._executable_name()).name, "codex")


class IgnoredFields(CodexCase):
    def test_fields_without_codex_meaning_are_accepted_and_warn_once(self):
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            eng = self.engine(betas=("x",), mcp_config=("m.json",), fallback_model="haiku")
            self.engine(betas=("x",))
        names = " ".join(str(w.message) for w in seen)
        self.assertIn("betas", names)
        self.assertIn("mcp_config", names)
        self.assertIn("fallback_model", names)
        self.assertEqual(sum("betas" in str(w.message) for w in seen), 1)
        self.fake.plan([{"lines": ok_lines()}])
        argv = (eng.ask("x"), self.fake.calls()[-1]["argv"])[1]
        self.assertNotIn("--betas", argv)

    def test_a_turn_cap_has_no_codex_meaning_and_says_so(self):
        # AUTO-116: codex exec has no turn limit to pass it to; the cap is
        # accepted, warned once, and the feature map says it is not supported.
        self.assertIn("max_turns", codex_engine.IGNORED_FIELDS)
        self.assertRegex(codex_engine.__doc__, r"\bmax_turns\b")   # not _budget_usd
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            eng = self.engine(max_turns=5)
        self.assertEqual(sum("max_turns" in str(w.message) for w in seen), 1)
        self.fake.plan([{"lines": ok_lines()}])
        eng.ask("x")
        self.assertFalse(any("turns" in a for a in self.fake.calls()[-1]["argv"]))

    def test_defaults_do_not_warn(self):
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            self.engine()
        self.assertEqual([str(w.message) for w in seen
                          if issubclass(w.category, codex_engine.CodexEngineWarning)], [])


# --------------------------------------------------------------------------- #
# structured output
# --------------------------------------------------------------------------- #

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"},
                                           "note": {"type": "string"}},
          "required": ["answer"]}


class Schema(CodexCase):
    def test_the_schema_is_made_strict_written_to_a_file_and_parsed(self):
        self.fake.plan([{"fixture": str(FIXTURES / "schema_ok.jsonl")}])
        eng = self.engine()
        data = eng.ask_json("Say ok", SCHEMA)
        call = self.fake.calls()[-1]
        self.assertEqual(data, {"answer": "ok"})        # the null optional dropped
        sent = call["schema"]
        self.assertFalse(sent["additionalProperties"])
        self.assertEqual(sorted(sent["required"]), ["answer", "note"])
        self.assertEqual(sent["properties"]["note"]["type"], ["string", "null"])
        path = flag(call["argv"], "--output-schema")
        self.assertFalse(Path(path).exists(), "the temp schema file is removed")

    def test_strict_conversion_reaches_nested_objects_and_arrays(self):
        schema = {"type": "object", "required": ["rows"], "properties": {
            "rows": {"type": "array", "items": {
                "type": "object", "required": ["id"],
                "properties": {"id": {"type": "integer", "default": 1},
                               "kind": {"enum": ["a", "b"]}}}}}}
        strict = codex_engine.strict_schema(schema)
        item = strict["properties"]["rows"]["items"]
        self.assertFalse(item["additionalProperties"])
        self.assertNotIn("default", item["properties"]["id"])
        self.assertIn(None, item["properties"]["kind"]["enum"])
        self.assertEqual(codex_engine.drop_nulls(
            {"rows": [{"id": 1, "kind": None}]}, schema), {"rows": [{"id": 1}]})

    def test_a_free_form_object_cannot_be_strict(self):
        self.assertIsNone(codex_engine.strict_schema(
            {"type": "object", "properties": {"m": {"type": "object"}}}))
        self.assertIsNone(codex_engine.strict_schema({"type": "array"}))

    def test_a_schema_that_cannot_be_strict_is_asked_in_the_prompt(self):
        loose = {"type": "object", "required": ["m"],
                 "properties": {"m": {"type": "object"}}}
        self.fake.plan([{"lines": ok_lines('```json\n{"m": {"k": 1}}\n```')}])
        data = self.engine().ask_json("Fill", loose)
        call = self.fake.calls()[-1]
        self.assertNotIn("--output-schema", call["argv"])
        self.assertIn('"required": ["m"]', call["stdin"])
        self.assertEqual(data, {"m": {"k": 1}})

    def test_a_schema_the_api_rejects_falls_back_to_the_prompt(self):
        self.fake.plan([{"fixture": str(FIXTURES / "schema_rejected.jsonl"), "exit": 1},
                        {"lines": ok_lines('{"answer": "ok"}')}])
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            data = self.engine().ask_json("Say ok", SCHEMA)
        calls = self.fake.calls()
        self.assertEqual(len(calls), 2)
        self.assertIn("--output-schema", calls[0]["argv"])
        self.assertNotIn("--output-schema", calls[1]["argv"])
        self.assertEqual(data, {"answer": "ok"})

    def test_missing_required_fields_are_an_error(self):
        loose = {"type": "object", "required": ["m"], "properties": {"m": {}}}
        self.fake.plan([{"lines": ok_lines('{"other": 1}')}])
        with self.assertRaises(ClaudeExecutionError) as caught:
            self.engine().ask_json("x", loose)
        self.assertIn("m", str(caught.exception))

    def test_an_answer_that_is_not_json_is_an_engine_error(self):
        self.fake.plan([{"lines": ok_lines("no json here")}])
        with self.assertRaises(claude_engine.ClaudeError):
            self.engine().ask_json("x", {"type": "object", "properties": {"m": {}}})


# --------------------------------------------------------------------------- #
# streaming events
# --------------------------------------------------------------------------- #

class Stream(CodexCase):
    def test_recorded_tool_events_map_onto_claude_events(self):
        self.fake.plan([{"fixture": str(FIXTURES / "agent_tools.jsonl")}])
        eng = self.engine(tools=("Read", "Write", "Bash"), model="gpt-5")
        events = list(eng.stream("do it"))
        kinds = [(e.type, e.subtype) for e in events]
        self.assertEqual(kinds[0], ("system", "init"))
        uses = [e for e in events if e.subtype == "tool_use"]
        self.assertEqual([u.tool_name for u in uses], ["Bash", "Write"])
        self.assertEqual(uses[0].tool_input["command"], "/bin/bash -lc 'cat hello.txt'")
        self.assertEqual(uses[1].tool_input["file_path"], "/ws/out.txt")
        results = [e for e in events if e.subtype == "tool_result"]
        self.assertEqual(results[0].type, "user")
        self.assertEqual(results[0].tool_result, "hello codex\n")
        texts = [e.text for e in events if e.type == "assistant" and e.text]
        self.assertEqual(texts[-1], "done")
        self.assertEqual(events[-1].type, "result")
        self.assertEqual(events[-1].text, "done")
        self.assertEqual(eng.session_id, "01a15656-1e2e-7360-a2d9-d2df38aade41")
        self.assertTrue(all(isinstance(e, Event) for e in events))

    def test_agent_returns_the_last_message_and_usage(self):
        self.fake.plan([{"fixture": str(FIXTURES / "agent_tools.jsonl")}])
        eng = self.engine(tools=("Bash",), model="gpt-5")
        answer = eng.agent("do it")
        self.assertEqual(answer.text, "done")
        self.assertEqual(answer.usage.output_tokens, 138)
        self.assertEqual(eng.usage.calls, 1)

    def test_a_tool_completed_without_a_start_still_yields_its_use(self):
        lines = ok_lines()[:2] + [
            {"type": "item.completed", "item": {"id": "i1", "type": "command_execution",
                                                "command": "ls", "aggregated_output": "a",
                                                "exit_code": 0, "status": "completed"}},
            {"type": "item.completed", "item": {"id": "i2", "type": "reasoning",
                                                "text": "hmm"}},
            {"type": "item.started", "item": {"id": "i3", "type": "mcp_tool_call",
                                              "server": "s", "tool": "t",
                                              "arguments": {"a": 1}}},
            {"type": "item.started", "item": {"id": "i4", "type": "web_search",
                                              "query": "q"}},
        ] + ok_lines()[2:]
        self.fake.plan([{"lines": lines}])
        events = list(self.engine().stream("x"))
        uses = [(e.tool_name, e.tool_input) for e in events if e.subtype == "tool_use"]
        self.assertEqual(uses, [("Bash", {"command": "ls"}), ("mcp__s__t", {"a": 1}),
                                ("WebSearch", {"query": "q"})])
        self.assertEqual([e.thinking for e in events if e.subtype == "thinking"], ["hmm"])

    def test_on_event_sees_every_event(self):
        self.fake.plan([{"lines": ok_lines()}])
        seen = []
        list(self.engine().stream("x", on_event=seen.append))
        self.assertEqual(seen[-1].type, "result")

    def test_a_failed_turn_raises(self):
        self.fake.plan([{"lines": ok_lines()[:2] + [
            {"type": "turn.failed", "error": {"message": "invalid_request_error: bad"}}],
            "exit": 1}])
        with self.assertRaises(ClaudeExecutionError) as caught:
            list(self.engine().stream("x"))
        self.assertFalse(caught.exception.transient)


# --------------------------------------------------------------------------- #
# usage, cost, budget
# --------------------------------------------------------------------------- #

class Accounting(CodexCase):
    def test_tokens_and_priced_cost(self):
        eng, answer, _ = self.run_ok(self.engine(model="gpt-5"))
        p = codex_engine.PRICES["gpt-5"]
        want = (600 * p["input"] + 400 * p["cached_input"] + 50 * p["output"]) / 1e6
        self.assertAlmostEqual(answer.usage.cost_usd, want)
        self.assertAlmostEqual(eng.usage.cost_usd, want)
        self.assertEqual(eng.usage.input_tokens, 600)
        self.assertEqual(eng.usage.cache_read_tokens, 400)
        self.assertEqual(eng.usage.output_tokens, 50)
        self.assertEqual(answer.model, "gpt-5")
        row = eng.usage.by_model["gpt-5"]
        self.assertEqual(row["calls"], 1)
        self.assertAlmostEqual(row["cost_usd"], want)
        self.assertEqual(eng.log[-1]["usage"]["reasoning_output_tokens"], 10)

    def test_the_recorded_usage_is_read(self):
        self.fake.plan([{"fixture": str(FIXTURES / "ask_developer_instructions.jsonl")}])
        eng = self.engine(model="gpt-5")
        answer = eng.ask("reply with the word ok")
        self.assertEqual(answer.text, "BANANA")
        self.assertEqual(eng.usage.input_tokens + eng.usage.cache_read_tokens, 14223)
        self.assertEqual(eng.usage.cache_read_tokens, 12288)
        self.assertEqual(eng.usage.output_tokens, 6)

    def test_an_unknown_model_records_tokens_and_no_cost(self):
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            eng, answer, _ = self.run_ok(self.engine(model="gpt-unknown"))
        self.assertIsNone(eng.log[-1]["total_cost_usd"])
        self.assertEqual(eng.usage.cost_usd, 0.0)
        self.assertEqual(eng.usage.output_tokens, 50)
        self.assertIn("gpt-unknown", eng.usage.by_model)
        self.assertTrue(any("gpt-unknown" in str(w.message) for w in seen))
        self.assertEqual(eng.unpriced_calls, 1)

    def test_prices_are_overridable_by_env_and_config(self):
        price = {"input": 1.0, "cached_input": 0.0, "output": 0.0}
        with mock.patch.dict(os.environ, {"CODEX_PRICES": json.dumps({"gpt-z": price})}):
            eng, _, _ = self.run_ok(self.engine(model="gpt-z"))
        self.assertAlmostEqual(eng.usage.cost_usd, 600 / 1e6)
        eng, _, _ = self.run_ok(self.engine(model="gpt-z", prices={"gpt-z": price}))
        self.assertAlmostEqual(eng.usage.cost_usd, 600 / 1e6)

    def test_the_default_model_is_read_from_codex_home(self):
        (self.fake.home / "config.toml").write_text('model = "gpt-5-mini"\n')
        eng, answer, call = self.run_ok(self.engine())
        self.assertNotIn("-m", call["argv"])
        self.assertEqual(answer.model, "gpt-5-mini")
        self.assertGreater(eng.usage.cost_usd, 0)

    def test_budget_stops_the_process_and_raises_the_budget_error(self):
        heavy = {"input_tokens": 10_000_000, "cached_input_tokens": 0,
                 "cache_write_input_tokens": 0, "output_tokens": 0,
                 "reasoning_output_tokens": 0}
        self.fake.plan([{"lines": ok_lines(usage=heavy), "hang": 30}])
        eng = self.engine(model="gpt-5", max_turns_budget_usd=0.5)
        started = time.monotonic()
        with self.assertRaises(ClaudeExecutionError) as caught:
            list(eng.stream("x"))
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn("error_max_budget_usd", str(caught.exception))
        self.assertFalse(caught.exception.transient)
        self.assertGreater(eng.usage.cost_usd, 0.5)         # the spend is still counted

    def test_budget_in_ask_raises_too(self):
        heavy = {"input_tokens": 10_000_000, "output_tokens": 0}
        self.fake.plan([{"lines": ok_lines(usage=heavy)}])
        with self.assertRaises(ClaudeExecutionError):
            self.engine(model="gpt-5", max_turns_budget_usd=0.5, max_retries=2).ask("x")
        self.assertEqual(len(self.fake.calls()), 1, "a budget stop is never retried")


# --------------------------------------------------------------------------- #
# failures, retries, timeout
# --------------------------------------------------------------------------- #

class Failures(CodexCase):
    def test_a_transient_failure_is_retried(self):
        self.fake.plan([{"stderr": "stream disconnected", "exit": 1},
                        {"lines": ok_lines("second")}])
        answer = self.engine(max_retries=2).ask("x")
        self.assertEqual(answer.text, "second")
        self.assertEqual(len(self.fake.calls()), 2)

    def test_a_permanent_failure_is_not_retried(self):
        self.fake.plan([{"stderr": "error: unexpected argument '--nope'", "exit": 2}])
        with self.assertRaises(ClaudeExecutionError):
            self.engine(max_retries=3).ask("x")
        self.assertEqual(len(self.fake.calls()), 1)

    def test_is_transient_knows_codex_permanent_errors(self):
        self.assertFalse(codex_engine.is_transient("code: invalid_json_schema"))
        self.assertFalse(codex_engine.is_transient("usage_not_included"))
        self.assertTrue(codex_engine.is_transient("stream disconnected before completion"))

    def test_a_stream_is_not_retried_once_content_was_delivered(self):
        self.fake.plan([{"lines": ok_lines()[:3], "exit": 1},
                        {"lines": ok_lines()}])
        with self.assertRaises(ClaudeExecutionError):
            list(self.engine(max_retries=2).stream("x"))
        self.assertEqual(len(self.fake.calls()), 1)

    def test_a_stream_before_content_is_retried(self):
        self.fake.plan([{"lines": ok_lines()[:2], "exit": 1}, {"lines": ok_lines("b")}])
        events = list(self.engine(max_retries=1).stream("x"))
        self.assertEqual(events[-1].text, "b")
        self.assertEqual(sum(e.subtype == "init" for e in events), 1)

    def test_timeout(self):
        self.fake.plan([{"lines": ok_lines()[:1], "hang": 30}])
        started = time.monotonic()
        with self.assertRaises(ClaudeExecutionError) as caught:
            self.engine(timeout=1.0).ask("x")
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn("timed out", str(caught.exception))
        self.assertTrue(caught.exception.transient)

    def test_no_turn_completed_is_an_error(self):
        self.fake.plan([{"lines": ok_lines()[:3]}])
        with self.assertRaises(ClaudeExecutionError):
            self.engine().ask("x")


# --------------------------------------------------------------------------- #
# the rest of the ClaudeEngine API
# --------------------------------------------------------------------------- #

class Api(CodexCase):
    def test_spawn_absorb_reset_log(self):
        eng = self.engine(model="gpt-5")
        worker = eng.spawn()
        self.assertIsInstance(worker, CodexEngine)
        self.fake.plan([{"lines": ok_lines()}])
        worker.ask("x")
        eng.absorb(worker)
        self.assertEqual(eng.usage.calls, 1)
        self.assertEqual(len(eng.log), 1)
        eng.session_id = "s"
        eng.reset()
        self.assertIsNone(eng.session_id)

    def test_task_runner_runs_on_codex(self):
        self.fake.plan([{"lines": ok_lines("one")}, {"lines": ok_lines("two")}])
        eng = self.engine(model="gpt-5")
        runner = codex_engine.TaskRunner(eng, jobs=2)
        results = runner.run([codex_engine.Task("a", "first"),
                              codex_engine.Task("b", "second")])
        self.assertTrue(all(r.ok for r in results.values()))
        self.assertEqual(eng.usage.calls, 2)

    def test_the_cli_asks(self):
        self.fake.plan([{"lines": ok_lines("from cli")}])
        env = dict(os.environ, **self.fake.env())
        out = subprocess.run([sys.executable, str(ROOT / "codex_engine.py"),
                              "--executable", str(self.fake.exe), "--model", "gpt-5",
                              "ask", "hi"], capture_output=True, text=True, env=env,
                             timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "from cli")
        self.fake.plan([{"lines": ok_lines('{"answer": "ok", "note": null}')}])
        schema = self.fake.dir / "s.json"
        schema.write_text(json.dumps(SCHEMA))
        out = subprocess.run([sys.executable, str(ROOT / "codex_engine.py"),
                              "--executable", str(self.fake.exe), "json", "hi",
                              "--schema", str(schema)], capture_output=True, text=True,
                             env=env, timeout=60)
        self.assertEqual(json.loads(out.stdout), {"answer": "ok"}, out.stderr)
        self.fake.plan([{"fixture": str(FIXTURES / "agent_tools.jsonl")}])
        out = subprocess.run([sys.executable, str(ROOT / "codex_engine.py"),
                              "--executable", str(self.fake.exe), "agent", "do",
                              "--write"], capture_output=True, text=True, env=env,
                             timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("done", out.stdout)
        self.assertEqual(flag(self.fake.calls()[-1]["argv"], "-s"), "workspace-write")


# --------------------------------------------------------------------------- #
# the harness seam
# --------------------------------------------------------------------------- #

class HarnessSeam(CodexCase):
    def setUp(self):
        super().setUp()
        from ytstudio import harness
        self.harness = harness
        self._saved = harness.ENGINE, harness.ENGINE_NAME

    def tearDown(self):
        self.harness.ENGINE, self.harness.ENGINE_NAME = self._saved
        super().tearDown()

    def _probe(self, env):
        code = ("from ytstudio import harness; "
                "print(harness.ENGINE.__module__, harness.ENGINE.__name__, harness.ENGINE_NAME)")
        full = {k: v for k, v in os.environ.items() if k != "YT_ENGINE"}
        full.update(env)
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=full,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.split()

    def test_yt_engine_codex_swaps_the_engine_and_the_default_is_unchanged(self):
        self.assertEqual(self._probe({}), ["claude_engine", "ClaudeEngine", "claude"])
        self.assertEqual(self._probe({"YT_ENGINE": "codex"}),
                         ["codex_engine", "CodexEngine", "codex"])

    def test_use_engine(self):
        self.harness.use_engine("codex")
        self.assertIs(self.harness.ENGINE, CodexEngine)
        self.harness.use_engine("claude")
        self.assertIs(self.harness.ENGINE, claude_engine.ClaudeEngine)
        with self.assertRaises(self.harness.HarnessError):
            self.harness.use_engine("nope")

    def test_the_default_digest_is_unchanged(self):
        h = self.harness.Harness(root=self.fake.dir, stage="s", verbose=False)
        self.assertEqual(h._asked_as, "sonnet")
        self.harness.use_engine("codex")
        self.assertEqual(h._asked_as, "sonnet~codex")

    def _codex(self):
        (self.fake.home / "config.toml").write_text('model = "gpt-5"\n')
        self.harness.use_engine("codex")
        env = dict(self.fake.env(), CODEX_ENGINE_EXECUTABLE=str(self.fake.exe))
        return mock.patch.dict(os.environ, env)

    def test_decide_round_trip(self):
        self.fake.plan([{"lines": ok_lines('{"word": "ok"}')}])
        schema = {"type": "object", "properties": {"word": {"type": "string"}},
                  "required": ["word"]}
        root = self.fake.dir / "project"
        with self._codex(), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            h = self.harness.Harness(root=root, stage="probe", verbose=False)
            result = h.decide("ping", "say ok", schema)
            again = h.decide("ping", "say ok", schema)
        self.assertEqual(result, {"word": "ok"})
        self.assertEqual(again, result)
        self.assertEqual(len(self.fake.calls()), 1, "the second answer came from disk")
        call = self.fake.calls()[0]
        self.assertEqual(flag(call["argv"], "-s"), "read-only")
        self.assertIn("developer_instructions", configs(call["argv"]))
        saved = json.loads((root / "decisions" / "probe" / "ping.json").read_text())
        self.assertGreater(saved["cost_usd"], 0)
        self.assertEqual(h.report()["calls"], 1)
        self.assertIn("gpt-5", h.report()["by_model"])

    def test_inspect_round_trip(self):
        lines = ok_lines()[:2] + [
            {"type": "item.started", "item": {"id": "i1", "type": "command_execution",
                                              "command": "cat a.txt", "status": "in_progress"}},
            {"type": "item.completed", "item": {"id": "i1", "type": "command_execution",
                                                "command": "cat a.txt",
                                                "aggregated_output": "A", "exit_code": 0}},
            {"type": "item.completed", "item": {"id": "i2", "type": "agent_message",
                                                "text": 'Seen.\n```json\n{"seen": "A"}\n```'}},
        ] + ok_lines()[3:]
        self.fake.plan([{"lines": lines}])
        evidence = self.fake.dir / "evidence"
        evidence.mkdir()
        (evidence / "a.txt").write_text("A")
        schema = {"type": "object", "properties": {"seen": {"type": "string"}},
                  "required": ["seen"]}
        with self._codex(), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            h = self.harness.Harness(root=self.fake.dir / "project", stage="probe",
                                     verbose=False)
            tools = []
            result = h.inspect("look", "Read a.txt", schema, cwd=evidence,
                               on_tool=lambda n, w: tools.append(n))
        self.assertEqual(result, {"seen": "A"})
        self.assertEqual(tools, ["Bash"])
        call = self.fake.calls()[0]
        self.assertEqual(flag(call["argv"], "-s"), "read-only")
        self.assertEqual(flag(call["argv"], "-C"), str(evidence))
        self.assertEqual(h.decisions[-1].tool_calls, 1)


if __name__ == "__main__":
    unittest.main()
