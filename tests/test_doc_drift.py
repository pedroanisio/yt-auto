"""The documents say what the code does: the flags they cite, the stage table's
Model column, the spend record's key and the spellings that were retired.

Each failure names the document, the line and the text, so the fix is one grep
away. The four documents are the ones an operator reads: README.md,
ytstudio/SKILL.md, docs/production.md and docs/compute.md.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ytstudio import cli, spend  # noqa: E402

DOCS = ("README.md", "ytstudio/SKILL.md", "docs/production.md", "docs/compute.md")

#: Flags of tools that are not `yt`, named in the documents.
FOREIGN_FLAGS = {
    "--release",                        # cargo
    "--link-dest", "--update",          # rsync
    "--settings", "--max-budget-usd", "--allowed-tools", "--permission-mode",
    "--dangerously-skip-permissions",   # the claude CLI
    "--cwd", "--timeout", "--version",
    "--bg", "--quote",                  # govern
    "--crf",                            # the engine
    "--tail-check",                     # the gate script
    "--permission-prompts",             # claude_engine.py passes it to the claude CLI
    "--schema", "--write",              # claude_engine.py's own command line
    "--process",                        # govern new
    "--ignore-scripts",                 # npm ci, the gateway engine's install step
    "--package-lock-only",              # npm install, writes gateway/package-lock.json
}

#: A retired yt spelling that is a live flag of another tool: (document, flag).
OTHER_TOOL_SPELLING = {("README.md", "--budget")}  # claude_engine.py --budget

FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9]*(?:-[a-z0-9]+)*")


def read(doc: str) -> list[str]:
    return (ROOT / doc).read_text(encoding="utf-8").splitlines()


def backticked(doc: str):
    """(line number, text) of every inline code span and every line inside a fence."""
    fenced = False
    for n, line in enumerate(read(doc), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif fenced:
            yield n, line
        else:
            for span in re.findall(r"`([^`\n]+)`", line):
                yield n, span


def parsers():
    """Every parser `yt` builds, the stage parsers included."""
    out, todo = [], [cli.build_parser()]
    while todo:
        p = todo.pop()
        out.append(p)
        for action in p._actions:
            if isinstance(getattr(action, "choices", None), dict):
                todo.extend(action.choices.values())
    return out


def known_options() -> set[str]:
    return {o for p in parsers() for a in p._actions for o in a.option_strings}


def retired() -> set[str]:
    """Every spelling a `flags.rounds/bar/budget(..., old=(...))` call retired.
    Read from the module sources: the calls live in the stage modules."""
    out = set()
    for path in (ROOT / "ytstudio").glob("*.py"):
        for group in re.findall(r"\bold=\(([^)]*)\)", path.read_text(encoding="utf-8")):
            out |= set(re.findall(r"""["'](--[a-z][a-z0-9-]*)["']""", group))
    return out


def old_spelling_row(line: str) -> bool:
    """A row of the flag table whose last column lists the retired spellings."""
    cells = line.strip().strip("|").split("|")
    return line.startswith("|") and len(cells) >= 4 and any(
        f"`{s}`" in cells[-1] for s in retired())


class Flags(unittest.TestCase):
    def test_every_flag_a_document_cites_exists_on_a_stage_parser(self):
        known = known_options() | FOREIGN_FLAGS
        cited, wrong = 0, []
        for doc in DOCS:
            for n, text in backticked(doc):
                for flag in FLAG.findall(text):
                    cited += 1
                    if flag not in known:
                        wrong.append(f"{doc}:{n}: `{flag}` is no option of any yt stage "
                                     f"(in: {text.strip()[:80]})")
        self.assertGreater(cited, 50, "almost no flags found; the pattern is broken")
        self.assertEqual(wrong, [], "\n" + "\n".join(wrong))

    def test_no_retired_spelling_is_cited(self):
        old = retired()
        self.assertTrue({"--revisions", "--author-bar", "--budget"} <= old, old)
        wrong = []
        for doc in DOCS:
            lines = read(doc)
            for n, text in backticked(doc):
                if old_spelling_row(lines[n - 1]):
                    continue
                wrong += [f"{doc}:{n}: `{flag}` is a retired spelling (in: {text.strip()[:80]})"
                          for flag in FLAG.findall(text) if flag in old and (doc, flag) not in OTHER_TOOL_SPELLING]
        self.assertEqual(wrong, [], "\n" + "\n".join(wrong))


class StageTable(unittest.TestCase):
    def rows(self) -> dict[str, tuple[int, str]]:
        out = {}
        for n, line in enumerate(read("ytstudio/SKILL.md"), 1):
            m = re.match(r"\| `yt ([a-z]+)` \|", line)
            last = line.rstrip().rstrip("|").rsplit("|", 1)[-1].strip()
            if m and re.match(r"(yes|no)\b", last):
                out.setdefault(m.group(1), (n, last))
        return out

    def test_the_model_column_says_no_only_for_the_stages_that_call_no_model(self):
        rows = self.rows()
        self.assertGreater(len(rows), 8, "the stage table was not found")
        for stage, (n, cell) in rows.items():
            says = cell.split()[0]
            want = "no" if stage in cli.NO_MODEL else "yes"
            self.assertEqual(says, want,
                             f"ytstudio/SKILL.md:{n}: `yt {stage}` Model column says "
                             f"{says!r}, cli.NO_MODEL says {want!r}")
        for stage in cli.NO_MODEL:
            self.assertIn(stage, rows,
                          f"ytstudio/SKILL.md: no stage-table row for `yt {stage}`")


class RecordShape(unittest.TestCase):
    def test_the_record_shape_sentence_names_the_usage_key(self):
        text = "\n".join(read("ytstudio/SKILL.md"))
        m = re.search(r"\*\*One spend record\.\*\*(.*?)\n\n", text, re.S)
        self.assertIsNotNone(m, "ytstudio/SKILL.md: the 'One spend record.' paragraph is gone")
        self.assertIn("`usage`", m.group(1))
        self.assertIn("usage", spend.__doc__)
        for key in spend.KEYS:
            self.assertIn(key, m.group(1), f"ytstudio/SKILL.md: spend key {key} not named")

    def test_no_document_puts_a_usage_stages_cost_under_engine(self):
        # Only the older-record fallback may name engine.cost_usd.
        for doc in DOCS:
            for n, line in enumerate(read(doc), 1):
                if re.search(r"engine\.cost_usd|`engine`[^.]*\b(cost|spend)", line) \
                        and not re.search(r"\bolder\b|before|not the spend", line):
                    self.fail(f"{doc}:{n}: puts the cost under engine: {line.strip()[:100]}")


class ReviewerPool(unittest.TestCase):
    """The reviewer pool's values and variable are documented and real (AUTO-125)."""

    def test_every_reviewer_variable_a_document_names_is_read_in_the_code(self):
        code = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "ytstudio").glob("*.py"))
        named = {(doc, n, v) for doc in DOCS for n, line in enumerate(read(doc), 1)
                 for v in re.findall(r"\bYT_REVIEWER_[A-Z_]+\b", line)}
        self.assertIn("YT_REVIEWER_EXCLUDE_QUANT", {v for _, _, v in named},
                      "no document names $YT_REVIEWER_EXCLUDE_QUANT")
        missing = [f"{doc}:{n}: {v}" for doc, n, v in sorted(named) if f'"{v}"' not in code]
        self.assertEqual(missing, [], "\n" + "\n".join(missing))
        self.assertIn('"YT_REVIEWER_EXCLUDE_QUANT"',
                      (ROOT / "ytstudio" / "reviewers.py").read_text(encoding="utf-8"))
        for doc in ("README.md", "ytstudio/SKILL.md"):
            self.assertIn("YT_REVIEWER_EXCLUDE_QUANT", "\n".join(read(doc)), doc)

    def test_the_reviewer_row_of_the_flag_table_names_the_pool(self):
        row = next((line for line in read("ytstudio/SKILL.md")
                    if line.startswith("| the model that reviews")), "")
        self.assertIn("`--reviewer-model`", row)
        self.assertIn("`pool`", row)
        self.assertIn("`sdk:", row)


if __name__ == "__main__":
    unittest.main()
