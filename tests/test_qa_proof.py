"""qa.py: the film against the authors' proof frames (AUTO-54).

The vfx gate reviews proof frames of the authored world; QA reviews the
composed film; nothing checked that the film shows what the author built.
`proof_frame_findings` maps each world's first proof frame to its film time
(`worlds[].start` + T in the file name), renders the film there through
`render_frames`, and files a blocking `proof-mismatch` owned by build when
the two stage bands differ. The images are synthetic PNGs; render_frames is
patched: no engine, no ssh.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

from ytstudio import improve, proofframes, qa

W, H = 640, 360


def _png(path: Path, x: int | None) -> Path:
    """A frame with a bright block at `x` inside the stage band (None: empty)."""
    im = Image.new("L", (W, H), 40)
    if x is not None:
        ImageDraw.Draw(im).rectangle([x, 100, x + 200, 260], fill=230)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return path


def _vfx(*worlds):
    return {"authored": [{"file": "vfx/v02/open.scene.xml", "version": "v02",
                          "worlds": list(worlds)}]}


WORLD = {"id": "fx-open", "start": 10.0, "seconds": 8.0, "shots": ["1.1", "1.2"]}
SHOTS = [{"label": "1.1", "at": 10.0, "seconds": 4.0, "section": "Open", "asset": "effect"},
         {"label": "1.2", "at": 14.0, "seconds": 4.0, "section": "Open", "asset": "effect"}]


class ProofFrames(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.out = self.home / "qa" / "pass-01"
        self.film_x = 50
        self.asked = []

    def tearDown(self):
        self._tmp.cleanup()

    def proof(self, name, x=50):
        return _png(self.home / "vfx" / "v02" / "frames" / name, x)

    def fake_render(self, args, project, scene, samples, frames_dir, *, render=None):
        for row in samples:
            self.asked.append(row["t"])
            _png(frames_dir / row["frame"], self.film_x)
        return []

    def run_check(self, vfx):
        with mock.patch.object(qa, "render_frames", self.fake_render):
            return qa.proof_frame_findings(None, None, "scene.xml", vfx, self.home,
                                           SHOTS, self.out)

    def test_a_film_that_differs_from_the_proof_frame_is_a_blocking_build_finding(self):
        self.proof("open__fx-open__2s.png", x=50)
        self.film_x = 400                      # the world restarted: another pose
        found, note = self.run_check(_vfx(WORLD))
        self.assertEqual(len(found), 1, found)
        f = found[0]
        self.assertEqual((f["kind"], f["owner"], f["severity"]), ("proof-mismatch", "build", "blocking"))
        self.assertEqual(f["timestamp"], 12.0)          # start 10 + T 2
        self.assertEqual(f["shot"], "1.1")
        self.assertIn("fx-open", f["problem"])
        self.assertGreater(f["difference"], qa.PROOF_DIFF)
        self.assertEqual(self.asked, [12.0])

    def test_a_film_that_matches_files_nothing(self):
        self.proof("open__fx-open__2s.png", x=50)
        self.film_x = 50
        found, _ = self.run_check(_vfx(WORLD))
        self.assertEqual(found, [])
        self.assertEqual(self.asked, [12.0])

    def test_one_frame_per_world_the_latest_by_time(self):
        for name in ("open__fx-open__6s.png", "open__fx-open__2s.png",
                     "open__fx-open__4.5s.png"):
            self.proof(name)
        self.run_check(_vfx(WORLD))
        self.assertEqual(self.asked, [16.0])

    def test_the_later_of_two_proof_frames_is_chosen(self):
        self.proof("open__fx-open__0.7s.png")
        self.proof("open__fx-open__27.9s.png")
        self.run_check(_vfx(dict(WORLD, seconds=30.0)))
        self.assertEqual(self.asked, [37.9])

    def test_the_film_time_follows_the_build_placement_and_drift_is_reported(self):
        self.proof("open__fx-open__2s.png", x=50)
        self.film_x = 400
        placed = [dict(SHOTS[0], at=11.0), dict(SHOTS[1], at=15.0)]   # build: 1.0 s later
        with mock.patch.object(qa, "render_frames", self.fake_render):
            found, _ = qa.proof_frame_findings(None, None, "scene.xml", _vfx(WORLD),
                                               self.home, placed, self.out)
        self.assertEqual(self.asked, [13.0])             # 11 + 2, not 10 + 2
        self.assertEqual(found[0]["timestamp"], 13.0)
        self.assertEqual(found[0]["start_drift"], {"plan_start": 10.0, "build_start": 11.0})
        self.assertIn("10", found[0]["problem"])
        self.assertIn("11", found[0]["problem"])

    def test_an_unplaced_shot_falls_back_to_the_plan_start_without_drift(self):
        self.proof("open__fx-open__2s.png", x=50)
        self.film_x = 400
        found, _ = self.run_check(_vfx(dict(WORLD, shots=["9.9"])))
        self.assertEqual(self.asked, [12.0])
        self.assertNotIn("start_drift", found[0])

    def test_worlds_cut_by_the_limit_are_counted_in_the_note(self):
        worlds = [dict(WORLD, id=f"w{i}") for i in range(qa.PROOF_LIMIT + 3)]
        for w in worlds:
            self.proof(f"open__{w['id']}__2s.png")
        _, note = self.run_check(_vfx(*worlds))
        self.assertEqual(len(self.asked), qa.PROOF_LIMIT)
        self.assertIn("3 world(s) not compared", note)

    def test_no_proof_frames_is_no_finding_and_a_note(self):
        found, note = self.run_check(_vfx(WORLD))
        self.assertEqual(found, [])
        self.assertTrue(note)
        self.assertEqual(self.asked, [])
        found, note = self.run_check(None)
        self.assertEqual((found, bool(note)), ([], True))

    def test_the_finding_is_mechanical_blocking_and_routes_to_build(self):
        self.proof("open__fx-open__2s.png")
        self.film_x = 400
        found, _ = self.run_check(_vfx(WORLD))
        self.assertIn("proof-mismatch", qa.ALWAYS_BLOCKING)
        self.assertIn("proof-mismatch", improve.MECHANICAL_KINDS)
        self.assertIsNotNone(improve.fingerprint(found[0]))
        scores, overall, capped = qa.cap_scores(
            {"visual_quality": 9.5, "animation_vfx": 9.5}, 9.5, found)
        self.assertEqual(capped, list(qa.CAPPED))
        self.assertIn("proof-mismatch", qa.findings_md(
            "pass-01", {"mechanical": found}, [], {"deltas": {}}, reasons=[]))

    def test_the_stage_band_only_is_compared(self):
        # a difference in the super band (top 21%) is not a film-vs-proof mismatch
        a, b = self.proof("open__fx-open__2s.png"), self.home / "film.png"
        with Image.open(a) as im:
            im = im.copy()
        ImageDraw.Draw(im).rectangle([0, 0, W, 40], fill=255)
        im.save(b)
        self.assertLess(qa.proof_diff(a, b), qa.PROOF_DIFF)


class Shared(unittest.TestCase):
    """AUTO-70: the plan and the comparison are one module, used by build and qa."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_qa_reads_the_plan_and_the_difference_from_the_shared_module(self):
        self.assertIs(qa.proof_plan, proofframes.proof_plan)
        self.assertIs(qa.proof_diff, proofframes.proof_diff)
        self.assertEqual(qa.PROOF_DIFF, proofframes.PROOF_DIFF)
        self.assertEqual(qa.PROOF_LIMIT, proofframes.PROOF_LIMIT)

    def test_a_plan_row_names_the_worlds_shots(self):
        _png(self.home / "vfx" / "v02" / "frames" / "open__fx-open__2s.png", 50)
        rows = proofframes.proof_plan(_vfx(WORLD), self.home, SHOTS)
        self.assertEqual(rows[0]["shots"], ["1.1", "1.2"])


if __name__ == "__main__":
    unittest.main()
