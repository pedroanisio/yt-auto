"""A shot the research found a still for is placed so the engine accepts it.

The first build that reached a downloaded image placed it with fit="contain"
and no box, and the engine refused the encode: a fitted layer needs
@boxWidth and @boxHeight. The image layer now carries the frame as its box.
"""

from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import build  # noqa: E402
from ytstudio.core import Idea  # noqa: E402
from ytstudio.scene_sdk import sdk  # noqa: E402
from ytstudio.script import Section, Shot  # noqa: E402


def png_1x1() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x80\x80\x80")) + chunk(b"IEND", b""))


class ImageLayer(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "downloads").mkdir()
        (self.root / "raw").mkdir()
        (self.root / "downloads" / "01-a.png").write_bytes(png_1x1())
        (self.root / "raw" / "08-downloads.json").write_text(json.dumps([
            {"title": "a comet still", "url": "https://h.example/a.png", "status": "ok",
             "file": "downloads/01-a.png", "licence": "CC0"}]))

    def test_a_fitted_still_has_a_box(self):
        idea = Idea.from_raw({"title": "A film", "genre": "explainer", "description": "p"})
        sections = [Section(heading="Open", shots=[
            Shot(label="1.1", n=1, visual="a comet", narration="Look.", seconds=4, at=0,
                 asset="a comet still")])]
        dossier = {"assets": [{"title": "a comet still", "kind": "image", "licence": "CC0",
                               "credit_line": "c", "use_for": "the opening"}]}
        data, _ = build.SceneBuilder(idea, sections, self.root, dossier=dossier,
                                     audio=False, verbose=False).build()
        errors = [str(f) for f in sdk().read(data).check() if str(f).startswith("error")]
        self.assertEqual(errors, [])
        layer = ElementTree.fromstring(data).find(".//layer[@id='shot-1.1']")
        self.assertIsNotNone(layer)
        if layer.get("fit") not in (None, "none"):
            self.assertTrue(layer.get("boxWidth") and layer.get("boxHeight"),
                            ElementTree.tostring(layer))


class MeasuredRuns(unittest.TestCase):
    """Takes measured by `yt voice` sit apart by a breath, so consecutive
    shots of one world never touch; they must still share one include, or
    the world restarts at t=0 at every cut (QA: "shot 1.2 replays the
    opening", "4.2 rewinds the world")."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "vfx").mkdir()
        (self.root / "vfx" / "open.scene.xml").write_text("<scene/>")

    def build(self, by_label, shots, measured=True):
        idea = Idea.from_raw({"title": "A film", "genre": "explainer", "description": "p"})
        sections = [Section(heading="Open", shots=shots[:2]), Section(heading="Close", shots=shots[2:])]
        vfx = {"scene": {"by_label": {k: {"file": "vfx/open.scene.xml", "symbol": v}
                                      for k, v in by_label.items()}}}
        # the measured takes, a breath apart, as voice.json records them
        voice = {"takes": [{"label": s.label, "at": s.at, "seconds": s.seconds}
                           for s in shots]} if measured else None
        data, _ = build.SceneBuilder(idea, sections, self.root, vfx=vfx, voice=voice,
                                     audio=False, verbose=False).build()
        self.root_el = ElementTree.fromstring(data)
        return self.root_el.findall(".//include")

    def shots(self):
        # a 0.35 s breath inside the section, 0.8 s between sections
        return [Shot(label="1.1", n=1, visual="a", narration="one", seconds=4, at=0),
                Shot(label="1.2", n=2, visual="b", narration="two", seconds=3, at=4.35),
                Shot(label="2.1", n=3, visual="c", narration="three", seconds=3, at=8.15)]

    def test_a_breath_between_takes_does_not_split_the_world(self):
        includes = self.build({"1.1": "fx-a", "1.2": "fx-a", "2.1": "fx-b"}, self.shots())
        self.assertEqual([(i.get("id"), i.get("symbol")) for i in includes],
                         [("shot-1.1", "fx-a"), ("shot-2.1", "fx-b")])
        self.assertEqual((includes[0].get("start"), includes[0].get("end")), ("0", "7.35"))
        # the beats stay on the measured takes, not on the stretched span
        beats = [(m.get("label"), m.get("time")) for m in self.root_el.iter("marker")
                 if m.get("kind") == "beat"]
        self.assertEqual(beats, [("1.1", "0"), ("1.2", "4.35"), ("2.1", "8.15")])

    def test_a_shot_in_the_gap_still_splits_it(self):
        # 1.2 is not drawn by fx-a, so fx-a's two uses stay two includes
        includes = self.build({"1.1": "fx-a", "2.1": "fx-a"}, self.shots())
        self.assertEqual([i.get("id") for i in includes], ["shot-1.1", "shot-2.1"])

    def test_a_shot_that_abuts_both_uses_still_splits_them(self):
        # script-timed shots touch exactly: a placeholder 1.2 at [4, 7]
        # between two uses of fx-a must keep them two includes
        shots = [Shot(label="1.1", n=1, visual="a", narration="one", seconds=4, at=0),
                 Shot(label="1.2", n=2, visual="b", narration="two", seconds=3, at=4),
                 Shot(label="2.1", n=3, visual="c", narration="three", seconds=3, at=7)]
        includes = self.build({"1.1": "fx-a", "2.1": "fx-a"}, shots, measured=False)
        self.assertEqual([i.get("id") for i in includes], ["shot-1.1", "shot-2.1"])


if __name__ == "__main__":
    unittest.main()
