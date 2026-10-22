"""`yt script --revise LABELS` rewrites only the lines it names (AUTO-95).

retime's `rewrite` verdict and a QA finding routed to the screenplay both
name shots, and the only way back used to be the whole screenplay again from
the plan. Direction 29 asks for the line that stumbles to be rewritten after
the read is measured, and judging 3 for a dip to send back the beat that dipped,
not the film. These tests drive `script.run` with `Screenwriter._ask` replaced
by a fake that answers by the schema it is given: no model is called.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import cli, script  # noqa: E402
from ytstudio.core import write_json  # noqa: E402

TITLE = "Why the bridge fell"
LONG = ("The crack that started in the steel under the paint grew a little every "
        "winter for thirty years while the trucks kept crossing and nobody who "
        "looked at the bridge could see it at all.")
LINES = [("One", [("1.1", "The bridge you cross every day fell in one night.", "hook"),
                  ("1.2", "Nobody saw the crack, and you would not have either.", "context"),
                  ("1.3", LONG, "setup")]),
         ("Two", [("2.1", "The paint hid the rust from every inspector who looked.", "reveal"),
                  ("2.2", "One bolt, painted over, held up four lanes.", "payoff")])]
PLANTED = "warning: rule:numbers: shot 1.2: planted, and never measured again"
FIXED = "The crack grew for years under the paint, out of sight."


def screenplay() -> dict:
    """A screenplay as `yt script` writes it, its rule findings measured
    from its own text, plus one planted on shot 1.2 that no rule would find
    there: it survives only if shot 1.2 is not measured again."""
    sections, n, at = [], 0, 0
    for heading, rows in LINES:
        shots = []
        for label, line, fn in rows:
            n += 1
            shots.append(script.Shot(visual="v", narration=line, n=n, label=label,
                                     seconds=5, at=at, function=fn, shows="s",
                                     source="[C1]"))
            at += 5
        sections.append(script.Section(heading=heading, seconds=15, shots=shots))
    plan = {"idea": {"title": TITLE}}
    flags = script.rule_findings(plan, sections)
    flags.append({"n": 2, "rule": "rule:numbers", "tier": "important",
                  "severity": "important", "fix": "-",
                  "problem": "rule:numbers: shot 1.2: planted, and never measured again"})
    return {"idea": {"title": TITLE, "genre": "g", "description": "d"},
            "target_seconds": at, "wpm": 150, "lang": "", "findings":
            script.rule_lines(flags[:-1]) + [PLANTED],
            "sections": [{"heading": s.heading, "purpose": "", "seconds": 15,
                          "beats": [], "shots": [script.asdict(x) for x in s.shots]}
                         for s in sections],
            "check": {}, "rules": {"flags": flags, "revisions": [], "kept": []},
            "polished": 0, "failed_sections": 0,
            "usage": {"cost_usd": 3.0, "model": "sonnet", "calls": 9}}


def shots_of(data: dict) -> dict[str, dict]:
    return {s["label"]: s for sec in data["sections"] for s in sec["shots"]}


class ReviseLabels(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name) / "work"
        self.work.mkdir()
        self.film = self.work / "the-bridge"
        self.film.mkdir()
        (self.film / "idea.json").write_text(json.dumps(
            {"title": TITLE, "genre": "g", "description": "d"}))
        write_json(self.film / "script.json", screenplay())
        self.before = (self.film / "script.json").read_bytes()
        self.calls: list[tuple[str, str, dict]] = []
        #: n -> the fix the fake hands back; a number not here is kept
        self.fixes = {3: FIXED}
        self.check_flags: list[dict] = []

    def fake(self, writer, name, prompt, schema):
        self.calls.append((name, prompt, schema))
        props = schema["properties"]
        if "fixes" in props:
            asked = [int(line.split(".", 1)[0]) for line in
                     prompt.split("The lines as written:", 1)[-1].split("\n\n", 2)[1].splitlines()
                     if line[:1].isdigit()]
            return {"fixes": [
                {"n": n, "action": "fix", "narration": self.fixes[n], "why": "one breath"}
                if n in self.fixes else
                {"n": n, "action": "keep", "narration": "", "why": "the inspector is the point"}
                for n in asked]}
        if "flags" in props:
            return {"flags": list(self.check_flags), "verdict": "defensible"}
        raise AssertionError(f"unexpected decision {name}")

    def run_stage(self, *flags):
        args = cli.build_parser().parse_args(
            ["script", "--project", str(self.film), "--work", str(self.work), "--quiet",
             *flags])
        test = self

        def ask(writer, name, prompt, schema):
            return test.fake(writer, name, prompt, schema)
        with mock.patch.object(script.Screenwriter, "_ask", ask), \
                contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = script.run(args)
        self.said = out.getvalue() + err.getvalue()
        return code

    def record(self) -> dict:
        return json.loads((self.film / "script.json").read_text())

    # -- named labels -------------------------------------------------------

    def test_one_revise_labels_decision_carries_only_the_named_shots(self):
        self.assertEqual(self.run_stage("--revise", "1.3,2.1"), 0, self.said)
        self.assertEqual([c[0] for c in self.calls], ["revise-labels"])
        prompt = self.calls[0][1]
        self.assertIn(LONG, prompt)
        self.assertIn("The paint hid the rust", prompt)
        for label, line, _ in LINES[0][1][:2] + LINES[1][1][1:]:
            self.assertNotIn(line, prompt, f"shot {label} was not named")

    def test_the_fix_is_applied_and_every_other_shot_is_unchanged(self):
        self.run_stage("--revise", "1.3,2.1", "--why", "it runs out of breath")
        old, new = shots_of(json.loads(self.before)), shots_of(self.record())
        self.assertEqual(new["1.3"]["narration"], FIXED)
        self.assertEqual(new["1.3"]["revised"], "one breath")
        for label in ("1.1", "1.2", "2.1", "2.2"):
            self.assertEqual(json.dumps(new[label]), json.dumps(old[label]), label)
        self.assertIn("it runs out of breath", self.calls[0][1])

    def test_only_the_changed_shots_are_measured_again(self):
        old = json.loads(self.before)["findings"]
        self.assertTrue(any(f.startswith("error: rule:sentence: shot 1.3") for f in old), old)
        self.assertEqual(self.run_stage("--revise", "1.3,2.1"), 0, self.said)
        findings = self.record()["findings"]
        self.assertFalse([f for f in findings if "rule:sentence: shot 1.3" in f], findings)
        self.assertIn(PLANTED, findings)               # 1.2 was not measured again
        self.assertTrue((self.film / "narration.txt").exists())
        self.assertIn(FIXED, (self.film / "narration.txt").read_text())

    def test_it_writes_the_four_files_and_keeps_the_backup(self):
        self.run_stage("--revise", "1.3")
        self.assertEqual((self.film / "script.json.bak").read_bytes(), self.before)
        for name in ("script.md", "narration.txt", "shotlist.csv"):
            self.assertIn(FIXED, (self.film / name).read_text(), name)
        revised = self.record()["revised"][-1]
        self.assertEqual(revised["from"], "labels")
        self.assertEqual([r["label"] for r in revised["applied"]], ["1.3"])

    def test_it_prints_what_changed_what_was_kept_and_the_voice_redo(self):
        self.run_stage("--revise", "1.3,2.1")
        self.assertIn("changed 1.3: one breath", self.said)
        self.assertIn("kept 2.1: the inspector is the point", self.said)
        self.assertIn("yt voice --redo 1.3", self.said)
        self.assertNotIn("--redo 1.3,2.1", self.said)        # a kept line keeps its take

    def test_the_record_bills_this_run_not_the_draft(self):
        self.run_stage("--revise", "1.3")
        self.assertNotEqual(self.record()["usage"].get("calls"), 9)

    def test_an_unknown_label_is_refused_and_nothing_is_written(self):
        self.assertEqual(self.run_stage("--revise", "1.3,9.9"), 2)
        self.assertIn("9.9", self.said)
        self.assertEqual(self.calls, [])
        self.assertEqual((self.film / "script.json").read_bytes(), self.before)

    # -- the fact-check -----------------------------------------------------

    def test_with_research_the_changed_lines_are_checked_again(self):
        write_json(self.film / "dossier.json", {"claims": [
            {"id": "C1", "claim": "the bolt was painted over", "confidence": "high"}]})
        self.check_flags = [{"n": 3, "problem": "years is not in the research",
                             "severity": "important", "fix": "say a long time"}]
        self.assertEqual(self.run_stage("--revise", "1.3,2.1"), 0, self.said)
        self.assertEqual([c[0] for c in self.calls], ["revise-labels", "verify-labels"])
        verify = self.calls[1][1]
        self.assertIn(FIXED, verify)
        self.assertNotIn("The paint hid the rust", verify)    # kept, so not changed
        check = self.record()["check"]
        self.assertEqual([f["n"] for f in check["flags"]], [3])
        self.assertIn("years is not in the research", self.said)

    def test_a_fact_check_that_fails_writes_nothing(self):
        write_json(self.film / "dossier.json", {"claims": [{"id": "C1", "claim": "c"}]})
        with mock.patch.object(script.Screenwriter, "verify", lambda *a, **k: {}):
            self.assertEqual(self.run_stage("--revise", "1.3"), 1)
        self.assertEqual((self.film / "script.json").read_bytes(), self.before)
        self.assertIn("fact-check", self.said)

    # -- from retime and from the feedback -----------------------------------

    def test_retime_takes_every_rewrite_label(self):
        write_json(self.film / "retime.json", {"decisions": [
            {"label": "1.3", "action": "rewrite", "note": "the line is twice its shot"},
            {"label": "1.1", "action": "accept", "note": ""},
            {"label": "2.1", "action": "rewrite", "note": "too short for the push-in"}],
            "changes": [{"label": "1.3", "from": 5, "to": 11, "measured": 10.6},
                        {"label": "2.1", "from": 5, "to": 3, "measured": 2.8}]})
        self.assertEqual(self.run_stage("--revise", "retime"), 0, self.said)
        self.assertEqual([c[0] for c in self.calls], ["revise-labels"])
        prompt = self.calls[0][1]
        self.assertIn("the line is twice its shot", prompt)
        self.assertIn("too short for the push-in", prompt)
        self.assertIn("10.6", prompt)
        self.assertNotIn(LINES[0][1][0][1], prompt)          # 1.1 was accepted
        self.assertEqual(self.record()["revised"][-1]["from"], "retime")

    def test_feedback_takes_the_shots_the_json_twin_names(self):
        fb = self.film / "feedback"
        fb.mkdir()
        write_json(fb / "script.json", {"stage": "script", "findings": [
            {"id": "F2", "shot": "2.1", "problem": "the line buries the rust",
             "fix": "lead with the rust", "severity": "important"}],
            "must_change": ["lead with the rust"], "check": "-"})
        # The prose names another shot; the twin is what is read.
        (fb / "script.md").write_text("- **F9** shot 1.1 [blocking]: something else\n")
        self.fixes = {4: "Rust, hidden by paint, from every inspector who looked."}
        # 1.3's error was not named, so it still stands and still blocks
        self.assertEqual(self.run_stage("--revise", "feedback"), 1, self.said)
        self.assertFalse((self.film / "narration.txt").exists())
        self.assertEqual([c[0] for c in self.calls], ["revise-labels"])
        prompt = self.calls[0][1]
        self.assertIn("the line buries the rust", prompt)
        self.assertNotIn(LINES[0][1][0][1], prompt)
        self.assertEqual(shots_of(self.record())["2.1"]["narration"],
                         "Rust, hidden by paint, from every inspector who looked.")
        self.assertIn("yt voice --redo 2.1", self.said)

    def test_feedback_without_its_json_twin_is_refused(self):
        (self.film / "feedback").mkdir()
        (self.film / "feedback" / "script.md").write_text("- shot 1.1: x\n")
        self.assertEqual(self.run_stage("--revise", "feedback"), 1)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
