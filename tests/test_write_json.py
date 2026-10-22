"""A stage's record is written whole or not at all.

`core.write_json` is the one way every stage writes JSON. The sound stage
now runs on a daemon thread beside `yt run`'s walk, so a Ctrl-C can end the
interpreter while that thread is halfway through sound.json; a truncated
sheet would count as "done" on the next run and feed the build a broken
record. The write goes to a sibling file and is moved into place.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio.core import read_json, write_json  # noqa: E402


class Whole(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sheet = self.root / "sound.json"
        write_json(self.sheet, {"cues": ["the old sheet"]})

    def test_a_write_cut_short_leaves_the_old_record_whole_and_nothing_beside_it(self):
        real = Path.write_text

        def cut_short(path, text, *args, **kwargs):
            real(path, text[: len(text) // 2], *args, **kwargs)
            raise KeyboardInterrupt

        with mock.patch.object(Path, "write_text", cut_short):
            with self.assertRaises(KeyboardInterrupt):
                write_json(self.sheet, {"cues": ["a new sheet, twice as long"] * 4})
        self.assertEqual(read_json(self.sheet), {"cues": ["the old sheet"]})
        self.assertEqual([p.name for p in self.root.iterdir()], ["sound.json"])

    def test_a_finished_write_replaces_the_record_and_leaves_nothing_beside_it(self):
        write_json(self.sheet, {"cues": []})
        self.assertEqual(json.loads(self.sheet.read_text(encoding="utf-8")), {"cues": []})
        self.assertTrue(self.sheet.read_text(encoding="utf-8").endswith("\n"))
        self.assertEqual([p.name for p in self.root.iterdir()], ["sound.json"])


if __name__ == "__main__":
    unittest.main()
