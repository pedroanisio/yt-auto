"""A blocking vfx continuity break keeps the film off READY, when it can.

The continuity review is the one place the film's sections are watched
together, but until now its verdict reached only a doctor warn, so a film
whose hero changed size at a section boundary could still be READY TO
PUBLISH. qa.vfx_blockers lifts a break marked `blocking` into QA's blocking
list -- but only when vfx.json records that the reviewer is of another family
than the author (flags.own_reviewer, the rule every stage applies): a model's
verdict on its own film, or a verdict that does not say who gave it (every
vfx.json written before the record), never blocks. A major or minor break,
or `one_film: false` on its own, stays a doctor warn.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from ytstudio import qa  # noqa: E402

AUTHORED = [{"n": 1, "section": "Open"}, {"n": 2, "section": "The Pressure Builds"}]


def broken(owner="The Pressure Builds", severity="blocking", **reviewer):
    rec = {"model": "opus", "author": "sonnet", "engine": "claude"}
    rec.update(reviewer)
    return {"authored": AUTHORED, "continuity": {
        "one_film": False, "score": 3, "summary": "two films", "monotony": [],
        "breaks": [{"sections": ["Open", owner], "owner_section": owner,
                    "problem": "the hero is twice the size", "fix": "scale it down",
                    "severity": severity}],
        "reviewer": rec}}


class VfxBlockers(unittest.TestCase):
    def test_a_blocking_break_from_another_family_is_a_blocker(self):
        self.assertEqual(qa.vfx_blockers(broken()), [
            "vfx continuity: section 2 (The Pressure Builds): the hero is twice the size"])
        # a codex author read by a claude reviewer is another family too
        self.assertEqual(len(qa.vfx_blockers(broken(author="gpt-5", engine="codex"))), 1)
        # the authored list may be passed apart from the document
        doc = broken()
        doc.pop("authored")
        self.assertEqual(qa.vfx_blockers(doc, AUTHORED), [
            "vfx continuity: section 2 (The Pressure Builds): the hero is twice the size"])

    def test_unmatched_owner_section_is_named_raw(self):
        self.assertEqual(qa.vfx_blockers(broken(owner="Somewhere else")), [
            "vfx continuity: Somewhere else: the hero is twice the size"])

    def test_same_family_record_gives_no_blocker(self):
        self.assertEqual(qa.vfx_blockers(broken(author="opus", model="opus[1m]")), [])

    def test_unrecorded_reviewer_gives_no_blocker(self):
        old = broken()
        old["continuity"].pop("reviewer")
        self.assertEqual(qa.vfx_blockers(old), [])
        half = broken(author="")
        self.assertEqual(qa.vfx_blockers(half), [])

    def test_major_or_minor_or_one_film_false_alone_give_nothing(self):
        for severity in ("major", "minor"):
            with self.subTest(severity=severity):
                self.assertEqual(qa.vfx_blockers(broken(severity=severity)), [])
        alone = broken()
        alone["continuity"]["breaks"] = []
        self.assertEqual(qa.vfx_blockers(alone), [])

    def test_skipped_and_error_continuity_give_nothing(self):
        for key in ("skipped", "error"):
            with self.subTest(key=key):
                doc = broken()
                doc["continuity"][key] = "the review could not be made"
                self.assertEqual(qa.vfx_blockers(doc), [])
        missing = broken()
        missing.pop("continuity")
        self.assertEqual(qa.vfx_blockers(missing), [])

    def test_none_vfx_gives_nothing(self):
        self.assertEqual(qa.vfx_blockers(None), [])
        self.assertEqual(qa.vfx_blockers({}), [])

    def test_mechanical_blockers_is_unchanged(self):
        # the existing tests/test_qa.py case, and the signature it is called with
        self.assertEqual(qa.mechanical_blockers({"placeholders": [], "findings": []},
                                                gate=None), [])
        blockers = qa.mechanical_blockers({"placeholders": ["3.1"], "findings": []},
                                          gate={"exit": 1, "triage": {
                                              "blocking": [{"what": "loudness -16 LUFS"}]}})
        self.assertEqual(len(blockers), 2)
        self.assertFalse(any("vfx continuity" in b for b in blockers))


if __name__ == "__main__":
    unittest.main()
