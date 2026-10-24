"""Press headlines the film may stage, read from the research's news records
(AUTO-149, part B).

`assets.search_news` stores Google News and Wikinews hits as `article`
records: a title with "(outlet)" on its end, cut at 160 characters, the
outlet in `source`, the date in `credit_line` or `note`. These tests make
the records with `search_news` itself, its HTTP helpers answered by fixture
text, so the parser reads exactly the shapes the dossier holds. No network.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import assets  # noqa: E402

RSS_ITEM = ("<item><title>{headline} - {outlet}</title><link>{link}</link>"
            "<pubDate>Sun, 03 Mar 2024 08:00:00 GMT</pubDate>"
            "<source url=\"https://{outlet}.example\">{outlet}</source></item>")


def news(google=(), wikinews=()):
    """The records search_news makes from these Google headlines (headline,
    outlet) and Wikinews titles, as a dossier stores them."""
    feed = "<rss><channel>" + "".join(
        RSS_ITEM.format(headline=h, outlet=o, link=f"https://news.example/{n}")
        for n, (h, o) in enumerate(google)) + "</channel></rss>"
    wiki = {"query": {"search": [{"title": t, "timestamp": "2023-11-05T10:00:00Z"}
                                 for t in wikinews]}}
    with mock.patch.object(assets, "_get_json", lambda *a, **k: wiki), \
            mock.patch.object(assets, "_get_text", lambda *a, **k: feed):
        hits = assets.search_news("bridge", 40, "ua")
    return [h.record() for h in hits if h.url]


class PressReference(unittest.TestCase):
    def test_a_google_record(self):
        [rec] = news(google=[("Genoa bridge inquiry names 59 defendants", "Reuters")])
        self.assertEqual(assets.press_reference(rec), {
            "headline": "Genoa bridge inquiry names 59 defendants", "outlet": "Reuters",
            "date": "03 Mar 2024", "url": "https://news.example/0", "truncated": False})

    def test_a_wikinews_record(self):
        [rec] = news(wikinews=["Bridge collapses in Genoa, Italy"])
        ref = assets.press_reference(rec)
        self.assertEqual(ref["headline"], "Bridge collapses in Genoa, Italy")
        self.assertEqual(ref["outlet"], "Wikinews")
        self.assertEqual(ref["date"], "2023-11-05")
        self.assertTrue(ref["url"].startswith("https://en.wikinews.org/wiki/"))
        self.assertFalse(ref["truncated"])

    def test_a_headline_ending_in_parentheses_keeps_them(self):
        [rec] = news(google=[("The bridge that fell (2019)", "Reuters")])
        self.assertEqual(rec["title"], "The bridge that fell (2019) (Reuters)")
        ref = assets.press_reference(rec)
        self.assertEqual(ref["headline"], "The bridge that fell (2019)")
        self.assertFalse(ref["truncated"])

    def test_a_title_cut_at_160_is_truncated_and_never_listed(self):
        long = "Engineers warned " + "for years " * 20 + "about the stays"
        [rec] = news(google=[(long, "Reuters")])
        self.assertEqual(len(rec["title"]), 160)
        self.assertTrue(assets.press_reference(rec)["truncated"])
        self.assertEqual(assets.press_items([rec]), [])

    def test_a_record_with_no_outlet_is_truncated(self):
        rec = {"kind": "article", "title": "A headline", "source": "news/press",
               "url": "u", "credit_line": "", "note": ""}
        self.assertTrue(assets.press_reference(rec)["truncated"])

    def test_a_record_with_no_date_is_never_listed(self):
        # the card's source line is "outlet, date": an undated one would be
        # attributed by outlet alone and nothing would say so
        [rec] = news(google=[("Genoa bridge inquiry names 59 defendants", "Reuters")])
        for undated in (dict(rec, credit_line=""), dict(rec, credit_line="Reuters"),
                        dict(rec, credit_line="AP, 03 Mar 2024")):
            self.assertEqual(assets.press_reference(undated)["date"], "")
            self.assertEqual(assets.press_items([undated]), [])
        [wiki] = news(wikinews=["Bridge collapses in Genoa, Italy"])
        self.assertEqual(assets.press_items([dict(wiki, note="")]), [])
        # the next dated article is still N1
        self.assertEqual([p["id"] for p in assets.press_items([dict(rec, credit_line=""),
                                                                rec])], ["N1"])


class PressItems(unittest.TestCase):
    IMAGE = {"kind": "image", "title": "A photo", "source": "commons", "url": "img"}

    def test_articles_after_seventy_images_are_still_n1_to_n3(self):
        articles = news(google=[(f"Headline {n}", "AP") for n in range(3)])
        items = assets.press_items([dict(self.IMAGE)] * 70 + articles)
        self.assertEqual([i["id"] for i in items], ["N1", "N2", "N3"])
        self.assertEqual([i["headline"] for i in items],
                         ["Headline 0", "Headline 1", "Headline 2"])

    def test_more_than_eight_give_n1_to_n8_in_dossier_order(self):
        articles = news(google=[(f"Headline {n}", "AP") for n in range(11)])
        items = assets.press_items(articles)
        self.assertEqual([i["id"] for i in items], [f"N{k}" for k in range(1, 9)])
        self.assertEqual(items[-1]["headline"], "Headline 7")

    def test_nothing_is_stored_or_added_to_a_record(self):
        [rec] = news(google=[("Genoa bridge inquiry names 59 defendants", "Reuters")])
        before = dict(rec)
        assets.press_items([rec])
        self.assertEqual(rec, before)
        from dataclasses import fields
        self.assertNotIn("headline", {f.name for f in fields(assets.AssetHit)})


if __name__ == "__main__":
    unittest.main()
