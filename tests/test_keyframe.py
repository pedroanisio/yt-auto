"""keyframe.py: the look-lock stage.

One composed frame presents the film's most qualified visual elements in the
chosen treatment for the operator's approval before `yt vfx` spends. What
these tests pin: the elements decision's schema and its cap; the briefs both
model calls get (props, palette roles, the type sample, the stack brief, the
operator's notes after a rejection); the document checks through the SDK; the
review loop (below the bar -> another round, at the bar -> stop, rounds
exhausted -> stop with the last score); the approval record and its digest;
the CLI registration, the stage order and the doctor's findings. The model is
never called (`harness.ENGINE` is a fake), nothing is rendered on a host (the
renderer seam writes a PNG), and no engine is asked to validate.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from claude_engine import Event, Usage
from ytstudio import cli, doctor, harness, keyframe, vfx, workspace
from tests import programs
from ytstudio.core import Idea, write_json
from ytstudio.scene_sdk import sdk

KEYFRAME_DOC = '''<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.3">
  <project width="1920" height="1080" fps="30" duration="1" background="var(--background)"/>
  <styles>
    <token name="background" value="#2B3A4A"/>
    <token name="ink" value="#F4EFE6"/>
    <token name="accent" value="#E8B65C"/>
    <token name="muted" value="#7F93A8"/>
  </styles>
  <output path="preview.mp4" codec="h264"/>
  <assets>
    <text id="t-sample" text="Um aviao de papel" width="900" height="80" size="40" color="var(--ink)" align="start"/>
    <text id="t-label-hero" text="hero: paper plane / authored" width="600" height="40" size="22" color="var(--muted)" align="start"/>
  </assets>
  <materials>
    <material id="m-paper" baseColor="#F4EFE6" roughness="0.8" doubleSided="true"/>
    <material id="m-oak" baseColor="#A8743F" roughness="0.6" doubleSided="true"/>
  </materials>
  <symbols>
    <symbol id="keyframe-look" width="1920" height="1080" duration="1">
      <camera id="cam" x="0" y="-100" z="-400" target="hero-plane" focalLength="50" active="true"/>
      <object3D id="floor" primitive="plane" material="m-oak" x="0" y="0" z="0" width="900" height="900" rotationX="90"/>
      <object3D id="wall" primitive="plane" material="m-oak" x="0" y="-200" z="400" width="900" height="400"/>
      <object3D id="hero-plane" primitive="box" material="m-paper" x="0" y="-150" z="100" width="60" height="4" depth="40"/>
      <group id="band" x="0" y="840" width="1920" height="240">
        <shape id="sw-background" shape="rect" x="60" y="40" width="80" height="80" fill="var(--background)" stroke="var(--muted)" strokeWidth="2"/>
        <shape id="sw-ink" shape="rect" x="160" y="40" width="80" height="80" fill="var(--ink)"/>
        <shape id="sw-accent" shape="rect" x="260" y="40" width="80" height="80" fill="var(--accent)"/>
        <layer id="l-sample" asset="t-sample" x="400" y="40"/>
        <layer id="l-label-hero" asset="t-label-hero" x="400" y="140"/>
      </group>
    </symbol>
  </symbols>
  <composition>
    <instance id="use-look" symbol="keyframe-look" start="0" end="1"/>
  </composition>
  <lights>
    <light id="sun" type="directional" color="#FFF1D0" intensity="3" yaw="200" pitch="-30" castShadow="true"/>
    <light id="fill" type="ambient" color="#5B7BA3" intensity="0.8"/>
  </lights>
</scene>
'''

STYLES = {"message": "a small true flight", "styles": [
    {"name": "3d animation", "fit": 8.5, "shows_well": "camera and plane on one timeline",
     "palette": "background shadow blue #2B3A4A, object paper white #F4EFE6, accent window "
                "highlight #FFF1D0, muted dust-gold #E8B65C, oak desk #A8743F",
     "typography": "a thin geometric sans in paper white, widely tracked",
     "motion": "one take, the camera trails the plane",
     "shots": ["0-2 s: the plane leaves the hand", "2-6 s: it crosses the sun shaft"]}]}

SCRIPT = {"logline": "One take follows a paper plane onto a desk", "lang": "pt",
          "sections": [{"heading": "One plane, one room", "shots": [
              {"label": "1.1", "visual": "a white paper plane leaves the frame edge in a sunlit room",
               "on_screen": "Um avião de papel", "narration": "", "asset": "", "seconds": 3},
              {"label": "1.2", "visual": "the plane banks past the window", "on_screen": "",
               "narration": "", "asset": "", "seconds": 4}]}]}

ELEMENTS = {
    "hero": "the folded paper plane", "treatment": "3D (3d animation)",
    "camera": "a long lens trailing the plane, shallow depth of field",
    "elements": [
        {"rank": 1, "kind": "hero", "name": "paper plane", "shows": "a folded plane, backlit",
         "why": "it is the subject of every shot", "source_kind": "authored", "source": "authored"},
        {"rank": 2, "kind": "environment", "name": "sunlit room with oak desk",
         "shows": "floor, far wall, desk", "why": "the whole geography of the shot",
         "source_kind": "props", "source": "hand_plane_no4",
         "file": "props/model/hand_plane_no4/hand_plane_no4_2k.gltf",
         "licence": "CC0 1.0 (Poly Haven)", "credit": "Hand Plane No4 by S. Mandal / CC0"},
        {"rank": 3, "kind": "key_light", "name": "window sun", "shows": "a warm key with soft shadows",
         "why": "the light the whole film is graded against", "source_kind": "props",
         "source": "studio_small", "file": "props/hdri/studio_small/studio_small_2k.hdr",
         "licence": "CC0 1.0 (Poly Haven)", "credit": "Studio Small / CC0"},
        {"rank": 4, "kind": "material", "name": "paper", "shows": "matte, translucent when backlit",
         "why": "the hero's surface", "source_kind": "authored", "source": "authored"},
        {"rank": 5, "kind": "palette", "name": "palette roles", "shows": "swatches with role names",
         "why": "every colour in the film", "source_kind": "authored", "source": "styles.json"},
        {"rank": 6, "kind": "typography", "name": "type sample", "shows": "one super",
         "why": "the only type in the film", "source_kind": "authored", "source": "script.json",
         "sample": "Um avião de papel"},
        {"rank": 7, "kind": "camera", "name": "trailing long lens", "shows": "shallow focus",
         "why": "the camera language", "source_kind": "authored", "source": "authored"}]}


def varied_png(width: int = 96, height: int = 54) -> bytes:
    im = Image.new("RGB", (width, height))
    for x in range(width):
        for y in range(height):
            im.putpixel((x, y), ((x * 5) % 256, (y * 9) % 256, 110))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def flat_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (96, 54), (12, 12, 12)).save(buf, format="PNG")
    return buf.getvalue()


class FakeEngine:
    """The three model roles of the stage: the elements decision answers
    from `answers`; the author (write tools) writes the fixture document;
    the reviewer (read-only tools) answers from `reviews`."""

    json_calls: list = []
    stream_calls: list = []
    answers: list = []
    reviews: list = []
    doc: str = KEYFRAME_DOC

    def __init__(self, config):
        self.config = config
        self.usage = Usage()
        self.usage.cost_usd = 0.02
        self.usage.calls = 1

    def ask_json(self, prompt, schema, system=None):
        FakeEngine.json_calls.append({"prompt": prompt, "schema": schema, "system": system})
        return FakeEngine.answers.pop(0) if FakeEngine.answers else json.loads(json.dumps(ELEMENTS))

    def stream(self, prompt, system=None, resume=False):
        tools = tuple(self.config.tools or ())
        FakeEngine.stream_calls.append({"prompt": prompt, "system": system,
                                        "tools": tools, "cwd": self.config.cwd,
                                        "resume": resume})
        self.session_id = "author-session"
        if "Write" in tools:
            # what an author writes now: the program that builds the document
            programs.author_program(Path(self.config.cwd), keyframe.PROGRAM_NAME,
                                    keyframe.DOC_NAME, FakeEngine.doc)
            yield Event(type="assistant", text=f"{keyframe.DOC_NAME}: {keyframe.SYMBOL_ID}")
        else:
            yield Event(type="assistant", subtype="tool_use", tool_name="Read",
                        tool_input={"file_path": keyframe.FRAME_NAME})
            review = FakeEngine.reviews.pop(0) if FakeEngine.reviews else {
                "seen": "a plane on a desk", "scores": {c: 8 for c in keyframe.CATEGORIES},
                "findings": [], "score": 8, "summary": "reads"}
            yield Event(type="assistant",
                        text="I looked.\n```json\n" + json.dumps(review) + "\n```")
        yield Event(type="result", raw={})


def fake_renderer(png: bytes = b"", calls: list | None = None):
    """The renderer seam: writes a PNG where the engine would, records the call."""
    def make(project_root, host, *, verbose=False):
        def render(doc: Path, out: Path, at: float):
            if calls is not None:
                calls.append({"doc": Path(doc), "out": Path(out), "at": at, "host": host})
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_bytes(png or varied_png())
            return True, "rendered by the fake"
        return render
    return make


class ProjectCase(unittest.TestCase):
    """A project with a treatment, a screenplay, props, an assessment and a
    licensed image, as the keyframe stage finds them."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name) / "work"
        self.root = self.work / "a-paper-plane-shot"
        self.root.mkdir(parents=True)
        self.idea = Idea(title="A paper plane shot", genre="cinematic",
                         description="one take, a paper plane lands on a desk")
        write_json(self.root / "idea.json", {"title": self.idea.title, "genre": "cinematic",
                                             "description": self.idea.description})
        write_json(self.root / "styles.json", STYLES)
        write_json(self.root / "script.json", SCRIPT)
        write_json(self.root / "dossier.json", {
            "idea": {"title": self.idea.title, "genre": "cinematic",
                     "description": self.idea.description},
            "assets": [{"title": "oak desk photo", "licence": "CC BY 2.0",
                        "credit_line": "desk by someone / CC BY 2.0",
                        "file": "downloads/desk.jpg", "commercial_ok": True},
                       {"title": "unusable", "licence": "all rights reserved",
                        "commercial_ok": False}]})
        (self.root / "downloads").mkdir()
        (self.root / "downloads" / "desk.jpg").write_bytes(b"jpg")
        for rel in ("props/model/hand_plane_no4/hand_plane_no4_2k.gltf",
                    "props/model/hand_plane_no4/textures/hand_plane_no4_diff_2k.jpg",
                    "props/hdri/studio_small/studio_small_2k.hdr"):
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_bytes(b"x")
        write_json(self.root / "props.json", {"count": 2, "items": [
            {"id": "hand_plane_no4", "kind": "model", "name": "Hand Plane No4",
             "file": "props/model/hand_plane_no4/hand_plane_no4_2k.gltf",
             "licence": "CC0 1.0 (Poly Haven)", "credit": "Hand Plane No4 by S. Mandal / CC0",
             "about": "a wooden hand plane"},
            {"id": "studio_small", "kind": "hdri", "name": "Studio Small",
             "file": "props/hdri/studio_small/studio_small_2k.hdr",
             "licence": "CC0 1.0 (Poly Haven)", "credit": "Studio Small / CC0",
             "about": "a small studio"}]})
        write_json(self.root / "assess.json", {
            "description": self.idea.title,
            "verdict": {"feasible": "yes", "summary": "real 3D with a physical camera",
                        "constructs": ["object3D@primitive=extrude", "material@transmission"],
                        "engine_limits": ["no volumetric god rays"], "missing": [],
                        "risks": [], "render_estimate": "minutes per frame at final"}})
        self._engine, self._renderer = harness.ENGINE, keyframe.RENDERER
        harness.ENGINE = FakeEngine
        FakeEngine.json_calls, FakeEngine.stream_calls = [], []
        FakeEngine.answers, FakeEngine.reviews = [], []
        FakeEngine.doc = KEYFRAME_DOC
        self.render_calls: list = []
        keyframe.RENDERER = fake_renderer(calls=self.render_calls)

    def tearDown(self):
        harness.ENGINE, keyframe.RENDERER = self._engine, self._renderer
        self._tmp.cleanup()

    def stage(self, **kw):
        project = workspace.Project(self.root)
        options = dict(model="sonnet", rounds=2, bar=7.0, verbose=False, host="root@fast",
                       work=self.work)
        options.update(kw)
        return keyframe.KeyframeStage(project, self.idea, **options)

    def run_stage(self, **kw):
        with patch.object(vfx.render, "engine_validate", lambda data, **k: []):
            return self.stage(**kw).run()


# --------------------------------------------------------------------------- #
# the elements
# --------------------------------------------------------------------------- #

class Elements(unittest.TestCase):
    def test_the_schema_asks_for_what_an_operator_reads(self):
        schema = keyframe.ELEMENTS_SCHEMA
        for field in ("hero", "treatment", "elements", "camera"):
            self.assertIn(field, schema["required"])
        item = schema["properties"]["elements"]["items"]
        for field in ("rank", "kind", "name", "why", "source_kind", "source"):
            self.assertIn(field, item["required"])
        self.assertEqual(item["properties"]["source_kind"]["enum"], ["props", "asset", "authored"])
        for kind in ("hero", "environment", "key_light", "material", "palette",
                     "typography", "camera"):
            self.assertIn(kind, item["properties"]["kind"]["enum"])

    def test_ranking_caps_at_eight_in_rank_order(self):
        raw = [{"rank": 12 - n, "kind": "material", "name": f"m{n}", "shows": "s", "why": "w",
                "source_kind": "authored", "source": "authored"} for n in range(12)]
        kept, findings = keyframe.rank_elements(raw, Path("/nowhere"))
        self.assertEqual(len(kept), keyframe.MAX_ELEMENTS)
        self.assertEqual(keyframe.MAX_ELEMENTS, 8)
        self.assertEqual([e["rank"] for e in kept], list(range(1, 9)))
        self.assertEqual(kept[0]["name"], "m11")
        self.assertEqual(len(findings), 1)                            # the drop is recorded
        self.assertIn("m0", findings[0])

    def test_a_file_the_project_does_not_hold_is_a_finding_and_the_element_is_authored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "props").mkdir()
            (root / "props" / "a.gltf").write_bytes(b"x")
            raw = [{"rank": 1, "kind": "hero", "name": "h", "shows": "s", "why": "w",
                    "source_kind": "props", "source": "a", "file": "props/a.gltf"},
                   {"rank": 2, "kind": "environment", "name": "e", "shows": "s", "why": "w",
                    "source_kind": "props", "source": "ghost", "file": "props/ghost.gltf"}]
            kept, findings = keyframe.rank_elements(raw, root)
        self.assertEqual(kept[0]["file"], "props/a.gltf")
        self.assertEqual(kept[1]["source_kind"], "authored")
        self.assertNotIn("file", kept[1])
        self.assertEqual(len(findings), 1)
        self.assertIn("ghost", findings[0])


class Briefs(ProjectCase):
    """What the two model calls are told."""

    def test_the_elements_decision_reads_props_roles_type_and_the_stack(self):
        self.run_stage()
        self.assertEqual(len(FakeEngine.json_calls), 1)
        call = FakeEngine.json_calls[0]
        prompt = call["prompt"]
        self.assertIs(call["schema"], keyframe.ELEMENTS_SCHEMA)
        self.assertIn("hand_plane_no4", prompt)                       # props
        self.assertIn("studio_small", prompt)
        self.assertIn("CC0 1.0 (Poly Haven)", prompt)                 # with their licence
        self.assertIn("oak desk photo", prompt)                       # the dossier's licensed image
        self.assertNotIn("unusable", prompt)
        self.assertIn('"background"', prompt)                         # palette roles
        self.assertIn("#2B3A4A", prompt)
        self.assertIn("Um avião de papel", prompt)                    # the supers, for the type sample
        self.assertIn('"pt"', prompt)                                 # in the film's language
        self.assertIn("3d animation", prompt)                         # the treatment
        self.assertIn("thin geometric sans", prompt)
        self.assertIn("real 3D with a physical camera", prompt)       # assess.stack_brief
        self.assertIn("material@transmission", prompt)
        self.assertIn(str(keyframe.MAX_ELEMENTS), prompt)
        self.assertIn("scene-render", call["system"])

    def test_the_author_brief_carries_the_elements_tokens_props_and_bands(self):
        self.run_stage()
        brief = (self.root / "keyframe" / "brief.md").read_text(encoding="utf-8")
        self.assertIn("paper plane", brief)
        self.assertIn("hand_plane_no4", brief)
        self.assertIn("../props/model/hand_plane_no4/hand_plane_no4_2k.gltf", brief)
        self.assertIn("../props/hdri/studio_small/studio_small_2k.hdr", brief)
        self.assertIn("../downloads/desk.jpg", brief)
        self.assertIn("var(--", brief)
        self.assertIn("--background", brief)
        self.assertIn("Um avião de papel", brief)
        self.assertIn("820", brief)                                   # the caption band
        self.assertIn("hero-", brief)
        self.assertIn(keyframe.DOC_NAME, brief)
        self.assertIn("kit.py build", brief)
        self.assertIn(keyframe.PROGRAM_NAME, brief)
        self.assertIn("--background: #2B3A4A", brief)                 # roles, from design.py
        # A role the palette never named is a fallback, and the strip must not show it
        self.assertRegex(brief, r"--negative: #[0-9A-F]{6} \(fallback")
        self.assertNotIn("--background: #2B3A4A (fallback", brief)
        roles, source = keyframe.palette_roles(STYLES)
        self.assertEqual(source, "styles.json")
        by_role = {r["role"]: r["from"] for r in roles}
        self.assertEqual((by_role["background"], by_role["negative"]), ("styles.json", "fallback"))
        tokens = (self.root / "keyframe" / "tokens.xml").read_text()
        self.assertIn('name="background" value="#2B3A4A"', tokens)
        self.assertTrue((self.root / "keyframe" / "sr.py").is_file())
        self.assertTrue((self.root / "keyframe" / "schema.md").is_file())
        self.assertTrue((self.root / "keyframe" / "examples.scene.xml").is_file())
        author = [c for c in FakeEngine.stream_calls if "Write" in c["tools"]][0]
        self.assertEqual(Path(author["cwd"]), self.root / "keyframe")
        self.assertIn("brief.md", author["prompt"])
        self.assertIn(keyframe.DOC_NAME, author["prompt"])

    def test_after_a_rejection_the_notes_outrank_everything_in_both_calls(self):
        self.run_stage()
        keyframe.reject(self.root, "the desk reads as plastic; the key must be warmer")
        FakeEngine.json_calls, FakeEngine.stream_calls = [], []
        self.run_stage()
        self.assertEqual(len(FakeEngine.json_calls), 1)              # the notes changed the question
        prompt = FakeEngine.json_calls[0]["prompt"]
        self.assertIn("the desk reads as plastic", prompt)
        self.assertLess(prompt.index("the desk reads as plastic"), prompt.index("hand_plane_no4"))
        self.assertIn("outrank", prompt.lower())
        brief = (self.root / "keyframe" / "brief.md").read_text(encoding="utf-8")
        self.assertIn("the desk reads as plastic", brief)
        self.assertIn("outrank", brief.lower())
        record = json.loads((self.root / "keyframe.json").read_text())
        self.assertEqual(record["approval"]["state"], "pending")
        self.assertEqual([n["notes"] for n in record["operator_notes"]],
                         ["the desk reads as plastic; the key must be warmer"])

    def test_the_same_inputs_are_not_asked_twice(self):
        self.run_stage()
        asked = len(FakeEngine.json_calls)
        authored = len([c for c in FakeEngine.stream_calls if "Write" in c["tools"]])
        self.run_stage()
        self.assertEqual(len(FakeEngine.json_calls), asked)
        self.assertEqual(len([c for c in FakeEngine.stream_calls if "Write" in c["tools"]]),
                         authored)
        self.run_stage(fresh=True)
        self.assertEqual(len(FakeEngine.json_calls), asked + 1)


# --------------------------------------------------------------------------- #
# the document
# --------------------------------------------------------------------------- #

class Document(ProjectCase):
    def test_the_fixture_passes_the_sdks_own_check_and_names_the_symbol(self):
        doc = sdk().read(KEYFRAME_DOC.encode("utf-8"))
        self.assertEqual([str(f) for f in doc.check()], [])
        self.assertEqual([str(el.id) for el in doc.iter("symbol")], [keyframe.SYMBOL_ID])

    def test_check_document_reports_the_sdks_findings_and_the_symbols(self):
        ws = vfx.Workspace(self.root, keyframe.KEYFRAME_DIR)
        (ws.dir / keyframe.DOC_NAME).write_text(KEYFRAME_DOC, encoding="utf-8")
        with patch.object(vfx.render, "engine_validate", lambda data, **k: []):
            symbols, findings = keyframe.check_document(ws)
        self.assertEqual(symbols, [keyframe.SYMBOL_ID])
        self.assertEqual([f for f in findings if f.startswith("error")], [])
        (ws.dir / keyframe.DOC_NAME).write_text(KEYFRAME_DOC.replace('align="start"', 'align="left"'),
                                                encoding="utf-8")
        with patch.object(vfx.render, "engine_validate", lambda data, **k: []):
            symbols, findings = keyframe.check_document(ws)
        self.assertTrue(any(f.startswith("error") and "align" in f for f in findings))

    def test_a_document_that_does_not_parse_is_an_error(self):
        ws = vfx.Workspace(self.root, keyframe.KEYFRAME_DIR)
        (ws.dir / keyframe.DOC_NAME).write_text("<scene", encoding="utf-8")
        symbols, findings = keyframe.check_document(ws)
        self.assertEqual(symbols, [])
        self.assertTrue(findings[0].startswith("error"))

    def test_the_frame_is_rendered_from_the_document_at_the_still_time_on_the_host(self):
        record = self.run_stage()
        self.assertEqual(len(self.render_calls), 1)
        call = self.render_calls[0]
        self.assertEqual(call["doc"], self.root / "keyframe" / keyframe.DOC_NAME)
        self.assertEqual(call["out"], self.root / "keyframe" / keyframe.FRAME_NAME)
        self.assertEqual(call["at"], keyframe.STILL_AT)
        self.assertEqual(call["host"], "root@fast")
        self.assertEqual(record["frame"], "keyframe/keyframe.png")
        self.assertEqual(record["document"], "keyframe/keyframe.scene.xml")
        self.assertTrue((self.root / record["frame"]).is_file())

    def test_the_engine_renderer_runs_a_still_job_through_render_run_job(self):
        seen = {}

        def run_job(cmd, project_root, host, *, job, verbose=False, **kw):
            seen.update(cmd=cmd, root=project_root, host=host, job=job)
            Path(cmd[cmd.index("-o") + 1]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[cmd.index("-o") + 1]).write_bytes(varied_png())
            return argparse.Namespace(returncode=0, stdout="", stderr="")

        out = self.root / "keyframe" / "keyframe.png"
        with patch.object(keyframe.render, "run_job", run_job):
            ok, message = keyframe.engine_renderer(self.root, "root@fast", verbose=False)(
                self.root / "keyframe" / "keyframe.scene.xml", out, 0.5)
        self.assertTrue(ok)
        self.assertEqual(seen["job"], "still")
        self.assertEqual(seen["host"], "root@fast")
        self.assertEqual(seen["root"], self.root)
        self.assertEqual(seen["cmd"][:2], ["scene-render", "render"])
        self.assertIn("-t", seen["cmd"])
        self.assertEqual(seen["cmd"][seen["cmd"].index("-t") + 1], "0.5")


# --------------------------------------------------------------------------- #
# the review loop
# --------------------------------------------------------------------------- #

class Loop(ProjectCase):
    def authors(self):
        return [c for c in FakeEngine.stream_calls if "Write" in c["tools"]]

    def reviews(self):
        return [c for c in FakeEngine.stream_calls if "Write" not in c["tools"]]

    def test_the_review_reads_the_frame_and_scores_the_four_categories(self):
        record = self.run_stage()
        review = self.reviews()[0]
        self.assertEqual(Path(review["cwd"]), self.root / "keyframe")
        self.assertIn(keyframe.FRAME_NAME, review["prompt"])
        self.assertIn("paper plane", review["prompt"])                # the elements it must find
        for category in ("visual_quality", "cinematography", "graphics_overlays",
                         "overall_production"):
            self.assertIn(category, review["prompt"])
        self.assertEqual(keyframe.CATEGORIES, ("visual_quality", "cinematography",
                                                "graphics_overlays", "overall_production"))
        self.assertEqual(record["score"], 8)
        self.assertEqual(record["rounds"][0]["scores"]["cinematography"], 8)
        self.assertTrue((self.root / "decisions" / "keyframe").is_dir())

    def test_below_the_bar_the_findings_go_back_verbatim_and_another_round_runs(self):
        FakeEngine.reviews = [
            {"seen": "a grey box", "scores": {c: 5 for c in keyframe.CATEGORIES},
             "findings": [{"category": "visual_quality",
                           "problem": "the desk is a flat brown box with no grain",
                           "fix": "give m-oak a baseColorMap from the hand plane's diffuse map",
                           "severity": "major"}],
             "score": 5, "summary": "a blockout"},
            {"seen": "a lit desk", "scores": {c: 8 for c in keyframe.CATEGORIES},
             "findings": [], "score": 8, "summary": "reads"}]
        record = self.run_stage()
        self.assertEqual(len(self.authors()), 2)
        self.assertEqual(len(self.reviews()), 2)
        self.assertEqual([r["score"] for r in record["rounds"]], [5, 8])
        self.assertEqual(record["score"], 8)
        second = self.authors()[1]["prompt"]
        self.assertIn("the desk is a flat brown box with no grain", second)
        self.assertIn("give m-oak a baseColorMap", second)
        self.assertIn("5/10", second)
        self.assertEqual(len(self.render_calls), 2)                   # a new frame per round

    def test_at_the_bar_the_loop_stops(self):
        FakeEngine.reviews = [{"seen": "s", "scores": {c: 7 for c in keyframe.CATEGORIES},
                               "findings": [], "score": 7, "summary": "at the bar"}]
        record = self.run_stage(bar=7.0)
        self.assertEqual(len(self.authors()), 1)
        self.assertEqual([r["score"] for r in record["rounds"]], [7])

    def test_rounds_exhausted_stops_with_the_last_score(self):
        FakeEngine.reviews = [{"seen": "s", "scores": {c: n for c in keyframe.CATEGORIES},
                               "findings": [], "score": n, "summary": "low"}
                              for n in (4, 5, 6, 6.5)]
        record = self.run_stage(rounds=2)
        self.assertEqual(len(self.authors()), 3)                     # the first, then two revisions
        self.assertEqual([r["score"] for r in record["rounds"]], [4, 5, 6])
        self.assertEqual(record["score"], 6)
        self.assertEqual(record["rounds_allowed"], 2)
        self.assertFalse(record["at_bar"])

    def test_a_flat_frame_is_an_error_that_sends_the_author_back_before_any_review(self):
        pngs = [flat_png(), varied_png()]
        keyframe.RENDERER = fake_renderer_sequence(pngs)
        record = self.run_stage()
        self.assertEqual(len(self.authors()), 2)
        self.assertEqual(len(self.reviews()), 1)                      # nothing to review while black
        self.assertTrue(any("error" in f and "black" in f for f in record["rounds"][0]["findings"]))
        self.assertIsNone(record["rounds"][0]["score"])
        self.assertIn("black", self.authors()[1]["prompt"])

    def test_a_document_with_errors_never_reaches_the_renderer(self):
        FakeEngine.doc = KEYFRAME_DOC.replace('align="start"', 'align="left"')
        record = self.run_stage(rounds=0)
        self.assertEqual(self.render_calls, [])
        self.assertEqual(self.reviews(), [])
        self.assertTrue(record["rounds"][0]["errors"])
        self.assertEqual(record["frame"], "")


def fake_renderer_sequence(pngs: list[bytes]):
    queue = list(pngs)

    def make(project_root, host, *, verbose=False):
        def render(doc, out, at):
            Path(out).write_bytes(queue.pop(0) if queue else varied_png())
            return True, "ok"
        return render
    return make


# --------------------------------------------------------------------------- #
# approval
# --------------------------------------------------------------------------- #

class Approval(ProjectCase):
    def test_a_fresh_keyframe_awaits_approval_with_the_documents_digest(self):
        record = self.run_stage()
        approval = record["approval"]
        self.assertEqual(approval["state"], "pending")
        self.assertEqual(approval["digest"],
                         keyframe.document_digest(self.root / "keyframe" / keyframe.DOC_NAME))
        self.assertTrue(approval["at"])
        self.assertEqual(keyframe.approval_state(self.root)["state"], "pending")
        self.assertIsNone(keyframe.approved_keyframe(self.root))
        self.assertTrue((self.root / "keyframe.md").is_file())

    def test_approve_records_the_digest_and_the_vfx_hook_sees_it(self):
        self.run_stage()
        record = keyframe.approve(self.root)
        self.assertEqual(record["approval"]["state"], "approved")
        self.assertEqual(keyframe.approval_state(self.root)["state"], "approved")
        look = keyframe.approved_keyframe(self.root)
        self.assertIsNotNone(look)
        self.assertEqual(look["document"], "keyframe/keyframe.scene.xml")
        self.assertEqual(look["frame"], "keyframe/keyframe.png")
        self.assertEqual(look["elements"][0]["kind"], "hero")
        self.assertTrue((self.root / look["document"]).is_file())
        self.assertIn("approved", (self.root / "keyframe.md").read_text())

    def test_a_changed_document_invalidates_the_approval(self):
        self.run_stage()
        keyframe.approve(self.root)
        path = self.root / "keyframe" / keyframe.DOC_NAME
        path.write_text(path.read_text().replace("intensity=\"3\"", "intensity=\"4\""))
        self.assertEqual(keyframe.approval_state(self.root)["state"], "stale")
        self.assertIsNone(keyframe.approved_keyframe(self.root))

    def test_reject_keeps_the_notes_and_the_next_run_resets_to_pending(self):
        self.run_stage()
        record = keyframe.reject(self.root, "warmer key")
        self.assertEqual(record["approval"]["state"], "rejected")
        self.assertEqual(record["approval"]["notes"], "warmer key")
        self.assertEqual(keyframe.approval_state(self.root)["state"], "rejected")
        self.assertIsNone(keyframe.approved_keyframe(self.root))
        keyframe.reject(self.root, "and less bloom")
        self.assertEqual([n["notes"] for n in keyframe.load_record(self.root)["operator_notes"]],
                         ["warmer key", "and less bloom"])
        record = self.run_stage()
        self.assertEqual(record["approval"]["state"], "pending")
        self.assertEqual(len(record["operator_notes"]), 2)

    def test_nothing_to_approve_is_an_error(self):
        with self.assertRaises(keyframe.KeyframeError):
            keyframe.approve(self.root)
        self.assertEqual(keyframe.approval_state(self.root)["state"], "none")


# --------------------------------------------------------------------------- #
# the CLI, the pipeline and the doctor
# --------------------------------------------------------------------------- #

class Registration(unittest.TestCase):
    def test_the_command_is_registered_with_its_flags(self):
        parser = cli.build_parser()
        args = parser.parse_args(["keyframe", "--project", "work/x", "--rounds", "3",
                                  "--bar", "8", "--fresh"])
        self.assertEqual(args.stage, "keyframe")
        self.assertEqual((args.rounds, args.bar, args.fresh), (3, 8.0, True))
        self.assertFalse(args.approve)
        self.assertIsNone(args.reject)
        args = parser.parse_args(["keyframe", "--project", "work/x", "--reject", "too dark"])
        self.assertEqual(args.reject, "too dark")
        args = parser.parse_args(["keyframe", "--project", "work/x", "--approve"])
        self.assertTrue(args.approve)
        defaults = parser.parse_args(["keyframe", "--project", "work/x"])
        self.assertEqual((defaults.rounds, defaults.bar), (2, 7.0))
        self.assertEqual((defaults.section, defaults.sheet, defaults.jobs), (None, False, 8))
        self.assertIsNone(defaults.budget_usd)

    def test_the_stage_sits_between_retime_and_vfx_and_owns_keyframe_json(self):
        # The look is a section of the film, so it is timed to the measured
        # narration (after retime) and locked before the animation (vfx).
        stages = [s for s, _ in workspace.STAGES]
        self.assertEqual(stages.index("keyframe"), stages.index("retime") + 1)
        self.assertEqual(stages.index("vfx"), stages.index("keyframe") + 1)
        self.assertEqual(dict(workspace.STAGES)["keyframe"], "keyframe")
        self.assertEqual(workspace.ARTIFACTS["keyframe"], "keyframe.json")
        self.assertIn("keyframe", cli.PIPELINE)

    def test_run_hands_the_stage_the_shared_flags_and_fresh_on_redo(self):
        base = ["--project", "work/x", "--model", "sonnet", "--quiet"]
        extra = cli.run_extras(argparse.Namespace(quick=False, minutes=1.0), redo=set())
        self.assertEqual(cli.stage_argv("keyframe", base, extra), ["keyframe", *base])
        self.assertIn("--fresh", cli.run_extras(argparse.Namespace(quick=False, minutes=1.0),
                                                redo={"keyframe"})["keyframe"])
        parser = cli.build_parser()
        parsed = parser.parse_args(cli.stage_argv("keyframe", base, extra))
        self.assertEqual(parsed.stage, "keyframe")


class Doctor(ProjectCase):
    def findings(self, area=None):
        report = doctor.check_project(workspace.Project(self.root))
        return [f for f in report.findings if area is None or f.area == area]

    def test_vfx_without_an_approved_keyframe_is_a_warning(self):
        write_json(self.root / "vfx.json", {"totals": {}, "plan": {}})
        warns = [f for f in self.findings() if f.level == "warn" and "keyframe" in f.message]
        self.assertEqual(len(warns), 1)
        self.assertIn("yt keyframe", warns[0].fix)

    def test_a_pending_keyframe_is_a_note_naming_the_approval_command(self):
        self.run_stage()
        notes = [f for f in self.findings("keyframe") if f.level == "note"]
        self.assertEqual(len(notes), 1)
        self.assertIn("approval", notes[0].message)
        self.assertIn("--approve", notes[0].fix)
        self.assertEqual(self.findings("keyframe")[0].level, "note")

    def test_an_approved_keyframe_is_ok_and_a_rejected_one_warns(self):
        self.run_stage()
        keyframe.approve(self.root)
        self.assertEqual([f.level for f in self.findings("keyframe")], ["ok"])
        keyframe.reject(self.root, "colder")
        warns = [f for f in self.findings("keyframe") if f.level == "warn"]
        self.assertEqual(len(warns), 1)
        self.assertIn("colder", warns[0].message)

    def test_vfx_that_predates_the_approval_is_stale(self):
        write_json(self.root / "vfx.json", {"totals": {}, "plan": {}})
        old = time.time() - 3600
        os.utime(self.root / "vfx.json", (old, old))
        self.run_stage()
        keyframe.approve(self.root)
        warns = [f for f in self.findings("vfx") if f.level == "warn"]
        self.assertEqual(len(warns), 1)
        self.assertIn("predates", warns[0].message)
        self.assertIn("yt vfx", warns[0].fix)
        later = time.time() + 3600
        os.utime(self.root / "vfx.json", (later, later))
        self.assertEqual([f for f in self.findings("vfx") if f.level == "warn"], [])

    def test_a_document_changed_after_approval_warns(self):
        self.run_stage()
        keyframe.approve(self.root)
        path = self.root / "keyframe" / keyframe.DOC_NAME
        path.write_text(path.read_text() + "\n")
        warns = [f for f in self.findings("keyframe") if f.level == "warn"]
        self.assertEqual(len(warns), 1)
        self.assertIn("changed", warns[0].message)


class Sheet(ProjectCase):
    def test_the_sheet_lists_the_frame_the_elements_their_sources_and_the_scores(self):
        self.run_stage()
        sheet = (self.root / "keyframe.md").read_text(encoding="utf-8")
        self.assertIn("keyframe/keyframe.png", sheet)
        self.assertIn("hand_plane_no4", sheet)
        self.assertIn("CC0 1.0 (Poly Haven)", sheet)
        self.assertIn("authored", sheet)
        self.assertIn("8", sheet)
        self.assertIn("--approve", sheet)
        self.assertTrue(re.search(r"\|\s*hero\s*\|", sheet))


if __name__ == "__main__":
    unittest.main()
