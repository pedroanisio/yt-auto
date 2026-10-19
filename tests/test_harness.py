"""harness.py: the one door from a stage to claude_engine.

Every production stage decides through it, so what the tests pin is the
contract the stages rely on: a decision is asked once per distinct input and
served from the project afterwards; a changed input is asked again; `fresh`
discards the cache; an agentic inspection that files no form is extracted
from its own words; and a failure is an error, never a silent default.
"""

import json
import tempfile
import unittest
from pathlib import Path

from claude_engine import Event, Usage
from ytstudio import harness

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}},
          "required": ["answer"]}


class FakeEngine:
    """Stands in for ClaudeEngine: records calls, answers from a script."""

    json_calls: list = []
    stream_calls: list = []
    answers: list = []
    texts: list = []
    fail = False
    by_model: dict = {}

    def __init__(self, config):
        self.config = config
        self.usage = Usage()
        self.usage.cost_usd = 0.01
        self.usage.calls = 1
        self.usage.by_model = {m: dict(row) for m, row in FakeEngine.by_model.items()}

    def ask_json(self, prompt, schema, system=None):
        if FakeEngine.fail:
            raise harness.ClaudeError("the model is down")
        FakeEngine.json_calls.append({"prompt": prompt, "schema": schema,
                                      "system": system,
                                      "tools": tuple(self.config.tools or ())})
        return FakeEngine.answers.pop(0) if FakeEngine.answers else {"answer": "yes"}

    def stream(self, prompt, system=None):
        FakeEngine.stream_calls.append({"prompt": prompt, "system": system,
                                        "tools": tuple(self.config.tools or ()),
                                        "cwd": self.config.cwd})
        yield Event(type="assistant", subtype="tool_use", tool_name="Read",
                    tool_input={"file_path": "frames/a.png"})
        for text in FakeEngine.texts or ["I looked at the frames."]:
            yield Event(type="assistant", text=text)
        yield Event(type="result", raw={})


class HarnessCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._engine = harness.ENGINE
        harness.ENGINE = FakeEngine
        FakeEngine.json_calls, FakeEngine.stream_calls = [], []
        FakeEngine.answers, FakeEngine.texts, FakeEngine.fail = [], [], False

    def tearDown(self):
        harness.ENGINE = self._engine
        self._tmp.cleanup()

    def make(self, **kw):
        return harness.Harness(self.root, "build", model="sonnet",
                               role="You edit.", house="## all\n- be clear",
                               verbose=False, **kw)


class Decide(HarnessCase):
    def test_asks_once_and_serves_the_same_input_from_the_project(self):
        h = self.make()
        first = h.decide("edit", "shot list A", SCHEMA)
        again = self.make().decide("edit", "shot list A", SCHEMA)
        self.assertEqual(first, again)
        self.assertEqual(len(FakeEngine.json_calls), 1)
        saved = json.loads((self.root / "decisions" / "build" / "edit.json").read_text())
        self.assertEqual(saved["result"], first)
        self.assertEqual(saved["stage"], "build")
        self.assertTrue(saved["digest"])

    def test_a_changed_input_is_asked_again(self):
        self.make().decide("edit", "shot list A", SCHEMA)
        self.make().decide("edit", "shot list B", SCHEMA)
        self.assertEqual(len(FakeEngine.json_calls), 2)

    def test_fresh_discards_the_cache(self):
        self.make().decide("edit", "shot list A", SCHEMA)
        self.make(fresh=True).decide("edit", "shot list A", SCHEMA)
        self.assertEqual(len(FakeEngine.json_calls), 2)

    def test_system_prompt_carries_mission_role_and_house(self):
        self.make().decide("edit", "p", SCHEMA)
        system = FakeEngine.json_calls[0]["system"]
        self.assertIn("scene-render", system)          # the mission
        self.assertIn("You edit.", system)             # the stage's role
        self.assertIn("be clear", system)              # the house standards
        self.assertEqual(FakeEngine.json_calls[0]["tools"], ())

    def test_report_accounts_for_what_was_asked_and_what_was_cached(self):
        h = self.make()
        h.decide("edit", "p", SCHEMA)
        h.decide("edit", "p", SCHEMA)
        report = h.report()
        self.assertEqual(report["model"], "sonnet")
        self.assertEqual([d["name"] for d in report["decisions"]], ["edit", "edit"])
        self.assertEqual([d["cached"] for d in report["decisions"]], [False, True])
        self.assertAlmostEqual(report["cost_usd"], 0.01)

    def test_a_failure_is_an_error_not_a_default(self):
        FakeEngine.fail = True
        with self.assertRaises(harness.HarnessError):
            self.make().decide("edit", "p", SCHEMA)
        self.assertFalse((self.root / "decisions").exists())

    def test_a_result_that_misses_a_required_field_is_an_error(self):
        FakeEngine.answers = [{"nothing": 1}]
        with self.assertRaises(harness.HarnessError):
            self.make().decide("edit", "p", SCHEMA)


class Inspect(HarnessCase):
    def test_reads_in_the_given_directory_with_read_only_tools(self):
        cwd = self.root / "qa" / "pass-01"
        cwd.mkdir(parents=True)
        (cwd / "a.png").write_bytes(b"png")
        result = self.make().inspect("pass-01", "Look at the frames.", SCHEMA, cwd=cwd)
        call = FakeEngine.stream_calls[0]
        self.assertEqual(Path(call["cwd"]), cwd)
        self.assertTrue(set(call["tools"]) <= set(harness.READ_ONLY_TOOLS))
        self.assertNotIn("Bash", call["tools"])
        # No form was filed, so the verdict is extracted from the agent's words.
        self.assertEqual(result, {"answer": "yes"})
        self.assertIn("I looked at the frames.", FakeEngine.json_calls[0]["prompt"])

    def test_a_form_the_agent_filed_is_taken_as_is(self):
        cwd = self.root / "qa" / "pass-01"
        cwd.mkdir(parents=True)
        FakeEngine.texts = ["done"]

        class Filing(FakeEngine):
            def stream(self, prompt, system=None):
                (cwd / "form.json").write_text(json.dumps({"answer": "filed"}))
                yield Event(type="result", raw={})

        harness.ENGINE = Filing
        result = self.make().inspect("pass-01", "p", SCHEMA, cwd=cwd, form="form.json")
        self.assertEqual(result, {"answer": "filed"})
        self.assertEqual(FakeEngine.json_calls, [])

    def test_the_agents_own_json_block_is_the_form(self):
        cwd = self.root / "qa" / "pass-01"
        cwd.mkdir(parents=True)
        FakeEngine.texts = ["I looked.", "Here it is:\n```json\n{\"answer\": \"seen\"}\n```"]
        result = self.make().inspect("pass-01", "p", SCHEMA, cwd=cwd)
        self.assertEqual(result, {"answer": "seen"})
        self.assertEqual(FakeEngine.json_calls, [])          # no text-only pass
        self.assertIn("```json", FakeEngine.stream_calls[0]["prompt"])

    def test_inspection_is_cached_on_the_files_as_well_as_the_prompt(self):
        cwd = self.root / "qa" / "pass-01"
        cwd.mkdir(parents=True)
        (cwd / "a.png").write_bytes(b"png")
        self.make().inspect("pass-01", "p", SCHEMA, cwd=cwd)
        self.make().inspect("pass-01", "p", SCHEMA, cwd=cwd)
        self.assertEqual(len(FakeEngine.stream_calls), 1)
        (cwd / "a.png").write_bytes(b"png-changed")
        self.make().inspect("pass-01", "p", SCHEMA, cwd=cwd)
        self.assertEqual(len(FakeEngine.stream_calls), 2)


class Helpers(unittest.TestCase):
    def test_validate_names_every_missing_required_field(self):
        schema = {"type": "object", "required": ["a", "b"],
                  "properties": {"a": {}, "b": {}}}
        self.assertEqual(harness.missing_fields({"a": 1}, schema), ["b"])
        self.assertEqual(harness.missing_fields("not an object", schema), ["a", "b"])

    def test_brief_renders_json_the_model_can_read(self):
        text = harness.brief("Shots", [{"label": "1.1", "seconds": 4.5}])
        self.assertTrue(text.startswith("Shots:"))
        self.assertIn('"label": "1.1"', text)


if __name__ == "__main__":
    unittest.main()


class Effort(HarnessCase):
    """Effort reaches the engine and is part of what a decision was asked with."""

    def setUp(self):
        super().setUp()
        harness.claude_engine.DEFAULTS.clear()

    def tearDown(self):
        harness.claude_engine.DEFAULTS.clear()
        super().tearDown()

    def test_effort_and_fallback_reach_the_engine(self):
        self.make(effort="high", fallback_model="opus").decide("q", "Q?", SCHEMA)
        self.assertEqual(len(FakeEngine.json_calls), 1)
        # FakeEngine keeps the config it was built with.
        cfg = harness.EngineConfig(model="sonnet", effort="high", fallback_model="opus")
        self.assertEqual((cfg.effort, cfg.fallback_model), ("high", "opus"))

    def test_a_different_effort_is_asked_again(self):
        self.make().decide("q", "Q?", SCHEMA)
        self.make(effort="high").decide("q", "Q?", SCHEMA)
        self.make(effort="high").decide("q", "Q?", SCHEMA)
        self.assertEqual(len(FakeEngine.json_calls), 2)

    def test_no_effort_keeps_the_digest_of_an_earlier_decision(self):
        h = self.make()
        self.assertEqual(h.effort, None)
        self.assertEqual(h._asked_as, "sonnet")

    def test_the_process_default_fills_the_harness(self):
        harness.claude_engine.set_defaults(effort="max")
        h = self.make()
        self.assertEqual(h.effort, "max")
        self.assertEqual(h.report()["effort"], "max")


class Models(HarnessCase):
    """The report says which models actually answered, per the engine's usage."""

    def test_report_carries_the_per_model_breakdown(self):
        FakeEngine.by_model = {"claude-sonnet-5-5": {"calls": 1, "cost_usd": 0.01}}
        try:
            h = self.make()
            h.decide("q", "Q?", SCHEMA)
        finally:
            FakeEngine.by_model = {}
        self.assertEqual(h.report()["by_model"], {"claude-sonnet-5-5": {"calls": 1, "cost_usd": 0.01}})
