"""`yt sound --fetch`: the cue sheet becomes files the build can place.

Freesound is faked at its HTTP seams. What the tests pin is the contract with
`yt build`: a filled cue carries `file`, `licence`, `credit`, `source_url` and
`duration`; a cue nothing matched carries no `file` and is listed under
`unfilled` with the queries tried; silence is never searched; and a sound id
already on disk is never downloaded twice.
"""

import json
import tempfile
import unittest
import urllib.parse
from dataclasses import fields
from pathlib import Path

from ytstudio import freesound, sound
from ytstudio.core import Idea


def row(sound_id: int, name: str, duration: float, licence: str = "cc0") -> dict:
    url = {"cc0": "http://creativecommons.org/publicdomain/zero/1.0/",
           "by": "https://creativecommons.org/licenses/by/4.0/",
           "nc": "http://creativecommons.org/licenses/by-nc/3.0/"}[licence]
    return {"id": sound_id, "name": name, "username": f"user{sound_id}",
            "license": url, "type": "wav", "filesize": 100, "duration": duration,
            "avg_rating": 4.0, "num_ratings": 5,
            "url": f"https://freesound.org/people/user{sound_id}/sounds/{sound_id}/",
            "previews": {"preview-hq-mp3": f"https://cdn.freesound.org/p/{sound_id}-hq.mp3"}}


def cue(kind: str, cue_id: str, name: str, seconds: int, **kw) -> dict:
    base = {"kind": kind, "cue_id": cue_id, "name": name, "at": 0,
            "seconds": seconds, "purpose": "p", "description": "d",
            "source": "Freesound", "search": [], "query": ""}
    base.update(kw)
    return base


class FetchCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._json, self._bytes = freesound.fetch_json, freesound.fetch_bytes
        self.searches: list[str] = []
        self.downloads: list[str] = []
        self.hush = lambda msg: None
        #: query text -> the rows Freesound would return for it
        self.catalogue: dict[str, list[dict]] = {}

        def fake_json(url, **kw):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            query = params["query"][0]
            self.searches.append(query)
            rows = self.catalogue.get(query, [])
            return {"count": len(rows), "results": rows}

        def fake_bytes(url, **kw):
            self.downloads.append(url)
            return b"ID3" + url.encode()
        freesound.fetch_json, freesound.fetch_bytes = fake_json, fake_bytes

    def tearDown(self):
        freesound.fetch_json, freesound.fetch_bytes = self._json, self._bytes
        self._tmp.cleanup()

    def data(self, *cues: dict) -> dict:
        return {"idea": {"title": "t"}, "runtime": 20, "cues": list(cues)}


class WriteBack(FetchCase):
    def test_a_filled_cue_carries_the_build_contract_fields(self):
        self.catalogue["paper slip"] = [row(10, "Paper Slip.wav", 1.5, "by")]
        data = self.data(cue("sfx", "S1", "Release: finger-paper slip", 1,
                             query="paper slip"))
        summary = sound.fetch(self.root, data, token="t", say=self.hush)
        c = data["cues"][0]
        self.assertEqual(c["file"], "sound/01-release-finger-paper-slip.mp3")
        self.assertEqual(c["licence"], "CC-BY 4.0")
        self.assertIn("user10", c["credit"])
        self.assertIn("https://freesound.org/people/user10/sounds/10/", c["credit"])
        self.assertEqual(c["source_url"], "https://freesound.org/people/user10/sounds/10/")
        self.assertEqual(c["duration"], 1.5)
        self.assertEqual(c["freesound_id"], 10)
        self.assertEqual(c["query_used"], "paper slip")
        self.assertTrue((self.root / c["file"]).is_file())
        self.assertEqual(summary["filled"], 1)
        self.assertEqual(summary["bytes"], (self.root / c["file"]).stat().st_size)
        self.assertEqual(data["unfilled"], [])
        self.assertIn("preview", summary["quality"])     # says it is the hq preview

    def test_the_file_number_is_the_cue_position_so_names_never_collide(self):
        self.catalogue["hum"] = [row(1, "Hum.wav", 20.0)]
        self.catalogue["tap"] = [row(2, "Tap.wav", 1.0)]
        data = self.data(cue("ambience", "A1", "Room tone", 15, query="hum"),
                         cue("silence", "Q1", "Drop", 2),
                         cue("sfx", "S1", "Room tone", 1, query="tap"))
        sound.fetch(self.root, data, token="t", say=self.hush)
        self.assertEqual(data["cues"][0]["file"], "sound/01-room-tone.mp3")
        self.assertEqual(data["cues"][2]["file"], "sound/03-room-tone.mp3")

    def test_silence_is_never_searched(self):
        data = self.data(cue("silence", "Q1", "Everything drops", 2))
        summary = sound.fetch(self.root, data, token="t", say=self.hush)
        self.assertEqual(self.searches, [])
        self.assertNotIn("file", data["cues"][0])
        self.assertEqual(data["unfilled"], [])
        self.assertEqual(summary["skipped"], ["Q1"])


class Queries(FetchCase):
    def test_the_model_query_is_tried_then_search_terms_and_never_the_name(self):
        c = cue("sfx", "S1", "Paper flutter following angular velocity", 9,
                query="paper flutter", search=["page flip wind", "paper rustle"])
        self.assertEqual(sound.cue_queries(c),
                         ["paper flutter", "page flip wind", "paper rustle"])

    def test_a_name_alone_gives_no_query(self):
        c = cue("sfx", "S1", "Release: finger-paper slip and air puff", 1)
        self.assertEqual(sound.cue_queries(c), [])

    def test_the_layers_name_the_sound_and_the_name_is_not_tried(self):
        c = cue("ambience", "A1", "Midday room tone (continuous bed)", 15,
                search=["hvac hum"],
                layers=["room tone 60-120 Hz HVAC", "air hiss, barely there"])
        self.assertEqual(sound.cue_queries(c),
                         ["hvac hum", "room tone hvac", "air hiss"])

    def test_queries_are_deduplicated(self):
        c = cue("sfx", "S1", "Tap", 1, query="tap", search=["tap", "Tap"])
        self.assertEqual(sound.cue_queries(c), ["tap"])

    def test_an_unfilled_cue_lists_every_query_tried_and_keeps_no_file(self):
        data = self.data(cue("music", "M1", "Cello fifth pad", 9,
                             query="cello drone", search=["cello pad"]))
        summary = sound.fetch(self.root, data, token="t", say=self.hush)
        self.assertNotIn("file", data["cues"][0])
        self.assertEqual(summary["filled"], 0)
        self.assertEqual(len(data["unfilled"]), 1)
        miss = data["unfilled"][0]
        self.assertEqual(miss["cue_id"], "M1")
        self.assertEqual(miss["kind"], "music")
        self.assertEqual(miss["queries"][:2], ["cello drone", "cello pad"])
        self.assertTrue(miss["reason"])

    def test_the_first_query_with_an_allowed_hit_wins(self):
        self.catalogue["cello drone"] = [row(5, "Cello NC.wav", 10.0, "nc")]
        self.catalogue["cello pad"] = [row(6, "Cello Pad.wav", 12.0, "cc0")]
        data = self.data(cue("music", "M1", "Cello fifth pad", 9,
                             query="cello drone", search=["cello pad"]))
        sound.fetch(self.root, data, token="t", say=self.hush)
        self.assertEqual(data["cues"][0]["freesound_id"], 6)
        self.assertEqual(data["cues"][0]["query_used"], "cello pad")
        self.assertEqual(data["cues"][0]["licence"], "CC0 1.0")


class Caching(FetchCase):
    def test_rerunning_does_not_download_the_same_sound_again(self):
        self.catalogue["hum"] = [row(1, "Hum.wav", 20.0)]
        data = self.data(cue("ambience", "A1", "Room tone", 15, query="hum"))
        first = sound.fetch(self.root, data, token="t", say=self.hush)
        again = sound.fetch(self.root, data, token="t", say=self.hush)
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(first["bytes"], again["bytes"])
        self.assertEqual(data["cues"][0]["file"], "sound/01-room-tone.mp3")

    def test_a_sound_id_fetched_for_another_cue_sheet_is_reused(self):
        self.catalogue["hum"] = [row(1, "Hum.wav", 20.0)]
        sound.fetch(self.root, self.data(cue("ambience", "A1", "Room tone", 15,
                                             query="hum")), token="t", say=self.hush)
        # the sheet was spotted again: same sound, a different cue position
        data = self.data(cue("sfx", "S1", "Click", 1),
                         cue("ambience", "A1", "Room bed", 15, query="hum"))
        sound.fetch(self.root, data, token="t", say=self.hush)
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(data["cues"][1]["file"], "sound/01-room-tone.mp3")
        self.assertEqual(data["cues"][1]["freesound_id"], 1)

    def test_a_cue_already_filled_whose_file_exists_is_not_searched(self):
        (self.root / "sound").mkdir()
        (self.root / "sound" / "01-room-tone.mp3").write_bytes(b"ID3x")
        data = self.data(cue("ambience", "A1", "Room tone", 15, query="hum",
                             file="sound/01-room-tone.mp3", freesound_id=1,
                             licence="CC0 1.0", credit="c", source_url="u",
                             duration=20.0))
        summary = sound.fetch(self.root, data, token="t", say=self.hush)
        self.assertEqual(self.searches, [])
        self.assertEqual(summary["filled"], 1)
        self.assertEqual(summary["cached"], 1)


class NoToken(FetchCase):
    def test_without_a_token_nothing_is_fetched_and_only_the_name_is_reported(self):
        data = self.data(cue("sfx", "S1", "Tap", 1, query="tap"))
        summary = sound.fetch(self.root, data, token="", say=self.hush)
        self.assertEqual(self.searches, [])
        self.assertEqual(summary["filled"], 0)
        self.assertIn("FREESOUND_CLIENT_SECRET", summary["error"])
        self.assertEqual(data["unfilled"][0]["cue_id"], "S1")


class Schema(unittest.TestCase):
    def test_the_spotting_schema_asks_for_a_query_per_cue(self):
        props = sound.CUES_SCHEMA["properties"]["cues"]["items"]["properties"]
        self.assertIn("query", props)

    def test_a_cue_keeps_its_query_and_its_fetched_fields(self):
        c = sound.Cue.from_raw({"kind": "sfx", "name": "n", "at": 1, "seconds": 2,
                                "purpose": "p", "description": "d", "source": "s",
                                "query": " paper slip "})
        self.assertEqual(c.query, "paper slip")
        names = {f.name for f in fields(sound.Cue)}
        for name in ("file", "licence", "credit", "source_url", "duration"):
            self.assertIn(name, names)

    def test_the_spotting_prompt_asks_for_the_query(self):
        designer = sound.SoundDesigner(Idea(title="t", genre="g", description="d"), verbose=False)
        asked = {}
        designer._ask = lambda prompt, schema: asked.setdefault("prompt", prompt) and {"cues": []}
        designer.spot_whole()
        self.assertIn("search_description", asked["prompt"])


class RuntimeFloor(unittest.TestCase):
    def test_a_short_film_keeps_its_own_runtime(self):
        self.assertEqual(sound.SoundDesigner(Idea(title="t", genre="g", description="d"), runtime=15,
                                             verbose=False).runtime, 15)
        self.assertEqual(sound.SoundDesigner(Idea(title="t", genre="g", description="d"), runtime=3,
                                             verbose=False).runtime, 10)


class Reload(FetchCase):
    def test_fetch_from_file_rewrites_the_cue_sheet_in_place(self):
        self.catalogue["tap"] = [row(2, "Tap.wav", 1.0)]
        path = self.root / "sound.json"
        path.write_text(json.dumps(self.data(cue("sfx", "S1", "Tap", 1, query="tap"))))
        summary = sound.fetch_file(path, token="t", say=self.hush)
        saved = json.loads(path.read_text())
        self.assertEqual(saved["cues"][0]["file"], "sound/01-tap.mp3")
        self.assertEqual(saved["fetch"]["filled"], 1)
        self.assertEqual(summary["filled"], 1)


if __name__ == "__main__":
    unittest.main()
