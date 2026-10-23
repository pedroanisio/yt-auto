"""A stage opts into the gateway engine per Harness, and nowhere else (AUTO-124).

`Harness(engine="gateway")` is how a capability stage asks a model neither
CLI can reach (audio, video, other reviewer families) for one decision,
while authoring and every other harness stay on claude or codex. With
`engine` unset, a harness builds the global ENGINE and asks exactly what it
asked before, with the same digest. The gateway engine runs a fake `node`
(tests/gateway_fakes.py); the default path runs the harness tests' fake engine.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import claude_engine  # noqa: E402
import gateway_engine  # noqa: E402
from claude_engine import ClaudeEngine  # noqa: E402
from gateway_fakes import KEY, MODEL, USAGE, FakeNode, err, ok  # noqa: E402
from test_harness import SCHEMA, FakeEngine  # noqa: E402
from ytstudio import harness  # noqa: E402


class Case(unittest.TestCase):
    def setUp(self):
        self.node = FakeNode(self)
        self.root = Path(self.node.dir) / "project"
        self.root.mkdir()

    def make(self, **kw) -> harness.Harness:
        kw.setdefault("model", MODEL)
        kw.setdefault("engine", "gateway")
        return harness.Harness(self.root, "qa", role="You judge.", house="## all\n- be clear",
                               verbose=False, **kw)

    def clip(self, name="clip.mp4", data=b"frames") -> Path:
        path = self.node.dir / name
        path.write_bytes(data)
        return path


class Default(unittest.TestCase):
    """With `engine` unset nothing changes: the global ENGINE, the old digest."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._engine = harness.ENGINE
        harness.ENGINE = FakeEngine
        self.addCleanup(setattr, harness, "ENGINE", self._engine)
        FakeEngine.json_calls, FakeEngine.answers, FakeEngine.fail = [], [], False
        claude_engine.DEFAULTS.clear()
        self.addCleanup(claude_engine.DEFAULTS.clear)

    def test_harness_default_engine_unchanged(self):
        h = harness.Harness(self._tmp.name, "qa", model="sonnet", role="R", house="H",
                            verbose=False)
        self.assertIsNone(h.engine)
        self.assertEqual(h._asked_as, "sonnet")
        self.assertIsInstance(h._engine(tools=()), FakeEngine)
        h.decide("edit", "P", SCHEMA)
        saved = json.loads((Path(self._tmp.name) / "decisions" / "qa" / "edit.json").read_text())
        self.assertEqual(saved["digest"], harness._digest(h.system, "P", SCHEMA, "sonnet"))
        # FakeEngine.ask_json takes no `media`: the call is exactly today's.
        self.assertEqual(len(FakeEngine.json_calls), 1)
        high = harness.Harness(self._tmp.name, "qa", model="opus", effort="high",
                               verbose=False)
        self.assertEqual(high._asked_as, "opus@high")

    def test_media_on_a_harness_without_the_gateway_engine_is_refused(self):
        h = harness.Harness(self._tmp.name, "qa", model="sonnet", verbose=False)
        path = Path(self._tmp.name) / "clip.mp4"
        path.write_bytes(b"x")
        with self.assertRaises(harness.HarnessError):
            h.decide("look", "P", SCHEMA, media=[path])
        self.assertEqual(FakeEngine.json_calls, [])

    def test_gateway_options_need_the_gateway_engine(self):
        with self.assertRaises(harness.HarnessError):
            harness.Harness(self._tmp.name, "qa", gateway={"providers": ["google"]})

    def test_engine_names_only_the_gateway(self):
        for name in ("claude", "codex", "vercel"):
            with self.subTest(name=name), self.assertRaises(harness.HarnessError):
                harness.Harness(self._tmp.name, "qa", model=MODEL, engine=name)


class Construction(Case):
    def test_gateway_harness_clears_effort_and_fallback(self):
        claude_engine.set_defaults(effort="high", fallback_model="haiku")
        h = self.make()
        self.assertIsNone(h.effort)
        self.assertIsNone(h.fallback_model)
        self.assertEqual(h._asked_as, f"{MODEL}~gateway")
        for kw in ({"effort": "high"}, {"fallback_model": "google/gemini-3.5-flash"}):
            with self.subTest(**kw), self.assertRaises(harness.HarnessError):
                self.make(**kw)

    def test_gateway_harness_rejects_alias_model_at_construction(self):
        for model in ("sonnet", "vmc/house-judge", "claude-code/anthropic/claude-opus-5"):
            with self.subTest(model=model), self.assertRaises(harness.HarnessError):
                self.make(model=model)
        with self.assertRaises(harness.HarnessError):
            harness.Harness(self.root, "qa", engine="gateway")   # the default "sonnet"

    def test_gateway_options_reach_request(self):
        h = self.make(gateway={"providers": ["google"], "has": ["structured-output"],
                               "cost_poll_tries": 2, "max_media_mb": 50})
        h.decide("look", "P", SCHEMA)
        (req,) = self.node.requests()
        self.assertEqual(req["gateway"], {"only": ["google"], "has": ["structured-output"]})
        self.assertEqual(req["costPoll"]["tries"], 2)
        with self.assertRaises(harness.HarnessError):
            self.make(gateway={"providers": ["google"], "models": ["openai/gpt-5"]})

    def test_inspect_refused_on_gateway(self):
        # The harness refuses before it builds an engine: the guard's own
        # words, no launch, and no failed decision booked or saved.
        h = self.make()
        with self.assertRaises(harness.HarnessError) as caught:
            h.inspect("pass-01", "look", SCHEMA, cwd=self.node.dir)
        self.assertIn("cannot inspect", str(caught.exception))
        self.assertEqual(self.node.count, 0)
        self.assertEqual(h.report()["decisions"], [])
        self.assertFalse((self.root / "decisions").exists())

    def test_gateway_names_are_strings(self):
        h = self.make(gateway={"providers": "google", "has": ["structured-output"]})
        h.decide("look", "P", SCHEMA)
        (req,) = self.node.requests()
        self.assertEqual(req["gateway"], {"only": ["google"], "has": ["structured-output"]})
        for bad in ({"providers": 5}, {"has": ["structured-output", 1]},
                    {"providers": {"google": True}}):
            with self.subTest(bad=bad), self.assertRaises(harness.HarnessError):
                self.make(gateway=bad)

    def test_gateway_does_not_need_yt_gateway(self):
        self.assertNotIn("YT_GATEWAY", os.environ)
        claude = ClaudeEngine(claude_engine.EngineConfig(executable=str(self.node.exe),
                                                         model="opus"))
        kw = dict(output_format="json", resume=False, tools=..., system="S",
                  schema=SCHEMA)
        argv, env = claude._build_argv(**kw), claude._popen_kwargs()["env"]
        self.assertEqual(self.make().decide("look", "P", SCHEMA), {"answer": "yes"})
        # A claude engine built after the gateway decision launches exactly as before.
        self.assertEqual(claude._build_argv(**kw), argv)
        self.assertEqual(claude._popen_kwargs()["env"], env)
        self.assertEqual(self.node.count, 1)
        # The global engine is still claude's, and the key alone reroutes nothing.
        self.assertIs(harness.ENGINE, ClaudeEngine)
        self.assertEqual(harness.ENGINE_NAME, "claude")
        self.assertEqual(ClaudeEngine(claude_engine.EngineConfig())._transport_env(
            {"AI_GATEWAY_API_KEY": KEY}), {})


class Media(Case):
    def test_decide_media_digest(self):
        clip = self.clip()
        first = self.make().decide("look", "P", SCHEMA, media=[clip])
        self.assertEqual(first, {"answer": "yes"})
        self.make().decide("look", "P", SCHEMA, media=[clip])
        self.assertEqual(self.node.count, 1, "the same bytes were asked again")
        clip.write_bytes(b"frameS")
        self.make().decide("look", "P", SCHEMA, media=[clip])
        self.assertEqual(self.node.count, 2, "a changed byte was served from the cache")
        saved = json.loads((self.root / "decisions" / "qa" / "look.json").read_text())
        h = self.make()
        sha = hashlib.sha256(b"frameS").hexdigest()
        self.assertEqual(saved["digest"], harness._digest(
            h.system, "P", SCHEMA, h._asked_as, [[sha, "video/mp4"]]))
        h.decide("plain", "P", SCHEMA)
        plain = json.loads((self.root / "decisions" / "qa" / "plain.json").read_text())
        self.assertEqual(plain["digest"], harness._digest(h.system, "P", SCHEMA, h._asked_as))
        self.assertEqual(self.node.requests()[-1]["media"], [])

    def test_media_are_hashed_without_reading_them_whole(self):
        # A full render may be hundreds of MB: the digest reads it in chunks,
        # and comes out the same as one hash of all its bytes.
        clip = self.clip(data=b"x" * (3 * 1024 * 1024 + 7))
        h = self.make()
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("whole file")):
            rows = h._media_digest([clip])
        self.assertEqual(rows, [[hashlib.sha256(clip.read_bytes()).hexdigest(), "video/mp4"]])

    def test_missing_media_fails_the_decision(self):
        with self.assertRaises(harness.HarnessError):
            self.make().decide("look", "P", SCHEMA, media=[self.node.dir / "gone.mp4"])
        self.assertEqual(self.node.count, 0)


class Retry(Case):
    def test_invalid_shape_gets_harness_retry(self):
        clip = self.clip()
        self.node.replies(ok({"verdict": "fine"}), ok({"answer": "yes"}))
        h = self.make()
        self.assertEqual(h.decide("look", "P", SCHEMA, media=[clip]), {"answer": "yes"})
        first, second = self.node.requests()
        self.assertEqual(self.node.count, 2)
        self.assertIn(harness.RETRY_INSTRUCTION.split("%s")[0].strip(), second["prompt"])
        self.assertEqual(first["media"], second["media"])
        self.assertEqual(len(first["media"]), 1)
        self.assertAlmostEqual(h.usage.cost_usd, 0.0024)
        self.assertEqual(h.usage.calls, 2)
        self.assertAlmostEqual(h.report()["cost_usd"], 0.0024)

    def test_non_json_reply_costs_one_call(self):
        self.node.replies(err("schema", 2, message="No object generated: could not parse",
                              usage=USAGE))
        h = self.make()
        with self.assertRaises(harness.HarnessError):
            h.decide("look", "P", SCHEMA)
        self.assertEqual(self.node.count, 1)
        self.assertEqual((h.usage.calls, h.usage.output_tokens), (1, 300))
        (row,) = h.report()["decisions"]
        self.assertEqual(row["kind"], "failed")


if __name__ == "__main__":
    unittest.main()
