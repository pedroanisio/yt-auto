"""vfx.py: one world per section, proof frames at the shots, eyes in the loop,
and authored documents that are never overwritten.

A section is authored as ONE symbol -- the shared world (set, materials,
light rig, hero model) and one camera keyed across the section -- with each
shot a phase at its offset, so `yt build` includes one continuous take; a cut
is the exception the plan has to ask for. The proof frames sit at each shot's
midpoint, the frame gate reads the engine's off-camera diagnostics, a vision
review scores the frames under a schema and sends the author back when the
score is under the bar, and every round of authoring lands in a new vfx/vNN/.
The model is never called: `harness.ENGINE` is a fake, and the render host is
a stub that returns a varied PNG.
"""

import argparse
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from claude_engine import Event, Usage
from ytstudio import harness, vfx
from ytstudio.core import Idea
from ytstudio.research import Dossier
from ytstudio.scene_sdk import sdk
from tests import programs

WORLD_DOC = '''<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.3">
  <project width="1920" height="1080" fps="30" duration="15" background="#101418"/>
  <output path="preview.mp4" codec="h264"/>
  <materials>
    <material id="m-a" baseColor="#AAAAAA" roughness="0.8" doubleSided="true"/>
  </materials>
  <symbols>
    <symbol id="world-open" width="1920" height="1080" duration="15">
      <camera id="cam" x="0" y="-100" z="-400" target="hero-ball" focalLength="50" active="true"/>
      <object3D id="floor" primitive="plane" material="m-a" x="0" y="0" z="0" width="900" height="900" rotationX="90"/>
      <object3D id="wall" primitive="plane" material="m-a" x="0" y="-200" z="400" width="900" height="400"/>
      <object3D id="desk" primitive="box" material="m-a" x="0" y="-40" z="100" width="100" height="80" depth="100"/>
      <object3D id="hero-ball" primitive="sphere" material="m-a" x="0" y="-150" z="100" radius="20">
        <animate property="x"><key time="0" value="-200"/><key time="15" value="200"/></animate>
      </object3D>
    </symbol>
  </symbols>
  <composition>
    <instance id="u" symbol="world-open" start="0" end="15"/>
  </composition>
  <lights>
    <light id="sun" type="directional" color="#FFFFFF" intensity="3" yaw="200" pitch="-30"/>
  </lights>
</scene>
'''

OFF_CAMERA = ("warning: object3D shot/{id} is drawn from 0.000 s to 2.967 s and is "
              "never inside the camera's view: its bounds lie wholly behind, beside "
              "or beyond the camera's frustum in every frame rendered")

SECTION = {"heading": "Open", "seconds": 15, "beats": ["b"], "shots": [
    {"label": "1.1", "at": 0, "seconds": 3, "visual": "release", "on_screen": "",
     "narration": "", "asset": ""},
    {"label": "1.2", "at": 3, "seconds": 4, "visual": "glide", "on_screen": "",
     "narration": "", "asset": ""},
    {"label": "1.3", "at": 7, "seconds": 4, "visual": "bank", "on_screen": "",
     "narration": "", "asset": ""},
    {"label": "1.4", "at": 11, "seconds": 4, "visual": "settle", "on_screen": "",
     "narration": "", "asset": ""}]}

EFFECTS = [
    {"id": "open-release", "name": "Release", "shots": ["1.1"], "seconds": 3,
     "conveys": "c1", "on_screen": "o1", "brief": "b1", "type": "3d-render",
     "difficulty": 3, "hours": 2, "technique": [], "fallback": "f", "section": "Open"},
    {"id": "open-glide", "name": "Glide", "shots": ["1.2", "1.3"], "seconds": 8,
     "conveys": "c2", "on_screen": "o2", "brief": "b2", "type": "3d-render",
     "difficulty": 3, "hours": 2, "technique": [], "fallback": "f", "section": "Open"},
    {"id": "open-settle", "name": "Settle", "shots": ["1.4"], "seconds": 4,
     "conveys": "c3", "on_screen": "o3", "brief": "b3", "type": "3d-render",
     "difficulty": 3, "hours": 2, "technique": [], "fallback": "f", "section": "Open"}]

STYLE = {"look": "warm", "palette": [{"name": "Base", "hex": "#101418", "use": "background"},
                                     {"name": "Ink", "hex": "#F2F5F7", "use": "text"}],
         "typography": "t", "camera_language": "c", "transitions": "t",
         "motion_rules": ["slow"], "avoid": []}


def varied_png() -> bytes:
    im = Image.new("RGB", (64, 36))
    for x in range(64):
        for y in range(36):
            im.putpixel((x, y), ((x * 4) % 256, (y * 7) % 256, 120))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def author_program(cwd: Path, name: str, doc: str) -> None:
    """What an author writes now: the program that builds and saves the document."""
    programs.author_program(cwd, name[:-len(".scene.xml")] + ".py", name, doc)


class FakeEngine:
    """Both agents of the stage: the author (write tools) writes the program
    that saves the fixture document; the reviewer (read-only tools) answers
    from a script."""

    stream_calls: list = []
    json_calls: list = []
    reviews: list = []
    doc: str = WORLD_DOC
    time_out_on: int = 0        # the n-th author call raises the engine's timeout
    stale_frame: bool = False   # the author leaves a frame of its own in frames/
    docs: list = []             # per author call, the document it writes (else `doc`)
    crash_on: int = 0           # the n-th author call breaks its program and the CLI exits 1

    def __init__(self, config):
        self.config = config
        self.usage = Usage()
        self.usage.cost_usd = 0.02
        self.usage.calls = 1

    def ask_json(self, prompt, schema, system=None):
        FakeEngine.json_calls.append({"prompt": prompt, "schema": schema})
        return {}

    def stream(self, prompt, system=None, resume=False):
        tools = tuple(self.config.tools or ())
        FakeEngine.stream_calls.append({"prompt": prompt, "system": system,
                                        "model": self.config.model,
                                        "tools": tools, "cwd": self.config.cwd,
                                        "timeout": self.config.timeout, "resume": resume})
        self.session_id = "author-session"
        if "Write" in tools:
            name = re.search(r"(\d{2}-[\w-]+\.scene\.xml)", prompt).group(1)
            authored = sum(1 for c in FakeEngine.stream_calls if "Write" in c["tools"])
            if FakeEngine.time_out_on == authored:
                (Path(self.config.cwd) / name).write_text("<scene>half-edited</scene>",
                                                         encoding="utf-8")
                raise harness.ClaudeError("claude timed out after 3600.0s")
            if FakeEngine.crash_on == authored:
                (Path(self.config.cwd) / (name[:-10] + ".py")).write_text("this is not python(")
                raise harness.ClaudeError("claude exited with code 1")
            author_program(Path(self.config.cwd), name,
                           FakeEngine.docs.pop(0) if FakeEngine.docs else FakeEngine.doc)
            if FakeEngine.stale_frame:
                stale = Path(self.config.cwd) / "frames" / f"{name[:-10]}__9s.png"
                stale.parent.mkdir(exist_ok=True)
                stale.write_bytes(b"an earlier picture")
            yield Event(type="assistant", text=f"{name}: world-open")
        else:
            yield Event(type="assistant", subtype="tool_use", tool_name="Read",
                        tool_input={"file_path": "frames/x.png"})
            review = FakeEngine.reviews.pop(0) if FakeEngine.reviews else {
                "frames": [], "score": 9, "summary": "fine"}
            if vfx.PREVIOUS_ROUND in prompt and "previous" not in review:
                review = self.verdict_for(review, prompt)
            yield Event(type="assistant",
                        text="I looked.\n```json\n" + json.dumps(review) + "\n```")
        yield Event(type="result", raw={})

    @staticmethod
    def verdict_for(review: dict, prompt: str) -> dict:
        """A script from before the reviewer was asked for a verdict (AUTO-78)
        judges by score, as the stage did then: higher is better, equal is the
        same. The previous round's score is read from the brief it was handed."""
        before = re.search(r'"score": ([\d.]+)', prompt.split(vfx.PREVIOUS_ROUND, 1)[1])
        score, was = float(review.get("score") or 0), float(before.group(1)) if before else 0.0
        verdict = "better" if score > was else "same" if score == was else "worse"
        return dict(review, previous={"verdict": verdict, "findings": []})


class WorldSymbols(unittest.TestCase):
    """One world per section by default; shots keep their offsets inside it."""

    def test_one_world_holds_every_shot_at_its_offset(self):
        worlds = vfx.world_symbols(SECTION, EFFECTS)
        self.assertEqual(len(worlds), 1)
        w = worlds[0]
        self.assertEqual(w["id"], "world-open")
        self.assertEqual(w["seconds"], 15)
        self.assertEqual(w["start"], 0)
        self.assertEqual([(s["label"], s["at"], s["seconds"]) for s in w["shots"]],
                         [("1.1", 0, 3), ("1.2", 3, 4), ("1.3", 7, 4), ("1.4", 11, 4)])
        self.assertEqual([e["id"] for e in w["phases"]],
                         ["open-release", "open-glide", "open-settle"])

    def test_offsets_are_relative_to_the_section_not_the_film(self):
        later = dict(SECTION, heading="Later", shots=[
            dict(s, at=s["at"] + 30) for s in SECTION["shots"]])
        w = vfx.world_symbols(later, EFFECTS)[0]
        self.assertEqual(w["id"], "world-later")
        self.assertEqual([s["at"] for s in w["shots"]], [0, 3, 7, 11])
        self.assertEqual(w["start"], 30)         # film time of its first shot
        self.assertEqual(w["offset"], 0)         # seconds into the section

    def test_a_planned_cut_splits_the_section_into_two_worlds(self):
        worlds = vfx.world_symbols(SECTION, EFFECTS, cuts=["1.3"])
        self.assertEqual([w["id"] for w in worlds], ["world-open", "world-open-2"])
        self.assertEqual([s["label"] for s in worlds[0]["shots"]], ["1.1", "1.2"])
        self.assertEqual([(s["label"], s["at"]) for s in worlds[1]["shots"]],
                         [("1.3", 0), ("1.4", 4)])
        self.assertEqual(worlds[1]["start"], 7)
        self.assertEqual(worlds[1]["offset"], 7)
        self.assertEqual(worlds[1]["seconds"], 8)
        # the glide effect spans the cut, so it is a phase of both worlds
        self.assertIn("open-glide", [e["id"] for e in worlds[0]["phases"]])
        self.assertIn("open-glide", [e["id"] for e in worlds[1]["phases"]])
        # a cut on the first shot, or on an unknown label, is no cut
        self.assertEqual(len(vfx.world_symbols(SECTION, EFFECTS, cuts=["1.1", "9.9"])), 1)

    def test_a_section_without_a_screenplay_is_still_one_world(self):
        w = vfx.world_symbols({"heading": "Open", "seconds": 20}, EFFECTS)
        self.assertEqual(len(w), 1)
        self.assertEqual(w[0]["seconds"], 20)
        self.assertEqual(w[0]["shots"], [])
        self.assertEqual(len(w[0]["phases"]), 3)

    def test_proof_times_sit_at_each_shots_midpoint(self):
        times = vfx.shot_times(SECTION["shots"], 15)
        for mid in (1.5, 5.0, 9.0, 13.0):
            self.assertIn(mid, times)
        self.assertTrue(all(0 < t < 15 for t in times))
        self.assertEqual(times, sorted(times))
        # the symbol's own start/mid/end are kept unless a midpoint already sits there
        self.assertEqual(vfx.shot_times([], 10), vfx.frame_times(10))

    def test_effects_schema_lets_the_plan_ask_for_a_cut(self):
        self.assertIn("cuts", vfx.EFFECTS_SCHEMA["properties"])
        self.assertNotIn("cuts", vfx.EFFECTS_SCHEMA["required"])


class Versions(unittest.TestCase):
    """A new round of authoring never overwrites the previous one."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_new_versions_count_up_and_keep_the_old_ones(self):
        ws = vfx.Workspace(self.root)
        self.assertEqual(ws.dir, self.root / "vfx")          # the pinned default
        self.assertIsNone(ws.version)
        self.assertEqual(ws.new_version(), "v01")
        (ws.dir / "01-open.scene.xml").write_text("one")
        self.assertEqual(ws.rel, "vfx/v01")
        self.assertEqual(vfx.Workspace(self.root).new_version(), "v02")
        self.assertEqual((self.root / "vfx" / "v01" / "01-open.scene.xml").read_text(), "one")
        self.assertEqual(vfx.Workspace.versions(self.root / "vfx"), ["v01", "v02"])

    def test_a_workspace_is_found_from_a_document_path(self):
        ws = vfx.Workspace(self.root, version="v03")
        doc = ws.dir / "01-open.scene.xml"
        doc.write_text("x")
        found = vfx.Workspace.of(doc)
        self.assertEqual(found.dir, ws.dir)
        self.assertEqual(found.root, self.root)
        self.assertEqual(found.version, "v03")
        flat = self.root / "vfx" / "02-flat.scene.xml"
        flat.write_text("x")
        self.assertEqual(vfx.Workspace.of(flat).dir, self.root / "vfx")
        self.assertIsNone(vfx.Workspace.of(flat).version)

    def test_sr_wrapper_resolves_the_workspace_from_the_document(self):
        ws = vfx.Workspace(self.root, version="v01")
        ws.prepare(tokens={"ink": "#FFFFFF"}, frame=(1920, 1080))
        self.assertIn("Workspace.of(", (ws.dir / "sr.py").read_text())


class Brief(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.idea = Idea(title="T", genre="g", description="d")

    def tearDown(self):
        self._tmp.cleanup()

    def brief(self, ws):
        worlds = vfx.world_symbols(SECTION, EFFECTS)
        path = ws.brief(1, idea=self.idea, house_rules="", style=STYLE,
                        tokens={"ink": "#FFFFFF"}, frame=(1920, 1080), section=SECTION,
                        effects=EFFECTS, words={}, assets=[], worlds=worlds)
        return path.read_text(encoding="utf-8")

    def test_brief_asks_for_one_world_with_the_shots_as_phases(self):
        ws = vfx.Workspace(self.root, version="v01")
        text = self.brief(ws)
        self.assertIn('<symbol id="world-open" width="1920" height="1080" duration="15">', text)
        self.assertIn("#### 1.2 · at 3s · 4s", text)
        self.assertIn("hero-", text)                       # the hero convention
        self.assertIn("Release", text)                     # the phases
        self.assertIn("Settle", text)
        self.assertIn("midpoint", text)                    # where the proof frames sit
        # one world, one continuous take (AUTO-143 keeps it inside a setting)
        self.assertIn("Each symbol is one world", text)
        self.assertIn("the room does not change", text)
        self.assertIn("one continuous path", text)
        self.assertNotIn("one <symbol> per effect", text)
        self.assertNotIn("## Props", text)                 # absent file, no section, no error

    def test_brief_lists_props_with_paths_relative_to_the_document(self):
        (self.root / "props" / "model" / "desk" / "textures").mkdir(parents=True)
        (self.root / "props" / "model" / "desk" / "desk_2k.gltf").write_bytes(b"gltf")
        for name in ("desk_diff_2k.jpg", "desk_nor_gl_2k.jpg", "desk_arm_2k.jpg"):
            (self.root / "props" / "model" / "desk" / "textures" / name).write_bytes(b"jpg")
        (self.root / "props.json").write_text(json.dumps({"items": [
            {"id": "desk", "kind": "model", "file": "props/model/desk/desk_2k.gltf",
             "licence": "CC0", "credit": "someone", "about": "a worn office desk"},
            {"id": "sky", "kind": "hdri", "file": "props/sky.hdr",
             "licence": "CC0", "credit": "", "about": "overcast"}]}))
        ws = vfx.Workspace(self.root, version="v01")
        text = self.brief(ws)
        self.assertIn("## Props", text)
        self.assertIn("../../props/model/desk/desk_2k.gltf", text)
        self.assertIn("a worn office desk", text)
        self.assertIn("../../props/sky.hdr", text)
        # the schema's own names, not a guess: a dome light with an environment,
        # a mesh asset placed by an object3D, material maps on large surfaces
        self.assertIn('type="dome"', text)
        self.assertIn("environment=", text)
        self.assertIn("<mesh", text)
        self.assertIn('primitive="mesh"', text)
        self.assertIn("baseColorMap", text)
        self.assertIn("normalMap", text)
        self.assertIn("../../props/model/desk/textures/desk_diff_2k.jpg", text)
        self.assertIn("default light rig", text)
        self.assertEqual(len(vfx.props_items(self.root)), 2)
        self.assertEqual(vfx.props_items(self.root / "nowhere"), [])
        self.assertEqual([Path(t).name for t in vfx.prop_textures(self.root, vfx.props_items(self.root)[0])],
                         ["desk_arm_2k.jpg", "desk_diff_2k.jpg", "desk_nor_gl_2k.jpg"])
        self.assertEqual(vfx.prop_textures(self.root, vfx.props_items(self.root)[1]), [])

    def test_brief_makes_an_unframed_object_the_authors_problem(self):
        ws = vfx.Workspace(self.root, version="v01")
        text = self.brief(ws)
        self.assertIn("never inside the camera's view", text)
        self.assertIn("visible", text)

    def test_brief_explains_how_to_decline_a_prop(self):
        (self.root / "props.json").write_text(json.dumps({"items": [
            {"id": "sky", "kind": "hdri", "file": "props/sky.hdr", "licence": "CC0",
             "credit": "", "about": "overcast"}]}))
        text = self.brief(vfx.Workspace(self.root, version="v01"))
        self.assertIn('<meta name="props-declined"', text)
        self.assertIn("props-declined", text)

    def test_brief_carries_the_approved_look(self):
        text = self.brief(vfx.Workspace(self.root, version="v01"))
        self.assertNotIn("approved look", text)
        approve_keyframe(self.root)
        text = self.brief(vfx.Workspace(self.root, version="v01"))
        self.assertIn("## The approved look", text)
        self.assertIn("../../keyframe/keyframe.scene.xml", text)
        self.assertIn("../../keyframe/keyframe.png", text)
        self.assertIn("a world that departs from it is wrong", text)
        self.assertIn("oak desk", text)                      # the elements, by name
        self.assertIn("inherit these materials, lights, hero model and camera language", text)
        # AUTO-143: the keyframe's environment is the lead section's first
        # world; a world cut to another setting may stand in another set
        self.assertIn("Its environment is the lead section's first world", text)
        self.assertIn("may stand in another set", text)

    def test_a_pending_or_stale_keyframe_is_not_the_approved_look(self):
        approve_keyframe(self.root, state="pending")
        self.assertIsNone(vfx.approved_look(self.root))
        approve_keyframe(self.root)
        self.assertEqual(vfx.approved_look(self.root)["frame"], "keyframe/keyframe.png")
        (self.root / "keyframe" / "keyframe.scene.xml").write_text("changed")
        self.assertIsNone(vfx.approved_look(self.root))      # digest no longer matches
        self.assertIsNone(vfx.approved_look(self.root / "nowhere"))


def approve_keyframe(root: Path, state: str = "approved") -> None:
    """A keyframe.json in the downstream contract's shape, tied to its document."""
    from ytstudio import keyframe
    (root / "keyframe").mkdir(exist_ok=True)
    doc = root / "keyframe" / "keyframe.scene.xml"
    doc.write_text("<scene/>")
    (root / "keyframe" / "keyframe.png").write_bytes(b"png")
    (root / "keyframe.json").write_text(json.dumps({
        "elements": [{"kind": "set", "name": "oak desk", "source": "authored", "file": "",
                      "licence": "", "credit": "", "why": "the landing surface"}],
        "document": "keyframe/keyframe.scene.xml", "frame": "keyframe/keyframe.png",
        "approval": {"state": state, "notes": "", "digest": keyframe.document_digest(doc),
                     "at": "2026-10-19T10:00:00"}}))


LOOK_PROGRAM = '''from srkit import S, FRAME, save
INK = "#F2F5F7"
scene = S.Scene(version="auto")
save(scene, "01-open.scene.xml")
'''


def approve_section_look(root: Path, state: str = "approved") -> None:
    """A keyframe.json for a look taken from the film (`yt keyframe` on section
    1), with the approved program kept at keyframe/look.py."""
    from ytstudio import keyframe
    (root / "keyframe").mkdir(exist_ok=True)
    (root / "vfx" / "v01").mkdir(parents=True, exist_ok=True)
    doc = root / "vfx" / "v01" / "01-open.scene.xml"
    doc.write_text("<scene/>")
    (root / "vfx" / "v01" / "01-open.py").write_text(LOOK_PROGRAM)
    (root / "keyframe" / "look.py").write_text(LOOK_PROGRAM)
    (root / "keyframe" / "keyframe.png").write_bytes(b"png")
    (root / "keyframe.json").write_text(json.dumps({
        "source": "section", "section": 1, "document": "vfx/v01/01-open.scene.xml",
        "program": "vfx/v01/01-open.py", "frame": "keyframe/keyframe.png",
        "look_program": "keyframe/look.py", "frame_size": [1920, 1080], "elements": [],
        "record": {"n": 1, "file": "vfx/v01/01-open.scene.xml", "program": "vfx/v01/01-open.py",
                   "version": "v01", "findings": []},
        "approval": {"state": state, "notes": "", "digest": keyframe.document_digest(doc),
                     "at": "2026-10-20T10:00:00"}}))


class LookModule(Brief):
    """AUTO-77: the approved look is an importable module in every section
    workspace, the brief says to import it, and `--relook` copies it again."""

    def test_prepare_copies_the_approved_look_into_the_version(self):
        approve_section_look(self.root)
        ws = vfx.Workspace(self.root, version="v02")
        ws.prepare(tokens={"ink": "#FFFFFF"}, frame=(1920, 1080))
        self.assertEqual((ws.dir / "look.py").read_bytes(),
                         (self.root / "keyframe" / "look.py").read_bytes())
        self.assertTrue((ws.dir / "srkit.py").is_file())      # beside the kit

    def test_no_approved_section_look_means_no_module(self):
        ws = vfx.Workspace(self.root, version="v01")
        ws.prepare(tokens={"ink": "#FFFFFF"}, frame=(1920, 1080))
        self.assertFalse((ws.dir / "look.py").exists())
        approve_section_look(self.root, state="pending")
        ws = vfx.Workspace(self.root, version="v02")
        ws.prepare(tokens={"ink": "#FFFFFF"}, frame=(1920, 1080))
        self.assertFalse((ws.dir / "look.py").exists())
        approve_keyframe(self.root)                            # a sheet: no program to import
        ws = vfx.Workspace(self.root, version="v03")
        ws.prepare(tokens={"ink": "#FFFFFF"}, frame=(1920, 1080))
        self.assertFalse((ws.dir / "look.py").exists())

    def test_the_keyframe_and_assess_workspaces_inherit_no_look(self):
        approve_section_look(self.root)
        for dirname in ("keyframe", "assess"):
            ws = vfx.Workspace(self.root, dirname)
            ws.prepare(tokens={"ink": "#FFFFFF"}, frame=(1920, 1080))
            self.assertTrue((ws.dir / "srkit.py").is_file())
        self.assertFalse((self.root / "assess" / "look.py").exists())
        # keyframe/look.py is the approved bytes, untouched
        self.assertEqual((self.root / "keyframe" / "look.py").read_text(), LOOK_PROGRAM)

    def test_the_brief_says_import_the_look_and_redefine_nothing(self):
        approve_section_look(self.root)
        text = self.brief(vfx.Workspace(self.root, version="v02"))
        self.assertIn("## The approved look (section 1 of this film", text)
        self.assertIn("`look.py` in your workspace is that program, byte for byte", text)
        self.assertIn("from look import", text)
        self.assertIn("redefine nothing it provides", text)
        self.assertIn("../../keyframe/look.py", text)         # where the bytes come from
        self.assertNotIn("Do not import", text)
        self.assertNotIn("copy into your program", text)
        # `--relook` and the first animation after the approval carry the module
        # too: tests.test_look_section.Provenance, which owns that fixture


class PropsRule(unittest.TestCase):
    """A listed prop is used, or declined with a reason the operator sees."""

    PROPS = [{"id": "sky", "kind": "hdri", "file": "props/sky.hdr"},
             {"id": "desk", "kind": "model", "file": "props/model/desk/desk_2k.gltf"},
             {"id": "box", "kind": "model", "file": "props/model/box/box_2k.gltf"}]
    TEXTURES = ["props/model/desk/textures/desk_diff_2k.jpg"]

    def test_unused_hdri_and_models_are_errors_naming_them(self):
        found, declined = vfx.props_findings(WORLD_DOC, self.PROPS, self.TEXTURES)
        errors = [f for f in found if f.startswith("error")]
        self.assertEqual(len(errors), 2)
        self.assertIn("sky", errors[0])
        self.assertIn("environment=", errors[0])
        self.assertIn("desk", errors[1])
        self.assertIn("box", errors[1])
        self.assertIn("<mesh", errors[1])
        self.assertIn("props-declined", errors[0])
        self.assertEqual(declined, {})

    def test_used_props_pass(self):
        doc = WORLD_DOC.replace("<materials>", '<assets><mesh id="m-desk" src="../../props/model/desk/desk_2k.gltf"/></assets>\n  <materials>') \
            .replace("<lights>", '<lights>\n    <light id="env" type="dome" environment="../../props/sky.hdr"/>')
        found, _ = vfx.props_findings(doc, self.PROPS, self.TEXTURES)
        self.assertFalse([f for f in found if f.startswith("error")])

    def test_declining_a_kind_or_every_id_with_a_reason_is_allowed(self):
        doc = WORLD_DOC.replace("<output", '<metadata><meta name="props-declined" value="hdri: no sunlit room among them"/>'
                                '<meta name="props-declined" value="desk: a metal desk against an oak treatment"/>'
                                '<meta name="props-declined" value="box: no box in the scene"/></metadata><output')
        found, declined = vfx.props_findings(doc, self.PROPS, self.TEXTURES)
        self.assertFalse([f for f in found if f.startswith("error")])
        self.assertEqual(declined, {"hdri": "no sunlit room among them",
                                    "desk": "a metal desk against an oak treatment",
                                    "box": "no box in the scene"})

    def test_declining_one_model_leaves_the_other_required(self):
        doc = WORLD_DOC.replace("<output", '<metadata><meta name="props-declined" value="hdri: none fits"/>'
                                '<meta name="props-declined" value="desk: wrong desk"/></metadata><output')
        found, _ = vfx.props_findings(doc, self.PROPS, self.TEXTURES)
        errors = [f for f in found if f.startswith("error")]
        self.assertEqual(len(errors), 1)
        self.assertIn("box", errors[0])
        self.assertNotIn("desk", errors[0].split("props-declined")[0].replace("desk_2k", ""))

    def test_a_decline_without_a_reason_does_not_count(self):
        doc = WORLD_DOC.replace("<output", '<metadata><meta name="props-declined" value="hdri"/></metadata><output')
        found, declined = vfx.props_findings(doc, self.PROPS, self.TEXTURES)
        self.assertTrue([f for f in found if f.startswith("error") and "sky" in f])
        self.assertEqual(declined, {})

    def test_a_generator_map_on_a_large_surface_is_a_note(self):
        doc = WORLD_DOC.replace('<material id="m-a"', '<material id="m-a" baseColorMap="#g-grain"')
        found, _ = vfx.props_findings(doc, self.PROPS, self.TEXTURES)
        notes = [f for f in found if f.startswith("note")]
        self.assertEqual(len(notes), 1)
        self.assertIn("desk_diff_2k.jpg", notes[0])
        doc = WORLD_DOC.replace('<material id="m-a"', '<material id="m-a" baseColorMap="../../props/model/desk/textures/desk_diff_2k.jpg"')
        self.assertFalse([f for f in vfx.props_findings(doc, self.PROPS, self.TEXTURES)[0] if f.startswith("note")])
        self.assertFalse([f for f in vfx.props_findings(WORLD_DOC, self.PROPS, [])[0] if f.startswith("note")])

    def test_no_props_means_no_rule(self):
        self.assertEqual(vfx.props_findings(WORLD_DOC, [], []), ([], {}))

    def test_validate_applies_the_rule_from_the_projects_props(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "props").mkdir()
            (root / "props" / "sky.hdr").write_bytes(b"hdr")
            (root / "props.json").write_text(json.dumps({"items": [self.PROPS[0]]}))
            ws = vfx.Workspace(root, version="v01")
            name = ws.doc_name(1, SECTION)
            (ws.dir / name).write_text(WORLD_DOC, encoding="utf-8")
            with patch.object(vfx.render, "engine_validate", lambda data, **kw: None):
                symbols, findings = ws.validate(name, frames=False, props=True)
                _, without = ws.validate(name, frames=False)
            self.assertEqual(symbols, ["world-open"])
            self.assertTrue([f for f in findings if f.startswith("error") and "sky" in f])
            # other stages (the keyframe) reuse validate without the rule
            self.assertFalse([f for f in without if f.startswith("error")])


class EncodeWindow(unittest.TestCase):
    """The shot windows run through render's public job hook."""

    def test_encode_findings_uses_run_job_and_reads_the_envelope(self):
        calls = []
        envelope = {"result": {"outputs": [{"findings": [
            {"code": "X-rs-scene-render-3D-OFF-CAMERA", "severity": "warning",
             "message": OFF_CAMERA.format(id="desk")[len("warning: "):]}]}]}}

        def fake_run_job(cmd, root, host, *, job, verbose=False, **kw):
            calls.append({"cmd": cmd, "root": Path(root), "host": host, "job": job,
                          "files": sorted(p.relative_to(root).as_posix()
                                          for p in Path(root).rglob("*") if p.is_file())})
            return SimpleNamespace(returncode=0, stdout=json.dumps(envelope), stderr="")

        with patch.object(vfx.render, "run_job", fake_run_job):
            lines = vfx.encode_findings({"vfx/v01/a.scene.xml": b"x", "vfx/v01/.proof-a.scene.xml": b"y"},
                                        "vfx/v01/.proof-a.scene.xml", start=3, end=7, host="root@h")
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call["job"], "proof")
        self.assertEqual(call["host"], "root@h")
        self.assertEqual(call["cmd"][:3], ["scene-render", "encode", "vfx/v01/.proof-a.scene.xml"])
        for flag, value in (("--start", "3"), ("--end", "7"), ("--quality", "draft")):
            self.assertEqual(call["cmd"][call["cmd"].index(flag) + 1], value)
        self.assertEqual(call["files"], ["vfx/v01/.proof-a.scene.xml", "vfx/v01/a.scene.xml"])
        self.assertEqual(vfx.framecheck.never_in_view(lines), ["desk"])
        self.assertNotIn("_run_remote", Path(vfx.__file__).read_text(encoding="utf-8"))


class Bundle(unittest.TestCase):
    """What travels with a document to the render host: a model with its
    buffers and textures, and every material map, keyed from the project root."""

    def test_a_gltf_ships_with_its_bin_and_textures_and_maps_are_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "props" / "model" / "desk"
            (model / "textures").mkdir(parents=True)
            (model / "desk_2k.gltf").write_bytes(b"gltf")
            (model / "desk.bin").write_bytes(b"bin")
            (model / "textures" / "desk_diff_2k.jpg").write_bytes(b"jpg")
            (root / "props" / "hdri").mkdir()
            (root / "props" / "hdri" / "sky.hdr").write_bytes(b"hdr")
            doc_dir = root / "vfx" / "v02"
            doc_dir.mkdir(parents=True)
            doc = ('<scene><assets><mesh id="m" src="../../props/model/desk/desk_2k.gltf"/></assets>'
                   '<materials><material id="w" baseColorMap="../../props/model/desk/textures/desk_diff_2k.jpg"'
                   ' normalMap="https://x/y.png"/></materials>'
                   '<lights><light type="dome" environment="../../props/hdri/sky.hdr"/></lights></scene>')
            files = vfx.referenced_files(doc, doc_dir, root)
            self.assertEqual(sorted(files), [
                "props/hdri/sky.hdr", "props/model/desk/desk.bin",
                "props/model/desk/desk_2k.gltf", "props/model/desk/textures/desk_diff_2k.jpg"])


class Frames(unittest.TestCase):
    """Proof frames at the shots' midpoints, judged with the engine's words."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ws = vfx.Workspace(self.root, version="v01")
        self.name = self.ws.doc_name(1, SECTION)
        (self.ws.dir / self.name).write_text(WORLD_DOC, encoding="utf-8")
        self.shots = {"world-open": SECTION["shots"]}

    def tearDown(self):
        self._tmp.cleanup()

    def still(self):
        calls = []

        def fake(xml, at, **kw):
            calls.append({"at": at, "doc_path": kw.get("doc_path"), "files": kw.get("files")})
            return varied_png(), "rendered locally"
        return calls, fake

    def encode(self, off_camera):
        """A fake of the shot-window encode: off_camera(start, end) -> ids."""
        windows = []

        def fake(files, doc_path, *, start, end, **kw):
            windows.append({"start": start, "end": end, "doc_path": doc_path,
                            "files": sorted(files)})
            return [OFF_CAMERA.format(id=i) for i in off_camera(start, end)]
        return windows, fake

    def go(self, still, encode, **kw):
        with patch.object(vfx.render, "engine_render_still", still), \
                patch.object(vfx, "encode_findings", encode):
            return self.ws.frames(self.name, **kw)

    def test_frames_are_rendered_at_each_shots_midpoint(self):
        calls, still = self.still()
        windows, encode = self.encode(lambda s, e: [])
        results = self.go(still,encode, shots=self.shots)
        ats = sorted(c["at"] for c in calls)
        for mid in (1.5, 5.0, 9.0, 13.0):
            self.assertIn(mid, ats)
        self.assertTrue(all(png is not None for png, _ in results))
        self.assertTrue(calls[0]["doc_path"].startswith("vfx/v01/"))
        self.assertIn(f"vfx/v01/{self.name}", calls[0]["files"])
        self.assertFalse([f for r in results for f in r[1] if f.startswith("error")])
        # one encode window per shot, over the shot's whole span
        self.assertEqual(sorted((w["start"], w["end"]) for w in windows),
                         [(0.0, 3.0), (3.0, 7.0), (7.0, 11.0), (11.0, 15.0)])
        self.assertTrue(windows[0]["doc_path"].startswith("vfx/v01/.proof-"))
        self.assertIn(f"vfx/v01/{self.name}", windows[0]["files"])
        self.assertIn(windows[0]["doc_path"], windows[0]["files"])

    def test_shots_json_is_read_when_no_shots_are_passed(self):
        self.ws.write_shots(self.shots)
        calls, still = self.still()
        windows, encode = self.encode(lambda s, e: [])
        self.go(still,encode)
        self.assertIn(5.0, [c["at"] for c in calls])
        self.assertEqual(len(windows), 4)

    def test_without_shots_the_window_is_the_whole_symbol(self):
        calls, still = self.still()
        windows, encode = self.encode(lambda s, e: [])
        self.go(still,encode, shots={})
        self.assertEqual([(w["start"], w["end"]) for w in windows], [(0.0, 15.0)])

    def test_a_hero_out_of_view_for_a_shot_is_an_error_on_that_shots_frame(self):
        calls, still = self.still()
        windows, encode = self.encode(lambda s, e: ["hero-ball"] if s == 3.0 else [])
        results = self.go(still,encode, shots=self.shots)
        errors = [(png, f) for png, found in results for f in found if f.startswith("error")]
        self.assertEqual(len(errors), 1)
        png, text = errors[0]
        self.assertIn("hero-ball", text)
        self.assertIn("shot 1.2", text)
        self.assertIn("world-open", text)
        self.assertTrue(png.name.endswith("__5s.png"))     # attached to that shot's midpoint
        # the engine's raw line is replaced by the verdict, not repeated
        self.assertFalse([f for _, found in results for f in found if "is drawn from" in f])

    def test_the_stills_own_off_camera_lines_count_too(self):
        def still(xml, at, **kw):
            lines = ["rendered locally"] + ([OFF_CAMERA.format(id="hero-ball")] if at == 9.0 else [])
            return varied_png(), "\n".join(lines)
        windows, encode = self.encode(lambda s, e: [])
        results = self.go(still,encode, shots=self.shots)
        errors = [f for _, found in results for f in found if f.startswith("error")]
        self.assertEqual(len(errors), 1)
        self.assertIn("9s of world-open", errors[0])

    def test_a_set_object_never_framed_in_any_shot_is_an_error(self):
        calls, still = self.still()
        windows, encode = self.encode(lambda s, e: ["floor", "wall", "desk"])
        results = self.go(still, encode, shots=self.shots)
        found = [f for _, fs in results for f in fs]
        # the per-shot counts are information; the whole-symbol verdict is the error
        self.assertEqual(len([f for f in found if f.startswith("note") and "3 of 3" in f]), 4)
        errors = [f for f in found if f.startswith("error")]
        self.assertEqual(len(errors), 1)
        self.assertIn("3 of 4", errors[0])
        self.assertIn("desk", errors[0])

    def test_an_object_seen_in_one_shot_is_not_an_error(self):
        calls, still = self.still()
        windows, encode = self.encode(lambda s, e: ["wall"] if s < 11.0 else [])
        results = self.go(still, encode, shots=self.shots)
        found = [f for _, fs in results for f in fs]
        self.assertFalse([f for f in found if f.startswith("error")])
        self.assertEqual(len([f for f in found if f.startswith("note") and "wall" in f]), 3)

    def test_no_engine_for_the_window_means_no_verdict_not_an_error(self):
        calls, still = self.still()
        results = self.go(still,lambda *a, **k: None, shots=self.shots)
        self.assertFalse([f for _, fs in results for f in fs if f.startswith("error")])
        self.assertEqual(len(results), len(calls))

    # AUTO-67: the author's frame renders stills only; the encode is the gate's
    def test_an_authors_frame_renders_stills_and_no_encode(self):
        calls, still = self.still()
        windows, encode = self.encode(lambda s, e: ["hero-ball"])
        with patch.object(vfx.render, "engine_render_still", still), \
                patch.object(vfx, "encode_findings", encode):
            results = self.ws.frames(self.name, times=[5.0], shots=self.shots, encode=False)
        self.assertEqual([c["at"] for c in calls], [5.0])
        self.assertEqual(windows, [])                      # no draft encode was paid for
        self.assertEqual(len(results), 1)
        self.assertIsNotNone(results[0][0])
        self.assertFalse([f for _, fs in results for f in fs if f.startswith("error")])

    def test_sr_frame_renders_stills_only_and_says_the_gate_encodes(self):
        self.ws.prepare(tokens={"ink": "#FFFFFF"}, frame=(1920, 1080))
        self.ws.write_shots(self.shots)
        spec = importlib.util.spec_from_file_location("sr_under_test", self.ws.dir / "sr.py")
        sr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sr)
        calls, still = self.still()
        windows, encode = self.encode(lambda s, e: ["hero-ball"])
        out = io.StringIO()
        with patch.object(vfx.render, "engine_render_still", still), \
                patch.object(vfx, "encode_findings", encode), contextlib.redirect_stdout(out):
            code = sr.frame([str(self.ws.dir / self.name)])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(c["at"] for c in calls), vfx.shot_times(SECTION["shots"], 15))
        self.assertEqual(windows, [])
        self.assertIn("gate", out.getvalue())             # the author is told where the camera check runs

    def test_the_gate_runs_the_encode_once_per_shot_per_round(self):
        calls, still = self.still()
        windows, encode = self.encode(lambda s, e: [])
        with patch.object(vfx.render, "engine_validate", lambda data, **kw: []), \
                patch.object(vfx.render, "engine_render_still", still), \
                patch.object(vfx, "encode_findings", encode), \
                patch.object(self.ws, "geometry", lambda *a, **k: []):
            symbols, findings = self.ws.validate(self.name, shots=self.shots)
        self.assertEqual(symbols, ["world-open"])
        self.assertEqual(sorted((w["start"], w["end"]) for w in windows),
                         [(0.0, 3.0), (3.0, 7.0), (7.0, 11.0), (11.0, 15.0)])
        self.assertEqual(len(calls), len(vfx.shot_times(SECTION["shots"], 15)))
        self.assertFalse([f for f in findings if f.startswith("error")])


class LostStills(unittest.TestCase):
    """AUTO-68: a still the host could not render is the host's failure, not
    the author's: it is retried once on the other host, and otherwise filed
    as `infra:`, never `error:`."""

    UNREACHABLE = "render host unreachable: ssh: connect to host root@small port 22: Connection refused"
    TIMED_OUT = ("render host unreachable: Command '['ssh', 'root@small', 'sh']' timed out "
                 "after 600.0 seconds")
    KILLED = "render failed\nerror: the engine could not render the frame: Killed"
    ROUTES = {"still": "root@small", "encode": "root@big"}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ws = vfx.Workspace(self.root, version="v01")
        self.name = self.ws.doc_name(1, SECTION)
        (self.ws.dir / self.name).write_text(WORLD_DOC, encoding="utf-8")
        self.shots = {"world-open": SECTION["shots"]}
        self.attempts: list[str] = []

    def tearDown(self):
        self._tmp.cleanup()

    def still(self, answers: dict[str, tuple]):
        def fake(xml, at, **kw):
            self.attempts.append(kw.get("host"))
            return answers[kw.get("host")]
        return fake

    def go(self, still, **kw):
        with patch.object(vfx.render, "remote_host", lambda args, job="encode": self.ROUTES[job]), \
                patch.object(vfx.render, "engine_render_still", still), \
                patch.object(vfx, "encode_findings", lambda *a, **k: []):
            return self.ws.frames(self.name, times=[5.0], shots=self.shots, **kw)

    def test_an_unreachable_host_is_dropped_and_the_still_rendered_on_the_other(self):
        with patch.dict(vfx.render._HOSTS, {"root@small": vfx.render.Host(addr="root@small", engine="/e")}):
            results = self.go(self.still({"root@small": (None, self.UNREACHABLE),
                                          "root@big": (varied_png(), "rendered on root@big")}))
            self.assertNotIn("root@small", vfx.render._HOSTS)       # probed afresh next time
        self.assertEqual(self.attempts, ["root@small", "root@big"])
        png, findings = results[0]
        self.assertIsNotNone(png)
        self.assertFalse([f for f in findings if f.startswith(("error", "infra"))], findings)
        self.assertTrue(any(f.startswith("note") and "root@big" in f and "root@small" in f
                            for f in findings), findings)

    def test_an_engine_that_died_naming_no_finding_is_retried_too(self):
        died = "render failed\nerror: the engine could not render the frame (exit 255)"
        results = self.go(self.still({"root@small": (None, died),
                                      "root@big": (varied_png(), "rendered on root@big")}))
        self.assertEqual(self.attempts, ["root@small", "root@big"])
        self.assertIsNotNone(results[0][0])

    def test_a_still_both_hosts_lost_is_an_infra_finding_not_an_error(self):
        results = self.go(self.still({"root@small": (None, self.UNREACHABLE),
                                      "root@big": (None, "render host unreachable: Connection timed out")}))
        self.assertEqual(self.attempts, ["root@small", "root@big"])
        png, findings = results[0]
        self.assertIsNone(png)
        self.assertTrue(findings[0].startswith("infra: frame at 5s of world-open"), findings)
        self.assertIn("root@small", findings[0])
        self.assertIn("root@big", findings[0])
        self.assertFalse([f for f in findings if f.startswith("error")], findings)

    def test_a_document_the_engine_refused_is_the_authors_error_with_no_retry(self):
        refused = "render failed\nerror[E7]: the camera frames nothing"
        results = self.go(self.still({"root@small": (None, refused),
                                      "root@big": (varied_png(), "rendered on root@big")}))
        self.assertEqual(self.attempts, ["root@small"])
        self.assertTrue(any(f.startswith("error") for f in results[0][1]))
        self.assertFalse(any(f.startswith("infra") for f in results[0][1]))

    def test_with_no_other_host_the_lost_still_is_infra_after_one_attempt(self):
        self.ROUTES = {"still": "root@small", "encode": "root@small"}
        with patch.object(vfx.render, "host_names", lambda args=None: ["root@small"]):
            results = self.go(self.still({"root@small": (None, self.UNREACHABLE)}))
        self.assertEqual(self.attempts, ["root@small"])
        self.assertTrue(results[0][1][0].startswith("infra:"), results[0][1])

    def test_what_is_the_hosts_failure(self):
        self.assertTrue(vfx.infra_failure(self.UNREACHABLE))
        self.assertTrue(vfx.infra_failure("no engine reachable"))
        self.assertTrue(vfx.infra_failure("render failed\nerror: the engine could not render the frame (exit 255)"))
        # A kill for memory is the world's cost, not the host's failure (below).
        self.assertFalse(vfx.infra_failure("render failed\nerror: the engine could not render the frame: Killed"))
        self.assertFalse(vfx.infra_failure(self.TIMED_OUT))
        self.assertFalse(vfx.infra_failure("render failed\nerror[E7]: the camera frames nothing"))
        self.assertFalse(vfx.infra_failure("render failed\nerror: solver refused its grid"))
        self.assertFalse(vfx.infra_failure("error: blocked on missing inputs: props/x.hdr"))

    def test_what_is_the_worlds_cost(self):
        # A still stopped at the deadline or killed for memory, however the
        # render reports it: the ssh side's timeout, the host's `timeout`
        # wrapper, the kernel's word, sdkkit.frame's own line.
        self.assertTrue(vfx.cost_failure(self.TIMED_OUT))
        self.assertTrue(vfx.cost_failure(self.KILLED))
        self.assertTrue(vfx.cost_failure("render failed\nerror: the engine could not render the frame (exit 137)"))
        self.assertTrue(vfx.cost_failure("render failed\nerror: the engine could not render the frame (exit 124)"))
        self.assertTrue(vfx.cost_failure("error: the render ran past 420 s"))
        # a local engine the signal killed exits -9, not 137
        self.assertTrue(vfx.cost_failure("render failed\nerror: the engine could not render the frame (exit -9)"))
        self.assertTrue(vfx.cost_failure(vfx.render._still_message(137, "", "cuda: alloc failed", "ok")))
        self.assertFalse(vfx.cost_failure(self.UNREACHABLE))
        self.assertFalse(vfx.cost_failure("render host unreachable: Connection timed out"))
        self.assertFalse(vfx.cost_failure("render failed\nerror: the engine could not render the frame (exit 255)"))
        self.assertFalse(vfx.cost_failure("render failed\nerror[E7]: the camera frames nothing"))
        self.assertFalse(vfx.cost_failure("rendered on root@big"))

    def test_a_still_one_host_stopped_for_its_cost_is_retried_on_the_other(self):
        results = self.go(self.still({"root@small": (None, self.TIMED_OUT),
                                      "root@big": (varied_png(), "rendered on root@big")}))
        self.assertEqual(self.attempts, ["root@small", "root@big"])
        png, findings = results[0]
        self.assertIsNotNone(png)
        self.assertFalse([f for f in findings if f.startswith(("error", "infra"))], findings)

    def test_a_still_both_hosts_stopped_for_its_cost_is_the_authors_error_naming_the_cost(self):
        results = self.go(self.still({"root@small": (None, self.TIMED_OUT),
                                      "root@big": (None, self.TIMED_OUT)}))
        self.assertEqual(self.attempts, ["root@small", "root@big"])
        png, findings = results[0]
        self.assertIsNone(png)
        self.assertTrue(findings[0].startswith("error: frame at 5s of world-open not rendered: "
                                               "stopped at the"), findings)
        self.assertIn("root@small", findings[0])
        self.assertIn("root@big", findings[0])
        self.assertIn("Cost drivers of this document: raster", findings[0])
        self.assertFalse([f for f in findings if f.startswith("infra")], findings)

    def test_a_host_that_stopped_a_still_for_its_cost_stays_known(self):
        # The host answered; only a host that did not is probed again.
        with patch.dict(vfx.render._HOSTS, {"root@small": vfx.render.Host(addr="root@small", engine="/e")}):
            results = self.go(self.still({"root@small": (None, self.TIMED_OUT),
                                          "root@big": (varied_png(), "rendered on root@big")}))
            self.assertIn("root@small", vfx.render._HOSTS)
        self.assertEqual(self.attempts, ["root@small", "root@big"])
        self.assertIsNotNone(results[0][0])

    def test_a_local_still_that_ran_past_the_deadline_is_the_worlds_cost_not_a_crash(self):
        # The local engine path raises on the deadline (sdkkit.frame catches
        # it the same way); the gate files the cost, it does not fall over.
        def still(xml, at, **kw):
            self.attempts.append(kw.get("host"))
            raise subprocess.TimeoutExpired(["sr"], kw.get("timeout", 600))
        results = self.go(still)
        self.assertEqual(self.attempts, ["root@small", "root@big"])
        png, findings = results[0]
        self.assertIsNone(png)
        self.assertTrue(findings[0].startswith("error: frame at 5s of world-open not rendered: "
                                               "stopped at the"), findings)
        self.assertIn("Cost drivers of this document", findings[0])

    def test_an_exit_137_at_the_deadline_is_the_deadline_not_memory(self):
        # render names the deadline when it judged the 137 a `timeout -k` kill;
        # the author is told time, not memory.
        at_deadline = ("render failed\nerror: the engine could not render the frame (exit 137) "
                       "after the still ran past 600 s on root@small")
        results = self.go(self.still({"root@small": (None, at_deadline),
                                      "root@big": (None, at_deadline)}))
        findings = results[0][1]
        self.assertIn("stopped at the", findings[0])
        self.assertNotIn("killed for memory", findings[0])

    def test_a_still_both_hosts_killed_for_memory_says_so(self):
        results = self.go(self.still({"root@small": (None, self.KILLED),
                                      "root@big": (None, self.KILLED)}))
        png, findings = results[0]
        self.assertIsNone(png)
        self.assertTrue(findings[0].startswith("error: frame at 5s of world-open not rendered: "
                                               "killed for memory"), findings)
        self.assertIn("Cost drivers of this document: raster", findings[0])

    def test_a_still_stopped_on_one_host_and_lost_on_the_other_is_infra(self):
        results = self.go(self.still({"root@small": (None, self.TIMED_OUT),
                                      "root@big": (None, self.UNREACHABLE)}))
        self.assertEqual(self.attempts, ["root@small", "root@big"])
        self.assertTrue(results[0][1][0].startswith("infra:"), results[0][1])

    def test_with_no_other_host_a_still_stopped_for_its_cost_is_the_authors_error(self):
        self.ROUTES = {"still": "root@small", "encode": "root@small"}
        with patch.object(vfx.render, "host_names", lambda args=None: ["root@small"]):
            results = self.go(self.still({"root@small": (None, self.TIMED_OUT)}))
        self.assertEqual(self.attempts, ["root@small"])
        self.assertTrue(results[0][1][0].startswith("error:"), results[0][1])
        self.assertIn("Cost drivers", results[0][1][0])


class EncodeCache(unittest.TestCase):
    """AUTO-67: the proof encode runs on the encode host, one per host at a
    time, and its lines are served from the cache the second time."""

    FILES = {"vfx/v01/a.scene.xml": b"<scene/>", "vfx/v01/.proof-a.scene.xml": b"<scene>p</scene>"}
    DOC = "vfx/v01/.proof-a.scene.xml"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self._tmp.name) / "stills"
        self.cache.mkdir()
        (self.cache / "engines.json").write_text(json.dumps(
            {"root@big": {"version": "1.0", "sha256": "abc", "path": "x"}}))
        self.jobs: list[str] = []
        self.locks: list[str] = []
        tests = self

        class Lock:
            def __init__(self, host):
                tests.locks.append(host)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return None
        self.lock = Lock
        self.envelope = {"result": {"outputs": [{"findings": [
            {"code": "X-rs-scene-render-3D-OFF-CAMERA", "severity": "warning",
             "message": OFF_CAMERA.format(id="desk")[len("warning: "):]}]}]}}

    def tearDown(self):
        self._tmp.cleanup()

    def run_job(self, returncode=0, stderr=""):
        def fake(cmd, root, host, *, job, verbose=False, **kw):
            self.jobs.append(host)
            return SimpleNamespace(returncode=returncode,
                                   stdout=json.dumps(self.envelope) if returncode == 0 else "",
                                   stderr=stderr)
        return fake

    def encode(self, run_job, **kw):
        routes = {"encode": "root@big", "still": "root@small"}
        with patch.dict(os.environ, {"YT_STILL_CACHE": str(self.cache)}), \
                patch.object(vfx.render, "run_job", run_job), \
                patch.object(vfx.render, "remote_host", lambda args, job="encode": routes[job]), \
                patch.object(vfx.sdkkit, "host_lock", self.lock):
            return vfx.encode_findings(self.FILES, self.DOC, **kw)

    def test_the_window_runs_on_the_encode_host_under_its_lock(self):
        lines = self.encode(self.run_job(), start=3, end=7)
        self.assertEqual(self.jobs, ["root@big"])
        self.assertEqual(self.locks, ["root@big"])
        self.assertEqual(vfx.framecheck.never_in_view(lines), ["desk"])

    def test_the_same_window_is_served_from_the_cache(self):
        first = self.encode(self.run_job(), start=3, end=7)
        second = self.encode(self.run_job(), start=3, end=7)
        self.assertEqual(self.jobs, ["root@big"])                  # one encode, two answers
        self.assertEqual(first, second)
        self.encode(self.run_job(), start=7, end=11)              # another window is another encode
        self.assertEqual(self.jobs, ["root@big", "root@big"])
        self.FILES = dict(self.FILES, **{"vfx/v01/a.scene.xml": b"<scene>changed</scene>"})
        self.encode(self.run_job(), start=3, end=7)               # a changed world too
        self.assertEqual(len(self.jobs), 3)

    def test_an_encode_the_host_could_not_hold_is_the_authors_error_and_is_not_cached(self):
        killed = self.run_job(returncode=137, stderr="scene-render: Killed\n")
        lines = self.encode(killed, start=3, end=7)
        self.assertTrue(lines[0].startswith("error:"), lines)
        self.assertIn("memory", lines[0])
        self.assertIn("3-7s", lines[0])
        self.encode(killed, start=3, end=7)
        self.assertEqual(len(self.jobs), 2)                       # a failure is run again next round

    def test_a_proof_encode_that_failed_for_another_reason_stays_a_warning(self):
        lines = self.encode(self.run_job(returncode=1, stderr="boom\n"), start=3, end=7)
        self.assertTrue(lines[0].startswith("warning:"), lines)

    def test_the_cache_is_written_whole_as_the_still_cache_is(self):
        # A reader in another process sees the file complete or not at all:
        # written beside its name and renamed into place, never partially.
        moves: list[tuple[str, str]] = []
        real = os.replace

        def replace(src, dst, *a, **kw):
            moves.append((Path(src).name, Path(dst).name))
            return real(src, dst, *a, **kw)
        with patch.object(vfx.os, "replace", replace):
            self.encode(self.run_job(), start=3, end=7)
        self.assertEqual(len(moves), 1, moves)
        tmp, final = moves[0]
        self.assertTrue(tmp.startswith(".") and tmp.endswith(f".{os.getpid()}.tmp"), tmp)
        self.assertTrue(final.endswith(".json"), final)
        self.assertEqual(sorted(p.name for p in self.cache.rglob("*.tmp")), [])

    def test_a_job_the_host_lost_is_not_the_authors_memory_error(self):
        # render._read_job's note on a lost job says "engine killed": the
        # host's failure, not the kernel's `Killed`, so no revision is paid.
        lost = ("remote job lost: its process is gone and it wrote no status "
                "(host rebooted or engine killed)\n")
        lines = self.encode(self.run_job(returncode=1, stderr=lost), start=3, end=7)
        self.assertTrue(lines[0].startswith("warning:"), lines)
        self.assertNotIn("memory", lines[0])
        self.assertFalse(vfx.OUT_OF_MEMORY.search(lost))
        self.assertTrue(vfx.OUT_OF_MEMORY.search("scene-render: Killed"))
        self.assertTrue(vfx.OUT_OF_MEMORY.search("CUDA error: Out of memory"))


class Review(unittest.TestCase):
    """The model looks at the frames; a low score sends the author back."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._engine = harness.ENGINE
        harness.ENGINE = FakeEngine
        FakeEngine.stream_calls, FakeEngine.json_calls, FakeEngine.reviews = [], [], []
        FakeEngine.doc = WORLD_DOC
        FakeEngine.time_out_on = 0
        FakeEngine.docs, FakeEngine.crash_on = [], 0
        idea = Idea(title="T", genre="g", description="d")
        bundle = {"idea": idea, "outline": {}, "claims": [], "assets": [], "premise": ""}
        self.ws = vfx.Workspace(self.root)
        self.planner = vfx.VfxPlanner(
            bundle, Dossier(self.root, idea, at=self.root), self.ws, model="sonnet",
            sections=[SECTION], revisions=2, author_bar=7.0, verbose=False)
        self.group = {"section": "Open", "seconds": 15, "effects": EFFECTS, "cuts": []}
        self.still = lambda xml, at, **kw: (varied_png(), "rendered locally")

    def tearDown(self):
        harness.ENGINE = self._engine
        self._tmp.cleanup()

    def author(self):
        with patch.object(vfx.render, "engine_validate", lambda data, **kw: []), \
                patch.object(vfx.render, "engine_render_still", self.still), \
                patch.object(vfx, "encode_findings", lambda *a, **k: []):
            return self.planner.author({"sections": [SECTION]}, [self.group], STYLE,
                                       {"ink": "#FFFFFF"})

    def test_a_low_score_sends_the_findings_back_and_both_rounds_are_recorded(self):
        FakeEngine.reviews = [
            {"frames": [{"frame": "frames/a.png", "shot": "1.2", "seen": "a grey wedge",
                         "findings": [{"category": "cinematography",
                                       "problem": "the hero is a sliver",
                                       "fix": "bring the camera to 60 cm behind the hero",
                                       "severity": "blocking"},
                                      {"category": "visual_quality",
                                       "problem": "the paper is opaque, with no translucency against the window",
                                       "fix": "set transmission to 0.35 on the paper material",
                                       "severity": "major"}]}],
             "continuity": "the hero changes size", "score": 5, "summary": "blockout"},
            {"frames": [], "continuity": "one take", "score": 8, "summary": "reads"}]
        authored = self.author()
        self.assertEqual(len(authored), 1)
        doc = authored[0]
        self.assertEqual(doc["symbols"], ["world-open"])
        self.assertEqual(doc["version"], "v01")
        self.assertTrue(doc["file"].startswith("vfx/v01/"))
        self.assertEqual([r["score"] for r in doc["rounds"]], [5, 8])
        self.assertEqual(doc["score"], 8)
        self.assertEqual(doc["revisions"], 1)
        self.assertEqual(doc["worlds"][0]["id"], "world-open")
        self.assertEqual(doc["worlds"][0]["shots"], ["1.1", "1.2", "1.3", "1.4"])
        authors = [c for c in FakeEngine.stream_calls if "Write" in c["tools"]]
        reviews = [c for c in FakeEngine.stream_calls if "Write" not in c["tools"]]
        self.assertEqual(len(authors), 2)
        self.assertEqual(len(reviews), 2)
        self.assertIn("60 cm behind the hero", authors[1]["prompt"])
        self.assertIn("5/10", authors[1]["prompt"])
        # the review's findings go back verbatim, as requirements, the
        # material / translucency / shadow ones first
        prompt = authors[1]["prompt"]
        self.assertIn("Requirements for this round", prompt)
        self.assertIn("the paper is opaque, with no translucency against the window", prompt)
        self.assertIn("set transmission to 0.35 on the paper material", prompt)
        self.assertLess(prompt.index("the paper is opaque"), prompt.index("the hero is a sliver"))
        # the reviewer is pointed at the PNGs to Read, under the version directory
        self.assertEqual(Path(reviews[0]["cwd"]), self.root / "vfx" / "v01")
        self.assertIn("frames/", reviews[0]["prompt"])
        self.assertIn("1.2", reviews[0]["prompt"])
        self.assertTrue((self.root / "decisions" / "vfx").is_dir())
        self.assertTrue((self.root / "vfx" / "v01" / "shots.json").is_file())

    def test_a_score_at_the_bar_needs_no_revision(self):
        FakeEngine.reviews = [{"frames": [], "score": 7, "summary": "ok"}]
        doc = self.author()[0]
        self.assertEqual(doc["revisions"], 0)
        self.assertEqual([r["score"] for r in doc["rounds"]], [7])
        self.assertEqual(len([c for c in FakeEngine.stream_calls if "Write" in c["tools"]]), 1)

    def test_without_an_engine_there_is_nothing_to_look_at(self):
        with patch.object(vfx.render, "engine_validate", lambda data, **kw: None):
            doc = self.planner.author({"sections": [SECTION]}, [self.group], STYLE,
                                      {"ink": "#FFFFFF"})[0]
        self.assertIsNone(doc["score"])
        self.assertEqual([c for c in FakeEngine.stream_calls if "Write" not in c["tools"]], [])

    def test_a_timed_out_revision_keeps_the_previous_document_and_is_recorded(self):
        FakeEngine.reviews = [{"frames": [], "score": 5, "summary": "blockout"},
                              {"frames": [], "score": 9, "summary": "never asked"}]
        FakeEngine.time_out_on = 2                      # the first revision times out
        doc = self.author()[0]
        self.assertEqual(len(doc["rounds"]), 2)
        self.assertTrue(doc["rounds"][1]["timed_out"])
        self.assertFalse(doc["rounds"][0].get("timed_out"))
        self.assertEqual(doc["rounds"][1]["score"], 5)           # the previous gate stands
        self.assertEqual(doc["score"], 5)
        self.assertEqual(doc["revisions"], 1)
        kept = (self.root / doc["file"]).read_text(encoding="utf-8")
        self.assertNotIn("half-edited", kept)
        # the document the first gate built from the program, not the half edit
        self.assertEqual(sdk().read(kept.encode()).to_bytes(layout="pretty"),
                         sdk().read(WORLD_DOC.encode()).to_bytes(layout="pretty"))
        self.assertIsNotNone(doc["provenance"])                  # still the program's output
        self.assertTrue(doc["program"].endswith(".py"))
        reviews = [c for c in FakeEngine.stream_calls if "Write" not in c["tools"]]
        self.assertEqual(len(reviews), 1)                        # nothing re-gated
        authors = [c for c in FakeEngine.stream_calls if "Write" in c["tools"]]
        self.assertEqual([c["timeout"] for c in authors], [3600.0, 3600.0])

    def test_the_review_never_sees_a_frame_the_gate_did_not_render(self):
        FakeEngine.stale_frame = True
        try:
            doc = self.author()[0]
        finally:
            FakeEngine.stale_frame = False
        frames = self.root / doc["file"].rsplit("/", 1)[0] / "frames"
        stem = doc["file"].rsplit("/", 1)[1][:-len(".scene.xml")]
        self.assertFalse((frames / f"{stem}__9s.png").exists())        # the author's own
        self.assertTrue(list(frames.glob(f"{stem}__world-open__*.png")))  # the gate's proofs

    def test_the_section_ends_on_its_best_reviewed_round(self):
        # AUTO-78: a revision judged worse ends the loop, so the last round is
        # the one that falls back; the fake's verdict follows its scores
        FakeEngine.reviews = [{"frames": [], "score": 5, "summary": "round zero"},
                              {"frames": [], "score": 6, "summary": "the best"},
                              {"frames": [], "score": 4, "summary": "worse"}]
        FakeEngine.docs = [WORLD_DOC, WORLD_DOC.replace("#AAAAAA", "#BBBBBB"),
                           WORLD_DOC.replace("#AAAAAA", "#CCCCCC")]
        doc = self.author()[0]
        self.assertEqual([r["score"] for r in doc["rounds"]], [5, 6, 4])
        self.assertEqual(doc["best_round"], 1)
        self.assertEqual(doc["score"], 6)
        self.assertEqual(doc["review"]["summary"], "the best")
        kept = (self.root / doc["file"]).read_text()
        self.assertIn("#BBBBBB", kept)                        # round 1's document is back
        self.assertNotIn("#CCCCCC", kept)
        self.assertIsNotNone(doc["provenance"])               # and still its program's output
        stem = doc["file"].rsplit("/", 1)[1][:-len(".scene.xml")]
        rounds = self.root / "vfx" / "v01" / "rounds" / stem
        # the section's record (AUTO-82) sits beside its kept rounds
        self.assertEqual(sorted(p.name for p in rounds.iterdir() if p.is_dir()),
                         ["r00", "r01", "r02"])
        self.assertTrue((rounds / "record.json").is_file())

    def test_a_final_revision_that_crashes_falls_back_to_the_best_round(self):
        FakeEngine.reviews = [{"frames": [], "score": 5, "summary": "round zero"}]
        FakeEngine.crash_on = 2                               # the only revision crashes
        self.planner.revisions = 1
        doc = self.author()[0]
        self.assertEqual(doc["score"], 5)
        self.assertEqual(doc["best_round"], 0)
        self.assertEqual(doc["symbols"], ["world-open"])
        self.assertFalse([f for f in doc["findings"] if f.startswith("error")])
        program = self.root / doc["program"]
        self.assertNotIn("not python", program.read_text())   # the crashed edit is gone
        scene = self.planner.scene_map([self.group], [doc])
        self.assertIn("1.1", scene["by_label"])                # the build can place it

    def test_a_section_whose_authoring_raises_is_an_error_record(self):
        with patch.object(self.planner, "_author_one", side_effect=RuntimeError("boom")):
            doc = self.author()[0]
        self.assertIsNone(doc["score"])
        self.assertIn("boom", doc["findings"][0])
        self.assertEqual(doc["worlds"][0]["id"], "world-open")

    def test_sections_re_authors_only_those_and_carries_the_rest(self):
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "first"}]
        first = self.author()[0]
        self.assertEqual(first["version"], "v01")
        idea = Idea(title="T", genre="g", description="d")
        bundle = {"idea": idea, "outline": {}, "claims": [], "assets": [], "premise": ""}
        FakeEngine.stream_calls = []
        self.planner = vfx.VfxPlanner(
            bundle, Dossier(self.root, idea, at=self.root), vfx.Workspace(self.root),
            model="sonnet", sections=[SECTION], revisions=2, author_bar=7.0, verbose=False,
            sections_only={2})                                 # section 1 is not asked for
        carried = self.author()[0]
        self.assertEqual(carried["version"], "v02")
        self.assertEqual(carried["carried_from"], "v01")
        self.assertEqual(carried["score"], 8)
        self.assertEqual(carried["cost_usd"], 0.0)
        self.assertTrue((self.root / carried["file"]).is_file())
        self.assertTrue((self.root / carried["program"]).is_file())
        self.assertIsNotNone(vfx.sdkkit.provenance_of(self.root / carried["file"]))
        self.assertFalse([c for c in FakeEngine.stream_calls if "Write" in c["tools"]])
        # and asking for section 1 authors it again
        self.planner.sections_only, self.planner._stale = {1}, {"12-author"}
        FakeEngine.reviews = [{"frames": [], "score": 9, "summary": "again"}]
        again = self.author()[0]
        self.assertEqual(again["version"], "v03")
        self.assertNotIn("carried_from", again)
        self.assertEqual(again["score"], 9)

    def test_a_worse_re_authored_section_keeps_the_version_it_replaced(self):
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "first"}]
        FakeEngine.docs = [WORLD_DOC]
        self.planner.revisions = 0
        first = self.author()[0]
        idea = Idea(title="T", genre="g", description="d")
        bundle = {"idea": idea, "outline": {}, "claims": [], "assets": [], "premise": ""}
        self.planner = vfx.VfxPlanner(
            bundle, Dossier(self.root, idea, at=self.root), vfx.Workspace(self.root),
            model="sonnet", sections=[SECTION], revisions=0, author_bar=7.0, verbose=False,
            sections_only={1})
        FakeEngine.reviews = [{"frames": [], "score": 5, "summary": "worse"}]
        FakeEngine.docs = [WORLD_DOC.replace("#AAAAAA", "#BBBBBB")]
        kept = self.author()[0]
        self.assertEqual(kept["version"], "v02")
        self.assertEqual(kept["kept_from"], "v01")
        self.assertEqual(kept["score"], 8)
        self.assertEqual(kept["attempt"]["score"], 5)
        self.assertGreater(kept["cost_usd"], 0)                 # the attempt was paid for
        self.assertIn("#AAAAAA", (self.root / kept["file"]).read_text())
        stem = kept["file"].rsplit("/", 1)[1][:-len(".scene.xml")]
        self.assertTrue((self.root / "vfx" / "v02" / "rounds" / f"{stem}.attempt").is_dir())

    def test_which_version_of_a_section_is_kept(self):
        good, better = {"score": 6, "findings": []}, {"score": 7, "findings": []}
        broken = {"score": None, "findings": ["error: hero penetrates the set"]}
        self.assertTrue(vfx.keeps_previous(good, broken))
        self.assertTrue(vfx.keeps_previous(better, good))
        self.assertFalse(vfx.keeps_previous(good, better))
        self.assertFalse(vfx.keeps_previous(good, dict(good)))  # a tie keeps the new one
        self.assertFalse(vfx.keeps_previous(broken, good))

    def test_the_review_scores_fidelity_to_an_approved_keyframe(self):
        approve_keyframe(self.root)
        FakeEngine.reviews = [{"frames": [], "score": 9, "fidelity": 8, "summary": "ok"}]
        self.author()
        review = [c for c in FakeEngine.stream_calls if "Write" not in c["tools"]][0]
        self.assertIn("approved look", review["prompt"])
        self.assertIn(str(self.root / "keyframe" / "keyframe.png"), review["prompt"])
        self.assertIn("fidelity", review["prompt"])
        self.assertIn("fidelity", vfx.REVIEW_SCHEMA["properties"])

    def test_declined_props_are_recorded_on_the_document(self):
        (self.root / "props" / "hdri").mkdir(parents=True)
        (self.root / "props" / "hdri" / "sky.hdr").write_bytes(b"hdr")
        (self.root / "props.json").write_text(json.dumps({"items": [
            {"id": "sky", "kind": "hdri", "file": "props/hdri/sky.hdr", "licence": "CC0",
             "credit": "", "about": "studio"}]}))
        FakeEngine.doc = WORLD_DOC.replace(
            '<output path="preview.mp4" codec="h264"/>',
            '<metadata><meta name="props-declined" value="hdri: a studio, not a sunlit room"/>'
            '</metadata>\n  <output path="preview.mp4" codec="h264"/>')
        FakeEngine.reviews = [{"frames": [], "score": 9, "summary": "ok"}]
        doc = self.author()[0]
        self.assertEqual(doc["props_declined"], {"hdri": "a studio, not a sunlit room"})
        self.assertFalse([f for f in doc["findings"] if f.startswith("error")])
        self.assertEqual(vfx.declined_of([doc]), {"hdri": "a studio, not a sunlit room"})

    def test_the_second_run_lands_in_the_next_version(self):
        FakeEngine.reviews = [{"frames": [], "score": 9, "summary": "ok"}]
        first = self.author()[0]
        self.planner.fresh = True
        FakeEngine.reviews = [{"frames": [], "score": 9, "summary": "ok"}]
        second = self.author()[0]
        self.assertEqual((first["version"], second["version"]), ("v01", "v02"))
        self.assertTrue((self.root / first["file"]).is_file())
        self.assertTrue((self.root / second["file"]).is_file())

    def test_scene_map_sends_every_shot_to_the_world(self):
        FakeEngine.reviews = [{"frames": [], "score": 9, "summary": "ok"}]
        authored = self.author()
        scene = self.planner.scene_map([self.group], authored)
        file = authored[0]["file"]
        self.assertEqual(scene["by_label"], {
            label: {"file": file, "symbol": "world-open"} for label in ("1.1", "1.2", "1.3", "1.4")})
        self.assertEqual(scene["symbols"]["world-open"]["shots"], ["1.1", "1.2", "1.3", "1.4"])
        self.assertEqual(scene["phases"], {"open-release": "world-open",
                                           "open-glide": "world-open",
                                           "open-settle": "world-open"})
        self.assertEqual(scene["dropped"], {})
        self.assertEqual(scene["files"], [file])


class Flags(unittest.TestCase):

    def test_author_bar_and_two_revisions_are_the_defaults(self):
        parser = argparse.ArgumentParser()
        vfx.register(parser.add_subparsers(), [])
        args = parser.parse_args(["vfx"])
        self.assertEqual(args.author_bar, 7.0)
        self.assertEqual(args.revisions, 2)
        self.assertEqual(parser.parse_args(["vfx", "--author-bar", "8.5"]).author_bar, 8.5)


if __name__ == "__main__":
    unittest.main()


class ResearchAssets(unittest.TestCase):
    """Each kind of downloaded research asset is offered for what it is, with
    a path the author can open from its own directory."""

    def test_kinds_paths_and_what_the_engine_cannot_load(self):
        root = Path("/p")
        assets = [
            {"kind": "image", "file": "downloads/01-a.jpg", "title": "a figure",
             "licence": "CC BY 4.0", "credit_line": "A. Author", "paper": "Doe 2024"},
            {"kind": "video", "file": "downloads/02-b.mp4", "title": "a clip", "licence": "PD"},
            {"kind": "3d-model", "file": "downloads/03-c.stl", "title": "a part", "licence": "CC0"},
            {"kind": "3d-model", "file": "downloads/04-d.glb", "title": "a body", "licence": "CC0"},
            {"kind": "structure", "file": "downloads/05-e.cif", "title": "a protein",
             "licence": "CC0"},
            {"kind": "audio", "file": "downloads/06-f.mp3", "title": "a sound"},
            {"kind": "document", "file": "downloads/07-g.pdf", "title": "a paper"},
            {"kind": "article", "file": "downloads/08-h.html", "title": "a news story"},
            {"kind": "3d-model", "file": "downloads/09-i.obj", "source_file": "downloads/09-i.stl",
             "title": "a converted part", "licence": "CC0"},
            {"kind": "image", "title": "not downloaded"},
        ]
        text = "\n".join(vfx.research_asset_lines(assets, root, root / "vfx" / "v01"))
        self.assertIn("`../../downloads/01-a.jpg`", text)            # from vfx/v01/
        self.assertIn("baseColorMap", text)                          # a photo as a map
        self.assertIn("Doe 2024", text) and self.assertIn("A. Author", text)
        self.assertIn("<video>", text)
        self.assertIn("03-c.stl` — a part (CC0) — not loadable", text)
        self.assertNotIn("04-d.glb` — a body (CC0) — not loadable", text)
        self.assertIn("mmCIF", text)
        self.assertNotIn("a sound", text)
        self.assertNotIn("a paper", text)
        self.assertNotIn("not downloaded", text)
        self.assertNotIn("a news story", text)                       # article: not the picture's
        self.assertIn("09-i.obj` — a converted part (CC0)\n", text + "\n")   # loadable

    def test_nothing_downloaded_is_no_section(self):
        self.assertEqual(vfx.research_asset_lines([{"kind": "image", "title": "x"}],
                                                  Path("/p"), Path("/p/vfx")), [])


class FactCheckConstraints(unittest.TestCase):
    """What the screenplay's fact-check says the picture may not show reaches
    the authors verbatim: the whole-film flags everywhere, a shot's flags in
    the section that holds the shot."""

    SCRIPT = {"sections": [
        {"heading": "One", "shots": [{"n": 1, "label": "1.1"}, {"n": 2, "label": "1.2"}]},
        {"heading": "Two", "shots": [{"n": 3, "label": "2.1"}]}],
        "check": {"verdict": "ok", "flags": [
            {"n": 0, "severity": "important", "problem": "the whole film draws X at atom scale",
             "fix": "draw X as a ribbon"},
            {"n": 3, "severity": "blocker", "problem": "shot 2.1 labels a model wrongly",
             "fix": "drop the label"},
            {"n": 1, "severity": "minor", "problem": "a minor wording", "fix": "reword"}]}}

    def test_binding_flags_are_kept_verbatim_with_their_shot(self):
        rows = vfx.picture_constraints(self.SCRIPT)
        self.assertEqual([(r["label"], r["severity"]) for r in rows],
                         [("", "important"), ("2.1", "blocker")])        # minor is not binding
        self.assertEqual(rows[0]["fix"], "draw X as a ribbon")

    def test_a_section_gets_the_whole_film_and_its_own_shots(self):
        rows = vfx.picture_constraints(self.SCRIPT)
        one = "\n".join(vfx.constraint_lines(rows, {"1.1", "1.2"}))
        two = "\n".join(vfx.constraint_lines(rows, {"2.1"}))
        self.assertIn("whole film: the whole film draws X", one)
        self.assertNotIn("labels a model", one)
        self.assertIn("[blocker] shot 2.1: shot 2.1 labels a model wrongly", two)
        self.assertIn("REQUIRED: drop the label", two)

    def test_no_check_means_no_section(self):
        self.assertEqual(vfx.picture_constraints({"sections": []}), [])
        self.assertEqual(vfx.constraint_lines([], {"1.1"}), [])
        self.assertEqual(vfx.picture_constraints(None), [])


class WorldLength(unittest.TestCase):
    """The brief's symbol duration and the timing gate's requirement are one
    number: a world spans to the end of its last shot, gaps between takes in."""

    def test_the_brief_states_what_the_gate_requires(self):
        section = {"heading": "Two metres", "shots": [
            {"label": "1.1", "at": 10.0, "seconds": 5.0},
            {"label": "1.2", "at": 15.7, "seconds": 4.0},       # 0.7 s of breath before it
            {"label": "1.3", "at": 20.0, "seconds": 6.32}]}
        world = vfx.world_symbols(section, [])[0]
        self.assertEqual(world["seconds"], 16.32)               # not 15.32, the shots' sum
        required = vfx.world_span(world["shots"])
        self.assertEqual(world["seconds"], required)
        doc = (f'<scene><symbols><symbol id="{world["id"]}" width="8" height="8" '
               f'duration="{world["seconds"]:g}"/></symbols></scene>')
        self.assertEqual(vfx.timing_findings(doc, {world["id"]: required}), [])


#: A section planned with one cut and two settings (AUTO-143): the story moves
#: from a 1953 lab to the inside of a cell at shot 1.3.
SETTINGS_GROUP = {"section": "Open", "seconds": 15, "effects": EFFECTS,
                  "setting": "1953 Cavendish lab, archival footage",
                  "cuts": [{"before": "1.3", "why": "the story moves inside the cell",
                            "setting": "inside one cell membrane, molecular scale"}]}

#: The two worlds that group asks for, as an author writes them.
TWO_WORLD_DOC = WORLD_DOC.replace(
    '<symbol id="world-open" width="1920" height="1080" duration="15">',
    '<symbol id="world-open" width="1920" height="1080" duration="7">').replace(
    '<key time="15" value="200"/>', '<key time="7" value="200"/>').replace(
    "    </symbol>\n  </symbols>",
    "    </symbol>\n"
    '    <symbol id="world-open-2" width="1920" height="1080" duration="8">\n'
    '      <camera id="cam2" x="0" y="-100" z="-400" target="hero-ball2" focalLength="35" active="true"/>\n'
    '      <object3D id="floor2" primitive="plane" material="m-a" x="0" y="0" z="0" width="900" height="900" rotationX="90"/>\n'
    '      <object3D id="hero-ball2" primitive="sphere" material="m-a" x="0" y="-150" z="100" radius="20">\n'
    '        <animate property="x"><key time="0" value="-200"/><key time="8" value="200"/></animate>\n'
    "      </object3D>\n"
    "    </symbol>\n  </symbols>").replace(
    '<instance id="u" symbol="world-open" start="0" end="15"/>',
    '<instance id="u" symbol="world-open" start="0" end="7"/>\n'
    '    <instance id="u2" symbol="world-open-2" start="7" end="15"/>')


class Settings(unittest.TestCase):
    """AUTO-143: a cut carries the setting of the world it starts, so a
    section can move to another place, scale, time or real record; inside
    one setting the take stays continuous."""

    def test_each_world_carries_its_setting(self):
        worlds = vfx.world_symbols(SECTION, EFFECTS, cuts=["1.3"],
                                   settings={"1.3": "inside a cell"},
                                   first_setting="a 1953 lab")
        self.assertEqual([(w["id"], w["setting"]) for w in worlds],
                         [("world-open", "a 1953 lab"), ("world-open-2", "inside a cell")])

    def test_with_no_settings_every_setting_is_empty(self):
        self.assertEqual([w["setting"] for w in vfx.world_symbols(SECTION, EFFECTS,
                                                                  cuts=["1.3"])], ["", ""])
        self.assertEqual(vfx.world_symbols({"heading": "Open", "seconds": 20},
                                           EFFECTS)[0]["setting"], "")

    def test_the_planner_worlds_read_the_groups_settings(self):
        worlds = vfx.VfxPlanner._worlds(SECTION, SETTINGS_GROUP)
        self.assertEqual([w["setting"] for w in worlds],
                         ["1953 Cavendish lab, archival footage",
                          "inside one cell membrane, molecular scale"])
        # a cached 11-effects group from before settings reads as empty settings
        old = {"section": "Open", "effects": EFFECTS,
               "cuts": [{"before": "1.3", "why": "another scale"}]}
        self.assertEqual([w["setting"] for w in vfx.VfxPlanner._worlds(SECTION, old)],
                         ["", ""])

    def test_the_effects_schema_asks_for_a_setting_per_world(self):
        props = vfx.EFFECTS_SCHEMA["properties"]
        self.assertEqual(props["setting"]["type"], "string")
        cut = props["cuts"]["items"]
        self.assertIn("setting", cut["properties"])
        self.assertIn("setting", cut["required"])

    def test_the_digest_takes_the_setting_and_keeps_an_old_groups_sha(self):
        tokens = {"ink": "#FFFFFF"}
        plain = {"effects": EFFECTS, "cuts": []}
        before = "7cbaf6c1c9554fd31a92dbd1072313a40cda258f4bae654ce2946a828e01601c"
        before_cut = "a01abf772b6ccc2610c924486a44d05c42bfb870f5b1b6ff8a526c8871037af6"
        # pinned before AUTO-143: a group without a setting keeps its sha
        self.assertEqual(vfx.section_inputs_digest(SECTION, plain, tokens, ""), before)
        self.assertEqual(vfx.section_inputs_digest(SECTION, dict(plain, setting=""),
                                                   tokens, ""), before)
        old_cut = {"effects": EFFECTS, "cuts": [{"before": "1.3", "why": "a new place"}]}
        self.assertEqual(vfx.section_inputs_digest(SECTION, old_cut, tokens, ""), before_cut)
        lab = vfx.section_inputs_digest(SECTION, dict(plain, setting="a lab"), tokens, "")
        cell = vfx.section_inputs_digest(SECTION, dict(plain, setting="a cell"), tokens, "")
        self.assertNotIn(lab, (before, cell))
        moved = dict(old_cut, cuts=[dict(old_cut["cuts"][0], setting="a cell")])
        self.assertNotEqual(vfx.section_inputs_digest(SECTION, moved, tokens, ""), before_cut)


class SettingsPlan(unittest.TestCase):
    """The planner of each section sees the film's sections in order, its own
    marked, and is told when a cut is warranted."""

    LATER = dict(SECTION, heading="Inside the cell", shots=[
        dict(s, label=f"2.{n}", visual=f"membrane {n}")
        for n, s in enumerate(SECTION["shots"], 1)])
    CLOSE = dict(SECTION, heading="What it meant", shots=[
        dict(SECTION["shots"][0], label="3.1", visual="x" * 400)])
    HEADINGS = ("Open", "Inside the cell", "What it meant")

    def plan(self, answer):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            idea = Idea(title="T", genre="g", description="d")
            bundle = {"idea": idea, "outline": {}, "claims": [], "assets": [], "premise": ""}
            planner = vfx.VfxPlanner(bundle, Dossier(root, idea, at=root), vfx.Workspace(root),
                                     model="sonnet", verbose=False)
            seen = {}

            def decide(name, prompt, schema):
                seen[name] = prompt
                return answer(name)
            with patch.object(planner, "_decide", decide), \
                    patch.object(planner, "vocabulary", lambda: ""):
                got = planner.effects({"sections": [SECTION, self.LATER, self.CLOSE]}, STYLE)
        return got, seen

    def test_every_sections_prompt_lists_the_film_and_marks_its_own(self):
        _, seen = self.plan(lambda name: {"effects": [], "cuts": []})
        self.assertEqual(len(seen), 3)
        for own in self.HEADINGS:
            prompt = seen[f"plan-effects-{vfx.slugify(own, 40)}"]
            lines = [line for line in prompt.splitlines()
                     if any(f". {h}: " in line for h in self.HEADINGS)]
            self.assertEqual(len(lines), 3, prompt)
            for n, (line, heading) in enumerate(zip(lines, self.HEADINGS), 1):
                self.assertIn(f"{n}. {heading}: ", line)
                self.assertEqual(vfx.THIS_SECTION in line, heading == own, line)
            self.assertIn("release", lines[0])                 # its first shot's visual
            self.assertIn("membrane 1", lines[1])
            self.assertIn("x" * 160, lines[2])
            self.assertNotIn("x" * 161, lines[2])               # clipped to 160
            # a cut is warranted where the story moves to another place, scale,
            # time or real-world record; inside one setting, one continuous take
            for words in ("another place, scale, time or real-world record",
                          "archival footage", "Inside one setting, one continuous take"):
                self.assertIn(words, prompt)
            self.assertNotIn("the default is none", prompt)

    def test_two_sections_with_one_heading_mark_only_their_own_line(self):
        twin = dict(SECTION, shots=[dict(SECTION["shots"][0], label="3.1", visual="twin")])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            idea = Idea(title="T", genre="g", description="d")
            bundle = {"idea": idea, "outline": {}, "claims": [], "assets": [], "premise": ""}
            planner = vfx.VfxPlanner(bundle, Dossier(root, idea, at=root), vfx.Workspace(root),
                                     model="sonnet", verbose=False)
            prompts = []

            def decide(name, prompt, schema):
                prompts.append(prompt)
                return {"effects": [], "cuts": []}
            with patch.object(planner, "_decide", decide), \
                    patch.object(planner, "vocabulary", lambda: ""):
                planner.effects({"sections": [SECTION, self.LATER, twin]}, STYLE)
        self.assertEqual(len(prompts), 3)
        for prompt in prompts:
            own = 3 if "- 3.1 (" in prompt else 2 if "- 2.1 (" in prompt else 1
            marked = [line for line in prompt.splitlines() if vfx.THIS_SECTION in line]
            self.assertEqual(len(marked), 1, prompt)
            self.assertTrue(marked[0].startswith(f"{own}. "), marked)

    def test_the_plan_keeps_the_setting_and_each_cuts_setting(self):
        def answer(name):
            if name == "plan-effects-open":
                return {"effects": [], "setting": "a 1953 lab",
                        "cuts": [{"before": "1.3", "why": "inside", "setting": "a cell"},
                                 {"before": "9.9", "why": "nowhere", "setting": "x"}]}
            return {"effects": [], "cuts": []}
        got, _ = self.plan(answer)
        self.assertEqual(got[0]["setting"], "a 1953 lab")
        self.assertEqual(got[0]["cuts"], [{"before": "1.3", "why": "inside", "setting": "a cell"}])
        self.assertEqual(got[1]["setting"], "")                # none answered, none kept


class SettingsBrief(unittest.TestCase):
    setUp, tearDown = Brief.setUp, Brief.tearDown

    def brief_of(self, group):
        ws = vfx.Workspace(self.root, version="v01")
        worlds = vfx.VfxPlanner._worlds(SECTION, group)
        path = ws.brief(1, idea=self.idea, house_rules="", style=STYLE,
                        tokens={"ink": "#FFFFFF"}, frame=(1920, 1080), section=SECTION,
                        effects=EFFECTS, words={}, assets=[], worlds=worlds)
        return path.read_text(encoding="utf-8")

    def test_a_two_world_brief_lists_each_setting(self):
        text = self.brief_of(SETTINGS_GROUP)
        self.assertIn("Each symbol is one world", text)
        self.assertNotIn("A section is one world", text)
        self.assertIn("`world-open`: 1953 Cavendish lab, archival footage", text)
        self.assertIn("`world-open-2`: inside one cell membrane, molecular scale", text)
        self.assertNotIn("the room does not change", text)
        # what carries across the cut, and what belongs to the world
        self.assertIn("the hero keeps its scale, material and colour across worlds", text)
        self.assertIn("the grade and type language carry across", text)

    def test_a_single_world_brief_reads_as_one_continuous_take(self):
        text = self.brief_of({"section": "Open", "effects": EFFECTS, "cuts": []})
        self.assertIn("Each symbol is one world", text)
        self.assertIn("the room does not change", text)
        self.assertIn("one continuous path", text)
        self.assertNotIn("across worlds", text)

    def test_the_author_system_says_each_symbol_is_one_world(self):
        self.assertIn("Each symbol is one world", vfx.AUTHOR_SYSTEM)
        self.assertNotIn("A section is one world", vfx.AUTHOR_SYSTEM)


class SettingsReview(unittest.TestCase):
    setUp, tearDown, author = Review.setUp, Review.tearDown, Review.author

    def test_the_section_review_scopes_the_room_to_one_world(self):
        FakeEngine.doc = TWO_WORLD_DOC
        self.group = SETTINGS_GROUP
        FakeEngine.reviews = [{"frames": [], "score": 9, "summary": "ok"}]
        doc = self.author()[0]
        self.assertEqual(doc["symbols"], ["world-open", "world-open-2"])
        self.assertEqual([w.get("setting") for w in doc["worlds"]],
                         ["1953 Cavendish lab, archival footage",
                          "inside one cell membrane, molecular scale"])
        review = [c for c in FakeEngine.stream_calls if "Write" not in c["tools"]][0]
        prompt = review["prompt"]
        self.assertIn("within each world", prompt)
        self.assertIn("different set across a planned cut is expected and is not a finding",
                      prompt)
        self.assertIn("inside one cell membrane, molecular scale", prompt)

    def test_a_cached_group_without_settings_still_authors(self):
        self.group = {"section": "Open", "seconds": 15, "effects": EFFECTS,
                      "cuts": [{"before": "1.3", "why": "another scale"}]}
        FakeEngine.doc = TWO_WORLD_DOC
        FakeEngine.reviews = [{"frames": [], "score": 9, "summary": "ok"}]
        doc = self.author()[0]
        self.assertEqual(doc["score"], 9)
        self.assertFalse([f for f in doc["findings"] if f.startswith("error")], doc["findings"])
        self.assertEqual([w.get("setting") for w in doc["worlds"]], ["", ""])
