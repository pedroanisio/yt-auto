"""The film ends on its credits (AUTO-147, part A).

A CC BY image or sound asks for its credit where viewers see the work, and
the build carried the credit no further than the document's metadata. Once
the assets block is complete, `yt build` now ends the film with an animated
end-credits card: every credit line verbatim, on one or more full-frame
text cards after the editor's tail, each fading in and out, timed for
reading, with a `kind="comment"` marker so no shot, section or chapter
reading changes. The programme, its duration in build.json and the mix all
run to the last card's end.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree

from tests.test_build_audio import _idea, _sections, _wav
from ytstudio import build, sound
from ytstudio.scene_sdk import sdk

AUTHORED = """<?xml version="1.0"?>
<scene version="1.1">
  <project width="1920" height="1080" fps="30" duration="10"/>
  <assets>
    <image id="plate" src="../downloads/01-plate.png" width="100" height="100"/>
  </assets>
  <symbols>
    <symbol id="fx-all">
      <shape id="s1" shape="rect" width="100" height="100" fill="#FFFFFF"/>
    </symbol>
  </symbols>
  <composition/>
</scene>
"""

IMAGE_CREDIT = "Plate by A. Author / Wikimedia Commons, CC BY 4.0"
CUE_CREDIT = "Pad by B. Maker (Freesound), CC BY 4.0"


def _cue(**over):
    row = {"cue_id": "M1", "kind": "music", "at": 0, "seconds": 30, "level": "-30 dBFS",
           "description": "a pad", "file": "sound/pad.wav", "fade_out": 1.0,
           "licence": "CC BY 4.0", "credit": CUE_CREDIT}
    row.update(over)
    return row


class Project(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "vfx").mkdir()
        (self.root / "downloads").mkdir()
        (self.root / "raw").mkdir()
        (self.root / "downloads" / "01-plate.png").write_bytes(b"x")
        (self.root / "vfx" / "open.scene.xml").write_text(AUTHORED)
        (self.root / "raw" / "08-downloads.json").write_text(json.dumps([
            {"title": "a micrograph", "status": "ok", "file": "downloads/01-plate.png",
             "licence": "CC BY 4.0"}]))
        self.dossier = {"assets": [{"title": "a micrograph", "kind": "image",
                                    "licence": "CC BY 4.0", "credit_line": IMAGE_CREDIT}]}
        _wav(self.root / "sound" / "pad.wav", 30)
        self.vfx = {"scene": {"by_label": {
            label: {"file": "vfx/open.scene.xml", "symbol": "fx-all"}
            for label in ("1.1", "1.2", "2.1")}}}

    def build(self, cues=None, dossier="default", **kw):
        sound_ = {"cues": cues} if cues is not None else None
        return build.SceneBuilder(
            _idea(), _sections(), self.root, vfx=self.vfx, sound=sound_,
            dossier=self.dossier if dossier == "default" else dossier,
            verbose=False, **kw).build()

    @staticmethod
    def tail_end(manifest) -> float:
        return round(max(s["at"] + s["seconds"] for s in manifest["shots"])
                     + manifest["editorial"]["tail_seconds"], 3)

    @staticmethod
    def cards(data):
        root = ElementTree.fromstring(data)
        texts = {t.get("id"): t for t in root.iter("text")
                 if t.get("id", "").startswith("credits-card-")}
        layers = [l for l in root.iter("layer") if l.get("asset") in texts]
        return root, texts, sorted(layers, key=lambda l: float(l.get("start")))


class Card(Project):
    def test_the_film_ends_with_a_credits_card_holding_the_line(self):
        data, manifest = self.build(audio=False)
        _, texts, layers = self.cards(data)
        self.assertEqual(list(texts), ["credits-card-1"])
        self.assertEqual(float(layers[0].get("start")), self.tail_end(manifest))
        self.assertIn(IMAGE_CREDIT, texts["credits-card-1"].get("text"))

    def test_the_document_still_checks(self):
        data, _ = self.build(cues=[_cue()])
        errors = [str(f) for f in sdk().read(data).check() if str(f).startswith("error")]
        self.assertEqual(errors, [])

    def test_the_card_fades_in_and_out(self):
        data, _ = self.build(audio=False)
        _, _, layers = self.cards(data)
        layer = layers[0]
        fade = next(a for a in layer.iter("animate") if a.get("property") == "opacity")
        keys = [(float(k.get("time")), float(k.get("value"))) for k in fade.iter("key")]
        start, end = float(layer.get("start")), float(layer.get("end"))
        self.assertEqual(keys[0], (start, 0.0))
        self.assertEqual(keys[-1], (end, 0.0))
        self.assertIn((round(start + 0.5, 3), 1.0), keys)
        self.assertIn((round(end - 0.5, 3), 1.0), keys)

    def test_a_hoisted_image_and_a_cc_by_cue_are_both_credited(self):
        data, manifest = self.build(cues=[_cue()])
        _, texts, _ = self.cards(data)
        text = "\n".join(t.get("text") for t in texts.values())
        self.assertIn(IMAGE_CREDIT, text)
        self.assertIn(CUE_CREDIT, text)
        self.assertEqual({r["credit"] for r in manifest["credits"]}, {IMAGE_CREDIT, CUE_CREDIT})

    def test_the_runtime_is_the_last_cards_end(self):
        data, manifest = self.build(cues=[_cue()])
        root, _, layers = self.cards(data)
        end = float(layers[-1].get("end"))
        self.assertEqual(float(root.find("project").get("duration")), end)
        self.assertEqual(manifest["timeline"]["runtime"], end)
        self.assertEqual(manifest["scene"]["duration"], end)
        card = manifest["credits_card"]
        self.assertEqual(card["at"], self.tail_end(manifest))
        self.assertEqual(card["cards"], 1)
        self.assertAlmostEqual(card["at"] + card["seconds"], end, places=3)
        self.assertGreaterEqual(card["seconds"], build.CREDITS_MIN_SECONDS)

    def test_the_markers_are_comments_and_the_shots_are_unchanged(self):
        data, _ = self.build(audio=False)
        root, _, _ = self.cards(data)
        credits = [m for m in root.iter("marker") if m.get("label") == "credits"]
        self.assertEqual(len(credits), 1)
        self.assertEqual(credits[0].get("kind"), "comment")
        self.assertIsNone(credits[0].get("duration"))
        with mock.patch.object(build, "credit_rows", return_value=[]):
            plain, _ = self.build(audio=False)
        self.assertEqual(self.cards(plain)[1], {})
        shots = build.shots_from_scene(data)
        self.assertEqual(shots, build.shots_from_scene(plain))
        self.assertEqual([s["label"] for s in shots], ["1.1", "1.2", "2.1"])

    def test_no_credit_no_card_and_no_change_to_the_runtime(self):
        (self.root / "vfx" / "open.scene.xml").write_text(
            AUTHORED.replace("../downloads/01-plate.png", "maps/local.png"))
        synth = _cue(licence=sound.SYNTH_LICENCE, credit=sound.SYNTH_CREDIT)
        data, manifest = self.build(cues=[synth])
        root, texts, _ = self.cards(data)
        self.assertEqual(texts, {})
        self.assertEqual(manifest["credits"], [])
        self.assertNotIn("credits_card", manifest)
        self.assertEqual(manifest["timeline"]["runtime"], self.tail_end(manifest))
        self.assertEqual(float(root.find("project").get("duration")), self.tail_end(manifest))

    def test_a_long_list_splits_into_cards_of_at_most_twelve_seconds(self):
        rows = [{"credit": f"Asset {n:02d}: a long attribution line for one placed file, by "
                           f"its maker number {n}, under CC BY 4.0",
                 "licence": "CC BY 4.0", "src": f"downloads/{n}.png", "id": f"img-{n}",
                 "tier": 1, "required": True} for n in range(40)]
        with mock.patch.object(build, "credit_rows", return_value=rows):
            data, manifest = self.build(audio=False)
        _, texts, layers = self.cards(data)
        self.assertGreater(len(layers), 1)
        self.assertEqual(manifest["credits_card"]["cards"], len(layers))
        for layer in layers:
            self.assertLessEqual(float(layer.get("end")) - float(layer.get("start")),
                                 build.CREDITS_MAX_SECONDS)
        for a, b in zip(layers, layers[1:]):
            self.assertEqual(a.get("end"), b.get("start"))
        shown = "\n".join(t.get("text") for t in texts.values())
        for row in rows:
            self.assertIn(row["credit"], shown)


class Mix(Project):
    def track(self, data, cue_id="M1"):
        return next(t for t in ElementTree.fromstring(data).iter("audioTrack")
                    if t.get("id") == f"cue-{cue_id}")

    def test_a_bed_reaching_the_tail_runs_on_under_the_card(self):
        data, manifest = self.build(cues=[_cue()])
        end = manifest["timeline"]["runtime"]
        track = self.track(data)
        self.assertAlmostEqual(float(track.get("start")) + float(track.get("clipOut")), end,
                               places=3)
        self.assertEqual(float(track.get("fadeOut")), 1.0)
        self.assertEqual(manifest["sound"]["extended"], [{"cue": "M1", "end": end}])
        self.assertEqual(manifest["sound"]["silence"], [])

    def test_with_no_bed_at_the_tail_the_card_is_a_silence_window(self):
        data, manifest = self.build(cues=[_cue(seconds=4)])
        card = manifest["credits_card"]
        self.assertEqual(float(self.track(data).get("clipOut")), 4)
        self.assertEqual(manifest["sound"]["extended"], [])
        self.assertEqual(manifest["sound"]["silence"],
                         [{"at": card["at"], "end": manifest["timeline"]["runtime"],
                           "why": "credits card"}])


if __name__ == "__main__":
    unittest.main()
