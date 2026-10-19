"""freesound.py: search, licence policy, ranking and download, no network.

The two HTTP seams (`fetch_json`, `fetch_bytes`) are replaced with fakes, so
what the tests pin is the contract `yt sound --fetch` relies on: only CC0 and
CC-BY hits survive, the best hit fits the cue's duration before it is the
best rated, the hq mp3 preview is what gets downloaded, and a file already on
disk is never fetched twice.
"""

import tempfile
import unittest
import urllib.parse
from pathlib import Path

from ytstudio import freesound


def hit(**kw) -> dict:
    row = {"id": 1, "name": "Paper Fluttering.wav", "username": "someone",
           "license": "http://creativecommons.org/publicdomain/zero/1.0/",
           "type": "wav", "filesize": 1000, "duration": 4.0,
           "avg_rating": 4.0, "num_ratings": 10,
           "url": "https://freesound.org/people/someone/sounds/1/",
           "previews": {"preview-hq-mp3": "https://cdn.freesound.org/p/1-hq.mp3",
                        "preview-lq-mp3": "https://cdn.freesound.org/p/1-lq.mp3"}}
    row.update(kw)
    return row


class Licence(unittest.TestCase):
    def test_licence_urls_are_named(self):
        self.assertEqual(freesound.licence_name(
            "http://creativecommons.org/publicdomain/zero/1.0/"), "CC0 1.0")
        self.assertEqual(freesound.licence_name(
            "https://creativecommons.org/licenses/by/4.0/"), "CC-BY 4.0")
        self.assertEqual(freesound.licence_name(
            "http://creativecommons.org/licenses/by-nc/3.0/"), "CC-BY-NC 3.0")
        self.assertEqual(freesound.licence_name(
            "http://creativecommons.org/licenses/sampling+/1.0/"), "Sampling+ 1.0")

    def test_freesound_display_names_are_named_too(self):
        self.assertEqual(freesound.licence_name("Creative Commons 0"), "CC0")
        self.assertEqual(freesound.licence_name("Attribution"), "CC-BY")
        self.assertEqual(freesound.licence_name("Attribution Noncommercial"),
                         "CC-BY-NC")

    def test_only_cc0_and_cc_by_are_allowed(self):
        self.assertTrue(freesound.allowed("CC0 1.0"))
        self.assertTrue(freesound.allowed("CC-BY 4.0"))
        self.assertFalse(freesound.allowed("CC-BY-NC 3.0"))
        self.assertFalse(freesound.allowed("Sampling+ 1.0"))
        self.assertFalse(freesound.allowed("CC-BY-SA 4.0"))
        self.assertFalse(freesound.allowed(""))


class Hits(unittest.TestCase):
    def test_a_hit_carries_credit_preview_and_extension(self):
        h = freesound.Hit.from_raw(hit(license="https://creativecommons.org/licenses/by/4.0/"))
        self.assertEqual(h.id, 1)
        self.assertEqual(h.licence, "CC-BY 4.0")
        self.assertEqual(h.preview, "https://cdn.freesound.org/p/1-hq.mp3")
        self.assertEqual(h.ext, "mp3")
        self.assertIn("someone", h.credit)
        self.assertIn("CC-BY 4.0", h.credit)
        self.assertIn(h.url, h.credit)

    def test_a_hit_without_a_preview_is_unusable(self):
        self.assertIsNone(freesound.Hit.from_raw(hit(previews={})))


class Ranking(unittest.TestCase):
    def test_a_hit_that_covers_the_cue_beats_one_too_short(self):
        short = freesound.Hit.from_raw(hit(id=1, duration=1.0, avg_rating=5.0))
        fits = freesound.Hit.from_raw(hit(id=2, duration=6.0, avg_rating=3.5))
        ranked = freesound.rank([short, fits], want=5.0)
        self.assertEqual([h.id for h in ranked], [2, 1])

    def test_between_two_that_fit_the_better_rated_wins(self):
        meh = freesound.Hit.from_raw(hit(id=1, duration=6.0, avg_rating=3.0))
        good = freesound.Hit.from_raw(hit(id=2, duration=6.5, avg_rating=4.6))
        ranked = freesound.rank([meh, good], want=5.0)
        self.assertEqual([h.id for h in ranked], [2, 1])

    def test_a_rating_from_one_vote_counts_less_than_many(self):
        lone = freesound.Hit.from_raw(hit(id=1, duration=6.0, avg_rating=5.0,
                                          num_ratings=1))
        chorus = freesound.Hit.from_raw(hit(id=2, duration=6.0, avg_rating=4.5,
                                            num_ratings=40))
        ranked = freesound.rank([lone, chorus], want=5.0)
        self.assertEqual(ranked[0].id, 2)

    def test_far_too_long_is_a_mild_penalty_not_a_veto(self):
        huge = freesound.Hit.from_raw(hit(id=1, duration=300.0, avg_rating=4.0))
        self.assertGreater(freesound.fit(huge, want=2.0), 0.0)
        self.assertLess(freesound.fit(huge, want=2.0), 1.0)


class Search(unittest.TestCase):
    def setUp(self):
        self.calls: list[str] = []
        self._json = freesound.fetch_json
        self.rows = [hit(id=1), hit(id=2, license="http://creativecommons.org/licenses/by-nc/3.0/"),
                     hit(id=3, license="https://creativecommons.org/licenses/by/4.0/")]

        def fake(url, **kw):
            self.calls.append(url)
            return {"count": len(self.rows), "results": self.rows}
        freesound.fetch_json = fake

    def tearDown(self):
        freesound.fetch_json = self._json

    def test_search_drops_disallowed_licences_and_never_prints_the_token(self):
        hits = freesound.search("paper flutter", want_seconds=4, token="SECRET-VALUE")
        self.assertEqual(sorted(h.id for h in hits), [1, 3])
        query = urllib.parse.urlparse(self.calls[0]).query
        params = urllib.parse.parse_qs(query)
        self.assertEqual(params["query"], ["paper flutter"])
        self.assertEqual(params["token"], ["SECRET-VALUE"])
        self.assertIn("Creative Commons 0", params["filter"][0])
        self.assertIn("Attribution", params["filter"][0])
        self.assertIn("duration:", params["filter"][0])
        # the token is in the URL, so the URL is never what gets reported
        self.assertNotIn("SECRET-VALUE", freesound.describe(self.calls[0]))

    def test_search_without_a_token_asks_nothing(self):
        self.assertEqual(freesound.search("x", want_seconds=1, token=""), [])
        self.assertEqual(self.calls, [])

    def test_a_network_failure_is_an_empty_result_not_a_crash(self):
        def boom(url, **kw):
            raise OSError("no route")
        freesound.fetch_json = boom
        self.assertEqual(freesound.search("x", want_seconds=1, token="t"), [])


class Download(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._bytes = freesound.fetch_bytes
        self.fetched: list[str] = []

        def fake(url, **kw):
            self.fetched.append(url)
            return b"ID3fake-mp3-bytes"
        freesound.fetch_bytes = fake

    def tearDown(self):
        freesound.fetch_bytes = self._bytes
        self._tmp.cleanup()

    def test_download_writes_the_hq_preview_and_reports_bytes(self):
        h = freesound.Hit.from_raw(hit())
        dest = self.root / "sound" / "01-paper.mp3"
        size = freesound.download(h, dest)
        self.assertEqual(size, len(b"ID3fake-mp3-bytes"))
        self.assertEqual(dest.read_bytes(), b"ID3fake-mp3-bytes")
        self.assertEqual(self.fetched, [h.preview])

    def test_a_file_already_there_is_not_fetched_again(self):
        h = freesound.Hit.from_raw(hit())
        dest = self.root / "01-paper.mp3"
        dest.write_bytes(b"already")
        self.assertEqual(freesound.download(h, dest), len(b"already"))
        self.assertEqual(self.fetched, [])

    def test_an_empty_download_is_a_failure(self):
        freesound.fetch_bytes = lambda url, **kw: b""
        h = freesound.Hit.from_raw(hit())
        dest = self.root / "01-paper.mp3"
        with self.assertRaises(freesound.FreesoundError):
            freesound.download(h, dest)
        self.assertFalse(dest.exists())


if __name__ == "__main__":
    unittest.main()
