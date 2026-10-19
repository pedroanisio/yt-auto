"""The realism probe suite: neutral briefs, an author that codes with the kit,
the harness's own build and frames, a review by a different model that names
each defect's cause, and a run table the next run is compared with. The model,
the engine's validation and the renderer are fakes here.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from claude_engine import Event, Usage  # noqa: E402
from tests.test_universality import leaks  # noqa: E402
from ytstudio import harness, probes, sdkkit  # noqa: E402

PROGRAM = '''from srkit import FRAME, S, save
scene = S.Scene(version="auto")
scene.add_project(width=FRAME["width"], height=FRAME["height"], fps=str(FRAME["fps"]),
                  duration=3)
scene.add_composition()
save(scene, "probe.scene.xml")
'''


def png_bytes(value: int = 128) -> bytes:
    """A small grey PNG with some texture, so the frame checks pass it."""
    import struct
    import zlib
    w, h = 64, 36
    raw = b"".join(b"\x00" + bytes(((value + (x * 7 + y * 3) % 60) % 256) for x in range(w)
                                   for _ in range(3)) for y in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(
            ">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


VERDICT = {"score": 5.5, "seen": "a sphere on a plane",
           "findings": [{"frame": "probe__1.5s.png", "problem": "no contact shadow",
                         "cause": "authoring", "fix": "castShadow on the key"},
                        {"frame": "probe__0.75s.png", "problem": "specks near the emitter",
                         "cause": "engine", "fix": "light with a light"}],
           "summary": "competent, floats"}


class FakeEngine:
    streams: list = []
    program: str | None = PROGRAM

    def __init__(self, config):
        self.config = config
        self.usage = Usage()
        self.usage.cost_usd = 0.5
        self.usage.calls = 1

    def stream(self, prompt, system=None, resume=None):
        tools = tuple(self.config.tools or ())
        FakeEngine.streams.append({"tools": tools, "model": self.config.model,
                                   "cwd": str(self.config.cwd), "system": system,
                                   "prompt": prompt, "resume": resume})
        self.session_id = resume or f"session-{len(FakeEngine.streams)}"
        if "Write" in tools:
            if FakeEngine.program is not None:
                (Path(self.config.cwd) / probes.PROGRAM).write_text(FakeEngine.program)
            yield Event(type="assistant", subtype="tool_use", tool_name="Bash",
                        tool_input={"command": "python3 kit.py build compose.py"})
            yield Event(type="assistant", text="I chose a sphere.")
        else:
            yield Event(type="assistant", text="```json\n" + json.dumps(VERDICT) + "\n```")


class ProbeCase(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="probes-"))
        self.addCleanup(shutil.rmtree, self.work, True)
        FakeEngine.streams = []
        FakeEngine.program = PROGRAM
        for target, value in ((harness, "ENGINE"),):
            patcher = mock.patch.object(target, value, FakeEngine)
            patcher.start()
            self.addCleanup(patcher.stop)
        patches = [mock.patch("ytstudio.render.engine_validate", return_value=[]),
                   mock.patch("ytstudio.render.engine_render_still",
                              return_value=(png_bytes(), "rendered")),
                   mock.patch.object(sdkkit, "frame_host", return_value="test-host"),
                   mock.patch.object(sdkkit, "engine_manifest",
                                     return_value={"source": "test", "manifest": {
                                         "engine": {"name": "rs", "version": "1"},
                                         "entries": []}})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def args(self, **over) -> argparse.Namespace:
        p = argparse.ArgumentParser()
        probes.add_arguments(p)
        ns = p.parse_args(["--work", str(self.work), "--quiet", "--jobs", "1", "--rounds", "0"])
        for k, v in over.items():
            setattr(ns, k, v)
        return ns

    def run_quiet(self, args) -> int:
        with mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            return probes.run(args)


class Briefs(unittest.TestCase):
    def test_every_class_a_photoreal_film_needs_has_a_probe(self):
        self.assertGreaterEqual(len(probes.PROBES), 8)
        self.assertEqual(len(probes.BY_ID), len(probes.PROBES))

    def test_briefs_name_no_film_and_prescribe_no_look(self):
        for probe in probes.PROBES:
            with self.subTest(probe=probe.id):
                self.assertEqual(leaks(probe.brief), [])
                self.assertNotRegex(probe.brief, r"#[0-9A-Fa-f]{6}")      # no colour
                self.assertNotIn("renderer", probe.brief)               # no construct

    def test_the_brief_hands_the_choices_to_the_author_and_the_kit_to_read(self):
        text = probes.brief_text(probes.BY_ID["metal"], props=[])
        self.assertIn("is yours to choose", text)
        self.assertIn("kit/SDK-GUIDE.md", text)
        self.assertIn(probes.PROGRAM, text)
        self.assertIn(probes.DOCUMENT, text)


class Run(ProbeCase):
    def test_a_probe_is_authored_built_by_the_harness_framed_and_reviewed(self):
        code = self.run_quiet(self.args(only="metal"))
        self.assertEqual(code, 0)
        runs = [p for p in (self.work / probes.PROBES_DIR).iterdir() if p.is_dir()]
        summary = json.loads((runs[0] / "summary.json").read_text())
        row = summary["rows"][0]
        self.assertEqual(row["probe"], "metal")
        self.assertEqual(row["score"], 5.5)
        self.assertEqual(row["causes"], {"authoring": 1, "engine": 1, "asset": 0, "unknown": 0})
        self.assertTrue(row["build"]["ok"])
        self.assertEqual(len(row["build"]["frames"]), len(probes.BY_ID["metal"].times))
        ws = runs[0] / "metal"
        for rel in ("brief.md", "kit.py", "srkit.py", "kit/ENGINE.md", "probe.scene.xml",
                    "probe.provenance.json", "review/brief.md", "review/compose.py",
                    "review/ENGINE.md", "probe.json"):
            self.assertTrue((ws / rel).exists(), rel)
        self.assertIn("| metal | 5.5 | 5.5 |", (runs[0] / "summary.md").read_text())

    def test_the_author_codes_with_the_kit_and_the_reviewer_only_looks(self):
        self.run_quiet(self.args(only="glass", model="sonnet", reviewer_model="opus"))
        author, reviewer = FakeEngine.streams
        self.assertIn("Bash", author["tools"])
        self.assertEqual(author["system"], sdkkit.AUTHOR_SYSTEM)
        self.assertEqual(author["model"], "sonnet")
        self.assertNotIn("Write", reviewer["tools"])
        self.assertEqual(reviewer["model"], "opus")
        self.assertTrue(reviewer["cwd"].endswith("review"))

    def test_under_the_bar_the_findings_go_back_to_the_same_session(self):
        scores = iter([4.0, 6.0, 8.5])
        real = FakeEngine.stream

        def scored(engine, prompt, system=None, resume=None):
            if "Write" not in tuple(engine.config.tools or ()):
                VERDICT["score"] = next(scores)
            yield from real(engine, prompt, system=system, resume=resume)
        try:
            with mock.patch.object(FakeEngine, "stream", scored):
                self.run_quiet(self.args(only="coated", rounds=3, bar=8.0))
        finally:
            VERDICT["score"] = 5.5
        authors = [s for s in FakeEngine.streams if "Write" in s["tools"]]
        self.assertEqual(len(authors), 3)                 # stopped at the bar, not at 4
        self.assertIsNone(authors[0]["resume"])
        self.assertEqual(authors[1]["resume"], "session-1")
        self.assertEqual(authors[2]["resume"], "session-1")
        self.assertIn("no contact shadow", authors[1]["prompt"])   # the findings, verbatim
        self.assertIn("Re-read brief.md", authors[1]["prompt"])
        run = next(p for p in (self.work / probes.PROBES_DIR).iterdir() if p.is_dir())
        row = json.loads((run / "summary.json").read_text())["rows"][0]
        self.assertEqual([r["score"] for r in row["rounds"]], [4.0, 6.0, 8.5])
        self.assertEqual(row["score"], 8.5)
        self.assertEqual(row["author"]["sessions"], 3)

    def test_a_revision_that_scores_lower_is_put_back_and_the_best_round_counts(self):
        scores = iter([5.0, 3.0, 4.0])
        real = FakeEngine.stream
        versions = iter(["# round 0\n", "# round 1\n", "# round 2\n"])

        def scored(engine, prompt, system=None, resume=None):
            if "Write" in tuple(engine.config.tools or ()):
                FakeEngine.program = PROGRAM + next(versions)
            else:
                VERDICT["score"] = next(scores)
            yield from real(engine, prompt, system=system, resume=resume)
        try:
            with mock.patch.object(FakeEngine, "stream", scored):
                self.run_quiet(self.args(only="glass", rounds=2, bar=8.0))
        finally:
            VERDICT["score"] = 5.5
            FakeEngine.program = PROGRAM
        authors = [s for s in FakeEngine.streams if "Write" in s["tools"]]
        self.assertIn("below round 0", authors[2]["prompt"])          # told why, and from where
        run = next(p for p in (self.work / probes.PROBES_DIR).iterdir() if p.is_dir())
        row = json.loads((run / "summary.json").read_text())["rows"][0]
        self.assertEqual([r["score"] for r in row["rounds"]], [5.0, 3.0, 4.0])
        self.assertEqual(row["score"], 5.0)
        self.assertEqual(row["best_round"], 0)
        for n in range(3):
            self.assertTrue((run / "glass" / "rounds" / f"r{n:02d}" / "compose.py").is_file())
        self.assertIn("# round 0", (run / "glass" / "rounds" / "r00" / "compose.py").read_text())

    def test_resume_keeps_finished_probes_and_restarts_the_rest(self):
        self.run_quiet(self.args(name="base", only="metal"))
        (self.work / probes.PROBES_DIR / "base" / "glass").mkdir()       # an unfinished one
        (self.work / probes.PROBES_DIR / "base" / "glass" / "stale.txt").write_text("x")
        FakeEngine.streams = []
        code = self.run_quiet(self.args(name="base", only="metal,glass", resume=True))
        self.assertEqual(code, 0)
        authored = {Path(s["cwd"]).name for s in FakeEngine.streams if "Write" in s["tools"]}
        self.assertEqual(authored, {"glass"})                          # metal was kept
        self.assertFalse((self.work / probes.PROBES_DIR / "base" / "glass" / "stale.txt").exists())
        rows = json.loads((self.work / probes.PROBES_DIR / "base" / "summary.json").read_text())["rows"]
        self.assertEqual([r["probe"] for r in rows], ["metal", "glass"])

    def test_an_existing_run_is_not_overwritten_without_resume(self):
        self.run_quiet(self.args(name="base", only="metal"))
        with mock.patch("sys.stderr", io.StringIO()) as err:
            code = probes.run(self.args(name="base", only="metal"))
        self.assertEqual(code, 2)
        self.assertIn("--resume", err.getvalue())

    def test_a_model_never_grades_its_own_work(self):
        with mock.patch("sys.stderr", io.StringIO()) as err:
            code = probes.run(self.args(model="opus", reviewer_model="opus"))
        self.assertEqual(code, 2)
        self.assertIn("different model", err.getvalue())

    def test_no_program_means_no_review_and_a_measured_zero(self):
        FakeEngine.program = None
        self.run_quiet(self.args(only="volume"))
        run = next(p for p in (self.work / probes.PROBES_DIR).iterdir() if p.is_dir())
        row = json.loads((run / "summary.json").read_text())["rows"][0]
        self.assertEqual(row["score"], 0.0)
        self.assertIn("no frame to judge", row["summary"])
        self.assertEqual(len(FakeEngine.streams), 1)          # the author, no reviewer

    def test_a_program_that_does_not_reproduce_gets_no_review(self):
        FakeEngine.program = PROGRAM.replace("duration=3)",
                                             "duration=1 + __import__('os').getpid() % 97)")
        self.run_quiet(self.args(only="detail"))
        run = next(p for p in (self.work / probes.PROBES_DIR).iterdir() if p.is_dir())
        row = json.loads((run / "summary.json").read_text())["rows"][0]
        self.assertFalse(row["build"]["ok"])
        self.assertIn("seed", row["summary"])

    def test_a_full_run_becomes_latest_and_the_next_run_shows_the_change(self):
        self.run_quiet(self.args(name="first"))
        self.assertEqual(json.loads((self.work / probes.PROBES_DIR / "latest.json")
                                    .read_text())["run"], "first")
        measured = probes.latest_measured(self.work)
        self.assertEqual(len(measured), len(probes.PROBES))
        VERDICT["score"] = 7.0
        try:
            self.run_quiet(self.args(name="second", only="metal"))
        finally:
            VERDICT["score"] = 5.5
        row = json.loads((self.work / probes.PROBES_DIR / "second" / "summary.json")
                         .read_text())["rows"][0]
        self.assertEqual(row["delta"], 1.5)
        # a partial run is not the new baseline
        self.assertEqual(json.loads((self.work / probes.PROBES_DIR / "latest.json")
                                    .read_text())["run"], "first")

    def test_the_last_run_is_what_engine_md_reports_as_measured(self):
        self.run_quiet(self.args(name="first"))
        ws = self.work / probes.PROBES_DIR / "first" / "metal"
        self.run_quiet(self.args(name="again", only="metal"))
        again = (self.work / probes.PROBES_DIR / "again" / "metal" / "kit" / "ENGINE.md").read_text()
        self.assertIn("Measured on this engine", again)
        self.assertTrue(ws.is_dir())

    def test_props_are_linked_below_the_workspace_and_listed_in_the_brief(self):
        props_root = self.work / probes.PROBES_DIR / "props"
        (props_root / "props" / "hdri" / "sky").mkdir(parents=True)
        (props_root / "props" / "hdri" / "sky" / "sky_2k.hdr").write_bytes(b"#?RADIANCE\n")
        (props_root / "props.json").write_text(json.dumps({"items": [
            {"kind": "hdri", "file": "props/hdri/sky/sky_2k.hdr", "about": "an open sky"}]}))
        self.run_quiet(self.args(only="terrain"))
        run = next(p for p in (self.work / probes.PROBES_DIR).iterdir()
                   if p.is_dir() and p.name != "props")
        ws = run / "terrain"
        linked = ws / "props" / "hdri" / "sky" / "sky_2k.hdr"
        self.assertTrue(linked.is_file())
        self.assertFalse((ws / "props").is_symlink())
        self.assertIn("props/hdri/sky/sky_2k.hdr", (ws / "brief.md").read_text())


if __name__ == "__main__":
    unittest.main()
