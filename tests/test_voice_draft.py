"""AUTO-139 part C: rule 29's read-aloud take, `yt voice --draft`.

A free local engine reads the screenplay sentence by sentence before any
voice is paid for; voice-draft.json keeps the measured seconds and the
script's hash, and a paid run after a current draft with a fail stops
unless --accept-draft. Nothing here calls a model or an engine: the
synthesiser is a fake that writes a WAV, the director a fake that is
asked only where the stage would ask it.
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import test_wordless
from tests.test_voice_retakes import FakeKit
from ytstudio import harness, voice
from ytstudio.script import Section, Shot, load_script


def row(label, text, seconds, section="One"):
    return {"label": label, "section": section, "text": text, "seconds": seconds,
            "words": len(text.split()), "wpm": 0.0}


class DraftFindings(unittest.TestCase):
    def fails(self, findings):
        return [f for f in findings if f["severity"] == "fail"]

    def test_a_sentence_over_ten_seconds_fails_and_one_under_passes(self):
        found = voice.draft_findings([row("1.1", "A long sentence.", 10.5)])
        self.assertEqual(len(self.fails(found)), 1)
        fail = self.fails(found)[0]
        self.assertEqual(fail["label"], "1.1")
        self.assertIn("29", fail["rule"])
        self.assertEqual(fail["strength"], "practitioner")
        self.assertEqual(self.fails(voice.draft_findings([row("1.1", "Short.", 9.5)])), [])

    def test_a_numeral_said_again_in_a_later_sentence_warns(self):
        found = voice.draft_findings([row("1.1", "It took 4,540 years.", 3),
                                      row("1.2", "Then 4,540 more, and 12 after.", 3)])
        warns = [f for f in found if f["severity"] == "warn"]
        self.assertEqual(len(warns), 1)
        self.assertEqual(warns[0]["label"], "1.2")
        self.assertIn("4,540", warns[0]["detail"])
        self.assertEqual(self.fails(found), [])

    def test_a_numeral_twice_in_one_sentence_is_not_a_repeat(self):
        found = voice.draft_findings([row("1.1", "Count 4 and 4 again.", 3)])
        self.assertEqual([f for f in found if f["severity"] == "warn"], [])

    def test_per_section_drift_is_info(self):
        found = voice.draft_findings(
            [row("1.1", "Short.", 2)],
            by_section={"One": {"spoken": 7.0, "planned": 6.0, "drift": 1.0}})
        info = [f for f in found if f["severity"] == "info"]
        self.assertEqual(len(info), 1)
        self.assertEqual((info[0]["section"], info[0]["drift"]), ("One", 1.0))


def film(narration="Line number 1. And a second one.", **marks):
    shot = Shot(narration=narration, n=1, label="1.1", seconds=4)
    for key, value in marks.items():
        setattr(shot, key, value)
    quiet = Shot(narration="", n=2, label="1.2", seconds=2)
    return [Section(heading="One", shots=[shot, quiet])]


class ScriptSha(unittest.TestCase):
    def test_the_same_script_is_the_same_sha(self):
        self.assertEqual(voice.script_sha(film()), voice.script_sha(film()))

    def test_the_words_and_each_mark_change_it(self):
        base = voice.script_sha(film())
        self.assertNotEqual(base, voice.script_sha(film("Line number 2.")))
        self.assertNotEqual(base, voice.script_sha(film(emphasis=["second"])))
        self.assertNotEqual(base, voice.script_sha(film(pauses=["second"])))
        self.assertNotEqual(base, voice.script_sha(
            film(pronounce=[{"term": "Line", "say": "LYNE"}])))

    def test_the_film_order_counts(self):
        first = Shot(narration="One line.", n=1, label="1.1", seconds=2)
        second = Shot(narration="Another line.", n=2, label="1.2", seconds=2)
        self.assertNotEqual(
            voice.script_sha([Section(heading="One", shots=[first, second])]),
            voice.script_sha([Section(heading="One", shots=[second, first])]))

    def test_a_shot_with_no_words_is_not_read_and_does_not_count(self):
        secs = film()
        secs[0].shots[1].visual = "something else entirely"
        secs[0].shots[1].seconds = 9
        self.assertEqual(voice.script_sha(secs), voice.script_sha(film()))


class Refuses(unittest.TestCase):
    """Neither a model nor a paid engine is ever reached from --draft."""

    def setUp(self):
        saved = harness.ENGINE
        self.addCleanup(setattr, harness, "ENGINE", saved)
        harness.ENGINE = NoModel
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        write_project(self.root)

    def run_draft(self, engine, available=()):
        args = test_wordless.WordlessRun.args(self)
        args.engine, args.draft = engine, True
        err = io.StringIO()
        with mock.patch.object(voice.Engine, "available",
                               lambda self: (self.name in available, "hint")), \
                mock.patch.object(voice, "_kit", NoKit), contextlib.redirect_stderr(err):
            code = voice.run(args)
        return code, err.getvalue()

    def test_stub_is_refused(self):
        code, err = self.run_draft("stub", available=("stub",))
        self.assertEqual(code, 1)
        self.assertIn("silence is not a read", err)

    def test_elevenlabs_is_refused(self):
        code, err = self.run_draft("elevenlabs", available=("elevenlabs",))
        self.assertEqual(code, 1)
        self.assertIn("--draft never pays", err)

    def test_neither_local_reader_available_exits_naming_both(self):
        code, err = self.run_draft("auto", available=("stub", "elevenlabs"))
        self.assertEqual(code, 1)
        self.assertIn("--draft needs kokoro or espeak-ng; neither is available", err)
        self.assertFalse((self.root / "voice-draft.json").exists())


class NoModel:
    def __init__(self, config):
        raise AssertionError("--draft asked a model")


def NoKit():
    raise AssertionError("--draft reached the paid engine")


def write_project(root, shots=None):
    shots = shots or [
        {"n": 1, "label": "1.1", "visual": "v", "seconds": 4,
         "narration": "Ask Schrödinger once. Then 12 more times."},
        {"n": 2, "label": "1.2", "visual": "v", "seconds": 3, "narration": ""},
        {"n": 3, "label": "2.1", "visual": "v", "seconds": 3, "narration": "Line three."}]
    script = {"sections": [{"heading": "One", "shots": shots[:2]},
                           {"heading": "Two", "shots": shots[2:]}]}
    (root / "script.json").write_text(json.dumps(script), encoding="utf-8")
    (root / "idea.json").write_text(json.dumps(
        {"title": "T", "genre": "g", "description": "d"}), encoding="utf-8")


class FakeSynth:
    """A local synthesiser: half a second a word, at a steady non-silent level."""

    def __init__(self):
        self.texts = []

    def __call__(self, text, out, *, voice, rate, opts):
        self.texts.append(text)
        words = len(voice_words(text))
        seconds = 0.5 * max(1, words)
        out.parent.mkdir(parents=True, exist_ok=True)
        import struct
        frames = struct.pack("<h", 3000) * int(seconds * rate)
        voice_module.write_wav(out, frames, rate)
        return {"timing": "estimated"}


voice_module = voice


def voice_words(text):
    import re
    return re.sub(r"<[^>]+>", " ", text).split()


class DraftRun(unittest.TestCase):
    def setUp(self):
        saved = harness.ENGINE
        self.addCleanup(setattr, harness, "ENGINE", saved)
        harness.ENGINE = NoModel
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        write_project(self.root)

    def run_draft(self, available=("espeak",), **extra):
        args = test_wordless.WordlessRun.args(self)
        args.draft = True
        for key, value in extra.items():
            setattr(args, key, value)
        synth = FakeSynth()
        err = io.StringIO()
        with mock.patch.object(voice.Engine, "available",
                               lambda self: (self.name in available, "hint")), \
                mock.patch.dict(voice.SYNTH, {"espeak": synth, "kokoro": synth}), \
                mock.patch.object(voice, "_kit", NoKit), contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            code = voice.run(args)
        return code, synth, err.getvalue()

    def draft(self):
        return json.loads((self.root / "voice-draft.json").read_text(encoding="utf-8"))

    def test_each_sentence_is_read_and_measured_with_the_scripts_sha(self):
        code, synth, _ = self.run_draft()
        self.assertEqual(code, 0)
        data = self.draft()
        self.assertEqual(data["engine"], "espeak")
        sections, _ = load_script(self.root / "script.json")
        self.assertEqual(data["script_sha"], voice.script_sha(sections))
        self.assertEqual([(r["label"], r["text"]) for r in data["sentences"]],
                         [("1.1", "Ask Schrödinger once."), ("1.1", "Then 12 more times."),
                          ("2.1", "Line three.")])
        first = data["sentences"][0]
        self.assertAlmostEqual(first["seconds"], 1.5, places=2)
        self.assertEqual(first["words"], 3)
        self.assertEqual(first["wpm"], 120.0)
        self.assertEqual(data["listened"], "not evidenced")
        self.assertEqual(set(data["by_section"]), {"One", "Two"})
        self.assertEqual(data["by_section"]["Two"]["planned"], 3)
        self.assertIn("findings", data)
        self.assertEqual(len(list((self.root / "voice-draft").glob("*.wav"))), 3)
        self.assertFalse((self.root / "voice.json").exists())
        self.assertEqual(len(synth.texts), 3)

    def test_kokoro_is_preferred_when_both_are_there(self):
        code, _, _ = self.run_draft(available=("espeak", "kokoro"))
        self.assertEqual(code, 0)
        self.assertEqual(self.draft()["engine"], "kokoro")

    def test_the_marks_are_rendered_for_the_draft_engine(self):
        script = json.loads((self.root / "script.json").read_text(encoding="utf-8"))
        script["sections"][0]["shots"][0] |= {
            "emphasis": ["once", "more"],
            "pronounce": [{"term": "Schrödinger", "say": "SHROH-ding-er"}]}
        (self.root / "script.json").write_text(json.dumps(script), encoding="utf-8")
        _, synth, _ = self.run_draft()
        self.assertEqual(synth.texts[0], "<speak>Ask <emphasis>shroh</emphasis>-ding-er "
                         "<emphasis>once</emphasis>.</speak>")
        self.assertEqual(synth.texts[1], "<speak>Then 12 <emphasis>more</emphasis> times."
                         "</speak>")
        self.assertEqual(synth.texts[2], "Line three.")
        self.assertEqual(self.draft()["sentences"][0]["text"], "Ask Schrödinger once.")

    def test_a_mark_change_changes_the_sha(self):
        self.run_draft()
        before = self.draft()["script_sha"]
        script = json.loads((self.root / "script.json").read_text(encoding="utf-8"))
        script["sections"][1]["shots"][0]["pauses"] = ["three"]
        (self.root / "script.json").write_text(json.dumps(script), encoding="utf-8")
        self.run_draft()
        self.assertNotEqual(self.draft()["script_sha"], before)

    def test_a_script_file_given_is_the_one_read_and_hashed(self):
        other = self.root / "other.json"
        other.write_text(json.dumps({"sections": [{"heading": "Only", "shots": [
            {"n": 1, "label": "1.1", "visual": "v", "seconds": 2,
             "narration": "Something else."}]}]}), encoding="utf-8")
        code, synth, _ = self.run_draft(script=str(other))
        self.assertEqual(code, 0)
        sections, _ = load_script(other)
        self.assertEqual(self.draft()["script_sha"], voice.script_sha(sections))
        own, _ = load_script(self.root / "script.json")
        self.assertNotEqual(self.draft()["script_sha"], voice.script_sha(own))
        self.assertEqual(synth.texts, ["Something else."])

    def test_a_long_sentence_is_a_fail_in_the_draft(self):
        write_project(self.root, [{"n": 1, "label": "1.1", "visual": "v", "seconds": 4,
                                   "narration": " ".join(["word"] * 22) + "."}])
        code, _, err = self.run_draft()
        self.assertEqual(code, 0)          # the draft reports; the paid run is gated
        fails = [f for f in self.draft()["findings"] if f["severity"] == "fail"]
        self.assertEqual(len(fails), 1)
        self.assertIn("fail", err)


class PaidGate(unittest.TestCase):
    """The gate stands only in front of a paid engine, and never under --dry-run."""

    def setUp(self):
        saved = harness.ENGINE
        self.addCleanup(setattr, harness, "ENGINE", saved)
        harness.ENGINE = test_wordless.FakeEngine
        test_wordless.FakeEngine.prompts = []
        test_wordless.FakeEngine.answers = [
            {"voice": "", "speed": 1.0, "why": "w"},
            {"verdict": "ok", "retakes": [], "notes": "n"}]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        write_project(self.root)

    def sha(self):
        sections, _ = load_script(self.root / "script.json")
        return voice.script_sha(sections)

    def write_draft(self, sha, fails=0):
        findings = [{"severity": "fail", "rule": "direction 29", "label": "1.1",
                     "detail": "long"}] * fails
        (self.root / "voice-draft.json").write_text(json.dumps(
            {"engine": "espeak", "script_sha": sha, "sentences": [], "by_section": {},
             "findings": findings, "listened": "not evidenced"}), encoding="utf-8")

    def run_paid(self, engine="elevenlabs", **extra):
        args = test_wordless.WordlessRun.args(self)
        args.engine, args.voice = engine, "v1" if engine == "elevenlabs" else ""
        for key, value in extra.items():
            setattr(args, key, value)
        err = io.StringIO()
        with mock.patch.object(voice, "_kit", FakeKit), contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            code = voice.run(args)
        return code, err.getvalue()

    def recorded(self):
        return json.loads((self.root / "voice.json").read_text(encoding="utf-8"))

    def test_no_draft_warns_naming_rule_29_and_records(self):
        code, err = self.run_paid()
        self.assertEqual(code, 0)
        self.assertIn("rule 29", err)
        self.assertEqual(self.recorded()["draft"], {"status": "missing", "fails": 0})

    def test_a_stale_draft_warns_and_records(self):
        self.write_draft("not-this-script", fails=1)
        code, err = self.run_paid()
        self.assertEqual(code, 0)
        self.assertIn("rule 29", err)
        self.assertEqual(self.recorded()["draft"]["status"], "stale")

    def test_a_current_draft_with_a_fail_stops_before_casting(self):
        self.write_draft(self.sha(), fails=1)
        code, err = self.run_paid()
        self.assertEqual(code, 1)
        self.assertIn("--accept-draft", err)
        self.assertEqual(test_wordless.FakeEngine.prompts, [])
        self.assertFalse((self.root / "voice.json").exists())

    def test_accept_draft_records_it_accepted(self):
        self.write_draft(self.sha(), fails=2)
        code, _ = self.run_paid(accept_draft=True)
        self.assertEqual(code, 0)
        self.assertEqual(self.recorded()["draft"], {"status": "accepted", "fails": 2})

    def test_a_current_draft_without_a_fail_is_current(self):
        self.write_draft(self.sha())
        code, err = self.run_paid()
        self.assertEqual(code, 0)
        self.assertNotIn("rule 29", err)
        self.assertEqual(self.recorded()["draft"], {"status": "current", "fails": 0})

    def test_dry_run_skips_the_gate(self):
        self.write_draft(self.sha(), fails=1)
        code, err = self.run_paid(dry_run=True)
        self.assertEqual(code, 0)
        self.assertNotIn("rule 29", err)

    def test_a_local_engine_ignores_the_draft_and_writes_no_draft_key(self):
        self.write_draft(self.sha(), fails=1)
        code, err = self.run_paid(engine="stub")
        self.assertEqual(code, 0)
        self.assertNotIn("rule 29", err)
        self.assertNotIn("draft", self.recorded())


if __name__ == "__main__":
    unittest.main()
