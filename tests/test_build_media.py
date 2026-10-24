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


# --------------------------------------------------------------------------- #
# AUTO-143: licensed footage fills a gap as a <video>, measured without ffprobe
# --------------------------------------------------------------------------- #

def box(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", 8 + len(body)) + kind + body


def mvhd(timescale: int, duration: int, version: int = 0) -> bytes:
    """A movie header box: version, flags, times, timescale, duration, the rest."""
    if version == 1:
        times = struct.pack(">QQIQ", 1, 2, timescale, duration)
    else:
        times = struct.pack(">IIII", 1, 2, timescale, duration)
    return box(b"mvhd", bytes([version, 0, 0, 0]) + times + bytes(80))


def mp4(seconds: float = 5.0, *, timescale: int = 1000) -> bytes:
    return (box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2mp41")
            + box(b"mdat", bytes(64))
            + box(b"moov", mvhd(timescale, int(seconds * timescale))))


class ClipSeconds(unittest.TestCase):
    """build.clip_seconds reads an mp4's length from its movie header, stdlib
    struct and seek only: no ffprobe runs on this workstation."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def seconds(self, data: bytes, name: str = "a.mp4"):
        path = self.dir / name
        path.write_bytes(data)
        return build.clip_seconds(path)

    def test_version_0_and_version_1_headers(self):
        self.assertEqual(self.seconds(mp4(5.0)), 5.0)
        moov = box(b"moov", mvhd(90000, 90000 * 12 + 45000, version=1))
        self.assertEqual(self.seconds(box(b"ftyp", b"isom") + moov, "b.mov"), 12.5)
        self.assertEqual(self.seconds(mp4(3.0), "c.M4V"), 3.0)

    def test_a_moov_after_a_large_mdat_is_found(self):
        big = bytes(1_200_000)
        data = (box(b"ftyp", b"isom") + box(b"mdat", big)
                + box(b"moov", box(b"udta", b"") + mvhd(600, 600 * 7)))
        self.assertEqual(self.seconds(data), 7.0)

    def test_a_box_with_a_64_bit_largesize(self):
        body = bytes(5000)
        large = struct.pack(">I4sQ", 1, b"mdat", 16 + len(body)) + body
        data = box(b"ftyp", b"isom") + large + box(b"moov", mvhd(1000, 4000))
        self.assertEqual(self.seconds(data), 4.0)

    def test_a_last_box_of_size_0_runs_to_the_end(self):
        moov_body = mvhd(1000, 9000)
        data = box(b"ftyp", b"isom") + struct.pack(">I4s", 0, b"moov") + moov_body
        self.assertEqual(self.seconds(data), 9.0)

    def test_what_has_no_length_is_none(self):
        self.assertIsNone(self.seconds(mp4(5.0, timescale=1)[:0] + box(b"ftyp", b"isom")
                                       + box(b"moov", mvhd(0, 5000))))
        self.assertIsNone(self.seconds(box(b"moov", mvhd(1000, 0))))
        self.assertIsNone(self.seconds(box(b"moov", mvhd(1000, 0xFFFFFFFF))))
        self.assertIsNone(self.seconds(box(b"moov", mvhd(1000, 0xFFFFFFFFFFFFFFFF,
                                                         version=1))))
        self.assertIsNone(self.seconds(box(b"ftyp", b"isom") + box(b"mdat", bytes(8))))
        self.assertIsNone(self.seconds(box(b"moov", box(b"trak", b""))))
        self.assertIsNone(self.seconds(b"\x1aE\xdf\xa3" + bytes(40), "a.webm"))
        self.assertIsNone(self.seconds(mp4(5.0), "a.webm"))           # not read by name
        self.assertIsNone(build.clip_seconds(self.dir / "missing.mp4"))


#: A section's world that embeds a downloaded mesh: the credit manifest must
#: still list it, so local_media is never filtered to pictures.
MESH_DOC = """<?xml version="1.0"?>
<scene version="1.1">
  <project width="1920" height="1080" fps="30" duration="4"/>
  <assets>
    <mesh id="ship" src="../downloads/03-ship.glb" format="glb"/>
  </assets>
  <symbols>
    <symbol id="fx-1-2">
      <shape id="s1" shape="rect" width="100" height="100" fill="#FFFFFF"/>
    </symbol>
  </symbols>
  <composition/>
</scene>
"""


class Footage(unittest.TestCase):
    """A downloaded clip that shows what a shot describes is placed as a
    <video>, never as an <image>; an audio file or a document never draws a
    picture; a clip shorter than its shot leaves the shot to the editor."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        for d in ("downloads", "raw", "vfx"):
            (self.root / d).mkdir()
        files = {"01-a.png": png_1x1(), "02-launch.mp4": mp4(6.0),
                 "04-roar.mp3": b"ID3", "05-paper.pdf": b"%PDF-1.4",
                 "06-short.mp4": mp4(1.0), "07-raw.webm": b"\x1aE\xdf\xa3",
                 "03-ship.glb": b"glTF"}
        for name, data in files.items():
            (self.root / "downloads" / name).write_bytes(data)
        rows = [("a comet still", "01-a.png", "image/png"),
                ("the 1969 launch film", "02-launch.mp4", "video/mp4"),
                ("a ship model", "03-ship.glb", "model/gltf-binary"),
                ("the engine roar", "04-roar.mp3", "audio/mpeg"),
                ("the launch report", "05-paper.pdf", "application/pdf"),
                ("a short splash", "06-short.mp4", "video/mp4"),
                ("a raw reel", "07-raw.webm", "video/webm")]
        (self.root / "raw" / "08-downloads.json").write_text(json.dumps([
            {"title": t, "url": f"https://h.example/{f}", "status": "ok",
             "file": f"downloads/{f}", "licence": "CC0", "content_type": ct}
            for t, f, ct in rows]))
        # the dossier knows two of them; the rest take their kind from the manifest
        self.dossier = {"assets": [
            {"title": "a comet still", "kind": "image", "licence": "CC0",
             "credit_line": "ESA", "use_for": "the opening"},
            {"title": "the 1969 launch film", "kind": "video", "licence": "CC0",
             "credit_line": "NASA", "use_for": "the launch"}]}

    def shots(self, *assets, seconds=4):
        return [Section(heading="Open", shots=[
            Shot(label=f"1.{n}", n=n, visual=f"shot {n}", narration="Look.",
                 seconds=seconds, at=(n - 1) * seconds, asset=a)
            for n, a in enumerate(assets, 1)])]

    def builder(self, sections, edit=None, vfx=None):
        idea = Idea.from_raw({"title": "A film", "genre": "explainer", "description": "p"})
        return build.SceneBuilder(idea, sections, self.root, dossier=self.dossier,
                                  vfx=vfx, edit=edit, audio=False, verbose=False)

    def test_local_media_keeps_every_kind_and_measures_the_clips(self):
        media = {m.path: m for m in build.local_media(self.root, self.dossier)}
        self.assertEqual(media["downloads/02-launch.mp4"].kind, "video")
        self.assertEqual(media["downloads/02-launch.mp4"].seconds, 6.0)
        self.assertEqual(media["downloads/03-ship.glb"].kind, "3d-model")
        self.assertEqual(media["downloads/04-roar.mp3"].kind, "audio")
        self.assertEqual(media["downloads/05-paper.pdf"].kind, "document")
        self.assertIsNone(media["downloads/01-a.png"].seconds)
        self.assertIsNone(media["downloads/07-raw.webm"].seconds)
        self.assertEqual([m.path for m in build.pictures(list(media.values()))],
                         ["downloads/01-a.png", "downloads/02-launch.mp4",
                          "downloads/06-short.mp4", "downloads/07-raw.webm"])

    def test_a_generic_content_type_takes_its_kind_from_the_suffix(self):
        # The Internet Archive serves clips as application/octet-stream; with
        # no dossier kind, the file's suffix decides, so a clip is footage.
        files = {"08-reel.mp4": mp4(6.0), "09-reel.ogv": b"OggS", "10-ship.glb": b"glTF",
                 "11-photo.jpg": b"\xff\xd8", "12-blob.bin": b"\x00"}
        for name, data in files.items():
            (self.root / "downloads" / name).write_bytes(data)
        rows = [("an archive reel", "08-reel.mp4", "application/octet-stream"),
                ("an ogg reel", "09-reel.ogv", "binary/octet-stream"),
                ("a mesh", "10-ship.glb", "application/octet-stream"),
                ("a photo", "11-photo.jpg", ""),
                ("a blob", "12-blob.bin", "application/octet-stream"),
                ("a typed still", "01-a.png", "image/png")]
        (self.root / "raw" / "08-downloads.json").write_text(json.dumps([
            {"title": t, "url": f"https://archive.example/{f}", "status": "ok",
             "file": f"downloads/{f}", "licence": "CC0", "content_type": ct}
            for t, f, ct in rows]))
        media = {m.path: m for m in build.local_media(self.root, {"assets": []})}
        self.assertEqual({p[len("downloads/"):]: (m.kind, m.seconds) for p, m in media.items()},
                         {"08-reel.mp4": ("video", 6.0), "09-reel.ogv": ("video", None),
                          "10-ship.glb": ("3d-model", None), "11-photo.jpg": ("image", None),
                          "12-blob.bin": ("image", None), "01-a.png": ("image", None)})
        self.dossier = {"assets": []}
        data, manifest = self.builder(self.shots("an archive reel")).build()
        root = ElementTree.fromstring(data)
        self.assertIsNotNone(root.find(".//assets/video[@src='downloads/08-reel.mp4']"))
        self.assertIsNone(root.find(".//assets/image[@src='downloads/08-reel.mp4']"))
        self.assertEqual(manifest["layers"]["footage"], 1)

    def test_a_matched_clip_is_a_video_layer_with_its_licence_and_no_sound(self):
        data, manifest = self.builder(self.shots("a comet still",
                                                 "the 1969 launch film")).build()
        root = ElementTree.fromstring(data)
        video = root.find(".//assets/video[@id='clip-1.2']")
        self.assertIsNotNone(video, data.decode()[:2000])
        self.assertEqual(video.get("src"), "downloads/02-launch.mp4")
        self.assertEqual(video.get("license"), "CC0")
        self.assertEqual(video.get("credit"), "NASA")
        self.assertEqual(video.get("hasAudio"), "false")
        self.assertEqual(float(video.get("duration")), 6.0)
        self.assertIsNone(root.find(".//assets/image[@src='downloads/02-launch.mp4']"))
        self.assertIsNotNone(root.find(".//assets/image[@id='img-1.1']"))
        layer = root.find(".//layer[@id='shot-1.2']")
        self.assertEqual(layer.get("asset"), "clip-1.2")
        self.assertEqual((layer.get("start"), layer.get("end")), ("4", "8"))
        self.assertEqual(manifest["layers"]["footage"], 1)
        self.assertEqual(manifest["layers"]["image"], 1)
        errors = [str(f) for f in sdk().read(data).check() if str(f).startswith("error")]
        self.assertEqual(errors, [])

    def test_an_audio_file_or_a_document_never_draws_a_shot(self):
        builder = self.builder(self.shots("the engine roar", "the launch report"))
        placed = build.timeline(builder.sections, builder.voice)
        media = build.local_media(self.root, self.dossier)
        drawn = builder._drawn(placed, media)
        self.assertEqual(drawn, {"1.1": ("none", None), "1.2": ("none", None)})
        data, manifest = builder.build()
        root = ElementTree.fromstring(data)
        for src in ("downloads/04-roar.mp3", "downloads/05-paper.pdf"):
            self.assertIsNone(root.find(f".//assets/*[@src='{src}']"))
        self.assertEqual(manifest["layers"]["placeholder"], 2)

    def test_an_embedded_mesh_listed_in_the_manifest_still_builds(self):
        (self.root / "vfx" / "open.scene.xml").write_text(MESH_DOC)
        vfx = {"scene": {"by_label": {"1.1": {"file": "vfx/open.scene.xml",
                                              "symbol": "fx-1-2"}}}}
        _, manifest = self.builder(self.shots("a ship model"), vfx=vfx).build()
        self.assertEqual([f for f in manifest["findings"] if f.startswith("error")], [])

    def test_a_clip_shorter_than_its_shot_is_left_to_the_editor(self):
        builder = self.builder(self.shots("a short splash"))
        placed = build.timeline(builder.sections, builder.voice)
        notes: list[str] = []
        drawn = builder._drawn(placed, build.local_media(self.root, self.dossier), notes)
        self.assertEqual(drawn["1.1"], ("none", None))
        self.assertEqual(len(notes), 1)
        self.assertIn("downloads/06-short.mp4", notes[0])
        self.assertTrue(notes[0].startswith("warning:"))
        _, manifest = builder.build()
        self.assertEqual(manifest["layers"]["placeholder"], 1)
        self.assertTrue(any("06-short.mp4" in f for f in manifest["findings"]))

    def edit(self, clip):
        return {"coverage": [{"label": "1.1", "action": "footage", "clip": clip,
                              "reason": "it shows the launch"}],
                "captions_off": [], "tail_seconds": 0.5, "notes": "", "blocking": []}

    def test_the_editors_footage_choice_is_placed(self):
        data, manifest = self.builder(self.shots("nothing matches this"),
                                      edit=self.edit("downloads/02-launch.mp4")).build()
        video = ElementTree.fromstring(data).find(".//assets/video[@id='clip-1.1']")
        self.assertIsNotNone(video)
        self.assertEqual(manifest["editorial"]["applied"]["1.1"], "footage")
        self.assertEqual(manifest["layers"]["footage"], 1)

    def test_a_shorter_clip_or_one_the_project_lacks_is_refused_and_blocked(self):
        for clip in ("downloads/06-short.mp4", "downloads/99-none.mp4",
                     "downloads/01-a.png", "downloads/04-roar.mp3"):
            data, manifest = self.builder(self.shots("nothing matches this"),
                                          edit=self.edit(clip)).build()
            self.assertEqual(manifest["editorial"]["applied"]["1.1"], "block", clip)
            self.assertEqual(manifest["layers"]["placeholder"], 1)
            self.assertTrue(any(f.startswith("warning:") and clip in f
                                for f in manifest["findings"]), (clip, manifest["findings"]))
            self.assertIsNone(ElementTree.fromstring(data).find(".//assets/video"))

    def test_a_clip_of_unknown_length_is_placed_with_a_warning(self):
        data, manifest = self.builder(self.shots("nothing matches this"),
                                      edit=self.edit("downloads/07-raw.webm")).build()
        video = ElementTree.fromstring(data).find(".//assets/video[@id='clip-1.1']")
        self.assertIsNotNone(video)
        self.assertEqual(float(video.get("duration")), 4.0)        # the shot it covers
        self.assertTrue(any(f.startswith("warning:") and "07-raw.webm" in f
                            and "length" in f for f in manifest["findings"]))

    def test_the_brief_and_the_edit_prompt_carry_the_footage(self):
        builder = self.builder(self.shots("the 1969 launch film", "nothing matches this"))
        brief = builder.brief()
        row = brief["shots"][0]
        self.assertEqual((row["drawn_by"], row["clip"], row["clip_seconds"], row["image"]),
                         ("footage", "downloads/02-launch.mp4", 6.0, ""))
        self.assertEqual((brief["shots"][1]["clip"], brief["shots"][1]["clip_seconds"]),
                         ("", None))
        self.assertEqual([m["path"] for m in brief["images"]], ["downloads/01-a.png"])
        launch = next(m for m in brief["footage"] if m["path"] == "downloads/02-launch.mp4")
        self.assertEqual((launch["seconds"], launch["credit"], launch["licence"]),
                         (6.0, "NASA", "CC0"))
        self.assertNotIn("downloads/04-roar.mp3", json.dumps(brief))
        prompt = build.edit_prompt(brief)
        self.assertIn("Footage the project has", prompt)
        self.assertIn('"seconds": 6.0', prompt)
        self.assertIn("`footage`", prompt)
        self.assertIn("footage", build.COVERAGE_ACTIONS)
        self.assertIn("clip", build.EDIT_SCHEMA["properties"]["coverage"]["items"]["properties"])
        self.assertIn("licensed clip", build.ROLE)

    def test_an_embedded_clip_is_credited_as_a_video_and_an_image_as_an_image(self):
        doc = MESH_DOC.replace(
            '<mesh id="ship" src="../downloads/03-ship.glb" format="glb"/>',
            '<video id="v" src="../downloads/02-launch.mp4" width="1920" height="1080" '
            'fps="30" duration="6"/>\n    '
            '<image id="i" src="../downloads/01-a.png" width="10" height="10"/>')
        (self.root / "vfx" / "open.scene.xml").write_text(doc)
        vfx = {"scene": {"by_label": {"1.1": {"file": "vfx/open.scene.xml",
                                              "symbol": "fx-1-2"}}}}
        data, manifest = self.builder(self.shots("nothing"), vfx=vfx).build()
        root = ElementTree.fromstring(data)
        video = root.find(".//assets/video[@id='credit-02-launch']")
        self.assertIsNotNone(video)
        self.assertEqual((video.get("credit"), video.get("hasAudio")), ("NASA", "false"))
        self.assertIsNone(root.find(".//assets/image[@src='downloads/02-launch.mp4']"))
        self.assertIsNotNone(root.find(".//assets/image[@id='credit-01-a']"))
        self.assertEqual([f for f in manifest["findings"] if f.startswith("error")], [])


if __name__ == "__main__":
    unittest.main()
