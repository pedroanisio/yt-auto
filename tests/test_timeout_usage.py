"""A call cut off by its timeout still spent tokens: the engine books what the
stream carried before the cut, and vfx, keyframe and probes record it, marked
as from a timed-out call. No model runs: a fake child process and fake engines.
"""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import claude_engine
from claude_engine import ClaudeEngine, ClaudeExecutionError, EngineConfig, Usage
from ytstudio import harness, keyframe, probes, vfx


def assistant(mid, model="m-1", **usage):
    return json.dumps({"type": "assistant", "message": {
        "id": mid, "model": model, "usage": usage,
        "content": [{"type": "text", "text": "working"}]}})


class FakeProc:
    """A child that prints `lines`, then hangs until killed (or ends cleanly)."""

    def __init__(self, lines, hang=True):
        self.lines, self.hang = lines, hang
        self._dead = threading.Event()
        self.returncode = None
        self.stdin = mock.Mock()
        self.stderr = mock.Mock(read=lambda: "")
        self.stdout = self._out()

    def _out(self):
        yield from (line + "\n" for line in self.lines)
        if self.hang:
            self._dead.wait(5)

    def kill(self):
        self.returncode = -9
        self._dead.set()

    def poll(self):
        return self.returncode

    def wait(self):
        return self.returncode if self.returncode is not None else 0


def run_stream(lines, hang=True, prices=None, timeout=0.05):
    engine = ClaudeEngine(EngineConfig(executable="python3", timeout=timeout,
                                       max_retries=0))
    env = {"CLAUDE_PRICES": json.dumps(prices)} if prices else {}
    with mock.patch.object(claude_engine.subprocess, "Popen",
                           return_value=FakeProc(lines, hang)), \
            mock.patch.dict(claude_engine.os.environ, env):
        try:
            list(engine.stream("go"))
            error = None
        except ClaudeExecutionError as exc:
            error = exc
    return engine, error


class EngineBooksTimedOutSpend(unittest.TestCase):
    def test_a_timeout_books_the_tokens_the_stream_carried(self):
        engine, error = run_stream([
            assistant("a", input_tokens=10, output_tokens=5,
                      cache_read_input_tokens=1000),
            assistant("b", input_tokens=20, output_tokens=7,
                      cache_read_input_tokens=2000)])
        self.assertIn("timed out", str(error))
        u = engine.usage
        self.assertEqual((u.input_tokens, u.output_tokens, u.cache_read_tokens),
                         (30, 12, 3000))
        self.assertEqual(u.timed_out_calls, 1)
        self.assertEqual(u.timed_out_tokens["output_tokens"], 12)

    def test_one_message_repeated_per_block_counts_once(self):
        engine, _ = run_stream([assistant("a", input_tokens=10, output_tokens=2),
                                assistant("a", input_tokens=10, output_tokens=9)])
        self.assertEqual((engine.usage.input_tokens, engine.usage.output_tokens), (10, 9))

    def test_a_priced_model_books_an_estimated_cost(self):
        engine, _ = run_stream(
            [assistant("a", input_tokens=1_000_000, output_tokens=1_000_000)],
            prices={"m-1": {"input": 2.0, "cached_input": 0.5, "output": 10.0}})
        self.assertAlmostEqual(engine.usage.cost_usd, 12.0)
        self.assertAlmostEqual(engine.usage.timed_out_cost_usd, 12.0)
        self.assertTrue(engine.usage.timed_out_cost_estimated)

    def test_a_model_the_cli_reports_is_priced_without_any_configuration(self):
        # AUTO-73: the table in ytstudio/spend.py reaches the engine through
        # the harness, so a timed-out call on a real model is billed.
        with mock.patch.dict(claude_engine.os.environ, {"CLAUDE_PRICES": ""}):
            engine, error = run_stream([assistant(
                "a", model="claude-sonnet-5-5", input_tokens=100_000, output_tokens=10_000,
                cache_read_input_tokens=1_000_000)])
        self.assertIn("timed out", str(error))
        self.assertGreater(engine.usage.cost_usd, 0.0)
        self.assertAlmostEqual(engine.usage.timed_out_cost_usd, engine.usage.cost_usd)
        self.assertTrue(engine.usage.timed_out_cost_estimated)

    def test_an_unpriced_model_books_tokens_and_says_the_cost_is_unknown(self):
        engine, _ = run_stream([assistant("a", input_tokens=5, output_tokens=5)])
        self.assertEqual(engine.usage.cost_usd, 0.0)
        self.assertFalse(engine.usage.timed_out_cost_estimated)
        self.assertEqual(engine.usage.output_tokens, 5)

    def test_a_finished_call_is_billed_from_its_result_only(self):
        result = json.dumps({"type": "result", "subtype": "success", "result": "ok",
                             "total_cost_usd": 0.5,
                             "usage": {"input_tokens": 11, "output_tokens": 6}})
        engine, error = run_stream([assistant("a", input_tokens=10, output_tokens=5),
                                    result], hang=False)
        self.assertIsNone(error)
        self.assertEqual(engine.usage.output_tokens, 6)
        self.assertEqual(engine.usage.timed_out_calls, 0)
        self.assertAlmostEqual(engine.usage.cost_usd, 0.5)

    def test_merge_carries_the_timed_out_account(self):
        engine, _ = run_stream([assistant("a", input_tokens=3, output_tokens=4)])
        total = Usage()
        total.merge(engine.usage)
        self.assertEqual(total.timed_out_calls, 1)
        self.assertEqual(total.timed_out_tokens["output_tokens"], 4)
        again = Usage(**json.loads(json.dumps(total.as_dict())))
        self.assertEqual(again.timed_out_calls, 1)


class TimingOutEngine:
    """Stands in for ClaudeEngine: spends, then is cut off by the timeout."""

    def __init__(self, config):
        self.config = config
        self.usage = Usage()
        self.session_id = None

    def stream(self, prompt, system=None, **kw):
        self.usage.add_partial(
            {"input_tokens": 7, "output_tokens": 53_000,
             "cache_read_input_tokens": 2_400_000}, cost=2.0, estimated=True)
        yield claude_engine.Event(type="assistant", text="working")
        raise ClaudeExecutionError("claude timed out after 1s", transient=True)


class StagesRecordIt(unittest.TestCase):
    def setUp(self):
        self._engine = harness.ENGINE
        harness.ENGINE = TimingOutEngine

    def tearDown(self):
        harness.ENGINE = self._engine

    def test_vfx_books_the_timed_out_author_into_its_usage(self):
        planner = vfx.VfxPlanner.__new__(vfx.VfxPlanner)
        planner.usage = Usage()
        planner._account_lock = threading.Lock()
        planner.verbose = False
        with tempfile.TemporaryDirectory() as tmp:
            planner.ws = mock.Mock(root=Path(tmp))
            engine = TimingOutEngine(EngineConfig())
            _, timed_out = planner._stream(engine, 1, "go")
            planner._account(engine.usage)
        self.assertTrue(timed_out)
        self.assertEqual(planner.usage.output_tokens, 53_000)
        self.assertAlmostEqual(planner.usage.cost_usd, 2.0)
        self.assertEqual(planner.usage.timed_out_calls, 1)
        self.assertEqual(vfx.timed_out_usage(engine.usage)["calls"], 1)

    def test_keyframe_books_it(self):
        stage = keyframe.KeyframeStage.__new__(keyframe.KeyframeStage)
        stage.usage = Usage()
        stage.verbose = False
        stage.root = Path(tempfile.gettempdir())
        engine = TimingOutEngine(EngineConfig())
        stage._stream(engine, "go")
        self.assertEqual(stage.usage.timed_out_calls, 1)
        self.assertAlmostEqual(stage.usage.cost_usd, 2.0)

    def test_probes_author_records_cost_and_the_timeout(self):
        probe = mock.Mock(id="p1")
        with tempfile.TemporaryDirectory() as tmp:
            got = probes.author(Path(tmp), probe, model="m", effort=None,
                                budget_usd=1.0, verbose=False)
        self.assertAlmostEqual(got["cost_usd"], 2.0)
        self.assertEqual(got["timed_out_usage"]["calls"], 1)


if __name__ == "__main__":
    unittest.main()
