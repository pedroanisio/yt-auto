"""ytstudio/SKILL.md is the operator's manual and the source of truth for how
the pipeline is run; .claude/skills/yt/SKILL.md is a link to it. These tests
fail when the link breaks, when the manual stops naming a stage or a command
the CLI has, or when its frontmatter would stop it loading as a skill.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ytstudio import cli, workspace  # noqa: E402

SOURCE = ROOT / "ytstudio" / "SKILL.md"
LINK = ROOT / ".claude" / "skills" / "yt" / "SKILL.md"


def frontmatter(text: str) -> dict[str, str]:
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out


def subparsers() -> dict:
    parser = cli.build_parser()
    for action in parser._actions:
        if getattr(action, "choices", None) and action.dest == "stage":
            return dict(action.choices)
    raise AssertionError("the yt parser has no <stage> subcommands")


def subcommands() -> list[str]:
    return list(subparsers())


#: A code span that runs one command: `yt render --quality final --strict`.
COMMAND_SPAN = re.compile(r"`yt ([a-z]+)((?: [^`]*)?)`")


class Skill(unittest.TestCase):
    def setUp(self):
        self.text = SOURCE.read_text(encoding="utf-8")

    def test_the_source_is_in_the_package_and_the_skill_links_to_it(self):
        self.assertTrue(SOURCE.is_file(), SOURCE)
        self.assertTrue(LINK.is_symlink(), f"{LINK} should be a link to {SOURCE}")
        self.assertEqual(LINK.resolve(), SOURCE.resolve())
        self.assertEqual(LINK.read_text(encoding="utf-8"), self.text)

    def test_frontmatter_names_and_describes_the_skill(self):
        fm = frontmatter(self.text)
        self.assertEqual(fm.get("name"), "yt")
        self.assertGreater(len(fm.get("description", "")), 80)
        self.assertIn("yt", fm["description"])

    def test_every_pipeline_stage_is_named(self):
        for stage, artifact in workspace.STAGES:
            self.assertIn(f"`yt {stage}`", self.text, f"stage {stage!r} is not in the manual")
            self.assertIn(f"`{workspace.ARTIFACTS[artifact]}`", self.text,
                          f"{stage}'s artifact {workspace.ARTIFACTS[artifact]!r} is not in the manual")

    def test_every_cli_command_is_named(self):
        for command in subcommands():
            self.assertIn(f"`yt {command}`", self.text, f"command {command!r} is not in the manual")

    def test_every_flag_the_manual_cites_exists_on_that_command(self):
        commands = subparsers()
        cited = 0
        for command, rest in COMMAND_SPAN.findall(self.text):
            self.assertIn(command, commands, f"`yt {command}` is not a yt command")
            known = {o for a in commands[command]._actions for o in a.option_strings}
            for flag in re.findall(r"--[a-z][a-z-]*", rest):
                cited += 1
                self.assertIn(flag, known, f"`yt {command}` has no {flag}")
        self.assertGreater(cited, 5, "the manual cites almost no flags; the pattern is broken")

    def test_the_retime_and_script_rows_name_the_revise_command(self):
        # retime's `rewrite` and the script stage's row both name the command
        # that sends the lines back (AUTO-95); the flag test above checks it
        # exists on `yt script`.
        rows: dict[str, str] = {}
        for line in self.text.splitlines():
            cells = line.split("|")
            if len(cells) > 2 and cells[1].strip().startswith("`yt "):
                stage = cells[1].strip("` ").split()[1]
                rows[stage] = rows.get(stage, "") + line
        for stage in ("retime", "script"):
            self.assertIn("`yt script --revise retime`", rows[stage], stage)

    def test_the_manual_says_where_its_truth_lives(self):
        self.assertIn("ytstudio/SKILL.md", self.text)
        self.assertIn(".claude/skills/yt/SKILL.md", self.text)
        self.assertIn("tests/test_skill.py", self.text)

    def test_the_manual_points_at_the_narrative_corpus(self):
        more = self.text[self.text.index("## Where to read more"):]
        self.assertIn("`docs/narrative/`", more)

    def test_the_manual_names_the_plan_review_where_it_runs(self):
        # AUTO-92: the concept table names script for the flags it took, the
        # stage table names the decision and its cost, and the `yt run`
        # paragraph says --quick drops it.
        table = self.text.split("One word per concept", 1)[1].split("\n## ", 1)[0]
        rows = [line for line in table.splitlines() if line.startswith("| ")]
        for flag in ("`--rounds`", "`--reviewer-model`", "`--review-budget-usd`"):
            row = next(r for r in rows if flag in r.split("|")[2])
            self.assertIn("`script`", row.split("|")[3], flag)
        script_row = next(line for line in self.text.splitlines()
                          if line.startswith("| `yt script` |"))
        for needle in ("`--review`", "`plan-review`", "`plan-2`", "$"):
            self.assertIn(needle, script_row, needle)
        walk = self.text[self.text.index("`yt run` (`yt run --title X`) walks"):]
        walk = walk[:walk.index("\n\n")]
        self.assertIn("`--quick`", walk)
        self.assertIn("plan review", walk)

    def test_the_beside_the_chain_table_names_retention(self):
        # AUTO-101: `yt retention` sits beside the chain, writes retention.json,
        # and the improvement loop names the source it adds
        beside = self.text[self.text.index("Beside the chain:"):]
        beside = beside[:beside.index("\n\n", beside.index("| Tool |"))]
        row = next(line for line in beside.splitlines()
                   if line.startswith("| `yt retention` |"))
        for needle in ("`retention.json`", "`work/<slug>/retention/*.json`", "`--export`",
                       "sanity_bound", "No model"):
            self.assertIn(needle, row, needle)
        loop = self.text[self.text.index("## The improvement loop"):]
        loop = loop[:loop.index("\n## ")]
        self.assertIn("`retention`", loop)

    def test_the_manual_names_the_story_review_where_it_runs(self):
        # AUTO-93: the script row names each decision of the story review,
        # what it costs and when --gate is to be turned on; a paragraph
        # beside the fact-check's says what binds voice; the improvement
        # loop says what a reject does to a pass.
        script_row = next(line for line in self.text.splitlines()
                          if line.startswith("| `yt script` |"))
        for needle in ("`review-r1-s1`", "-again", "`cold-read`", "`cold-read-match`",
                       "`revise-story-1`", "`redraft-02`", "`--gate`", "review-log.jsonl",
                       "20 labelled", "`## Story review`", "not_evidenced"):
            self.assertIn(needle, script_row, needle)
        self.assertNotIn("not yet wired", script_row)
        binds = self.text[self.text.index("**The story review binds voice"):]
        binds = binds[:binds.index("\n\n")]
        for needle in ("`--gate`", "`error:`", "narration.txt", "review-log.jsonl"):
            self.assertIn(needle, binds, needle)
        loop = self.text[self.text.index("## The improvement loop"):]
        loop = loop[:loop.index("\n## ")]
        self.assertIn("story review", loop)
        self.assertIn("`story-review`", loop)

    def test_the_manual_keeps_the_delivery_order_and_the_bar(self):
        for needle in ("--quality final --strict", "READY TO PUBLISH", "9.0", "8.5",
                       "docs/production.md", "docs/compute.md", "RS_RENDER_HOSTS"):
            self.assertIn(needle, self.text, needle)


if __name__ == "__main__":
    unittest.main()
