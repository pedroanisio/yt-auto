"""`yt props` fetches CC0 3D props (models, HDRIs, textures) from Poly Haven.

Nothing here touches the network: the two HTTP seams, `props.fetch_json` and
`props.fetch_bytes`, are replaced by fakes that serve a small catalogue. The
fixture's subject nouns belong to the fixture; the tooling knows none.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import cli, props, workspace  # noqa: E402

DL = "https://dl.example/"

LISTING = {
    "models": {
        "metal_office_desk": {
            "type": 2, "name": "Metal Office Desk",
            "tags": ["old", "vintage", "metal", "scratched"],
            "categories": ["furniture", "table", "office"],
            "authors": {"Rico Cilliers": "All"},
            "description": "Free CC0 model of a worn metal office desk."},
        "hand_plane_no4": {
            "type": 2, "name": "Hand Plane No4",
            "tags": ["vintage", "hand tool", "carpentry", "wood"],
            "categories": ["tools"], "authors": {"James Ray Cock": "All"},
            "description": "A woodworking hand plane."},
        "arm_chair_01": {
            "type": 2, "name": "Arm Chair 01", "tags": ["chair", "gothic"],
            "categories": ["furniture", "seating"],
            "authors": {"Kirill Sannikov": "All"}, "description": "An armchair."},
    },
    "hdris": {
        "studio_small_03": {
            "type": 0, "name": "Studio Small 03",
            "tags": ["studio", "softbox", "photo studio"],
            "categories": ["indoor", "studio", "artificial light"],
            "authors": {"Sergej Majboroda": "All"},
            "description": "A small photo studio with softboxes."},
        "aarfontein_dawn_2": {
            "type": 0, "name": "Aarfontein Dawn 2", "tags": ["grass", "hilltop"],
            "categories": ["outdoor", "nature"], "authors": {"Greg Zaal": "All"},
            "description": "Rocky grassland at dawn."},
    },
    "textures": {
        "oak_veneer_01": {
            "type": 1, "name": "Oak Veneer 01",
            "tags": ["wood", "oak", "desk", "veneer"], "categories": ["wood"],
            "authors": {"Rob Tuytel": "All"}, "description": "Oak veneer planks."},
    },
}

FILES = {
    "metal_office_desk": {
        "blend": {"2k": {"blend": {"url": DL + "desk.blend", "size": 1}}},
        "gltf": {
            "1k": {"gltf": {"url": DL + "desk_1k.gltf", "size": 10, "md5": "a",
                            "include": {"desk.bin": {"url": DL + "desk.bin", "size": 7}}}},
            "2k": {"gltf": {"url": DL + "desk_2k.gltf", "size": 10, "md5": "b",
                            "include": {
                                "textures/metal_office_desk_diff_2k.jpg":
                                    {"url": DL + "desk_diff_2k.jpg", "size": 5},
                                "metal_office_desk.bin":
                                    {"url": DL + "desk.bin", "size": 7}}}},
            "4k": {"gltf": {"url": DL + "desk_4k.gltf", "size": 10, "md5": "c",
                            "include": {}}},
        },
    },
    "hand_plane_no4": {"gltf": {"2k": {"gltf": {"url": DL + "plane_2k.gltf",
                                                "size": 3, "include": {}}}}},
    "arm_chair_01": {"gltf": {"1k": {"gltf": {"url": DL + "chair_1k.gltf",
                                              "size": 3, "include": {}}},
                              "4k": {"gltf": {"url": DL + "chair_4k.gltf",
                                              "size": 3, "include": {}}}}},
    "studio_small_03": {
        "hdri": {"1k": {"hdr": {"url": DL + "studio_1k.hdr", "size": 2},
                        "exr": {"url": DL + "studio_1k.exr", "size": 4}},
                 "2k": {"hdr": {"url": DL + "studio_2k.hdr", "size": 2},
                        "exr": {"url": DL + "studio_2k.exr", "size": 4}}},
        "tonemapped": {"url": DL + "studio.jpg"},
    },
    "aarfontein_dawn_2": {"hdri": {"4k": {"exr": {"url": DL + "dawn_4k.exr", "size": 4}}}},
    "oak_veneer_01": {
        "Diffuse": {"2k": {"jpg": {"url": DL + "oak_diff_2k.jpg", "size": 3},
                           "png": {"url": DL + "oak_diff_2k.png", "size": 9}}},
        "nor_gl": {"2k": {"jpg": {"url": DL + "oak_nor_gl_2k.jpg", "size": 3}}},
        "Rough": {"2k": {"png": {"url": DL + "oak_rough_2k.png", "size": 3}}},
        "blend": {"2k": {"blend": {"url": DL + "oak.blend", "size": 1}}},
    },
}


class FakeNet:
    """Serves the fixture through the two seams and counts what was asked."""

    def __init__(self):
        self.json_urls: list[str] = []
        self.byte_urls: list[str] = []

    def fetch_json(self, url, **kw):
        self.json_urls.append(url)
        if "/assets?" in url:
            kind = url.rsplit("t=", 1)[1]
            return LISTING[kind]
        if "/files/" in url:
            return FILES[url.rsplit("/", 1)[1]]
        raise AssertionError(f"unexpected url {url}")

    def fetch_bytes(self, url, **kw):
        self.byte_urls.append(url)
        # Deliberately not JSON, not glTF, not an image: the stage must never
        # look inside what it downloaded.
        return b"not a parseable file {" + url.encode()


class PropsCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name)
        self.root = self.work / "a-film"
        self.root.mkdir()
        self.net = FakeNet()
        self._seams = (props.fetch_json, props.fetch_bytes)
        props.fetch_json = self.net.fetch_json
        props.fetch_bytes = self.net.fetch_bytes

    def tearDown(self):
        props.fetch_json, props.fetch_bytes = self._seams
        self._tmp.cleanup()

    def catalogue(self, **kw):
        return props.Catalogue(self.work / ".props-cache", **kw)


class Ranking(unittest.TestCase):
    def test_ranks_by_tag_and_name_match(self):
        hits = props.rank(LISTING["models"], "office desk", limit=3)
        self.assertEqual(hits[0]["id"], "metal_office_desk")
        self.assertNotIn("hand_plane_no4", [h["id"] for h in hits])
        self.assertNotIn("arm_chair_01", [h["id"] for h in hits])

    def test_a_tag_match_counts_without_a_name_match(self):
        hits = props.rank(LISTING["textures"], "wooden desk", limit=3)
        self.assertEqual([h["id"] for h in hits], ["oak_veneer_01"])

    def test_nothing_matches_nothing(self):
        self.assertEqual(props.rank(LISTING["hdris"], "zebra", limit=3), [])

    def test_limit_caps_the_hits(self):
        hits = props.rank(LISTING["models"], "vintage", limit=1)
        self.assertEqual(len(hits), 1)


class Files(unittest.TestCase):
    def test_a_model_is_the_gltf_and_its_includes_at_2k(self):
        main, files = props.pick_files("model", FILES["metal_office_desk"])
        self.assertEqual(main, "desk_2k.gltf")
        paths = {f["path"] for f in files}
        self.assertEqual(paths, {"desk_2k.gltf", "textures/metal_office_desk_diff_2k.jpg",
                                 "metal_office_desk.bin"})

    def test_an_hdri_is_the_2k_hdr(self):
        main, files = props.pick_files("hdri", FILES["studio_small_03"])
        self.assertEqual(main, "studio_2k.hdr")
        self.assertEqual([f["url"] for f in files], [DL + "studio_2k.hdr"])

    def test_an_hdri_without_hdr_takes_the_exr(self):
        main, files = props.pick_files("hdri", FILES["aarfontein_dawn_2"])
        self.assertEqual(main, "dawn_4k.exr")

    def test_a_texture_is_its_2k_jpg_maps_with_the_diffuse_first(self):
        main, files = props.pick_files("texture", FILES["oak_veneer_01"])
        self.assertEqual(main, "oak_diff_2k.jpg")
        self.assertEqual([f["path"] for f in files],
                         ["oak_diff_2k.jpg", "oak_nor_gl_2k.jpg", "oak_rough_2k.png"])

    def test_resolution_falls_back_to_the_nearest_below_then_above(self):
        main, _ = props.pick_files("model", FILES["arm_chair_01"])
        self.assertEqual(main, "chair_1k.gltf")
        main, _ = props.pick_files("model", {"gltf": {"8k": {"gltf": {
            "url": DL + "x_8k.gltf", "size": 1, "include": {}}}}})
        self.assertEqual(main, "x_8k.gltf")

    def test_no_usable_format_is_nothing(self):
        self.assertEqual(props.pick_files("model", {"blend": {"2k": {}}}), ("", []))


class Gathering(PropsCase):
    def test_downloads_into_props_kind_id_and_writes_the_bytes_verbatim(self):
        items = props.gather(self.root, ["office desk"], kinds=("model",), limit=2,
                             catalogue=self.catalogue(), verbose=False)
        self.assertEqual([i["id"] for i in items], ["metal_office_desk"])
        item = items[0]
        self.assertEqual(item["file"], "props/model/metal_office_desk/desk_2k.gltf")
        for rel in item["files"]:
            path = self.root / rel
            self.assertTrue(path.is_file(), rel)
            self.assertTrue(path.read_bytes().startswith(b"not a parseable file {"))
        self.assertEqual(sorted(item["files"]), sorted([
            "props/model/metal_office_desk/desk_2k.gltf",
            "props/model/metal_office_desk/textures/metal_office_desk_diff_2k.jpg",
            "props/model/metal_office_desk/metal_office_desk.bin"]))

    def test_the_manifest_carries_the_agreed_fields(self):
        items = props.gather(self.root, ["office desk", "studio"], kinds=("model", "hdri"),
                             limit=1, catalogue=self.catalogue(), verbose=False)
        path = props.write_manifest(self.root, items, phrases=["office desk", "studio"])
        self.assertEqual(path, self.root / "props.json")
        data = json.loads(path.read_text())
        self.assertEqual({i["id"] for i in data["items"]},
                         {"metal_office_desk", "studio_small_03"})
        for item in data["items"]:
            for key in ("id", "kind", "file", "files", "licence", "credit", "about",
                        "source_url"):
                self.assertIn(key, item)
            self.assertIn(item["kind"], ("model", "hdri", "texture"))
            self.assertIn("CC0", item["licence"])
            self.assertIn("Poly Haven", item["credit"])
            self.assertEqual(item["source_url"], f"https://polyhaven.com/a/{item['id']}")
            self.assertFalse(item["file"].startswith("/"))
        desk = next(i for i in data["items"] if i["id"] == "metal_office_desk")
        self.assertIn("Rico Cilliers", desk["credit"])
        self.assertIn("Metal Office Desk", desk["about"])
        self.assertIn("office desk", data["phrases"])

    def test_a_partial_match_says_so_in_about(self):
        items = props.gather(self.root, ["paper desk"], kinds=("texture",), limit=1,
                             catalogue=self.catalogue(), verbose=False)
        self.assertEqual(items[0]["id"], "oak_veneer_01")
        self.assertIn("matches only 'desk' of 'paper desk'", items[0]["about"])
        items = props.gather(self.root, ["office desk"], kinds=("model",), limit=1,
                             catalogue=self.catalogue(), verbose=False)
        self.assertNotIn("matches only", items[0]["about"])

    def test_kinds_and_limit_are_honoured(self):
        items = props.gather(self.root, ["studio", "vintage"], kinds=("hdri",), limit=1,
                             catalogue=self.catalogue(), verbose=False)
        self.assertEqual([i["kind"] for i in items], ["hdri"])
        self.assertEqual(items[0]["id"], "studio_small_03")
        self.assertNotIn(props.API + "/assets?t=models", self.net.json_urls)

    def test_a_second_run_downloads_nothing_and_redownload_fetches_again(self):
        props.gather(self.root, ["office desk"], kinds=("model",), limit=1,
                     catalogue=self.catalogue(), verbose=False)
        fetched = len(self.net.byte_urls)
        self.assertEqual(fetched, 3)
        props.gather(self.root, ["office desk"], kinds=("model",), limit=1,
                     catalogue=self.catalogue(), verbose=False)
        self.assertEqual(len(self.net.byte_urls), fetched)
        props.gather(self.root, ["office desk"], kinds=("model",), limit=1,
                     catalogue=self.catalogue(), redownload=True, verbose=False)
        self.assertEqual(len(self.net.byte_urls), fetched * 2)

    def test_the_catalogue_is_cached_on_disk(self):
        self.catalogue().listing("model")
        self.catalogue().listing("model")
        self.assertEqual(self.net.json_urls.count(props.API + "/assets?t=models"), 1)
        self.catalogue(fresh=True).listing("model")
        self.assertEqual(self.net.json_urls.count(props.API + "/assets?t=models"), 2)

    def test_an_asset_is_listed_once_under_its_best_phrase(self):
        items = props.gather(self.root, ["office desk", "metal desk"], kinds=("model",),
                             limit=2, catalogue=self.catalogue(), verbose=False)
        self.assertEqual([i["id"] for i in items], ["metal_office_desk"])


class DerivedPhrases(PropsCase):
    def test_phrases_come_from_the_script_assets_and_the_styles_when_none_are_given(self):
        (self.root / "script.json").write_text(json.dumps({"sections": [{"shots": [
            {"label": "1.1", "visual": "a room", "asset": "To be made: an oak desk "
             "with brass pulls, oak veneer on the top"},
            {"label": "1.2", "visual": "a chair by the desk", "asset": ""}]}]}))
        (self.root / "styles.json").write_text(json.dumps({"styles": [
            {"name": "3d animation", "shots": ["a small photo studio key light"],
             "shows_well": "the office at dusk"}]}))
        vocabulary = self.catalogue().vocabulary(("model", "hdri", "texture"))
        phrases = props.derive_phrases(props.project_text(self.root), vocabulary)
        self.assertIn("oak", phrases)
        self.assertIn("desk", phrases)
        self.assertIn("photo studio", phrases)      # a multi-word tag survives whole
        self.assertNotIn("brass", phrases)          # nothing in the catalogue has it
        self.assertNotIn("a", phrases)

    def test_no_project_text_means_no_phrases(self):
        self.assertEqual(props.derive_phrases("", {"desk"}), [])


class Stage(PropsCase):
    def test_props_is_a_known_artifact(self):
        self.assertEqual(workspace.ARTIFACTS["props"], "props.json")
        self.assertTrue(workspace.Project(self.root).path("props").name == "props.json")

    def test_the_stage_runs_from_the_cli_into_the_project(self):
        parser = cli.build_parser()
        args = parser.parse_args(["props", "--project", str(self.root), "--quiet",
                                  "--limit", "1", "--kinds", "model,texture",
                                  "office desk", "oak"])
        self.assertEqual(args.run(args), 0)
        data = json.loads((self.root / "props.json").read_text())
        kinds = {i["kind"] for i in data["items"]}
        self.assertEqual(kinds, {"model", "texture"})
        self.assertTrue((self.root / "props" / "texture" / "oak_veneer_01").is_dir())

    def test_a_project_with_nothing_to_search_says_so(self):
        parser = cli.build_parser()
        args = parser.parse_args(["props", "--project", str(self.root), "--quiet"])
        self.assertEqual(args.run(args), 1)
        self.assertFalse((self.root / "props.json").exists())


if __name__ == "__main__":
    unittest.main()
