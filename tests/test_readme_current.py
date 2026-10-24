"""README.md states what the code does (AUTO-60): the record shape, the sound
queries, the props decisions and the flags the stage sections list."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ytstudio import cli  # noqa: E402

TEXT = (ROOT / "README.md").read_text(encoding="utf-8")


def section(stage: str) -> str:
    m = re.search(rf"^### `yt {stage}`\n(.*?)(?=^#{{1,3}} )", TEXT, re.S | re.M)
    assert m, f"no section for yt {stage}"
    return m.group(1)


def flat(text: str) -> str:
    return " ".join(text.split())


class Stale(unittest.TestCase):
    def test_props_is_not_described_as_model_free(self):
        self.assertNotIn("no model calls, no key", TEXT)
        self.assertNotIn("decides nothing", TEXT)
        self.assertNotIn("No model calls; the stage takes", TEXT)

    def test_manifests_carry_usage_not_engine_cost(self):
        text = flat(TEXT)
        self.assertNotIn("Each manifest carries `\"engine\"`", text)
        self.assertIn("`usage`", text)
        self.assertIn("names the TTS engine in `voice.json` and the binary in `render.json`", text)

    def test_research_spells_the_budget_flag_once(self):
        self.assertNotIn("`--budget USD`", TEXT)
        self.assertIn("--budget-usd", section("research"))

    def test_freesound_order_never_searches_the_name(self):
        text = flat(section("sound"))
        self.assertNotIn("then its name", text)
        self.assertIn("`search_description`", text)
        self.assertIn("never the name", text)

    def test_improve_rescores_against_the_newer_of_video_and_scene(self):
        text = flat(section("improve"))
        self.assertNotIn("if it is older than the video)", text)
        self.assertIn("`scene.xml`", text)

    def test_doctor_probes_the_hosts(self):
        text = flat(section("doctor"))
        self.assertNotIn("no network", text)
        self.assertIn("ssh", text)
        self.assertIn("packages", text)


class Missing(unittest.TestCase):
    def test_build_lists_the_caption_and_normalise_flags(self):
        text = flat(section("build"))
        for flag in ("--burn-captions", "--normalize", "--normalize-reason"):
            self.assertIn(flag, text)
        self.assertIn("sidecar", text)

    def test_sound_review_revises_unless_report_only(self):
        text = flat(section("sound"))
        self.assertIn("--report-only", text)
        self.assertIn("revise", text)

    def test_qa_records_the_basis_and_blocks_a_scratch_take(self):
        # AUTO-152: how each audio score is known, and the scratch blocker
        text = flat(section("qa"))
        for needle in ("`audio_basis`", "`basis`", "not from the rendered track",
                       "scratch take", "`yt voice --engine auto`"):
            self.assertIn(needle, text)
        self.assertIn("`doctor.real_engines`", flat(section("improve")))

    def test_qa_listen_checks_the_transcript(self):
        # AUTO-152 Part B: the per-shot transcript under --listen
        from ytstudio import listen
        text = flat(section("qa"))
        for needle in ("transcript-<audio sha256[:16]>", "`listen.transcript_check`",
                       "`listen.normalise_words`", "`listen.wer`", "`INTELLIGIBILITY_WER`",
                       "`TRANSCRIPT_TIME_SLACK_S`", "`TRANSCRIPT_OUTPUT_TOKENS`",
                       "never the words", "owned by voice", "TO VERIFY"):
            self.assertIn(needle, text)
        self.assertIn(f"({listen.INTELLIGIBILITY_WER:g}, provisional", text)
        self.assertIn(f"({listen.TRANSCRIPT_TIME_SLACK_S:.1f})", text)
        self.assertIn(f"({listen.TRANSCRIPT_OUTPUT_TOKENS})", text)

    def test_research_asks_for_historical_parallels(self):
        # AUTO-151: one question at depth 4 or more, at most two kept rows,
        # each from a page opened; an old dossier gets them only afresh
        from ytstudio import research
        text = flat(section("research"))
        for needle in ("historical parallel", f"depth {research.PARALLEL_MIN_DEPTH}",
                       "`parallels`", "`parallels_why`", "where it breaks",
                       "never opened", "`warnings`", "`yt research --fresh`", "PL1",
                       "Stakes:"):
            self.assertIn(needle, text, needle)

    def test_research_cross_checks_key_claims(self):
        # AUTO-134: off by default, judged by another family, after the
        # outline; its price an assumption until a film measures it
        from ytstudio import spend
        text = flat(section("research"))
        for needle in ("`--crosscheck N`", "`--reviewer-model`", "`09-crosscheck`",
                       "after the outline", "`crosscheck`", "`independent`",
                       "`unverified`", "`crosscheck_skipped`", "TO VERIFY",
                       f"${spend.CROSSCHECK_CLAIM_USD:.2f}", "`yt run`", "--quick"):
            self.assertIn(needle, text, needle)

    def test_voice_retakes_and_redo(self):
        text = flat(section("voice"))
        self.assertIn("speed", text)
        self.assertIn("only the named", text)

    def test_voice_marks_the_draft_and_what_an_old_project_is_asked(self):
        from ytstudio import voice
        text = flat(section("voice"))
        for needle in ("`voice.MARK_SUPPORT`", "`yt voice --draft`", "`--accept-draft`",
                       "`voice-draft.json`", "`spoken`", "`caption_mismatch`",
                       "`delivery.unmatched`"):
            self.assertIn(needle, text)
        for engine in voice.MARK_SUPPORT:
            self.assertIn(engine, text)
        # a pre-marks screenplay keeps its cast; a marked one is cast again,
        # and the operator is told how to hold the bill
        self.assertIn("asked the casting and retake questions as before", text)
        for needle in ("`--voice`", "`--max-chars`", "`--dry-run` does not cast"):
            self.assertIn(needle, text)
        self.assertIn("the first `yt script` after the marks", flat(TEXT))

    def test_render_names_the_still_cache_and_statuses(self):
        text = flat(section("render"))
        self.assertIn("still", text)
        self.assertIn("cache", text)
        for status in ("rendered", "failed", "timeout", "blocked-inputs"):
            self.assertIn(status, text)


class Flags(unittest.TestCase):
    def test_every_flag_a_stage_section_lists_is_on_its_parser(self):
        stages = {}
        for action in cli.build_parser()._actions:
            if getattr(action, "choices", None) and action.dest == "stage":
                stages = dict(action.choices)
        checked = 0
        for stage, parser in stages.items():
            m = re.search(rf"^### `yt {stage}`\n(.*?)(?=^#{{1,3}} )", TEXT, re.S | re.M)
            if not m:
                continue
            known = {o for a in parser._actions for o in a.option_strings}
            for para in m.group(1).split("\n\n"):
                if not re.match(r"\s*(`--|· `--)", para):
                    continue
                for flag in re.findall(r"--[a-z][a-z-]*", para):
                    checked += 1
                    self.assertIn(flag, known, f"README lists {flag} under `yt {stage}`")
        self.assertGreater(checked, 40)


if __name__ == "__main__":
    unittest.main()
