"""igstudio/SKILL.md is the operator's manual for `ig` and the source of
truth for how it is run; .claude/skills/ig/SKILL.md is a link to it. These
tests fail when the link breaks, when the manual stops naming a command the
CLI has, when a flag it cites does not exist, or when its frontmatter would
stop it loading as a skill.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from igstudio import cli  # noqa: E402

SOURCE = ROOT / "igstudio" / "SKILL.md"
LINK = ROOT / ".claude" / "skills" / "ig" / "SKILL.md"
COMMAND_SPAN = re.compile(r"`ig ([a-z-]+)((?: [^`]*)?)`")


def frontmatter(text: str) -> dict[str, str]:
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    out = {}
    for line in (m.group(1).splitlines() if m else []):
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out


def subparsers() -> dict:
    parser = cli.build_parser()
    for action in parser._actions:
        if getattr(action, "choices", None) and action.dest == "command":
            return dict(action.choices)
    raise AssertionError("the ig parser has no <command> subcommands")


def flags_of(parser) -> set[str]:
    out = set()
    for action in parser._actions:
        out.update(o for o in action.option_strings)
        if getattr(action, "choices", None) and action.dest == "sub":
            for sub in action.choices.values():
                out.update(flags_of(sub))
    return out


class Skill(unittest.TestCase):
    def setUp(self):
        self.text = SOURCE.read_text(encoding="utf-8")

    def test_the_source_is_in_the_package_and_the_skill_links_to_it(self):
        self.assertTrue(SOURCE.is_file(), SOURCE)
        self.assertTrue(LINK.is_symlink(), f"{LINK} should be a link to {SOURCE}")
        self.assertEqual(LINK.resolve(), SOURCE.resolve())

    def test_frontmatter_names_and_describes_the_skill(self):
        fm = frontmatter(self.text)
        self.assertEqual(fm.get("name"), "ig")
        self.assertGreater(len(fm.get("description", "")), 80)
        self.assertIn("`ig`", fm["description"])

    def test_every_cli_command_is_named(self):
        for command in subparsers():
            self.assertIn(f"`ig {command}", self.text, f"command {command!r} is not in the manual")

    def test_every_flag_the_manual_cites_exists_on_that_command(self):
        commands = subparsers()
        cited = 0
        for command, rest in COMMAND_SPAN.findall(self.text):
            self.assertIn(command, commands, f"`ig {command}` is not an ig command")
            known = flags_of(commands[command]) | flags_of(cli.build_parser())
            for flag in re.findall(r"(--[a-z-]+)", rest):
                self.assertIn(flag, known, f"`ig {command}` has no flag {flag}")
                cited += 1
        self.assertGreater(cited, 5)

    def test_the_guide_and_the_manual_name_the_same_home(self):
        guide = (ROOT / "docs" / "instagram.md").read_text(encoding="utf-8")
        for name in ("accounts.json", "queue.json", "house.md", "record/"):
            self.assertIn(name, self.text, name)
            self.assertIn(name, guide, name)


if __name__ == "__main__":
    unittest.main()
