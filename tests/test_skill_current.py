"""The operator's manual keeps up with the code: AUTO-59. Each test ties a
sentence of ytstudio/SKILL.md to the code it describes, so a change to either
shows here.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ytstudio import cli, render, sdkkit  # noqa: E402

TEXT = (ROOT / "ytstudio" / "SKILL.md").read_text(encoding="utf-8")
FLAT = " ".join(TEXT.split())


def row(prefix: str) -> str:
    return next(l for l in TEXT.splitlines() if l.startswith(prefix))


class Stale(unittest.TestCase):
    def test_the_grid_copies_and_cells_are_named(self):
        from ytstudio import framecheck, vfx
        self.assertIn("frames/grid/", row("| `yt vfx`"))
        self.assertEqual(vfx.GRID_DIR, "grid")
        self.assertEqual(len(framecheck.GRID_CELLS), 48)
        for needle in ("keyframe/grid/keyframe.png", "framecheck.GRID_CELLS",
                       "Workspace.cell_objects", "not evidence"):
            self.assertIn(needle, FLAT)

    def test_props_is_a_model_stage_with_its_two_decisions(self):
        self.assertEqual(cli.NO_MODEL, ("render",))
        line = row("| `yt props`")
        self.assertNotIn("| no (Poly Haven", line)
        self.assertIn("| yes", line)
        self.assertIn("phrases", line)
        self.assertIn("keep", line)

    def test_run_hands_props_the_model_flags(self):
        self.assertNotIn("takes only `--project` and `--quiet`", FLAT)
        self.assertIn("`yt render` is the only stage handed no model flags", FLAT)

    def test_the_improve_estimate_is_measured(self):
        self.assertNotIn("or $10 for a vfx pass", FLAT)
        # The pricing moved to spend so `yt run` reads it too (AUTO-81).
        self.assertIn("`spend.stage_estimates`", FLAT)


class Missing(unittest.TestCase):
    def test_sleep_inhibitor(self):
        self.assertIn("systemd-inhibit", FLAT)
        self.assertIn("`cli.LOCAL_ONLY`", FLAT)
        for stage in sorted(cli.LOCAL_ONLY):
            self.assertIn(f"`yt {stage}`", FLAT)

    def test_still_cache_and_job_statuses(self):
        for needle in (".cache/stills/", "$YT_STILL_CACHE", "render-jobs"):
            self.assertIn(needle, FLAT)
        for status in render.STATUSES:
            self.assertIn(status, FLAT)

    def test_detached_jobs_and_the_half_open_host(self):
        self.assertIn("survives a dropped ssh", FLAT)
        self.assertIn("accepts TCP on the ssh port but not SSH", FLAT)

    def test_research_scales_to_the_runtime(self):
        self.assertIn("under ten minutes", FLAT)
        self.assertIn("`--depth` or `--shots`", FLAT)
        self.assertIn("claim ceiling", FLAT)

    def test_sound_review_fixes_its_findings(self):
        self.assertIn("`yt sound --review --report-only`", FLAT)

    def test_kit_list(self):
        self.assertIn("`precheck`", FLAT)
        self.assertIn("kit/engine_facts.json", FLAT)
        self.assertIn(f"{sdkkit.STILL_BUDGET} s", FLAT)

    def test_the_lessons_log_and_its_promotion_are_documented(self):
        from ytstudio import vfx
        self.assertIn(f"work/{vfx.LESSONS_LOG}", FLAT)
        for needle in ("`yt vfx --promote-lesson", "`yt vfx --lessons`", "verified_on",
                       "never a score", "cannot hold two asks", "`note: lesson: not asked`",
                       "`yt keyframe`'s lead section"):
            self.assertIn(needle, FLAT)

    def test_delivery_reaches_the_voice(self):
        from ytstudio import voice
        para = next(l for l in TEXT.splitlines()
                    if l.startswith("**Delivery reaches the voice"))
        for needle in ("`voice.MARK_SUPPORT`", "`yt voice --draft`", "`--accept-draft`",
                       "`voice-draft.json`", "`script.prune_marks`", "`voice.caption_words`",
                       "`--max-chars`"):
            self.assertIn(needle, para)
        for engine in voice.MARK_SUPPORT:
            self.assertIn(engine, para)
        self.assertIn("voice-draft.json", row("| `yt voice`"))
        self.assertIn("pre-marks screenplay", para)

    def test_keyframe_approve_refuses_a_mixed_record(self):
        self.assertIn("refuses a record whose digests", FLAT)

    def test_qa_records_the_basis_and_blocks_a_scratch_take(self):
        # AUTO-152: the qa row says how each audio score is known and that a
        # scratch narration blocks READY; improve says where it routes one
        qa_row = " ".join(row("| `yt qa`").split())
        for needle in ("`audio_basis`", "`basis`", "heard", "read",
                       "`doctor.SCRATCH_ENGINES`", "scratch take"):
            self.assertIn(needle, qa_row)
        for needle in ("`doctor.real_engines`", "Needs a human decision"):
            self.assertIn(needle, FLAT)

    def test_qa_listen_checks_the_transcript(self):
        # AUTO-152 Part B: the qa row names the transcript, its check and bar
        from ytstudio import listen
        qa_row = " ".join(row("| `yt qa`").split())
        for needle in ("transcript-<audio sha256[:16]>", "`listen.TRANSCRIPT_SCHEMA`",
                       "`listen.transcript_check`", "`listen.normalise_words`", "`listen.wer`",
                       "never its words", "owned by voice", "`listen.transcript`"):
            self.assertIn(needle, qa_row)
        self.assertIn(f"`INTELLIGIBILITY_WER` ({listen.INTELLIGIBILITY_WER:g}, provisional",
                      qa_row)
        self.assertIn(f"`TRANSCRIPT_OUTPUT_TOKENS` {listen.TRANSCRIPT_OUTPUT_TOKENS}", qa_row)


if __name__ == "__main__":
    unittest.main()
