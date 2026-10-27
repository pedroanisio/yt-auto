"""igstudio.publish: checks, plans and the container flow."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from igstudio import publish as pub  # noqa: E402
from igstudio.graph import Graph, GraphError  # noqa: E402
from tests.ig_fakes import FakeGraph  # noqa: E402

IMG = "https://cdn.example/a.jpg"
VID = "https://cdn.example/a.mp4"


class Checks(unittest.TestCase):
    def test_each_kind_and_its_counts(self):
        self.assertEqual(pub.check(pub.Post("image", [IMG], "hi")), [])
        self.assertEqual(pub.check(pub.Post("reel", [VID], "hi")), [])
        self.assertEqual(pub.check(pub.Post("story", [VID])), [])
        self.assertEqual(pub.check(pub.Post("carousel", [IMG, VID])), [])
        self.assertTrue(pub.check(pub.Post("image", [IMG, IMG])))
        self.assertTrue(pub.check(pub.Post("carousel", [IMG])))
        self.assertTrue(pub.check(pub.Post("carousel", [IMG] * 11)))
        self.assertTrue(pub.check(pub.Post("reel", [IMG])))
        self.assertTrue(pub.check(pub.Post("image", [VID])))
        self.assertTrue(pub.check(pub.Post("story", [IMG], caption="no")))
        self.assertTrue(pub.check(pub.Post("tiktok", [IMG])))
        self.assertTrue(pub.check(pub.Post("image", [])))

    def test_local_paths_and_long_captions_are_refused(self):
        problems = pub.check(pub.Post("image", ["/home/me/a.jpg"]))
        self.assertIn("public URL", problems[0])
        self.assertTrue(pub.check(pub.Post("image", [IMG], "x" * 2201)))
        self.assertTrue(pub.check(pub.Post("image", [IMG], "#a " * 31)))

    def test_plan_shapes(self):
        steps = pub.plan(pub.Post("image", [IMG], "cap", alt_text="a cat", location_id="7"))
        self.assertEqual(steps[0]["params"], {"image_url": IMG, "caption": "cap",
                                              "location_id": "7", "alt_text": "a cat"})
        steps = pub.plan(pub.Post("reel", [VID], "cap", cover_url=IMG, share_to_feed=False,
                                  thumb_offset=1500))
        self.assertEqual(steps[0]["params"]["media_type"], "REELS")
        self.assertIs(steps[0]["params"]["share_to_feed"], False)
        self.assertEqual(steps[0]["params"]["thumb_offset"], 1500)
        steps = pub.plan(pub.Post("story", [IMG]))
        self.assertEqual(steps[0]["params"], {"media_type": "STORIES", "image_url": IMG})
        steps = pub.plan(pub.Post("carousel", [IMG, VID], "cap", collaborators=["ann"]))
        self.assertEqual(len(steps), 3)
        self.assertEqual(steps[0]["params"], {"is_carousel_item": True, "image_url": IMG})
        self.assertEqual(steps[1]["params"]["media_type"], "VIDEO")
        self.assertEqual(steps[2]["params"]["media_type"], "CAROUSEL")
        self.assertEqual(steps[2]["params"]["collaborators"], ["ann"])
        with self.assertRaises(ValueError):
            pub.plan(pub.Post("image", []))

    def test_trial_reel_and_ai_label(self):
        steps = pub.plan(pub.Post("reel", [VID], "c", trial="auto", ai_generated=True))
        params = steps[0]["params"]
        self.assertEqual(params["trial_params"], {"graduation_strategy": "SS_PERFORMANCE"})
        self.assertIs(params["is_ai_generated"], True)
        steps = pub.plan(pub.Post("reel", [VID], "c", trial="manual"))
        self.assertEqual(steps[0]["params"]["trial_params"], {"graduation_strategy": "MANUAL"})
        self.assertNotIn("is_ai_generated", steps[0]["params"])
        steps = pub.plan(pub.Post("image", [IMG], "c", ai_generated=True))
        self.assertIs(steps[0]["params"]["is_ai_generated"], True)
        self.assertNotIn("trial_params", pub.plan(pub.Post("reel", [VID], "c"))[0]["params"])
        self.assertTrue(any("trial" in p for p in pub.check(pub.Post("image", [IMG], trial="auto"))))
        self.assertTrue(any("trial" in p for p in pub.check(pub.Post("reel", [VID], trial="later"))))

    def test_post_round_trips_through_a_dict(self):
        post = pub.Post("reel", [VID], "c", cover_url=IMG, trial="auto", variant_of="m9")
        self.assertEqual(pub.Post.from_dict(post.to_dict()), post)
        self.assertEqual(pub.Post.from_dict({"kind": "image", "media": [IMG], "junk": 1}).kind,
                         "image")


class Flow(unittest.TestCase):
    def graph(self, fake):
        return Graph("t", transport=fake, sleep=lambda s: None)

    def test_image_publishes_after_the_container_finishes(self):
        fake = FakeGraph({"GET c1": [{"status_code": "IN_PROGRESS"},
                                     {"status_code": "FINISHED"}]})
        slept, said = [], []
        row = pub.publish(self.graph(fake), "1784", pub.Post("image", [IMG], "hello"),
                          sleep=slept.append, say=said.append)
        self.assertEqual(row["id"], "mc1")
        self.assertEqual(row["container_id"], "c1")
        self.assertIn("instagram.com", row["permalink"])
        self.assertEqual(slept, [5.0])
        paths = [p for _, p, _ in fake.requests]
        self.assertEqual(paths, ["1784/content_publishing_limit", "1784/media", "c1", "c1",
                                 "1784/media_publish", "mc1"])
        self.assertEqual(fake.sent("POST", "/media")[0]["caption"], "hello")
        self.assertEqual(fake.sent("POST", "/media_publish")[0]["creation_id"], "c1")
        self.assertIn("quota 3/100", said[0])

    def test_trial_params_go_over_the_wire_as_json(self):
        fake = FakeGraph()
        pub.publish(self.graph(fake), "1784", pub.Post("reel", [VID], "c", trial="auto", ai_generated=True))
        sent = fake.sent("POST", "/media")[0]
        self.assertEqual(sent["trial_params"], '{"graduation_strategy":"SS_PERFORMANCE"}')
        self.assertEqual(sent["is_ai_generated"], "true")

    def test_carousel_children_are_the_item_containers(self):
        fake = FakeGraph()
        pub.publish(self.graph(fake), "1784", pub.Post("carousel", [IMG, IMG, VID], "c"))
        posts = fake.sent("POST", "/media")
        self.assertEqual(len(posts), 4)
        self.assertEqual(posts[3]["children"], "c1,c2,c3")
        self.assertEqual(posts[3]["media_type"], "CAROUSEL")
        self.assertEqual(fake.sent("POST", "/media_publish")[0]["creation_id"], "c4")

    def test_quota_used_up_refuses_before_any_container(self):
        fake = FakeGraph({"GET 1784/content_publishing_limit":
                          {"data": [{"quota_usage": 100, "config": {"quota_total": 100}}]}})
        with self.assertRaises(GraphError) as ctx:
            pub.publish(self.graph(fake), "1784", pub.Post("image", [IMG]))
        self.assertIn("quota", str(ctx.exception))
        self.assertEqual(fake.sent("POST", "/media"), [])

    def test_container_error_and_timeout(self):
        fake = FakeGraph({"GET c1": {"status_code": "ERROR", "status": "Media too large"}})
        with self.assertRaises(GraphError) as ctx:
            pub.publish(self.graph(fake), "1784", pub.Post("reel", [VID]))
        self.assertIn("Media too large", str(ctx.exception))
        fake = FakeGraph({"GET c1": lambda p: {"status_code": "IN_PROGRESS"}})
        ticks = iter(range(0, 10_000, 100))
        with self.assertRaises(GraphError) as ctx:
            pub.publish(self.graph(fake), "1784", pub.Post("reel", [VID]), timeout=250,
                        sleep=lambda s: None, clock=lambda: next(ticks))
        self.assertIn("still IN_PROGRESS", str(ctx.exception))
        self.assertEqual(fake.sent("POST", "/media_publish"), [])

    def test_quota_reader_defaults(self):
        fake = FakeGraph({"GET 1/content_publishing_limit": {"data": [{"quota_usage": 7}]}})
        self.assertEqual(pub.quota(self.graph(fake), "1"), (7, 100))


if __name__ == "__main__":
    unittest.main()
