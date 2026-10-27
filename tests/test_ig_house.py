"""igstudio.house: the layered standards an ig stage is handed."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from igstudio import house  # noqa: E402


class Layering(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_default_ships_every_stage(self):
        h = house.load(self.root)
        self.assertEqual(h.sources, [house.DEFAULT_SOURCE])
        for stage in house.STAGES:
            self.assertIn(stage, h.sections, stage)
        block = h.for_stage("caption")
        self.assertIn("engagement bait", block)          # all
        self.assertIn("curiosity trigger", block)        # hook
        self.assertIn("hashtags", block.lower())         # caption
        self.assertNotIn("Answer the question asked", block)   # reply is not read
        self.assertIn("Answer the question asked", h.for_stage("reply"))

    def test_registry_then_account_then_explicit_replace_sections_by_name(self):
        (self.root / "house.md").write_text("## cta\nRegistry CTA rule.\n", encoding="utf-8")
        (self.root / "house").mkdir()
        (self.root / "house" / "news.md").write_text("Account preamble.\n\n## hook\nAccount hook rule.\n",
                                                     encoding="utf-8")
        extra = self.root / "extra.md"
        extra.write_text("## alt\nExplicit alt rule.\n", encoding="utf-8")
        h = house.load(self.root, "news", explicit=str(extra))
        self.assertEqual(len(h.sources), 4)
        block = h.for_stage("caption")
        self.assertIn("Account preamble.", block)
        self.assertIn("Account hook rule.", block)
        self.assertNotIn("curiosity trigger", block)      # the default hook section was replaced
        self.assertIn("Registry CTA rule.", block)
        self.assertIn("Explicit alt rule.", block)
        self.assertIn("hashtags", block.lower())          # the default caption section stays
        other = house.load(self.root, "brand")
        self.assertNotIn("Account hook rule.", other.for_stage("caption"))
        self.assertIn("Registry CTA rule.", other.for_stage("caption"))

    def test_digest_follows_the_block_and_off_is_empty(self):
        a = house.load(self.root).digest("caption")
        (self.root / "house.md").write_text("## reply\nOther.\n", encoding="utf-8")
        b = house.load(self.root).digest("caption")
        self.assertNotEqual(a["sha256"], b["sha256"])
        self.assertEqual(a["block_sha256"], b["block_sha256"])   # caption's block is unchanged
        (self.root / "house.md").write_text("## hook\nOther.\n", encoding="utf-8")
        c = house.load(self.root).digest("caption")
        self.assertNotEqual(a["block_sha256"], c["block_sha256"])
        off = house.load(self.root, enabled=False)
        self.assertEqual(off.for_stage("caption"), "")
        self.assertEqual(off.digest("caption")["block_sha256"], "")


if __name__ == "__main__":
    unittest.main()
