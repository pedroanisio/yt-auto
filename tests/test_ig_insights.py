"""igstudio.insights: the ranking behind `ig top`."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from igstudio import insights  # noqa: E402
from igstudio.graph import Graph  # noqa: E402
from tests.ig_fakes import FakeGraph  # noqa: E402


def row(mid, views, **more):
    return {"id": mid, "media_product_type": "REELS", "timestamp": "2026-10-20T10:00:00+0000",
            "caption": f"Hook {mid}", "permalink": f"https://x/{mid}/",
            "metrics": {"views": views, "reach": views // 2, "saved": 1, "shares": 0, **more}}


class Rank(unittest.TestCase):
    def test_ratio_against_the_median_and_winners(self):
        rows = [row("a", 100), row("b", 400), row("c", 1000), row("d", 120)]
        ranked = insights.rank(rows, metric="views")
        self.assertEqual([r["id"] for r in ranked], ["c", "b", "d", "a"])
        self.assertEqual(ranked[0]["median"], 260)
        self.assertAlmostEqual(ranked[0]["ratio"], 1000 / 260, places=3)
        self.assertEqual([r["id"] for r in ranked if r["winner"]], ["c", "b"])
        self.assertEqual(insights.rank([], metric="views"), [])
        one = insights.rank([row("a", 0)], metric="views")
        self.assertEqual(one[0]["ratio"], 0.0)
        self.assertFalse(one[0]["winner"])

    def test_top_reads_media_then_each_ones_insights(self):
        fake = FakeGraph({
            "GET 1/media": {"data": [row("a", 0), row("b", 0)]},
            "GET a/insights": {"data": [{"name": "views", "values": [{"value": 50}]},
                                        {"name": "reach", "values": [{"value": 40}]}]},
            "GET b/insights": {"data": [{"name": "views", "values": [{"value": 500}]}]},
        })
        ranked = insights.top(Graph("t", transport=fake, sleep=lambda s: None), "1", limit=10)
        self.assertEqual([r["id"] for r in ranked], ["b", "a"])
        self.assertEqual(ranked[0]["metrics"]["views"], 500)
        self.assertTrue(ranked[0]["winner"])
        self.assertEqual(ranked[1]["metrics"]["reach"], 40)


if __name__ == "__main__":
    unittest.main()
