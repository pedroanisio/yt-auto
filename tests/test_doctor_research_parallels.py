"""`yt doctor` reads a dossier's historical parallels (AUTO-151).

A parallel the research could not source is a research warn, never a
failure, and it is no provenance trouble about the claims: it must not hide
the blind-question check. An explainer whose outline named no parallel and
gave no reason gets one note; a reason, a wordless film or a dossier written
before the field gets nothing, since no parallel is better than an invented
one.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import doctor, research, workspace  # noqa: E402

OPENED = ["https://example.org/tacoma"]
NOTE = "no historical parallel and no reason given"


def row(**kw):
    p = {"event": "Tacoma Narrows bridge collapse", "year": "1940", "how_it_maps": "m",
         "where_it_breaks": "b", "source_url": "https://example.org/tacoma"}
    p.update(kw)
    return p


def report(**dossier):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "idea.json").write_text(json.dumps({"title": "T"}))
        base = {"scope": "explainer", "claims": [], "urls_opened": OPENED, "warnings": [],
                "notes": [{"question": "q1", "notes": "n", "visited": OPENED,
                           "searches": 1}]}
        base.update(dossier)
        (root / "dossier.json").write_text(json.dumps(base))
        return doctor.check_project(workspace.Project(root))


def research_rows(rep):
    return [f for f in rep.findings if f.area == "research"]


class Note(unittest.TestCase):
    def notes(self, **dossier):
        return [f for f in research_rows(report(**dossier)) if f.message == NOTE]

    def test_an_explainer_with_none_and_no_reason_is_noted_once(self):
        found = self.notes(parallels=[], parallels_why="")
        self.assertEqual([f.level for f in found], ["note"])
        self.assertIn("--fresh", found[0].fix)

    def test_a_reason_is_an_answer(self):
        self.assertEqual(self.notes(parallels=[], parallels_why="no precedent documented"),
                         [])

    def test_a_kept_parallel_is_no_note(self):
        self.assertEqual(self.notes(parallels=[row()], parallels_why=""), [])

    def test_a_paper_film_is_asked_too(self):
        # a paper film is spoken and gets the explainer's outline text
        found = self.notes(scope="paper", parallels=[], parallels_why="")
        self.assertEqual([f.level for f in found], ["note"])

    def test_a_wordless_film_is_never_asked(self):
        self.assertEqual(self.notes(scope="cinematic", parallels=[], parallels_why=""), [])

    def test_an_older_dossier_is_never_asked(self):
        self.assertEqual(self.notes(), [])


class Metrics(unittest.TestCase):
    def test_the_research_metrics_count_the_parallels(self):
        rep = report(parallels=[row(), row(event="Quebec bridge")])
        self.assertEqual(rep.metrics["research"]["parallels"], 2)
        self.assertEqual(report().metrics["research"]["parallels"], 0)


class Dropped(unittest.TestCase):
    def dropped(self):
        return research.parallels_of({"parallels": [
            row(event="A blog's bridge", source_url="https://example.org/blog")]}, OPENED)[1]

    def test_a_dropped_parallel_is_a_research_warn(self):
        warnings = self.dropped()
        found = [f for f in research_rows(report(warnings=warnings, parallels=[]))
                 if f.message.startswith(research.PARALLEL_DROPPED)]
        self.assertEqual([(f.level, f.message) for f in found],
                         [("warn", warnings[0])])
        # no fresh research is asked for: it would pay for a whole re-research
        # and source the row no better
        self.assertEqual(found[0].fix, "")

    def test_it_leaves_the_blind_question_warning_standing(self):
        notes = [{"question": "q1", "notes": "n", "visited": OPENED, "searches": 1},
                 {"question": "q2", "notes": "n", "visited": [], "searches": 2}]
        found = [f.message for f in research_rows(report(warnings=self.dropped(),
                                                         notes=notes))
                 if "opened no page" in f.message]
        self.assertEqual(found, ["question(s) 2 opened no page; those facts rest on "
                                 "search snippets"])


if __name__ == "__main__":
    unittest.main()
