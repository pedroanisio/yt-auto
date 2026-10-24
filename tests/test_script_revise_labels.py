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


#: Where a revise prompt lists the lines it sends back, one `n. reason` each.
SENT = ("each with the reason:\n\n", "flagged by the fact-check:\n\n")


def sent_back(prompt: str) -> list[int]:
    """The shot numbers a revise prompt asks about, read from its list of
    reasons (the listing above it may show every shot, to read them in)."""
    for marker in SENT:
        if marker in prompt:
            block = prompt.split(marker, 1)[1].split("\n\n", 1)[0]
            return [int(line.split(".", 1)[0]) for line in block.splitlines()
                    if line[:1].isdigit()]
    raise AssertionError("the revise prompt lists no lines")


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
        #: n -> the on-screen strings the fake hands back with its fix
        self.supers: dict[int, list[str]] = {}
        #: n -> the fact-check revision's fix (revise-labels-facts-N)
        self.fact_fixes: dict[int, str] = {}
        #: what each fact-check answers, in order; [] once they run out
        self.checks: list[list[dict]] = []
        #: numbers the fake answers for although nobody asked
        self.rogue: dict[int, str] = {}
        self.down = False

    def fake(self, writer, name, prompt, schema):
        self.calls.append((name, prompt, schema))
        props = schema["properties"]
        if "fixes" in props:
            if self.down:
                raise script.ClaudeError("the model is down")
            fixes = self.fixes if name == "revise-labels" else self.fact_fixes
            out = []
            for n in sent_back(prompt):
                if n not in fixes and n not in self.supers:
                    out.append({"n": n, "action": "keep", "narration": "",
                                "why": "the inspector is the point"})
                    continue
                row = {"n": n, "action": "fix", "narration": fixes.get(n, ""),
                       "why": "one breath"}
                if n in self.supers:
                    row["on_screen_text"] = self.supers[n]
                out.append(row)
            out += [{"n": n, "action": "fix", "narration": line, "why": "unasked"}
                    for n, line in self.rogue.items()]
            return {"fixes": out}
        if "flags" in props:
            return {"flags": self.checks.pop(0) if self.checks else [],
                    "verdict": "defensible"}
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

    def test_one_revise_labels_decision_sends_back_only_the_named_shots(self):
        self.rogue = {1: "A line nobody asked for."}
        self.assertEqual(self.run_stage("--revise", "1.3,2.1"), 0, self.said)
        self.assertEqual([c[0] for c in self.calls], ["revise-labels"])
        prompt = self.calls[0][1]
        self.assertEqual(sent_back(prompt), [3, 4])
        # Every line is there to read the named ones in, so a rewrite keeps
        # the pronoun or connective that ties it to its neighbour.
        for label, line, _ in LINES[0][1] + LINES[1][1]:
            self.assertIn(line, prompt, label)
        self.assertEqual(shots_of(self.record())["1.1"]["narration"], LINES[0][1][0][1])

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

    def research(self):
        write_json(self.film / "dossier.json", {"claims": [
            {"id": "C1", "claim": "the bolt was painted over", "confidence": "high"}]})

    YEARS = {"n": 3, "problem": "years is not in the research", "severity": "important",
             "fix": "say a long time"}

    def test_with_research_the_changed_lines_are_checked_again(self):
        self.research()
        self.assertEqual(self.run_stage("--revise", "1.3,2.1"), 0, self.said)
        self.assertEqual([c[0] for c in self.calls], ["revise-labels", "verify-labels"])
        verify = self.calls[1][1]
        self.assertIn(FIXED, verify)
        self.assertNotIn("The paint hid the rust", verify)    # kept, so not changed

    def test_a_fact_flag_on_a_changed_line_is_revised_and_checked_again(self):
        # As the stage's own fact loop does: the flagged line goes to the
        # fact revision, and the line it writes is checked once more.
        self.research()
        self.checks = [[self.YEARS], []]
        self.fact_fixes = {3: "The crack grew under the paint, out of sight."}
        self.assertEqual(self.run_stage("--revise", "1.3,2.1"), 0, self.said)
        self.assertEqual([c[0] for c in self.calls], [
            "revise-labels", "verify-labels", "revise-labels-facts-1", "verify-labels-1"])
        self.assertEqual(sent_back(self.calls[2][1]), [3])
        self.assertEqual(shots_of(self.record())["1.3"]["narration"],
                         "The crack grew under the paint, out of sight.")
        check = self.record()["check"]
        self.assertEqual(check["flags"], [])
        self.assertEqual(check["unresolved"], [])
        self.assertIn("yt voice --redo 1.3", self.said)

    def test_a_fact_flag_the_revision_keeps_stands_with_its_reason(self):
        self.research()
        self.checks = [[self.YEARS]]
        self.assertEqual(self.run_stage("--revise", "1.3,2.1"), 0, self.said)
        check = self.record()["check"]
        self.assertEqual([f["n"] for f in check["flags"]], [3])
        self.assertEqual(check["flags"][0]["kept"], "the inspector is the point")
        self.assertIn("years is not in the research", self.said)

    def test_the_check_forgets_why_an_old_line_was_kept(self):
        data = screenplay()
        data["check"] = {"flags": [{"n": 3, "problem": "old", "severity": "important",
                                    "kept": "the old line hedged"}],
                         "kept": [{"n": 3, "why": "the old line hedged"},
                                  {"n": 5, "why": "the bolt is in C1"}]}
        write_json(self.film / "script.json", data)
        self.research()
        self.assertEqual(self.run_stage("--revise", "1.3,2.1"), 0, self.said)
        self.assertEqual(self.record()["check"]["kept"], [{"n": 5, "why": "the bolt is in C1"}])

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
        self.assertEqual(sent_back(prompt), [3, 4])          # 1.1 was accepted
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
        self.assertEqual(sent_back(prompt), [4])
        self.assertEqual(shots_of(self.record())["2.1"]["narration"],
                         "Rust, hidden by paint, from every inspector who looked.")
        self.assertIn("yt voice --redo 2.1", self.said)

    def test_a_finding_about_the_super_changes_the_super(self):
        # QA's script owner answers for the supers too: a finding pinned to a
        # shot's on-screen text is something this revision can fix.
        data = screenplay()
        data["sections"][1]["shots"][0]["on_screen_text"] = ["4 lanes"]
        write_json(self.film / "script.json", data)
        fb = self.film / "feedback"
        fb.mkdir()
        write_json(fb / "script.json", {"stage": "script", "findings": [
            {"id": "F1", "shot": "1.3", "problem": "runs out of breath", "fix": "cut it"},
            {"id": "F2", "shot": "2.1", "problem": "the super shows the wrong figure",
             "fix": "show the one bolt"}], "must_change": [], "check": "-"})
        self.supers = {4: ["1 bolt"]}
        self.assertEqual(self.run_stage("--revise", "feedback"), 0, self.said)
        prompt, schema = self.calls[0][1], self.calls[0][2]
        self.assertIn("[on screen: 4 lanes]", prompt)
        self.assertIn("on_screen_text",
                      schema["properties"]["fixes"]["items"]["properties"])
        shot = shots_of(self.record())["2.1"]
        self.assertEqual(shot["on_screen_text"], ["1 bolt"])
        self.assertEqual(shot["narration"], LINES[1][1][0][1])
        self.assertIn("yt voice --redo 1.3 ", self.said)     # the take of 2.1 still holds

    def test_a_mark_the_rewrite_dropped_leaves_script_json_too(self):
        # AUTO-139: a mark on a word the new line no longer says goes with
        # it in the record, not only in memory; a changed shot that had no
        # marks gains no mark keys.
        data = screenplay()
        shots = shots_of(data)
        shots["1.3"] |= {"emphasis": ["paint"], "pauses": ["thirty"], "pronounce": []}
        for key in ("emphasis", "pauses", "pronounce"):
            shots["2.1"].pop(key, None)
        write_json(self.film / "script.json", data)
        self.fixes = {3: FIXED, 4: "The paint hid the rust from the inspectors."}
        self.assertEqual(self.run_stage("--revise", "1.3,2.1"), 0, self.said)
        new = shots_of(self.record())
        self.assertEqual(new["1.3"]["narration"], FIXED)
        self.assertEqual(new["1.3"]["emphasis"], ["paint"])
        self.assertEqual(new["1.3"]["pauses"], [])
        self.assertEqual(new["2.1"]["narration"], self.fixes[4])
        for key in ("emphasis", "pauses", "pronounce"):
            self.assertNotIn(key, new["2.1"], key)
        applied = {r["label"]: r for r in self.record()["revised"][-1]["applied"]}
        self.assertEqual(applied["1.3"]["marks_dropped"], [{"kind": "pause", "word": "thirty"}])

    def test_a_changed_shots_picture_flags_stop_binding(self):
        # The picture-check's standing flags (AUTO-98) speak of the text the
        # check read; once that shot's line or supers change they are stale,
        # kept under `picture.stale` and out of `picture.flags`, which vfx
        # binds. An unchanged shot's flag stands as it was recorded.
        data = screenplay()
        old = {"n": 3, "section": 1, "kind": "picture:bare-number", "severity": "important",
               "problem": "picture:bare-number: shot 1.3: thirty years, bare", "fix": "f"}
        other = {"n": 5, "section": 2, "kind": "picture:unshown-claim",
                 "severity": "important",
                 "problem": "picture:unshown-claim: shot 2.2: the bolt never holds", "fix": "f"}
        data["picture"] = {"model": "opus", "sections": [], "flags": [old, other],
                           "revisions": [], "kept": []}
        data["findings"] += [f"warning: {old['problem']}; still standing after the revision",
                             f"warning: {other['problem']}; still standing after the revision"]
        write_json(self.film / "script.json", data)
        self.assertEqual(self.run_stage("--revise", "1.3"), 0, self.said)
        record = self.record()
        self.assertEqual([f["n"] for f in record["picture"]["flags"]], [5])
        self.assertEqual([f["n"] for f in record["picture"]["stale"]], [3])
        self.assertFalse([f for f in record["findings"] if "thirty years, bare" in f],
                         record["findings"])
        self.assertTrue([f for f in record["findings"] if "the bolt never holds" in f])

    def test_a_fix_that_returns_the_line_unchanged_is_kept_as_written(self):
        self.fixes = {4: LINES[1][1][0][1]}
        self.research()
        self.assertEqual(self.run_stage("--revise", "2.1", "--report-only"), 0, self.said)
        self.assertEqual([c[0] for c in self.calls], ["revise-labels"])
        self.assertIn("kept 2.1: the revision left it as written", self.said)
        self.assertNotIn("yt voice --redo", self.said)
        self.assertEqual(json.dumps(shots_of(self.record())["2.1"]),
                         json.dumps(shots_of(json.loads(self.before))["2.1"]))

    def test_a_revision_that_fails_writes_nothing(self):
        self.down = True
        self.assertEqual(self.run_stage("--revise", "1.3"), 1)
        self.assertIn("nothing was written", self.said)
        self.assertEqual((self.film / "script.json").read_bytes(), self.before)

    def test_a_finding_with_no_fix_is_not_handed_another_shots_change(self):
        fb = self.film / "feedback"
        fb.mkdir()
        write_json(fb / "script.json", {"stage": "script", "findings": [
            {"id": "F2", "shot": "2.1", "problem": "the line buries the rust"}],
            "must_change": ["cut the bridge's age from 1.1"], "check": "-"})
        self.run_stage("--revise", "feedback")
        prompt = self.calls[0][1]
        self.assertNotIn("cut the bridge's age", prompt)
        self.assertIn("the change the reason asks for", prompt)

    def test_feedback_without_its_json_twin_is_refused(self):
        (self.film / "feedback").mkdir()
        (self.film / "feedback" / "script.md").write_text("- shot 1.1: x\n")
        self.assertEqual(self.run_stage("--revise", "feedback"), 1)
        self.assertEqual(self.calls, [])



# --------------------------------------------------------------------------- #
# AUTO-132: a screenplay with statuses, revised by label
# --------------------------------------------------------------------------- #

#: c1 a settled finding; c2 a claim the research rates low.
STATUS_DOSSIER = {"claims": [
    {"claim": "the bolt was painted over", "detail": "", "source_title": "NTSB",
     "source_url": "u", "source_kind": "primary", "confidence": "high",
     "standing": "established"},
    {"claim": "the crack grew for decades", "detail": "", "source_title": "NTSB",
     "source_url": "u", "source_kind": "secondary", "confidence": "low",
     "standing": "established"}]}

#: A status blocker the other-family reviewer wrote on shot 1.3 under
#: `yt script --verify --review`.
BLOCKER = {"n": 3, "severity": "blocker", "check": "status",
           "problem": "presented as proven on a claim the research rates low",
           "fix": "say it is a guess"}


def status_screenplay() -> dict:
    """The screenplay above as written with claim ids: shot 1.3 says it is
    established on c2 and stores the ceiling established, which the dossier
    does not allow; the other shots cite c1."""
    data = screenplay()
    for sec in data["sections"]:
        for shot in sec["shots"]:
            shot.update(claim="c1", status="established", ceiling="established",
                        source="NTSB")
    third = data["sections"][0]["shots"][2]
    third.update(claim="c2")
    data["check"] = {"flags": [dict(BLOCKER)], "verdict": "defensible",
                     "status": {"name": "verify-status", "model": "gpt-5",
                                "verdict": "shot 3 overclaims"},
                     "unresolved": [dict(BLOCKER)], "revisions": [], "kept": []}
    return data


class ReviseStatus(unittest.TestCase):
    """`yt script --revise` on a screenplay with statuses: the ceiling is the
    dossier's on load, and a status blocker on a changed line is never
    dropped by a revise that brought no reviewer to read it again."""

    fake, run_stage, record = ReviseLabels.fake, ReviseLabels.run_stage, ReviseLabels.record

    def setUp(self):
        ReviseLabels.setUp(self)
        write_json(self.film / "script.json", status_screenplay())
        write_json(self.film / "dossier.json", STATUS_DOSSIER)
        self.before = (self.film / "script.json").read_bytes()

    def test_the_revise_reads_the_claim_ids_and_the_capped_status(self):
        self.assertEqual(self.run_stage("--revise", "1.3"), 0, self.said)
        prompt = self.calls[0][1]
        self.assertEqual(self.calls[0][0], "revise-labels")
        self.assertIn("- c1: the bolt was painted over", prompt)
        # the stored ceiling is not trusted: c2 is low, so speculation
        self.assertIn("[status: speculation, claim c2]", prompt)
        record = self.record()
        self.assertEqual(shots_of(record)["1.3"]["ceiling"], "speculation")
        self.assertTrue(any("rule:unframed-status: shot 1.3" in f
                            for f in record["findings"]), record["findings"])

    def test_a_status_blocker_on_a_changed_line_still_binds(self):
        self.assertEqual(self.run_stage("--revise", "1.3"), 0, self.said)
        self.assertEqual([c[0] for c in self.calls], ["revise-labels", "verify-labels"])
        check = self.record()["check"]
        self.assertIn(BLOCKER, [{k: f[k] for k in BLOCKER} for f in check["flags"]])
        self.assertIn(3, [f["n"] for f in check["unresolved"]])
        # the reading the reviewer made stands; the revise's is beside it
        self.assertEqual(check["status"]["verdict"], "shot 3 overclaims")
        self.assertEqual(check["status"]["model"], "gpt-5")
        self.assertEqual(check["status"]["recheck_labels"]["not_evidenced"],
                         script.STATUS_UNREAD)
        self.assertIn("not_evidenced: verify-status-labels "
                      f"({script.STATUS_UNREAD})", self.record()["findings"])

    def test_the_doctor_still_fails_the_blocker(self):
        from ytstudio import doctor, workspace
        self.assertEqual(self.run_stage("--revise", "1.3"), 0, self.said)
        report = doctor.check_project(workspace.Project(self.film))
        failed = [f.message for f in report.findings
                  if f.level == "fail" and f.area == "fact-check"]
        self.assertTrue(any("shot 3" in m and "proven" in m for m in failed), failed)


class ReviseStatusNoDossier(unittest.TestCase):
    """`yt script --revise` on a screenplay with statuses whose dossier is
    gone: nothing can cap it again, so the ceiling script.json stored is
    kept for reading it (it only ever lowers a line), and the run says so."""

    fake, run_stage, record = ReviseLabels.fake, ReviseLabels.run_stage, ReviseLabels.record

    def setUp(self):
        ReviseLabels.setUp(self)
        data = status_screenplay()
        data["check"] = {}
        third = data["sections"][0]["shots"][2]
        third.update(claim="c4", status="established", ceiling="hypothesis")
        data["cited_claims"] = {"c1": STATUS_DOSSIER["claims"][0] | {"standing":
                                                                     "established"},
                                "c4": {"standing": "limitation", "confidence": "high",
                                       "caveat": "", "source_kind": "primary"}}
        write_json(self.film / "script.json", data)

    def test_the_status_line_is_never_stronger_than_the_stored_ceiling(self):
        self.assertEqual(self.run_stage("--revise", "1.3"), 0, self.said)
        md = (self.film / "script.md").read_text()
        self.assertIn("**STATUS** — hypothesis (c4, limitation), capped from established",
                      md)
        self.assertNotIn("**STATUS** — established (c4", md)
        self.assertEqual(shots_of(self.record())["1.3"]["ceiling"], "hypothesis")
        self.assertIn("no dossier", self.said)


class ReviseOlderScreenplay(unittest.TestCase):
    fake, run_stage, record = ReviseLabels.fake, ReviseLabels.run_stage, ReviseLabels.record
    setUp = ReviseLabels.setUp

    def test_an_older_screenplay_with_a_dossier_asks_what_it_always_did(self):
        write_json(self.film / "dossier.json", STATUS_DOSSIER)
        self.assertEqual(self.run_stage("--revise", "1.3"), 0, self.said)
        prompt = self.calls[0][1]
        self.assertNotIn("c1: ", prompt)
        self.assertNotIn("status", prompt)
        self.assertNotIn("not_evidenced: verify-status", " ".join(self.record()["findings"]))


if __name__ == "__main__":
    unittest.main()
