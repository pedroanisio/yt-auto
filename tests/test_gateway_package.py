"""gateway/package.json pins the Node program's dependencies exactly (AUTO-124).

No node is needed: the files are read as JSON. A range (^7.0.150, ~7.0.150)
would let `npm install` pick a newer `ai` than the one the engine checks
for and the report verified, so every version is an exact one. The lockfile
is written by `npm install --package-lock-only` on a machine with Node; until
it is committed, its checks are skipped and say so.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gateway_engine  # noqa: E402

GATEWAY = ROOT / "gateway"
EXACT = re.compile(r"^\d+\.\d+\.\d+$")


class Package(unittest.TestCase):
    def setUp(self):
        self.package = json.loads((GATEWAY / "package.json").read_text())

    def test_ai_is_pinned_exactly(self):
        self.assertEqual(self.package["dependencies"]["ai"], "7.0.150")
        self.assertEqual(gateway_engine.AI_VERSION, "7.0.150")

    def test_every_dependency_is_exact(self):
        self.assertIn("undici", self.package["dependencies"])
        for name, version in self.package["dependencies"].items():
            with self.subTest(name=name):
                self.assertRegex(version, EXACT)

    def test_module_private_and_node_22(self):
        self.assertEqual(self.package["type"], "module")
        self.assertIs(self.package["private"], True)
        self.assertEqual(self.package["engines"]["node"], ">=22")

    def test_the_program_is_where_the_engine_runs_it(self):
        self.assertTrue((GATEWAY / gateway_engine.PROGRAM).is_file())
        self.assertEqual(gateway_engine.PROGRAM_DIR, GATEWAY)

    def test_node_modules_is_ignored(self):
        ignored = (ROOT / ".gitignore").read_text().splitlines()
        self.assertIn("gateway/node_modules/", ignored)

    def test_the_lockfile_resolves_the_pins(self):
        lock = GATEWAY / "package-lock.json"
        if not lock.exists():
            self.skipTest("TO VERIFY: gateway/package-lock.json is written by "
                          "`npm install --package-lock-only` on a machine with Node")
        packages = json.loads(lock.read_text())["packages"]
        self.assertEqual(packages["node_modules/ai"]["version"], "7.0.150")
        self.assertEqual(packages["node_modules/undici"]["version"],
                         self.package["dependencies"]["undici"])


if __name__ == "__main__":
    unittest.main()
