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

AUTO-71: the hoist rebuilt the intensity <animate> with defaultInterpolation
"hold" when the author wrote none (the schema's default is linear), dropped
every per-key attribute, and under an authored linear default the gate keys
became a fade the author never wrote, leaking into the neighbouring sections.
The author's <animate> is now carried verbatim, re-timed, and the gate
segments are explicit holds: 0 held until the first carried key, the last
carried key held until the include's end, 0 from there.
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


def _intensity_animate(data):
    light = ElementTree.fromstring(data).find(".//light")
    return next(a for a in light.iter("animate") if a.get("property") == "intensity")


def _span(data):
    """The include's start and end, as the build wrote them."""
    include = ElementTree.fromstring(data).find(".//include")
    return float(include.get("start")), float(include.get("end"))


def _at(animate, t):
    """The animation's value at t, as the schema reads the keys: a key's
    interpolation governs the segment to the next key, a key without one
    takes the animation's defaultInterpolation (linear by default), and the
    first and last values hold outside the keys. Only hold and linear are
    read; another curve in a gate test is a failure."""
    keys = sorted(animate.iter("key"), key=lambda k: float(k.get("time")))
    default = animate.get("defaultInterpolation", "linear")
    if t < float(keys[0].get("time")):
        return float(keys[0].get("value"))
    for a, b in zip(keys, keys[1:]):
        t0, t1 = float(a.get("time")), float(b.get("time"))
        if t0 <= t < t1:
            curve = a.get("interpolation", default)
            v0, v1 = float(a.get("value")), float(b.get("value"))
            if curve == "hold":
                return v0
            if curve == "linear":
                return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
            raise AssertionError(f"a {curve} segment at {t}")
    return float(keys[-1].get("value"))


class HoistedLightCurve(Fixture):
    """The include starts at 4; the authored keys sit at 0 and 1.5."""

    def test_the_hoisted_animate_has_the_authored_default_interpolation(self):
        self.write_authored(AUTHORED.replace(
            '<animate property="intensity">',
            '<animate property="intensity" defaultInterpolation="ease-in-out">'))
        data, _ = self.build()
        self.assertEqual(_intensity_animate(data).get("defaultInterpolation"),
                         "ease-in-out")

    def test_an_absent_default_interpolation_stays_absent(self):
        data, _ = self.build()
        self.assertIsNone(_intensity_animate(data).get("defaultInterpolation"))

    def test_a_carried_key_keeps_its_own_attributes(self):
        self.write_authored(AUTHORED.replace(
            '<key time="0" value="2"/>',
            '<key time="0" value="2" interpolation="ease-out" easeOut="0.5,0"/>'))
        data, _ = self.build()
        key = {float(k.get("time")): k for k in _intensity_animate(data).iter("key")}
        self.assertEqual(key[4.0].get("interpolation"), "ease-out")
        self.assertEqual(key[4.0].get("easeOut"), "0.5,0")

    def test_the_light_is_dark_outside_its_span_under_linear_interpolation(self):
        self.write_authored(AUTHORED.replace(
            '<animate property="intensity">',
            '<animate property="intensity" defaultInterpolation="linear">'))
        data, _ = self.build()
        animate = _intensity_animate(data)
        start, end = _span(data)
        self.assertEqual(start, 4)
        self.assertEqual(_at(animate, start - 0.001), 0)
        self.assertEqual(_at(animate, end + 0.001), 0)
        # the author's ramp from 2 to 6 over the section's first 1.5 s
        self.assertEqual(_at(animate, 4), 2)
        self.assertAlmostEqual(_at(animate, 4.75), 4)
        # the last key holds to the include's end, with no fade to the gate
        self.assertEqual(_at(animate, end - 0.1), 6)

    def test_the_gate_segments_are_explicit_holds(self):
        data, _ = self.build()
        _, end = _span(data)
        key = {float(k.get("time")): k for k in _intensity_animate(data).iter("key")}
        self.assertEqual(key[0.0].get("interpolation"), "hold")
        self.assertEqual(key[5.5].get("interpolation"), "hold")
        self.assertEqual(key[end].get("value"), "0")

    def test_a_light_first_keyed_after_its_start_holds_that_value_from_the_start(self):
        # Before its first key the authored document shows the first key's
        # value (the schema holds it), not the light's intensity attribute.
        self.write_authored(AUTHORED.replace('<key time="0" value="2"/>\n', ""))
        data, _ = self.build()
        animate = _intensity_animate(data)
        self.assertEqual(_at(animate, 4), 6)
        self.assertEqual(_at(animate, 4.5), 6)
        self.assertEqual(_at(animate, 4 - 0.001), 0)


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
