"""Every tool works on its own or inside the pipeline.

Inside the pipeline a stage finds its inputs in the project directory. On its
own it is handed files: an explicit `--project DIR` is authoritative even when
the directory has no idea yet, and an explicit input file anchors the run to
that file's directory without turning it into a project. The stages that need
upstream manifests fall back to what the document itself carries.
"""

import argparse
import tempfile
import unittest
from pathlib import Path

from ytstudio import pilot, qa, workspace

SCENE = """<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.1">
  <project width="1920" height="1080" fps="30" duration="20.5" background="#000000"/>
  <markers>
    <marker id="section-1" time="0" kind="section" label="Open"/>
    <marker id="section-2" time="10" kind="section" label="Close"/>
    <marker id="beat-1.1" time="0" duration="4" kind="beat" label="1.1"/>
    <marker id="beat-1.2" time="4" duration="6" kind="beat" label="1.2"/>
    <marker id="beat-2.1" time="10" duration="10" kind="beat" label="2.1"/>
  </markers>
  <composition>
    <layer id="shot-1.1" start="0" end="4" asset="a"/>
  </composition>
</scene>
"""


def _args(**kw):
    base = dict(project=None, title=None, index=None, source=None, stdin=None,
                genre=None, description=None, work=None)
    base.update(kw)
    return argparse.Namespace(**base)


class Resolve(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.work = self.root / "work"
        self.work.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_an_explicit_project_directory_is_enough(self):
        bare = self.root / "my-cut"
        bare.mkdir()
        (bare / "scene.xml").write_text(SCENE)
        idea, project = workspace.resolve(_args(project=str(bare), work=str(self.work)))
        self.assertEqual(project.root, bare)
        self.assertEqual(idea.title, "my cut")
        self.assertTrue((bare / "idea.json").exists())     # it is a project now
        self.assertFalse(project.standalone)

    def test_a_title_names_the_synthesised_idea(self):
        bare = self.root / "x"
        bare.mkdir()
        idea, _ = workspace.resolve(_args(project=str(bare), title="The Fall",
                                          work=str(self.work)))
        self.assertEqual(idea.title, "The Fall")

    def test_an_input_file_anchors_a_standalone_run(self):
        elsewhere = self.root / "renders"
        elsewhere.mkdir()
        scene = elsewhere / "droplet.scene.xml"
        scene.write_text(SCENE)
        idea, project = workspace.resolve(
            _args(work=str(self.work), scene=str(scene)), inputs=("scene",))
        self.assertEqual(project.root, elsewhere)
        self.assertTrue(project.standalone)
        self.assertEqual(idea.title, "droplet")
        self.assertFalse((elsewhere / "idea.json").exists())  # nothing planted
        self.assertEqual(workspace.projects(self.work), [])   # nothing created

    def test_without_project_or_inputs_the_work_root_still_rules(self):
        with self.assertRaises(SystemExit):
            workspace.resolve(_args(work=str(self.work)), inputs=("scene",))


class InputFlags(unittest.TestCase):
    def test_add_inputs_declares_override_flags_under_a_safe_dest(self):
        p = argparse.ArgumentParser()
        workspace.add_inputs(p, "script", "voice")
        args = p.parse_args(["--script", "s.json"])
        self.assertEqual(args.in_script, "s.json")
        self.assertIsNone(args.in_voice)
        with tempfile.TemporaryDirectory() as tmp:
            project = workspace.Project(Path(tmp))
            self.assertEqual(project.source("script", workspace.override(args, "script")),
                             "s.json")
            self.assertIsNone(project.source("voice", workspace.override(args, "voice")))


class FromTheDocument(unittest.TestCase):
    def test_qa_samples_from_the_scene_markers_when_there_is_no_manifest(self):
        rows = qa.shots_from_scene(SCENE.encode())
        self.assertEqual([r["label"] for r in rows], ["1.1", "1.2", "2.1"])
        self.assertEqual(rows[1]["section"], "Open")
        self.assertEqual(rows[2]["section"], "Close")
        samples = qa.sample({"shots": rows})
        self.assertTrue(samples)
        self.assertEqual(samples[0]["label"], "1.1")

    def test_qa_samples_the_runtime_evenly_when_there_are_no_markers(self):
        bare = SCENE.split("<markers>")[0] + "</scene>"
        rows = qa.shots_from_scene(bare.encode())
        self.assertEqual(len(rows), qa.EVEN_SHOTS)
        self.assertAlmostEqual(sum(r["seconds"] for r in rows), 20.5, places=2)

    def test_pilot_gathers_beats_from_the_scene_when_there_is_no_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scene.xml").write_text(SCENE)
            beats, build = pilot.gather(workspace.Project(root, standalone=True))
        self.assertEqual([b.label for b in beats], ["1.1", "1.2", "2.1"])
        self.assertIn("hook", beats[0].traits)
        self.assertIn("payoff", beats[-1].traits)
        self.assertEqual(beats[2].section, "Close")
        self.assertEqual(build["scene"]["background"], "#000000")


if __name__ == "__main__":
    unittest.main()
