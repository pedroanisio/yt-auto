"""`yt props` on a derived run: the model picks the phrases and the kept
results, and "nothing fits" is a valid answer. No network, no model: the
Poly Haven seams and the harness engine are fakes."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from claude_engine import Usage  # noqa: E402
from ytstudio import cli, harness  # noqa: E402
from test_props import PropsCase  # noqa: E402


class FakeEngine:
    """Answers the phrase question and the keep question from a script."""

    phrases: list = []
    keep_titles: list = []      # names of the candidates to keep; [] keeps none
    calls: list = []

    def __init__(self, config):
        self.config = config
        self.usage = Usage()

    def ask_json(self, prompt, schema, system=None):
        if "keep" not in schema["properties"]:
            FakeEngine.calls.append({"name": "phrases", "prompt": prompt, "system": system})
            return {"phrases": list(FakeEngine.phrases)}
        FakeEngine.calls.append({"name": "keep", "prompt": prompt, "system": system})
        rows = []
        for line in prompt.splitlines():
            head, _, rest = line.partition(". ")
            if head.isdigit() and any(t in rest for t in FakeEngine.keep_titles):
                rows.append({"n": int(head), "as": "it is the thing the shot shows"})
        return {"keep": rows}


class Decided(PropsCase):
    def setUp(self):
        super().setUp()
        self._engine = harness.ENGINE
        harness.ENGINE = FakeEngine
        FakeEngine.phrases, FakeEngine.keep_titles, FakeEngine.calls = [], [], []
        (self.root / "script.json").write_text(json.dumps({"sections": [{"shots": [
            {"label": "1.1", "visual": "an office",
             "asset": "a metal desk, a hand plane"}]}]}))

    def tearDown(self):
        harness.ENGINE = self._engine
        super().tearDown()

    def run_stage(self):
        args = cli.build_parser().parse_args(
            ["props", "--project", str(self.root), "--quiet", "--limit", "3",
             "--kinds", "model"])
        return args.run(args)

    def manifest(self):
        return json.loads((self.root / "props.json").read_text())

    def test_the_model_keeps_what_fits_and_only_that_is_fetched(self):
        FakeEngine.phrases = ["vintage"]            # finds the desk and the plane
        FakeEngine.keep_titles = ["Metal Office Desk"]
        self.assertEqual(self.run_stage(), 0)
        data = self.manifest()
        self.assertEqual([i["id"] for i in data["items"]], ["metal_office_desk"])
        self.assertEqual(data["phrases"], ["vintage"])
        self.assertFalse(any("plane" in u for u in self.net.byte_urls + self.net.json_urls))

    def test_a_subject_the_catalogue_cannot_serve_keeps_nothing(self):
        FakeEngine.phrases = ["vintage"]
        FakeEngine.keep_titles = []
        self.assertEqual(self.run_stage(), 0)
        self.assertEqual(self.manifest()["items"], [])
        self.assertEqual(self.net.byte_urls, [])
        self.assertFalse(any("/files/" in u for u in self.net.json_urls))
        self.assertEqual([c["name"] for c in FakeEngine.calls], ["phrases", "keep"])

    def test_none_fit_at_the_phrase_step_searches_nothing(self):
        FakeEngine.phrases = []
        self.assertEqual(self.run_stage(), 0)
        self.assertEqual(self.manifest()["items"], [])
        self.assertEqual([c["name"] for c in FakeEngine.calls], ["phrases"])
        self.assertEqual(self.net.byte_urls, [])

    def test_the_phrase_question_carries_the_project_text_and_the_vocabulary(self):
        self.run_stage()
        prompt = FakeEngine.calls[0]["prompt"]
        self.assertIn("a metal desk, a hand plane", prompt)
        self.assertIn("hand tool", prompt)

    def test_a_model_failure_is_an_error_not_a_keyword_fallback(self):
        class Down(FakeEngine):
            def ask_json(self, prompt, schema, system=None):
                raise harness.ClaudeError("down")
        harness.ENGINE = Down
        self.assertEqual(self.run_stage(), 1)
        self.assertFalse((self.root / "props.json").exists())
        self.assertEqual(self.net.byte_urls, [])


if __name__ == "__main__":
    unittest.main()
