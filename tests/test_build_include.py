"""An <include> points at a cleaned copy of the authored document.

The engine does not merge an included file's <styles>, <paints> or <lights>
(warning E12, and the gate stops on warnings), and it resolves the included
content's colour tokens from that file's own styles. So the build writes a
copy beside the authored file with the token values inlined and those three
sections removed, hoists the paints and lights into the scene, and points the
include at the copy. The authored file is never touched.
"""

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
  <styles>
    <token name="rim-white" value="#F4F8FB"/>
    <token name="edge" value="var(--rim-white)"/>
  </styles>
  <paints>
    <radialGradient id="g-x" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0" color="#FFFFFF"/>
      <stop offset="1" color="#000000"/>
    </radialGradient>
  </paints>
  <symbols>
    <symbol id="fx-1-1">
      <shape id="s1" shape="rect" width="100" height="100" fill="var(--edge)" stroke="url(#g-x)"/>
    </symbol>
  </symbols>
  <composition/>
  <lights>
    <light id="key" type="rect-area" color="#FFD9A0" intensity="8" x="-900" width="900" height="900"/>
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
                     seconds=3, at=4)])]


def _checked(data: bytes) -> ElementTree.Element:
    errors = [str(f) for f in sdk().read(data).check() if str(f).startswith("error")]
    assert not errors, errors
    return ElementTree.fromstring(data)


class CleanedInclude(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "vfx").mkdir()
        self.authored = self.root / "vfx" / "open.scene.xml"
        self.authored.write_text(AUTHORED)
        self.copy = self.root / "vfx" / "open.include.scene.xml"

    def tearDown(self):
        self._tmp.cleanup()

    def build(self, by_label=None, **kw):
        by_label = by_label or {
            "1.1": {"file": "vfx/open.scene.xml", "symbol": "fx-1-1"},
            "1.2": {"file": "vfx/open.scene.xml", "symbol": "fx-1-1"}}
        kw.setdefault("audio", False)
        kw.setdefault("verbose", False)
        return build.SceneBuilder(_idea(), _sections(), self.root,
                                  vfx={"scene": {"by_label": by_label}}, **kw).build()

    def test_the_include_points_at_a_copy_without_what_is_not_merged(self):
        data, manifest = self.build()
        root = _checked(data)
        include = root.find(".//include")
        self.assertEqual(include.get("src"), "vfx/open.include.scene.xml")
        self.assertEqual(include.get("symbol"), "fx-1-1")
        # the authored file is untouched, the copy sits beside it
        self.assertEqual(self.authored.read_text(), AUTHORED)
        self.assertTrue(self.copy.is_file())
        copied = ElementTree.parse(self.copy).getroot()
        for tag in ("styles", "paints", "lights"):
            self.assertIsNone(copied.find(tag), tag)
        # the symbol is still there, its colour token inlined (through the alias)
        shape = copied.find("symbols/symbol[@id='fx-1-1']/shape")
        self.assertEqual(shape.get("fill"), "#F4F8FB")
        self.assertEqual(shape.get("stroke"), "url(#g-x)")
        # the copy is a document the engine will validate on its own
        errors = [str(f) for f in sdk().read(self.copy.read_bytes()).check()
                  if str(f).startswith("error")]
        self.assertEqual(errors, [])
        # the paint the symbol names is now the scene's own
        self.assertIsNotNone(root.find("paints/radialGradient[@id='g-x']"))
        self.assertEqual(manifest["hoisted"]["paints"], 1)
        self.assertEqual(manifest["hoisted"]["lights"], 1)
        self.assertEqual(manifest["hoisted"]["cleaned"],
                         {"vfx/open.include.scene.xml": "vfx/open.scene.xml"})
        self.assertFalse([f for f in manifest["findings"] if f.startswith("error")])

    def test_markers_no_one_names_are_dropped_from_the_copy(self):
        # measured on the Euler film: a section's own <markers> (a note for the
        # sound) raised E12 on every include of it, and --strict refuses warnings
        self.authored.write_text(AUTHORED.replace(
            "<composition/>", '<composition/>\n  <markers><marker id="beat-drop" time="1"/></markers>'))
        data, manifest = self.build()
        self.assertIsNone(ElementTree.parse(self.copy).getroot().find("markers"))
        self.assertFalse([f for f in manifest["findings"] if "E12" in f])

    def test_a_marker_the_content_names_stays_and_is_reported(self):
        self.authored.write_text(AUTHORED.replace(
            "<composition/>", '<composition/>\n  <markers><marker id="beat-drop" time="1"/></markers>')
            .replace('<shape id="s1"', '<shape id="s1" name="beat-drop"'))
        data, manifest = self.build()
        self.assertIsNotNone(ElementTree.parse(self.copy).getroot().find("markers"))
        self.assertTrue([f for f in manifest["findings"] if "E12" in f and "beat-drop" in f])

    def test_a_symbol_the_document_lacks_is_an_error(self):
        _, manifest = self.build({"1.1": {"file": "vfx/open.scene.xml",
                                          "symbol": "fx-nope"}})
        errors = [f for f in manifest["findings"] if f.startswith("error")]
        self.assertTrue(any("fx-nope" in f for f in errors), manifest["findings"])

    def test_a_token_nothing_declares_is_reported_and_left(self):
        self.authored.write_text(AUTHORED.replace("var(--edge)", "var(--nothing)"))
        _, manifest = self.build()
        shape = ElementTree.parse(self.copy).getroot().find(".//shape")
        self.assertEqual(shape.get("fill"), "var(--nothing)")
        self.assertTrue(any("nothing" in f and f.startswith("warning")
                            for f in manifest["findings"]), manifest["findings"])

    def test_a_paint_declared_twice_keeps_the_first_and_names_a_difference(self):
        other = self.root / "vfx" / "close.scene.xml"
        other.write_text(AUTHORED.replace('color="#000000"', 'color="#111111"')
                                 .replace("fx-1-1", "fx-1-2"))
        data, manifest = self.build({
            "1.1": {"file": "vfx/open.scene.xml", "symbol": "fx-1-1"},
            "1.2": {"file": "vfx/close.scene.xml", "symbol": "fx-1-2"}})
        root = _checked(data)
        paints = root.findall("paints/radialGradient[@id='g-x']")
        self.assertEqual(len(paints), 1)
        self.assertEqual(paints[0].find("stop[@offset='1']").get("color"), "#000000")
        self.assertEqual(manifest["hoisted"]["paints"], 1)
        self.assertTrue(any("g-x" in f and "close.scene.xml" in f
                            for f in manifest["findings"]), manifest["findings"])
        self.assertEqual(len(root.findall(".//include")), 2)
        self.assertEqual(set(manifest["hoisted"]["cleaned"]),
                         {"vfx/open.include.scene.xml", "vfx/close.include.scene.xml"})

    def test_a_paint_reference_the_scene_cannot_carry_is_an_error(self):
        self.authored.write_text(AUTHORED.replace("url(#g-x)", "url(#g-none)"))
        _, manifest = self.build()
        self.assertTrue(any("g-none" in f and f.startswith("error")
                            for f in manifest["findings"]), manifest["findings"])


class SplitWorld(unittest.TestCase):
    """AUTO-54: a symbol that draws consecutive shots must not end up in two includes unnoticed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "vfx").mkdir()
        (self.root / "vfx" / "open.scene.xml").write_text(AUTHORED.replace(
            "</symbols>", '<symbol id="fx-1-2"><shape id="s2" shape="rect" width="10" '
                          'height="10" fill="#FFFFFF"/></symbol></symbols>'))

    def tearDown(self):
        self._tmp.cleanup()

    def build(self, by_label, image=False):
        """`image`: shot 1.2 is drawn by a licensed download, so the only
        thing between a split world and a publishable build is the split."""
        dossier = None
        if image:
            from PIL import Image
            (self.root / "downloads").mkdir()
            Image.new("RGB", (8, 8)).save(self.root / "downloads" / "thing.png")
            (self.root / "raw").mkdir()
            (self.root / "raw" / "08-downloads.json").write_text(
                '[{"status": "ok", "file": "downloads/thing.png", "title": "a thing", '
                '"licence": "CC0"}]')
            dossier = {"assets": [{"title": "a thing", "licence": "CC0",
                                   "credit_line": "someone"}]}
        sections = [Section(heading="Open", shots=[
            Shot(label=f"1.{n}", n=n, visual="v", narration="w", seconds=3,
                 at=3 * (n - 1), asset="a thing" if image and n == 2 else "")
            for n in (1, 2, 3)])]
        return build.SceneBuilder(_idea(), sections, self.root, audio=False,
                                  verbose=False, dossier=dossier,
                                  vfx={"scene": {"by_label": by_label}}).build()

    def hit(self, symbol):
        return {"file": "vfx/open.scene.xml", "symbol": symbol}

    def test_another_symbol_between_two_spans_is_not_a_split(self):
        _, manifest = self.build({"1.1": self.hit("fx-1-1"), "1.2": self.hit("fx-1-2"),
                                  "1.3": self.hit("fx-1-1")})
        self.assertEqual(manifest["includes"], 3)
        self.assertEqual(manifest["split_worlds"], [])
        self.assertFalse([f for f in manifest["findings"] if "include(s)" in f])

    def test_a_run_split_only_by_a_gap_is_an_error_and_not_publishable(self):
        # AUTO-70: a split world is the defect behind a 3.0 scorecard, found
        # 90 minutes later by QA; the build refuses to write it without --force.
        _, manifest = self.build({"1.1": self.hit("fx-1-1"), "1.3": self.hit("fx-1-1")})
        self.assertEqual(manifest["includes"], 2)
        errors = [f for f in manifest["findings"]
                  if f.startswith("error") and "fx-1-1" in f]
        self.assertEqual(len(errors), 1, manifest["findings"])
        for needle in ("1.1", "1.3", "2 include"):
            self.assertIn(needle, errors[0])
        self.assertEqual(manifest["split_worlds"][0]["includes"], 2)

    def test_a_split_world_alone_makes_the_build_unpublishable(self):
        _, manifest = self.build({"1.1": self.hit("fx-1-1"), "1.3": self.hit("fx-1-1")},
                                 image=True)
        self.assertEqual(manifest["layers"]["placeholder"], 0, manifest["findings"])
        self.assertEqual(manifest["split_worlds"][0]["includes"], 2)
        self.assertFalse(manifest["publishable"])

    def test_a_run_with_no_gap_is_one_include_and_publishable(self):
        _, manifest = self.build({"1.1": self.hit("fx-1-1"), "1.2": self.hit("fx-1-1"),
                                  "1.3": self.hit("fx-1-1")})
        self.assertEqual(manifest["includes"], 1)
        self.assertEqual(manifest["split_worlds"], [])
        self.assertTrue(manifest["publishable"])

    def test_the_summary_line_says_includes_for_shots_with_the_warning(self):
        import contextlib
        import io
        _, manifest = self.build({"1.1": self.hit("fx-1-1"), "1.3": self.hit("fx-1-1")})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            build.print_build(manifest)
        out = buf.getvalue()
        self.assertIn("2 include(s) for 3 shots", out)
        self.assertIn("error: fx-1-1", out)


if __name__ == "__main__":
    unittest.main()
