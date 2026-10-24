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
    "--max-turns",                      # the claude CLI, from EngineConfig.max_turns
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


#: The documents that may name a gateway variable: the four, and the chat
#: bridge's page, which says the gateway leaves the bridge alone.
GATEWAY_DOCS = DOCS + ("docs/chat-bridge.md",)
GATEWAY_VAR = re.compile(r"\b(?:YT_GATEWAY(?:_[A-Z]+)*|AI_GATEWAY_[A-Z_]*[A-Z]|GATEWAY_PRICES)\b")


def code_text() -> str:
    """Every Python module that runs (the root engines and ytstudio/), and
    the gateway engine's Node program, which reads its own variables."""
    paths = [*ROOT.glob("*.py"), *(ROOT / "ytstudio").glob("*.py"),
             ROOT / "gateway" / "gateway-engine.mjs"]
    return "\n".join(p.read_text(encoding="utf-8") for p in paths)


def readme_section(heading: str) -> str:
    """One `## ` section of README.md, up to the next heading of level 1 or 2
    or the `---` rule, as one line; "" when there is none. A `# comment` in
    a fenced block is not a heading."""
    out, inside, fenced = [], False, False
    for line in read("README.md"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and (re.match(r"#{1,2} ", line) or line == "---"):
            if inside:
                break
            inside = line == f"## {heading}"
            continue
        if inside:
            out.append(line)
    return " ".join(" ".join(out).split())


class Gateway(unittest.TestCase):
    """The gateway is documented as what the code does (AUTO-123): every
    variable a document names is one the code reads, the README alone is
    enough to switch it on, and the CLIs stay the default."""

    SECTION = "Vercel AI Gateway (optional)"

    def test_every_gateway_variable_a_document_names_is_read_in_the_code(self):
        code = code_text()
        named = {(doc, n, v) for doc in GATEWAY_DOCS for n, line in enumerate(read(doc), 1)
                 for v in GATEWAY_VAR.findall(line)}
        self.assertTrue({"YT_GATEWAY", "AI_GATEWAY_API_KEY", "YT_GATEWAY_AUTH",
                         "YT_GATEWAY_URL"} <= {v for _, _, v in named}, named)
        missing = [f"{doc}:{n}: {v} is read nowhere in the code" for doc, n, v in sorted(named)
                   if not re.search(rf"""["']{v}["']|\benv\.{v}\b""", code)]
        self.assertEqual(missing, [], "\n" + "\n".join(missing))

    def test_gateway_ping_is_a_yt_doctor_flag(self):
        doctor = next(p for p in parsers() if p.prog.endswith(" doctor"))
        self.assertIn("--gateway-ping", {o for a in doctor._actions for o in a.option_strings})

    def test_the_readme_alone_switches_the_gateway_on(self):
        import gateway_engine
        from ytstudio import gateway
        text = readme_section(self.SECTION)
        self.assertTrue(text, f"README.md: no '## {self.SECTION}' section")
        for var in (gateway.SWITCH, gateway.KEY, gateway.AUTH, gateway.URL,
                    gateway_engine.NODE_ENV, gateway_engine.PRICES_ENV):
            self.assertIn(f"`{var}", text, f"the section does not name {var}")
        self.assertIn(f"{gateway.SWITCH}={gateway.VERCEL}", text)
        self.assertIn(f"{gateway.AUTH}=subscription", text)
        for url in (gateway.DEFAULT_HOST + gateway.CLAUDE_PATH,
                    gateway.DEFAULT_HOST + gateway.CODEX_PATH,
                    "https://vercel.com/docs/ai-gateway/coding-agents/claude-code",
                    "https://vercel.com/docs/ai-gateway/coding-agents/openai-codex"):
            self.assertIn(url, text)
        for words in ("yt doctor --gateway-ping", "`npx vercel ai-gateway setup`",
                      "~/.claude/settings.json", "~/.codex/config.toml",
                      "list price", "`cost_cli_calls`", "402", "429", "`vmc/<slug>`",
                      "BYOK", "zero data retention", "fallback", "not evidenced"):
            self.assertIn(words, text, f"the section does not say {words}")

    def test_the_readme_says_the_clis_stay_the_default(self):
        text = readme_section(self.SECTION)
        self.assertIn("The claude and codex CLIs are the default", text)
        self.assertIn("never replaces a CLI call", text)
        self.assertIn("No dependencies.", "\n".join(read("README.md")))

    UNCHECKED = r"Not yet checked against the live gateway:(.*?)\.(?: |$)"

    def test_the_readme_does_not_state_the_unchecked_as_fact(self):
        text = readme_section(self.SECTION)
        m = re.search(self.UNCHECKED, text)
        self.assertIsNotNone(m, "the section does not list what is unchecked")
        for words in ("structured output", "bare", "1-hour", "model ids", "credits",
                      "`npx vercel ai-gateway setup`"):
            self.assertIn(words, m.group(1))
        # Outside that list, neither the section nor SKILL.md's paragraph
        # states an unchecked item as a fact.
        for where, rest in (("README.md", text[:m.start()] + text[m.end():]),
                            ("ytstudio/SKILL.md", self.skill_paragraph())):
            low = rest.lower()
            for words in ("1-hour", "generation id", "generation-id", "--json-schema",
                          "schema flag"):
                self.assertNotIn(words, low, f"{where} states '{words}' outside the list")
            for sentence in re.split(r"(?<=\.) ", low):
                if "bare" in sentence:
                    self.assertNotRegex(sentence, r"\bworks\b|signs? in",
                                        f"{where}: {sentence}")

    def test_the_readme_cites_vercel_for_its_pricing(self):
        text = readme_section(self.SECTION)
        self.assertIn("https://vercel.com/docs/ai-gateway/pricing", text)

    def test_the_readme_says_any_other_failure_is_transient(self):
        text = readme_section(self.SECTION)
        self.assertIn("Any other failure text", text)
        self.assertIn("treated as transient", text)
        self.assertNotIn("unless it matches one of those", text)

    def test_the_readme_section_names_no_ticket(self):
        """A pointer to a pass on another branch names what holds either way,
        not a ticket a reader cannot follow."""
        self.assertNotRegex(readme_section(self.SECTION), r"AUTO-\d+")

    def test_the_reviewer_differs_by_family_or_maker_in_both(self):
        for where, text in (("README.md", readme_section(self.SECTION)),
                            ("ytstudio/SKILL.md", self.skill_paragraph())):
            self.assertIn("by maker", text, where)
            self.assertIn("`sdk:<id>`", text, where)

    def test_both_say_how_a_call_is_booked(self):
        """The booking order is the code's (gateway_engine.COST_SOURCES and
        claude_engine's cli fallback): the gateway's reported cost, else a
        price row, else the CLI's own figure."""
        para = self.skill_paragraph()
        self.assertNotIn("at list price from the price table", para)
        for words in ("the cost the gateway reports", "price row", "the CLI's own figure"):
            self.assertIn(words, para)

    def skill_paragraph(self) -> str:
        text = "\n".join(read("ytstudio/SKILL.md"))
        inv = re.search(r"^## The invariants\n(.*?)^## ", text, re.S | re.M).group(1)
        para = next((p for p in inv.split("\n\n") if "hold under Vercel AI Gateway" in p), "")
        self.assertTrue(para, "ytstudio/SKILL.md: The invariants says nothing of the gateway")
        return " ".join(para.split())

    def test_skill_says_the_invariants_hold_under_the_gateway(self):
        para = self.skill_paragraph()
        for words in ("CLIs stay the default", "`YT_GATEWAY=vercel`", "not evidenced",
                      "`Harness.decide`", "family", "`vmc/<slug>`", "dependency-free"):
            self.assertIn(words, para)

    def test_both_engines_say_how_they_reach_the_gateway(self):
        import ast
        for name in ("claude_engine.py", "codex_engine.py"):
            doc = ast.get_docstring(ast.parse((ROOT / name).read_text(encoding="utf-8")))
            requires = " ".join(next(p for p in doc.split("\n\n")
                                     if p.lstrip().startswith("Requires")).split())
            self.assertIn("YT_GATEWAY=vercel", requires, name)
            self.assertIn("AI_GATEWAY_API_KEY", requires, name)

    def test_the_bridge_page_says_the_gateway_leaves_it_alone(self):
        text = " ".join("\n".join(read("docs/chat-bridge.md")).split())
        for words in ("`YT_GATEWAY`", "the bridge is unaffected"):
            self.assertTrue(words in text, f"docs/chat-bridge.md does not say {words}")


if __name__ == "__main__":
    unittest.main()
