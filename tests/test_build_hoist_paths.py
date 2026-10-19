"""A hoisted element's file paths point where the scene is.

`yt build` lifts the lights and text styles out of an included document into
the scene, because an <include> carries neither. Their file-valued attributes
(a dome light's environment map, a light's IES profile, a text style's font)
were written relative to the authored file under vfx/vNN/; copied verbatim
into scene.xml at the project root they pointed outside the project, and the
encode failed with error A01 (file does not exist). The build rebases them to
the scene's directory, and leaves absolute paths and URLs alone. Paints carry
no file and tokens are values, so nothing else needs rebasing.
"""

import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from ytstudio import build
from ytstudio.core import Idea
from ytstudio.script import Section, Shot

AUTHORED = """<?xml version="1.0"?>
<scene version="1.3">
  <project width="1920" height="1080" fps="30" duration="4"/>
  <styles>
    <textStyle id="caption-type" fontFamily="Inter" fontSize="40" fontFile="../../fonts/inter.ttf"/>
  </styles>
  <symbols>
    <symbol id="fx-1-1">
      <shape id="s1" shape="rect" width="100" height="100" fill="#FFFFFF"/>
    </symbol>
  </symbols>
  <composition/>
  <lights>
    <light id="env" type="dome" environment="../../props/hdri/studio/studio_2k.hdr" intensity="0.5"/>
    <light id="spot" type="spot" ies="/opt/ies/profile.ies" intensity="4"/>
    <light id="sky" type="dome" environment="https://example.org/sky.hdr" intensity="1"/>
  </lights>
</scene>
"""


class Rebase(unittest.TestCase):
    def test_a_relative_path_is_rewritten_from_the_file_to_the_scene(self):
        self.assertEqual(build.rebase("../../props/hdri/a.hdr", Path("/p/vfx/v03"), Path("/p")),
                         "props/hdri/a.hdr")
        self.assertEqual(build.rebase("maps/a.png", Path("/p/vfx/v03"), Path("/p")),
                         "vfx/v03/maps/a.png")

    def test_absolute_paths_urls_and_empty_values_are_left_alone(self):
        for value in ("/opt/ies/a.ies", "https://example.org/a.hdr", "data:,x", ""):
            self.assertEqual(build.rebase(value, Path("/p/vfx/v03"), Path("/p")), value)

    def test_the_file_valued_attributes_are_the_schemas(self):
        self.assertEqual(set(build.HOISTED_FILES["light"]), {"ies", "environment"})
        self.assertEqual(build.HOISTED_FILES["textStyle"], ("fontFile",))


class HoistedPaths(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "vfx" / "v03").mkdir(parents=True)
        self.authored = self.root / "vfx" / "v03" / "open.scene.xml"
        self.authored.write_text(AUTHORED)

    def tearDown(self):
        self._tmp.cleanup()

    def test_hoisted_rebases_lights_and_fonts_to_the_scene(self):
        info = build.hoisted(self.authored, scene_dir=self.root)
        lights = {row["id"]: row for row in info["lights"]}
        self.assertEqual(lights["env"]["environment"], "props/hdri/studio/studio_2k.hdr")
        self.assertEqual(lights["spot"]["ies"], "/opt/ies/profile.ies")
        self.assertEqual(lights["sky"]["environment"], "https://example.org/sky.hdr")
        self.assertEqual(info["text_styles"][0]["fontFile"], "fonts/inter.ttf")

    def test_without_a_scene_dir_the_attributes_are_as_authored(self):
        info = build.hoisted(self.authored)
        self.assertEqual(info["lights"][0]["environment"],
                         "../../props/hdri/studio/studio_2k.hdr")

    def test_the_built_scene_points_at_the_project_file(self):
        idea = Idea.from_raw({"title": "A film", "genre": "explainer",
                              "description": "a premise"})
        sections = [Section(heading="Open", shots=[
            Shot(label="1.1", n=1, visual="a thing", narration="Hello", seconds=4, at=0)])]
        by_label = {"1.1": {"file": "vfx/v03/open.scene.xml", "symbol": "fx-1-1"}}
        data, _ = build.SceneBuilder(idea, sections, self.root, audio=False, verbose=False,
                                     vfx={"scene": {"by_label": by_label}}).build()
        root = ElementTree.fromstring(data)
        envs = {l.get("environment") for l in root.iter("light") if l.get("type") == "dome"}
        self.assertIn("props/hdri/studio/studio_2k.hdr", envs)
        self.assertIn("https://example.org/sky.hdr", envs)
        ies = {l.get("ies") for l in root.iter("light") if l.get("ies")}
        self.assertEqual(ies, {"/opt/ies/profile.ies"})
        self.assertFalse(any((l.get("environment") or "").startswith("..")
                             for l in root.iter("light")))


if __name__ == "__main__":
    unittest.main()
