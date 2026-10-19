"""`yt sound --synth`: the cues nothing could fetch become original sound.

The reference package this pipeline was compared against shipped its own
procedural sound design: a deterministic script, no samples, a WAV whose
hash is in the scene. A cue sheet whose library search came back empty is
the same problem, so an agent writes that script for the cues still without
a file, runs it, and the plumbing checks every WAV it wrote before any cue
says it has one. What these tests pin is the contract with `yt build`: a
realised cue carries `file`, `licence`, `credit` and `duration` exactly as a
fetched one does, a file that fails the check is never presented as filled,
and a later `--fetch` does not throw a realised cue away.
"""

from __future__ import annotations

import json
import math
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_engine import Usage  # noqa: E402
from ytstudio import harness, sound  # noqa: E402


def write_wav(path: Path, seconds: float, *, rate=48000, channels=2,
              peak=0.5, freq=440.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        amp = int(peak * 32767)
        one = [struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / rate)))
               for i in range(frames)]
        w.writeframes(b"".join(s * channels for s in one))


def sheet() -> dict:
    return {"cues": [
        {"cue_id": "C1", "kind": "sfx", "name": "Drop impact plink", "at": 3.6,
         "seconds": 1.0, "description": "a tuned plink as the drop meets the surface"},
        {"cue_id": "C2", "kind": "silence", "name": "Held breath", "at": 9.0, "seconds": 1.0},
        {"cue_id": "C3", "kind": "ambience", "name": "Room tone", "at": 0.0, "seconds": 2.0},
        {"cue_id": "C4", "kind": "sfx", "name": "Already fetched", "at": 5.0, "seconds": 1.0,
         "file": "sound/04-fetched.wav", "licence": "CC0", "credit": "user4",
         "freesound_id": 4},
    ], "unfilled": [{"cue_id": "C1", "name": "Drop impact plink", "kind": "sfx",
                     "queries": ["plink"], "reason": "no CC0 or CC-BY hit"}]}


class SynthCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        write_wav(self.root / "sound/04-fetched.wav", 1.0)
        self.data = sheet()

    def tearDown(self):
        self._tmp.cleanup()

    def author_writing(self, **per_cue):
        """A fake author: writes each target the way `per_cue[cue_id]` says
        (a dict of write_wav options, or None to write nothing)."""
        seen = {}

        def author(ws: Path, targets: list[dict]) -> Usage:
            seen["ws"], seen["targets"] = ws, targets
            (ws / "make_audio.py").write_text("# the synthesis script\n", encoding="utf-8")
            for t in targets:
                how = per_cue.get(t["cue_id"], {})
                if how is not None:
                    write_wav(self.root / t["file"], how.pop("seconds", t["seconds"]), **how)
            u = Usage(); u.cost_usd = 0.12
            return u
        return author, seen


class Targets(SynthCase):
    def test_only_cues_without_a_file_are_realised_and_silence_never(self):
        targets = sound.synth_targets(self.root, self.data)
        self.assertEqual([t["cue_id"] for t in targets], ["C1", "C3"])
        for t in targets:
            self.assertTrue(t["file"].startswith(sound.SYNTH_DIR + "/"))
            self.assertTrue(t["file"].endswith(".wav"))
        self.assertNotEqual(targets[0]["file"], targets[1]["file"])

    def test_a_cue_whose_file_is_gone_is_realised_again(self):
        (self.root / "sound/04-fetched.wav").unlink()
        ids = [t["cue_id"] for t in sound.synth_targets(self.root, self.data)]
        self.assertIn("C4", ids)


class Realise(SynthCase):
    def test_realised_cues_carry_the_fetch_contract(self):
        author, _ = self.author_writing()
        summary = sound.realise(self.root, self.data, author=author)
        cues = {c["cue_id"]: c for c in self.data["cues"]}
        for cid in ("C1", "C3"):
            cue = cues[cid]
            self.assertTrue((self.root / cue["file"]).is_file())
            self.assertEqual(cue["licence"], sound.SYNTH_LICENCE)
            self.assertTrue(cue["credit"])
            self.assertAlmostEqual(cue["duration"], cue["seconds"], places=2)
            self.assertEqual(cue["synth"]["script"], f"{sound.SYNTH_DIR}/make_audio.py")
            self.assertEqual(len(cue["synth"]["sha256"]), 64)
        self.assertEqual(cues["C4"]["file"], "sound/04-fetched.wav")   # untouched
        self.assertNotIn("file", cues["C2"])
        self.assertEqual(self.data["unfilled"], [])
        self.assertEqual(summary["realised"], 2)
        self.assertEqual(summary["failed"], 0)
        self.assertAlmostEqual(summary["cost_usd"], 0.12)
        self.assertIs(self.data["synth"], summary)

    def test_a_file_that_fails_the_check_is_never_presented_as_filled(self):
        author, _ = self.author_writing(C1={"rate": 44100}, C3=None)
        summary = sound.realise(self.root, self.data, author=author)
        cues = {c["cue_id"]: c for c in self.data["cues"]}
        self.assertNotIn("file", cues["C1"])
        self.assertNotIn("file", cues["C3"])
        reasons = {u["cue_id"]: u["reason"] for u in self.data["unfilled"]}
        self.assertIn("44100", reasons["C1"])
        self.assertIn("not written", reasons["C3"])
        self.assertEqual(len(self.data["unfilled"]), 2)      # C1 replaced, not duplicated
        self.assertEqual(summary["failed"], 2)

    def test_silence_clipping_and_length_are_checked(self):
        cases = {"silent": {"peak": 0.0}, "clipped": {"peak": 1.0},
                 "short": {"seconds": 0.2}, "mono": {"channels": 1}}
        for label, how in cases.items():
            with self.subTest(label):
                path = self.root / f"check-{label}.wav"
                opts = dict(how); seconds = opts.pop("seconds", 1.0)
                write_wav(path, seconds, **opts)
                _, problems = sound.check_wav(path, want_seconds=1.0)
                self.assertTrue(problems, f"{label} passed the check")
        write_wav(self.root / "good.wav", 1.0)
        self.assertEqual(sound.check_wav(self.root / "good.wav", want_seconds=1.0)[1], [])

    def test_nothing_to_realise_asks_no_author(self):
        for cue in self.data["cues"]:
            if cue["cue_id"] in ("C1", "C3"):
                cue["kind"] = "silence"
        called = []
        summary = sound.realise(self.root, self.data, author=lambda *a: called.append(a))
        self.assertEqual(called, [])
        self.assertEqual(summary["realised"], 0)

    def test_the_workspace_holds_the_brief_the_targets_and_a_checker(self):
        author, seen = self.author_writing()
        sound.realise(self.root, self.data, author=author)
        ws = seen["ws"]
        self.assertEqual(ws, self.root / sound.SYNTH_DIR)
        brief = (ws / "BRIEF.md").read_text(encoding="utf-8")
        for t in seen["targets"]:
            self.assertIn(t["file"], brief)
            self.assertIn(t["cue_id"], brief)
        for needle in ("48000", "stereo", "16-bit", "make_audio.py", "check.py", "no samples"):
            self.assertIn(needle, brief)
        listed = json.loads((ws / "cues.json").read_text(encoding="utf-8"))
        self.assertEqual([t["cue_id"] for t in listed], ["C1", "C3"])

    def test_the_checker_the_agent_runs_is_the_plumbing_check(self):
        author, seen = self.author_writing(C3={"peak": 0.0})
        sound.realise(self.root, self.data, author=author)
        proc = subprocess.run([sys.executable, "check.py"], cwd=seen["ws"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("C1 ok", proc.stdout)
        self.assertIn("C3", proc.stdout)
        self.assertIn("silent", proc.stdout)


class Author(SynthCase):
    def test_the_default_author_is_a_sealed_agent_in_the_workspace(self):
        made = []

        class FakeEngine:
            def __init__(self, config):
                self.config, self.usage = config, Usage()
                made.append(self)

            def agent(self, prompt, **kw):
                self.prompt = prompt
                self.usage.cost_usd = 0.3
                return None

        saved = harness.ENGINE
        harness.ENGINE = FakeEngine
        try:
            author = sound.synth_author(model="sonnet", budget_usd=2.0)
            ws = self.root / sound.SYNTH_DIR
            ws.mkdir(parents=True)
            usage = author(ws, sound.synth_targets(self.root, self.data))
        finally:
            harness.ENGINE = saved
        engine = made[0]
        self.assertEqual(Path(engine.config.cwd), ws)
        self.assertIn("Write", engine.config.tools)
        self.assertIn("Bash", engine.config.tools)
        self.assertNotIn("Agent", engine.config.tools)
        self.assertEqual(engine.config.max_turns_budget_usd, 2.0)
        for needle in ("BRIEF.md", "make_audio.py", "check.py"):
            self.assertIn(needle, engine.prompt)
        self.assertAlmostEqual(usage.cost_usd, 0.3)


class FetchKeepsSynth(SynthCase):
    def test_a_later_fetch_keeps_a_realised_cue(self):
        author, _ = self.author_writing()
        sound.realise(self.root, self.data, author=author)
        before = {c["cue_id"]: dict(c) for c in self.data["cues"]}
        sound.fetch(self.root, self.data, token="", say=lambda m: None)
        after = {c["cue_id"]: c for c in self.data["cues"]}
        for cid in ("C1", "C3"):
            self.assertEqual(after[cid].get("file"), before[cid]["file"])
            self.assertEqual(after[cid].get("synth"), before[cid]["synth"])
        self.assertEqual([u["cue_id"] for u in self.data["unfilled"]], [])


class Cli(unittest.TestCase):
    def test_sound_takes_synth_and_a_budget(self):
        from ytstudio import cli
        args = cli.build_parser().parse_args(
            ["sound", "--project", "work/x", "--synth", "--synth-budget-usd", "1.5"])
        self.assertTrue(args.synth)
        self.assertEqual(args.synth_budget_usd, 1.5)


if __name__ == "__main__":
    unittest.main()
