"""Vercel AI Gateway for the claude CLI (AUTO-118): opt-in, and invisible when off.

`YT_GATEWAY=vercel` sends ClaudeEngine's calls through the gateway's Claude
Code endpoint; with it unset the child's argv and environment are what they
were before the gateway existed. No model, no network: a fake `claude`
records the environment it was launched with.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import claude_engine  # noqa: E402
from claude_engine import ClaudeEngine, ClaudeExecutionError, EngineConfig  # noqa: E402
from codex_engine import CodexConfig, CodexEngine  # noqa: E402
from ytstudio import gateway, harness  # noqa: E402

KEY = "vck_test_secret_0123456789"
HOST = "https://ai-gateway.vercel.sh"

#: Every variable the gateway reads or writes, cleared from the parent env so
#: the machine running the tests cannot leak into them.
GATEWAY_VARS = ("YT_GATEWAY", "YT_GATEWAY_AUTH", "YT_GATEWAY_URL", "AI_GATEWAY_API_KEY",
                "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
                "ANTHROPIC_CUSTOM_HEADERS")

FAKE_CLAUDE = r'''#!/usr/bin/env python3
"""A stand-in for `claude -p` that records its argv and environment, then
prints one successful result (stream-json when asked for it)."""
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "env": dict(os.environ)}) + "\n")
sys.stdin.read()
result = {"type": "result", "subtype": "success", "is_error": False, "result": "ok",
          "session_id": "s-fake", "num_turns": 1, "total_cost_usd": 0.0,
          "usage": {"input_tokens": 1, "output_tokens": 1}, "duration_ms": 1}
if "stream-json" in sys.argv:
    print(json.dumps({"type": "system", "subtype": "init", "session_id": "s-fake"}))
print(json.dumps(result))
'''


def on(**extra: str) -> dict[str, str]:
    """The environment of an operator who switched the gateway on with a key."""
    return {"YT_GATEWAY": "vercel", "AI_GATEWAY_API_KEY": KEY, **extra}


class GatewayCase(unittest.TestCase):
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
        self._patch = mock.patch.dict(os.environ, {"FAKE_LOG": str(self.log)})
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()
        os.environ.update(self._saved)
        claude_engine.DEFAULTS.clear()

    def engine(self, **kw) -> ClaudeEngine:
        kw.setdefault("max_retries", 0)
        kw.setdefault("retry_backoff", 0.0)
        kw.setdefault("timeout", 10.0)
        return ClaudeEngine(EngineConfig(executable=str(self.exe), **kw))

    def launches(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(l) for l in self.log.read_text().splitlines()]

    def child_env(self, **kw) -> dict[str, str]:
        """The environment the one launch of `ask` handed the fake claude."""
        self.engine(**kw).ask("hi")
        (launch,) = self.launches()
        return launch["env"]


class Overlay(GatewayCase):
    """The pure part: which variables the switch sets, from which inputs."""

    def test_off_unless_the_switch_names_vercel(self):
        self.assertFalse(gateway.active({}))
        self.assertEqual(gateway.claude_env({}), {})
        self.assertTrue(gateway.active(on()))
        self.assertTrue(gateway.active({"YT_GATEWAY": " Vercel "}))

    def test_a_key_alone_does_not_turn_it_on(self):
        env = {"AI_GATEWAY_API_KEY": KEY, "YT_GATEWAY_AUTH": "subscription",
               "YT_GATEWAY_URL": "https://gw.example"}
        self.assertFalse(gateway.active(env))
        self.assertEqual(gateway.claude_env(env), {})

    def test_key_mode_sets_the_three_variables(self):
        self.assertEqual(gateway.claude_env(on()), {
            "ANTHROPIC_BASE_URL": HOST + "/claude-code",
            "ANTHROPIC_AUTH_TOKEN": KEY,
            "ANTHROPIC_API_KEY": "",
        })

    def test_gateway_url_has_no_v1(self):
        # The CLI's SDK appends /v1/messages itself: a /v1 here is a 404.
        url = gateway.claude_env(on())["ANTHROPIC_BASE_URL"]
        self.assertFalse(url.rstrip("/").endswith("/v1"), url)
        self.assertNotIn("/v1", url)

    def test_the_url_variable_replaces_the_host_only(self):
        env = on(YT_GATEWAY_URL="https://gw.example/")
        self.assertEqual(gateway.claude_env(env)["ANTHROPIC_BASE_URL"],
                         "https://gw.example/claude-code")

    def test_a_url_that_already_names_a_path_is_refused(self):
        for bad in ("https://ai-gateway.vercel.sh/v1", "https://ai-gateway.vercel.sh/claude-code",
                    "ai-gateway.vercel.sh", "ftp://ai-gateway.vercel.sh"):
            with self.subTest(url=bad), self.assertRaises(gateway.GatewayError):
                gateway.claude_env(on(YT_GATEWAY_URL=bad))

    def test_subscription_mode_sets_the_header_and_the_url_only(self):
        overlay = gateway.claude_env(on(YT_GATEWAY_AUTH="subscription"))
        self.assertEqual(overlay, {
            "ANTHROPIC_BASE_URL": HOST + "/claude-code",
            "ANTHROPIC_CUSTOM_HEADERS": f"x-ai-gateway-api-key: Bearer {KEY}",
        })

    def test_subscription_mode_keeps_the_operators_other_headers(self):
        env = on(YT_GATEWAY_AUTH="subscription",
                 ANTHROPIC_CUSTOM_HEADERS="x-team: video\nx-ai-gateway-api-key: Bearer old")
        headers = gateway.claude_env(env)["ANTHROPIC_CUSTOM_HEADERS"].split("\n")
        self.assertEqual(headers, ["x-team: video", f"x-ai-gateway-api-key: Bearer {KEY}"])

    def test_a_switch_that_names_no_known_gateway_is_refused(self):
        # A typo must not quietly mean "direct": the operator asked for a gateway.
        with self.assertRaises(gateway.GatewayError) as caught:
            gateway.claude_env({"YT_GATEWAY": "vercell", "AI_GATEWAY_API_KEY": KEY})
        self.assertIn("vercel", str(caught.exception))

    def test_an_unknown_auth_mode_is_refused(self):
        with self.assertRaises(gateway.GatewayError):
            gateway.claude_env(on(YT_GATEWAY_AUTH="oauth"))

    def test_on_without_a_key_is_refused_in_both_modes(self):
        for extra in ({}, {"YT_GATEWAY_AUTH": "subscription"}):
            env = {"YT_GATEWAY": "vercel", "AI_GATEWAY_API_KEY": "  ", **extra}
            with self.subTest(**extra), self.assertRaises(gateway.GatewayError) as caught:
                gateway.claude_env(env)
            self.assertIn("AI_GATEWAY_API_KEY", str(caught.exception))

    def test_no_error_carries_the_key(self):
        for env in (on(YT_GATEWAY_URL="https://x/v1"), on(YT_GATEWAY_AUTH="nope"),
                    {"YT_GATEWAY": "other", "AI_GATEWAY_API_KEY": KEY}):
            with self.subTest(env=sorted(env)):
                with self.assertRaises(gateway.GatewayError) as caught:
                    gateway.claude_env(env)
                self.assertNotIn(KEY, str(caught.exception))

    def test_the_parent_environment_is_the_default_input(self):
        with mock.patch.dict(os.environ, on()):
            self.assertTrue(gateway.active())
            self.assertEqual(gateway.claude_env()["ANTHROPIC_AUTH_TOKEN"], KEY)


class Off(GatewayCase):
    """With YT_GATEWAY unset nothing about a launch changes."""

    def test_gateway_off_env_identical(self):
        with mock.patch.dict(os.environ, {"AI_GATEWAY_API_KEY": KEY,
                                          "ANTHROPIC_API_KEY": "sk-ant-direct"}):
            eng = self.engine(env={"EXTRA": "1"})
            # Today's recipe, spelled out: the parent, then config.env, then CI.
            expected = dict(os.environ)
            expected.update({"EXTRA": "1"})
            expected.setdefault("CI", "1")
            self.assertEqual(eng._popen_kwargs()["env"], expected)
            eng.ask("hi")
        env = self.launches()[0]["env"]
        self.assertEqual(env["ANTHROPIC_API_KEY"], "sk-ant-direct")
        for name in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_CUSTOM_HEADERS"):
            self.assertNotIn(name, env)

    def test_gateway_off_argv_identical(self):
        cfg = EngineConfig(executable=str(self.exe), model="opus", effort="high",
                           betas=("b1",), bare=True, tools=())
        kw = dict(output_format="json", resume=False, tools=..., system=None,
                  schema={"type": "object"})
        argv = ClaudeEngine(cfg)._build_argv(**kw)
        self.assertEqual(argv, [
            str(self.exe), "-p", "--output-format", "json", "--model", "opus",
            "--effort", "high", "--betas", "b1", "--tools", "",
            "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--strict-mcp-config", "--bare", "--json-schema", '{"type": "object"}'])
        with mock.patch.dict(os.environ, on()):
            self.assertEqual(ClaudeEngine(cfg)._build_argv(**kw), argv)

    def test_the_decision_cache_key_ignores_the_gateway(self):
        # A decision asked directly and one asked through the gateway are the
        # same question of the same model: switching it on re-asks nothing.
        with tempfile.TemporaryDirectory() as tmp:
            direct = harness.Harness(Path(tmp), "build", model="sonnet")._asked_as
            with mock.patch.dict(os.environ, on()):
                routed = harness.Harness(Path(tmp), "build", model="sonnet")._asked_as
        self.assertEqual(direct, "sonnet")
        self.assertEqual(routed, direct)


class On(GatewayCase):
    """With YT_GATEWAY=vercel the child gets the gateway's variables."""

    def test_gateway_on_sets_claude_env(self):
        with mock.patch.dict(os.environ, on(ANTHROPIC_API_KEY="sk-ant-direct")):
            env = self.child_env()
        self.assertEqual(env["ANTHROPIC_BASE_URL"], HOST + "/claude-code")
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], KEY)
        # Claude Code takes a non-empty ANTHROPIC_API_KEY first: it must be empty.
        self.assertEqual(env["ANTHROPIC_API_KEY"], "")
        self.assertEqual(env["CI"], "1")

    def test_the_stream_path_gets_the_same_variables(self):
        with mock.patch.dict(os.environ, on(ANTHROPIC_API_KEY="sk-ant-direct")):
            events = list(self.engine().stream("hi"))
        self.assertEqual(events[-1].type, "result")
        env = self.launches()[0]["env"]
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], KEY)
        self.assertEqual(env["ANTHROPIC_API_KEY"], "")

    def test_config_env_overrides_gateway(self):
        with mock.patch.dict(os.environ, on()):
            env = self.child_env(env={"ANTHROPIC_BASE_URL": "https://proxy.example",
                                      "ANTHROPIC_API_KEY": "sk-explicit"})
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://proxy.example")
        self.assertEqual(env["ANTHROPIC_API_KEY"], "sk-explicit")
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], KEY)

    def test_config_env_can_switch_it_on(self):
        # A library caller who sets the switch on the engine, not the shell.
        env = self.child_env(env=on())
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], KEY)
        self.assertEqual(env["ANTHROPIC_API_KEY"], "")

    def test_subscription_mode_sets_custom_header_only(self):
        parent = on(YT_GATEWAY_AUTH="subscription", ANTHROPIC_API_KEY="sk-ant-direct")
        with mock.patch.dict(os.environ, parent):
            env = self.child_env()
        self.assertEqual(env["ANTHROPIC_BASE_URL"], HOST + "/claude-code")
        self.assertEqual(env["ANTHROPIC_CUSTOM_HEADERS"], f"x-ai-gateway-api-key: Bearer {KEY}")
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        # The login is left alone, ANTHROPIC_API_KEY included.
        self.assertEqual(env["ANTHROPIC_API_KEY"], "sk-ant-direct")

    def test_betas_pass_through_unchanged(self):
        with mock.patch.dict(os.environ, on()):
            self.engine(betas=("context-1m-2025-08-07",)).ask("hi")
        argv = self.launches()[0]["argv"]
        self.assertEqual(argv[argv.index("--betas") + 1], "context-1m-2025-08-07")

    def test_the_key_is_never_on_the_command_line(self):
        with mock.patch.dict(os.environ, on()):
            self.engine(model="opus").ask("hi", schema={"type": "object"})
        self.assertNotIn(KEY, " ".join(self.launches()[0]["argv"]))


class Refused(GatewayCase):
    """A gateway that cannot be used fails at once, before any launch."""

    def assert_refused_without_launch(self, call) -> ClaudeExecutionError:
        with self.assertRaises(ClaudeExecutionError) as caught:
            call()
        self.assertFalse(caught.exception.transient)
        # A count, not the list: a launch record holds the whole environment.
        self.assertEqual(len(self.launches()), 0, "no subprocess may start")
        self.assertNotIn(KEY, str(caught.exception))
        return caught.exception

    def test_gateway_on_without_key_raises_permanent(self):
        with mock.patch.dict(os.environ, {"YT_GATEWAY": "vercel"}):
            exc = self.assert_refused_without_launch(
                lambda: self.engine(max_retries=2).ask("hi"))
            self.assertIn("AI_GATEWAY_API_KEY", str(exc))
            self.assert_refused_without_launch(
                lambda: list(self.engine(max_retries=2).stream("hi")))

    def test_bare_with_gateway_key_mode(self):
        # --bare authenticates with ANTHROPIC_API_KEY only, which key mode
        # must leave empty: refused until a live check shows --bare honours
        # ANTHROPIC_AUTH_TOKEN.
        with mock.patch.dict(os.environ, on()):
            exc = self.assert_refused_without_launch(
                lambda: self.engine(bare=True).ask("hi"))
        self.assertIn("bare", str(exc))

    def test_bare_without_the_gateway_still_launches(self):
        self.engine(bare=True).ask("hi")
        self.assertIn("--bare", self.launches()[0]["argv"])

    def test_a_bad_switch_value_is_permanent(self):
        with mock.patch.dict(os.environ, {"YT_GATEWAY": "on", "AI_GATEWAY_API_KEY": KEY}):
            self.assert_refused_without_launch(lambda: self.engine(max_retries=2).ask("hi"))


class Codex(GatewayCase):
    """The Claude Code variables are not codex's route: its child is untouched."""

    def test_the_codex_child_gets_no_claude_overlay(self):
        with mock.patch.dict(os.environ, {"YT_GATEWAY": "vercel",
                                          "ANTHROPIC_API_KEY": "sk-ant-direct"}):
            eng = CodexEngine(CodexConfig(executable="codex", env={"EXTRA": "1"}))
            expected = dict(os.environ)
            expected.update({"EXTRA": "1"})
            expected.setdefault("CI", "1")
            self.assertEqual(eng._popen_kwargs()["env"], expected)


if __name__ == "__main__":
    unittest.main()
