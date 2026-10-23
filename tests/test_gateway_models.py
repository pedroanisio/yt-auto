"""AUTO-120: a gateway model id is the same model as its direct name.

Vercel's AI Gateway names models `provider/model` (`anthropic/claude-opus-5`,
`openai/gpt-5`), writes Anthropic's versions with a dot
(`anthropic/claude-haiku-4.5`), and its Claude Code picker adds a
`claude-code/` lead and a `[1m]` tail. A virtual model (`vmc/<slug>`) may route
anywhere. These tests pin that a gateway id counts as its direct name's family,
price row and Claude-ness, that a virtual model is refused as a reviewer or an
author, and that every direct name resolves exactly as before. No network, no
model: the functions are pure, and doctor reads hand-written records.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import claude_engine  # noqa: E402
import codex_engine  # noqa: E402
from ytstudio import doctor, flags, harness, spend, workspace  # noqa: E402,F401
from ytstudio.core import write_json  # noqa: E402

# Importing harness installs spend.PRICES into claude_engine.PRICES, as every
# stage does; the rows below are those.


class Canonical(unittest.TestCase):
    CASES = [
        ("anthropic/claude-opus-5", ("anthropic", "claude-opus-5")),
        ("claude-code/anthropic/claude-opus-5[1m]", ("anthropic", "claude-opus-5")),
        ("anthropic/claude-haiku-4.5", ("anthropic", "claude-haiku-4-5")),
        ("openai/gpt-5", ("openai", "gpt-5")),
        # Only Anthropic's versions are dotted on the gateway and dashed in
        # its own ids; OpenAI's dot is part of the id.
        ("openai/gpt-5.1", ("openai", "gpt-5.1")),
        ("vmc/foo", ("vmc", "foo")),
        ("claude-code/vmc/foo", ("vmc", "foo")),
        ("sonnet", ("", "sonnet")),
        ("claude-opus-5-5", ("", "claude-opus-5-5")),
        ("opus[1m]", ("", "opus")),
        ("", ("", "")),
        (None, ("", "")),
    ]

    def test_the_table(self):
        from ytstudio import models
        for name, want in self.CASES:
            self.assertEqual(models.canonical(name), want, name)

    def test_the_engines_and_the_package_share_one_function(self):
        # The engines are single stdlib files that do not import ytstudio,
        # so the function lives in claude_engine and the package names it.
        from ytstudio import models
        self.assertIs(models.canonical, claude_engine.canonical_model)


class Family(unittest.TestCase):
    def test_family_same_with_and_without_provider_prefix(self):
        self.assertEqual(flags.model_family("openai/gpt-5"), flags.model_family("gpt-5"))
        self.assertEqual(flags.model_family("anthropic/claude-opus-5"),
                         flags.model_family("opus"))
        self.assertEqual(flags.model_family("claude-code/anthropic/claude-sonnet-5[1m]"),
                         flags.model_family("sonnet"))
        self.assertTrue(flags.own_reviewer("openai/gpt-5", "gpt-5"))
        self.assertTrue(flags.own_reviewer("gpt-5", "openai/gpt-5"))
        self.assertTrue(flags.own_reviewer("anthropic/claude-opus-5", "opus"))

    def test_own_reviewer_refuses_vmc(self):
        for model, reviewer in (("vmc/foo", "opus"), ("opus", "vmc/foo"),
                                ("sonnet", "claude-code/vmc/foo"),
                                ("vmc/a", "vmc/b")):
            error = flags.own_reviewer(model, reviewer)
            self.assertTrue(error, (model, reviewer))
            self.assertIn("virtual", error)

    def test_the_claude_aliases_are_judged_as_before(self):
        self.assertEqual(flags.own_reviewer("opus", "sonnet"), "")
        self.assertEqual(flags.own_reviewer("sonnet", "haiku"), "")
        self.assertEqual(flags.own_reviewer("opus", "gpt-5"), "")
        self.assertTrue(flags.own_reviewer("opus", "claude-opus-5-5"))
        self.assertTrue(flags.own_reviewer("opus", "opus[1m]"))
        self.assertTrue(flags.own_reviewer("sonnet", "claude-sonnet-5-5"))


class IsClaude(unittest.TestCase):
    def test_is_claude_model_anthropic_slash(self):
        for name in ("anthropic/claude-opus-5", "claude-code/anthropic/claude-opus-5[1m]",
                     "anthropic/claude-haiku-4.5", "anthropic/claude-opus-5.5"):
            self.assertTrue(codex_engine.is_claude_model(name), name)
        for name in ("openai/gpt-5", "gpt-5", "vmc/foo", "", None):
            self.assertFalse(codex_engine.is_claude_model(name), name)

    def test_a_claude_code_picker_id_is_never_passed_to_codex(self):
        # `claude-code/...` is what Claude Code's gateway picker shows, the
        # virtual `claude-code/vmc/<slug>` too: only the Claude CLI sends it,
        # so Codex drops it with a warning and answers with its own default,
        # as it does for `opus`. A bare `vmc/<slug>` may be meant for Codex.
        for name in ("claude-code/vmc/foo", "claude-code/anthropic/claude-opus-5.5[1m]",
                     "claude-code/openai/gpt-5"):
            self.assertTrue(codex_engine.is_claude_model(name), name)
            with mock.patch.dict(os.environ, {"CODEX_ENGINE_MODEL": ""}), \
                    mock.patch.object(codex_engine, "_warn_once"):
                engine = codex_engine.CodexEngine(codex_engine.CodexConfig(model=name))
                self.assertNotEqual(engine._model_flag(), name)
        with mock.patch.dict(os.environ, {"CODEX_ENGINE_MODEL": ""}):
            engine = codex_engine.CodexEngine(codex_engine.CodexConfig(model="vmc/foo"))
            self.assertEqual(engine._model_flag(), "vmc/foo")

    def test_the_direct_names_are_judged_as_before(self):
        for name in ("opus", "sonnet", "haiku", "fable", "default", "claude-opus-5-5",
                     "opus[1m]", "us.anthropic.claude-opus-5", "anthropic.claude-opus-5"):
            self.assertTrue(codex_engine.is_claude_model(name), name)
        for name in ("gpt-5", "gpt-5.1-codex", "o3"):
            self.assertFalse(codex_engine.is_claude_model(name), name)


class Prices(unittest.TestCase):
    def test_price_for_gateway_id_hits_claude_row(self):
        self.assertEqual(spend.price_for("anthropic/claude-opus-5"),
                         spend.PRICES["claude-opus-5"])
        self.assertEqual(spend.price_for("anthropic/claude-haiku-4.5"),
                         spend.PRICES["claude-haiku-4-5"])
        # The default generation, as the gateway spells it (a dotted version).
        self.assertEqual(spend.price_for("anthropic/claude-opus-5.5"),
                         spend.PRICES["claude-opus-5-5"])
        self.assertEqual(spend.price_for("anthropic/claude-sonnet-5.5"),
                         spend.PRICES["claude-sonnet-5-5"])
        self.assertEqual(spend.price_for("claude-code/anthropic/claude-sonnet-5.5"),
                         spend.PRICES["claude-sonnet-5-5"])
        # The dashed spelling resolves too.
        self.assertEqual(spend.price_for("anthropic/claude-opus-5-5"),
                         spend.PRICES["claude-opus-5-5"])
        self.assertEqual(spend.price_for("anthropic/sonnet"), spend.PRICES["claude-sonnet-5-5"])
        # A virtual model may route anywhere: it has no row.
        self.assertIsNone(spend.price_for("vmc/claude-opus-5"))
        self.assertIsNone(spend.price_for("claude-code/vmc/claude-opus-5"))

    def test_a_1m_context_id_is_unpriced_on_either_route(self):
        # A 1M-token context may bill above the base rate, so `[1m]` is not
        # priced at the base row on the gateway any more than on the direct
        # route: the call is staged under the CLI's own budget stop
        # (sdkkit.stream_round), which knows the real cost.
        for name in ("claude-code/anthropic/claude-opus-5[1m]",
                     "claude-code/anthropic/claude-opus-5.5[1m]",
                     "anthropic/claude-sonnet-5.5[1m]",
                     "claude-opus-5-5[1m]", "opus[1m]"):
            self.assertIsNone(spend.price_for(name), name)
            self.assertIsNone(claude_engine._partial_cost({"output_tokens": 1}, name), name)
        # An operator who knows the rate prices the id as it is reported.
        row = {"input": 8, "cached_input": 0.8, "output": 40}
        name = "claude-code/anthropic/claude-opus-5.5[1m]"
        with mock.patch.dict(os.environ, {"CLAUDE_PRICES": json.dumps({name: row})}):
            self.assertEqual(spend.price_for(name), row)
            self.assertAlmostEqual(claude_engine._partial_cost(
                {"output_tokens": 1_000_000}, name), 40.0)

    def test_another_provider_is_not_priced_as_the_tables_own(self):
        # On the gateway the provider is part of the model: Anthropic's rows
        # price anthropic/ ids only, OpenAI's openai/ ids only. Bedrock or
        # Vertex may bill a Claude model at their own rate.
        for name in ("openai/sonnet", "openai/claude-opus-5", "bedrock/claude-opus-5",
                     "vertex/claude-sonnet-5-5", "google/opus"):
            self.assertIsNone(spend.price_for(name), name)
            self.assertIsNone(claude_engine._partial_cost({"output_tokens": 1}, name), name)
        for name in ("anthropic/gpt-5", "azure/gpt-5", "bedrock/gpt-5"):
            self.assertIsNone(codex_engine.price_for(name), name)

    def test_an_operator_row_still_wins(self):
        exact = {"input": 1, "cached_input": 0.1, "output": 7}
        with mock.patch.dict(os.environ, {"CLAUDE_PRICES": json.dumps(
                {"anthropic/claude-opus-5": exact})}):
            self.assertEqual(spend.price_for("anthropic/claude-opus-5"), exact)
            self.assertAlmostEqual(claude_engine._partial_cost(
                {"output_tokens": 1_000_000}, "anthropic/claude-opus-5"), 7.0)
        # An override of the direct id is the gateway id's row too.
        direct = {"input": 2, "cached_input": 0.2, "output": 9}
        with mock.patch.dict(os.environ, {"CLAUDE_PRICES": json.dumps(
                {"claude-opus-5": direct})}):
            self.assertEqual(spend.price_for("anthropic/claude-opus-5"), direct)
            self.assertAlmostEqual(claude_engine._partial_cost(
                {"output_tokens": 1_000_000}, "anthropic/claude-opus-5"), 9.0)

    def test_partial_cost_gateway_id_nonzero(self):
        self.assertAlmostEqual(claude_engine._partial_cost(
            {"output_tokens": 1_000_000}, "anthropic/claude-sonnet-5-5"), 10.0)
        self.assertAlmostEqual(claude_engine._partial_cost(
            {"output_tokens": 1_000_000}, "anthropic/claude-haiku-4.5"), 5.0)
        self.assertAlmostEqual(claude_engine._partial_cost(
            {"output_tokens": 1_000_000}, "anthropic/claude-opus-5.5"), 20.0)
        self.assertAlmostEqual(claude_engine._partial_cost(
            {"output_tokens": 1_000_000}, "claude-code/anthropic/claude-sonnet-5.5"), 10.0)
        self.assertIsNone(claude_engine._partial_cost(
            {"output_tokens": 1_000_000}, "vmc/claude-sonnet-5-5"))

    def test_codex_price_for_openai_prefix(self):
        self.assertEqual(codex_engine.price_for("openai/gpt-5"), codex_engine.PRICES["gpt-5"])
        self.assertEqual(codex_engine.price_for("openai/gpt-5.1"),
                         codex_engine.PRICES["gpt-5.1"])
        self.assertIsNone(codex_engine.price_for("vmc/gpt-5"))
        exact = {"input": 1, "cached_input": 0.1, "output": 3}
        self.assertEqual(codex_engine.price_for(
            "openai/gpt-5", overrides={"openai/gpt-5": exact}), exact)
        self.assertEqual(codex_engine.price_for(
            "openai/gpt-5", env={"CODEX_PRICES": json.dumps({"gpt-5": exact})}), exact)

    def test_direct_names_unchanged(self):
        # Every row and alias resolves to the row it resolved to before the
        # gateway, and the names no table holds stay unpriced: the engine's
        # table keeps no alias, so a session on a short name is still staged
        # under the CLI's own budget stop (sdkkit.stream_round).
        for name, row in spend.PRICES.items():
            self.assertEqual(spend.price_for(name), row, name)
            self.assertEqual(claude_engine._partial_cost({"output_tokens": 1_000_000}, name),
                             row["output"], name)
        for alias, target in spend.ALIASES.items():
            self.assertEqual(spend.price_for(alias), spend.PRICES[target], alias)
            self.assertIsNone(claude_engine._partial_cost({"output_tokens": 1}, alias), alias)
        for name in ("opus[1m]", "claude-opus-5-5[1m]", "claude-haiku-4.5", "claude-x-9"):
            self.assertIsNone(spend.price_for(name), name)
            self.assertIsNone(claude_engine._partial_cost({"output_tokens": 1}, name), name)
        for name, row in codex_engine.PRICES.items():
            self.assertEqual(codex_engine.price_for(name), row, name)
        self.assertIsNone(codex_engine.price_for("gpt-9"))


class StoryReview(unittest.TestCase):
    def test_a_gateway_reviewer_is_priced_from_its_own_row(self):
        # A miss would fall back to the opus row; the gateway id of Haiku 4.5
        # prices the estimate as Haiku 4.5's direct id does.
        def usd(reviewer):
            return spend.story_review_usd(None, ["--review", "--reviewer-model", reviewer])
        self.assertNotEqual(spend.PRICES["claude-haiku-4-5"], spend.PRICES["claude-opus-5-5"])
        self.assertEqual(usd("anthropic/claude-haiku-4.5"), usd("claude-haiku-4-5"))
        self.assertLess(usd("anthropic/claude-haiku-4.5"), usd("opus"))
        self.assertEqual(usd("anthropic/claude-opus-5.5"), usd("opus"))


class CacheKeys(unittest.TestCase):
    """The decision cache is keyed by the model name as passed, so a gateway
    id is a new question and an alias is the same one on either route."""

    def test_the_digest_names_the_model_as_passed(self):
        with tempfile.TemporaryDirectory() as tmp:
            direct = harness.Harness(root=Path(tmp), stage="t", model="opus", verbose=False)
            prefixed = harness.Harness(root=Path(tmp), stage="t",
                                       model="anthropic/claude-opus-5", verbose=False)
            self.assertEqual(direct._asked_as, "opus")
            self.assertEqual(prefixed._asked_as, "anthropic/claude-opus-5")


class Doctor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        write_json(self.root / "idea.json", {"title": "T", "genre": "g", "description": "d"})

    def spend_findings(self):
        report = doctor.check_project(workspace.Project(self.root))
        return [f for f in report.findings if f.area == "spend"]

    def test_gateway_ids_of_priced_models_are_quiet(self):
        by_model = {name: {"calls": 1} for name in (
            "anthropic/claude-opus-5", "anthropic/claude-haiku-4.5",
            "anthropic/claude-opus-5.5", "claude-code/anthropic/claude-sonnet-5.5",
            "claude-code/anthropic/claude-sonnet-5-5", "openai/gpt-5")}
        write_json(self.root / "styles.json", {"styles": [], "usage": spend.record(
            0.4, "anthropic/claude-opus-5", 4, by_model=by_model)})
        self.assertEqual(self.spend_findings(), [])

    def test_a_1m_context_gateway_id_warns_as_the_direct_one_does(self):
        name = "claude-code/anthropic/claude-opus-5.5[1m]"
        write_json(self.root / "styles.json", {"styles": [], "usage": spend.record(
            0.4, name, 1, by_model={name: {"calls": 1}})})
        found = self.spend_findings()
        self.assertEqual([f.level for f in found], ["warn"])
        self.assertIn(name, found[0].message)

    def test_a_virtual_model_is_unpriced(self):
        write_json(self.root / "styles.json", {"styles": [], "usage": spend.record(
            0.4, "vmc/foo", 1, by_model={"vmc/foo": {"calls": 1}})})
        found = self.spend_findings()
        self.assertEqual([f.level for f in found], ["warn"])
        self.assertIn("vmc/foo", found[0].message)


if __name__ == "__main__":
    unittest.main()
