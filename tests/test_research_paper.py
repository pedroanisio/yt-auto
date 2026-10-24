"""AUTO-131: `yt research --paper` reads a paper as the film's subject.

No stage treated an academic paper as its input: papers came in only as
sources or assets, and the plan asked checkable questions with no place for
what the paper means. A paper now rides on the idea (`Idea.paper`). The
research reads it first, in one agentic pass (`00-paper`) whose read must be
evidenced by the engine's own tool events, plans the questions a paper film
needs (the problem's cost, the prior work, the reception, the consequences,
the limits), and decides a digest (`06b-paper`) whose items each cite
dossier claims, checked mechanically and asked once more before it fails.

No model, network or render host is reached: the engine is a fake that
streams the events a test gives it, and the harness answers by name.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from claude_engine import ClaudeError, Event, Usage  # noqa: E402
from ytstudio import assets as yt_assets  # noqa: E402
from ytstudio import cli, harness, research, spend  # noqa: E402
from ytstudio.core import Idea, compact  # noqa: E402

from test_research_levels import RAW  # noqa: E402

ARXIV = "https://arxiv.org/abs/1706.03762"
PDF = "https://arxiv.org/pdf/1706.03762"
DOI = "10.1038/nature14539"


class FakeEngine:
    """Streams what `script(prompt)` returns, and records each call's prompt,
    tools and working directory."""

    script = staticmethod(lambda prompt: [])
    calls: list[dict] = []

    def __init__(self, config):
        self.config = config
        self.usage = Usage()

    def stream(self, prompt, system=None):
        FakeEngine.calls.append({"prompt": prompt, "tools": tuple(self.config.tools or ()),
                                 "cwd": self.config.cwd})
        yield from FakeEngine.script(prompt)


def fetch(url):
    return Event(type="assistant", subtype="tool_use", tool_name="WebFetch",
                 tool_input={"url": url})


def read(path):
    return Event(type="assistant", subtype="tool_use", tool_name="Read",
                 tool_input={"file_path": str(path)})


def said(text):
    return Event(type="assistant", text=text)


def done(denied=()):
    return Event(type="result", permission_denials=list(denied))


def blank(schema):
    """An answer of the schema's required fields, each empty of its type."""
    props = schema.get("properties", {})
    empty = {"array": list, "object": dict, "integer": int}
    return {k: empty.get(props.get(k, {}).get("type"), str)() for k in schema.get("required", [])}


class PaperHarness:
    """Answers each decision from `answers` by name (a callable is handed
    the prompt), else blank; keeps every prompt and what was forgotten."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.prompts: dict[str, str] = {}
        self.forgotten: list[str] = []
        self.usage = Usage()

    def decide(self, name, prompt, schema):
        self.prompts[name] = prompt
        answer = self.answers.get(name)
        if callable(answer):
            answer = answer(prompt)
        return copy.deepcopy(answer) if answer is not None else blank(schema)

    def forget(self, name):
        self.forgotten.append(name)

    def report(self):
        return {}


CLAIMS = [
    {"claim": "The Transformer drops recurrence entirely.", "detail": "",
     "source_title": "Attention Is All You Need", "source_url": PDF,
     "source_kind": "primary", "confidence": "high"},
    {"claim": "Training a translation model took days of GPU time.", "detail": "",
     "source_title": "A cost survey", "source_url": "https://example.org/cost",
     "source_kind": "secondary", "confidence": "medium"},
    {"claim": "It reached 28.4 BLEU on English-German.", "detail": "",
     "source_title": "Attention Is All You Need", "source_url": PDF + "v5",
     "source_kind": "primary", "confidence": "high"},
    {"claim": "Most large language models since build on it.", "detail": "",
     "source_title": "A review", "source_url": "https://example.org/review",
     "source_kind": "secondary", "confidence": "medium"},
]


def item(text, *claims):
    return {"text": text, "claims": list(claims)}


DIGEST = {
    "citation": {"title": "Attention Is All You Need", "authors": "Vaswani et al.",
                 "year": "2017", "url": PDF},
    "problem": item("Translation models were slow to train.", CLAIMS[1]["claim"]),
    "contribution": item("A network of attention alone.", CLAIMS[0]["claim"]),
    "significance": item("It trained faster and scored higher.", CLAIMS[2]["claim"]),
    "implications": item("Attention scales where recurrence did not.", CLAIMS[3]["claim"]),
    "consequences": item("The models since are built on it.", CLAIMS[3]["claim"]),
    "limitations": item("Attention costs grow with the square of the length.",
                        CLAIMS[0]["claim"]),
}


def paper_idea(ref=ARXIV, **kw):
    kw.setdefault("genre", "explainer")
    return Idea(title="Attention", description="The paper behind the chatbots.", paper=ref, **kw)


class _Case(unittest.TestCase):
    """A researcher on PaperHarness, with the catalogues stubbed and the
    engine faked."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "film"
        self.root.mkdir()
        for name in ("find_assets", "cited_papers"):
            p = mock.patch.object(yt_assets, name, side_effect=lambda *a, **k: [])
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(research, "ClaudeEngine", FakeEngine)
        p.start()
        self.addCleanup(p.stop)
        FakeEngine.calls = []
        FakeEngine.script = staticmethod(
            lambda prompt: [fetch(PDF), said("PAPER NOTES"), done()])

    def researcher(self, idea, answers=None, **kw):
        dossier = research.Dossier(self.root, idea, at=self.root)
        r = research.Researcher(idea, dossier, verbose=False, jobs=1, **kw)
        self.harness = r.harness = PaperHarness(answers)
        return r

    def raw(self):
        return sorted(p.stem for p in (self.root / "raw").iterdir())


# --------------------------------------------------------------------------- #
# the idea
# --------------------------------------------------------------------------- #

class IdeaPaper(unittest.TestCase):
    def test_the_paper_is_stripped_and_round_trips_through_compact(self):
        idea = Idea.from_raw({"title": "T", "paper": f"  {ARXIV} \n"})
        self.assertEqual(idea.paper, ARXIV)
        self.assertEqual(Idea.from_raw(compact(idea)).paper, ARXIV)
        self.assertNotIn("paper", compact(Idea(title="T", genre="g", description="d")))

    def test_the_block_without_a_paper_is_todays_string(self):
        idea = Idea(title="T", genre="g", description="d", hook="h", length="3 minutes",
                    keywords=["a", "b"])
        self.assertEqual(idea.block(), "Title: T\nGenre: g\nPremise: d\nPlanned hook: h\n"
                                       "Target length: 3 minutes\nSearch intent: a, b")
        self.assertEqual(Idea(title="T", genre="g", description="d").block(),
                         "Title: T\nGenre: g\nPremise: d")

    def test_the_block_with_a_paper_ends_in_it(self):
        self.assertTrue(paper_idea().block().endswith(f"\nPaper: {ARXIV}"))

    def test_the_block_names_a_local_paper_by_its_basename_only(self):
        # Every stage sends idea.block() to a model; a folder must not travel.
        for ref in ("/home/someone/papers/attention.pdf", "papers/attention.pdf",
                    "~/attention.pdf"):
            self.assertTrue(paper_idea(ref).block().endswith("\nPaper: file:attention.pdf"),
                            ref)
        for ref in (ARXIV, DOI, "1706.03762", "arXiv:1706.03762v5"):
            self.assertTrue(paper_idea(ref).block().endswith(f"\nPaper: {ref}"), ref)


# --------------------------------------------------------------------------- #
# references
# --------------------------------------------------------------------------- #

class Refs(unittest.TestCase):
    def test_paper_url_reads_a_doi_an_arxiv_id_a_url_and_a_local_path(self):
        self.assertEqual(research.paper_url(DOI), f"https://doi.org/{DOI}")
        self.assertEqual(research.paper_url(f"doi:{DOI}"), f"https://doi.org/{DOI}")
        self.assertEqual(research.paper_url("1706.03762"), PDF)
        self.assertEqual(research.paper_url("arXiv:1706.03762v5"), PDF + "v5")
        self.assertEqual(research.paper_url(ARXIV), PDF)
        self.assertEqual(research.paper_url("https://example.org/p.html"),
                         "https://example.org/p.html")
        self.assertEqual(research.paper_url("/home/me/papers/attention.pdf"),
                         "file:attention.pdf")
        self.assertEqual(research.paper_url("attention.pdf"), "file:attention.pdf")

    def test_same_paper_matches_the_abs_and_pdf_twins_with_or_without_a_version(self):
        for other in (PDF, PDF + "v2", ARXIV + "v5", "http://arxiv.org/pdf/1706.03762.pdf",
                      "https://arxiv.org/pdf/1706.03762v2/"):
            self.assertTrue(research.same_paper(ARXIV, other), other)
        self.assertFalse(research.same_paper(ARXIV, "https://arxiv.org/abs/1706.03763"))
        self.assertFalse(research.same_paper(PDF, "https://example.org/cost"))
        self.assertFalse(research.same_paper("", ""))
        self.assertTrue(research.same_paper(f"https://doi.org/{DOI}",
                                            f"https://dx.doi.org/{DOI}"))
        self.assertTrue(research.same_paper("file:a.pdf", "file:a.pdf"))

    def test_claim_key_is_the_research_blocks_normalisation(self):
        from ytstudio import script
        self.assertEqual(research.claim_key("  The  Bolt\nSheared "), "the bolt sheared")
        self.assertEqual(len(research.claim_key("x" * 300)), 120)
        self.assertIs(script.claim_key, research.claim_key)


# --------------------------------------------------------------------------- #
# the scope
# --------------------------------------------------------------------------- #

class Scope(unittest.TestCase):
    def test_a_paper_idea_gets_the_paper_scope_whatever_its_genre(self):
        for genre in ("explainer", "cinematic"):
            self.assertEqual(research.scope_for(paper_idea(genre=genre))["name"], "paper")
        self.assertEqual(research.scope_for(Idea("T", "explainer", "d"))["name"], "explainer")
        self.assertEqual(research.scope_for(Idea("T", "cinematic", "d"))["name"], "cinematic")

    def test_the_paper_plan_asks_the_questions_a_paper_film_needs(self):
        text = research.SCOPES["paper"]["plan"].lower()
        for words in ("costs in the world", "state of the art before", "received",
                      "consequences", "limitations", "refute"):
            self.assertIn(words, text)
        self.assertIn("{depth}", research.SCOPES["paper"]["plan"])

    def test_the_explainer_plan_asks_one_documented_significance_question(self):
        text = research.SCOPES["explainer"]["plan"]
        self.assertIn("what the subject costs or changes in the world and for whom", text)
        self.assertIn("is not a vague framing question", text)

    def test_the_paper_gaps_prompt_is_the_explainers(self):
        self.assertEqual(research.SCOPES["paper"]["gaps"], research.SCOPES["explainer"]["gaps"])
        self.assertTrue(research.SCOPES["paper"]["spoken"])
        self.assertEqual(research.SCOPES["paper"]["depth"], research.DEFAULT_DEPTH)

    def test_the_paper_claims_and_outline_prompts_name_the_papers_url(self):
        scope = research.scope_for(paper_idea())
        self.assertIn(f"({PDF})", scope["claims"])
        self.assertTrue(scope["claims"].startswith(research.SCOPES["explainer"]["claims"]))
        self.assertIn("before the paper's mechanism", scope["outline"])
        self.assertNotIn("{", scope["claims"] + scope["outline"])


# --------------------------------------------------------------------------- #
# reading the paper
# --------------------------------------------------------------------------- #

class ReadPaper(_Case):
    def test_an_arxiv_abs_ref_read_as_its_pdf_is_an_entry_flagged_paper(self):
        entry = self.researcher(paper_idea()).read_paper()
        self.assertEqual(entry["question"], f"The paper itself: {ARXIV}")
        self.assertTrue(entry["paper"])
        self.assertEqual(entry["visited"], [PDF])
        self.assertEqual((entry["searches"], entry["denied"]), (0, []))
        self.assertEqual(entry["notes"], "PAPER NOTES")
        self.assertEqual(FakeEngine.calls[0]["tools"], ("WebFetch",))
        self.assertIn(PDF, FakeEngine.calls[0]["prompt"])
        self.assertIn("00-paper", self.raw())

    def test_a_doi_is_matched_by_a_fetch_of_its_doi_org_url(self):
        FakeEngine.script = staticmethod(
            lambda p: [fetch(f"https://doi.org/{DOI}"), said("n"), done()])
        entry = self.researcher(paper_idea(DOI)).read_paper()
        self.assertEqual(entry["visited"], [f"https://doi.org/{DOI}"])

    def test_a_read_that_never_opens_the_paper_fails_naming_it(self):
        FakeEngine.script = staticmethod(
            lambda p: [fetch("https://example.org/about"), said("from memory"), done()])
        with self.assertRaises(ClaudeError) as caught:
            self.researcher(paper_idea()).read_paper()
        self.assertIn(ARXIV, str(caught.exception))
        self.assertNotIn("00-paper", self.raw())

    def test_a_denied_tool_fails_the_read(self):
        FakeEngine.script = staticmethod(lambda p: [
            fetch(PDF), said("n"),
            done([{"tool_name": "WebFetch", "tool_input": {"url": PDF}}])])
        with self.assertRaises(ClaudeError) as caught:
            self.researcher(paper_idea()).read_paper()
        self.assertIn(ARXIV, str(caught.exception))
        self.assertIn("denied", str(caught.exception))

    def test_a_local_pdf_is_read_with_read_in_its_directory_and_named_by_its_basename(self):
        folder = Path(self._tmp.name) / "papers"
        folder.mkdir()
        pdf = folder / "attention.pdf"
        pdf.write_bytes(b"%PDF-1.4 fixture")
        ref = str(folder / "sub" / ".." / "attention.pdf")
        FakeEngine.script = staticmethod(lambda p: [
            read(pdf), said(f"Read {pdf} in {folder}: it drops recurrence."), done()])
        entry = self.researcher(paper_idea(ref)).read_paper()
        self.assertEqual(FakeEngine.calls[0]["tools"], ("Read",))
        self.assertEqual(Path(FakeEngine.calls[0]["cwd"]).resolve(), folder.resolve())
        self.assertNotIn(str(folder), FakeEngine.calls[0]["prompt"])
        self.assertEqual(entry["visited"], ["file:attention.pdf"])
        self.assertEqual(entry["question"], "The paper itself: file:attention.pdf")
        self.assertNotIn(str(folder), json.dumps(entry))
        self.assertIn("it drops recurrence", entry["notes"])

    def test_a_local_read_of_another_file_fails(self):
        folder = Path(self._tmp.name) / "papers"
        folder.mkdir()
        (folder / "attention.pdf").write_bytes(b"%PDF")
        FakeEngine.script = staticmethod(
            lambda p: [read(folder / "other.pdf"), said("n"), done()])
        with self.assertRaises(ClaudeError) as caught:
            self.researcher(paper_idea(str(folder / "attention.pdf"))).read_paper()
        self.assertIn("attention.pdf", str(caught.exception))

    def test_a_missing_local_file_fails_before_any_engine_call(self):
        with self.assertRaises(ClaudeError):
            self.researcher(paper_idea(str(Path(self._tmp.name) / "none.pdf"))).read_paper()
        self.assertEqual(FakeEngine.calls, [])

    def test_under_codex_the_paper_is_refused_before_any_engine_call(self):
        with mock.patch.object(harness, "ENGINE_NAME", "codex"):
            with self.assertRaises(ClaudeError) as caught:
                self.researcher(paper_idea()).read_paper()
        self.assertIn("--paper needs the claude engine: codex reports no WebFetch/Read "
                      "events, so the paper read cannot be evidenced", str(caught.exception))
        self.assertEqual(FakeEngine.calls, [])

    def test_a_cached_read_of_another_paper_is_read_again(self):
        self.researcher(paper_idea()).read_paper()
        FakeEngine.script = staticmethod(
            lambda p: [fetch(f"https://doi.org/{DOI}"), said("n"), done()])
        entry = self.researcher(paper_idea(DOI)).read_paper()
        self.assertEqual(entry["question"], f"The paper itself: {DOI}")
        self.assertEqual(len(FakeEngine.calls), 2)


# --------------------------------------------------------------------------- #
# where the entry goes
# --------------------------------------------------------------------------- #

QUESTION = {"premise": "p", "questions": [{"question": "What did it cost before?",
                                           "why": "w", "searches": ["s"]}]}


def paper_answers(**more):
    return {"01-questions": QUESTION, "03-claims": {"claims": CLAIMS},
            "06b-paper": DIGEST, **more}


class Ordering(_Case):
    def setUp(self):
        super().setUp()
        FakeEngine.script = staticmethod(lambda prompt: (
            [fetch(PDF), said("PAPER NOTES"), done()] if "The paper itself" in prompt
            or "Read this paper" in prompt
            else [fetch("https://example.org/cost"), said("QUESTION NOTES"), done()]))

    def test_a_failing_read_propagates_before_the_questions(self):
        FakeEngine.script = staticmethod(lambda p: [said("from memory"), done()])
        with self.assertRaises(ClaudeError):
            self.researcher(paper_idea(), paper_answers()).run()
        self.assertNotIn("01-questions", self.harness.prompts)

    def test_the_paper_entry_is_first_and_reaches_the_questions_and_claims(self):
        data = self.researcher(paper_idea(), paper_answers()).run()
        self.assertTrue(data["notes"][0]["paper"])
        self.assertEqual(data["notes"][1]["notes"], "QUESTION NOTES")
        self.assertIn("PAPER NOTES", self.harness.prompts["01-questions"])
        self.assertIn("PAPER NOTES", self.harness.prompts["03-claims"])
        self.assertIn(PDF, data["urls_opened"])

    def test_the_explainer_questions_prompt_carries_no_paper(self):
        self.researcher(Idea("Comets", "explainer", "d"), paper_answers()).run()
        self.assertNotIn("PAPER NOTES", self.harness.prompts["01-questions"])
        self.assertNotIn("00-paper", self.raw())

    def test_a_research_cached_without_the_paper_is_made_again_with_the_paper_first(self):
        # The cached notes answered questions planned without the paper: kept,
        # the paper's questions (reception, limits) would never be asked.
        r = self.researcher(paper_idea(), paper_answers())
        r.dossier.save("02-research", [{"question": "old q", "notes": "OLD NOTES",
                                        "visited": ["https://example.org/old"],
                                        "searches": 1, "denied": []}])
        data = r.run()
        self.assertTrue(data["notes"][0]["paper"])
        self.assertEqual(data["notes"][1]["notes"], "QUESTION NOTES")
        self.assertIn("PAPER NOTES", self.harness.prompts["03-claims"])
        self.assertNotIn("OLD NOTES", self.harness.prompts["03-claims"])
        self.assertNotIn("old q", [e["question"] for e in data["notes"]])

    def test_the_same_paper_again_replays_every_stage(self):
        self.researcher(paper_idea(), paper_answers()).run()
        FakeEngine.calls = []
        data = self.researcher(paper_idea(), paper_answers()).run()
        self.assertEqual(self.harness.prompts, {})
        self.assertEqual(FakeEngine.calls, [])
        self.assertEqual(data["paper"], DIGEST)

    def test_another_paper_on_the_same_project_is_researched_again(self):
        other = "https://arxiv.org/abs/2005.14165"

        def swap(obj):
            return json.loads(json.dumps(obj).replace("1706.03762", "2005.14165")
                              .replace("Attention Is All You Need", "Few-Shot Learners"))
        FakeEngine.script = staticmethod(lambda prompt: (
            [fetch(swap(PDF)), said("PAPER B NOTES"), done()] if "2005.14165" in prompt
            and ("The paper itself" in prompt or "Read this paper" in prompt)
            else [fetch(PDF), said("PAPER NOTES"), done()] if "The paper itself" in prompt
            or "Read this paper" in prompt
            else [fetch("https://example.org/cost"), said("QUESTION NOTES"), done()]))
        self.researcher(paper_idea(), paper_answers()).run()
        data = self.researcher(paper_idea(other), swap(paper_answers())).run()
        for name in ("01-questions", "03-claims", "06b-paper", "07-outline"):
            self.assertIn(name, self.harness.prompts)
        self.assertIn("PAPER B NOTES", self.harness.prompts["03-claims"])
        self.assertNotIn("PAPER NOTES\n", self.harness.prompts["03-claims"])
        self.assertEqual(data["paper"]["citation"]["title"], "Few-Shot Learners")
        self.assertEqual(data["notes"][0]["notes"], "PAPER B NOTES")

    def test_an_explainer_dossier_given_a_paper_is_researched_again(self):
        self.researcher(Idea("Attention", "explainer", "d"), paper_answers()).run()
        data = self.researcher(paper_idea(), paper_answers()).run()
        self.assertIn("the problem the paper addresses", self.harness.prompts["01-questions"])
        self.assertIn("PAPER NOTES", self.harness.prompts["03-claims"])
        self.assertIn("06b-paper", self.harness.prompts)
        self.assertEqual(data["paper"], DIGEST)

    def test_the_paper_run_leaves_its_stages_and_returns_the_digest(self):
        data = self.researcher(paper_idea(), paper_answers()).run()
        self.assertEqual(self.raw(), sorted(RAW + ["00-paper", "06b-paper"]))
        self.assertEqual(data["scope"], "paper")
        self.assertEqual(data["paper"], DIGEST)
        self.assertIn(DIGEST["problem"]["text"], self.harness.prompts["07-outline"])
        self.assertIn(PDF, self.harness.prompts["04-sources"])
        self.assertIn(PDF, self.harness.prompts["03-claims"])
        self.assertEqual(data["warnings"], [])

    def test_the_digest_is_decided_beside_the_gap_audit(self):
        self.researcher(paper_idea(), paper_answers()).run()
        names = list(self.harness.prompts)
        self.assertLess(names.index("03-claims"), names.index("06b-paper"))
        self.assertLess(names.index("06b-paper"), names.index("07-outline"))
        self.assertIn(CLAIMS[0]["claim"], self.harness.prompts["06b-paper"])

    def test_an_explainer_run_leaves_todays_stages_and_no_paper(self):
        data = self.researcher(Idea("Comets", "explainer", "d"), paper_answers()).run()
        self.assertEqual(self.raw(), RAW)
        self.assertNotIn("paper", data)
        self.assertNotIn("06b-paper", self.harness.prompts)

    def test_a_local_paper_leaves_no_path_in_the_dossier_and_says_it_has_no_url(self):
        folder = Path(self._tmp.name) / "papers"
        folder.mkdir()
        pdf = folder / "attention.pdf"
        pdf.write_bytes(b"%PDF")
        FakeEngine.script = staticmethod(lambda prompt: (
            [read(pdf), said(f"PAPER NOTES from {pdf}"), done()]
            if "attention.pdf" in prompt and "Read the file" in prompt
            else [fetch("https://example.org/cost"), said("QUESTION NOTES"), done()]))
        local = [dict(c, source_url="file:attention.pdf") if c["source_url"].startswith(
            "https://arxiv") else c for c in CLAIMS]
        r = self.researcher(paper_idea(str(pdf)),
                            paper_answers(**{"03-claims": {"claims": local}}))
        data = r.run()
        written = research.write_dossier(r.dossier, data, [])
        self.assertIn("the paper: no public URL; the primary source is file:attention.pdf",
                      data["warnings"])
        self.assertFalse([w for w in data["warnings"] if "memory" in w], data["warnings"])
        self.assertEqual(data["idea"]["paper"], "file:attention.pdf")
        for path in written:
            if path.suffix in (".json", ".md", ".csv"):
                self.assertNotIn(str(folder), path.read_text(encoding="utf-8"), path.name)
        for name, prompt in self.harness.prompts.items():
            self.assertNotIn(str(folder), prompt, name)


# --------------------------------------------------------------------------- #
# provenance
# --------------------------------------------------------------------------- #

class Provenance(unittest.TestCase):
    def paper(self, **kw):
        return {"question": "The paper itself: x", "notes": "n", "visited": [PDF],
                "searches": 0, "denied": [], "paper": True, **kw}

    def test_the_paper_entry_shifts_no_question_number(self):
        notes = [self.paper(), {"question": "q", "notes": "n", "visited": [], "searches": 0}]
        self.assertEqual(research.provenance_warnings(notes),
                         ["question 1: no search or fetch happened; its notes may be "
                          "written from memory"])

    def test_a_url_paper_that_was_opened_adds_nothing(self):
        self.assertEqual(research.provenance_warnings([self.paper()]), [])

    def test_a_local_paper_says_it_has_no_public_url(self):
        out = research.provenance_warnings([self.paper(visited=["file:a.pdf"])])
        self.assertEqual(out, ["the paper: no public URL; the primary source is file:a.pdf"])

    def test_doctor_numbers_a_blind_question_without_the_paper_entry(self):
        from ytstudio import doctor, workspace
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "idea.json").write_text(json.dumps({"title": "T", "paper": ARXIV}))
            (root / "dossier.json").write_text(json.dumps({
                "claims": CLAIMS, "urls_opened": [PDF], "warnings": [],
                "notes": [self.paper(), {"question": "q", "notes": "n", "visited": [],
                                         "searches": 2}]}))
            found = [f.message for f in doctor.check_project(workspace.Project(root)).findings
                     if f.area == "research" and "opened no page" in f.message]
        self.assertEqual(found, ["question(s) 1 opened no page; those facts rest on "
                                 "search snippets"])

    def test_a_denied_paper_read_is_named_as_the_papers(self):
        out = research.provenance_warnings(
            [self.paper(denied=[{"tool": "WebFetch", "wanted": PDF}])])
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("the paper: WebFetch denied"), out)


# --------------------------------------------------------------------------- #
# the digest
# --------------------------------------------------------------------------- #

class Problems(unittest.TestCase):
    def check(self, digest):
        return research.paper_problems(digest, CLAIMS, PDF)

    def test_a_clean_digest_has_no_problem(self):
        self.assertEqual(self.check(DIGEST), [])

    def test_each_fault_is_one_line(self):
        for key, value in (("problem", item("", CLAIMS[1]["claim"])),
                           ("implications", item("text")),
                           ("consequences", item("text", "A claim nobody made.")),
                           ("contribution", item("text", CLAIMS[1]["claim"]))):
            lines = self.check({**DIGEST, key: value})
            self.assertEqual(len(lines), 1, (key, lines))
            self.assertTrue(lines[0].startswith(key), lines)

    def test_a_quote_differing_only_in_case_and_whitespace_is_accepted(self):
        quote = "  the transformer DROPS\n recurrence   entirely. "
        self.assertEqual(self.check({**DIGEST, "contribution": item("t", quote)}), [])

    def test_a_significance_citing_only_the_paper_is_not_a_problem(self):
        self.assertEqual(self.check({**DIGEST, "significance": item("t", CLAIMS[0]["claim"])}),
                         [])


class Digest(_Case):
    def test_a_bad_first_answer_is_asked_again_with_its_problem(self):
        bad = {**DIGEST, "limitations": item("text")}
        r = self.researcher(paper_idea(), {"06b-paper": bad, "06b-paper-again": DIGEST})
        got = r.paper_digest({"claims": CLAIMS}, {"sources": []})
        self.assertEqual(got, DIGEST)
        self.assertIn("limitations: cites no claim", self.harness.prompts["06b-paper-again"])
        self.assertEqual(self.harness.forgotten, [])
        self.assertIn("06b-paper", self.raw())

    def test_bad_twice_fails_and_keeps_neither_answer(self):
        bad = {**DIGEST, "limitations": item("text")}
        r = self.researcher(paper_idea(), {"06b-paper": bad, "06b-paper-again": bad})
        with self.assertRaises(ClaudeError) as caught:
            r.paper_digest({"claims": CLAIMS}, {"sources": []})
        self.assertIn("limitations", str(caught.exception))
        self.assertEqual(self.harness.forgotten, ["06b-paper", "06b-paper-again"])
        self.assertNotIn("06b-paper", self.raw())

    def test_the_schema_requires_the_citation_and_every_item_with_its_claims(self):
        schema = research.PAPER_SCHEMA
        self.assertEqual(set(schema["required"]), {"citation", *research.PAPER_ITEMS})
        self.assertEqual(research.PAPER_ITEMS, ("problem", "contribution", "significance",
                                                "implications", "consequences",
                                                "limitations"))
        self.assertEqual(set(schema["properties"]["citation"]["required"]),
                         {"title", "authors", "year", "url"})
        for key in research.PAPER_ITEMS:
            self.assertEqual(set(schema["properties"][key]["required"]), {"text", "claims"})


class Writer(unittest.TestCase):
    def test_the_readme_has_a_section_for_the_paper(self):
        with tempfile.TemporaryDirectory() as tmp:
            dossier = research.Dossier(Path(tmp), paper_idea(), at=Path(tmp))
            data = {"premise": "p", "questions": [], "claims": [], "sources": [],
                    "assets": [], "gaps": [], "urls_opened": [], "notes": [],
                    "usage": {}, "scope": "paper", "paper": DIGEST}
            research.write_dossier(dossier, data, [])
            md = (Path(tmp) / "README.md").read_text(encoding="utf-8")
        section = md.split("## The paper", 1)[1].split("\n## ", 1)[0]
        self.assertIn("Attention Is All You Need", section)
        self.assertIn("Vaswani et al.", section)
        self.assertIn(PDF, section)
        for key in research.PAPER_ITEMS:
            self.assertIn(DIGEST[key]["text"], section)
        self.assertIn(CLAIMS[1]["claim"], section)


# --------------------------------------------------------------------------- #
# the commands
# --------------------------------------------------------------------------- #

class Command(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "film"

    def yt(self, *argv):
        args = cli.build_parser().parse_args(list(argv))
        with redirect_stderr(io.StringIO()) as err, redirect_stdout(io.StringIO()):
            code = args.run(args)
        self.err = err.getvalue()
        return code

    def test_research_paper_writes_the_paper_into_idea_json_before_the_research(self):
        seen = {}

        class Stop(research.Researcher):
            def run(self):
                seen["paper"] = self.idea.paper
                seen["idea.json"] = json.loads((self.dossier.dir / "idea.json").read_text())
                raise ClaudeError("stopped by the test")
        with mock.patch.object(research, "Researcher", Stop):
            code = self.yt("research", "--project", str(self.root), "--title", "Attention",
                           "--paper", ARXIV, "--quiet")
        self.assertEqual(code, 1)
        self.assertEqual(seen["paper"], ARXIV)
        self.assertEqual(seen["idea.json"]["paper"], ARXIV)

    def test_a_relative_local_paper_is_kept_by_its_absolute_path(self):
        # idea.json is local and may hold the path; kept as typed, a later
        # run from another folder would not find the file.
        seen = {}

        class Stop(research.Researcher):
            def run(self):
                seen["idea.json"] = json.loads((self.dossier.dir / "idea.json").read_text())
                raise ClaudeError("stopped by the test")
        folder = Path(self._tmp.name) / "papers"
        folder.mkdir()
        (folder / "paper.pdf").write_bytes(b"%PDF")
        elsewhere = Path(self._tmp.name) / "elsewhere"
        elsewhere.mkdir()
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        os.chdir(folder)
        with mock.patch.object(research, "Researcher", Stop):
            self.yt("research", "--project", str(self.root), "--title", "Attention",
                    "--paper", "paper.pdf", "--quiet")
        kept = seen["idea.json"]["paper"]
        self.assertEqual(Path(kept), (folder / "paper.pdf").resolve())
        os.chdir(elsewhere)
        self.assertTrue(research._local_paper(kept).resolve().is_file())

    def test_under_codex_research_refuses_the_paper_before_any_work(self):
        built = []
        with mock.patch.object(harness, "ENGINE_NAME", "codex"), \
                mock.patch.object(research, "Researcher", lambda *a, **k: built.append(1)):
            code = self.yt("research", "--project", str(self.root), "--title", "Attention",
                           "--paper", ARXIV, "--quiet")
        self.assertEqual(code, 2)
        self.assertEqual(built, [])
        self.assertIn("--paper needs the claude engine", self.err)

    def test_run_hands_the_paper_to_research(self):
        args = cli.build_parser().parse_args(["run", "--title", "T", "--paper", ARXIV])
        self.assertEqual(cli.run_extras(args, redo=set())["research"], ["--paper", ARXIV])
        args = cli.build_parser().parse_args(["run", "--title", "T"])
        self.assertEqual(cli.run_extras(args, redo=set())["research"], [])


from test_run_pipeline import RunCase as _RunCase  # noqa: E402


class RunPlants(_RunCase):
    def test_yt_run_paper_plants_the_paper_before_the_first_stage(self):
        seen = {}
        self.hooks["research"] = lambda: seen.update(
            json.loads((self.root / "idea.json").read_text()))
        self.run_yt("--paper", ARXIV, "--skip", "keyframe,vfx,sound,build,render")
        self.assertEqual(seen.get("paper"), ARXIV)
        self.assertEqual(self.seen["research"].paper, ARXIV)

    def test_a_paper_already_in_idea_json_reaches_research_and_the_estimate(self):
        (self.root / "idea.json").write_text(json.dumps(
            {"title": "Attention", "genre": "explainer", "description": "d",
             "paper": ARXIV}))
        self.run_yt("--dry-run")
        row = next(line for line in self.out.splitlines() if line.strip().startswith("research"))
        self.assertIn("--paper", row)
        self.run_yt("--skip", "keyframe,vfx,sound,build,render")
        self.assertEqual(self.seen["research"].paper, ARXIV)

    def test_under_codex_a_paper_already_in_idea_json_runs_nothing(self):
        (self.root / "idea.json").write_text(json.dumps(
            {"title": "Attention", "genre": "explainer", "description": "d",
             "paper": ARXIV}))
        with mock.patch.object(harness, "ENGINE_NAME", "codex"):
            code = self.run_yt()
        self.assertEqual(code, 2)
        self.assertEqual(self.order, [])
        self.assertIn("--paper needs the claude engine", self.err)

    def test_yt_run_keeps_a_relative_local_paper_by_its_absolute_path(self):
        folder = Path(self.tmp.name) / "papers"
        folder.mkdir()
        (folder / "paper.pdf").write_bytes(b"%PDF")
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        os.chdir(folder)
        self.run_yt("--paper", "paper.pdf", "--skip", "keyframe,vfx,sound,build,render")
        kept = json.loads((self.root / "idea.json").read_text())["paper"]
        self.assertEqual(Path(kept), (folder / "paper.pdf").resolve())

    def test_under_codex_yt_run_paper_runs_nothing(self):
        with mock.patch.object(harness, "ENGINE_NAME", "codex"):
            code = self.run_yt("--paper", ARXIV)
        self.assertEqual(code, 2)
        self.assertEqual(self.order, [])
        self.assertIn("--paper needs the claude engine", self.err)


class Estimate(unittest.TestCase):
    def test_research_paper_adds_the_paper_pass_and_research_alone_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "film"
            root.mkdir()
            plain = spend.stage_estimates(root, ["research"], extras={"research": []})[0]
            paper = spend.stage_estimates(root, ["research"],
                                          extras={"research": ["--paper", ARXIV]})[0]
        self.assertEqual(plain["usd"], spend.DEFAULT_USD["research"])
        self.assertEqual(paper["usd"],
                         round(spend.DEFAULT_USD["research"] + spend.PAPER_USD, 2))
        self.assertIn("--paper", paper["source"])

    def test_a_record_that_holds_the_digest_already_counts_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dossier.json").write_text(json.dumps(
                {"engine": {"decisions": [{"name": "06b-paper"}]}}))
            self.assertEqual(spend.flag_increments(root, "research", ["--paper", ARXIV]), [])
            (root / "dossier.json").write_text(json.dumps({"engine": {"decisions": []}}))
            self.assertEqual(spend.flag_increments(root, "research", ["--paper", ARXIV]),
                             [("--paper", round(spend.PAPER_USD, 2))])


# --------------------------------------------------------------------------- #
# without a paper, research asks what it asked
# --------------------------------------------------------------------------- #

def explainer_prompts(root: Path) -> dict[str, str]:
    """Every prompt an explainer research makes, the agent's included, by
    name: the questions, one researched question, the decisions."""
    idea = Idea(title="Comets", genre="explainer", description="What a tail is.",
                hook="A tail that points the wrong way", length="3 minutes",
                keywords=["comet tail"])
    calls = []

    class Agent(FakeEngine):
        def stream(self, prompt, system=None):
            calls.append(prompt)
            yield fetch("https://example.org/comet")
            yield said("notes")
            yield done()
    answers = {"01-questions": QUESTION,
               "03-claims": {"claims": CLAIMS[1:2]},
               "04-sources": {"sources": [{"title": "s", "url": "https://example.org/cost",
                                           "kind": "paper", "why_it_matters": "w"}]}}
    with mock.patch.object(yt_assets, "find_assets", side_effect=lambda *a, **k: []), \
            mock.patch.object(yt_assets, "cited_papers", side_effect=lambda *a, **k: []), \
            mock.patch.object(research, "ClaudeEngine", Agent):
        dossier = research.Dossier(root, idea, at=root)
        r = research.Researcher(idea, dossier, verbose=False, jobs=1)
        r.harness = PaperHarness(answers)
        r.run()
    return {**r.harness.prompts, "agent": calls[0]}


#: sha256 of each explainer prompt, taken on develop 57fd5a5 before AUTO-131.
#: The 01-questions plan text is the one prompt the card changes.
EXPLAINER_PROMPTS = {
    "agent": "40aecd0bec5f6b21ccf32481177e96e69260d254002fd877266ae30120b7d92e",
    "03-claims": "41475bb96d86fbcf4dfec4ab0ce4d3b024ff29e3e135ce5ec717c247d4fcd259",
    "04-sources": "d7ff65c3dcfc7e03124571646e8ae195e5a4914f3b74a73f721a24540d79dacb",
    "05-shots": "976cc3da462e8e565fa327d28ab84651b079c689c981cae74ed8c413f8d55b09",
    "06-gaps": "2d574b5c6ff5f40b2a635451dd2d18687e70eea8b47fdcad25e0e3c9b94a655e",
    "07-outline": "6467f044b75ccd12dd98a6f8f8d90177977ddd4071c0d12258a14ee946522121",
}


class Unchanged(unittest.TestCase):
    def test_an_explainer_researchs_prompts_are_todays(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompts = explainer_prompts(Path(tmp))
        got = {k: hashlib.sha256(v.encode()).hexdigest() for k, v in prompts.items()
               if k != "01-questions"}
        self.assertEqual(got, EXPLAINER_PROMPTS)
        idea = Idea(title="Comets", genre="explainer", description="What a tail is.",
                    hook="A tail that points the wrong way", length="3 minutes",
                    keywords=["comet tail"])
        depth = research.runtime_plan(idea, research.DEFAULT_DEPTH)["depth"]
        self.assertEqual(prompts["01-questions"], idea.block() + "\n\n"
                         + research.SCOPES["explainer"]["plan"].format(depth=depth))


if __name__ == "__main__":
    unittest.main()
