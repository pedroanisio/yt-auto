"""`ig` end to end against the fake Graph: no network, a temporary home."""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from igstudio import cli  # noqa: E402
from igstudio import queue as q  # noqa: E402
from tests.ig_fakes import FakeGraph  # noqa: E402

IMG = "https://cdn.example/a.jpg"
VID = "https://cdn.example/a.mp4"
NOW = dt.datetime(2026, 10, 27, 12, 0, tzinfo=dt.timezone.utc)


class CliCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        self.fake = FakeGraph()
        patcher = mock.patch.dict(os.environ, {"IG_NO_NET": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def ig(self, *argv, fake=None, engine=None, now=NOW):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--home", self.home, *argv], transport=fake or self.fake,
                            sleep=lambda s: None, clock=lambda: 0.0, now=lambda: now,
                            engine=engine)
        return code, out.getvalue(), err.getvalue()

    def add(self, name="shop", user_id="1784", **kw):
        return self.ig("accounts", "add", name, "--user-id", user_id, "--token", f"tok-{name}",
                       "--timezone", "Europe/Lisbon", **kw)


class Accounts(CliCase):
    def test_add_verifies_lists_and_never_prints_the_token(self):
        code, out, err = self.add()
        self.assertEqual(code, 0, err)
        self.assertIn("added shop (@shop, 1200 followers)", out)
        self.assertEqual(self.fake.tokens, ["Bearer tok-shop"])
        code, out, _ = self.ig("accounts", "list")
        self.assertIn("shop", out)
        self.assertIn("@shop", out)
        self.assertNotIn("tok-shop", out)
        code, out, _ = self.ig("--json", "accounts", "list")
        self.assertEqual(json.loads(out)[0]["token"], "file:…")

    def test_add_with_a_bad_token_keeps_the_row_and_says_so(self):
        fake = FakeGraph({"GET 1784": {"error": {"message": "Invalid OAuth", "code": 190}}})
        code, out, err = self.add(fake=fake)
        self.assertEqual(code, 1)
        self.assertIn("did not verify", err)
        self.assertIn("code 190", err)
        code, out, _ = self.ig("accounts", "list")
        self.assertIn("shop", out)

    def test_unknown_account_is_a_plain_error(self):
        code, out, err = self.ig("media", "nobody")
        self.assertEqual(code, 1)
        self.assertIn("no account named 'nobody'", err)

    def test_verify_all_reports_each(self):
        self.add("a", "1")
        self.add("b", "2")
        fake = FakeGraph({"GET 2": {"error": {"message": "expired", "code": 190}}})
        code, out, _ = self.ig("accounts", "verify", fake=fake)
        self.assertEqual(code, 1)
        self.assertIn("a: ok", out)
        self.assertIn("b: FAILED", out)

    def test_refresh_if_within_skips_a_fresh_token(self):
        self.ig("accounts", "add", "shop", "--user-id", "1", "--token", "t", "--expires-in",
                str(50 * 86400), "--no-verify")
        code, out, _ = self.ig("accounts", "refresh", "--if-within", "10")
        self.assertIn("skipped, 50 days left", out)
        fake = FakeGraph({"GET refresh_access_token": {"access_token": "n", "expires_in": 100}})
        code, out, _ = self.ig("accounts", "refresh", fake=fake)
        self.assertEqual(code, 0)
        self.assertIn("refreshed", out)

    def test_discover_prints_ids(self):
        fake = FakeGraph({"GET me": {"user_id": "99", "username": "found"}})
        code, out, _ = self.ig("accounts", "discover", "--token", "x", fake=fake)
        self.assertEqual(code, 0)
        self.assertIn("@found  id 99", out)
        code, out, err = self.ig("accounts", "discover")
        self.assertEqual(code, 2)

    def test_status_shows_quota_and_token_days(self):
        self.ig("accounts", "add", "shop", "--user-id", "1784", "--token", "t", "--expires-in",
                str(12 * 86400), "--no-verify")
        code, out, _ = self.ig("status")
        self.assertEqual(code, 0)
        self.assertIn("quota 3/100", out)
        self.assertIn("token 12 d left", out)


class Posting(CliCase):
    def setUp(self):
        super().setUp()
        self.add()
        self.fake.requests.clear()

    def test_dry_run_sends_nothing(self):
        code, out, _ = self.ig("post", "shop", "--image", IMG, "--caption", "hi", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("would publish a image", out)
        self.assertIn("image_url", out)
        self.assertEqual(self.fake.requests, [])

    def test_publish_now(self):
        code, out, err = self.ig("post", "shop", "--carousel", IMG, VID, "--caption", "two")
        self.assertEqual(code, 0, err)
        self.assertIn("published to shop: https://www.instagram.com/p/mc3/", out)
        self.assertEqual(len(self.fake.sent("POST", "/media")), 3)

    def test_bad_post_is_refused_before_any_call(self):
        code, out, err = self.ig("post", "shop", "--reel", IMG)
        self.assertEqual(code, 2)
        self.assertIn("a reel needs a video URL", err)
        self.assertEqual(self.fake.requests, [])
        code, out, err = self.ig("post", "shop", "--image", "/tmp/a.jpg")
        self.assertEqual(code, 2)
        self.assertIn("public URL", err)

    def test_caption_file_and_json_output(self):
        path = Path(self.home) / "cap.txt"
        path.write_text("from a file\n#one #two", encoding="utf-8")
        code, out, _ = self.ig("--json", "post", "shop", "--image", IMG, "--caption-file",
                               str(path))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["id"], "mc1")
        self.assertEqual(self.fake.sent("POST", "/media")[0]["caption"], "from a file\n#one #two")


class Scheduling(CliCase):
    def setUp(self):
        super().setUp()
        self.add()
        self.fake.requests.clear()

    def test_queue_then_run_publishes_when_due(self):
        code, out, _ = self.ig("post", "shop", "--image", IMG, "--caption", "later", "--at", "+2h")
        self.assertEqual(code, 0)
        self.assertIn("queued", out)
        self.assertEqual(self.fake.requests, [])
        code, out, _ = self.ig("queue", "list")
        self.assertIn("pending", out)
        self.assertIn("2026-10-27 14:00", out)
        code, out, _ = self.ig("run")
        self.assertIn("nothing due", out)
        code, out, _ = self.ig("run", now=NOW + dt.timedelta(hours=3))
        self.assertEqual(code, 0)
        self.assertIn("published https://www.instagram.com/p/mc1/", out)
        code, out, _ = self.ig("queue", "list")
        self.assertIn("done", out)
        self.assertIn("-> https://www.instagram.com/p/mc1/", out)
        code, out, _ = self.ig("queue", "clear")
        self.assertIn("removed 1 done", out)

    def test_run_dry_run_changes_nothing(self):
        self.ig("post", "shop", "--image", IMG, "--at", "now")
        code, out, _ = self.ig("run", "--dry-run", now=NOW + dt.timedelta(minutes=1))
        self.assertEqual(code, 0)
        self.assertIn("due, image (dry run", out)
        self.assertEqual(self.fake.requests, [])
        self.assertEqual(q.Queue(Path(self.home)).listing("pending")[0]["attempts"], 0)

    def test_failed_run_exits_1_and_keeps_the_error(self):
        self.ig("post", "shop", "--reel", VID, "--at", "now")
        fake = FakeGraph({"GET c1": {"status_code": "ERROR", "status": "Unsupported codec"}})
        code, out, _ = self.ig("run", fake=fake, now=NOW + dt.timedelta(minutes=1))
        self.assertEqual(code, 1)
        self.assertIn("Unsupported codec", out)
        code, out, _ = self.ig("queue", "list", "--state", "failed")
        self.assertIn("Unsupported codec", out)
        code, out, _ = self.ig("queue", "remove", q.Queue(Path(self.home)).items[0]["id"])
        self.assertIn("removed", out)

    def test_naive_time_is_read_in_the_account_timezone(self):
        code, out, _ = self.ig("--json", "post", "shop", "--image", IMG, "--at",
                               "2026-10-28 09:00")
        self.assertEqual(json.loads(out)["when"], "2026-10-28T09:00:00+00:00")


class Reading(CliCase):
    def setUp(self):
        super().setUp()
        self.add()

    def test_media_insights_comments(self):
        fake = FakeGraph({
            "GET 1784/media": {"data": [{"id": "m1", "caption": "A post", "timestamp":
                                         "2026-10-20T10:00:00+0000", "like_count": 5,
                                         "comments_count": 1, "media_product_type": "FEED",
                                         "permalink": "https://x/p/m1/"}]},
            "GET 1784/insights": [
                {"data": [{"name": "reach", "total_value": {"value": 900}}]},
                {"error": {"message": "under 100 followers", "code": 100}}],
            "GET m1/insights": {"data": [{"name": "reach", "values": [{"value": 42}]}]},
            "GET m1/comments": {"data": [{"id": "k1", "text": "Nice", "username": "fan",
                                          "timestamp": "2026-10-21T10:00:00+0000",
                                          "replies": {"data": [{"username": "shop",
                                                                "text": "thanks"}]}},
                                         {"id": "k2", "text": "Price?", "username": "buyer",
                                          "timestamp": "2026-10-21T11:00:00+0000"}]},
        })
        code, out, _ = self.ig("media", fake=fake)
        self.assertIn("A post", out)
        self.assertIn("5 likes", out)
        code, out, _ = self.ig("insights", "shop", "--days", "7", fake=fake)
        self.assertRegex(out, r"reach\s+900")
        self.assertIn("warning: follower_count", out)
        self.assertEqual(fake.sent("GET", "/insights")[0]["metric_type"], "total_value")
        code, out, _ = self.ig("insights", "shop", "--media", "m1", fake=fake)
        self.assertIn("reach", out)
        self.assertIn("42", out)
        code, out, _ = self.ig("comments", "shop", "--unanswered", fake=fake)
        self.assertIn("@buyer: Price?", out)
        self.assertNotIn("@fan", out)
        code, out, _ = self.ig("reply", "shop", "k2", "DM sent!", fake=fake)
        self.assertEqual(code, 0)
        self.assertEqual(fake.sent("POST", "k2/replies")[0]["message"], "DM sent!")
        self.ig("hide", "shop", "k2", fake=fake)
        self.assertEqual(fake.sent("POST", "k2")[0]["hide"], "true")
        self.ig("delete-comment", "shop", "k2", fake=fake)
        self.assertEqual([m for m, p, _ in fake.requests if p == "k2" and m == "DELETE"],
                         ["DELETE"])


class Caption(CliCase):
    class Engine:
        prompts: list = []
        usage = type("U", (), {"cost_usd": 0.0123, "calls": 1})()

        def ask_json(self, prompt, schema):
            self.prompts.append(prompt)
            assert set(schema["required"]) >= {"hook_trigger", "cta_type"}
            return {"hook_line": "Your coffee is 2 minutes from better", "hook_trigger": "paradox",
                    "body": "Grind finer.\nPour slower.", "cta": "Send this to the friend "
                    "who swears by pods.", "cta_type": "send",
                    "hashtags": ["coffee", "#homebarista", "pourover"],
                    "alt_text": "A kettle pouring into a V60.", "hook_variants": ["a", "b"]}

    def test_caption_is_held_to_the_house_and_the_record(self):
        self.add()
        Path(self.home, "house").mkdir()
        Path(self.home, "house", "shop.md").write_text("## hook\nNEWS HOOK RULE.\n", encoding="utf-8")
        from igstudio import record as rec
        rec.add(Path(self.home), "shop", rec.fingerprint("Your coffee is 2 minutes from better\n#old",
                                                         media_id="9", permalink="https://x/p/9/",
                                                         published_at="2026-10-20T00:00:00+00:00"))
        engine = self.Engine()
        code, out, err = self.ig("caption", "A reel about pour-over", "--goal", "shares",
                                 "--account", "shop", engine=engine)
        self.assertEqual(code, 0, err)
        prompt = engine.prompts[-1]
        self.assertIn("Goal: shares, so the CTA is a send", prompt)
        self.assertIn("@shop", prompt)
        self.assertIn("NEWS HOOK RULE.", prompt)
        self.assertNotIn("curiosity trigger, declared by name", prompt)   # replaced by the account
        self.assertIn("engagement bait", prompt)                          # default `all` stays
        self.assertIn("last 1 posts", prompt)
        self.assertTrue(out.startswith("Your coffee is 2 minutes from better\n\nGrind finer."))
        self.assertIn("#coffee #homebarista #pourover", out)
        self.assertIn("hook trigger: paradox   cta: send", out)
        self.assertIn("cost: $0.0123", out)
        self.assertIn("warning: account: hook: opens on the same words as https://x/p/9/", err)
        code, out, _ = self.ig("--json", "caption", "brief", "--account", "shop", "--no-house",
                               engine=engine)
        data = json.loads(out)
        self.assertNotIn("NEWS HOOK RULE.", engine.prompts[-1])
        self.assertEqual(data["house"]["block_sha256"], "")
        self.assertEqual(len(data["repeats"]), 1)


class HouseAndRecord(CliCase):
    def test_house_command_layers_and_names_sources(self):
        Path(self.home, "house.md").write_text("## cta\nREGISTRY CTA.\n", encoding="utf-8")
        code, out, _ = self.ig("house", "--stage", "cta")
        self.assertEqual(code, 0)
        self.assertIn("igstudio/house/default.md", out)
        self.assertIn("house.md", out)
        self.assertIn("REGISTRY CTA.", out)
        code, out, _ = self.ig("--json", "house", "news", "--stage", "reply", "--no-house")
        self.assertEqual(json.loads(out)["block"], "")

    def test_post_records_a_row_and_warns_on_a_repeat(self):
        self.add()
        self.fake.requests.clear()
        code, out, err = self.ig("post", "shop", "--image", IMG, "--caption",
                                 "Why the bridge fell\n#history #bridges", "--trigger", "question",
                                 "--cta", "save")
        self.assertEqual(code, 0, err)
        code, out, _ = self.ig("record", "shop")
        self.assertIn("question", out)
        self.assertIn("Why the bridge fell", out)
        code, out, err = self.ig("post", "shop", "--image", IMG, "--caption",
                                 "Why the bridge fell again\n#bridges #history", "--dry-run")
        self.assertIn("warning: account: hook: opens on the same words", err)
        self.assertIn("warning: account: hashtags: the same set", err)
        code, out, err = self.ig("post", "shop", "--story", VID)
        self.assertEqual(code, 0, err)
        code, out, _ = self.ig("--json", "record", "shop")
        self.assertEqual(len(json.loads(out)), 1)                   # a story is not recorded

    def test_post_trial_ai_and_variant_flags(self):
        self.add()
        code, out, err = self.ig("--json", "post", "shop", "--reel", VID, "--caption", "c",
                                 "--trial", "auto", "--ai-generated", "--variant-of", "m9",
                                 "--dry-run")
        self.assertEqual(code, 0, err)
        params = json.loads(out)["steps"][0]["params"]
        self.assertEqual(params["trial_params"], {"graduation_strategy": "SS_PERFORMANCE"})
        self.assertIs(params["is_ai_generated"], True)
        code, out, err = self.ig("post", "shop", "--reel", VID, "--caption", "A new opening\n#x",
                                 "--variant-of", "m9", "--ai-generated")
        self.assertEqual(code, 0, err)
        code, out, _ = self.ig("--json", "record", "shop")
        self.assertEqual(json.loads(out)[0]["variant_of"], "m9")
        code, out, err = self.ig("post", "shop", "--image", IMG, "--trial", "auto", "--dry-run")
        self.assertEqual(code, 2)
        self.assertIn("trial", err)

    def test_top_ranks_the_account_and_names_variants(self):
        self.add()
        from igstudio import record as rec
        rec.add(Path(self.home), "shop", rec.fingerprint("Variant hook\n#x", kind="reel",
                                                         media_id="m2", variant_of="m1"))
        fake = FakeGraph({
            "GET 1784/media": {"data": [
                {"id": "m1", "caption": "Parent hook", "media_product_type": "REELS",
                 "timestamp": "2026-10-20T10:00:00+0000", "permalink": "https://x/m1/"},
                {"id": "m2", "caption": "Variant hook", "media_product_type": "REELS",
                 "timestamp": "2026-10-21T10:00:00+0000", "permalink": "https://x/m2/"},
                {"id": "m3", "caption": "Other", "media_product_type": "FEED",
                 "timestamp": "2026-10-22T10:00:00+0000", "permalink": "https://x/m3/"}]},
            "GET m1/insights": {"data": [{"name": "views", "values": [{"value": 100}]}]},
            "GET m2/insights": {"data": [{"name": "views", "values": [{"value": 900}]}]},
            "GET m3/insights": {"data": [{"name": "views", "values": [{"value": 120}]}]},
        })
        code, out, err = self.ig("top", "shop", fake=fake)
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertTrue(lines[1].startswith("*") or "m2" in lines[1], out)
        self.assertIn("variant of m1", out)
        self.assertIn("beat it", out)
        code, out, _ = self.ig("--json", "top", "shop", "--metric", "views", fake=fake)
        data = json.loads(out)
        self.assertEqual(data[0]["id"], "m2")
        self.assertTrue(data[0]["winner"])
        self.assertEqual(data[0]["variant_of"], "m1")

    def test_record_sync_reads_the_media_list(self):
        self.add()
        fake = FakeGraph({"GET 1784/media": {"data": [
            {"id": "m1", "caption": "Synced hook\n#one #two", "permalink": "https://x/p/m1/",
             "timestamp": "2026-10-21T10:00:00+0000", "media_product_type": "REELS",
             "media_type": "VIDEO"}]}})
        code, out, _ = self.ig("record", "shop", "--sync", fake=fake)
        self.assertEqual(code, 0)
        self.assertIn("1 added, 0 updated", out)
        self.assertIn("Synced hook", out)
        self.assertIn("reel", out)


if __name__ == "__main__":
    unittest.main()
