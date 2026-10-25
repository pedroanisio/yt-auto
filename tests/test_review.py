"""review.py: the review sheet's reader, and what its credit issues do (AUTO-160).

The sheet is written by sr-review and read by a model of another family than
the author. A credit the sheet shows nothing for is the one issue that acts:
it is typed (`kind: credit`), names the asset, and makes `yt review` exit 1;
review.json records the scene it read, so QA and doctor read the credits only
while scene.xml is that scene. sr-review and the reader are fakes here: no
model, no engine, no PDF library.
"""

import contextlib
import hashlib
import io
import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from ytstudio import cli, harness, qa, review, vfx
from ytstudio.harness import HarnessError

SECTION = "The Pressure Builds"
CREDITS = [{"asset": "img-lab", "credit": "Photo: J. Doe / CC BY 4.0",
            "licence": "CC-BY-4.0"}]


def issue(kind="credit", asset="fig-pressure", severity="blocking",
          what="a chart redrawn from Smith 2019 with no source on screen",
          fix="add 'after Smith 2019' under the chart"):
    row = {"what": what, "severity": severity, "owner": "vfx", "fix": fix, "kind": kind}
    if asset is not None:
        row["asset"] = asset
    return row


def critique(*issues, verdict="send back", heading=SECTION):
    return {"sections": [{"heading": heading, "reads": "a pressure chart over a lab photo",
                          "issues": list(issues)}],
            "overall": "one figure has no source on screen", "verdict": verdict}


class Case(unittest.TestCase):
    """A project with a scene, a build.json by a sonnet author, and fakes for
    sr-review and the reader."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="review-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "work" / "pressure"
        self.root.mkdir(parents=True)
        self.scene = self.root / "scene.xml"
        self.scene.write_text("<scene version='1.3'><markers/></scene>")
        self.write_build({"engine": {"model": "sonnet"}, "credits": CREDITS,
                          "credit_errors": [], "publishable": True,
                          "shots": [{"label": "2.1", "at": 12.5, "section": SECTION}]})
        (self.root / "script.json").write_text(json.dumps({"sections": [
            {"heading": SECTION, "purpose": "the rise",
             "shots": [{"label": "2.1", "visual": "a chart", "on_screen": ""}]}]}))
        # sr-review: writes a PDF where it is told to and says 0
        self.rendered = []

        def main(argv):
            self.rendered.append(list(argv))
            Path(argv[argv.index("-o") + 1]).write_bytes(b"%PDF-1.4 fake")
            return 0
        fake = types.ModuleType("sr_core.review.cli")
        fake.main = main
        modules = mock.patch.dict(sys.modules, {
            "sr_core": types.ModuleType("sr_core"),
            "sr_core.review": types.ModuleType("sr_core.review"),
            "sr_core.review.cli": fake})
        modules.start()
        self.addCleanup(modules.stop)
        sdk = mock.patch.object(review, "sdk", lambda: None)
        sdk.start()
        self.addCleanup(sdk.stop)
        # the reader: canned answers, one per inspection, the last repeating
        self.answers = [critique(issue())]
        self.asked = []
        case = self

        def inspect(self_, name, prompt, schema, *, cwd, **kw):
            case.asked.append({"name": name, "prompt": prompt, "model": self_.model,
                               "engine": self_.engine, "schema": schema})
            answer = case.answers.pop(0) if len(case.answers) > 1 else case.answers[0]
            if isinstance(answer, Exception):
                raise answer
            return answer
        patch = mock.patch.object(harness.Harness, "inspect", inspect)
        patch.start()
        self.addCleanup(patch.stop)

    def write_build(self, build):
        (self.root / "build.json").write_text(json.dumps(build))

    def args(self, *extra):
        return cli.build_parser().parse_args(
            ["review", "--project", str(self.root), "--quiet", "--no-house", *extra])

    def run_review(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = review.run(self.args(*extra))
        self.out, self.err = out.getvalue(), err.getvalue()
        return code

    def review_json(self):
        return json.loads((self.root / "review.json").read_text())


class CreditExit(Case):
    def test_a_credit_issue_returns_1_and_is_written(self):
        self.assertEqual(self.run_review(), 1)
        written = self.review_json()
        self.assertEqual(written["credit_issues"], [
            {"heading": SECTION, "what": "a chart redrawn from Smith 2019 with no source "
             "on screen", "asset": "fig-pressure", "severity": "blocking",
             "fix": "add 'after Smith 2019' under the chart"}])
        self.assertEqual(written["critique"]["verdict"], "send back")

    def test_band_issues_only_return_0(self):
        self.answers = [critique(issue("band", asset=None, what="a super sits in the stage"),
                                 issue("legibility", asset=None), verdict="send back")]
        self.assertEqual(self.run_review(), 0)
        self.assertEqual(self.review_json()["credit_issues"], [])

    def test_a_minor_credit_under_approve_still_returns_1(self):
        self.answers = [critique(issue(severity="minor"), verdict="approve")]
        self.assertEqual(self.run_review(), 1)
        self.assertIn("note: the reader approved the sheet but filed 1 credit issue(s); "
                      "they block", self.err)
        self.assertEqual(len(self.review_json()["credit_issues"]), 1)

    def test_review_json_records_the_scene_digest(self):
        self.run_review()
        self.assertEqual(self.review_json()["scene_sha256"],
                         qa.scene_digest(self.scene.read_bytes()))
        self.assertEqual(self.review_json()["scene_sha256"],
                         hashlib.sha256(self.scene.read_bytes()).hexdigest())

    def test_kind_is_required(self):
        row = issue()
        del row["kind"]
        self.assertIn("sections[0].issues[0].kind",
                      harness.missing_fields(critique(row), review.REVIEW_SCHEMA))
        self.assertTrue(harness.enum_violations(critique(issue("licence")),
                                                review.REVIEW_SCHEMA))
        self.assertNotIn("asset", review.REVIEW_SCHEMA["properties"]["sections"]["items"]
                         ["properties"]["issues"]["items"]["required"])
        # what Harness.inspect raises once form, fenced JSON and extractor fail
        self.answers = [HarnessError("[review] the model's sheet answer lacks "
                                     "sections[0].issues[0].kind")]
        self.assertEqual(self.run_review(), 1)
        self.assertFalse((self.root / "review.json").exists())

    def test_a_credit_with_no_asset_is_asked_once_more_then_fails(self):
        self.answers = [critique(issue(asset="  ")), critique(issue(asset=""))]
        self.assertEqual(self.run_review(), 1)
        self.assertEqual([a["name"] for a in self.asked], ["sheet", "sheet-retry"])
        self.assertIn(f"Your form filed credit issues in {SECTION} with no `asset`",
                      self.asked[1]["prompt"])
        self.assertTrue(self.asked[1]["prompt"].startswith(self.asked[0]["prompt"]))
        self.assertIn("error: [review] sheet: a credit issue names no asset", self.err)
        self.assertFalse((self.root / "review.json").exists())

    def test_a_retry_that_names_the_asset_is_written(self):
        self.answers = [critique(issue(asset=None)), critique(issue(asset="fig-pressure"))]
        self.assertEqual(self.run_review(), 1)
        self.assertEqual([a["name"] for a in self.asked], ["sheet", "sheet-retry"])
        self.assertEqual(self.review_json()["credit_issues"][0]["asset"], "fig-pressure")

    def test_a_named_credit_is_not_asked_again(self):
        self.run_review()
        self.assertEqual([a["name"] for a in self.asked], ["sheet"])


class Reviewer(Case):
    def test_same_family_reviewer_is_refused_before_anything_runs(self):
        self.write_build({"engine": {"model": "opus"}, "shots": []})
        self.assertEqual(self.run_review("--reviewer-model", "opus"), 2)
        self.assertIn("a model does not grade its own work", self.err)
        self.assertEqual((self.rendered, self.asked), ([], []))
        self.assertFalse((self.root / "review.pdf").exists())

    def test_the_author_is_build_jsons_engine_model_not_args_model(self):
        # build was written by opus; --model sonnet does not make opus a reviewer
        self.write_build({"engine": {"model": "claude-opus-5-5"}, "shots": []})
        self.assertEqual(self.run_review("--model", "sonnet"), 2)
        self.assertEqual((self.rendered, self.asked), ([], []))

    def test_author_falls_back_to_args_model(self):
        (self.root / "build.json").unlink()
        self.assertEqual(self.run_review("--model", "sonnet", "--reviewer-model", "sonnet"), 2)
        self.assertEqual((self.rendered, self.asked), ([], []))
        # and a build.json with no engine falls back the same way
        self.write_build({"shots": []})
        self.assertEqual(self.run_review("--model", "opus"), 2)
        self.assertEqual((self.rendered, self.asked), ([], []))

    def test_pool_reviewer_is_refused(self):
        for value in ("pool", "sdk:zai/glm-5.3"):
            self.assertEqual(self.run_review("--reviewer-model", value), 2)
            self.assertIn(review.reviewers.agent_refusal("review", value), self.err)
            self.assertEqual((self.rendered, self.asked), ([], []))

    def test_the_reader_is_the_reviewer_model(self):
        self.assertEqual(review.REVIEWER_MODEL, vfx.REVIEWER_MODEL)
        self.assertEqual(self.args().reviewer_model, vfx.REVIEWER_MODEL)
        self.run_review()
        self.assertEqual(self.asked[0]["model"], vfx.REVIEWER_MODEL)
        # a claude CLI reader, never the gateway engine
        self.assertIsNone(self.asked[0]["engine"])
        self.assertEqual(self.review_json()["engine"]["model"], vfx.REVIEWER_MODEL)
        self.asked.clear()
        self.run_review("--reviewer-model", "gpt-5.1-codex")
        self.assertEqual(self.asked[0]["model"], "gpt-5.1-codex")

    def test_the_brief_carries_build_credits(self):
        self.write_build({"engine": {"model": "sonnet"}, "credits": CREDITS,
                          "credit_errors": ["img-x: CC-BY-4.0 with no credit"], "shots": []})
        self.run_review()
        prompt = self.asked[0]["prompt"]
        self.assertIn("Photo: J. Doe / CC BY 4.0", prompt)
        self.assertIn("img-x: CC-BY-4.0 with no credit", prompt)

    def test_a_build_from_before_the_credits_passes_nothing_for_them(self):
        self.write_build({"engine": {"model": "sonnet"}, "shots": []})
        self.run_review()
        prompt = self.asked[0]["prompt"]
        self.assertNotIn('"credits"', prompt)
        self.assertNotIn('"credit_errors"', prompt)

    def test_the_role_defines_a_credit_issue(self):
        for needle in ("`credit`", "after X", "logo", "screenshot", "TBD",
                       "outlet, date", "credits"):
            self.assertIn(needle, review.ROLE)


class Pure(unittest.TestCase):
    def test_credit_issues_and_unnamed_credits(self):
        c = {"sections": [
            {"heading": "A", "reads": "", "issues": [issue(), issue("band", asset=None)]},
            {"heading": "B", "reads": "", "issues": [issue(asset=" ")]},
            {"heading": "C", "reads": "", "issues": [issue(asset=None)]}],
            "overall": "", "verdict": "send back"}
        found = review.credit_issues(c)
        self.assertEqual([f["heading"] for f in found], ["A", "B", "C"])
        self.assertEqual(set(found[0]), {"heading", "what", "asset", "severity", "fix"})
        self.assertEqual(found[2]["asset"], "")
        self.assertEqual(review.unnamed_credits(c), ["B", "C"])
        self.assertEqual(review.credit_issues({"sections": []}), [])


class CurrentCredits(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="review-current-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.scene = self.tmp / "scene.xml"
        self.scene.write_text("<scene/>")
        self.issues = [{"heading": SECTION, "what": "w", "asset": "fig", "severity": "minor",
                        "fix": "f"}]

    def write(self, **record):
        (self.tmp / "review.json").write_text(json.dumps(record))

    def test_current_credits(self):
        self.assertEqual(review.current_credits(self.tmp, self.scene), ([], ""))
        self.write(credit_issues=self.issues)          # from before this card
        self.assertEqual(review.current_credits(self.tmp, self.scene),
                         ([], review.STALE_REVIEW))
        self.assertTrue(review.STALE_REVIEW.startswith("not_evidenced: "))
        self.write(credit_issues=self.issues, scene_sha256="0" * 64)
        self.assertEqual(review.current_credits(self.tmp, self.scene),
                         ([], review.STALE_REVIEW))
        self.write(credit_issues=self.issues,
                   scene_sha256=qa.scene_digest(self.scene.read_bytes()))
        self.assertEqual(review.current_credits(self.tmp, self.scene), (self.issues, ""))
        self.assertEqual(review.current_credits(self.tmp, str(self.scene)), (self.issues, ""))
        self.assertEqual(review.current_credits(self.tmp, None), ([], ""))
        # the scene rebuilt: the old review no longer speaks for it
        self.scene.write_text("<scene version='2'/>")
        self.assertEqual(review.current_credits(self.tmp, self.scene),
                         ([], review.STALE_REVIEW))


if __name__ == "__main__":
    unittest.main()
