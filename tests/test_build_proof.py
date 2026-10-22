"""build.py: each world against its proof frame before the encode (AUTO-70).

The defect behind a 3.0 scorecard (a world restarted mid-section, 86
includes for 88 shots) was found by qa.proof_frame_findings after ~90 min of
encode, gate and QA. `build.proof_check` renders the built document at each
world's proof-frame time through the still renderer (cached, on a host) and
compares the stage bands with the gate's proof frame: a mismatch is an
`error:` finding naming the world, the time and the difference, so the scene
is not written without --force, and the build is not publishable. The
renderer is a fake: no engine, no ssh.
"""

import argparse
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

from tests.test_build_include import AUTHORED, _idea
from ytstudio import build, proofframes
from ytstudio.script import Section, Shot

W, H = 640, 360


def _frame(x: int | None) -> bytes:
    """A frame with a bright block at `x` inside the stage band (None: empty)."""
    im = Image.new("L", (W, H), 40)
    if x is not None:
        ImageDraw.Draw(im).rectangle([x, 100, x + 200, 260], fill=230)
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


WORLD = {"id": "fx-1-1", "start": 0.0, "seconds": 7.0, "shots": ["1.1", "1.2"]}


def _vfx(*worlds):
    return {"scene": {"by_label": {label: {"file": "vfx/open.scene.xml", "symbol": w["id"]}
                                   for w in worlds for label in w["shots"]}},
            "authored": [{"file": "vfx/open.scene.xml", "worlds": list(worlds)}]}


def _sections():
    return [Section(heading="Open", shots=[
        Shot(label="1.1", n=1, visual="v", narration="w", seconds=4, at=0),
        Shot(label="1.2", n=2, visual="v", narration="w", seconds=3, at=4)])]


class _Project(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "vfx").mkdir()
        (self.root / "vfx" / "open.scene.xml").write_text(AUTHORED)
        self.film_x = 50
        self.renders = []

    def tearDown(self):
        self._tmp.cleanup()

    def proof(self, name="open__fx-1-1__2s.png", x=50):
        path = self.root / "vfx" / "frames" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_frame(x))
        return path

    def still(self, data, at, *, host, files, **kw):
        self.renders.append({"at": at, "host": host, "files": sorted(files),
                             "graded": b"<colorManagement" in data})
        return _frame(self.film_x), "rendered"


class ProofCheck(_Project):
    def built(self, vfx=None, look=None):
        builder = build.SceneBuilder(_idea(), _sections(), self.root, audio=False,
                                     verbose=False, vfx=vfx or _vfx(WORLD), look=look)
        return builder.build()

    def check(self, data, manifest, vfx=None, host="h"):
        return build.proof_check(data, manifest, vfx or _vfx(WORLD), self.root,
                                 host=host, render_still=self.still)

    def test_a_mismatch_is_an_error_naming_world_time_and_difference_and_not_publishable(self):
        self.proof(x=50)
        self.film_x = 400                       # the world restarted: another pose
        data, manifest = self.built()
        self.assertTrue(manifest["publishable"], manifest["findings"])
        record = self.check(data, manifest)
        errors = [f for f in manifest["findings"] if f.startswith("error")]
        self.assertEqual(len(errors), 1, manifest["findings"])
        for needle in ("fx-1-1", "2s", f"against {proofframes.PROOF_DIFF:g}"):
            self.assertIn(needle, errors[0])
        self.assertFalse(manifest["publishable"])
        self.assertEqual(record["status"], "mismatch")
        self.assertEqual(record["mismatched"], ["fx-1-1"])
        self.assertEqual([r["at"] for r in self.renders], [2.0])
        row = record["worlds"][0]
        self.assertEqual((row["world"], row["t"], row["status"]), ("fx-1-1", 2.0, "mismatch"))
        self.assertGreater(row["difference"], proofframes.PROOF_DIFF)
        # the film's frame is kept beside the manifest for a reviewer
        self.assertTrue((self.root / build.PROOF_DIR / row["frame"]).is_file())

    def test_a_match_files_nothing_and_leaves_publishable_alone(self):
        self.proof(x=50)
        self.film_x = 50
        data, manifest = self.built()
        record = self.check(data, manifest)
        self.assertEqual([f for f in manifest["findings"] if "proof" in f], [])
        self.assertTrue(manifest["publishable"])
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["mismatched"], [])

    def test_the_still_is_rendered_from_the_built_bytes_with_the_include_copies_shipped(self):
        self.proof()
        data, manifest = self.built()
        self.check(data, manifest)
        self.assertEqual(self.renders[0]["host"], "h")
        self.assertIn("vfx/open.include.scene.xml", self.renders[0]["files"])

    def test_no_render_host_is_not_checked_and_said(self):
        self.proof()
        data, manifest = self.built()
        record = self.check(data, manifest, host="")
        self.assertEqual(record["status"], "not checked")
        self.assertEqual(self.renders, [])
        self.assertTrue(manifest["publishable"])
        warned = [f for f in manifest["findings"] if f.startswith("warning") and "proof" in f]
        self.assertEqual(len(warned), 1, manifest["findings"])

    def test_no_proof_frames_is_no_check_and_no_finding(self):
        data, manifest = self.built()
        record = self.check(data, manifest)
        self.assertEqual(record["status"], "no proof frames")
        self.assertEqual(self.renders, [])
        self.assertEqual([f for f in manifest["findings"] if "proof" in f], [])

    def test_a_frame_that_did_not_come_back_is_unread_not_a_mismatch(self):
        self.proof()
        data, manifest = self.built()

        def nothing(data, at, *, host, files, **kw):
            return None, "error: the engine fell over"
        record = build.proof_check(data, manifest, _vfx(WORLD), self.root,
                                   host="h", render_still=nothing)
        self.assertEqual(record["status"], "incomplete")
        self.assertEqual(record["worlds"][0]["status"], "unread")
        self.assertIn("fell over", record["worlds"][0]["reason"])
        self.assertFalse([f for f in manifest["findings"] if f.startswith("error")])
        self.assertTrue(manifest["publishable"])


class FakeHarness:
    """Stands in for Harness in build.run: the edit and no look."""

    def __init__(self, *a, **kw):
        pass

    def decide(self, name, prompt, schema):
        if name == "edit":
            return {"coverage": [], "captions_off": [], "tail_seconds": 0.5,
                    "notes": "", "blocking": []}
        return None

    def report(self):
        return {}


class Run(_Project):
    """The check sits after builder.build(): the scene is not written on a mismatch."""

    def setUp(self):
        super().setUp()
        (self.root / "idea.json").write_text(json.dumps(
            {"title": "A film", "genre": "explainer", "description": "p"}))
        (self.root / "script.json").write_text(json.dumps({"sections": [
            {"heading": "Open", "shots": [
                {"label": "1.1", "n": 1, "visual": "v", "narration": "w", "seconds": 4, "at": 0},
                {"label": "1.2", "n": 2, "visual": "v", "narration": "w", "seconds": 3, "at": 4}]}]}))
        (self.root / "vfx.json").write_text(json.dumps(_vfx(WORLD)))
        self.proof(x=50)

    def build_run(self, *extra):
        from ytstudio import cli
        args = cli.build_parser().parse_args(
            ["build", "--project", str(self.root), "--no-audio", "--quiet", *extra])
        with mock.patch.object(build, "Harness", FakeHarness), \
                mock.patch.object(build, "look_host", lambda: "h"), \
                mock.patch("ytstudio.render.engine_render_still", self.still), \
                mock.patch("sys.stdout", new=io.StringIO()):
            return build.run(args)

    def test_a_mismatch_stops_the_build_before_the_scene_is_written(self):
        self.film_x = 400
        self.assertEqual(self.build_run(), 1)
        self.assertFalse((self.root / "scene.xml").exists())
        self.assertEqual([r["at"] for r in self.renders], [2.0])

    def test_force_writes_it_with_the_record_in_the_manifest(self):
        self.film_x = 400
        self.assertEqual(self.build_run("--force"), 0)
        self.assertTrue((self.root / "scene.xml").exists())
        manifest = json.loads((self.root / "build.json").read_text())
        self.assertEqual(manifest["proof_check"]["status"], "mismatch")
        self.assertFalse(manifest["publishable"])
        self.assertTrue(any(f.startswith("error") and "fx-1-1" in f
                            for f in manifest["findings"]))

    def test_a_match_writes_the_scene(self):
        self.film_x = 50
        self.assertEqual(self.build_run(), 0)
        manifest = json.loads((self.root / "build.json").read_text())
        self.assertEqual(manifest["proof_check"]["status"], "ok")
        self.assertTrue(manifest["publishable"])


if __name__ == "__main__":
    unittest.main()
