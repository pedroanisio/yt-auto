"""`yt doctor` reads the independent cross-check (AUTO-134).

A claim the independent sources contradict is a research warn naming
`yt script --verify`, which must flag or clear it; entries the check could
not verify are counted in one warn. A spoken dossier with claims and no
cross-check at all gets one note, never more: --quick runs and older
projects gain a note, not a warn. A wordless film that skipped it, and
says so, gets nothing.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import doctor, research, workspace  # noqa: E402

OPENED = ["https://example.org/a"]
CLAIM = {"claim": "The tail is 100 million km long.", "detail": "", "source_title": "NASA",
         "source_url": "https://nasa.gov/tail", "source_kind": "primary", "confidence": "high"}
NOTE = "key claims were not cross-checked independently"


def entry(status, **kw):
    e = {"claim": CLAIM["claim"], "source_url": CLAIM["source_url"], "status": status,
         "sources": [], "correction": "", "note": "", "visited": OPENED, "searches": 1,
         "denied": [], "problems": []}
    e.update(kw)
    return e


def report(**dossier):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "idea.json").write_text(json.dumps({"title": "T"}))
        base = {"scope": "explainer", "claims": [CLAIM], "urls_opened": OPENED,
                "warnings": [], "notes": [{"question": "q1", "notes": "n",
                                           "visited": OPENED, "searches": 1}]}
        base.update(dossier)
        (root / "dossier.json").write_text(json.dumps(base))
        return doctor.check_project(workspace.Project(root))


def research_rows(rep):
    return [f for f in rep.findings if f.area == "research"]


class Contradicted(unittest.TestCase):
    def test_each_contradicted_claim_is_a_warn_naming_the_fact_check(self):
        rows = research_rows(report(crosscheck=[entry(
            "contradicted", correction="about 90 million km",
            sources=[{"url": "https://other.org/x", "says": "90", "agrees": "no"}])]))
        warns = [f for f in rows if f.level == "warn" and "contradict" in f.message]
        self.assertEqual(len(warns), 1, rows)
        self.assertIn(CLAIM["claim"], warns[0].message)
        self.assertIn("about 90 million km", warns[0].message)
        self.assertIn("yt script", warns[0].fix)
        self.assertTrue(warns[0].fix.endswith("--verify"), warns[0].fix)

    def test_unverified_entries_are_one_counted_warn(self):
        rows = research_rows(report(crosscheck=[entry("unverified", problems=["x"]),
                                                entry("unverified", problems=["y"]),
                                                entry("corroborated")]))
        warns = [f for f in rows if f.level == "warn" and "unverified" in f.message]
        self.assertEqual(len(warns), 1, rows)
        self.assertIn("2 ", warns[0].message)

    def test_a_clean_check_adds_nothing(self):
        rows = research_rows(report(crosscheck=[entry("corroborated"), entry("not_found")]))
        self.assertFalse([f for f in rows if "cross-check" in f.message
                          or "contradict" in f.message or NOTE in f.message], rows)


class NotChecked(unittest.TestCase):
    def notes(self, **dossier):
        return [f for f in research_rows(report(**dossier)) if NOTE in f.message]

    def test_a_spoken_dossier_with_no_check_is_noted_once(self):
        found = self.notes()
        self.assertEqual([f.level for f in found], ["note"])
        self.assertIn("--crosscheck", f"{found[0].message} {found[0].fix}")

    def test_a_skipped_check_a_wordless_film_or_no_claims_get_none(self):
        self.assertEqual(self.notes(crosscheck_skipped="cinematic scope"), [])
        self.assertEqual(self.notes(scope="cinematic"), [])
        self.assertEqual(self.notes(claims=[]), [])
        self.assertEqual(self.notes(crosscheck=[]), [])


class Provenance(unittest.TestCase):
    def test_a_blind_cross_check_is_warned_and_hides_no_blind_question(self):
        warning = f"{research.CROSSCHECK_WARNING} 1: opened no page, so it corroborates nothing"
        rows = research_rows(report(
            warnings=[warning], crosscheck=[entry("not_found", visited=[])],
            notes=[{"question": "q1", "notes": "n", "visited": [], "searches": 2}]))
        self.assertIn(warning, [f.message for f in rows if f.level == "warn"])
        self.assertTrue(any("question(s) 1 opened no page" in f.message for f in rows), rows)


if __name__ == "__main__":
    unittest.main()
