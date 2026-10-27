"""igstudio.queue: times, the file, due items, and how a run treats failures."""

from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
import zoneinfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from igstudio import queue as q  # noqa: E402
from igstudio.graph import GraphError  # noqa: E402
from igstudio.publish import Post  # noqa: E402

IMG = "https://cdn.example/a.jpg"
UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
LISBON = zoneinfo.ZoneInfo("Europe/Lisbon")


class When(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(q.parse_when("now", UTC, now=NOW), NOW)
        self.assertEqual(q.parse_when("+2h", UTC, now=NOW), NOW + dt.timedelta(hours=2))
        self.assertEqual(q.parse_when("+30m", UTC, now=NOW), NOW + dt.timedelta(minutes=30))
        self.assertEqual(q.parse_when("+1d", UTC, now=NOW), NOW + dt.timedelta(days=1))
        when = q.parse_when("2026-10-28 09:00", LISBON, now=NOW)
        self.assertEqual(when.utcoffset(), dt.timedelta(0))   # Lisbon is on UTC in late October
        self.assertEqual(when.hour, 9)
        when = q.parse_when("2026-10-28T09:00+02:00", LISBON, now=NOW)
        self.assertEqual(when.astimezone(UTC).hour, 7)
        with self.assertRaises(ValueError):
            q.parse_when("tomorrow-ish", UTC, now=NOW)


class File(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_add_list_due_remove_clear(self):
        queue = q.Queue(self.root)
        late = queue.add("shop", NOW + dt.timedelta(hours=1), Post("image", [IMG], "later"), now=NOW)
        soon = queue.add("brand", NOW - dt.timedelta(minutes=1), Post("reel", ["https://x/v.mp4"]),
                         now=NOW)
        queue.save()
        again = q.Queue(self.root)
        self.assertEqual([i["id"] for i in again.listing()], [soon["id"], late["id"]])
        self.assertEqual([i["id"] for i in again.due(NOW)], [soon["id"]])
        self.assertEqual([i["id"] for i in again.listing(account="shop")], [late["id"]])
        again.remove(soon["id"])
        with self.assertRaises(KeyError):
            again.get(soon["id"])
        again.items[0]["state"] = "done"
        self.assertEqual(again.clear("done"), 1)
        self.assertEqual(again.items, [])
        with self.assertRaises(ValueError):
            queue.add("shop", NOW, Post("image", ["/local.jpg"]))


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.queue = q.Queue(Path(self.tmp.name))
        self.item = self.queue.add("shop", NOW, Post("image", [IMG], "c"), now=NOW)
        self.queue.add("shop", NOW + dt.timedelta(days=1), Post("image", [IMG], "future"), now=NOW)

    def test_success_marks_done_and_saves(self):
        calls = []

        def publisher(account, post):
            calls.append((account, post.caption))
            return {"id": "m1", "permalink": "https://instagram.com/p/m1/"}
        touched = q.run(self.queue, NOW, publisher)
        self.assertEqual(calls, [("shop", "c")])
        self.assertEqual(touched[0]["state"], "done")
        self.assertEqual(touched[0]["result"]["id"], "m1")
        saved = q.Queue(self.queue.root)
        self.assertEqual(saved.get(self.item["id"])["state"], "done")
        self.assertEqual(len(saved.listing("pending")), 1)
        self.assertEqual(q.run(self.queue, NOW, publisher), [])   # nothing due twice

    def test_transient_failure_stays_pending_until_max_attempts(self):
        def publisher(account, post):
            raise GraphError("rate", code=4)
        said = []
        for n in range(1, q.MAX_ATTEMPTS + 1):
            touched = q.run(self.queue, NOW, publisher, say=said.append)
            self.assertEqual(touched[0]["attempts"], n)
            self.assertEqual(touched[0]["state"], "pending" if n < q.MAX_ATTEMPTS else "failed")
        self.assertIn("will retry", said[0])
        self.assertIn("failed", said[-1])

    def test_permanent_failure_fails_at_once(self):
        def publisher(account, post):
            raise GraphError("bad media", code=100)
        touched = q.run(self.queue, NOW, publisher)
        self.assertEqual(touched[0]["state"], "failed")
        self.assertIn("bad media", touched[0]["error"])

        def missing(account, post):
            raise LookupError("no token")
        self.queue.items[0]["state"] = "pending"
        touched = q.run(self.queue, NOW, missing)
        self.assertEqual(touched[0]["state"], "failed")


if __name__ == "__main__":
    unittest.main()
