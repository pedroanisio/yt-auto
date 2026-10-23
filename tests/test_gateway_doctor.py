"""`yt doctor` and the gateway engine (AUTO-124).

A by_model row the gateway engine booked says how its cost was found
(cost_gateway_calls, cost_generation_calls, cost_table_calls,
cost_unpriced_calls). A row the gateway priced itself is not "unpriced" for
want of a table row; a row it could not price still is. The environment
check shows a "gateway engine" block only on a checkout where the Node
program was installed, and never prints the key.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gateway_fakes import KEY, MODEL, FakeNode  # noqa: E402
from ytstudio import doctor, spend, workspace  # noqa: E402
from ytstudio.core import write_json  # noqa: E402


class Spend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        write_json(self.root / "idea.json", {"title": "T", "genre": "g", "description": "d"})

    def findings(self, row: dict) -> list:
        write_json(self.root / "qa.json", {"usage": spend.record(
            0.01, MODEL, 1, by_model={MODEL: {"calls": 1, "cost_usd": 0.01, **row}})})
        report = doctor.check_project(workspace.Project(self.root))
        return [f for f in report.findings if f.area == "spend"]

    def test_doctor_gateway_row_priced(self):
        self.assertEqual(self.findings({"cost_gateway_calls": 1}), [])
        self.assertEqual(self.findings({"cost_generation_calls": 2}), [])
        warned = self.findings({"cost_gateway_calls": 1, "cost_unpriced_calls": 1})
        self.assertEqual(len(warned), 1)
        self.assertIn(MODEL, warned[0].message)
        self.assertEqual(len(self.findings({"cost_unpriced_calls": 1})), 1)

    def test_a_row_without_the_counters_is_judged_as_before(self):
        self.assertEqual(len(self.findings({})), 1)

    def test_a_table_priced_row_is_quiet_when_gateway_prices_has_it(self):
        with mock.patch.dict(os.environ, {"GATEWAY_PRICES": json.dumps(
                {MODEL: {"input": 0.75, "output": 3.75}})}):
            self.assertEqual(self.findings({"cost_table_calls": 1}), [])


class Environment(unittest.TestCase):
    def setUp(self):
        self.node = FakeNode(self)

    def block(self, **env) -> list:
        report = doctor.Report("environment")
        with mock.patch.dict(os.environ, env):
            doctor.check_gateway_engine(report)
        return [f for f in report.findings if f.area == "gateway engine"]

    def test_doctor_engine_block(self):
        rows = self.block()
        self.assertEqual([f.level for f in rows], ["ok", "ok", "ok"], [f.message for f in rows])
        self.assertIn(str(self.node.exe), rows[0].message)
        self.assertIn("v24.11.0", rows[0].message)
        self.assertIn("7.0.150", rows[1].message)
        self.assertIn("AI_GATEWAY_API_KEY", rows[2].message)
        self.assertNotIn(KEY, " ".join(f.message + f.fix for f in rows))

    def test_no_key_row_when_yt_gateway_is_set(self):
        rows = self.block(YT_GATEWAY="vercel")
        self.assertEqual(len(rows), 2)
        self.assertFalse([f for f in rows if "AI_GATEWAY_API_KEY" in f.message])

    def test_a_missing_key_is_a_warning(self):
        os.environ.pop("AI_GATEWAY_API_KEY")
        rows = self.block()
        self.assertEqual(rows[2].level, "warn")

    def test_old_node_and_another_ai_fail(self):
        (self.node.program_dir / "node_modules" / "ai" / "package.json").write_text(
            json.dumps({"version": "6.0.314"}))
        rows = self.block(FAKE_NODE_VERSION="v20.11.1")
        self.assertEqual([f.level for f in rows[:2]], ["fail", "fail"])
        self.assertIn("npm ci --ignore-scripts", rows[1].fix)

    def test_no_block_without_node_modules(self):
        import shutil
        shutil.rmtree(self.node.program_dir / "node_modules")
        self.assertEqual(self.block(), [])

    def test_check_environment_carries_the_block(self):
        with mock.patch.object(doctor.render, "hosts", return_value=[]):
            report = doctor.check_environment(Path(tempfile.gettempdir()))
        self.assertTrue([f for f in report.findings if f.area == "gateway engine"])


if __name__ == "__main__":
    unittest.main()
