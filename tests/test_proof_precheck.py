"""AUTO-80: the vfx frame gate runs the checklist's mechanical items on the
proof frames it renders, `kit.py precheck` reads the gate's frames beside the
author's own, and a text row in the caption band is a rule finding the
author sees in its own build. No model, no engine: the still is a stub.
"""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_kit_backlog import grey_png
from tests.test_vfx_world import SECTION, WORLD_DOC, varied_png
from ytstudio import design, sdkkit, vfx


def veiled_png() -> bytes:
    """A frame under a veil: the pre-check's haze finding."""
    return grey_png(lambda im: (im.paste(150, (0, 0, 320, 180)),
                                im.paste(175, (80, 40, 240, 140))))


#: The world with two labels that overlap: the pre-check names the pair once.
LABELLED_DOC = WORLD_DOC.replace(
    '<object3D id="floor"',
    '<text id="lbl-a" text="one" x="100" y="100" width="300" height="60" size="30"/>'
    '<text id="lbl-b" text="two" x="180" y="120" width="300" height="60" size="30"/>'
    '<object3D id="floor"')


class ProofFrames(unittest.TestCase):
    """A proof frame carries the pre-check's findings, as a kit frame does."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ws = vfx.Workspace(self.root, version="v01")
        self.name = self.ws.doc_name(1, SECTION)
        self.shots = {"world-open": SECTION["shots"]}

    def tearDown(self):
        self._tmp.cleanup()

    def frames(self, doc: str, png: bytes, **kw):
        (self.ws.dir / self.name).write_text(doc, encoding="utf-8")
        with mock.patch.object(vfx.render, "engine_render_still",
                               lambda xml, at, **k: (png, "rendered on h")), \
                mock.patch.object(vfx, "encode_findings", lambda *a, **k: []):
            return self.ws.frames(self.name, shots=self.shots, **kw)

    def test_a_veiled_proof_frame_carries_the_haze_finding(self):
        results = self.frames(WORLD_DOC, veiled_png(), times=[5.0])
        png, findings = results[0]
        self.assertIsNotNone(png)
        self.assertTrue(any(f.startswith("warning: frame at 5s of world-open") and "haze" in f
                            for f in findings), findings)

    def test_a_clean_proof_frame_carries_no_precheck_finding(self):
        results = self.frames(WORLD_DOC, varied_png(), times=[5.0])
        self.assertFalse([f for f in results[0][1] if "haze" in f or "contrast" in f
                          or "framing" in f], results[0][1])

    def test_overlapping_labels_are_named_once_per_symbol_not_per_frame(self):
        results = self.frames(LABELLED_DOC, varied_png())
        self.assertGreater(len(results), 1)
        overlaps = [f for _, fs in results for f in fs if "`lbl-a`" in f and "`lbl-b`" in f]
        self.assertEqual(len(overlaps), 1, overlaps)

    def test_a_section_cut_into_worlds_names_each_pair_once_under_its_own_world(self):
        second = LABELLED_DOC.replace(
            '</symbol>', '</symbol><symbol id="world-two" width="1920" height="1080" duration="15">'
            '<text id="lbl-c" text="c" x="100" y="100" width="300" height="60" size="30"/>'
            '<text id="lbl-d" text="d" x="180" y="120" width="300" height="60" size="30"/>'
            '</symbol>', 1)
        results = self.frames(second, varied_png(), times=[5.0])
        self.assertEqual(len(results), 2)
        named = [f for _, fs in results for f in fs if "overlap" in f]
        self.assertEqual(len(named), 2, named)
        self.assertTrue(any("world-open" in f and "`lbl-a`" in f for f in named), named)
        self.assertTrue(any("world-two" in f and "`lbl-c`" in f for f in named), named)


class PrecheckCommand(unittest.TestCase):
    """`kit.py precheck <doc>` reads the gate's proof frames in frames/ beside
    the author's own in frames/kit/."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.doc = self.dir / "01-open.scene.xml"
        self.doc.write_text(WORLD_DOC, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def precheck(self) -> str:
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            sdkkit.main(["precheck", str(self.doc)])
        return out.getvalue()

    def test_proof_frames_and_kit_frames_are_both_read(self):
        (self.dir / "frames" / "kit").mkdir(parents=True)
        (self.dir / "frames" / "01-open__world-open__5s.png").write_bytes(veiled_png())
        (self.dir / "frames" / "kit" / "01-open__9s.png").write_bytes(veiled_png())
        text = self.precheck()
        self.assertIn("frame at 5s of world-open", text)
        self.assertIn("frame at 9s of 01-open", text)
        self.assertEqual(text.count("haze"), 2)                # one per frame, whichever directory
        self.assertIn("precheck: 4 finding(s)", text)           # contrast and haze, twice

    def test_another_documents_proof_frames_are_not_read(self):
        (self.dir / "frames").mkdir()
        (self.dir / "frames" / "02-other__world-other__5s.png").write_bytes(veiled_png())
        self.assertIn("precheck: clean", self.precheck())


class BandRule(unittest.TestCase):
    """A 2D text or formula node whose box enters the caption band is a rule
    finding, with the fix named."""

    HEAD = ('<scene version="1.3"><project width="1920" height="1080" fps="30" duration="5"/>'
            '<assets><text id="t-row" text="a row" width="600" height="80" size="40"/>'
            '<text id="t-line" text="a line" size="40"/>'
            '<formula id="f-sum" tex="x" size="48"/></assets>'
            '<symbols><symbol id="world" width="1920" height="1080" duration="5">')
    TAIL = '</symbol></symbols></scene>'

    def rule(self, body: str) -> list[str]:
        found = sdkkit.rule_findings((self.HEAD + body + self.TAIL).encode())
        return [f for f in found if "rule:band" in f]

    def test_a_text_layer_in_the_band_is_named_with_the_fix(self):
        found = self.rule('<layer id="l-low" asset="t-row" x="100" y="900"/>')
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith("warning: rule:band:"), found[0])
        self.assertIn("`l-low`", found[0])
        top = round(1080 * design.CAPTION_FRACTION)
        self.assertIn(str(top), found[0])                      # where the band starts
        self.assertIn("caption", found[0])
        self.assertIn("move", found[0])                        # the fix

    def test_a_box_that_ends_in_the_band_counts_even_when_it_starts_above(self):
        found = self.rule('<layer id="l-edge" asset="t-row" x="100" y="780"/>')   # 780 + 80 > 820
        self.assertEqual(len(found), 1, found)

    def test_a_text_above_the_band_passes(self):
        self.assertEqual(self.rule('<layer id="l-high" asset="t-row" x="100" y="300"/>'), [])
        self.assertEqual(self.rule('<layer id="l-just" asset="t-row" x="100" y="700"/>'), [])

    def test_a_text_with_no_height_is_one_row_of_its_size(self):
        self.assertEqual(len(self.rule('<layer id="l-line" asset="t-line" y="800"/>')), 1)
        self.assertEqual(self.rule('<layer id="l-line" asset="t-line" y="700"/>'), [])

    def test_a_formula_counts_too(self):
        self.assertEqual(len(self.rule('<layer id="l-sum" asset="f-sum" y="850"/>')), 1)

    def test_what_the_engine_places_is_left_to_the_engine(self):
        self.assertEqual(self.rule('<group id="g" layout="column">'
                                   '<layer id="l-in" asset="t-row" y="900"/></group>'), [])
        self.assertEqual(self.rule('<layer id="l-al" asset="t-row" alignY="bottom" y="900"/>'), [])
        self.assertEqual(self.rule('<layer id="l-rel" asset="t-row" y="90%"/>'), [])
        self.assertEqual(self.rule('<layer id="l-3d" asset="t-row" threeD="true" y="900"/>'), [])

    def test_a_layer_drawing_no_text_is_not_judged(self):
        self.assertEqual(self.rule('<layer id="l-img" asset="pic" y="900"/>'), [])

    def test_a_text_with_a_named_style_takes_the_styles_line_height(self):
        # One row of size 40 is 48 at the default 1.2; the style's 2 makes it 80.
        self.HEAD = self.HEAD.replace(
            '<assets>', '<styles><textStyle id="ts-tall" lineHeight="2"/></styles><assets>'
                        '<text id="t-styled" text="x" size="40" style="ts-tall"/>')
        self.assertEqual(len(self.rule('<layer id="l-st" asset="t-styled" y="750"/>')), 1)
        self.assertEqual(self.rule('<layer id="l-line" asset="t-line" y="750"/>'), [])

    def test_a_translated_groups_child_is_judged_where_the_group_puts_it(self):
        self.assertEqual(len(self.rule('<group id="g" y="300">'
                                       '<layer id="l-in" asset="t-row" y="600"/></group>')), 1)
        self.assertEqual(self.rule('<group id="g" y="-300">'
                                   '<layer id="l-in" asset="t-row" y="900"/></group>'), [])
        self.assertEqual(self.rule('<group id="g" y="50%">'
                                   '<layer id="l-in" asset="t-row" y="900"/></group>'), [])

    def test_a_relative_anchor_is_left_to_the_engine(self):
        self.assertEqual(self.rule('<layer id="l-anc" asset="t-row" y="900" anchorY="50%"/>'), [])
        self.assertEqual(self.rule('<layer id="l-anc" asset="t-row" y="900" anchorY="200"/>'), [])
        self.assertEqual(len(self.rule('<layer id="l-anc" asset="t-row" y="900" anchorY="20"/>')), 1)

    def test_the_rule_reaches_the_authors_build_check(self):
        doc = (self.HEAD + '<layer id="l-low" asset="t-row" x="100" y="900"/>' + self.TAIL).encode()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.scene.xml"
            path.write_bytes(doc)
            checked = sdkkit.check_document(path, validate=False)
        self.assertTrue(any("rule:band" in f for f in checked["sdk"]), checked["sdk"])
