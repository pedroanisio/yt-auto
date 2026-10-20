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
import io
import json
import re
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
            author_program(Path(self.config.cwd), name, FakeEngine.doc)
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
            yield Event(type="assistant",
                        text="I looked.\n```json\n" + json.dumps(review) + "\n```")
        yield Event(type="result", raw={})


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
