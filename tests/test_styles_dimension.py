"""AUTO-154: each treatment in the style library has a dimension.

The film is meant to mix 2D, 2.5D and 3D where each works best, and a shot
says which treatment it belongs to (script.json `style`). The library's
briefs said "2.5D cutaway" and "an accurate 3D model" only in prose, so
nothing could compare what a shot declares with what was authored.
STYLE_DIMENSION gives every library treatment one of 2d, 2.5d, 3d, footage
or any; `dimension_of` looks a name up loosely, and a name the library does
not hold is `unknown`. Nothing a model is asked changes: style_schema is
frozen here as it was before.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import styles  # noqa: E402

#: style_schema(False) and style_schema(True) as they were before AUTO-154.
FROZEN = Path(__file__).resolve().parent / "fixtures" / "styles-schema" / "style_schema.json"


class Dimension(unittest.TestCase):
    def test_every_library_style_has_a_dimension(self):
        self.assertEqual(set(styles.STYLE_DIMENSION), set(styles.STYLE_LIBRARY))
        allowed = set(styles.DIMENSIONS) - {"unknown"}
        for name, dim in styles.STYLE_DIMENSION.items():
            self.assertIn(dim, allowed, name)

    def test_isometric_is_2_5d_and_footage_styles_are_footage(self):
        self.assertEqual(styles.dimension_of("isometric diagram"), "2.5d")
        for name in ("archival montage", "talking head + b-roll", "satellite and GIS"):
            self.assertEqual(styles.dimension_of(name), "footage", name)
        self.assertEqual(styles.dimension_of("3d animation"), "3d")
        self.assertEqual(styles.dimension_of("2d motion graphics"), "2d")
        self.assertEqual(styles.dimension_of("split screen comparison"), "any")

    def test_dimension_of_matches_loosely(self):
        self.assertEqual(styles.dimension_of("Isometric Diagram "), "2.5d")
        self.assertEqual(styles.dimension_of("3D-Animation"), "3d")

    def test_unknown_name_is_unknown(self):
        self.assertEqual(styles.dimension_of("3D cutaway"), "unknown")
        self.assertEqual(styles.dimension_of(""), "unknown")

    def test_the_library_keeps_its_briefs(self):
        # pool_lines and candidates read STYLE_LIBRARY's values as text
        self.assertTrue(all(isinstance(v, str) for v in styles.STYLE_LIBRARY.values()))

    def test_style_schema_unchanged(self):
        frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
        self.assertEqual(styles.style_schema(False), frozen["plain"])
        self.assertEqual(styles.style_schema(True), frozen["rich"])


if __name__ == "__main__":
    unittest.main()
