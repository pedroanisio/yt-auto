"""AUTO-35: a family the repo does not ship is a rule finding in the author's own
round, not an error hours later at `yt build`."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import fonts, keyframe, sdkkit, vfx  # noqa: E402
from ytstudio.core import Idea  # noqa: E402

MISSING = "Zzyzx Unshipped"


def doc(body: str, styles: str = "") -> bytes:
    return ('<scene version="1.3"><project width="1920" height="1080" fps="30" duration="1"/>'
            f'<styles>{styles}</styles><assets>{body}</assets></scene>').encode()


def font_findings(data: bytes) -> list[str]:
    return [f for f in sdkkit.rule_findings(data) if "rule:font" in f]


class FontRule(unittest.TestCase):
    def test_an_unshipped_text_family_is_an_error_naming_it_and_the_shipped_ones(self):
        found = font_findings(doc(f'<text id="t" text="x" size="80" font="{MISSING}"/>'))
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith("error"), found)
        self.assertIn(MISSING, found[0])
        for family in fonts.families():
            self.assertIn(family, found[0])

    def test_text_style_and_3d_text_are_read_the_same_way(self):
        data = doc(f'<object3D id="o" primitive="text" font="{MISSING}"/>',
                   f'<textStyle id="s" font="{MISSING} B"/>')
        found = font_findings(data)
        self.assertEqual(len(found), 2, found)

    def test_a_font_inherited_from_a_style_is_judged(self):
        data = doc('<text id="t" text="x" size="80" style="s" weight="700"/>',
                   f'<textStyle id="s" font="{MISSING}"/>')
        self.assertTrue(font_findings(data))

    def test_a_list_that_falls_back_to_a_shipped_family_raises_nothing(self):
        shipped = fonts.families()[0]
        for font in (f"{MISSING}, {shipped}", f"'{MISSING}', serif", "Helvetica", shipped):
            self.assertEqual(font_findings(doc(f'<text id="t" text="x" size="80" font="{font}"/>')),
                             [], font)

    def test_a_node_with_no_font_takes_the_default_and_is_fine(self):
        self.assertEqual(font_findings(doc('<text id="t" text="x" size="80"/>'
                                           '<object3D id="o" primitive="text"/>')), [])

    def test_a_pinned_node_is_not_judged(self):
        self.assertEqual(font_findings(doc(
            f'<text id="t" text="x" size="80" font="{MISSING}" fontAsset="f1"/>')), [])

    def test_the_same_request_is_reported_once(self):
        body = "".join(f'<text id="t{i}" text="x" size="80" font="{MISSING}"/>' for i in range(3))
        self.assertEqual(len(font_findings(doc(body))), 1)


class BriefListsFamilies(unittest.TestCase):
    def test_the_vfx_brief_lists_the_shipped_families(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = vfx.Workspace(Path(tmp), version="v01")
            section = {"index": 1, "title": "S", "shots": [{"label": "1.1", "seconds": 4.0}]}
            path = ws.brief(1, idea=Idea(title="T", genre="g", description="d"), house_rules="",
                            style={}, tokens={"background": "#000"}, frame=(1920, 1080),
                            section=section, effects=[], words={}, assets=[])
            text = path.read_text(encoding="utf-8")
        for family in fonts.families():
            self.assertIn(family, text)

    def test_the_keyframe_brief_lists_the_shipped_families(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = vfx.Workspace(Path(tmp), keyframe.KEYFRAME_DIR)
            ws.prepare(tokens={"background": "#000"}, frame=keyframe.FRAME)
            path = keyframe.write_brief(
                ws, idea=Idea(title="T", genre="g", description="d"), house_rules="",
                direction=None, elements={}, roles=[], tokens={"background": "#000"},
                facts={}, assets=[], notes=[])
            text = path.read_text(encoding="utf-8")
        for family in fonts.families():
            self.assertIn(family, text)


if __name__ == "__main__":
    unittest.main()
