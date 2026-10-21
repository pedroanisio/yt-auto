"""Every text node is pinned to a licensed font file the repo ships.

The treatments ask for faces by family name; a host that lacks them used to
fall back to DejaVu in silence. The build now resolves family, weight and
style to a file under ytstudio/fonts, adds the font asset, ships the file with
the project, and fails on a family it has no file for.
"""

import hashlib
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from ytstudio import build, fonts
from ytstudio.core import Idea
from ytstudio.scene_sdk import sdk
from ytstudio.script import Section, Shot

INCLUDED = """<?xml version="1.0"?>
<scene version="1.1">
  <project width="1920" height="1080" fps="30" duration="4"/>
  <styles>
    <textStyle id="lead" font="Inter" weight="500" size="64"/>
  </styles>
  <symbols>
    <symbol id="fx-1-1">
      <layer id="l1">
        %s
      </layer>
    </symbol>
  </symbols>
  <composition/>
</scene>
"""


def _idea():
    return Idea.from_raw({"title": "A film", "genre": "explainer",
                          "description": "a premise"})


def _sections():
    return [Section(heading="Open", shots=[
        Shot(label="1.1", n=1, visual="a thing appears", narration="Hello",
             seconds=4, at=0)])]


class Catalogue(unittest.TestCase):
    def test_every_shipped_file_is_a_face_with_a_family_weight_and_style(self):
        shipped = sorted(p for p in fonts.FONT_DIR.glob("*/*.ttf"))
        self.assertTrue(shipped)
        self.assertEqual(sorted(f.path for f in fonts.faces()), shipped)

    def test_inter_medium_is_the_medium_file(self):
        face, _ = fonts.resolve("Inter", "500", "normal")
        self.assertEqual(face.path.name, "Inter-Medium.ttf")
        self.assertEqual((face.family, face.weight, face.style),
                         ("Inter", 500, "normal"))

    def test_weight_words_and_italics(self):
        self.assertEqual(fonts.resolve("Lora", "bold", "italic")[0].path.name,
                         "Lora-BoldItalic.ttf")
        self.assertEqual(fonts.resolve("Inter Display", "900", "normal")[0]
                         .path.name, "InterDisplay-Black.ttf")

    def test_a_missing_weight_takes_the_nearest_and_says_so(self):
        face, notes = fonts.resolve("Liberation Sans", "600", "normal")
        self.assertEqual(face.path.name, "LiberationSans-Bold.ttf")
        self.assertTrue(notes)

    def test_a_family_list_takes_the_first_one_that_resolves(self):
        face, _ = fonts.resolve('"Nope Sans", Inter, sans-serif', "400", "normal")
        self.assertEqual(face.family, "Inter")

    def test_an_unknown_family_is_an_error(self):
        with self.assertRaises(fonts.FontError) as raised:
            fonts.resolve("JetBrains Mono", "400", "normal")
        self.assertIn("JetBrains Mono", str(raised.exception))

    def test_a_family_name_with_a_number_or_capitals_keeps_its_real_name(self):
        face, notes = fonts.resolve("Source Sans 3", "600", "normal")
        self.assertEqual((face.family, face.weight, face.path.name),
                         ("Source Sans 3", 600, "SourceSans3-SemiBold.ttf"))
        self.assertEqual(notes, [])
        face, _ = fonts.resolve("STIX Two Text", "400", "normal")
        self.assertEqual((face.family, face.path.name),
                         ("STIX Two Text", "STIXTwoText-Regular.ttf"))
        self.assertIn("Source Sans 3", fonts.families())
        self.assertIn("STIX Two Text", fonts.families())

    def test_every_shipped_family_has_a_licence_file(self):
        for face in fonts.faces():
            self.assertTrue(face.licence_file, face.path)
            self.assertTrue((fonts.FONT_DIR.parent.parent / face.licence_file).is_file(),
                            face.licence_file)

    def test_an_explicit_alias_resolves_and_is_noted(self):
        face, notes = fonts.resolve("Helvetica", "400", "normal")
        self.assertEqual(face.family, "Liberation Sans")
        self.assertTrue(any("Helvetica" in n for n in notes))


class PinnedScene(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def scene(self):
        S = sdk()
        scene = S.Scene(version="auto")
        scene.add_project(width=1920, height=1080, fps="30", duration=4,
                          background="#000000")
        assets = scene.add_assets()
        scene.add_composition()
        return scene, assets

    def test_a_text_asset_gets_a_font_asset_and_the_file_ships(self):
        scene, assets = self.scene()
        assets.add_text(id="t1", text="Hello", width=800, height=200, size=48,
                        font="Inter", weight="500")
        pinner = fonts.Pinner(self.root)
        pinner.pin_scene(scene)
        data = scene.to_bytes(layout="pretty")
        root = ElementTree.fromstring(data)
        font = root.find("assets/font")
        self.assertEqual((font.get("family"), font.get("weight")), ("Inter", "500"))
        shipped = self.root / font.get("src")
        self.assertEqual(shipped.name, "Inter-Medium.ttf")
        self.assertEqual(shipped.parent, self.root / "fonts")
        self.assertEqual(font.get("sha256"),
                         hashlib.sha256(shipped.read_bytes()).hexdigest())
        self.assertIn("OFL", font.get("license"))
        self.assertEqual(root.find("assets/text").get("fontAsset"), font.get("id"))
        self.assertEqual(pinner.errors, [])
        errors = [str(f) for f in sdk().read(data).check()
                  if str(f).startswith("error")]
        self.assertEqual(errors, [])

    def test_a_text_with_no_font_is_pinned_to_the_default(self):
        scene, assets = self.scene()
        assets.add_text(id="t1", text="Hello", width=800, height=200, size=48)
        pinner = fonts.Pinner(self.root)
        pinner.pin_scene(scene)
        root = ElementTree.fromstring(scene.to_bytes(layout="pretty"))
        self.assertEqual(root.find("assets/font").get("family"), fonts.DEFAULT_FAMILY)

    def test_an_unresolvable_family_is_an_error_and_ships_nothing(self):
        scene, assets = self.scene()
        assets.add_text(id="t1", text="Hello", width=800, height=200, size=48,
                        font="IBM Plex Sans")
        pinner = fonts.Pinner(self.root)
        pinner.pin_scene(scene)
        self.assertTrue(any(e.startswith("error") and "IBM Plex Sans" in e
                            for e in pinner.errors), pinner.errors)

    def test_a_3d_text_object_names_the_family_of_a_font_asset(self):
        scene, assets = self.scene()
        comp = scene.find("composition")
        comp.add_object3D(id="o1", primitive="text", text="Hi", font="Helvetica")
        pinner = fonts.Pinner(self.root)
        pinner.pin_scene(scene)
        root = ElementTree.fromstring(scene.to_bytes(layout="pretty"))
        self.assertEqual(root.find(".//object3D").get("font"), "Liberation Sans")
        self.assertEqual(root.find("assets/font").get("family"), "Liberation Sans")


class InBuild(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "vfx").mkdir()
        self.authored = self.root / "vfx" / "open.scene.xml"

    def tearDown(self):
        self._tmp.cleanup()

    def build(self, layer_body):
        self.authored.write_text(INCLUDED % layer_body)
        by_label = {"1.1": {"file": "vfx/open.scene.xml", "symbol": "fx-1-1"}}
        return build.SceneBuilder(
            _idea(), _sections(), self.root, audio=False, verbose=False,
            vfx={"scene": {"by_label": by_label}}).build()

    def test_inter_medium_reaches_the_scene_and_the_project(self):
        data, manifest = self.build(
            '<text id="a" text="Hi" width="400" height="100" size="40" '
            'font="Inter" weight="500"/>')
        copy = ElementTree.parse(self.root / "vfx" / "open.include.scene.xml")
        node = copy.getroot().find(".//text")
        self.assertEqual((self.root / "vfx" / node.get("fontFile")).resolve(),
                         (self.root / "fonts" / "Inter-Medium.ttf").resolve())
        self.assertTrue((self.root / "fonts" / "Inter-Medium.ttf").is_file())
        # the hoisted text style is pinned in the scene itself
        root = ElementTree.fromstring(data)
        style = root.find("styles/textStyle[@id='lead']")
        families = {f.get("id"): f for f in root.findall("assets/font")}
        self.assertEqual(families[style.get("fontAsset")].get("family"), "Inter")
        self.assertEqual(families[style.get("fontAsset")].get("weight"), "500")
        self.assertFalse([f for f in manifest["findings"] if f.startswith("error")],
                         manifest["findings"])
        self.assertTrue(manifest["fonts"]["pinned"])

    def test_a_family_with_no_file_fails_the_build(self):
        _, manifest = self.build(
            '<text id="a" text="Hi" width="400" height="100" size="40" '
            'font="JetBrains Mono"/>')
        errors = [f for f in manifest["findings"] if f.startswith("error")]
        self.assertTrue(any("JetBrains Mono" in f for f in errors), errors)

    def test_text_that_uses_a_style_keeps_the_style(self):
        self.build('<text id="a" text="Hi" width="400" height="100" size="40" '
                   'style="lead"/>')
        copy = ElementTree.parse(self.root / "vfx" / "open.include.scene.xml")
        self.assertIsNone(copy.getroot().find(".//text").get("fontFile"))


if __name__ == "__main__":
    unittest.main()
