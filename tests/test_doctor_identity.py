"""AUTO-146: `yt doctor` on the film's identity and its fallback palette.

A film in design.py's house fallback palette has nobody's look, so doctor
names the fix for its cause: no styles.json is `yt styles --rich`; a
styles.json whose palette did not parse needs hex codes, which a cached
choice would not add, so its fix asks again with --fresh. The severity is
AUTO-155's: a styles warning before the render, a delivery fail after.

A rich styles.json with no identity, or one whose swap test was weak, not
evidenced or never run, is a styles warning naming the command that judges
it again.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_delivery import engine_report  # noqa: E402
from ytstudio import doctor, workspace  # noqa: E402

NO_HEX = "the primary treatment's palette in styles.json names no hex code"
RICH_ROW = {"name": "Diagram", "fit": 8, "palette": "deep blues", "typography": "Lora",
            "motion": "slow", "sound": "low"}
IDENTITY = {"atmosphere": "a", "personality": "p", "motif": "m", "signature": "s",
            "belongs_because": "b"}


class Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "a-film"
        self.root.mkdir()
        self.clock = time.time() - 1000
        self.write("idea.json", {"title": "A film", "genre": "g", "description": "d"})

    def write(self, name, data):
        path = self.root / name
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(json.dumps(data), encoding="utf-8")
        self.clock += 10
        os.utime(path, (self.clock, self.clock))

    def built(self, source="fallback", why=None):
        design = {"treatment": "", "source": source, "roles": {}}
        if why:
            design["fallback"] = why
        self.write("build.json", {"scene": {"duration": 30.0}, "publishable": True,
                                  "findings": [], "layers": {}, "timeline": {"shots": 9},
                                  "audio_tracks": 4, "design": design})
        self.write("scene.xml", b"<scene/>")

    def rendered(self):
        self.write("render.json", engine_report())
        self.write("video.mp4", b"\x00" * 1024)

    def report(self):
        return doctor.check_project(workspace.Project(self.root))

    def about(self, words, level=None, area=None):
        return [f for f in self.report().findings if words in f.message
                and (level is None or f.level == level) and (area is None or f.area == area)]


class FallbackFix(Case):
    def test_no_styles_json_names_yt_styles_rich(self):
        self.built(why="no styles.json")
        rows = self.about("fallback palette", "warn", "styles")
        self.assertEqual(len(rows), 1, rows)
        self.assertIn(f"yt styles --project {self.root} --rich", rows[0].fix)
        self.assertNotIn("--fresh", rows[0].fix)
        self.rendered()
        fails = self.about("fallback palette", "fail", "delivery")
        self.assertEqual(len(fails), 1, fails)
        self.assertIn(f"yt styles --project {self.root} --rich", fails[0].fix)
        self.assertNotIn("--fresh", fails[0].fix)

    def test_a_palette_that_did_not_parse_names_the_hex_code_fix(self):
        self.write("styles.json", {"styles": [dict(RICH_ROW)], "identity": IDENTITY})
        self.built(why=NO_HEX)
        rows = self.about("fallback palette", "warn", "styles")
        self.assertEqual(len(rows), 1, rows)
        self.assertIn("add hex codes to the palette", rows[0].message)
        self.assertIn(f"yt styles --project {self.root} --rich --fresh", rows[0].fix)
        self.rendered()
        fails = self.about("fallback palette", "fail", "delivery")
        self.assertEqual(len(fails), 1, fails)
        self.assertIn("add hex codes to the palette", fails[0].message)
        self.assertIn("--rich --fresh", fails[0].fix)

    def test_a_styles_json_with_no_treatment_says_so_not_hex_codes(self):
        self.write("styles.json", {"styles": []})
        self.built(why="styles.json lists no treatment")
        rows = self.about("fallback palette", "warn", "styles")
        self.assertEqual(len(rows), 1, rows)
        self.assertNotIn("hex code", rows[0].message)
        self.assertIn("returned no treatment", rows[0].message)
        self.assertIn(f"yt styles --project {self.root} --rich --fresh", rows[0].fix)

    def test_an_older_build_json_is_told_apart_by_the_file(self):
        # written before build.json said why: styles.json on disk or not
        self.built()
        self.assertNotIn("--fresh", self.about("fallback palette", "warn")[0].fix)
        self.write("styles.json", {"styles": [{"name": "Diagram", "fit": 8}]})
        self.assertIn("--rich --fresh", self.about("fallback palette", "warn")[0].fix)

    def test_the_films_own_palette_gives_no_row(self):
        self.write("styles.json", {"styles": [dict(RICH_ROW, palette="#101010")],
                                   "identity": dict(IDENTITY, review={"verdict": "pass"})})
        self.built(source="styles.json")
        self.assertEqual(self.about("fallback palette"), [])
        self.rendered()
        self.assertEqual(self.about("fallback palette"), [])


class Identity(Case):
    def styles(self, **doc):
        self.write("styles.json", {"styles": [dict(RICH_ROW)], **doc})

    def identity_rows(self):
        return [f for f in self.report().findings
                if f.area == "styles" and "identity" in f.message]

    def test_a_rich_styles_json_without_an_identity_warns(self):
        self.styles()
        rows = self.identity_rows()
        self.assertEqual([r.level for r in rows], ["warn"], rows)
        self.assertIn("--rich", rows[0].fix)

    def test_a_plain_styles_json_does_not(self):
        self.write("styles.json", {"styles": [{"name": "Diagram", "fit": 8}]})
        self.assertEqual(self.identity_rows(), [])

    def critique(self, review):
        self.styles(identity=dict(IDENTITY, review=review))
        rows = self.identity_rows()
        self.assertEqual([r.level for r in rows], ["warn"], (review, rows))
        self.assertIn(review["verdict"], rows[0].message)
        return rows[0]

    def test_a_weak_review_names_the_critique_asked_afresh(self):
        # the same inputs would serve the cached choice and judgements, and
        # give the same weak verdict back
        row = self.critique({"verdict": "weak", "objection": "any bridge", "scores": {}})
        self.assertIn(f"yt styles --project {self.root} --rich --deliberate critique --fresh",
                      row.fix)

    def test_an_unevidenced_review_names_the_critique_without_fresh(self):
        # a reviewer failure is never cached, so the plain command asks again
        row = self.critique({"verdict": "not_evidenced", "error": "the reviewer is down"})
        self.assertIn(f"yt styles --project {self.root} --rich --deliberate critique",
                      row.fix)
        self.assertNotIn("--fresh", row.fix)

    def test_an_identity_no_critique_was_asked_for_is_a_note(self):
        # `yt run --quick` gives styles --rich alone: the operator chose no review
        for doc in ({}, {"deliberation": {"stages": ["pool"]}}):
            self.styles(identity=dict(IDENTITY), **doc)
            rows = self.identity_rows()
            self.assertEqual([r.level for r in rows], ["note"], (doc, rows))
            self.assertIn("--deliberate critique", rows[0].fix)

    def test_an_identity_left_unjudged_under_critique_warns(self):
        self.styles(identity=dict(IDENTITY), deliberation={"stages": ["critique"]})
        rows = self.identity_rows()
        self.assertEqual([r.level for r in rows], ["warn"], rows)
        self.assertIn("--deliberate critique", rows[0].fix)

    def test_a_passing_review_is_quiet(self):
        self.styles(identity=dict(IDENTITY, review={"verdict": "pass", "scores": {}}))
        self.assertEqual(self.identity_rows(), [])


if __name__ == "__main__":
    unittest.main()
