"""Doctor names a project whose vetted pictures were never fetched (AUTO-143).

Build and vfx place only files on disk (raw/08-downloads.json rows with
status ok). A dossier that vetted images or clips, with no manifest or no ok
row in it, makes a film drawn without them, and nothing said so. Doctor now
warns in the `assets` area and names `yt research --download`.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import doctor, workspace  # noqa: E402

PICTURE = {"title": "Bridge, 1931", "url": "https://upload.wikimedia.org/a/bridge.jpg",
           "kind": "image", "source": "wikimedia", "licence": "CC0"}
CLIP = {"title": "Opening day", "url": "https://upload.wikimedia.org/a/day.webm",
        "kind": "video", "source": "wikimedia", "licence": "Public domain"}
SOUND = {"title": "Crowd", "url": "https://upload.wikimedia.org/a/crowd.ogg",
         "kind": "audio", "source": "wikimedia", "licence": "CC0"}
LOCKED = PICTURE | {"licence": "All rights reserved", "url": "https://x.org/l.jpg"}


class DownloadsWarn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "idea.json").write_text(json.dumps({"title": "T"}))

    def tearDown(self):
        self.tmp.cleanup()

    def dossier(self, *assets):
        (self.root / "dossier.json").write_text(json.dumps({
            "claims": [], "urls_opened": [], "warnings": [], "assets": list(assets)}))

    def manifest(self, *statuses):
        raw = self.root / "raw"
        raw.mkdir(exist_ok=True)
        (raw / "08-downloads.json").write_text(json.dumps(
            [{"url": f"https://x.org/{n}", "status": s} | (
                {"file": f"downloads/{n}.jpg"} if s == "ok" else {})
             for n, s in enumerate(statuses)]))

    def warns(self):
        return [f for f in doctor.check_project(workspace.Project(self.root)).findings
                if f.area == "assets" and "--download" in (f.fix or "")]

    def test_vetted_pictures_with_no_manifest_name_the_download(self):
        self.dossier(PICTURE, CLIP)
        found = self.warns()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].level, "warn")
        self.assertIn("2", found[0].message)
        self.assertIn(f"yt research --project {self.root} --download", found[0].fix)

    def test_a_manifest_with_no_ok_row_warns_too(self):
        self.dossier(CLIP)
        self.manifest("failed: 404", "deferred: 429")
        found = self.warns()
        self.assertEqual(len(found), 1)
        # the download ran: the warning says none succeeded, not that it never ran
        self.assertNotIn("never downloaded", found[0].message)
        self.assertIn("none of the 2 download(s) succeeded", found[0].message)
        self.assertIn("deferred and failed", found[0].fix)

    def test_no_manifest_says_never_downloaded(self):
        self.dossier(CLIP)
        self.assertIn("never downloaded", self.warns()[0].message)
        self.manifest()                                   # an empty manifest
        self.assertIn("never downloaded", self.warns()[0].message)

    def test_one_ok_download_says_nothing(self):
        self.dossier(PICTURE, CLIP)
        self.manifest("ok", "failed: 404")
        self.assertEqual(self.warns(), [])

    def test_assets_that_are_not_pictures_say_nothing(self):
        self.dossier(SOUND)
        self.assertEqual(self.warns(), [])

    def test_a_picture_whose_licence_is_not_usable_is_not_vetted(self):
        self.dossier(LOCKED)
        self.assertEqual(self.warns(), [])

    def test_no_assets_say_nothing(self):
        self.dossier()
        self.assertEqual(self.warns(), [])


if __name__ == "__main__":
    unittest.main()
