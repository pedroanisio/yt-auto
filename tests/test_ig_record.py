"""igstudio.record: the account record and the repeats it measures."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from igstudio import record as rec  # noqa: E402

CAP = "Your coffee is two minutes from better.\n\nGrind finer.\n\nSave this for Sunday.\n\n#coffee #pourover #homebarista"


class Fingerprints(unittest.TestCase):
    def test_hook_opening_hashtags_and_labels(self):
        row = rec.fingerprint(CAP, kind="reel", trigger="unexpected_observation", cta="save",
                              media_id="1", permalink="https://x/p/1/", published_at="2026-10-27T10:00:00+00:00")
        self.assertEqual(row["hook"], "Your coffee is two minutes from better.")
        self.assertEqual(row["opening"], ["your", "coffee", "is", "two"])
        self.assertEqual(row["hashtags"], ["coffee", "homebarista", "pourover"])
        self.assertEqual((row["trigger"], row["cta"]), ("unexpected_observation", "save"))
        bad = rec.fingerprint(CAP, trigger="clickbait", cta="tag")
        self.assertEqual((bad["trigger"], bad["cta"]), ("", ""))
        self.assertTrue(bad["published_at"])


class File(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_add_earlier_and_sync(self):
        rec.add(self.root, "news", rec.fingerprint("Old hook\n#a", media_id="1",
                                                    published_at="2026-10-01T00:00:00+00:00",
                                                    trigger="question", cta="save"))
        rec.add(self.root, "news", rec.fingerprint("New hook\n#b", media_id="2",
                                                    published_at="2026-10-20T00:00:00+00:00"))
        self.assertEqual([r["media_id"] for r in rec.earlier(self.root, "news")], ["2", "1"])
        self.assertEqual(len(rec.earlier(self.root, "news", limit=1)), 1)
        self.assertEqual([r["media_id"] for r in rec.earlier(self.root, "news",
                                                              before="2026-10-10")], ["1"])
        media = [
            {"id": "1", "caption": "Old hook changed\n#a", "permalink": "https://x/p/1/",
             "timestamp": "2026-10-01T09:00:00+0000", "media_product_type": "FEED",
             "media_type": "CAROUSEL_ALBUM"},
            {"id": "3", "caption": "Synced hook\n#c #d", "permalink": "https://x/p/3/",
             "timestamp": "2026-10-25T09:00:00+0000", "media_product_type": "REELS",
             "media_type": "VIDEO"},
        ]
        self.assertEqual(rec.sync(self.root, "news", media), (1, 1))
        rows = {r["media_id"]: r for r in rec.rows(self.root, "news")}
        self.assertEqual(rows["1"]["trigger"], "question")           # kept
        self.assertEqual(rows["1"]["hook"], "Old hook")               # kept
        self.assertEqual(rows["1"]["published_at"], "2026-10-01T09:00:00+00:00")
        self.assertEqual(rows["3"]["kind"], "reel")
        self.assertEqual(rows["3"]["hashtags"], ["c", "d"])
        self.assertEqual(rows["3"]["source"], "sync")
        self.assertEqual(rec.sync(self.root, "news", media), (0, 2))


class Repeats(unittest.TestCase):
    def rows(self):
        mk = rec.fingerprint
        return [
            mk("Nobody tells you this about leases\n#rent #leases", media_id="a", trigger="someone_knows", cta="save",
               permalink="https://x/p/a/", published_at="2026-10-26T00:00:00+00:00"),
            mk("Why the bridge fell\n#history", media_id="b", trigger="question", cta="save",
               published_at="2026-10-25T00:00:00+00:00"),
            mk("A dam meant to drain a city\n#history #italy", media_id="c", trigger="unresolved_outcome", cta="save",
               published_at="2026-10-24T00:00:00+00:00"),
        ]

    def test_opening_hashtags_cta_run_and_trigger_majority(self):
        new = rec.fingerprint("Nobody tells you this about rent\n#rent #leases", trigger="save", cta="save")
        lines = rec.repeats(new, self.rows())
        self.assertTrue(any("opens on the same words as https://x/p/a/" in l for l in lines), lines)
        self.assertTrue(any("hashtags: the same set" in l for l in lines), lines)
        self.assertTrue(any("cta: the last 3 posts all asked for a save" in l for l in lines), lines)
        self.assertFalse(any("is the trigger" in l for l in lines))   # three different triggers
        rows = self.rows()
        for r in rows:
            r["trigger"] = "question"
        lines = rec.repeats(rec.fingerprint("Fresh words here\n#x", trigger="question", cta="send"), rows)
        self.assertEqual(len(lines), 1)
        self.assertIn("question is the trigger 3 of the last 3", lines[0])
        self.assertEqual(rec.repeats(rec.fingerprint("", trigger="", cta=""), rows), [])

    def test_variant_of_is_kept_and_summarised(self):
        row = rec.fingerprint("A new opening\n#x", kind="reel", media_id="v1", variant_of="m9")
        self.assertEqual(row["variant_of"], "m9")
        self.assertEqual(rec.fingerprint("x")["variant_of"], "")
        self.assertIn("variant of m9", rec.summary([row]))

    def test_summary_names_hooks_and_labels(self):
        text = rec.summary(self.rows())
        self.assertIn("last 3 posts", text)
        self.assertIn("trigger someone_knows", text)
        self.assertIn("#leases #rent", text)
        self.assertEqual(rec.summary([]), "")


if __name__ == "__main__":
    unittest.main()
