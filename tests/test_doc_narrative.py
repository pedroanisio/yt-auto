"""docs/narrative/ is the craft corpus every story check cites, so its shape is
pinned here: every rule carries a `Check:` a reviewer or a lint can run, a
`Stages:` tag naming what it bears on, and at least one URL; every file says
what is contested, what is folklore and where its sources are; and the README
states each rule's strength once, so the stages that enforce a rule read how
hard to enforce it from one place instead of deciding it again.

`rules_of()` is the parser, exported for the tests that cross-check their own
rule ids against the corpus (the house defaults, the narrative rubric, the
retention findings).
"""

from __future__ import annotations

import re
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "docs" / "narrative"
README = CORPUS / "README.md"
FILES = ("structure-and-hooks.md", "attention-and-stakes.md",
         "direction-and-narration.md", "judging-narrative.md")

#: The `yt` stages a rule may be tagged with. A tag may also name a schema
#: field the rule asks for, as `plan.<field>` or `shot.<field>`.
STAGES = ("ideas", "research", "styles", "script", "voice", "retime",
          "keyframe", "vfx", "sound", "build", "qa", "improve")
FIELD = re.compile(r"^(plan|shot)\.[a-z_]+(\.[a-z_]+)*$")

#: The four strengths, strongest first: what the README's table may say.
STRENGTHS = ("peer-reviewed", "platform", "practitioner", "house")

#: The sections every rule file ends with.
SECTIONS = ("## Contested", "## Folklore and open questions", "## Sources")

#: Phrases a rule's text uses when its peer-reviewed source stops short of
#: what the rule prescribes; such a rule takes the strength of what remains.
NOT_TESTED = ("not a tested prescription", "not a tested device",
              "not verified in this pass", "does not test")

#: Calls the strength audit made against the definition, pinned so a later
#: edit to the table restates them on purpose: structure 19's study does not
#: test placement or closure, structure 25's is descriptive, attention 2's
#: does not test the stand-in a film must use, attention 10's is a
#: correlation, not a test of stating the stake.
AUDITED = {"structure 19": "practitioner", "structure 25": "house",
           "attention 2": "house", "attention 10": "practitioner"}

RULE_START = re.compile(r"^(\d+)\. \*\*")
STAGES_LINE = re.compile(r"^\s*Stages:\s*(.*)$", re.M)
STRENGTH_ROW = re.compile(r"^\|\s*([a-z]+ \d+)\s*\|\s*([a-z-]+)\s*\|\s*$", re.M)


@dataclass(frozen=True)
class Rule:
    """One numbered rule. `id` is the corpus's own spelling: "structure 7"."""
    id: str
    file: str
    text: str
    check: str
    stages: tuple[str, ...]
    strength: str | None


def short(path: Path | str) -> str:
    """The word a rule id starts with: structure-and-hooks.md -> structure."""
    return Path(path).name.split("-")[0]


def strengths(readme: Path = README) -> dict[str, str]:
    """{rule id: strength} from the README's strength table."""
    if not readme.is_file():
        return {}
    return dict(STRENGTH_ROW.findall(readme.read_text(encoding="utf-8")))


def rules_of(path: Path | str) -> list[Rule]:
    """The numbered rules of one corpus file, in order.

    A rule starts at a line `N. **` and runs to the next rule or the next `## `
    heading, so the bold statement may span lines and the intro's prose is never
    read as a rule; nothing from `## Contested` on is a rule. Its strength
    comes from the README beside the file.
    """
    path = Path(path)
    table = strengths(path.parent / "README.md")
    blocks: list[tuple[int, list[str]]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() == SECTIONS[0]:
            break  # Contested, Folklore and Sources hold no rules
        start = RULE_START.match(line)
        if start:
            blocks.append((int(start.group(1)), [line]))
        elif line.startswith("## ") or line.strip() == "---":
            blocks.append((0, []))  # closes the open rule
        elif blocks and blocks[-1][0]:
            blocks[-1][1].append(line)
    out = []
    for n, lines in blocks:
        if not n:
            continue
        text = "\n".join(lines).strip()
        rid = f"{short(path)} {n}"
        tag = STAGES_LINE.search(text)
        stages = tuple(s.strip() for s in tag.group(1).split(",") if s.strip()) if tag else ()
        check = ""
        if "Check:" in text:
            check = text.split("Check:", 1)[1]
            check = STAGES_LINE.split(check, 1)[0] if tag else check
            check = " ".join(check.split())
        out.append(Rule(rid, path.name, text, check, stages, table.get(rid)))
    return out


def corpus() -> list[Rule]:
    """Every rule of the four files, in file order."""
    return [rule for name in FILES for rule in rules_of(CORPUS / name)]


def stage_column() -> dict[str, set[str]]:
    """{file: the stages its README row says it feeds}."""
    out = {}
    for line in README.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0].strip("`") in FILES:
            out[cells[0].strip("`")] = {s.strip() for s in cells[3].split(",")}
    return out


class Corpus(unittest.TestCase):
    def test_the_corpus_is_the_readme_and_four_rule_files(self):
        self.assertTrue(README.is_file())
        self.assertEqual(sorted(p.name for p in CORPUS.glob("*.md")),
                         sorted(("README.md",) + FILES))
        text = README.read_text(encoding="utf-8")
        for name in FILES:
            self.assertIn(f"`{name}`", text, name)
        self.assertNotIn("Destination in the repo", text)

    def test_rules_are_numbered_from_one_without_a_gap(self):
        for name in FILES:
            with self.subTest(file=name):
                ids = [r.id for r in rules_of(CORPUS / name)]
                self.assertTrue(ids, name)
                self.assertEqual(ids, [f"{short(name)} {n}" for n in range(1, len(ids) + 1)])

    def test_the_readme_counts_each_files_rules(self):
        text = README.read_text(encoding="utf-8")
        for name in FILES:
            with self.subTest(file=name):
                row = re.search(rf"^\| `{re.escape(name)}` \|[^|]*\| (\d+) \|", text, re.M)
                self.assertIsNotNone(row, name)
                self.assertEqual(int(row.group(1)), len(rules_of(CORPUS / name)))

    def test_every_rule_has_a_check_stages_and_a_url(self):
        for rule in corpus():
            with self.subTest(rule=rule.id):
                self.assertTrue(rule.check, f"{rule.id} has no Check: line")
                self.assertTrue(rule.stages, f"{rule.id} has no Stages: line")
                self.assertRegex(rule.text, r"https?://", f"{rule.id} cites no URL")

    def test_stages_name_only_known_stages_or_schema_fields(self):
        for rule in corpus():
            with self.subTest(rule=rule.id):
                odd = [s for s in rule.stages if s not in STAGES and not FIELD.match(s)]
                self.assertEqual(odd, [], rule.id)
                self.assertTrue(set(rule.stages) & set(STAGES),
                                f"{rule.id} names fields but no stage")

    def test_the_readme_stage_column_is_distilled_from_the_tags(self):
        column = stage_column()
        for name in FILES:
            with self.subTest(file=name):
                tagged = {s for r in rules_of(CORPUS / name) for s in r.stages if s in STAGES}
                self.assertEqual(column.get(name), tagged)
        text = README.read_text(encoding="utf-8")
        self.assertIn("Stages:", text)
        self.assertNotIn("applies_to", text)

    def test_every_file_says_what_is_contested_folklore_and_sourced(self):
        for name in FILES:
            text = (CORPUS / name).read_text(encoding="utf-8")
            for heading in SECTIONS:
                with self.subTest(file=name, section=heading):
                    self.assertRegex(text, rf"(?m)^{re.escape(heading)}\s*$")

    def test_the_strength_table_covers_every_rule_with_the_four_words(self):
        table = strengths()
        ids = [r.id for r in corpus()]
        self.assertEqual(sorted(table), sorted(ids))
        self.assertEqual(sorted(set(table.values()) - set(STRENGTHS)), [])
        for rule in corpus():
            self.assertIn(rule.strength, STRENGTHS, rule.id)


    def test_a_rule_that_says_its_study_does_not_test_it_is_not_peer_reviewed(self):
        # The word is the strongest source that supports the rule as stated.
        # A study that only describes what texts do, that does not test the
        # device the rule prescribes, or that this pass could not verify does
        # not lift a rule to peer-reviewed, whatever its venue.
        for rule in corpus():
            said = [p for p in NOT_TESTED if p in " ".join(rule.text.split()).lower()]
            if said:
                with self.subTest(rule=rule.id, said=said):
                    self.assertNotEqual(rule.strength, "peer-reviewed")

    def test_the_audited_calls_hold(self):
        table = strengths()
        for rid, word in AUDITED.items():
            with self.subTest(rule=rid):
                self.assertEqual(table.get(rid), word)

    def test_the_definition_names_the_transfer_and_what_does_not_count(self):
        text = " ".join(README.read_text(encoding="utf-8").split())
        definition = text[text.index("**peer-reviewed**"):text.index("**platform**")]
        for words in ("YouTube retention", "describes", "does not test", "could not verify"):
            self.assertIn(words, definition)


class Parser(unittest.TestCase):
    """The parser reads a missing part as missing, so the checks above can fail."""

    def parse(self, text: str, table: str = "") -> list[Rule]:
        tmp = Path(tempfile.mkdtemp())
        (tmp / "structure-and-hooks.md").write_text(text, encoding="utf-8")
        if table:
            (tmp / "README.md").write_text(table, encoding="utf-8")
        return rules_of(tmp / "structure-and-hooks.md")

    def test_a_statement_may_span_lines_and_the_parts_are_read(self):
        rules = self.parse(
            "# T\n\nIntro with a Check: in prose.\n\n## S\n\n"
            "1. **A rule whose bold statement\n   runs on.** Evidence (https://x.org).\n"
            "   Check: the reviewer\n   looks.\n   Stages: script, plan.promise\n\n"
            "2. **Bare.** No source.\n\n## Contested\n\n3. **Not a rule.**\n",
            "| Rule | Strength |\n|---|---|\n| structure 1 | platform |\n")
        self.assertEqual([r.id for r in rules], ["structure 1", "structure 2"])
        first, bare = rules
        self.assertEqual(first.check, "the reviewer looks.")
        self.assertEqual(first.stages, ("script", "plan.promise"))
        self.assertEqual(first.strength, "platform")
        self.assertEqual((bare.check, bare.stages, bare.strength), ("", (), None))
        self.assertNotRegex(bare.text, r"https?://")


if __name__ == "__main__":
    unittest.main()
