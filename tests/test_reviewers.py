"""A script reviewer from another maker, through the gateway engine (AUTO-125).

`--reviewer-model pool` (or `sdk:<provider>/<model>`) on `yt script --review`
and on the calibration run picks a pinned row of `reviewers.POOL` whose maker
is not the author's, checks the gateway engine is ready, narrows the row's
providers to the ones live now, and builds the plan review, the picture
check and the story review's readers with `engine="gateway"`. Every other
value takes today's path unchanged, and the roles that open images through
an agent (keyframe, vfx, probe) refuse the pool.

No test touches the network: `fetch` is injected, `urllib.request.urlopen`
raises, the gateway engine is a fake class, and its readiness check is
stubbed. The endpoint answers below are written in the shape the research
read (status, quantization, tags, per-token pricing); that shape is a TO
VERIFY of the card, not a copy of a live response.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import claude_engine  # noqa: E402
import gateway_engine  # noqa: E402
from claude_engine import EngineConfig, Usage  # noqa: E402
from ytstudio import (cli, doctor, flags, harness, keyframe, narrative, probes,  # noqa: E402
                      reviewers, script, spend, vfx, workspace)

from tests.test_script_plan import PLAN, idea, shots_for  # noqa: E402
from tests.test_script_review import (ACCEPT, ANSWERS, COLD, flag, kind,  # noqa: E402
                                      sample)

GOOGLE = {"model": "google/gemini-3.6-flash", "maker": "google",
          "price": {"input": 0.75, "output": 3.75},
          "providers": ("google", "vertex"), "priced_on": "2026-10-23"}
ZAI = {"model": "zai/glm-5.3", "maker": "zai", "price": {"input": 1.20, "output": 4.00},
       "providers": ("cerebras", "deepinfra", "novita", "zai"), "priced_on": "2026-10-23"}
DEEPSEEK = {"model": "deepseek/deepseek-v4.1-flash", "maker": "deepseek",
            "price": {"input": 0.075, "output": 0.30},
            "providers": ("deepseek",), "priced_on": "2026-10-23"}
TEST_POOL = (GOOGLE, ZAI, DEEPSEEK)
GATEWAY_ON = {"YT_GATEWAY": "vercel"}


def endpoint(provider, *, status=0, quant="fp8", so=True, inp=1.20, out=4.00):
    """One provider of `/v1/models/{id}/endpoints`, as the research read it."""
    return {"provider_name": provider, "status": status, "quantization": quant,
            "tags": ["structured-output", "tool-use"] if so else ["tool-use"],
            "pricing": {"prompt": f"{inp / 1e6:.12f}", "completion": f"{out / 1e6:.12f}"}}


def catalog(*models, deprecated=()):
    return {"object": "list",
            "data": [{"id": m, "owned_by": m.split("/")[0],
                      "deprecated_at": "2026-11-01" if m in deprecated else None}
                     for m in models]}


class Fetch:
    """A fake `fetch`: answers by URL, counts every call."""

    def __init__(self, endpoints=None, *, models=None, fail=None):
        self.calls: list[str] = []
        self.endpoints = endpoints or {}
        self.models = models
        self.fail = fail

    def __call__(self, url):
        self.calls.append(url)
        if self.fail is not None:
            raise self.fail
        if url == reviewers.MODELS_URL:
            return self.models or catalog(*self.endpoints)
        for model, rows in self.endpoints.items():
            if url == reviewers.ENDPOINTS_URL.format(id=model):
                return {"data": {"id": model, "endpoints": rows}}
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)


def zai_live():
    return Fetch({"zai/glm-5.3": [endpoint("cerebras"), endpoint("deepinfra", quant="fp4"),
                                  endpoint("novita"), endpoint("zai")]})


class Offline(unittest.TestCase):
    """No network, a test pool, an empty preflight cache, a clean environment."""

    def setUp(self):
        for target, name, value in (
                ("urllib.request.urlopen", None,
                 mock.Mock(side_effect=AssertionError("a test reached the network"))),
                (reviewers, "POOL", TEST_POOL)):
            p = (mock.patch(target, value) if name is None
                 else mock.patch.object(target, name, value))
            p.start()
            self.addCleanup(p.stop)
        reviewers.clear_cache()
        self.addCleanup(reviewers.clear_cache)
        saved = {k: os.environ.pop(k) for k in list(os.environ)
                 if k.startswith(("YT_GATEWAY", "YT_REVIEWER", "AI_GATEWAY"))}
        self.addCleanup(os.environ.update, saved)

    def ready(self, problems=()):
        p = mock.patch.object(gateway_engine, "readiness", return_value=list(problems))
        stub = p.start()
        self.addCleanup(p.stop)
        return stub


# --------------------------------------------------------------------------- #
# the maker, the pool
# --------------------------------------------------------------------------- #

class Maker(unittest.TestCase):
    def test_maker_table(self):
        for name, want in (("opus", "anthropic"), ("claude-opus-5-5", "anthropic"),
                           ("anthropic/claude-haiku-4.5", "anthropic"),
                           ("sonnet", "anthropic"), ("bedrock/claude-opus-5", "anthropic"),
                           ("gpt-5", "openai"), ("o4-mini", "openai"),
                           ("gpt-5.1-codex", "openai"), ("openai/gpt-oss-120b", "openai"),
                           ("google/gemma-4-31b-it", "google"),
                           ("google/gemini-3.6-flash", "google"),
                           ("zai/glm-5.3", "zai"), ("sdk:zai/glm-5.3", "zai"),
                           ("vmc/foo", None), ("mystery", None), ("", None)):
            self.assertEqual(reviewers.maker(name), want, name)

    def test_the_pinned_pool_has_no_cli_maker_and_its_makers_are_its_prefixes(self):
        for row in reviewers.POOL:
            self.assertNotIn(row["maker"], ("anthropic", "openai"), row["model"])
            self.assertEqual(reviewers.maker(row["model"]), row["maker"])
            self.assertEqual(set(row), {"model", "maker", "price", "providers", "priced_on"})
            self.assertIsInstance(row["providers"], tuple)


class Choose(Offline):
    def test_choose_skips_author_maker(self):
        self.assertEqual(reviewers.choose("opus")["model"], GOOGLE["model"])
        self.assertEqual(reviewers.choose("google/gemini-3.7-flash")["model"], ZAI["model"])
        self.assertEqual(reviewers.choose("opus", avoid=("google",))["model"], ZAI["model"])

    def test_choose_refuses_unknown_author(self):
        for author in ("mystery", "vmc/foo"):
            with self.assertRaisesRegex(ValueError, "maker"):
                reviewers.choose(author)

    def test_choose_refuses_when_pool_exhausted(self):
        with self.assertRaisesRegex(ValueError, "no row"):
            reviewers.choose("google/gemini-3.7-flash", avoid=("zai", "deepseek"))

    def test_a_row_with_no_pinned_provider_is_not_offered(self):
        bare = dict(GOOGLE, providers=())
        with mock.patch.object(reviewers, "POOL", (bare, ZAI)):
            self.assertEqual(reviewers.choose("opus")["model"], ZAI["model"])


# --------------------------------------------------------------------------- #
# preflight
# --------------------------------------------------------------------------- #

class Preflight(Offline):
    def test_preflight_narrows_to_pinned(self):
        fetch = Fetch({"zai/glm-5.3": [
            endpoint("cerebras"),                       # kept
            endpoint("zai", status=1),                  # down
            endpoint("deepinfra", quant="fp4"),         # fp4
            endpoint("novita", so=False),               # no structured output
            endpoint("baseten", inp=1.20, out=4.00),    # not in the row's list
        ]})
        dear = dict(ZAI, providers=ZAI["providers"] + ("parasail",))
        fetch.endpoints["zai/glm-5.3"].append(endpoint("parasail", out=4.50))  # above the row
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            got = reviewers.preflight(dear, fetch=fetch, env={})
        self.assertEqual(got["providers"], ("cerebras",))
        self.assertEqual(got["price_source"], "live")
        self.assertTrue(got["fetched_at"])
        self.assertEqual(got["live_price"], {"input": 1.20, "output": 4.00})
        notes = [line for line in said.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(notes), 1, notes)
        for name in ("zai", "deepinfra", "novita", "parasail"):
            self.assertIn(name, notes[0])
        self.assertNotIn("baseten", notes[0])

    def test_preflight_exclude_quant_env(self):
        fetch = Fetch({"zai/glm-5.3": [endpoint("cerebras", quant="fp8"),
                                       endpoint("zai", quant="bf16"),
                                       endpoint("novita", quant="fp4")]})
        with contextlib.redirect_stderr(io.StringIO()):
            got = reviewers.preflight(ZAI, fetch=fetch,
                                      env={reviewers.EXCLUDE_QUANT_ENV: "fp4,fp8"})
        self.assertEqual(got["providers"], ("zai",))

    def test_preflight_refuses_deprecated(self):
        fetch = Fetch({"zai/glm-5.3": [endpoint("cerebras")]},
                      models=catalog("zai/glm-5.3", deprecated=("zai/glm-5.3",)))
        with self.assertRaisesRegex(ValueError, "deprecated"):
            reviewers.preflight(ZAI, fetch=fetch, env={})

    def test_preflight_refuses_when_none_left(self):
        fetch = Fetch({"zai/glm-5.3": [endpoint("cerebras", status=1),
                                       endpoint("deepinfra", quant="fp4")]})
        with self.assertRaises(ValueError) as caught, \
                contextlib.redirect_stderr(io.StringIO()):
            reviewers.preflight(ZAI, fetch=fetch, env={})
        self.assertIn("cerebras", str(caught.exception))
        self.assertIn("deepinfra", str(caught.exception))

    def test_preflight_failure_uses_pinned(self):
        fetch = Fetch(fail=urllib.error.URLError("no route to host"))
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            got = reviewers.preflight(ZAI, fetch=fetch, env={})
        self.assertEqual(got["providers"], ZAI["providers"])
        self.assertEqual(got["price_source"], "pinned")
        self.assertIsNone(got["live_price"])
        self.assertEqual(len([x for x in said.getvalue().splitlines() if x.strip()]), 1)

    def test_preflight_cached_in_process(self):
        self.ready()
        fetch = zai_live()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        before = sorted(Path(tmp.name).rglob("*"))
        with contextlib.redirect_stderr(io.StringIO()), \
                mock.patch("os.getcwd", return_value=tmp.name):
            reviewers.resolve("opus", "sdk:zai/glm-5.3", env=GATEWAY_ON, fetch=fetch)
            first = len(fetch.calls)
            self.assertGreater(first, 0)
            reviewers.resolve("opus", "sdk:zai/glm-5.3", env=GATEWAY_ON, fetch=fetch)
            self.assertEqual(len(fetch.calls), first)
            reviewers.clear_cache()            # as a new process starts
            reviewers.resolve("opus", "sdk:zai/glm-5.3", env=GATEWAY_ON, fetch=fetch)
            self.assertEqual(len(fetch.calls), 2 * first)
        self.assertEqual(sorted(Path(tmp.name).rglob("*")), before)


# --------------------------------------------------------------------------- #
# resolve
# --------------------------------------------------------------------------- #

class Resolve(Offline):
    def test_a_cli_value_is_not_resolved_and_touches_nothing(self):
        stub = self.ready(["never asked"])
        fetch = Fetch()
        for value in ("opus", "sonnet", "gpt-5", "openai/gpt-5", "google/gemini-3.6-flash",
                      None, ""):
            self.assertIsNone(reviewers.resolve("opus", value, env=GATEWAY_ON, fetch=fetch))
        stub.assert_not_called()
        self.assertEqual(fetch.calls, [])
        self.assertEqual(reviewers.harness_kwargs(None), {})

    def test_pool_resolves_to_the_first_other_maker_and_narrows(self):
        self.ready()
        fetch = Fetch({"google/gemini-3.6-flash": [endpoint("google", inp=0.75, out=3.75),
                                                   endpoint("vertex", inp=0.75, out=3.75)]})
        got = reviewers.resolve("opus", "pool", env=GATEWAY_ON, fetch=fetch)
        self.assertEqual(got.model, "google/gemini-3.6-flash")
        self.assertEqual(got.maker, "google")
        self.assertEqual(got.providers, ("google", "vertex"))
        self.assertEqual(got.has, ("structured-output",))
        self.assertEqual(got.price, {"input": 0.75, "output": 3.75})
        self.assertEqual(got.price_source, "live")
        self.assertEqual(set(got.record()),
                         {"model", "maker", "providers", "price_source", "fetched_at"})

    def test_resolve_sdk_needs_pinned_row(self):
        self.ready()
        fetch = Fetch({"zai/glm-9": [endpoint("zai", inp=0.1, out=0.1)]})
        with self.assertRaisesRegex(ValueError, "POOL"):
            reviewers.resolve("opus", "sdk:zai/glm-9", env=GATEWAY_ON, fetch=fetch)
        self.assertEqual(fetch.calls, [])

    def test_resolve_reports_readiness(self):
        self.ready(["node 20 found, need >=22", "AI_GATEWAY_API_KEY not set"])
        fetch = zai_live()
        with self.assertRaises(ValueError) as caught:
            reviewers.resolve("opus", "pool", env=GATEWAY_ON, fetch=fetch)
        self.assertIn("node 20 found, need >=22", str(caught.exception))
        self.assertIn("AI_GATEWAY_API_KEY not set", str(caught.exception))
        self.assertEqual(fetch.calls, [])

    def test_without_the_switch_resolve_refuses_and_names_it(self):
        self.ready()
        fetch = zai_live()
        for env in ({}, {"YT_GATEWAY": "off"}):
            with self.assertRaisesRegex(ValueError, "YT_GATEWAY=vercel"):
                reviewers.resolve("opus", "pool", env=env, fetch=fetch)
        self.assertEqual(fetch.calls, [])

    def test_the_authors_maker_and_an_unknown_author_are_refused(self):
        self.ready()
        fetch = zai_live()
        with self.assertRaisesRegex(ValueError, "maker"):
            reviewers.resolve("mystery", "pool", env=GATEWAY_ON, fetch=fetch)
        with self.assertRaisesRegex(ValueError, "maker"):
            reviewers.resolve("zai/glm-5.2", "sdk:zai/glm-5.3", env=GATEWAY_ON, fetch=fetch)
        self.assertEqual(fetch.calls, [])

    def test_harness_kwargs_no_claude_defaults(self):
        self.ready()
        claude_engine.set_defaults(effort="high", fallback_model="sonnet")
        self.addCleanup(claude_engine.DEFAULTS.clear)
        with contextlib.redirect_stderr(io.StringIO()):
            got = reviewers.resolve("opus", "sdk:zai/glm-5.3", env=GATEWAY_ON,
                                    fetch=Fetch(fail=OSError("offline")))
        self.assertEqual(got.price_source, "pinned")
        kw = reviewers.harness_kwargs(got)
        self.assertEqual(kw["engine"], "gateway")
        self.assertEqual(kw["model"], "zai/glm-5.3")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        h = harness.Harness(tmp.name, "script", verbose=False, budget_usd=2.0, **kw)
        self.assertIsNone(h.effort)
        self.assertIsNone(h.fallback_model)
        self.assertEqual(h._asked_as, "zai/glm-5.3~gateway")
        engine = h._engine(tools=())
        self.assertEqual(tuple(engine.config.providers), ZAI["providers"])
        self.assertEqual(tuple(engine.config.has), ("structured-output",))
        self.assertFalse(engine.config.effort)
        self.assertFalse(engine.config.fallback_model)


class Readiness(unittest.TestCase):
    """gateway_engine.readiness says what _prepare would refuse, without a call."""

    def test_readiness_names_the_key_node_and_install(self):
        from gateway_fakes import FakeNode
        node = FakeNode(self)
        self.assertEqual(gateway_engine.readiness(), [])
        with mock.patch.dict(os.environ, {"FAKE_NODE_VERSION": "v20.1.0"}):
            gateway_engine._NODE_VERSIONS.clear()
            said = gateway_engine.readiness()
        self.assertEqual(len(said), 1)
        self.assertIn("Node 22 or later", said[0])
        gateway_engine._NODE_VERSIONS.clear()
        with mock.patch.dict(os.environ, {"AI_GATEWAY_API_KEY": ""}):
            said = gateway_engine.readiness()
        self.assertEqual(said, [f"the gateway engine needs the gateway key in "
                                f"{gateway_engine._gateway.KEY}"])
        (node.program_dir / "node_modules" / "ai" / "package.json").unlink()
        said = gateway_engine.readiness()
        self.assertEqual(len(said), 1)
        self.assertIn("npm", said[0])
        self.assertEqual(node.count, 0)        # the program never ran


# --------------------------------------------------------------------------- #
# the roles: script, calibration, the inspect stages, yt run
# --------------------------------------------------------------------------- #

class CliEngine:
    """The author's CLI engine (harness.ENGINE), answering by schema."""
    seen: list = []

    def __init__(self, config):
        self.config = config
        self.usage = Usage()
        self.usage.cost_usd, self.usage.calls = 0.01, 1
        self.usage.by_model = {config.model: {"cost_usd": 0.01, "calls": 1}}

    def ask_json(self, prompt, schema, system=None, **kw):
        type(self).seen.append({"kind": kind(schema), "model": self.config.model,
                                "config": self.config})
        return answer(schema, prompt)


class GatewayFake(CliEngine):
    """Stands in for gateway_engine.GatewayEngine: records its config."""
    seen: list = []


def answer(schema, prompt=""):
    which = kind(schema)
    if which == "plan-review":
        return copy.deepcopy(ACCEPT)
    if which == "plan":
        return copy.deepcopy(PLAN)
    if which == "shots":
        out = shots_for(1, ("context", "payoff"))
        for shot in out["shots"]:
            shot["source"] = "S1"
        return out
    if which == "review":
        if "previous" in schema.get("required", []):
            return sample("recommend", previous="better")
        return sample("consider", flags=[flag()])
    if which == "cold-read":
        return copy.deepcopy(COLD)
    if which == "answers":
        return copy.deepcopy(ANSWERS)
    if which == "match":
        return {"verdict": "match", "difference": ""}
    if which == "fixes":
        return {"fixes": [{"n": 1, "action": "fix", "narration": "The river is empty now.",
                           "why": "opens on the picture"}]}
    if which == "flags":
        return {"flags": [], "verdict": "fine"}
    if which == "edits":
        return {"edits": []}
    if "sections" in schema.get("properties", {}):
        return {"sections": [], "reports": []}
    return {}


REVIEWER_ROWS = ("plan-review", "review-r", "cold-read", "picture-check")


class Engines(Offline):
    """harness.ENGINE is the CLI fake; the gateway engine is GatewayFake."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        for target, name, value in ((harness, "ENGINE", CliEngine),
                                    (gateway_engine, "GatewayEngine", GatewayFake)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        CliEngine.seen, GatewayFake.seen = [], []

    def resolved(self, model="zai/glm-5.3"):
        self.ready()
        with contextlib.redirect_stderr(io.StringIO()):
            return reviewers.resolve("sonnet", "sdk:" + model, env=GATEWAY_ON,
                                     fetch=Fetch(fail=OSError("offline")))


class ScriptRoles(Engines):
    def test_script_review_roles_on_pool(self):
        got = self.resolved()
        w = script.Screenwriter(idea(), root=self.root, verbose=False, minutes=1, jobs=1,
                                model="sonnet", research="", reviewer_model="pool",
                                reviewer=got, review_budget_usd=2.0)
        data = w.run(review=True, do_verify=True)
        rows = data["engine"]["decisions"]
        asked = {d["name"]: d["model"] for d in rows}
        for name in ("plan-review", "review-r1-s1", "review-r1-s2", "review-r1-s3",
                     "review-r2-s1", "review-r2-s2", "review-r2-s3", "cold-read",
                     "cold-read-answers", "cold-read-match", "cold-read-r2",
                     "cold-read-answers-r2", "cold-read-match-r2", "picture-check",
                     "picture-check-again"):
            self.assertEqual(asked.get(name), "zai/glm-5.3", (name, asked))
        for name in ("plan", "write-01", "revise-story-1", "revise-ledger-2", "revise-rules"):
            self.assertEqual(asked.get(name), "sonnet", (name, asked))
        for name, model in asked.items():
            on_pool = name.startswith(REVIEWER_ROWS)
            self.assertEqual(model, "zai/glm-5.3" if on_pool else "sonnet", name)
        self.assertTrue(GatewayFake.seen)
        self.assertEqual({c["model"] for c in GatewayFake.seen}, {"zai/glm-5.3"})
        for call in GatewayFake.seen:
            self.assertEqual(tuple(call["config"].providers), ZAI["providers"])
            self.assertEqual(tuple(call["config"].has), ("structured-output",))
            self.assertIsNone(call["config"].effort)
        self.assertNotIn("zai/glm-5.3", {c["model"] for c in CliEngine.seen})
        self.assertEqual(data["review"]["model"], "zai/glm-5.3")
        for key in ("plan_review", "review", "picture"):
            seen = data[key]["reviewer"]
            self.assertEqual(seen["model"], "zai/glm-5.3", key)
            self.assertEqual(seen["maker"], "zai")
            self.assertEqual(seen["providers"], list(ZAI["providers"]))
            self.assertEqual(seen["price_source"], "pinned")

    def test_script_cli_reviewer_unchanged(self):
        w = script.Screenwriter(idea(), root=self.root, verbose=False, minutes=1, jobs=1,
                                model="opus", research="", reviewer_model="sonnet",
                                review_budget_usd=2.0)
        for h in (w.reviewer, w.picture_reviewer, w._reader(1), w._reader("cold")):
            self.assertIsNone(h.engine)
            self.assertIsNone(h.gateway)
            self.assertEqual(h._asked_as, "sonnet")
            engine = h._engine(tools=())
            self.assertIsInstance(engine, CliEngine)
            self.assertEqual(engine.config, EngineConfig(
                model="sonnet", effort=h.effort, fallback_model=h.fallback_model,
                timeout=h.timeout, max_turns_budget_usd=2.0, tools=()))
        data = w.run(review=True)
        self.assertEqual(GatewayFake.seen, [])
        for key in ("plan_review", "review", "picture"):
            self.assertNotIn("reviewer", data[key], key)


class Command(Offline):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name) / "work"
        self.film = self.work / "why-the-bridge-fell"
        self.film.mkdir(parents=True)
        (self.film / "idea.json").write_text(
            '{"title": "Why the bridge fell", "genre": "g", "description": "d"}')

    def stage(self, name, *argv):
        args = cli.build_parser().parse_args(
            [name, "--project", str(self.film), "--work", str(self.work), "--quiet", *argv])
        built = mock.Mock(side_effect=AssertionError("an engine was built"))
        with mock.patch.object(harness, "ENGINE", built), \
                mock.patch.object(gateway_engine, "GatewayEngine", built), \
                contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = args.run(args)
        return code, out.getvalue() + err.getvalue()

    def test_pool_without_gateway_refused(self):
        fetch = mock.Mock(side_effect=AssertionError("fetched"))
        with mock.patch.object(reviewers, "_get_json", fetch):
            code, said = self.stage("script", "--review", "--model", "opus",
                                    "--reviewer-model", "pool")
        self.assertEqual(code, 2, said)
        self.assertIn("YT_GATEWAY=vercel", said)
        fetch.assert_not_called()
        self.assertFalse((self.film / "script.json").exists())

    def test_inspect_roles_refuse_pool(self):
        for stage in ("keyframe", "vfx"):
            for value in ("pool", "sdk:zai/glm-5.3"):
                code, said = self.stage(stage, "--model", "opus", "--reviewer-model", value)
                self.assertEqual(code, 2, (stage, value, said))
                self.assertIn("text decisions only", said)
        for value in ("pool", "sdk:zai/glm-5.3"):
            args = cli.build_parser().parse_args(
                ["probe", "--work", str(self.work), "--model", "opus",
                 "--reviewer-model", value])
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(probes.run(args), 2, err.getvalue())
            self.assertIn("text decisions only", err.getvalue())


class Calibrate(Engines):
    def test_calibrate_resolves_pool(self):
        cases = Path(self._tmp.name) / "cases"
        case = cases / "one"
        case.mkdir(parents=True)
        (case / "plan.json").write_text(json.dumps(PLAN))
        (case / "expected.json").write_text(json.dumps({"verdict": "recommend"}))
        read = mock.Mock(wraps=narrative.calibration_cases)
        with mock.patch.object(narrative, "calibration_cases", read), \
                self.assertRaisesRegex(ValueError, "YT_GATEWAY=vercel"):
            narrative.calibrate(cases, work=self.root / "w", model="opus",
                                reviewer_model="pool", verbose=False)
        read.assert_not_called()
        self.ready()
        with mock.patch.dict(os.environ, GATEWAY_ON), \
                mock.patch.object(reviewers, "_get_json", side_effect=OSError("offline")), \
                contextlib.redirect_stderr(io.StringIO()):
            report = narrative.calibrate(cases, work=self.root / "w", model="opus",
                                         reviewer_model="pool", verbose=False)
        self.assertEqual(report["reviewer_model"], "google/gemini-3.6-flash")
        self.assertTrue(GatewayFake.seen)
        self.assertEqual({c["model"] for c in GatewayFake.seen}, {"google/gemini-3.6-flash"})
        with mock.patch.object(sys, "argv", ["x"]), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = narrative.main(["calibrate", str(cases), "--work", str(self.root / "w"),
                                   "--model", "opus", "--reviewer-model", "pool"])
        self.assertEqual(code, 2, err.getvalue())


class Run(Offline):
    def args(self, *argv):
        return cli.build_parser().parse_args(["run", "--title", "T", *argv])

    def test_run_hands_pool_to_script_only(self):
        extra = cli.run_extras(self.args("--reviewer-model", "sdk:zai/glm-5.3",
                                         "--review-budget-usd", "3"), redo=set())
        script_argv = extra["script"]
        at = script_argv.index("--reviewer-model")
        self.assertEqual(script_argv[at + 1], "sdk:zai/glm-5.3")
        self.assertEqual(script_argv[script_argv.index("--review-budget-usd") + 1], "3")
        for stage in ("keyframe", "vfx"):
            self.assertNotIn("--reviewer-model", extra[stage])
            self.assertEqual(extra[stage][-2:], ["--review-budget-usd", "3"])

    def test_a_cli_reviewer_is_handed_as_today(self):
        extra = cli.run_extras(self.args("--reviewer-model", "sonnet",
                                         "--review-budget-usd", "3"), redo=set())
        want = ["--reviewer-model", "sonnet", "--review-budget-usd", "3"]
        self.assertEqual(extra["script"][-4:], want)
        self.assertEqual(extra["keyframe"], want)
        self.assertEqual(extra["vfx"], want)

    def walk(self, *argv):
        args = self.args(*argv)
        with mock.patch.object(workspace, "resolve",
                               side_effect=AssertionError("passed the check")), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            try:
                code = cli._run_pipeline(args)
            except AssertionError as exc:
                self.assertEqual(str(exc), "passed the check")
                code = None
        return code, err.getvalue(), args

    def test_run_refuses_pool_where_keyframe_and_vfx_keep_the_authors_family(self):
        code, said, _ = self.walk("--model", "opus", "--reviewer-model", "pool")
        self.assertEqual(code, 2)
        self.assertIn("keyframe", said)
        self.assertIn("vfx", said)
        code, said, _ = self.walk("--model", "opus", "--reviewer-model", "pool", "--quick",
                                  "--skip", "keyframe,vfx")
        self.assertEqual(code, 2)
        self.assertIn("--quick", said)

    def test_run_resolves_pool_once_and_hands_the_id(self):
        self.ready()
        with mock.patch.dict(os.environ, GATEWAY_ON), \
                mock.patch.object(reviewers, "_get_json", side_effect=OSError("offline")):
            code, said, args = self.walk("--model", "opus", "--reviewer-model", "pool",
                                         "--skip", "keyframe,vfx")
            self.assertIsNone(code, said)
            self.assertEqual(args.reviewer_model, "sdk:google/gemini-3.6-flash")
            code, said, args = self.walk("--model", "sonnet", "--reviewer-model", "pool")
            self.assertIsNone(code, said)
            self.assertEqual(args.reviewer_model, "sdk:google/gemini-3.6-flash")

    def test_run_without_the_gateway_refuses_pool(self):
        code, said, _ = self.walk("--model", "sonnet", "--reviewer-model", "pool")
        self.assertEqual(code, 2)
        self.assertIn("YT_GATEWAY=vercel", said)


# --------------------------------------------------------------------------- #
# prices
# --------------------------------------------------------------------------- #

class Prices(unittest.TestCase):
    """The pinned rows are spend's rows, so the budget check and doctor find them."""

    def test_every_pool_row_is_priced_by_spend(self):
        for row in reviewers.POOL:
            got = spend.price_for(row["model"])
            self.assertIsNotNone(got, row["model"])
            self.assertEqual(got["input"], row["price"]["input"])
            self.assertEqual(got["output"], row["price"]["output"])
            self.assertEqual(got["cached_input"], row["price"]["input"])
            self.assertIsNotNone(gateway_engine.price_for(row["model"], env={}))

    def test_pool_rows_bounded(self):
        opus = spend.price_for("opus")
        for row in reviewers.POOL:
            self.assertLessEqual(row["price"]["input"], opus["input"], row["model"])
            self.assertLessEqual(row["price"]["output"], opus["output"], row["model"])

    def test_estimate_uses_resolved_row(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")), \
                mock.patch.object(reviewers, "POOL", TEST_POOL):
            pooled = spend.story_review_usd(None, ["--model", "opus", "--review",
                                                   "--reviewer-model", "pool"])
            pinned = spend.story_review_usd(None, ["--review", "--reviewer-model",
                                                   "sdk:google/gemini-3.6-flash"])
            direct = spend.story_review_usd(None, ["--review", "--reviewer-model",
                                                   "google/gemini-3.6-flash"])
            opus = spend.story_review_usd(None, ["--review"])
        self.assertEqual(pooled, pinned)
        self.assertEqual(pooled, direct)
        self.assertLess(pooled, opus)

    def test_doctor_prices_pool_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "film"
            root.mkdir()
            (root / "script.json").write_text(json.dumps(
                {"usage": {"cost_usd": 0.1, "model": "sonnet", "calls": 2,
                           "by_model": {"zai/glm-5.3": {"cost_usd": 0.05, "calls": 1}}}}))
            report = doctor.Report("t")
            doctor.check_spend(workspace.Project(root), report)
        self.assertEqual([f.message for f in report.findings if f.area == "spend"], [])


if __name__ == "__main__":
    unittest.main()
