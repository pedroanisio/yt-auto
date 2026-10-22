"""`yt run` wires the new stages: props fetches before the animation is
authored, sound fetches its cues, and the env hints name the Freesound keys.
Its cost plan (`--dry-run`) and its cap (`--budget-usd`) read the stage
estimates, which live in `spend` (AUTO-81)."""

import argparse
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

# Imported under another name so a reader sees it is the base, not a case here
# (it has no tests of its own, so the loader collects nothing from it either way).
from test_run_pipeline import RunCase as _RunCase  # noqa: E402
from ytstudio import cli, env, improve, retime, spend, workspace  # noqa: E402

#: A fake pricing: one row per stage asked for, gate and qa appended as the
#: real table does, so the plan is seen to leave them out.
PRICES = {"research": 4.0, "styles": 5.0, "script": 3.0, "props": 0.0, "voice": 2.0,
          "retime": 0.5, "keyframe": 6.0, "vfx": 20.0, "sound": 0.5, "build": 0.1,
          "render": 0.0}


def fake_estimates(root, stages, sections=None):
    rows = [{"stage": s, "usd": PRICES.get(s, 0.1), "basis": "fake", "source": "test"}
            for s in stages]
    return rows + [{"stage": s, "usd": 9.0, "basis": "fake", "source": "test"}
                   for s in ("gate", "qa")]


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


class Estimates(unittest.TestCase):
    def test_the_stage_estimates_live_in_spend_and_improve_reads_them_there(self):
        # One pricing of a stage, for `yt improve` and `yt run` alike.
        for name in ("stage_estimates", "_reported", "_like", "total",
                     "MANIFEST", "DEFAULT_USD", "VFX_PASS_USD"):
            self.assertIs(getattr(improve, name), getattr(spend, name), name)
        self.assertEqual(spend.stage_estimates.__module__, "ytstudio.spend")


class Priced(_RunCase):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(spend, "stage_estimates", fake_estimates)
        patch.start()
        self.addCleanup(patch.stop)

    def rows(self) -> dict[str, str]:
        out = {}
        for line in self.out.splitlines():
            words = line.split()
            if len(words) >= 2 and words[1].startswith("$"):
                out[words[0]] = words[1]
        return out


class DryRun(Priced):
    def test_the_run_parser_takes_the_two_flags(self):
        args = cli.build_parser().parse_args(["run", "--dry-run", "--budget-usd", "12"])
        self.assertTrue(args.dry_run)
        self.assertEqual(args.budget_usd, 12.0)
        args = cli.build_parser().parse_args(["run"])
        self.assertFalse(args.dry_run)
        self.assertIsNone(args.budget_usd)

    def test_a_dry_run_prints_a_row_per_stage_and_the_total_and_runs_nothing(self):
        self.assertEqual(self.run_yt("--dry-run"), 0)
        self.assertEqual(self.order, [])
        rows = self.rows()
        # The walk stops before vfx while the look awaits the operator, and
        # sound runs beside the keyframe, so it is in the plan.
        planned = ["research", "styles", "script", "props", "voice", "retime", "keyframe",
                   "sound"]
        self.assertEqual(sorted(rows), sorted(planned + ["total"]), self.out)
        self.assertEqual(rows["keyframe"], "$6.00")
        self.assertEqual(rows["total"], f"${sum(PRICES[s] for s in planned):.2f}")
        self.assertIn("stops before vfx", self.out)
        self.assertNotIn("gate", self.out)

    def test_the_plan_is_what_the_walk_would_do(self):
        (self.root / "dossier.json").write_text("{}\n")
        (self.root / "sound.json").write_text("{}\n")
        self.assertEqual(self.run_yt("--dry-run", "--skip", "keyframe,render",
                                     "--redo", "research"), 0)
        rows = self.rows()
        self.assertEqual(sorted(rows), sorted(["research", "styles", "script", "props",
                                               "voice", "retime", "vfx", "build", "total"]))
        self.assertEqual(rows["total"], "$34.60")
        self.assertNotIn("stops before", self.out)
        self.assertEqual(self.order, [])


class Budget(Priced):
    def test_a_stage_whose_estimate_does_not_fit_is_refused_by_name(self):
        self.costs.update(research=3.5, styles=4.0)
        self.assertEqual(self.run_yt("--budget-usd", "10"), retime.UNSETTLED)
        self.assertEqual([s for s in self.order if s.endswith(":start")],
                         ["research:start", "styles:start"])
        self.assertIn("script", self.err)
        self.assertIn("$3.00", self.err)
        self.assertIn("$7.50", self.err)
        self.assertIn("$10.00", self.err)

    def test_the_remainder_goes_to_vfx_as_its_stage_budget(self):
        self.costs.update(research=1.0, styles=0.5)
        vfx_started = threading.Event()
        # Sound is still running beside when vfx starts, so it counts at its
        # estimate ($0.50) until it reports.
        self.hooks.update(vfx=vfx_started.set, sound=lambda: vfx_started.wait(timeout=5))
        self.assertEqual(self.run_yt("--budget-usd", "50", "--skip", "keyframe,render"), 0)
        self.assertAlmostEqual(self.seen["vfx"].stage_budget_usd, 50 - 1.5 - 0.5)

    def test_without_a_budget_nothing_is_priced_and_vfx_keeps_its_own_default(self):
        with mock.patch.object(spend, "stage_estimates",
                               side_effect=AssertionError("priced without a budget")):
            self.assertEqual(self.run_yt("--skip", "keyframe,render"), 0)
        self.assertIsNone(self.seen["vfx"].stage_budget_usd)

    def test_a_re_record_the_editor_asks_for_is_capped_like_any_stage(self):
        # retime reports $5.60 and sends a take back for a re-record; the
        # voice's $2.00 does not fit in $6, so it is refused by name before
        # a take is recorded again, not counted after.
        self.costs.update(retime=5.6)
        self.codes["retime"] = retime.UNSETTLED
        self.records["retime"] = {"decisions": [{"label": "T1", "action": "rerecord"}]}
        self.assertEqual(self.run_yt("--budget-usd", "6"), retime.UNSETTLED)
        self.assertEqual(self.started("voice"), ["voice:start", "voice:end"])
        self.assertIn("== voice: estimated at $2.00", self.err)
        self.assertIn("$5.60", self.err)
        self.assertNotIn("sound:start", self.order)
        self.assertNotIn("retime exited", self.err)

    def test_a_sound_stage_beside_is_refused_like_any_other(self):
        # Every estimate up to retime fits in $6; retime then reports $5.60,
        # and sound's $0.50 is the first that does not fit.
        self.costs.update(retime=5.6)
        self.assertEqual(self.run_yt("--budget-usd", "6"), retime.UNSETTLED)
        self.assertIn("$0.50", self.err)
        self.assertNotIn("sound:start", self.order)
        self.assertNotIn("keyframe:start", self.order)
        self.assertIn("sound", self.err)


class WalkOrder(_RunCase):
    """The plan and the cap price the stages in the order the walk reaches
    them, sound before the keyframe, with the real estimator: an assumed vfx
    folds in what follows it, so a stage priced after vfx when it runs before
    it would count at $0.00."""

    def test_the_walk_order_is_the_pipeline_with_sound_at_the_keyframe_less_the_skipped(self):
        order = cli.walk_order()
        self.assertEqual(sorted(order), sorted(cli.PIPELINE))
        self.assertEqual(order.index("sound") + 1, order.index("keyframe"))
        self.assertEqual([s for s in order if s != "sound"],
                         [s for s in cli.PIPELINE if s != "sound"])
        self.assertEqual(cli.walk_order({"keyframe", "render"}),
                         [s for s in order if s not in ("keyframe", "render")])

    def test_the_dry_run_and_the_cap_price_sound_alike_on_a_fresh_project(self):
        # Nothing measured, no like project: every stage is at its default,
        # and sound is $0.50 in the plan as in the cap.
        self.assertEqual(self.run_yt("--dry-run"), 0)
        rows = {}
        for line in self.out.splitlines():
            words = line.split()
            if len(words) >= 2 and words[1].startswith("$"):
                rows[words[0]] = float(words[1][1:])
        self.assertEqual(rows["sound"], 0.5, self.out)
        up_to_sound = sum(rows[s] for s in ("research", "styles", "script", "props", "voice",
                                            "retime", "sound"))
        self.assertEqual(up_to_sound, 13.0)
        self.assertEqual(rows["total"], up_to_sound + rows["keyframe"])
        # Each stage reports what it was assumed to cost, so after retime the
        # run has spent $12.50 and sound's $0.50 is the first that does not fit.
        self.costs.update({s: spend.DEFAULT_USD[s] for s in cli.PIPELINE})
        self.order.clear()
        self.assertEqual(self.run_yt("--budget-usd", f"{up_to_sound - 0.1:.2f}"),
                         retime.UNSETTLED)
        self.assertIn("== sound: estimated at $0.50", self.err)
        self.assertIn("$12.50", self.err)
        self.assertNotIn("sound:start", self.order)
        self.assertNotIn("keyframe:start", self.order)


class EnvHints(unittest.TestCase):
    def test_the_freesound_keys_are_named(self):
        self.assertIn("FREESOUND_CLIENT_ID", env.KNOWN)
        self.assertIn("FREESOUND_CLIENT_SECRET", env.KNOWN)
