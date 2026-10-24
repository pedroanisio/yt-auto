"""Bridge transport, failure boundaries, evidence and stage integration."""
import importlib.util
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from bridge_engine import BridgeEngine, BridgeError, pending, read, submit
from claude_engine import EngineConfig, Usage
from ytstudio import harness, research, script, spend
from ytstudio.core import Idea

SCHEMA = {"type": "object", "properties": {"value": {"type": "integer", "minimum": 1}},
          "required": ["value"], "additionalProperties": False}


@unittest.skipUnless(importlib.util.find_spec("jsonschema"),
                     "optional bridge dependency: pip install -r requirements-bridge.txt")
class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, {"YT_BRIDGE_DIR": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.addCleanup(self.pool.shutdown)

    def engine(self, **kwargs):
        return BridgeEngine(EngineConfig(tools=(), timeout=2, **kwargs))

    def request(self, count=1):
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            found = list(pending(self.root))
            if len(found) >= count:
                return found if count > 1 else found[0]
            time.sleep(0.01)
        self.fail("No bridge request appeared")

    def response(self, request, **more):
        return {"request_id": request["id"], "request_digest": request["digest"],
                "status": "ok", "text": "", "structured": {"value": 2}, **more}

    def test_schema_and_binding_reject_bad_answers_then_accept_valid(self):
        engine = self.engine()
        future = self.pool.submit(engine.ask_json, "Choose", SCHEMA)
        req = self.request()
        for change in ({"structured": {"value": "wrong"}}, {"structured": {"value": 0}},
                       {"structured": {"value": 2, "extra": True}},
                       {"request_digest": "wrong"}, {"request_id": "wrong"}):
            with self.assertRaises(BridgeError):
                submit(self.root, req["id"], self.response(req, **change))
        submit(self.root, req["id"], self.response(req))
        self.assertEqual(future.result(), {"value": 2})
        self.assertEqual(engine.usage.unmetered_calls, 1)
        total = Usage()
        total.merge(engine.usage)
        self.assertEqual(total.as_dict()["unmetered_calls"], 1)
        self.assertIn("incomplete", str(total))
        self.assertIsNone(engine.log[0]["total_cost_usd"])
        recorded = spend.from_report({"calls": 1, "cost_usd": 0, "unmetered_calls": 1})
        self.assertEqual(recorded["unmetered_calls"], 1)
        self.assertIsNone(spend.cost_in({"usage": recorded}))
        with self.assertRaises(BridgeError):
            submit(self.root, req["id"], self.response(req))

    def test_concurrent_requests_cannot_cross(self):
        a = self.pool.submit(self.engine().ask_json, "A", SCHEMA)
        b = self.pool.submit(self.engine().ask_json, "B", SCHEMA)
        requests = self.request(2)
        for req in reversed(requests):
            submit(self.root, req["id"], self.response(req, structured={"value": 1 if req["prompt"] == "A" else 2}))
        self.assertEqual(a.result(), {"value": 1})
        self.assertEqual(b.result(), {"value": 2})

    def test_timeout_and_explicit_worker_failure(self):
        engine = BridgeEngine(EngineConfig(tools=(), timeout=0.05))
        with self.assertRaisesRegex(BridgeError, "timed out"):
            engine.ask("Timeout")
        self.assertEqual(list(pending(self.root)), [])
        expired = read(next(self.root.glob("*/request.json")))
        with self.assertRaisesRegex(BridgeError, "no longer active"):
            submit(self.root, expired["id"], self.response(expired))
        future = self.pool.submit(self.engine().ask, "Fail")
        req = self.request()
        submit(self.root, req["id"], self.response(req, status="error", error="Source unavailable"))
        with self.assertRaisesRegex(BridgeError, "Source unavailable"):
            future.result()

    def test_research_receives_web_evidence_and_no_silent_memory_fallback(self):
        saved, name = harness.ENGINE, harness.ENGINE_NAME
        harness.use_engine("bridge")
        self.addCleanup(harness.use_engine, name)
        self.addCleanup(setattr, harness, "ENGINE", saved)
        researcher = research.Researcher(Idea("Moon", "explainer", "Rotation"), None, verbose=False)
        question = {"question": "How does the Moon rotate?", "why": "Explain", "searches": ["NASA Moon rotation"]}
        future = self.pool.submit(researcher._research_one, question)
        req = self.request()
        with self.assertRaisesRegex(BridgeError, "Missing actual"):
            submit(self.root, req["id"], self.response(req, text="Unverified"))
        # These are synthetic fixtures, not claims of real browsing.
        evidence = [
            {"tool": "WebSearch", "input": {"query": "NASA Moon rotation"},
             "reference": "fixture-search", "summary": "Synthetic search fixture"},
            {"tool": "WebFetch", "input": {"url": "https://example.org/moon"},
             "reference": "fixture-page", "summary": "Synthetic page fixture"}]
        submit(self.root, req["id"], self.response(req, text="Fixture notes", evidence=evidence))
        result = future.result()
        self.assertEqual(result["searches"], 1)
        self.assertEqual(result["visited"], ["https://example.org/moon"])
        self.assertEqual(result["notes"], "Fixture notes")

    def test_script_uses_bridge_and_preserves_system_prompt(self):
        saved, name = harness.ENGINE, harness.ENGINE_NAME
        harness.use_engine("bridge")
        self.addCleanup(harness.use_engine, name)
        self.addCleanup(setattr, harness, "ENGINE", saved)
        writer = script.Screenwriter(Idea("Moon", "explainer", "Rotation"), root=self.root, verbose=False)
        future = self.pool.submit(writer._ask, "Draft", SCHEMA)
        req = self.request()
        self.assertIn(script.SYSTEM, req["system"])
        submit(self.root, req["id"], self.response(req))
        self.assertEqual(future.result(), {"value": 2})
        self.assertEqual(writer.usage.unmetered_calls, 1)

    def test_unsupported_permissions_budgets_resume_and_traversal_fail(self):
        for config in (EngineConfig(tools=("Bash",)), EngineConfig(tools=(), max_turns_budget_usd=1),
                       EngineConfig(tools=(), max_turns=3)):
            with self.assertRaises(BridgeError):
                BridgeEngine(config).ask("Refuse")
        with self.assertRaises(BridgeError):
            self.engine().ask("Resume", resume=True)
        with self.assertRaises(BridgeError):
            submit(self.root, "../other", {})
        self.assertEqual(list(pending(self.root)), [])

    def test_spawn_stays_bridge_and_bad_tool_evidence_is_rejected(self):
        self.assertIsInstance(self.engine().spawn(), BridgeEngine)
        future = self.pool.submit(self.engine().ask, "Pure reasoning")
        req = self.request()
        with self.assertRaises(BridgeError):
            submit(self.root, req["id"], self.response(req, evidence=[{"tool": "Bash"}]))
        submit(self.root, req["id"], self.response(req, text="Done"))
        self.assertEqual(future.result().text, "Done")


if __name__ == "__main__":
    unittest.main()
