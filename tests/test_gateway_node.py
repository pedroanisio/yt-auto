"""Runs gateway/test/engine.test.mjs with `node --test` (AUTO-124).

The command is package.json's own "test" script, so `npm test` and this
test run the same thing. It names the file: since Node 21 the arguments of
`node --test` are glob patterns and a bare directory is not searched.

The Node program's own tests inject generateText, fetch and makeAgent, so
they need no gateway, but they import `ai` and `undici` and so need Node 22
or later and `npm ci --ignore-scripts` in gateway/. Without both the test is
skipped and says which is missing; the Python side is tested with a fake
node in test_gateway_engine.py either way.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gateway_engine  # noqa: E402

GATEWAY = ROOT / "gateway"


class NodeTests(unittest.TestCase):
    def test_engine_mjs(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not on PATH")
        major = gateway_engine.node_major(node)
        if major is None or major < gateway_engine.MIN_NODE:
            self.skipTest(f"node {major} is older than {gateway_engine.MIN_NODE}")
        if gateway_engine.installed_ai_version(GATEWAY) is None:
            self.skipTest("run `cd gateway && npm ci --ignore-scripts` first")
        script = json.loads((GATEWAY / "package.json").read_text())["scripts"]["test"]
        argv = shlex.split(script)
        self.assertEqual(argv[0], "node", script)
        proc = subprocess.run([node, *argv[1:]], cwd=GATEWAY, capture_output=True,
                              text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout[-4000:] + proc.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
