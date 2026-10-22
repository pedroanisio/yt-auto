"""One vocabulary for the stage flags: each concept (rounds, bar, budget, fresh, jobs) has one
spelling across stages, the old spellings still work as hidden aliases that say so, and
every stage that calls a model accepts --model, --effort and --fallback-model."""

from __future__ import annotations

import contextlib
import io
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ytstudio import cli, improve, workspace  # noqa: E402

SKILL = ROOT / "ytstudio" / "SKILL.md"

#: concept -> (canonical flag, old spelling -> stage that used it, stages that take it)
CONCEPTS = {
    "rounds": ("--rounds", {"--revisions": "vfx"}, ("keyframe", "vfx", "probe", "script")),
    "reviewer": ("--reviewer-model", {}, ("keyframe", "vfx", "probe", "run", "script")),
    "review budget": ("--review-budget-usd", {}, ("keyframe", "vfx", "probe", "run", "script")),
    "bar": ("--bar", {"--author-bar": "vfx"}, ("keyframe", "vfx", "probe")),
    "budget": ("--budget-usd", {"--budget": "research"},
               ("keyframe", "vfx", "probe", "research", "assess", "improve", "run")),
    "fresh": ("--fresh", {}, ("research", "keyframe", "vfx", "props")),
    "jobs": ("--jobs", {}, ("ideas", "research", "styles", "script", "keyframe", "vfx",
                            "probe", "sound")),
    # the shots sent back to the screenplay for one revision (AUTO-95)
    "revise": ("--revise", {}, ("script",)),
}
#: Stages that author with or ask a model; render and the local fetchers do not.
MODEL_STAGES = ("ideas", "research", "styles", "script", "keyframe", "voice", "vfx", "sound",
                "build", "retime", "pilot", "gate", "review", "qa", "improve", "assess",
                "props", "probe")


def stages() -> dict:
    parser = cli.build_parser()
    for action in parser._actions:
        if getattr(action, "choices", None) and action.dest == "stage":
            return dict(action.choices)
    raise AssertionError("no subcommands")


def options(p) -> dict:
    return {o: a for a in p._actions for o in a.option_strings}


class OneSpelling(unittest.TestCase):
    def test_each_concept_has_its_canonical_flag_on_every_stage_that_has_it(self):
        sub = stages()
        for concept, (flag, _old, where) in CONCEPTS.items():
            for stage in where:
                self.assertIn(flag, options(sub[stage]), f"{concept}: yt {stage} lacks {flag}")

    def test_old_spellings_still_parse_to_the_same_place_and_are_hidden(self):
        sub = stages()
        for concept, (flag, old, _where) in CONCEPTS.items():
            for spelling, stage in old.items():
                acts = options(sub[stage])
                self.assertIn(spelling, acts, f"{spelling} must keep working")
                self.assertEqual(acts[spelling].dest, acts[flag].dest,
                                 f"{spelling} must land where {flag} does")
                self.assertIsNone(re.search(re.escape(spelling) + r"(?![\w-])",
                                            sub[stage].format_help()), f"{spelling} is hidden")

    def test_an_old_spelling_works_and_prints_one_deprecation_line(self):
        parser = cli.build_parser()
        for stage, old, flag, value in (("vfx", "--revisions", "--rounds", 4),
                                        ("vfx", "--author-bar", "--bar", 6.5),
                                        ("research", "--budget", "--budget-usd", 3.0)):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                ns = parser.parse_args([stage, old, str(value)])
            dest = options(stages()[stage])[flag].dest
            self.assertEqual(getattr(ns, dest), value)
            lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
            self.assertEqual(len(lines), 1, err.getvalue())
            self.assertIn(old, lines[0])
            self.assertIn(flag, lines[0])

    def test_the_new_spelling_prints_nothing(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ns = cli.build_parser().parse_args(["vfx", "--rounds", "3", "--bar", "6"])
        self.assertEqual(err.getvalue(), "")
        self.assertEqual((ns.revisions, ns.author_bar), (3, 6.0))

    def test_defaults_are_unchanged(self):
        ns = cli.build_parser().parse_args(["vfx"])
        self.assertEqual((ns.revisions, ns.author_bar), (2, 7.0))
        self.assertIsNone(cli.build_parser().parse_args(["research"]).budget)


class ModelStages(unittest.TestCase):
    def test_every_model_stage_accepts_model_effort_and_fallback(self):
        sub = stages()
        for stage in MODEL_STAGES:
            opts = options(sub[stage])
            for flag in ("--model", "--effort", "--fallback-model"):
                self.assertIn(flag, opts, f"yt {stage} lacks {flag}")

    def test_render_calls_no_model_and_hides_the_model_flags(self):
        render = stages()["render"]
        self.assertNotIn("--model", render.format_help())
        self.assertNotIn("--fallback-model", render.format_help())

    def test_render_still_accepts_the_model_flags_as_noops_with_one_note(self):
        parser = cli.build_parser()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ns = parser.parse_args(["render", "--project", "X", "--model", "sonnet",
                                    "--quality", "final"])
        self.assertEqual((ns.project, ns.quality), ("X", "final"))
        lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
        self.assertEqual(lines, ["note: render calls no model; --model is ignored"])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            parser.parse_args(["render", "--effort", "high", "--fallback-model", "opus,haiku"])
        self.assertEqual(err.getvalue().splitlines(),
                         ["note: render calls no model; --effort is ignored",
                          "note: render calls no model; --fallback-model is ignored"])

    def test_render_without_the_model_flags_prints_nothing(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            cli.build_parser().parse_args(["render", "--quality", "draft"])
        self.assertEqual(err.getvalue(), "")

    def test_yt_run_hands_props_the_model_flags_and_render_none(self):
        base = ["--project", "work/x", "--model", "opus", "--effort", "high", "--quiet"]
        self.assertEqual(cli.stage_argv("props", base, {}), ["props", *base])
        self.assertNotIn("--model", cli.stage_argv("render", base, {}))
        parser = cli.build_parser()
        for stage in cli.PIPELINE:
            parser.parse_args(cli.stage_argv(stage, base, {}))


class Registries(unittest.TestCase):
    def test_the_docstring_lists_the_stages_in_pipeline_order(self):
        doc = cli.__doc__
        head = doc.split("    yt ")[0]
        listed = re.findall(r"^    yt (\w+)  +\S", doc, re.M)
        order = [s for s, _ in workspace.STAGES]
        self.assertEqual([s for s in listed if s in order], order)
        words = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
                 "nine", "ten", "eleven", "twelve"]
        self.assertIn(words[len(order)], head.lower())

    def test_routable_is_the_pipeline(self):
        self.assertEqual(improve.ROUTABLE, improve.PIPELINE)

    def test_artifacts_name_each_key_once(self):
        src = (ROOT / "ytstudio" / "workspace.py").read_text(encoding="utf-8")
        block = src.split("ARTIFACTS = ", 1)[1].split("\n}", 1)[0]
        keys = re.findall(r'^\s+"(\w+)":', block, re.M)
        self.assertEqual(len(keys), len(set(keys)), "a key is listed twice")


class Manual(unittest.TestCase):
    def test_the_concepts_table_names_each_flag_and_its_old_spelling(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("One word per concept", text)
        table = text.split("One word per concept", 1)[1].split("\n## ", 1)[0]
        for concept, (flag, old, _where) in CONCEPTS.items():
            self.assertIn(f"`{flag}`", table, concept)
            for spelling in old:
                self.assertIn(f"`{spelling}`", table, spelling)
        for flag in ("--model", "--effort", "--fallback-model"):
            self.assertIn(f"`{flag}`", table)


if __name__ == "__main__":
    unittest.main()
