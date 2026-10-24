"""The package's two decisions (AUTO-147, part B): a draft and its review.

"package-draft" (--model) words the title, the hook, one title per chapter
and the tags, and picks the thumbnail time from the candidates code found;
"package-review" (--reviewer-model, never the author's own family) flags a
promise the narration never keeps, a claim it never makes and a chapter
title that gives a reveal away. A blocking flag earns one revise round; one
still standing makes the package "not ready". The model is never called:
`harness.ENGINE` is a fake answering by schema.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

import claude_engine
from tests.test_harness_decisions import FakeEngine
from tests.test_package import ProjectCase, answer_for
from ytstudio import harness, package


class Scripted(FakeEngine):
    """Answers in turn from `script`, a list of callables (schema -> answer);
    the last one answers whatever is asked after it."""

    script: list = []

    def ask_json(self, prompt, schema, system=None):
        FakeEngine.json_calls.append({"prompt": prompt, "schema": schema, "system": system,
                                      "model": self.config.model})
        step = Scripted.script.pop(0) if len(Scripted.script) > 1 else Scripted.script[0]
        return step(schema)


def draft(**over):
    def answer(schema):
        return dict(answer_for(schema), **over) if "thumbnail_at" in schema["properties"] \
            else answer_for(schema)
    return answer


def flagged(schema):
    if "thumbnail_at" in schema["properties"]:
        return answer_for(schema)
    return {"flags": [{"field": "title", "problem": "the narration never shows a chip printed",
                       "severity": "blocking", "fix": "promise the lens, not the chip"}]}


class Decide(ProjectCase):
    def setUp(self):
        super().setUp()
        harness.ENGINE = Scripted
        Scripted.script = [answer_for]

    def asked(self, kind: str) -> list[dict]:
        key = "thumbnail_at" if kind == "draft" else "flags"
        return [c for c in FakeEngine.json_calls if key in c["schema"]["properties"]]

    def test_a_missing_field_is_asked_again_once_then_fails(self):
        def no_title(schema):
            answer = answer_for(schema)
            answer.pop("title", None)
            return answer
        Scripted.script = [no_title]
        code, said = self.run_package()
        self.assertEqual(code, 1, said)
        self.assertEqual(len(self.asked("draft")), 2)
        self.assertIn("title", said)
        self.assertFalse((self.root / "package" / "package.json").exists())

    def test_a_thumbnail_time_outside_the_candidates_is_refused(self):
        Scripted.script = [draft(thumbnail_at=999.0)]
        code, said = self.run_package()
        self.assertEqual(code, 1, said)
        self.assertIn("thumbnail_at", said)
        self.assertEqual(self.stills, [])
        self.assertEqual(self.asked("review"), [])

    def test_one_chapter_title_per_chapter_or_the_answer_is_refused(self):
        Scripted.script = [draft(chapter_titles=["Only one"])]
        code, said = self.run_package()
        self.assertEqual(code, 1, said)
        self.assertIn("chapter", said)
        self.assertEqual(self.asked("review"), [])

    def test_a_blocking_flag_earns_exactly_one_revise_round(self):
        # draft, flagged review, revised draft, clean review
        Scripted.script = [answer_for, flagged, answer_for, answer_for]
        code, said = self.run_package()
        self.assertEqual(code, 0, said)
        self.assertEqual((len(self.asked("draft")), len(self.asked("review"))), (2, 2))
        revise = self.asked("draft")[1]["prompt"]
        self.assertIn("the narration never shows a chip printed", revise)
        pkg = self.package()
        self.assertEqual(pkg["verdict"], "ready")
        self.assertEqual(pkg["review"]["rounds"], 1)

    def test_a_flag_still_standing_after_the_round_is_not_ready(self):
        Scripted.script = [answer_for, flagged, answer_for, flagged]
        code, _ = self.run_package()
        self.assertEqual(code, 1)
        self.assertEqual((len(self.asked("draft")), len(self.asked("review"))), (2, 2))
        pkg = self.package()
        self.assertEqual(pkg["verdict"], "not ready")
        self.assertTrue([f for f in pkg["findings"] if f.startswith("error:")
                         and "never shows a chip printed" in f])

    def test_the_reviewer_is_the_reviewer_model_and_the_author_the_model(self):
        self.run_package("--model", "sonnet")
        self.assertEqual({c["model"] for c in self.asked("draft")}, {"sonnet"})
        self.assertEqual({c["model"] for c in self.asked("review")}, {package.REVIEWER_MODEL})
        self.assertEqual(package.REVIEWER_MODEL, "opus")


class Refusal(ProjectCase):
    def test_an_opus_author_with_the_default_reviewer_exits_two_before_any_call(self):
        code, said = self.run_package("--model", "opus")
        self.assertEqual(code, 2)
        self.assertIn("--reviewer-model sonnet", said)
        self.assertEqual(FakeEngine.json_calls, [])
        self.assertFalse((self.root / "package").exists())

    def test_an_explicit_same_family_pair_exits_two(self):
        code, said = self.run_package("--model", "opus", "--reviewer-model", "opus")
        self.assertEqual(code, 2)
        self.assertEqual(FakeEngine.json_calls, [])
        self.assertIn("--reviewer-model", said)

    def test_a_sonnet_author_with_the_default_reviewer_runs(self):
        code, said = self.run_package("--model", "sonnet")
        self.assertEqual(code, 0, said)
        self.assertTrue(FakeEngine.json_calls)


class GatewayUnset(ProjectCase):
    def test_only_cli_engines_are_built_and_their_config_is_todays(self):
        built = []

        class Recording(package.Harness):
            def __post_init__(self_):
                super().__post_init__()
                built.append(self_)
        clean = {k: v for k, v in os.environ.items()
                 if not k.startswith(("YT_GATEWAY", "AI_GATEWAY")) and k != "YT_ENGINE"}
        with mock.patch.dict(os.environ, clean, clear=True), \
                mock.patch.object(package, "Harness", Recording):
            code, said = self.run_package("--model", "sonnet")
            self.assertEqual(code, 0, said)
            self.assertEqual(len(built), 2)
            harness.ENGINE = claude_engine.ClaudeEngine
            for made in built:
                self.assertEqual((made.engine, made.gateway), (None, None))
                plain = harness.Harness(made.root, "package", model=made.model,
                                        role=made.role, verbose=False)
                got = made._engine(tools=())
                self.assertIsInstance(got, claude_engine.ClaudeEngine)
                self.assertEqual(got.config, plain._engine(tools=()).config)
        self.assertEqual(sorted(m.model for m in built), ["opus", "sonnet"])


if __name__ == "__main__":
    unittest.main()
