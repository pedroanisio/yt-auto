"""What the screenplay's writer and fact-check are shown of the research.

A dossier can hold far more claims than a prompt should carry, so the script
sees a ranked selection. A run once ranked only by confidence: a claim the
outline was built on fell outside the cut, and the fact-check then called
the line resting on it unsourced -- a false blocker. The outline's own claims
always go in.
"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import script  # noqa: E402


def claim(text, confidence="high"):
    return {"claim": text, "detail": "", "source_title": "s",
            "source_url": f"https://h.example/{text.split()[-1]}",
            "source_kind": "primary", "confidence": confidence}


class ResearchBlock(unittest.TestCase):
    def block(self, data, limit):
        with contextlib.redirect_stderr(io.StringIO()):
            return script.research_block(data, limit=limit)

    def test_the_outlines_claims_are_never_dropped(self):
        claims = [claim(f"high claim number {n}") for n in range(10)]
        claims.append(claim("the outline needs this low one", "low"))
        data = {"claims": claims,
                "outline": {"sections": [{"claims_used": [
                    "The outline needs   this low one"]}]}}
        text = self.block(data, limit=3)
        self.assertIn("the outline needs this low one", text)
        for n in range(3):
            self.assertIn(f"high claim number {n}", text)
        self.assertNotIn("high claim number 3", text)

    def test_without_an_outline_the_best_sourced_are_kept(self):
        data = {"claims": [claim("weak one", "low"), claim("strong one", "high")]}
        text = self.block(data, limit=1)
        self.assertIn("strong one", text)
        self.assertNotIn("weak one", text)


if __name__ == "__main__":
    unittest.main()
