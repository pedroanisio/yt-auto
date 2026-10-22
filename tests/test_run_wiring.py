"""`yt run` wires the new stages: props fetches before the animation is
authored, sound fetches its cues, and the env hints name the Freesound keys."""

import argparse
import unittest

from ytstudio import cli, env, workspace


class Pipeline(unittest.TestCase):
    def test_props_runs_after_the_script_and_before_the_animation(self):
        stages = [s for s, _ in workspace.STAGES]
        self.assertIn("props", stages)
        self.assertLess(stages.index("script"), stages.index("props"))
        self.assertLess(stages.index("props"), stages.index("vfx"))
        self.assertEqual(dict(workspace.STAGES)["props"], "props")

    def test_each_stage_gets_only_the_flags_it_accepts(self):
        base = ["--project", "work/x", "--model", "sonnet", "--quiet"]
        # props asks a model, so it takes the model flags; render calls none.
        argv = cli.stage_argv("props", base, {"props": []})
        self.assertEqual(argv, ["props", *base])
        argv = cli.stage_argv("render", base, {"render": []})
        self.assertEqual(argv, ["render", "--project", "work/x", "--quiet"])
        argv = cli.stage_argv("sound", base, {"sound": ["--fetch"]})
        self.assertEqual(argv, ["sound", *base, "--fetch"])

    def test_sound_fetches_and_synthesises_its_cues_in_both_modes(self):
        # A cue Freesound cannot fill is synthesised, so an unattended run
        # never builds against a sheet with holes (AUTO-75).
        for quick in (False, True):
            args = argparse.Namespace(quick=quick, minutes=1.0)
            extra = cli.run_extras(args, redo=set())
            self.assertEqual(extra["sound"][:2], ["--fetch", "--synth"], f"quick={quick}")
            self.assertEqual("--review" in extra["sound"], not quick)
            self.assertEqual(extra["props"], [])

    def test_every_pipeline_stage_parses_its_argv(self):
        parser = cli.build_parser()
        base = ["--project", "work/x", "--model", "sonnet", "--quiet"]
        extra = cli.run_extras(argparse.Namespace(quick=False, minutes=1.0), redo=set())
        for stage in cli.PIPELINE:
            parsed = parser.parse_args(cli.stage_argv(stage, base, extra))
            self.assertEqual(parsed.stage, stage)


class EnvHints(unittest.TestCase):
    def test_the_freesound_keys_are_named(self):
        self.assertIn("FREESOUND_CLIENT_ID", env.KNOWN)
        self.assertIn("FREESOUND_CLIENT_SECRET", env.KNOWN)
