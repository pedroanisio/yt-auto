"""AUTO-43: the spotter writes what the pipeline does (a catalogue search, a
synthesis), and the fetch queries come from that, never from a cue's name."""
from __future__ import annotations

import tempfile
import unittest

from ytstudio import freesound, sound
from ytstudio.core import Idea


def cue(**kw) -> dict:
    base = {"kind": "sfx", "cue_id": "S1", "name": "Pad carries", "at": 0,
            "seconds": 4, "purpose": "p", "description": "d"}
    base.update(kw)
    return base


def designer(**kw):
    return sound.SoundDesigner(Idea(title="t", genre="g", description="d"),
                               verbose=False, **kw)


class Schema(unittest.TestCase):
    item = sound.CUES_SCHEMA["properties"]["cues"]["items"]

    def test_each_cue_asks_for_a_search_description_and_a_synth_spec(self):
        for key in ("search_description", "synth_spec"):
            self.assertEqual(self.item["properties"][key]["type"], "string")
            self.assertIn(key, self.item["required"])

    def test_the_cue_keeps_both(self):
        c = sound.Cue.from_raw({"kind": "sfx", "name": "n", "at": 1, "seconds": 2,
                                "search_description": " glass bowl strike ",
                                "synth_spec": " damped sine 880 Hz "})
        self.assertEqual(c.search_description, "glass bowl strike")
        self.assertEqual(c.synth_spec, "damped sine 880 Hz")

    def test_the_prompt_is_for_the_pipeline_not_a_human_crew(self):
        for kw in ({}, {"free_only": True}, {"library": ["x library"]}):
            text = designer(**kw)._sourcing()
            self.assertIn("search_description", text)
            self.assertIn("synth_spec", text)
            self.assertIn("noise", text.lower())
            for crew in ("commission", "record it yourself", "recorded yourself",
                         "composer"):
                self.assertNotIn(crew, text.lower())

    def test_the_spot_prompt_carries_it(self):
        asked = {}
        d = designer()
        d._ask = lambda prompt, schema: asked.setdefault("p", prompt) and {"cues": []}
        d.spot_whole()
        self.assertIn("search_description", asked["p"])


class Queries(unittest.TestCase):
    def test_the_query_comes_from_the_description(self):
        c = cue(search_description="glass bowl strike")
        self.assertEqual(sound.cue_queries(c)[0], "glass bowl strike")

    def test_a_long_description_is_also_tried_shortened(self):
        c = cue(search_description="a soft glass bowl being struck once in a quiet room")
        qs = sound.cue_queries(c)
        self.assertLessEqual(len(qs[0].split()), 6)
        self.assertGreater(len(qs), 1)

    def test_the_name_is_never_a_query(self):
        c = cue(name="Solid solid flash", search_description="electric arc zap",
                query="spark", search=["crackle"], layers=["static hiss, faint"])
        for q in sound.cue_queries(c):
            self.assertNotIn("solid", q)
            self.assertNotIn("flash", q)

    def test_no_description_means_no_query_not_name_fragments(self):
        self.assertEqual(sound.cue_queries(cue(name="Study air")), [])

    def test_order_description_then_query_search_layers(self):
        c = cue(search_description="paper flutter", query="page flip",
                search=["rustle"], layers=["wind gust, low"])
        self.assertEqual(sound.cue_queries(c),
                         ["paper flutter", "page flip", "rustle", "wind gust"])


class Synth(unittest.TestCase):
    def test_the_target_and_the_brief_carry_the_spec(self):
        data = {"cues": [cue(synth_spec="band-passed pink noise 200-800 Hz, slow swell",
                             search_description="wind bed")]}
        with tempfile.TemporaryDirectory() as root:
            targets = sound.synth_targets(root, data)
        self.assertTrue(targets[0]["synth_spec"].startswith("band-passed"))
        brief = sound.synth_brief(targets)
        self.assertIn("synth_spec: band-passed pink noise", brief)
        self.assertIn("search_description: wind bed", brief)
        self.assertIn("synth_spec", sound.SYNTH_RULES)


class FetchUsesDescription(unittest.TestCase):
    def test_fetch_searches_only_description_derived_queries(self):
        seen = []
        real_best, real_ranked = freesound.best, freesound.ranked

        def fake_best(queries, **kw):
            seen.extend(queries)
            return None, ""

        def fake_ranked(queries, **kw):     # the fetch's search since AUTO-38
            seen.extend(queries)
            return [], ""

        freesound.best, freesound.ranked = fake_best, fake_ranked
        try:
            with tempfile.TemporaryDirectory() as root:
                data = {"cues": [cue(name="C arrives", search_description="bell tone")]}
                sound.fetch(root, data, token="t", say=lambda m: None)
        finally:
            freesound.best, freesound.ranked = real_best, real_ranked
        self.assertEqual(seen, ["bell tone"])


if __name__ == "__main__":
    unittest.main()
