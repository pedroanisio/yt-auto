"""AUTO-89: the craft corpus reaches every decision through a shipped house.

`ytstudio/house/default.md` is the distilled form of `docs/narrative/`: each
section holds the rule statements that bear on one stage, with a comment that
lists the corpus rule ids it came from. It is loaded under the channel's
`work/house.md` and the project's own, which replace it section by section.
These tests hold the file to its sources (every id exists, every tagged rule
names the section's stage), to its size (every byte enters every prompt and
every digest) and to invariant 2 (editorial craft, never a look).
"""

import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ytstudio import doctor, house, workspace
from ytstudio.core import write_json

ROOT = Path(__file__).resolve().parent.parent
NARRATIVE = ROOT / "docs" / "narrative"
DEFAULT = ROOT / "ytstudio" / "house" / "default.md"

#: The short names a section's comment uses for the corpus files.
CORPUS = {"structure": "structure-and-hooks.md",
          "attention": "attention-and-stakes.md",
          "direction": "direction-and-narration.md",
          "judging": "judging-narrative.md"}

SHIPPED = ("all", "script", "styles", "vfx", "sound", "voice", "format")

#: The stage a section's rules must be tagged for. `format` is read by the
#: script stage; `all` reaches every stage, so any tag will do.
READ_BY = {"format": "script"}

#: Words that name a look. The default is editorial craft; the look is
#: styles.json's (invariant 2).
LOOK_WORDS = ("hex", "palette", "font", "2D", "3D", "gradient", "colour", "color")

RULE = re.compile(r"^(\d+)\. \*\*", re.M)
COMMENT = re.compile(r"<!--\s*rules:\s*(.*?)\s*-->", re.S)


def rules_of(path: Path) -> dict[int, dict]:
    """{number: {"text", "stages"}} for the numbered rules of one corpus file.

    A rule runs from its `N. **` line to the next rule or heading; Contested,
    Folklore and Sources come after the rules and are not read. `stages` is
    the set its `Stages:` line names, or None while the file has no tags."""
    text = path.read_text(encoding="utf-8").split("\n## Contested", 1)[0]
    found = list(RULE.finditer(text))
    out = {}
    for n, match in enumerate(found):
        end = found[n + 1].start() if n + 1 < len(found) else len(text)
        body = text[match.start():end]
        body = re.split(r"^#", body, maxsplit=1, flags=re.M)[0]
        tag = re.search(r"^\s*Stages:\s*(.+)$", body, re.M)
        stages = ({s.strip(" `.").lower() for s in re.split(r"[,;]", tag.group(1)) if s.strip()}
                  if tag else None)
        out[int(match.group(1))] = {"text": body, "stages": stages}
    return out


def raw_sections(text: str) -> dict[str, str]:
    """{name: the section as written, comment included}."""
    found = list(house.HEADING.finditer(text))
    return {m.group(1).strip().lower(): text[m.end():(found[n + 1].start()
                                                      if n + 1 < len(found) else len(text))]
            for n, m in enumerate(found)}


def cited(comment: str) -> list[tuple[str, int]]:
    """`structure 1, 6, 9; attention 8` into [("structure", 1), ...]; a
    range `27-28` names both ends and everything between."""
    out = []
    for group in comment.split(";"):
        name, _, numbers = group.strip().partition(" ")
        for item in numbers.split(","):
            item = item.strip()
            if not item:
                continue
            low, _, high = item.replace("–", "-").partition("-")
            out += [(name, n) for n in range(int(low), int(high or low) + 1)]
    return out


def stage_problems(raw: dict[str, str], corpus: dict[str, dict]) -> list[str]:
    """Each cited rule whose Stages: tag does not name the section's reader.

    A file with no tag at all is not judged yet (AUTO-88 tags the corpus);
    once a file has any, a cited rule of it without one is a problem. The
    script stage also reads the plan and shot fields its rules name, and
    `## all` reaches every stage, so any tag will do there."""
    tagged = {file: any(rule["stages"] is not None for rule in rules.values())
              for file, rules in corpus.items()}
    out = []
    for name, body in raw.items():
        stage = READ_BY.get(name, name)
        for file, number in cited(COMMENT.search(body).group(1)):
            if not tagged[file]:
                continue
            stages = corpus[file][number]["stages"]
            if stages is None:
                out.append(f"## {name}: {file} {number} has no Stages: tag")
            elif name != "all" and not (stage in stages or (stage == "script" and any(
                    s.startswith(("plan.", "shot.")) for s in stages))):
                out.append(f"## {name}: {file} {number} is tagged {sorted(stages)}")
    return out


def args(**kw):
    return SimpleNamespace(**({"work": None, "house": None, "no_house": False} | kw))


class Loading(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        self.project = self.work / "a-film"
        self.project.mkdir()
        self.shipped = house.parse(DEFAULT.read_text(encoding="utf-8"))

    def test_the_default_ships_one_section_per_reader(self):
        self.assertEqual(tuple(self.shipped), SHIPPED)
        self.assertEqual(house.STAGES, SHIPPED)

    def test_with_no_house_file_the_default_is_what_loads(self):
        loaded = house.load(self.work, self.project)
        self.assertEqual(loaded.sections, self.shipped)
        self.assertEqual(house.files(self.work, self.project), [house.DEFAULT])

    def test_a_project_section_replaces_only_that_section(self):
        (self.project / "house.md").write_text("## script\nWrite for the ear.\n",
                                              encoding="utf-8")
        loaded = house.load(self.work, self.project)
        self.assertEqual(loaded.sections["script"], "Write for the ear.")
        for name in SHIPPED:
            if name != "script":
                self.assertEqual(loaded.sections[name], self.shipped[name], name)

    def test_the_channel_overrides_the_default_and_the_project_the_channel(self):
        (self.work / "house.md").write_text("## format\nThe channel's own format.\n"
                                           "## voice\nThe channel's pacing.\n",
                                           encoding="utf-8")
        (self.project / "house.md").write_text("## voice\nThis film's pacing.\n",
                                              encoding="utf-8")
        loaded = house.load(self.work, self.project)
        self.assertEqual(loaded.sections["format"], "The channel's own format.")
        self.assertEqual(loaded.sections["voice"], "This film's pacing.")
        self.assertEqual(loaded.sections["sound"], self.shipped["sound"])

    def test_a_channel_format_that_fixes_a_temperament_reaches_the_writer(self):
        # AUTO-158: no code reads the temperament; the writer reads the
        # channel's `## format`, which replaces the default's whole
        (self.work / "house.md").write_text(
            "## format\n- Narrator: one narrator, the viewer's proxy.\n"
            "- Temperament: wry, every film.\n", encoding="utf-8")
        block = house.load(self.work, self.project).for_stage("script")
        self.assertIn("Temperament: wry, every film.", block)
        self.assertNotIn("with none set here, this film's own", block)
        self.assertIn("with none set here, this film's own", self.shipped["format"])

    def test_a_channel_format_that_names_a_recurring_device_reaches_the_writer(self):
        # AUTO-137: recurrence is the channel's to name, and it still binds
        (self.work / "house.md").write_text(
            "## format\n- Opening: a cold open on one object, every film.\n",
            encoding="utf-8")
        block = house.load(self.work, self.project).for_stage("script")
        self.assertIn("Opening: a cold open on one object, every film.", block)
        self.assertNotIn("open on this film's question", block)

    def test_no_house_is_an_empty_house(self):
        loaded = house.load(self.work, self.project, enabled=False)
        self.assertEqual((loaded.sections, loaded.sources, loaded.sha256), ({}, [], ""))
        block, digest = house.for_args(args(no_house=True), None, "script")
        self.assertEqual((block, digest["sources"]), ("", []))

    def test_the_digest_names_the_default_and_changes_with_an_override(self):
        bare = house.load(self.work, self.project).digest()
        self.assertEqual(bare["sources"], ["ytstudio/house/default.md"])
        self.assertTrue(bare["sha256"])
        (self.work / "house.md").write_text("## all\nOne rule.\n", encoding="utf-8")
        channel = house.load(self.work, self.project).digest()
        self.assertEqual(channel["sources"],
                         ["ytstudio/house/default.md", str(self.work / "house.md")])
        self.assertNotEqual(channel["sha256"], bare["sha256"])

    def test_a_comment_only_edit_is_not_a_changed_house(self):
        # The digest is of what a prompt can see; a provenance comment is not.
        channel = self.work / "house.md"
        channel.write_text("## voice\n<!-- rules: direction 27 -->\nRead slowly.\n",
                           encoding="utf-8")
        before = house.load(self.work, self.project).digest("voice")
        channel.write_text("## voice\n<!-- rules: direction 27, 28 -->\nRead slowly.\n",
                           encoding="utf-8")
        after = house.load(self.work, self.project).digest("voice")
        self.assertEqual(after, before)

    def test_the_digest_names_the_stage_and_the_block_it_was_handed(self):
        bare = house.load(self.work, self.project)
        script_before, voice_before = bare.digest("script"), bare.digest("voice")
        self.assertEqual(script_before["stage"], "script")
        self.assertTrue(script_before["block_sha256"])
        (self.work / "house.md").write_text("## voice\nThe channel's pacing.\n",
                                           encoding="utf-8")
        edited = house.load(self.work, self.project)
        # The whole house changed; the screenplay's block did not, so its
        # cached decisions are not asked again, and the read's are.
        self.assertNotEqual(edited.sha256, bare.sha256)
        self.assertEqual(edited.digest("script")["block_sha256"],
                         script_before["block_sha256"])
        self.assertNotEqual(edited.digest("voice")["block_sha256"],
                            voice_before["block_sha256"])
        self.assertEqual(house.load(enabled=False).digest("voice")["block_sha256"], "")

    def test_for_args_hands_a_stage_the_default(self):
        block, digest = house.for_args(args(), SimpleNamespace(root=self.project), "script")
        self.assertIn(self.shipped["script"], block)
        self.assertEqual(digest["sources"], ["ytstudio/house/default.md"])
        self.assertEqual(digest["stage"], "script")

    def test_the_script_stage_reads_the_format_and_a_reader_without_a_section_reads_all(self):
        loaded = house.load(self.work, self.project)
        script_block = loaded.for_stage("script")
        self.assertIn(self.shipped["format"], script_block)
        self.assertLess(script_block.index(self.shipped["format"]),
                        script_block.index(self.shipped["script"]))
        qa_block = loaded.for_stage("qa")
        self.assertIn(self.shipped["all"], qa_block)
        for name in SHIPPED[1:]:
            self.assertNotIn(self.shipped[name], qa_block, name)

    def test_the_keyframe_reads_the_vfx_section_until_the_channel_writes_its_own(self):
        # The keyframe authors the lead section's animation, which the vfx
        # stage then follows, so it is held to the animation rules.
        from ytstudio import keyframe
        film = SimpleNamespace(root=self.project)
        block, digest = keyframe.house_rules(args(), film)
        self.assertEqual((block, digest), house.for_args(args(), film, "keyframe"))
        self.assertIn(self.shipped["all"], block)
        self.assertIn(self.shipped["vfx"], block)
        self.assertEqual(digest["stage"], "keyframe")
        (self.project / "house.md").write_text("## keyframe\nLock the look first.\n",
                                              encoding="utf-8")
        own = house.load(self.work, self.project).for_stage("keyframe")
        self.assertIn("Lock the look first.", own)
        self.assertNotIn(self.shipped["vfx"], own)

    def test_the_provenance_comment_never_reaches_a_prompt(self):
        loaded = house.load(self.work, self.project)
        for name in SHIPPED:
            self.assertNotIn("<!--", loaded.for_stage(name), name)
        self.assertEqual(house.parse("## all\n<!-- rules: x 1 -->\nSay it.\n"),
                         {"all": "Say it."})


class TheShippedText(unittest.TestCase):
    """The file against its sources, its size and invariant 2."""

    def setUp(self):
        self.text = DEFAULT.read_text(encoding="utf-8")
        self.raw = raw_sections(self.text)
        self.parsed = house.parse(self.text)
        self.corpus = {name: rules_of(NARRATIVE / file) for name, file in CORPUS.items()}

    def test_no_retime_or_qa_section(self):
        # retime has no corpus rule; qa's rubric is rendered from the rubric
        # itself so it is written once.
        self.assertNotIn("retime", self.raw)
        self.assertNotIn("qa", self.raw)

    def test_every_section_names_the_rules_it_came_from_and_they_exist(self):
        for name, body in self.raw.items():
            with self.subTest(section=name):
                comment = COMMENT.search(body)
                self.assertIsNotNone(comment, f"## {name} has no rules comment")
                ids = cited(comment.group(1))
                self.assertTrue(ids)
                for file, number in ids:
                    self.assertIn(file, self.corpus, f"## {name}: unknown file {file}")
                    self.assertIn(number, self.corpus[file], f"## {name}: {file} {number}")

    def test_every_cited_rule_is_tagged_for_the_reader(self):
        # docs/narrative/ gets its Stages: tags from AUTO-88; until a file
        # carries any, there is nothing to hold the default to, and the suite
        # says so rather than passing with no assertion. When AUTO-88 adds
        # test_doc_narrative.rules_of, this file's reader gives way to it.
        if not any(rule["stages"] is not None
                   for rules in self.corpus.values() for rule in rules.values()):
            self.skipTest("docs/narrative/ carries no Stages: tags yet (AUTO-88)")
        self.assertEqual(stage_problems(self.raw, self.corpus), [])

    def test_the_stages_check_is_strict_per_file(self):
        # Once a file is tagged, a cited rule of it without a tag fails: a
        # dropped tag is drift, not a rule still waiting for one.
        corpus = {"structure": {1: {"stages": {"script"}}, 2: {"stages": None}},
                  "attention": {8: {"stages": None}}}
        raw = {"script": "<!-- rules: structure 1, 2; attention 8 -->",
               "sound": "<!-- rules: structure 1 -->",
               "all": "<!-- rules: structure 2 -->"}
        self.assertEqual(stage_problems(raw, corpus),
                         ["## script: structure 2 has no Stages: tag",
                          "## sound: structure 1 is tagged ['script']",
                          "## all: structure 2 has no Stages: tag"])

    def test_the_check_reads_the_corpus(self):
        self.assertEqual(len(self.corpus["structure"]), 40)
        self.assertIn("BUT", self.corpus["structure"][1]["text"])
        self.assertEqual(cited("structure 1, 6; direction 27-28"),
                         [("structure", 1), ("structure", 6),
                          ("direction", 27), ("direction", 28)])

    def test_every_section_is_short(self):
        for name, body in self.raw.items():
            with self.subTest(section=name):
                self.assertLess(len(body), 1500)

    def test_the_script_section_names_the_opening_trigger_and_stays_short(self):
        # AUTO-136: the trigger went into an existing bullet, not a new one
        script = self.raw["script"]
        self.assertIn("Open on a question, a paradox or an unexpected observation.", script)
        # the trigger did not cost the payoff rule, nor read as the question
        # the promise restates
        self.assertIn("Nothing promised goes unpaid.", script)
        self.assertNotIn("question within the first 30 seconds, on a question", script)
        self.assertIn(("attention", 1), cited(COMMENT.search(script).group(1)))
        self.assertEqual(len(self.parsed["script"].splitlines()), 11)
        self.assertLess(len(script), 1500)

    def test_all_is_three_sentences_at_most(self):
        # `## all` reaches every stage that asks the model, props and gate too.
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", self.parsed["all"]) if s.strip()]
        self.assertLessEqual(len(sentences), 3)

    def test_no_look_is_named(self):
        for name, body in self.parsed.items():
            with self.subTest(section=name):
                self.assertNotIn("#", body)
                for word in LOOK_WORDS:
                    self.assertIsNone(re.search(rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z])",
                                                body, re.I), word)

    def line(self, section: str, starts: str) -> str:
        found = [l for l in self.parsed[section].splitlines() if l.startswith(starts)]
        self.assertEqual(len(found), 1, f"## {section}: {starts!r}")
        return found[0]

    def test_every_format_device_is_neutral_until_the_channel_sets_it(self):
        # The script stage knows no other film and no series: a device the
        # default asks for unconditionally is one the writer makes up.
        for device in ("Opening", "Ending", "Hand-off", "Series", "Temperament", "Form"):
            with self.subTest(device=device):
                self.assertRegex(self.line("format", f"- {device}:"), r"\bnone\b")
        self.assertNotRegex(self.line("format", "- Hand-off:"), r"^- Hand-off: one spoken")

    def test_the_narrator_talks_with_the_viewer_and_may_be_funny_on_the_point(self):
        # AUTO-138: attention 25's conversational voice and attention 21's
        # humour that serves a claim, in the format's one Narrator bullet
        narrator = self.line("format", "- Narrator:")
        for words in ("the viewer's proxy who is finding out", "says 'you' and 'we'",
                      "asks aloud the question the viewer is about to ask",
                      "warns before the step most likely to lose them",
                      "may be funny when the joke makes the point"):
            self.assertIn(words, narrator)
        said = cited(COMMENT.search(self.raw["format"]).group(1))
        for rule in (("attention", 18), ("attention", 21), ("attention", 25)):
            self.assertIn(rule, said)
        self.assertLess(len(self.raw["format"]), 1500)

    def test_the_format_keeps_one_proxy_narrator_and_leaves_the_temperament_to_the_film(self):
        # AUTO-158: structure 37's "same narrator persona" is held as one
        # proxy narrator; the temperament is each film's, named in its plan
        narrator = self.line("format", "- Narrator:")
        self.assertIn("one narrator across films", narrator)
        self.assertIn("viewer's proxy", narrator)
        self.assertIn("expertise belongs to named sources", narrator)
        self.assertNotIn("one persona across films", narrator)
        temperament = self.line("format", "- Temperament:")
        self.assertIn("with none set here, this film's own", temperament)
        self.assertIn("persona", temperament)
        self.assertIn("a temperament per film is a house extension", self.raw["format"])
        self.assertNotIn("house extension", self.parsed["format"])

    def test_the_format_leaves_the_form_to_the_film_unless_the_channel_fixes_it(self):
        # AUTO-157: FORMS is a house prior; structure 20 gives no list and
        # no template, so the default fixes no form and names how a channel
        # does. Its own example sits inside the line, so it fixes nothing.
        from ytstudio import script
        form = self.line("format", "- Form:")
        self.assertEqual(sum(1 for line in self.parsed["format"].splitlines()
                             if line.startswith("- Form:")), 1)
        self.assertIn("none", form)
        self.assertIn('("- Form: countdown")', form)
        self.assertIn("choose this film's form from its subject", form)
        self.assertEqual(script.house_form(self.parsed["format"]), "")
        self.assertIn(("structure", 20), cited(COMMENT.search(self.raw["format"]).group(1)))
        self.assertLess(len(self.raw["format"]), 1500)

    def test_the_styles_ask_for_a_needed_formula_and_space_shown_in_space(self):
        # AUTO-145: direction 9 (already the section's tag) puts the formula
        # after the viewer has seen what it relates happen; the spatial line
        # is a house rule, so the tag does not change.
        formula = self.line("styles", "- A formula appears only when the beat needs it")
        self.assertIn("never a formula as the first picture of an idea", formula)
        spatial = self.line("styles", "- Where the idea is spatial")
        for words in ("a scale comparison", "a cutaway", "a camera move through it",
                      "not a label"):
            self.assertIn(words, spatial)
        self.assertEqual(cited(COMMENT.search(self.raw["styles"]).group(1)),
                         [("direction", 4), ("direction", 9)])
        self.assertLess(len(self.raw["styles"]), 1500)

    def test_the_format_chooses_each_film_for_its_subject(self):
        # AUTO-137: no persona and no device recurs by default; structure
        # 20's "no fixed template" (Fong) is the default, and structure 37's
        # sameness is what a channel's own house file names
        body = self.parsed["format"]
        self.assertNotIn("The channel is a repeatable format", body)
        self.assertNotIn("the same device every film", body)
        self.assertNotIn("one persona across films", body)
        self.assertIn("Each film's format is chosen for its subject; only what the "
                      "channel's own house file names recurs.", body)
        for device in ("Opening", "Ending"):
            with self.subTest(device=device):
                self.assertIn("one recurs only when set here",
                              self.line("format", f"- {device}:"))
        self.assertIn(("structure", 20), cited(COMMENT.search(self.raw["format"]).group(1)))
        self.assertIn("structure 20", self.raw["format"].split("-->")[-2])
        self.assertNotIn("structure 20", body)

    def test_the_strength_is_the_corpus_strength(self):
        # attention 8 says "whenever possible"; structure 19 lets a section
        # defer its answer to a named later one; structure 35 allows the one
        # spoken hand-off inside the quiet tail.
        self.assertIn("whenever possible", self.line("script", "- Say what is at risk"))
        self.assertIn("names the later section", self.line("script", "- Each section opens"))
        self.assertIn("beyond the one hand-off line", self.line("sound", "- The last 5 to 20"))

    def test_no_measured_number_is_carried(self):
        # Effect sizes and percentages are evidence, read by maintainers; the
        # unverified ones must not become prompt text either.
        for name, body in self.parsed.items():
            with self.subTest(section=name):
                self.assertNotIn("%", body)
                self.assertIsNone(re.search(r"\b[dgr] ?= ?\.?\d", body))
                self.assertNotIn("http", body)


class Doctor(unittest.TestCase):
    """A manifest decided under another house is one whose decisions the next
    run asks again; doctor says so before the money is spent."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "a-film"
        self.root.mkdir()
        write_json(self.root / "idea.json", {"title": "T", "genre": "g", "description": "d"})

    def house_findings(self, work=None):
        report = doctor.check_project(workspace.Project(self.root), work=work)
        return [f.message for f in report.findings if f.area == "house" and f.level == "warn"]

    def decided(self, name, stage, work=None, nested=False):
        digest = house.load(work or self.root.parent, self.root).digest(stage)
        write_json(self.root / name, {"engine": {"house": digest}} if nested
                   else {"house": digest})

    def test_a_manifest_under_another_house_digest_is_a_warning(self):
        write_json(self.root / "script.json", {"house": {"sources": [], "sha256": "0123"}})
        found = self.house_findings()
        self.assertEqual(len(found), 1)
        self.assertIn("decided under a different house digest", found[0])
        self.assertIn("script.json", found[0])

    def test_the_digest_inside_the_engine_report_counts_too(self):
        write_json(self.root / "qa.json", {"engine": {"house": {"sha256": "0123"}}})
        self.assertEqual(len(self.house_findings()), 1)

    def test_only_the_stage_whose_block_changed_is_warned(self):
        # Every decision's digest holds the stage's block, not the whole
        # house: an edit to `## voice` re-asks the read and nothing else.
        self.decided("script.json", "script")
        self.decided("voice.json", "voice")
        (self.root.parent / "house.md").write_text("## voice\nRead slowly.\n",
                                                   encoding="utf-8")
        self.addCleanup((self.root.parent / "house.md").unlink)
        found = self.house_findings()
        self.assertEqual(len(found), 1)
        self.assertIn("voice.json", found[0])
        self.assertIn("decided under a different house digest", found[0])

    def test_a_comment_only_edit_is_quiet(self):
        channel = self.root.parent / "house.md"
        self.addCleanup(lambda: channel.unlink(missing_ok=True))
        channel.write_text("## voice\n<!-- rules: direction 27 -->\nRead slowly.\n",
                           encoding="utf-8")
        self.decided("voice.json", "voice")
        channel.write_text("## voice\n<!-- rules: direction 27, 28 -->\nRead slowly.\n",
                           encoding="utf-8")
        self.assertEqual(self.house_findings(), [])

    def test_a_stale_report_beside_a_current_one_is_a_warning(self):
        now = house.load(self.root.parent, self.root).digest("build")
        write_json(self.root / "build.json",
                   {"house": now, "engine": {"review": {"house": {"sha256": "0123"}}}})
        found = self.house_findings()
        self.assertEqual(len(found), 1)
        self.assertIn("build.json", found[0])

    def test_the_pilot_counts(self):
        write_json(self.root / "pilot.json", {"engine": {"house": {"sha256": "0123"}}})
        self.assertEqual(len(self.house_findings()), 1)

    def test_the_house_is_read_from_the_work_directory_doctor_was_given(self):
        # A film kept outside ./work is still run with --work's house.md.
        work = self.root.parent / "elsewhere"
        work.mkdir()
        (work / "house.md").write_text("## script\nThe channel's own rule.\n",
                                       encoding="utf-8")
        self.decided("script.json", "script", work=work)
        self.assertEqual(self.house_findings(work=work), [])
        self.assertEqual(len(self.house_findings()), 1)

    def test_a_manifest_under_the_current_house_is_quiet(self):
        now = house.load(self.root.parent, self.root).digest()
        write_json(self.root / "script.json", {"house": now})
        write_json(self.root / "sound.json", {"engine": {"house": now}})
        self.assertEqual(self.house_findings(), [])


if __name__ == "__main__":
    unittest.main()
