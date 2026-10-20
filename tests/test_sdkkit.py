"""The coding kit: an authoring agent composes with programs written against
the sr-core SDK, and the kit gives it what to read, the helper its programs
import and the build that runs a program, checks what it wrote and records
where the document came from. These tests never call a model or an engine:
validation is off, and the examples are run with the SDK's checks only (each
was also validated and rendered on the engine when it was written).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests import hermetic  # noqa: E402
from tests.test_universality import leaks  # noqa: E402
from ytstudio import sdkkit  # noqa: E402

MINIMAL = '''from srkit import FRAME, S, save
scene = S.Scene(version="auto")
scene.add_project(width=FRAME["width"], height=FRAME["height"], fps=str(FRAME["fps"]),
                  duration=1)
scene.add_composition()
save(scene, "minimal.scene.xml")
'''

MANIFEST = {"format": "scene-render-capabilities/1",
            "engine": {"name": "rs-scene-render", "version": "9.9.9"}, "schema": "1.7.0",
            "entries": [{"construct": "material/@unevenness", "status": "approximate",
                         "when": ["camera/@renderer=pathtrace"],
                         "note": "applied by the raster renderer only"}]}


class KitCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="kit-"))
        self.engine_src = Path(tempfile.mkdtemp(prefix="engine-"))
        (self.engine_src / "capabilities.json").write_text(json.dumps(MANIFEST))
        patcher = mock.patch.object(sdkkit, "ENGINE_SRC", self.engine_src)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.addCleanup(shutil.rmtree, self.engine_src, True)
        self.installed = sdkkit.install(self.dir, frame=(1280, 720), fps=25)

    def program(self, text: str, name: str = "compose.py") -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path


class Install(KitCase):
    def test_the_workspace_gets_the_helper_the_commands_and_the_reading(self):
        for rel in ("srkit.py", "kit.py", "frame.json", "kit/SDK-GUIDE.md", "kit/ENGINE.md",
                    "kit/examples/README.md"):
            self.assertTrue((self.dir / rel).is_file(), rel)
        self.assertEqual(json.loads((self.dir / "frame.json").read_text()),
                         {"width": 1280, "height": 720, "fps": 25})
        self.assertIn("sr_core", (self.dir / "srkit.py").read_text())
        self.assertIn("sdkkit.main", (self.dir / "kit.py").read_text())

    def test_the_guide_is_sr_cores_own(self):
        self.assertIn("Scene SDK guide", (self.dir / "kit/SDK-GUIDE.md").read_text())

    def test_engine_md_carries_the_manifest_and_the_schemas_renderer_notes(self):
        text = (self.dir / "kit/ENGINE.md").read_text()
        self.assertIn("rs-scene-render 9.9.9", text)
        self.assertIn("`material/@unevenness` -- approximate (when camera/@renderer=pathtrace)",
                      text)
        # read from the schema, not written here: the path tracer drops clearcoat
        self.assertIn("`material/@clearcoat`", text)
        self.assertIn("Not applied by the path tracer", text)

    def test_measured_rows_join_engine_md(self):
        text = sdkkit.engine_text({"manifest": MANIFEST, "source": "x"}, [],
                                  [{"probe": "glass", "score": 4, "summary": "opaque"}])
        self.assertIn("Measured on this engine", text)
        self.assertIn("glass: 4 -- opaque", text)

    def test_every_example_is_copied_and_indexed(self):
        names = [p.name for p in sdkkit.example_files()]
        self.assertGreaterEqual(len(names), 4)
        index = (self.dir / "kit/examples/README.md").read_text()
        for name in names:
            self.assertTrue((self.dir / "kit/examples" / name).is_file(), name)
            self.assertIn(name, index)

    def test_a_stage_that_writes_its_own_frame_json_keeps_it(self):
        other = Path(tempfile.mkdtemp(prefix="kit-"))
        self.addCleanup(shutil.rmtree, other, True)
        (other / "frame.json").write_text('{"width": 3840, "height": 2160, "fps": 60}')
        sdkkit.install(other)
        self.assertIn("3840", (other / "frame.json").read_text())


class Examples(unittest.TestCase):
    """The examples are shared text: they name no film, and each says what it shows."""

    def test_examples_name_no_project(self):
        for path in sdkkit.example_files():
            with self.subTest(example=path.name):
                self.assertEqual(leaks(path.read_text(encoding="utf-8")), [], path.name)

    def test_each_example_opens_with_what_it_shows(self):
        for path in sdkkit.example_files():
            with self.subTest(example=path.name):
                doc = sdkkit._docstring(path)
                self.assertTrue(doc.splitlines()[0].strip(), path.name)
                self.assertIn("placeholder", doc, "an example must say its values are not a look")


class ExamplesBuild(KitCase):
    def test_every_example_builds_deterministically_with_no_sdk_finding(self):
        model = self.dir / "props" / "m.gltf"
        model.parent.mkdir(parents=True)
        model.write_text(json.dumps({
            "scene": 0, "scenes": [{"nodes": [0]}],
            "nodes": [{"mesh": 0, "translation": [0, 0.25, 0]}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
            "accessors": [{"min": [-0.5, -0.25, -0.5], "max": [0.5, 0.25, 0.5]}]}))
        hdri = self.dir / "props" / "e.hdr"
        hdri.write_bytes(b"#?RADIANCE\n")
        structure = self.dir / "structure.xyz"
        structure.write_text("C 0 0 0\nO 1.5 0 0\nN 0 1.5 1.5\n")
        env = {"KIT_MODEL": "props/m.gltf", "KIT_HDRI": "props/e.hdr",
               "KIT_STRUCTURE": "structure.xyz"}
        for path in sdkkit.example_files():
            with self.subTest(example=path.name), mock.patch.dict(os.environ, env):
                program = self.program(path.read_text(encoding="utf-8"), path.name)
                result = sdkkit.build(program, validate=False)
                self.assertTrue(result["ok"], result.get("error"))
                self.assertTrue(result["deterministic"])
                for doc in result["documents"]:
                    self.assertEqual(doc["sdk"], [], doc["path"])

    def test_the_model_example_grounds_the_model_from_its_own_bounds(self):
        model = self.dir / "props" / "m.gltf"
        model.parent.mkdir(parents=True)
        # a 1 m cube whose lowest point is at y = 0 once its node is applied
        model.write_text(json.dumps({
            "scenes": [{"nodes": [0]}],
            "nodes": [{"mesh": 0, "translation": [0, 0.5, 0]}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
            "accessors": [{"min": [-0.5, -0.5, -0.5], "max": [0.5, 0.5, 0.5]}]}))
        (self.dir / "props" / "e.hdr").write_bytes(b"#?RADIANCE\n")
        src = next(p for p in sdkkit.example_files() if "model" in p.name)
        with mock.patch.dict(os.environ, {"KIT_MODEL": "props/m.gltf",
                                          "KIT_HDRI": "props/e.hdr"}):
            result = sdkkit.build(self.program(src.read_text(), src.name), validate=False)
        self.assertTrue(result["ok"], result.get("error"))
        doc = (self.dir / result["documents"][0]["path"]).read_text()
        self.assertIn('scaleX="1.2"', doc)       # 120 units from 1 m at 100 units per metre
        self.assertIn('y="0"', doc.split('id="hero-model"')[1].split(">")[0])


class Build(KitCase):
    def test_a_clean_program_builds_and_records_its_provenance(self):
        result = sdkkit.build(self.program(MINIMAL), validate=False)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(result["deterministic"])
        self.assertEqual([d["path"] for d in result["documents"]], ["minimal.scene.xml"])
        record = sdkkit.provenance_of(self.dir / "minimal.scene.xml")
        self.assertEqual(record["program"], "compose.py")
        self.assertEqual(record["runs"], 2)
        self.assertEqual(len(record["program_sha256"]), 64)

    def test_the_document_follows_frame_json(self):
        sdkkit.build(self.program(MINIMAL), validate=False)
        text = (self.dir / "minimal.scene.xml").read_text()
        self.assertIn('width="1280"', text)
        self.assertIn('fps="25"', text)

    def test_a_program_whose_output_changes_between_runs_is_refused(self):
        unseeded = MINIMAL.replace("duration=1)", "duration=1 + __import__('os').getpid() % 97)")
        result = sdkkit.build(self.program(unseeded), validate=False)
        self.assertFalse(result["ok"])
        self.assertFalse(result["deterministic"])
        self.assertIn("seed", result["error"])

    def test_a_program_that_saves_nothing_is_refused(self):
        result = sdkkit.build(self.program("print('hello')\n"), validate=False)
        self.assertFalse(result["ok"])
        self.assertIn("srkit.save", result["error"])

    def test_a_program_that_fails_reports_its_traceback(self):
        result = sdkkit.build(self.program("from srkit import S\nS.Scene().add_nothing()\n"),
                              validate=False)
        self.assertFalse(result["ok"])
        self.assertIn("add_nothing", result["error"])

    def test_a_wrong_value_is_refused_by_the_sdk_at_construction(self):
        bad = MINIMAL.replace('fps=str(FRAME["fps"])', 'fps=FRAME["fps"]')
        result = sdkkit.build(self.program(bad), validate=False)
        self.assertFalse(result["ok"])
        self.assertIn("SchemaValueError", result["error"])

    def test_provenance_lapses_when_the_document_is_edited_by_hand(self):
        sdkkit.build(self.program(MINIMAL), validate=False)
        doc = self.dir / "minimal.scene.xml"
        doc.write_text(doc.read_text().replace('duration="1"', 'duration="2"'))
        self.assertIsNone(sdkkit.provenance_of(doc))

    def test_an_unreachable_validator_is_a_note_not_an_error(self):
        with mock.patch("ytstudio.render.engine_validate",
                        return_value=["error: Connection timed out during banner exchange"]):
            result = sdkkit.build(self.program(MINIMAL))
        self.assertTrue(result["ok"], result)
        self.assertIsNone(result["documents"][0]["engine"])
        self.assertTrue(any("could not run" in f for f in result["documents"][0]["sdk"]))

    def test_engine_findings_join_the_sdks(self):
        with mock.patch("ytstudio.render.engine_validate",
                        return_value=["error[E1]: the engine refuses it"]):
            result = sdkkit.build(self.program(MINIMAL))
        self.assertFalse(result["ok"])
        self.assertEqual(result["documents"][0]["errors"], ["error[E1]: the engine refuses it"])


class Honesty(KitCase):
    """The program is the source only if it builds the scene: what it leaves
    on disk is what counts, and saving a document it read is refused."""

    def test_the_generated_helper_compiles(self):
        compile((self.dir / "srkit.py").read_text(), "srkit.py", "exec")

    def test_a_document_read_and_saved_again_is_refused(self):
        (self.dir / "hand.scene.xml").write_text(
            '<scene version="1.3"><project width="8" height="8" fps="30" duration="1"/>'
            '<composition/></scene>')
        result = sdkkit.build(self.program(
            "from srkit import S, save\nsave(S.read('hand.scene.xml'), 'out.scene.xml')\n"),
            validate=False)
        self.assertFalse(result["ok"])
        self.assertIn("rule:program-passthrough", result["error"])

    def test_reading_another_document_for_values_is_noted_not_refused(self):
        (self.dir / "look.scene.xml").write_text(
            '<scene version="1.3"><project width="8" height="8" fps="30" duration="9"/>'
            '<composition/></scene>')
        result = sdkkit.build(self.program(
            "from srkit import FRAME, S, save\nlook = S.read('look.scene.xml')\n"
            + MINIMAL.split("\n", 1)[1]), validate=False)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(any("rule:program-reads-document" in f
                            for f in result["documents"][0]["sdk"]))

    def test_a_document_changed_after_save_is_refused(self):
        changed = MINIMAL + "open('minimal.scene.xml', 'a').write('<!-- after -->')\n"
        result = sdkkit.build(self.program(changed), validate=False)
        self.assertFalse(result["ok"])
        self.assertIn("after saving it", result["error"])

    def test_a_document_removed_after_save_is_an_error_not_a_crash(self):
        removed = MINIMAL + "import os\nos.replace('minimal.scene.xml', 'moved.scene.xml')\n"
        program = self.program(removed)
        result = sdkkit.build(program, validate=False)
        self.assertIn("removed or renamed", result["error"])
        findings = sdkkit.build_findings(program, "minimal.scene.xml")
        self.assertTrue(findings[0].startswith("error: rule:program"))

    def test_an_authors_preview_frame_stays_out_of_the_proof_frames(self):
        doc = self.dir / "a.scene.xml"
        doc.write_text('<scene><project width="8" height="8" fps="30" duration="1"/></scene>')
        from tests.test_probes import png_bytes
        with mock.patch("ytstudio.render.engine_render_still", return_value=(png_bytes(), "ok")):
            path, _ = sdkkit.frame(doc, 0.5, host="h")
        self.assertEqual(path.parent, self.dir / "frames" / "kit")


class Rules(unittest.TestCase):
    LIT = ('<scene version="1.3"><project width="64" height="36" fps="30" duration="1"/>'
           '<symbols><symbol id="w" width="64" height="36"><object3D id="o" primitive="box"/>'
           '</symbol></symbols><lights>{lights}</lights></scene>')

    def test_a_world_whose_lights_cast_no_shadow_is_flagged(self):
        doc = self.LIT.format(lights='<light id="k" type="rect-area"/>').encode()
        self.assertTrue(any("rule:shadow" in f for f in sdkkit.rule_findings(doc)))

    def test_castshadow_one_is_true(self):
        doc = self.LIT.format(lights='<light id="k" type="spot" castShadow="1"/>').encode()
        self.assertEqual(sdkkit.rule_findings(doc), [])

    def test_one_casting_light_is_enough(self):
        doc = self.LIT.format(lights='<light id="k" type="rect-area" castShadow="true"/>'
                                     '<light id="f" type="point"/>').encode()
        self.assertEqual(sdkkit.rule_findings(doc), [])

    def test_a_dome_alone_or_a_flat_stage_is_not_flagged(self):
        self.assertEqual(sdkkit.rule_findings(
            self.LIT.format(lights='<light id="d" type="dome"/>').encode()), [])
        self.assertEqual(sdkkit.rule_findings(
            b'<scene><project/><lights><light id="k" type="spot"/></lights></scene>'), [])


class Frames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        hermetic.fake_remote_class(cls)

    def test_a_heavy_document_renders_where_an_encode_would(self):
        with mock.patch("ytstudio.render.remote_host", side_effect=lambda a, job: job) as rh:
            self.assertEqual(sdkkit.frame_host('<camera renderer="pathtrace"/>'), "encode")
            self.assertEqual(sdkkit.frame_host('<object3D><medium/></object3D>'), "encode")
            self.assertEqual(sdkkit.frame_host('<camera renderer="raster"/>'), "still")
        self.assertEqual(rh.call_count, 3)

    def test_every_rendered_frame_reports_its_cost_and_settings(self):
        d = Path(tempfile.mkdtemp(prefix="kit-"))
        self.addCleanup(shutil.rmtree, d, True)
        doc = d / "a.scene.xml"
        doc.write_text('<scene><project width="8" height="8" fps="30" duration="1"/>'
                       '<symbols><symbol id="w"><camera id="c" renderer="pathtrace" '
                       'pathSamples="256" maxBounces="12"/></symbol></symbols></scene>')
        from tests.test_probes import png_bytes
        with mock.patch("ytstudio.render.engine_render_still", return_value=(png_bytes(), "ok")):
            _, findings = sdkkit.frame(doc, 0.5, host="h7")
        note = next(f for f in findings if sdkkit.RENDER_NOTE in f)
        self.assertIn("on h7 (pathtrace, 256 samples, 12 bounces)", note)
        self.assertEqual(sdkkit.render_settings('<camera renderer="raster"/>'), "raster")

    def test_engine_md_says_what_a_frame_costs(self):
        text = sdkkit.engine_text({"manifest": MANIFEST}, [],
                                  [{"probe": "glass", "score": 6, "summary": "s",
                                    "render": "135 s on h (pathtrace, 256 samples, 12 bounces)"}])
        self.assertIn("## What a frame costs", text)
        self.assertIn(f"past {sdkkit.STILL_DEADLINE} s is abandoned", text)
        self.assertIn("[one frame: 135 s on h (pathtrace, 256 samples, 12 bounces)]", text)

    def test_a_host_that_stops_answering_hands_the_frame_to_another(self):
        d = Path(tempfile.mkdtemp(prefix="kit-"))
        self.addCleanup(shutil.rmtree, d, True)
        doc = d / "a.scene.xml"
        doc.write_text('<scene><project width="8" height="8" fps="30" duration="1"/></scene>')
        from tests.test_probes import png_bytes
        answers = iter([(None, "render failed\nerror: Connection to 10.0.0.13 port 22 timed out"),
                        (png_bytes(), "ok")])
        with mock.patch("ytstudio.render.engine_render_still", side_effect=lambda *a, **k: next(answers)), \
                mock.patch.object(sdkkit, "frame_host", return_value="h7"):
            path, findings = sdkkit.frame(doc, 0.5, host="h13")
        self.assertIsNotNone(path)
        self.assertTrue(any("on h7" in f for f in findings), findings)

    def test_a_still_dies_on_the_host_by_the_deadline_kept_here(self):
        from types import SimpleNamespace
        from ytstudio import render
        seen = {}

        def run(cmd, **kw):
            seen["script"] = cmd[-1]
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"{}")
        with mock.patch.dict(render._HOSTS, {"h": SimpleNamespace(engine="/opt/sr")}), \
                mock.patch.object(render.subprocess, "run", side_effect=run):
            render.engine_render_still(b"<scene/>", 1.0, host="h", timeout=600)
        self.assertIn("timeout -k 10 595 /opt/sr render", seen["script"])

    def test_renders_on_one_host_never_overlap(self):
        import threading
        import time as _time
        active, peak, lock = [0], [0], threading.Lock()

        def slow(*a, **k):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            _time.sleep(0.05)
            with lock:
                active[0] -= 1
            return None, "not rendered"
        d = Path(tempfile.mkdtemp(prefix="kit-"))
        self.addCleanup(shutil.rmtree, d, True)
        doc = d / "a.scene.xml"
        doc.write_text('<scene><project width="8" height="8" fps="30" duration="1"/></scene>')
        with mock.patch("ytstudio.render.engine_render_still", side_effect=slow):
            threads = [threading.Thread(target=sdkkit.frame, args=(doc, 0.5),
                                        kwargs={"host": "same-host"}) for _ in range(4)]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
        self.assertEqual(peak[0], 1)


class Renderer(unittest.TestCase):
    def test_the_notes_come_from_the_schemas_documentation(self):
        notes = sdkkit.renderer_notes()
        pairs = {(n["element"], n["attribute"]) for n in notes}
        self.assertIn(("material", "clearcoat"), pairs)
        self.assertIn(("camera", "renderer"), pairs)
        self.assertTrue(all(n["doc"] for n in notes))


if __name__ == "__main__":
    unittest.main()
