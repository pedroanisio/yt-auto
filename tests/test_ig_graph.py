"""igstudio.graph: the one call shape, the token in a header, errors, retries, paging."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from igstudio import graph as g  # noqa: E402
from tests.ig_fakes import FakeGraph  # noqa: E402


class CallShape(unittest.TestCase):
    def test_token_in_header_and_params_in_query(self):
        fake = FakeGraph({"GET 17841400/media": {"data": [{"id": "1"}]}})
        api = g.Graph("tok-secret", transport=fake)
        data = api.get("17841400/media", fields=("id", "caption"), limit=5)
        self.assertEqual(data["data"], [{"id": "1"}])
        self.assertEqual(fake.tokens, ["Bearer tok-secret"])
        method, path, params = fake.requests[0]
        self.assertEqual((method, path), ("GET", "17841400/media"))
        self.assertEqual(params, {"fields": "id,caption", "limit": "5"})

    def test_post_sends_a_form_body_with_booleans_as_words(self):
        fake = FakeGraph()
        api = g.Graph("t", transport=fake)
        api.post("1/media", image_url="https://x/a.jpg", share_to_feed=False, children=["a", "b"])
        method, path, params = fake.requests[0]
        self.assertEqual(method, "POST")
        self.assertEqual(params["share_to_feed"], "false")
        self.assertEqual(params["children"], "a,b")

    def test_host_follows_the_login_and_version_follows_the_env(self):
        with mock.patch.dict(os.environ, {"IG_API_VERSION": "v26.0"}):
            api = g.Graph("t", login="facebook")
        self.assertEqual(api.base, "https://graph.facebook.com/v26.0")
        self.assertEqual(g.Graph("t").base, f"https://graph.instagram.com/{g.DEFAULT_VERSION}")
        with self.assertRaises(ValueError):
            g.Graph("t", login="tiktok")


class Errors(unittest.TestCase):
    def test_api_error_becomes_graph_error_with_code(self):
        fake = FakeGraph({"GET me": {"error": {"message": "Invalid OAuth access token",
                                               "type": "OAuthException", "code": 190,
                                               "fbtrace_id": "A1"}}})
        api = g.Graph("t", transport=fake)
        with self.assertRaises(g.GraphError) as ctx:
            api.get("me")
        err = ctx.exception
        self.assertEqual(err.code, 190)
        self.assertTrue(err.expired_token)
        self.assertFalse(err.transient)
        self.assertIn("code 190", str(err))

    def test_transient_error_is_retried_then_raised(self):
        fake = FakeGraph({"GET me": [{"error": {"message": "limit", "code": 4}},
                                     {"error": {"message": "limit", "code": 4}},
                                     {"id": "1"}]})
        slept = []
        api = g.Graph("t", transport=fake, sleep=slept.append)
        self.assertEqual(api.get("me"), {"id": "1"})
        self.assertEqual(len(fake.requests), 3)
        self.assertEqual(slept, [2.0, 4.0])
        fake = FakeGraph({"GET me": {"error": {"message": "down"}}})
        fake.routes["GET me"] = (503, {"error": {"message": "down"}})
        api = g.Graph("t", transport=fake, retries=1, sleep=lambda s: None)
        with self.assertRaises(g.GraphError) as ctx:
            api.get("me")
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(len(fake.requests), 2)

    def test_non_json_and_http_error_bodies(self):
        api = g.Graph("t", transport=lambda *a: (502, "<html>bad gateway</html>"), retries=0)
        with self.assertRaises(g.GraphError) as ctx:
            api.get("me")
        self.assertEqual(ctx.exception.status, 502)
        api = g.Graph("t", transport=lambda *a: (403, "{}"), retries=0)
        with self.assertRaises(g.GraphError) as ctx:
            api.get("me")
        self.assertIn("http 403", str(ctx.exception))

    def test_no_net_guard(self):
        with mock.patch.dict(os.environ, {"IG_NO_NET": "1"}):
            with self.assertRaises(g.NoNetwork):
                g.Graph("t").get("me")


class Paging(unittest.TestCase):
    def test_follows_next_cursor_and_stops_at_limit(self):
        page1 = {"data": [{"id": "1"}, {"id": "2"}],
                 "paging": {"cursors": {"after": "AA"}, "next": "https://..."}}
        page2 = {"data": [{"id": "3"}], "paging": {"cursors": {"after": "BB"}}}
        fake = FakeGraph({"GET 1/media": [page1, page2]})
        api = g.Graph("t", transport=fake)
        self.assertEqual([r["id"] for r in api.pages("1/media", fields="id")], ["1", "2", "3"])
        self.assertEqual(fake.requests[1][2].get("after"), "AA")
        fake = FakeGraph({"GET 1/media": [dict(page1), page2]})
        api = g.Graph("t", transport=fake)
        self.assertEqual(len(list(api.pages("1/media", limit=1))), 1)
        self.assertEqual(len(fake.requests), 1)


if __name__ == "__main__":
    unittest.main()
