"""Every stage builds its engine through the harness.

`harness.ENGINE` is the one switch (YT_ENGINE=codex moves it), so a stage that
constructs `ClaudeEngine` itself is a stage the switch does not reach. These
tests pin that no stage module calls `ClaudeEngine(` and that swapping
`harness.ENGINE` reaches each stage. No model is called: the swap is a fake.
"""

import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from claude_engine import ClaudeEngine, EngineConfig, Usage
from ytstudio import assess, harness, ideas, research, script, sound, styles
from ytstudio.core import Idea

STAGES = (assess, ideas, research, script, styles, sound)


class Reached(Exception):
    """Raised by the fake engine once a stage has asked it for anything."""


class SwappedEngine:
    built: list = []

    def __init__(self, config):
        self.config = config
        self.usage = Usage()
        SwappedEngine.built.append(config)

    def ask_json(self, *a, **kw):
        raise Reached

    def stream(self, *a, **kw):
        return iter(())

    def agent(self, *a, **kw):
        raise Reached


class RoutingTest(unittest.TestCase):
    def setUp(self):
        SwappedEngine.built = []
        self._engine = harness.ENGINE
        harness.ENGINE = SwappedEngine
        self.addCleanup(setattr, harness, "ENGINE", self._engine)
        # A stage that bypasses the harness must fail here, never reach a model.
        for name in ("ask_json", "stream", "agent"):
            patcher = mock.patch.object(ClaudeEngine, name, side_effect=AssertionError(
                "the real ClaudeEngine was used"))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.root = Path(tempfile.mkdtemp())
        self.idea = Idea(title="A test film", genre="cinematic", description="one take")

    def test_no_stage_module_calls_claude_engine_directly(self):
        for module in STAGES:
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\bClaudeEngine\(", source),
                              f"{module.__name__} constructs ClaudeEngine itself")

    def test_the_helper_follows_engine_and_a_patched_name(self):
        cfg = EngineConfig(tools=())
        self.assertIsInstance(harness.build_engine(cfg, ClaudeEngine), SwappedEngine)
        self.assertIsInstance(harness.build_engine(cfg), SwappedEngine)
        harness.ENGINE = self._engine
        other = mock.Mock(return_value="mine")
        self.assertEqual(harness.build_engine(cfg, other), "mine")
        self.assertIsInstance(harness.build_engine(cfg, ClaudeEngine), ClaudeEngine)

    def test_script_reaches_the_swapped_engine(self):
        writer = script.Screenwriter(self.idea, root=self.root, verbose=False)
        with self.assertRaises(Reached):
            writer._ask("decide", {"type": "object"})
        self.assertEqual(len(SwappedEngine.built), 1)

    def test_styles_reaches_the_swapped_engine(self):
        director = styles.Director(self.idea, root=self.root, verbose=False)
        with self.assertRaises(Reached):
            director._ask("decide", {"type": "object"})
        self.assertEqual(len(SwappedEngine.built), 1)

    def test_sound_reaches_the_swapped_engine(self):
        designer = sound.SoundDesigner(self.idea, root=self.root, verbose=False)
        with self.assertRaises(Reached):
            designer._ask("decide", {"type": "object"})
        self.assertEqual(len(SwappedEngine.built), 1)

    def test_research_reaches_the_swapped_engine(self):
        researcher = research.Researcher(self.idea, SimpleNamespace(), verbose=False)
        with self.assertRaises(Reached):
            researcher._ask_json("decide", {"type": "object"})
        self.assertEqual(len(SwappedEngine.built), 1)

    def test_ideas_reaches_the_swapped_engine(self):
        generator = ideas.Generator(EngineConfig(tools=()), verbose=False)
        self.assertIsInstance(generator._engine(), SwappedEngine)

    def test_assess_reaches_the_swapped_engine_for_both_passes(self):
        ws = SimpleNamespace(dir=self.root)
        with self.assertRaises(Reached):
            assess.investigate(ws, model="sonnet", budget_usd=1.0, verbose=False)
        self.assertEqual(len(SwappedEngine.built), 2)
        self.assertEqual(SwappedEngine.built[1].tools, ())


if __name__ == "__main__":
    unittest.main()
