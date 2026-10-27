"""igstudio.accounts: the registry, token references, verify, refresh, discover."""

from __future__ import annotations

import datetime as dt
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from igstudio import accounts as acc  # noqa: E402
from igstudio.graph import GraphError  # noqa: E402
from tests.ig_fakes import FakeGraph  # noqa: E402


class RegistryFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_add_with_literal_token_stores_it_in_a_0600_file_not_the_registry(self):
        reg = acc.Registry(self.root)
        a = reg.add("shop", ig_user_id="1784", token="EAAsecret", timezone="Europe/Lisbon",
                    expires_in=60 * 86400)
        reg.save()
        text = (self.root / "accounts.json").read_text()
        self.assertNotIn("EAAsecret", text)
        self.assertTrue(a.token.startswith("file:"))
        path = Path(a.token[5:])
        self.assertEqual(path.read_text().strip(), "EAAsecret")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(reg.token(a), "EAAsecret")
        again = acc.Registry(self.root)
        self.assertEqual(again.get("shop").timezone, "Europe/Lisbon")
        self.assertAlmostEqual(again.get("shop").token_days_left(), 60, delta=0.1)
        self.assertEqual(json.loads(text)["accounts"]["shop"]["ig_user_id"], "1784")

    def test_env_reference_reads_the_variable_and_names_it_when_missing(self):
        reg = acc.Registry(self.root)
        a = reg.add("brand", ig_user_id="2", token_env="IG_TOKEN_BRAND")
        with mock.patch.dict(os.environ, {"IG_TOKEN_BRAND": "abc"}):
            self.assertEqual(reg.token(a), "abc")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("IG_TOKEN_BRAND", None)
            with self.assertRaises(LookupError) as ctx:
                reg.token(a)
        self.assertIn("IG_TOKEN_BRAND", str(ctx.exception))

    def test_env_reference_reads_the_checkout_key_file_from_any_directory(self):
        root = self.root / "checkout"
        root.mkdir()
        (root / ".env").write_text("IG_TOKEN_FAR=from-checkout\n", encoding="utf-8")
        reg = acc.Registry(self.root)
        a = reg.add("far", ig_user_id="3", token_env="IG_TOKEN_FAR")
        cwd = os.getcwd()
        os.chdir(self.root)            # a folder with no key file of its own
        self.addCleanup(os.chdir, cwd)
        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.object(acc, "REPO_ROOT", root):
            os.environ.pop("IG_TOKEN_FAR", None)
            self.assertEqual(reg.token(a), "from-checkout")

    def test_add_refuses_bad_input(self):
        reg = acc.Registry(self.root)
        with self.assertRaises(ValueError):
            reg.add("all", ig_user_id="1", token="x")
        with self.assertRaises(ValueError):
            reg.add("a", ig_user_id="1")
        with self.assertRaises(ValueError):
            reg.add("a", ig_user_id="1", token="x", token_env="Y")
        with self.assertRaises(ValueError):
            reg.add("a", ig_user_id="1", token="x", login="basic")
        with self.assertRaises(Exception):
            reg.add("a", ig_user_id="1", token="x", timezone="Mars/Olympus")

    def test_select_and_remove(self):
        reg = acc.Registry(self.root)
        reg.add("b", ig_user_id="2", token="x")
        reg.add("a", ig_user_id="1", token="y")
        self.assertEqual([x.name for x in reg.select("all")], ["a", "b"])
        self.assertEqual([x.name for x in reg.select("b,a")], ["b", "a"])
        with self.assertRaises(KeyError):
            reg.select("c")
        token_file = Path(reg.get("a").token[5:])
        reg.remove("a")
        self.assertFalse(token_file.exists())
        self.assertEqual(reg.names(), ["b"])


class Remote(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.reg = acc.Registry(Path(self.tmp.name))
        self.a = self.reg.add("shop", ig_user_id="1784", token="old")

    def test_verify_updates_username(self):
        fake = FakeGraph({"GET 1784": {"id": "1784", "username": "myshop",
                                      "followers_count": 10}})
        profile = self.reg.verify(self.a, self.reg.graph(self.a, transport=fake))
        self.assertEqual(profile["username"], "myshop")
        self.assertEqual(self.a.username, "myshop")
        self.assertTrue(self.a.verified_at)
        self.assertIn("followers_count", fake.requests[0][2]["fields"])

    def test_refresh_instagram_login_writes_the_new_token_back(self):
        fake = FakeGraph({"GET refresh_access_token": {"access_token": "new", "token_type":
                                                       "bearer", "expires_in": 5184000}})
        info = self.reg.refresh(self.a, self.reg.graph(self.a, transport=fake))
        self.assertEqual(info["expires_in"], 5184000)
        self.assertEqual(self.reg.token(self.a), "new")
        self.assertAlmostEqual(self.a.token_days_left(), 60, delta=0.1)
        params = fake.requests[0][2]
        self.assertEqual(params["grant_type"], "ig_refresh_token")
        self.assertEqual(params["access_token"], "old")

    def test_refresh_facebook_login_needs_the_app_secret(self):
        self.a.login = "facebook"
        with mock.patch.dict(os.environ, {"IG_APP_ID": "", "IG_APP_SECRET": ""}), \
                mock.patch.object(acc, "load_keys", lambda: None):   # not this checkout's keys
            with self.assertRaises(LookupError):
                self.reg.refresh(self.a, self.reg.graph(self.a, transport=FakeGraph()))
        fake = FakeGraph({"GET oauth/access_token": {"access_token": "fb-new"}})
        with mock.patch.dict(os.environ, {"IG_APP_ID": "app", "IG_APP_SECRET": "sec"}):
            self.reg.refresh(self.a, self.reg.graph(self.a, transport=fake))
        self.assertEqual(self.reg.token(self.a), "fb-new")
        self.assertEqual(fake.requests[0][2]["grant_type"], "fb_exchange_token")
        self.assertEqual(self.a.token_expires_at, "")

    def test_refresh_without_a_token_in_the_answer_is_an_error(self):
        fake = FakeGraph({"GET refresh_access_token": {"token_type": "bearer"}})
        with self.assertRaises(GraphError):
            self.reg.refresh(self.a, self.reg.graph(self.a, transport=fake))
        self.assertEqual(self.reg.token(self.a), "old")

    def test_discover_both_logins(self):
        fake = FakeGraph({"GET me": {"id": "app-scoped", "user_id": "1784", "username": "me"}})
        found = acc.discover("t", login="instagram", transport=fake)
        self.assertEqual(found[0]["ig_user_id"], "1784")
        fake = FakeGraph({"GET me/accounts": {"data": [
            {"id": "p1", "name": "Shop page", "instagram_business_account":
             {"id": "9", "username": "shop"}},
            {"id": "p2", "name": "No IG"}]}})
        found = acc.discover("t", login="facebook", transport=fake)
        self.assertEqual([(f["ig_user_id"], f["username"]) for f in found], [("9", "shop")])
        self.assertIn("Shop page", found[0]["via"])


if __name__ == "__main__":
    unittest.main()
