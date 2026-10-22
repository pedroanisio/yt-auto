"""The tooling serves any film. No subject of one project may leak into the
text that steers every film: the prompts and examples handed to the model,
and the operator manual and docs that describe the stages.

A film's own nouns belong in its project directory (script.json, styles.json,
house.md), never in ytstudio/ or the docs. These words come from projects
that have already leaked once; add to the list when another one does.
"""

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Nouns of real projects that leaked into shared text before.
LEAKED = ("paper plane", "paper-plane", "hero-plane", "desk", "keel", "oak",
          "thin paper", "17-27 units", "airborne", "flight path",
          "três portas", "trocar", "fx-doors",
          "jogador", "prêmio", "premio", "prize")

#: Real names that contain a leaked word and are not about any film.
ALLOWED = ("pve-desk-1",)

#: Verbatim quotations the docs cite that contain a leaked word. A quotation
#: stays as written, so its source is the reason; the exemption is for the
#: docs only, because the same words in a prompt would steer every film.
DOC_ALLOWED = ALLOWED + (
    # the MrBeast production handbook, p. 8, quoted in
    # docs/narrative/structure-and-hooks.md rule 28
    "build hype for the prize or payoff",)

MODULES = sorted((ROOT / "ytstudio").glob("*.py"))
DOCS = [ROOT / "README.md", ROOT / "ytstudio" / "SKILL.md",
        *sorted((ROOT / "docs").glob("**/*.md"))]


def strings_of(path: Path) -> str:
    """Every string literal in a module: prompts, examples, help, docstrings."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return "\n".join(node.value for node in ast.walk(tree)
                     if isinstance(node, ast.Constant) and isinstance(node.value, str))


def comments_of(path: Path) -> str:
    return "\n".join(line.split("#", 1)[1] for line in
                     path.read_text(encoding="utf-8").splitlines() if "#" in line)


def leaks(text: str, allowed: tuple[str, ...] = ALLOWED) -> list[str]:
    for name in allowed:
        text = text.replace(name, "")
    low = text.lower()
    return [w for w in LEAKED if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", low)]


class NoSubjectInSharedText(unittest.TestCase):
    def test_prompts_examples_and_help_name_no_project(self):
        for path in MODULES:
            with self.subTest(module=path.name):
                self.assertEqual(leaks(strings_of(path)), [], path.name)

    def test_code_comments_name_no_project(self):
        for path in MODULES:
            with self.subTest(module=path.name):
                self.assertEqual(leaks(comments_of(path)), [], path.name)

    def test_manual_and_docs_name_no_project(self):
        self.assertIn(ROOT / "docs" / "narrative" / "README.md", DOCS)
        for path in DOCS:
            with self.subTest(doc=path.name):
                self.assertEqual(leaks(path.read_text(encoding="utf-8"), DOC_ALLOWED),
                                 [], path.name)

    def test_the_check_itself_catches_a_leak(self):
        self.assertEqual(leaks("key the flight to clear the desk"), ["desk"])
        self.assertEqual(leaks("host pve-desk-1 renders"), [])
        self.assertEqual(leaks("a desktop machine"), [])
        self.assertEqual(leaks("the prize behind the door"), ["prize"])

    def test_a_cited_quotation_is_allowed_in_the_docs_only(self):
        quote = "build hype for the prize or payoff"
        self.assertEqual(leaks(quote, DOC_ALLOWED), [])
        self.assertEqual(leaks(quote), ["prize"])
