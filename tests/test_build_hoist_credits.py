"""A hoisted light keeps its animation; an image inside an include is credited.

AUTO-36: `yt build` lifts each included document's lights into the scene. A
light keyed inside its section (a rim that brightens on a cue) arrived static
with a warning. The hoist now carries the light's <animate> children, re-timed
by the include's start.

AUTO-37: the build credits the research images it places itself. An image an
author embeds inside a section's world left no credit anywhere, and the gate
blocked on the licence. The build now scans the included documents for files
under downloads/ and credits each from the download manifest; a file the
manifest does not know is an error naming it.
"""

import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from ytstudio import build
from ytstudio.core import Idea
from ytstudio.scene_sdk import sdk
from ytstudio.script import Section, Shot

AUTHORED = """<?xml version="1.0"?>
<scene version="1.1">
  <project width="1920" height="1080" fps="30" duration="4"/>
  <assets>
    <image id="plate" src="../downloads/01-plate.png" width="100" height="100"/>
  </assets>
  <symbols>
    <symbol id="fx-1-2">
      <shape id="s1" shape="rect" width="100" height="100" fill="#FFFFFF"/>
    </symbol>
  </symbols>
  <composition/>
  <lights>
    <light id="rim" type="rect-area" color="#FFD9A0" intensity="2" x="-900" width="900" height="900">
      <animate property="intensity">
        <key time="0" value="2"/>
        <key time="1.5" value="6"/>
      </animate>
    </light>
  </lights>
</scene>
"""


def _idea():
    return Idea.from_raw({"title": "A film", "genre": "explainer",
                          "description": "a premise"})


def _sections():
    return [Section(heading="Open", shots=[
        Shot(label="1.1", n=1, visual="a thing appears", narration="Hello",
             seconds=4, at=0),
        Shot(label="1.2", n=2, visual="it turns", narration="and turns",
             seconds=4, at=4)])]


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "vfx").mkdir()
        (self.root / "downloads").mkdir()
        (self.root / "raw").mkdir()
        (self.root / "downloads" / "01-plate.png").write_bytes(b"x")
        self.write_authored(AUTHORED)

    def write_authored(self, text):
        (self.root / "vfx" / "open.scene.xml").write_text(text)

    def manifest(self, rows):
        (self.root / "raw" / "08-downloads.json").write_text(json.dumps(rows))

    def build(self, dossier=None):
        by_label = {"1.2": {"file": "vfx/open.scene.xml", "symbol": "fx-1-2"}}
        return build.SceneBuilder(_idea(), _sections(), self.root, dossier=dossier,
                                  vfx={"scene": {"by_label": by_label}},
                                  audio=False, verbose=False).build()


class HoistedLightAnimation(Fixture):
    def test_hoisted_carries_the_lights_animate(self):
        info = build.hoisted(self.root / "vfx" / "open.scene.xml")
        self.assertEqual(info["findings"], [])
        self.assertIn("animate", info["lights"][0]["animate"][0])

    def test_a_key_at_section_time_t_arrives_at_include_start_plus_t(self):
        data, manifest = self.build()
        root = ElementTree.fromstring(data)
        light = root.find(".//light")
        self.assertIsNotNone(light)
        starts = {k.get("time") for k in light.iter("key")}
        # include starts at 4: the carried keys sit at 4 and 5.5
        self.assertIn("5.5", {f"{float(t):g}" for t in starts})
        values = {f"{float(k.get('time')):g}": k.get("value")
                  for a in light.iter("animate") if a.get("property") == "intensity"
                  for k in a.iter("key")}
        self.assertEqual(values.get("5.5"), "6")

    def test_no_warning_about_the_light_animation(self):
        _, manifest = self.build()
        self.assertEqual([f for f in manifest["findings"]
                          if "only its attributes" in f or "own animate" in f], [])
        errors = [f for f in manifest["findings"] if f.startswith("error")]
        self.assertEqual([e for e in errors if "light" in e], [])

    def test_the_scene_still_validates(self):
        data, _ = self.build()
        errors = [str(f) for f in sdk().read(data).check()
                  if str(f).startswith("error") and "downloads" not in str(f)
                  and "does not exist" not in str(f)]
        self.assertEqual(errors, [])


class IncludedImageCredit(Fixture):
    def test_an_image_only_in_an_include_is_credited(self):
        self.manifest([{"title": "a micrograph", "status": "ok",
                        "file": "downloads/01-plate.png", "licence": "CC BY 4.0"}])
        dossier = {"assets": [{"title": "a micrograph", "kind": "image",
                               "licence": "CC BY 4.0", "credit_line": "A. Author"}]}
        data, manifest = self.build(dossier=dossier)
        image = ElementTree.fromstring(data).find(
            ".//assets/image[@src='downloads/01-plate.png']")
        self.assertIsNotNone(image)
        self.assertEqual(image.get("license"), "CC BY 4.0")
        self.assertEqual(image.get("credit"), "A. Author")
        self.assertEqual([f for f in manifest["findings"]
                          if "01-plate" in f and f.startswith("error")], [])

    def test_a_file_with_no_manifest_entry_is_an_error_naming_it(self):
        self.manifest([])
        _, manifest = self.build(dossier={"assets": []})
        errors = [f for f in manifest["findings"]
                  if f.startswith("error") and "downloads/01-plate.png" in f]
        self.assertEqual(len(errors), 1, manifest["findings"])

    def test_a_document_without_downloads_has_no_credit_finding(self):
        self.write_authored(AUTHORED.replace("../downloads/01-plate.png",
                                             "maps/local.png"))
        _, manifest = self.build()
        self.assertEqual([f for f in manifest["findings"] if "downloads/" in f], [])


if __name__ == "__main__":
    unittest.main()
