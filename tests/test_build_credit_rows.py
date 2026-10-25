"""Every placed third-party asset is a credit row (AUTO-147, part A).

The build carried a licence and a credit onto each image, clip and cue it
placed, and stopped there: no viewer ever saw them, and CC BY asks for the
credit where the work is seen. `build.credit_rows` reads the finished
<assets> block (and the props an include names, which never reach it) into
the rows the end-credits card and `yt package`'s description print, each
with the licence's tier and whether the licence requires the credit.
"""

import json
import unittest
from pathlib import Path
from xml.etree import ElementTree

from ytstudio import assets, build, sound
from tests.test_build_hoist_credits import Fixture

SCENE = """<scene version="1.1">
  <assets>
    <image id="img-1.1" src="downloads/01-plate.png" license="CC BY 4.0" credit="A. Author / Wikimedia"/>
    <audio id="cue-asset-M1" src="sound/pad.wav" license="CC BY-SA 4.0" credit="pad by B. Maker (Freesound)"/>
    <audio id="cue-asset-S1" src="sound/synth.wav" license="{synth}" credit="{synth_credit}"/>
    <audio id="vo-1.1" src="voice/1.1.wav"/>
    <text id="card-1.2" text="1.2"/>
  </assets>
</scene>""".format(synth=sound.SYNTH_LICENCE, synth_credit=sound.SYNTH_CREDIT)

PROP = {"id": "studio", "kind": "hdri", "file": "props/hdri/studio/studio.hdr",
        "files": ["props/hdri/studio/studio.hdr"], "licence": "CC0 1.0 (Poly Haven)",
        "credit": "Studio by C. Lighter / CC0 (Poly Haven)", "src": "props/hdri/studio/studio.hdr"}


class Rows(unittest.TestCase):
    def rows(self, text=SCENE, props=(PROP,)):
        return build.credit_rows(ElementTree.fromstring(text), list(props))

    def test_three_rows_the_synthesised_cue_dropped(self):
        rows = self.rows()
        self.assertEqual([r["credit"] for r in rows],
                         ["Studio by C. Lighter / CC0 (Poly Haven)",
                          "A. Author / Wikimedia", "pad by B. Maker (Freesound)"])
        self.assertNotIn(sound.SYNTH_CREDIT, [r["credit"] for r in rows])

    def test_required_on_attribution_and_share_alike_only(self):
        by = {r["credit"]: r for r in self.rows()}
        self.assertFalse(by["Studio by C. Lighter / CC0 (Poly Haven)"]["required"])
        self.assertTrue(by["A. Author / Wikimedia"]["required"])
        self.assertTrue(by["pad by B. Maker (Freesound)"]["required"])
        self.assertEqual(by["A. Author / Wikimedia"]["tier"], assets.TIER_ATTRIBUTION)
        # share-alike is judged as allowed here: the default veto is the
        # catalogue's, and a placed file is already in the film
        self.assertEqual(by["pad by B. Maker (Freesound)"]["tier"], assets.TIER_SHARE_ALIKE)

    def test_each_row_has_its_shape(self):
        for row in self.rows():
            self.assertEqual(set(row), {"credit", "licence", "src", "id", "tier", "required"})
        image = next(r for r in self.rows() if r["id"] == "img-1.1")
        self.assertEqual((image["src"], image["licence"]),
                         ("downloads/01-plate.png", "CC BY 4.0"))

    def test_duplicate_credit_lines_are_one_row(self):
        twice = SCENE.replace(
            '<text id="card-1.2" text="1.2"/>',
            '<image id="credit-02" src="downloads/02.png" license="CC BY 4.0" '
            'credit="A. Author / Wikimedia"/>')
        rows = self.rows(twice, ())
        self.assertEqual([r["credit"] for r in rows].count("A. Author / Wikimedia"), 1)
        self.assertEqual(next(r["id"] for r in rows if r["credit"] == "A. Author / Wikimedia"),
                         "img-1.1")

    def test_ordered_by_tier_then_first_appearance(self):
        text = """<scene><assets>
          <image id="a" src="a.png" license="CC BY-SA 4.0" credit="first SA"/>
          <image id="b" src="b.png" license="CC BY 4.0" credit="first BY"/>
          <image id="c" src="c.png" license="CC BY 4.0" credit="second BY"/>
          <image id="d" src="d.png" license="Public domain" credit="a PD"/>
        </assets></scene>"""
        self.assertEqual([r["credit"] for r in self.rows(text, ())],
                         ["a PD", "first BY", "second BY", "first SA"])

    def test_no_third_party_asset_no_rows(self):
        text = '<scene><assets><audio id="vo-1" src="voice/1.wav"/></assets></scene>'
        self.assertEqual(self.rows(text, ()), [])


class PropReference(Fixture):
    """An include naming a file under props/ is credited from props.json:
    the include copy keeps its own <assets>, so the scene never sees it."""

    def test_an_hdri_an_include_names_yields_the_props_item(self):
        (self.root / "props" / "hdri" / "x").mkdir(parents=True)
        (self.root / "props" / "hdri" / "x" / "x.hdr").write_bytes(b"#?RADIANCE")
        (self.root / "props.json").write_text(json.dumps({"items": [{
            "id": "x", "kind": "hdri", "file": "props/hdri/x/x.hdr",
            "files": ["props/hdri/x/x.hdr", "props/hdri/x/x_preview.png"],
            "licence": "CC0 1.0 (Poly Haven)", "credit": "X by Y / CC0 (Poly Haven)"}]}))
        self.write_authored(Fixture_AUTHORED_WITH_HDRI)
        self.manifest([{"title": "a micrograph", "status": "ok",
                        "file": "downloads/01-plate.png", "licence": "CC BY 4.0"}])
        dossier = {"assets": [{"title": "a micrograph", "kind": "image",
                               "licence": "CC BY 4.0", "credit_line": "A. Author"}]}
        _, manifest = self.build(dossier=dossier)
        credits = {r["credit"]: r for r in manifest["credits"]}
        self.assertIn("X by Y / CC0 (Poly Haven)", credits, manifest["credits"])
        self.assertEqual(credits["X by Y / CC0 (Poly Haven)"]["src"], "props/hdri/x/x.hdr")
        self.assertFalse(credits["X by Y / CC0 (Poly Haven)"]["required"])

    def test_hoisted_lists_the_props_it_names(self):
        self.write_authored(Fixture_AUTHORED_WITH_HDRI)
        info = build.hoisted(self.root / "vfx" / "open.scene.xml", scene_dir=self.root)
        self.assertEqual(info["props"], ["props/hdri/x/x.hdr"])


class PropCreditErrors(Fixture):
    """A prop an include names owes its credit as a placed picture does: an
    attribution licence with no credit, or a file props.json does not list
    (so nobody recorded its licence), is a credit error and the build is
    not publishable."""

    def prop(self, licence: str, credit: str):
        (self.root / "props" / "hdri" / "x").mkdir(parents=True, exist_ok=True)
        (self.root / "props" / "hdri" / "x" / "x.hdr").write_bytes(b"#?RADIANCE")
        (self.root / "props.json").write_text(json.dumps({"items": [{
            "id": "x", "kind": "hdri", "file": "props/hdri/x/x.hdr",
            "files": ["props/hdri/x/x.hdr"], "licence": licence, "credit": credit}]}))

    def run_build(self):
        self.write_authored(Fixture_AUTHORED_WITH_HDRI)
        self.manifest([{"title": "a micrograph", "status": "ok",
                        "file": "downloads/01-plate.png", "licence": "CC BY 4.0"}])
        dossier = {"assets": [{"title": "a micrograph", "kind": "image",
                               "licence": "CC BY 4.0", "credit_line": "A. Author"}]}
        _, manifest = self.build(dossier=dossier)
        return manifest

    def test_a_cc_by_prop_with_no_credit_is_not_publishable(self):
        self.prop("CC BY 4.0", "")
        manifest = self.run_build()
        self.assertEqual(len(manifest["credit_errors"]), 1, manifest["credit_errors"])
        self.assertTrue(manifest["credit_errors"][0].startswith("error: credit: "))
        self.assertIn("props/hdri/x/x.hdr", manifest["credit_errors"][0])
        self.assertIn("props.json", manifest["credit_errors"][0])
        self.assertIn(manifest["credit_errors"][0], manifest["findings"])
        self.assertFalse(manifest["publishable"])

    def test_an_unknown_licence_prop_with_no_credit_is_not_publishable(self):
        self.prop("", "")
        manifest = self.run_build()
        self.assertEqual(len(manifest["credit_errors"]), 1, manifest["credit_errors"])
        self.assertIn("unknown", manifest["credit_errors"][0])
        self.assertFalse(manifest["publishable"])

    def test_a_cc0_prop_with_no_credit_stays_publishable(self):
        self.prop("CC0 1.0 (Poly Haven)", "")
        manifest = self.run_build()
        self.assertEqual(manifest["credit_errors"], [])

    def test_a_prop_props_json_does_not_list_is_not_publishable(self):
        (self.root / "props" / "hdri" / "x").mkdir(parents=True)
        (self.root / "props" / "hdri" / "x" / "x.hdr").write_bytes(b"#?RADIANCE")
        manifest = self.run_build()
        self.assertEqual(len(manifest["credit_errors"]), 1, manifest["credit_errors"])
        self.assertTrue(manifest["credit_errors"][0].startswith("error: credit: "))
        self.assertIn("does not list", manifest["credit_errors"][0])
        self.assertFalse(manifest["publishable"])


class MergedDuty(unittest.TestCase):
    """Two assets under one credit line print it once, and the merged row
    keeps the stronger duty: a CC0 file first in the document must not
    hide that a CC BY file under the same line requires it."""

    def test_a_required_row_survives_a_free_one_with_the_same_line(self):
        text = """<scene><assets>
          <image id="a" src="downloads/a.png" license="CC0 1.0" credit="An Archive"/>
          <image id="b" src="downloads/b.png" license="CC BY 4.0" credit="An Archive"/>
        </assets></scene>"""
        rows = build.credit_rows(ElementTree.fromstring(text), [])
        self.assertEqual(len(rows), 1, rows)
        self.assertTrue(rows[0]["required"])
        self.assertEqual(rows[0]["licence"], "CC BY 4.0")
        self.assertEqual(rows[0]["tier"], assets.TIER_ATTRIBUTION)


Fixture_AUTHORED_WITH_HDRI = """<?xml version="1.0"?>
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
    <light id="sky" type="dome" environment="../props/hdri/x/x.hdr" intensity="1"/>
  </lights>
</scene>
"""


if __name__ == "__main__":
    unittest.main()
