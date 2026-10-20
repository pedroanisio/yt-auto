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


if __name__ == "__main__":
    unittest.main()
