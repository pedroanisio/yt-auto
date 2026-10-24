"""Research asks for a sourced historical parallel (AUTO-151).

No research stage asked what happened before, so a film explained its
subject as if nothing like it had ever happened. An explainer researched at
depth 4 or more now spends one of its questions on a dated precedent or
parallel, and the outline returns at most two `parallels`, each with how it
maps and where it breaks. None is trusted on the model's word: a row is
kept only when its source is a page the research opened. The rest are
dropped with a warning; an empty list with a reason is valid, since no
parallel is better than an invented one. The idea's stakes now reach the
research too.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import doctor, harness, research, workspace  # noqa: E402
from ytstudio.core import Idea  # noqa: E402
from tests.test_research_levels import _Researched  # noqa: E402

OPENED = ["https://example.org/tacoma", "https://Example.org/quebec/",
          "https://example.org/dee"]


def parallel(**kw):
    row = {"event": "Tacoma Narrows bridge collapse", "year": "1940",
           "how_it_maps": "a design trusted on paper failed under a load nobody modelled",
           "where_it_breaks": "Tacoma failed by flutter, not by corroded stays",
           "source_url": "https://example.org/tacoma"}
    row.update(kw)
    return row


QUEBEC = parallel(event="Quebec bridge collapse", year="1907",
                  source_url="https://example.org/quebec")
DEE = parallel(event="Dee bridge disaster", year="c. 1847",
               source_url="https://example.org/dee")
BLOG = parallel(event="A bridge a blog remembers", source_url="https://example.org/blog")


def outline(**kw):
    row = {"working_title": "t", "runtime": "5 min", "sections": [],
           "misconceptions": [], "identifiable_case": {"who": "", "source": ""},
           "human_stories": [], "parallels": [], "parallels_why": ""}
    row.update(kw)
    return row


class Schema(unittest.TestCase):
    def test_both_fields_are_required(self):
        required = research.OUTLINE_SCHEMA["required"]
        self.assertIn("parallels", required)
        self.assertIn("parallels_why", required)
        items = research.OUTLINE_SCHEMA["properties"]["parallels"]["items"]
        self.assertEqual(set(items["required"]),
                         {"event", "year", "how_it_maps", "where_it_breaks", "source_url"})

    def test_an_outline_without_them_is_missing_them(self):
        old = outline()
        del old["parallels"], old["parallels_why"]
        missing = harness.missing_fields(old, research.OUTLINE_SCHEMA)
        self.assertIn("parallels", missing)
        self.assertIn("parallels_why", missing)

    def test_a_row_without_a_source_is_missing_it(self):
        row = parallel()
        del row["source_url"]
        self.assertIn("parallels[0].source_url",
                      harness.missing_fields(outline(parallels=[row]),
                                             research.OUTLINE_SCHEMA))

    def test_the_year_is_a_string(self):
        errors = harness.type_errors(outline(parallels=[parallel(year=1850)]),
                                     research.OUTLINE_SCHEMA)
        self.assertTrue(any("year" in e for e in errors), errors)

    def test_none_with_a_reason_is_valid(self):
        row = outline(parallels=[], parallels_why="no documented precedent")
        self.assertEqual(harness.missing_fields(row, research.OUTLINE_SCHEMA), [])
        self.assertEqual(harness.type_errors(row, research.OUTLINE_SCHEMA), [])


class Prompts(_Researched):
    def asked(self, *, depth, idea=None):
        """The 01-questions prompt a researcher at `depth` sends."""
        if idea is not None:
            self.idea = idea
        r = self.researcher(jobs=1, parties=1)
        r.depth = depth
        r.fresh = True      # asked anew each time, never from the cached stage
        seen = {}
        answer = self.harness.decide

        def decide(name, prompt, schema):
            seen[name] = prompt
            return answer(name, prompt, schema)
        self.harness.decide = decide
        r.questions()
        return seen["01-questions"], r

    def test_a_deep_explainer_asks_for_a_parallel(self):
        prompt, r = self.asked(depth=6)
        self.assertIn("historical parallel or precedent", prompt)
        self.assertIn("Among the 6 questions", prompt)
        self.assertTrue(prompt.endswith(research.PARALLEL_QUESTION.format(depth=6)))

    def test_a_shallow_explainer_is_asked_as_before(self):
        for depth in (2, 3):
            prompt, r = self.asked(depth=depth)
            self.assertEqual(prompt, f"{self.idea.block()}\n\n"
                             + research.SCOPES["explainer"]["plan"].format(depth=depth))

    def test_a_cinematic_film_is_never_asked(self):
        idea = Idea(title="Comets", genre="cinematic", description="A tail at night.")
        prompt, r = self.asked(depth=6, idea=idea)
        self.assertEqual(r.scope["name"], "cinematic")
        self.assertNotIn("historical parallel", prompt)
        self.assertEqual(prompt, f"{idea.block()}\n\n"
                         + research.SCOPES["cinematic"]["plan"].format(depth=6))

    def test_the_outline_and_gap_prompts(self):
        out = research.SCOPES["explainer"]["outline"]
        for word in ("parallels", "where_it_breaks", "parallels_why", "source_url"):
            self.assertIn(word, out)
        self.assertIn("leave the list empty rather than name one no source documents", out)
        self.assertIn("empty parallels list", research.SCOPES["cinematic"]["outline"])
        self.assertIn("historical parallel", research.SCOPES["explainer"]["gaps"])
        # the paper film is an explainer's outline with more
        self.assertIn("parallels_why", research.SCOPES["paper"]["outline"])

    def test_the_explainer_plan_itself_is_not_edited(self):
        self.assertNotIn("parallel", research.SCOPES["explainer"]["plan"])

    def test_the_stakes_reach_the_research(self):
        idea = Idea(title="Comets", genre="explainer", description="What a tail is.",
                    stakes="a mission that misjudges the tail loses its probe")
        before = idea.block()
        prompt, r = self.asked(depth=2, idea=idea)
        self.assertIn("Stakes: a mission that misjudges the tail loses its probe", prompt)
        self.assertEqual(r._idea_block(), before + "\nStakes: a mission that misjudges "
                         "the tail loses its probe")
        self.assertEqual(idea.block(), before)
        self.assertNotIn("Stakes", idea.block())

    def test_a_cinematic_film_with_stakes_gets_them_and_no_parallel(self):
        # the stakes reach every film's research; the parallel only a spoken one's
        idea = Idea(title="Comets", genre="cinematic", description="A tail at night.",
                    stakes="the last night the comet is seen for a lifetime")
        prompt, r = self.asked(depth=6, idea=idea)
        self.assertEqual(prompt, f"{idea.block()}\nStakes: the last night the comet is "
                         "seen for a lifetime\n\n"
                         + research.SCOPES["cinematic"]["plan"].format(depth=6))
        self.assertNotIn("historical parallel", prompt)

    def test_without_stakes_the_idea_block_is_todays(self):
        prompt, r = self.asked(depth=2)
        self.assertEqual(r._idea_block(), self.idea.block())
        self.assertNotIn("Stakes", prompt)


class Kept(unittest.TestCase):
    def of(self, *rows, opened=OPENED):
        return research.parallels_of({"parallels": list(rows)}, opened)

    def test_a_row_whose_source_was_opened_is_kept(self):
        kept, warnings = self.of(parallel(), QUEBEC)
        self.assertEqual(warnings, [])
        self.assertEqual([k["event"] for k in kept],
                         ["Tacoma Narrows bridge collapse", "Quebec bridge collapse"])

    def test_a_url_variant_still_matches(self):
        for url in ("https://example.org/tacoma/", "https://example.org/tacoma#fig2",
                    "HTTPS://EXAMPLE.ORG/tacoma", "  https://example.org/tacoma  "):
            kept, warnings = self.of(parallel(source_url=url))
            self.assertEqual((len(kept), warnings), (1, []), url)
        # the path is not case-folded: another path is another page
        kept, warnings = self.of(parallel(source_url="https://example.org/TACOMA"))
        self.assertEqual(kept, [])

    def test_a_row_never_opened_is_dropped_with_one_warning(self):
        kept, warnings = self.of(BLOG)
        self.assertEqual(kept, [])
        self.assertEqual(warnings, ["historical parallel dropped: A bridge a blog "
                                    "remembers: source never opened "
                                    "(https://example.org/blog)"])

    def test_a_row_with_no_place_it_breaks_is_dropped(self):
        kept, warnings = self.of(parallel(where_it_breaks="  "))
        self.assertEqual(kept, [])
        self.assertEqual(warnings, ["historical parallel dropped: Tacoma Narrows bridge "
                                    "collapse: where_it_breaks is empty"])

    def test_a_row_with_no_event_is_named_by_a_question_mark(self):
        kept, warnings = self.of(parallel(event=""))
        self.assertEqual(kept, [])
        self.assertEqual(warnings, ["historical parallel dropped: ?: event is empty"])

    def test_a_row_that_is_not_a_row_is_dropped_with_a_warning(self):
        # a bare string from the outline model leaves a trace, never nothing
        kept, warnings = self.of("Tacoma, 1940", parallel())
        self.assertEqual([p["event"] for p in kept], ["Tacoma Narrows bridge collapse"])
        self.assertEqual(warnings, ["historical parallel dropped: ?: not a row"])

    def test_at_most_two_are_kept(self):
        kept, warnings = self.of(parallel(), QUEBEC, DEE)
        self.assertEqual(len(kept), 2)
        self.assertEqual(warnings, ["historical parallel dropped: Dee bridge disaster: "
                                    "more than 2"])

    def test_kept_rows_are_stripped_and_the_year_stays_a_string(self):
        kept, _ = self.of(parallel(event="  Dee bridge disaster ", year=" c. 1847 "))
        self.assertEqual(kept[0]["event"], "Dee bridge disaster")
        self.assertEqual(kept[0]["year"], "c. 1847")

    def test_an_older_or_odd_outline_has_none(self):
        self.assertEqual(research.parallels_of({"sections": []}, OPENED), ([], []))
        self.assertEqual(research.parallels_of({"parallels": "none"}, OPENED), ([], []))
        self.assertEqual(research.parallels_of(None, OPENED), ([], []))


class Why(unittest.TestCase):
    def test_the_reason_stands_only_when_none_is_kept(self):
        row = {"parallels_why": "  no documented precedent  "}
        self.assertEqual(research.parallels_why_of(row, []), "no documented precedent")
        self.assertEqual(research.parallels_why_of(row, [parallel()]), "")

    def test_a_reason_that_is_not_text_is_none(self):
        # the recording harness answers [] for every required field
        self.assertEqual(research.parallels_why_of({"parallels_why": []}, []), "")
        self.assertEqual(research.parallels_why_of({"parallels_why": None}, []), "")
        self.assertEqual(research.parallels_why_of(None, []), "")


class InTheDossier(_Researched):
    def run_with(self, rows, why=""):
        r = self.researcher(jobs=1, parties=1)
        # the web pass, cached: one question that opened the sourced pages
        r.dossier.save("02-research", [{"question": "q", "notes": "n",
                                        "visited": list(OPENED), "searches": 1}])
        answer = self.harness.decide

        def decide(name, prompt, schema):
            row = answer(name, prompt, schema)
            if name == "07-outline":
                row["parallels"] = rows
                row["parallels_why"] = why
            return row
        self.harness.decide = decide
        return r.run()

    def test_the_dossier_keeps_two_sourced_rows_and_warns_of_the_rest(self):
        data = self.run_with([parallel(), BLOG, QUEBEC, DEE])
        self.assertEqual([p["event"] for p in data["parallels"]],
                         ["Tacoma Narrows bridge collapse", "Quebec bridge collapse"])
        self.assertEqual(data["parallels_why"], "")
        dropped = [w for w in data["warnings"] if w.startswith(research.PARALLEL_DROPPED)]
        self.assertEqual(len(dropped), 2, data["warnings"])
        self.assertIn("source never opened", dropped[0])
        self.assertIn("more than 2", dropped[1])
        # the outline keeps what the model said, unvetted
        self.assertEqual(len(data["outline"]["parallels"]), 4)

    def test_the_markdown_shows_the_kept_rows_with_their_urls(self):
        data = self.run_with([parallel(), BLOG])
        research.write_dossier(research.Dossier(self.root, self.idea, at=self.root),
                               data, [])
        md = (self.root / "README.md").read_text(encoding="utf-8")
        self.assertIn("## Historical parallels", md)
        self.assertIn("- Tacoma Narrows bridge collapse (1940): maps: a design trusted on "
                      "paper failed under a load nobody modelled; breaks: Tacoma failed "
                      "by flutter, not by corroded stays — https://example.org/tacoma", md)
        self.assertIn("source never opened", md.split("## Historical parallels", 1)[1])
        # a dropped parallel is not a provenance warning about the claims
        self.assertNotIn("historical parallel dropped", md.split("## Historical", 1)[0])
        # dossier.json, read back from disk, holds the vetted rows and the warning
        saved = json.loads((self.root / "dossier.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["parallels"], data["parallels"])
        self.assertEqual([p["event"] for p in saved["parallels"]],
                         ["Tacoma Narrows bridge collapse"])
        self.assertEqual([w for w in saved["warnings"]
                          if w.startswith(research.PARALLEL_DROPPED)],
                         ["historical parallel dropped: A bridge a blog remembers: "
                          "source never opened (https://example.org/blog)"])

    def test_doctor_shows_the_written_dossier_s_dropped_rows_as_warns(self):
        # end to end: the dossier run() wrote, read back by `yt doctor`
        data = self.run_with([parallel(), BLOG, QUEBEC, DEE])
        research.write_dossier(research.Dossier(self.root, self.idea, at=self.root),
                               data, [])
        if not (self.root / "idea.json").exists():
            (self.root / "idea.json").write_text(json.dumps({"title": self.idea.title}))
        rep = doctor.check_project(workspace.Project(self.root))
        dropped = [f for f in rep.findings if f.area == "research"
                   and f.message.startswith(research.PARALLEL_DROPPED)]
        self.assertEqual([f.level for f in dropped], ["warn", "warn"])
        self.assertEqual([f.fix for f in dropped], ["", ""])

    def test_a_row_without_a_year_prints_no_empty_brackets(self):
        data = self.run_with([parallel(year="")])
        research.write_dossier(research.Dossier(self.root, self.idea, at=self.root),
                               data, [])
        md = (self.root / "README.md").read_text(encoding="utf-8")
        self.assertIn("- Tacoma Narrows bridge collapse: maps: ", md)
        self.assertNotIn("()", md)

    def test_none_with_a_reason_is_said_so(self):
        data = self.run_with([], why="no documented precedent")
        self.assertEqual((data["parallels"], data["parallels_why"]),
                         ([], "no documented precedent"))
        self.assertFalse([w for w in data["warnings"] if "parallel" in w])
        research.write_dossier(research.Dossier(self.root, self.idea, at=self.root),
                               data, [])
        md = (self.root / "README.md").read_text(encoding="utf-8")
        self.assertIn("## Historical parallels\n\n- none: no documented precedent", md)

    def test_an_older_dossier_gets_no_section(self):
        data = self.run_with([])
        for key in ("parallels", "parallels_why"):
            data.pop(key)
        research.write_dossier(research.Dossier(self.root, self.idea, at=self.root),
                               data, [])
        md = (self.root / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("Historical parallels", md)


if __name__ == "__main__":
    unittest.main()
