"""Vercel AI Gateway for the codex CLI (AUTO-119): opt-in, and invisible when off.

`YT_GATEWAY=vercel` makes CodexEngine name the gateway as Codex's model
provider with `-c` pairs (Vercel's manual setup for Codex: a provider whose
base_url is the gateway's /codex/v1, whose env_key is AI_GATEWAY_API_KEY and
whose wire_api is responses). Nothing is written to $CODEX_HOME. With the
switch unset the argv and the environment are what they were before. No
model, no network: a fake `codex` records the argv and the environment it was
launched with.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
import warnings
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import codex_engine                                    # noqa: E402
import test_codex_engine                               # noqa: E402
from claude_engine import ClaudeExecutionError         # noqa: E402
from codex_engine import CodexConfig, CodexEngine      # noqa: E402
from test_codex_engine import CodexCase, configs, flag, ok_lines   # noqa: E402
from ytstudio import gateway                           # noqa: E402

KEY = "vck_test_secret_0123456789"
HOST = "https://ai-gateway.vercel.sh"

#: Every variable the gateway reads, cleared from the parent env so the
#: machine running the tests cannot leak into them.
GATEWAY_VARS = ("YT_GATEWAY", "YT_GATEWAY_AUTH", "YT_GATEWAY_URL", "AI_GATEWAY_API_KEY")

#: test_codex_engine's fake, also recording the environment it was given.
FAKE = test_codex_engine.FAKE.replace(
    '"schema": schema}', '"schema": schema, "env": dict(os.environ)}')
assert FAKE != test_codex_engine.FAKE, "the fake's record line moved"

#: The pairs Vercel's Codex page gives for its manual setup, as -c arguments.
PROVIDER_ARGS = [
    'model_provider="vercel"',
    'model_providers.vercel.name="Vercel AI Gateway"',
    f'model_providers.vercel.base_url="{HOST}/codex/v1"',
    'model_providers.vercel.env_key="AI_GATEWAY_API_KEY"',
    'model_providers.vercel.wire_api="responses"',
]


def on(**extra: str) -> dict[str, str]:
    """The environment of an operator who switched the gateway on with a key."""
    return {"YT_GATEWAY": "vercel", "AI_GATEWAY_API_KEY": KEY, **extra}


def pairs(argv: list[str]) -> list[str]:
    """Every value that follows a -c, in order."""
    return [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]


class GatewayCodexCase(CodexCase):
    def setUp(self):
        super().setUp()
        self.fake.exe.write_text(FAKE)
        self._saved = {k: os.environ.pop(k) for k in list(os.environ) if k in GATEWAY_VARS}

    def tearDown(self):
        os.environ.update(self._saved)
        super().tearDown()

    def launch(self, engine=None, **ask) -> dict:
        """The one launch of an ask: its argv and environment."""
        self.fake.plan([{"lines": ok_lines()}])
        (engine or self.engine()).ask("hello", **ask)
        (call,) = self.fake.calls()
        return call


class Pairs(unittest.TestCase):
    """The pure part: which -c pairs the switch asks for, from which inputs."""

    def test_off_unless_the_switch_names_vercel(self):
        self.assertEqual(gateway.codex_config({}), {})
        self.assertEqual(gateway.codex_config({"AI_GATEWAY_API_KEY": KEY}), {})
        for word in ("off", "0", "false", "no", "none", " OFF "):
            with self.subTest(word=word):
                self.assertEqual(gateway.codex_config(on(YT_GATEWAY=word)), {})

    def test_the_provider_vercel_documents(self):
        self.assertEqual(gateway.codex_config(on()), {
            "model_provider": "vercel",
            "model_providers.vercel.name": "Vercel AI Gateway",
            "model_providers.vercel.base_url": HOST + "/codex/v1",
            "model_providers.vercel.env_key": "AI_GATEWAY_API_KEY",
            "model_providers.vercel.wire_api": "responses",
        })

    def test_the_key_travels_by_name_only(self):
        # Codex reads the key from the variable env_key names: the value
        # never goes on a command line, where `ps` would show it.
        self.assertNotIn(KEY, json.dumps(gateway.codex_config(on())))

    def test_the_url_variable_replaces_the_host_only(self):
        config = gateway.codex_config(on(YT_GATEWAY_URL="https://gw.example/"))
        self.assertEqual(config["model_providers.vercel.base_url"],
                         "https://gw.example/codex/v1")

    def test_a_url_that_already_names_the_codex_path_is_refused(self):
        for bad in (HOST + "/codex/v1", HOST + "/codex", HOST + "/codex/"):
            with self.subTest(url=bad), self.assertRaises(gateway.GatewayError):
                gateway.codex_config(on(YT_GATEWAY_URL=bad))

    def test_no_key_a_bad_key_or_an_unknown_switch_is_refused(self):
        for env in ({"YT_GATEWAY": "vercel"}, on(AI_GATEWAY_API_KEY="vck a"),
                    on(YT_GATEWAY="vercell")):
            with self.subTest(env=sorted(env)), self.assertRaises(gateway.GatewayError) as caught:
                gateway.codex_config(env)
            self.assertNotIn(KEY, str(caught.exception))

    def test_a_key_with_whitespace_around_it_is_refused_for_codex(self):
        # Codex reads the variable as it is, so a key that is only right
        # once stripped would pass a stripped check and fail at the gateway.
        for raw in (KEY + "\n", " " + KEY, KEY + " "):
            with self.subTest(raw=repr(raw)), self.assertRaises(gateway.GatewayError) as caught:
                gateway.codex_config(on(AI_GATEWAY_API_KEY=raw))
            self.assertIn("AI_GATEWAY_API_KEY", str(caught.exception))
            self.assertNotIn(KEY, str(caught.exception))

    def test_no_websocket_setting_is_sent(self):
        # Vercel says non-OpenAI models do not support Codex's WebSocket
        # mode; the provider is left on plain HTTP streaming.
        self.assertFalse([k for k in gateway.codex_config(on()) if "websocket" in k.lower()])


class Off(GatewayCodexCase):
    """With YT_GATEWAY unset, a Codex launch is the one it was before."""

    #: Today's argv for this config, spelled out (argv[0], the binary, aside).
    SNAPSHOT = [
        "exec", "--json", "--skip-git-repo-check", "-m", "gpt-5",
        "-c", 'model_reasoning_effort="high"', "-s", "read-only",
        "-c", 'developer_instructions="Be terse."', "--ephemeral",
        "-c", 'model_verbosity="low"', "-",
    ]

    def snapshot_engine(self):
        return self.engine(model="gpt-5", effort="high", tools=(), system_prompt="Be terse.",
                           persist_session=False, codex_config={"model_verbosity": "low"})

    def test_codex_gateway_off_argv_identical(self):
        self.assertEqual(self.launch(self.snapshot_engine())["argv"], self.SNAPSHOT)
        # A key alone, or the switch set to a word for off, changes nothing.
        for parent in ({"AI_GATEWAY_API_KEY": KEY}, on(YT_GATEWAY="off")):
            with self.subTest(parent=sorted(parent)), mock.patch.dict(os.environ, parent):
                self.assertEqual(self.launch(self.snapshot_engine())["argv"], self.SNAPSHOT)

    def test_codex_gateway_off_env_identical(self):
        with mock.patch.dict(os.environ, {"AI_GATEWAY_API_KEY": KEY}):
            eng = self.engine()
            expected = dict(os.environ)
            expected.update(self.fake.env())
            expected.setdefault("CI", "1")
            self.assertEqual(eng._popen_kwargs()["env"], expected)
            env = self.launch(eng)["env"]
        for name, value in expected.items():
            self.assertEqual(env.get(name), value, name)

    def test_a_bare_model_stays_bare_and_the_default_is_codexs(self):
        self.assertEqual(flag(self.launch(self.engine(model="o3"))["argv"], "-m"), "o3")
        (self.fake.home / "config.toml").write_text('model = "gpt-5-codex"\n')
        self.assertNotIn("-m", self.launch(self.engine())["argv"])


class On(GatewayCodexCase):
    """With YT_GATEWAY=vercel, codex is pointed at the gateway's provider."""

    def test_codex_gateway_on_emits_provider_pairs(self):
        with mock.patch.dict(os.environ, on()):
            call = self.launch(self.engine(model="gpt-5", tools=()))
        argv = call["argv"]
        got = pairs(argv)
        self.assertEqual([p for p in got if p.startswith("model_provider")], PROVIDER_ARGS)
        # Exactly once each, and only the pairs added: the rest is today's argv.
        for arg in PROVIDER_ARGS:
            self.assertEqual(got.count(arg), 1, arg)
        self.assertEqual(argv[-1], "-")
        self.assertNotIn(KEY, " ".join(argv))
        # The key reaches codex through the environment, under the name
        # env_key gives.
        self.assertEqual(call["env"]["AI_GATEWAY_API_KEY"], KEY)

    def test_the_switch_on_the_engines_env_turns_it_on(self):
        # A library caller who sets the switch on the engine, not the shell.
        call = self.launch(self.engine(env=on()))
        self.assertIn(PROVIDER_ARGS[0], pairs(call["argv"]))

    def test_user_codex_config_wins(self):
        mine = {"model_providers.vercel.base_url": "https://proxy.example/codex/v1",
                "model_verbosity": "low"}
        with mock.patch.dict(os.environ, on()):
            argv = self.launch(self.engine(codex_config=mine))["argv"]
        cfg = configs(argv)
        self.assertEqual(cfg["model_providers.vercel.base_url"],
                         '"https://proxy.example/codex/v1"')
        self.assertEqual(cfg["model_verbosity"], '"low"')
        self.assertEqual(cfg["model_provider"], '"vercel"')
        # Once: the user's pair replaces the gateway's, it does not follow it.
        self.assertEqual(sum(p.startswith("model_providers.vercel.base_url=")
                             for p in pairs(argv)), 1)

    def test_the_users_own_provider_wins_too(self):
        with mock.patch.dict(os.environ, on()):
            cfg = configs(self.launch(self.engine(codex_config={"model_provider": "mine"}))["argv"])
        self.assertEqual(cfg["model_provider"], '"mine"')

    def test_bare_openai_model_prefixed_under_gateway(self):
        cases = {"gpt-5": "openai/gpt-5", "o3": "openai/o3",
                 "gpt-5.1-codex": "openai/gpt-5.1-codex",
                 "openai/gpt-5": "openai/gpt-5",
                 "google/gemini-3-pro": "google/gemini-3-pro"}
        with mock.patch.dict(os.environ, on()):
            for given, sent in cases.items():
                with self.subTest(model=given):
                    self.assertEqual(flag(self.launch(self.engine(model=given))["argv"], "-m"),
                                     sent)

    def test_codexs_own_default_is_named_and_prefixed_under_gateway(self):
        # Left unnamed, codex would send its default's bare slug to the
        # gateway, which is not documented to take one.
        (self.fake.home / "config.toml").write_text('model = "gpt-5-codex"\n')
        with mock.patch.dict(os.environ, on()):
            eng = self.engine(model="sonnet")      # a Claude alias: dropped
            self.assertEqual(flag(self.launch(eng)["argv"], "-m"), "openai/gpt-5-codex")
            self.assertEqual(eng.resolved_model(), "openai/gpt-5-codex")

    def test_no_known_default_passes_no_model(self):
        with mock.patch.dict(os.environ, on()):
            self.assertNotIn("-m", self.launch(self.engine())["argv"])

    def test_claude_ids_are_never_passed_to_codex(self):
        for given in ("anthropic/claude-opus-5", "claude-code/anthropic/claude-opus-5",
                      "opus", "claude-opus-5-5"):
            with self.subTest(model=given), mock.patch.dict(os.environ, on()):
                argv = self.launch(self.engine(model=given))["argv"]
                self.assertNotIn("-m", argv)
        with mock.patch.dict(os.environ, on(CODEX_ENGINE_MODEL="anthropic/claude-opus-5")):
            self.assertNotIn("-m", self.launch(self.engine(model="opus"))["argv"])

    def test_a_prefixed_model_is_priced_as_its_direct_name(self):
        with mock.patch.dict(os.environ, on()):
            eng = self.engine(model="gpt-5")
            self.launch(eng)
        self.assertIn("openai/gpt-5", eng.usage.by_model)
        self.assertGreater(eng.usage.cost_usd, 0.0)
        self.assertEqual(eng.unpriced_calls, 0)


class Effort(GatewayCodexCase):
    def cache(self, slug: str) -> None:
        (self.fake.home / "models_cache.json").write_text(json.dumps({"models": [
            {"slug": slug, "priority": 1, "visibility": "list",
             "supported_reasoning_levels": [{"effort": e} for e in
                                            ("low", "medium", "high", "xhigh")]}]}))

    def effort(self, **kw) -> str:
        return configs(self.launch(self.engine(**kw))["argv"])["model_reasoning_effort"]

    def test_effort_lookup_tries_both_forms(self):
        self.cache("openai/gpt-x")
        self.assertEqual(self.effort(model="gpt-x", effort="xhigh"), '"xhigh"')
        self.assertEqual(self.effort(model="openai/gpt-x", effort="xhigh"), '"xhigh"')
        self.cache("gpt-y")
        self.assertEqual(self.effort(model="openai/gpt-y", effort="xhigh"), '"xhigh"')
        with mock.patch.dict(os.environ, on()):
            # Under the gateway gpt-y is sent as openai/gpt-y; the cache's
            # bare slug still names its efforts.
            self.assertEqual(self.effort(model="gpt-y", effort="xhigh"), '"xhigh"')

    def test_an_unlisted_effort_is_lowered_with_one_warning(self):
        self.cache("gpt-y")
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            self.assertEqual(self.effort(model="gpt-z", effort="xhigh"), '"high"')
            self.assertEqual(self.effort(model="gpt-z", effort="xhigh"), '"high"')
        hits = [str(w.message) for w in seen
                if issubclass(w.category, codex_engine.CodexEngineWarning)
                and "xhigh" in str(w.message)]
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("gpt-z", hits[0])
        self.assertIn("high", hits[0])

    def test_a_base_effort_never_warns(self):
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            self.assertEqual(self.effort(model="gpt-z", effort="medium"), '"medium"')
        self.assertFalse([w for w in seen if "effort" in str(w.message)])


class Refused(GatewayCodexCase):
    """A gateway that cannot be used fails at once, before any launch."""

    def assert_refused_without_launch(self, call) -> ClaudeExecutionError:
        with self.assertRaises(ClaudeExecutionError) as caught:
            call()
        self.assertFalse(caught.exception.transient)
        # A count, not the list: a launch record holds the whole environment.
        self.assertEqual(len(self.fake.calls()), 0, "codex must not be launched")
        self.assertNotIn(KEY, str(caught.exception))
        return caught.exception

    def test_codex_gateway_no_key_fails_permanent(self):
        self.fake.plan([{"lines": ok_lines()}])
        with mock.patch.dict(os.environ, {"YT_GATEWAY": "vercel"}):
            exc = self.assert_refused_without_launch(
                lambda: self.engine(max_retries=2).ask("hi"))
            self.assertIn("AI_GATEWAY_API_KEY", str(exc))
            self.assert_refused_without_launch(
                lambda: list(self.engine(max_retries=2).stream("hi")))

    def test_an_unknown_switch_value_fails_permanent(self):
        self.fake.plan([{"lines": ok_lines()}])
        with mock.patch.dict(os.environ, on(YT_GATEWAY="on")):
            self.assert_refused_without_launch(lambda: self.engine(max_retries=2).ask("hi"))

    def test_the_budget_guard_still_refuses_an_unpriced_model(self):
        self.fake.plan([{"lines": ok_lines()}])
        with mock.patch.dict(os.environ, on()):
            with self.assertRaises(codex_engine.CodexBudgetRefused) as caught:
                self.engine(model="gpt-unknown", max_turns_budget_usd=1.0).ask("hi")
            self.assertIn("openai/gpt-unknown", str(caught.exception))
            self.assertEqual(len(self.fake.calls()), 0)
            # A priced model keeps its dollar budget through the gateway.
            self.assertEqual(self.engine(model="gpt-5", max_turns_budget_usd=5.0)
                             .ask("hi").text, "ok")


class Documented(unittest.TestCase):
    def test_the_readme_and_the_skill_name_the_codex_route(self):
        for path in (ROOT / "README.md", ROOT / "ytstudio" / "SKILL.md"):
            flat = " ".join(path.read_text(encoding="utf-8").split())
            with self.subTest(path=path.name):
                self.assertIn("/codex/v1", flat)
                self.assertIn("`openai/`", flat)
                self.assertIn("config.toml", flat)

    def test_the_engines_feature_map_names_the_gateway(self):
        doc = codex_engine.__doc__ or ""
        self.assertIn("YT_GATEWAY=vercel", doc)
        self.assertIn("model_provider", doc)


if __name__ == "__main__":
    unittest.main()
