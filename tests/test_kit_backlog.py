"""The kit backlog (AUTO-12..16): authors confined to their workspace, the
reviewer's checklist and a pre-check, a structure-to-surface example, a bound
on a still's render time, and an author who knows his clock. No model, no
engine and no network: fake engines, a fake clock and patched renders.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from claude_engine import ClaudeError, Event, Usage  # noqa: E402
from tests.test_probes import png_bytes  # noqa: E402
from tests.test_sdkkit import MANIFEST, KitCase  # noqa: E402
from tests.test_universality import leaks  # noqa: E402
from ytstudio import framecheck, harness, keyframe, probes, sdkkit, vfx  # noqa: E402


def grey_png(draw) -> bytes:
    """A 320x180 greyscale PNG; `draw(im)` paints on a PIL image."""
    from PIL import Image
    im = Image.new("L", (320, 180), 0)
    draw(im)
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


# --------------------------------------------------------------------------- #
# AUTO-12
# --------------------------------------------------------------------------- #

class Confinement(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="conf-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        for name in ("props", "keyframe", "downloads"):
            (self.root / name).mkdir()
        self.ws = self.root / "vfx" / "v01"
        self.ws.mkdir(parents=True)
        # the machine's reference libraries are not part of what these tests pin
        patcher = mock.patch.object(sdkkit, "REFERENCES_DIR", self.root / "no-references")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_files_are_confined_to_the_workspace_and_the_project_dirs_read_only(self):
        cfg = sdkkit.author_config(self.ws, project_root=self.root, programs=["01-open.py"])
        self.assertTrue(cfg["restricted"])
        self.assertEqual(sorted(Path(d).name for d in cfg["add_dirs"]),
                         ["downloads", "keyframe", "props"])
        for d in cfg["add_dirs"]:
            # a single leading "/" is project-relative to Claude Code; "//" is absolute
            self.assertTrue(d.startswith("/"))
            self.assertIn(f"Write(/{d}/**)", cfg["disallowed_tools"])
            self.assertIn(f"Edit(/{d}/**)", cfg["disallowed_tools"])
            self.assertNotIn(f"Write({d}/**)", cfg["disallowed_tools"])

    def test_a_project_dir_that_does_not_exist_is_not_added(self):
        shutil.rmtree(self.root / "downloads")
        cfg = sdkkit.author_config(self.ws, project_root=self.root)
        self.assertEqual(sorted(Path(d).name for d in cfg["add_dirs"]), ["keyframe", "props"])

    def test_bash_is_an_allow_list_and_nothing_auto_allows_the_bare_tool(self):
        cfg = sdkkit.author_config(self.ws, project_root=self.root, programs=["01-open.py"])
        self.assertFalse(cfg["auto_allow_tools"])
        self.assertEqual(cfg["permission_mode"], "dontAsk")
        allowed = list(cfg["allowed_tools"])
        self.assertNotIn("Bash", allowed)
        for rule in ("Bash(python3 kit.py *)", "Bash(python3 sr.py *)", "Bash(python3 01-open.py)"):
            self.assertIn(rule, allowed)
        for tool in ("Read", "Glob", "Grep", "Write", "Edit"):
            self.assertIn(tool, allowed)
        self.assertEqual(tuple(cfg["tools"]), sdkkit.AUTHOR_TOOLS)

    def test_probes_author_session_is_confined(self):
        seen = {}

        class Rec:
            def __init__(self, config):
                seen["config"] = self.config = config
                self.usage = Usage()

            def stream(self, prompt, system=None, resume=None):
                return iter(())
        ws = self.root / "probe"
        ws.mkdir()
        with mock.patch.object(harness, "ENGINE", Rec):
            probes.author(ws, probes.PROBES[0], model="sonnet", effort=None, budget_usd=1,
                          verbose=False)
        cfg = seen["config"]
        self.assertTrue(cfg.restricted)
        self.assertFalse(cfg.auto_allow_tools)
        self.assertIn("Bash(python3 kit.py *)", cfg.allowed_tools)
        self.assertIn("Bash(python3 compose.py)", cfg.allowed_tools)

    def test_vfx_and_keyframe_author_sessions_are_confined(self):
        from tests import test_vfx_world as world
        configs = []

        class Rec(world.FakeEngine):
            def __init__(self, config):
                configs.append(config)
                super().__init__(config)
        base = world.Review("test_a_score_at_the_bar_needs_no_revision")
        base.setUp()
        self.addCleanup(base.tearDown)
        (base.root / "props").mkdir(exist_ok=True)
        harness.ENGINE = Rec
        base.author()
        author = next(c for c in configs if "Write" in (c.tools or ()))
        self.assertTrue(author.restricted)
        self.assertFalse(author.auto_allow_tools)
        self.assertIn(str(base.root / "props"), [str(d) for d in author.add_dirs])
        self.assertTrue(any(r.startswith("Bash(python3 ") and r.endswith(".py)")
                         for r in author.allowed_tools), author.allowed_tools)
        self.assertEqual(author.timeout, vfx.AUTHOR_TIMEOUT)

    def test_the_keyframe_author_session_is_confined(self):
        from tests import test_keyframe as tk
        configs = []

        class Rec(tk.FakeEngine):
            def __init__(self, config):
                configs.append(config)
                super().__init__(config)
        base = tk.Loop("test_the_review_reads_the_frame_and_scores_the_four_categories")
        base.setUp()
        self.addCleanup(base.tearDown)
        harness.ENGINE = Rec
        base.run_stage()
        author = next(c for c in configs if "Write" in (c.tools or ()))
        self.assertTrue(author.restricted)
        self.assertFalse(author.auto_allow_tools)
        self.assertIn(str(base.root / "downloads"), [str(d) for d in author.add_dirs])
        self.assertIn(f"Bash(python3 {keyframe.PROGRAM_NAME})", author.allowed_tools)


class EngineFacts(KitCase):
    def test_the_facts_live_in_a_data_file_stamped_with_an_engine_version(self):
        data = json.loads((sdkkit.FACTS_FILE).read_text(encoding="utf-8"))
        self.assertTrue(data["verified_on"])
        blob = json.dumps(data["facts"])
        for needle in ("100 scene units","castShadow", "environmentVisible", "emissive",
                       "baseColor", "4 samples", "512", "shutterAngle"):
            self.assertIn(needle, blob)
        for fact in data["facts"]:
            self.assertTrue(fact["text"] and fact["source"])
        self.assertEqual(leaks(blob), [])

    def test_engine_md_says_verified_on_and_stays_quiet_when_the_version_matches(self):
        facts = {"verified_on": "9.9.9", "facts": [{"topic": "t", "text": "a fact",
                                                    "source": "s"}]}
        text = sdkkit.engine_text({"manifest": MANIFEST}, [], facts=facts)
        self.assertIn("verified on 9.9.9", text)
        self.assertIn("a fact", text)
        self.assertNotIn("re-check", text)

    def test_engine_md_flags_the_facts_when_the_engine_version_changed(self):
        facts = {"verified_on": "0.6.0", "facts": [{"topic": "t", "text": "a fact",
                                                    "source": "s"}]}
        text = sdkkit.engine_text({"manifest": MANIFEST}, [], facts=facts)
        self.assertIn("verified on 0.6.0", text)
        self.assertIn("this engine is 9.9.9", text)
        self.assertIn("re-check", text)

    def test_the_installed_engine_md_carries_the_facts(self):
        text = (self.dir / "kit" / "ENGINE.md").read_text(encoding="utf-8")
        self.assertIn("## Facts measured on the engine", text)
        self.assertIn("castShadow", text)


# --------------------------------------------------------------------------- #
# AUTO-13
# --------------------------------------------------------------------------- #

class Checklist(unittest.TestCase):
    def test_one_constant_feeds_the_brief_and_both_reviewers(self):
        text = framecheck.checklist_text()
        self.assertGreaterEqual(len(framecheck.CHECKLIST), 6)
        for item in framecheck.CHECKLIST:
            self.assertIn(item[1], text)
        self.assertIn(text, sdkkit.BRIEF)
        self.assertIn(text, vfx.REVIEW_ROLE)
        self.assertIn(text, keyframe.REVIEW_ROLE)
        self.assertEqual(leaks(text), [])
        self.assertIn("python3 kit.py precheck", sdkkit.BRIEF)


class Precheck(unittest.TestCase):
    @staticmethod
    def good():
        def draw(im):
            im.paste(40, (0, 0, 320, 180))
            im.paste(110, (0, 120, 320, 180))
            im.paste(200, (70, 30, 250, 150))
            im.paste(250, (120, 60, 200, 110))
        return grey_png(draw)

    def test_a_well_framed_picture_with_tonal_steps_passes(self):
        self.assertEqual(framecheck.precheck(self.good(), 1.0, "w"), [])

    def test_a_subject_that_is_a_speck_is_a_framing_finding(self):
        png = grey_png(lambda im: (im.paste(30, (0, 0, 320, 180)),
                                   im.paste(220, (158, 88, 164, 92))))
        found = framecheck.precheck(png, 1.0, "w")
        self.assertTrue(any("framing" in f for f in found), found)

    def test_a_picture_in_two_tones_has_too_few_contrast_steps(self):
        png = grey_png(lambda im: (im.paste(90, (0, 0, 320, 180)),
                                   im.paste(150, (60, 30, 260, 150))))
        found = framecheck.precheck(png, 1.0, "w")
        self.assertTrue(any("contrast" in f for f in found), found)

    def test_a_veil_over_the_whole_picture_is_haze(self):
        def draw(im):
            im.paste(150, (0, 0, 320, 180))
            im.paste(170, (60, 30, 260, 150))
            im.paste(185, (120, 60, 200, 110))
        found = framecheck.precheck(grey_png(draw), 1.0, "w")
        self.assertTrue(any("haze" in f for f in found), found)

    def test_every_finding_is_a_warning_naming_the_frame(self):
        png = grey_png(lambda im: im.paste(150, (0, 0, 320, 180)))
        for f in framecheck.precheck(png, 2.0, "world"):
            self.assertTrue(f.startswith("warning: frame at 2s of world"), f)


class Labels(unittest.TestCase):
    DOC = ('<scene><symbols><symbol id="s">'
           '<text id="a" text="one" x="100" y="100" width="300" height="60" size="30"/>'
           '<text id="b" text="two" x="180" y="120" width="300" height="60" size="30"/>'
           '<text id="c" text="three" x="1000" y="900" width="300" height="60" size="30"/>'
           '</symbol></symbols></scene>')

    def test_overlapping_labels_are_named(self):
        found = framecheck.label_overlaps(self.DOC, "doc")
        self.assertEqual(len(found), 1)
        self.assertIn("`a`", found[0])
        self.assertIn("`b`", found[0])
        self.assertNotIn("`c`", found[0])

    def test_labels_apart_are_clean(self):
        self.assertEqual(framecheck.label_overlaps(self.DOC.replace('x="180" y="120"',
                                                                     'x="700" y="500"'), "doc"), [])


class PrecheckCommand(KitCase):
    def test_the_kit_exposes_the_precheck_and_documents_it(self):
        self.assertIn("kit.py precheck", (self.dir / "kit.py").read_text(encoding="utf-8"))
        doc = self.dir / "a.scene.xml"
        doc.write_text(Labels.DOC)
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            code = sdkkit.main(["precheck", str(doc)])
        self.assertEqual(code, 0)
        self.assertIn("overlap", out.getvalue())

    def test_a_rendered_frame_carries_the_precheck_findings(self):
        doc = self.dir / "a.scene.xml"
        doc.write_text('<scene><project width="8" height="8" fps="30" duration="1"/></scene>')
        veil = grey_png(lambda im: (im.paste(150, (0, 0, 320, 180)),
                                    im.paste(175, (80, 40, 240, 140))))
        with mock.patch("ytstudio.render.engine_render_still", return_value=(veil, "ok")):
            _, findings = sdkkit.frame(doc, 0.5, host="h7")
        self.assertTrue(any("haze" in f or "contrast" in f for f in findings), findings)


# --------------------------------------------------------------------------- #
# AUTO-14
# --------------------------------------------------------------------------- #

FIXTURE_PDB = "".join(
    f"ATOM  {i + 1:5d}  CA  GLY A{i + 1:4d}    {x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00           C\n"
    for i, (x, y, z) in enumerate([(0, 0, 0), (3.8, 0, 0), (3.8, 3.8, 0), (0, 3.8, 3.8)]))


class StructureExample(KitCase):
    def example(self) -> Path:
        return next(p for p in sdkkit.example_files() if p.name == "structure_to_surface.py")

    def run_with(self, text: str, name: str = "s.pdb"):
        (self.dir / name).write_text(text)
        with mock.patch.dict(os.environ, {"KIT_STRUCTURE": name}):
            return sdkkit.build(self.program(self.example().read_text(), "structure.py"),
                                validate=False)

    def test_it_is_shipped_and_indexed(self):
        self.assertTrue(self.example().is_file())
        self.assertIn("structure_to_surface.py",
                      (self.dir / "kit" / "examples" / "README.md").read_text())

    def test_a_structure_file_becomes_one_clay_object_of_blobs(self):
        result = self.run_with(FIXTURE_PDB)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(result["deterministic"])
        self.assertEqual(result["documents"][0]["sdk"], [])
        xml = (self.dir / result["documents"][0]["path"]).read_text()
        self.assertEqual(xml.count('primitive="clay"'), 1)
        self.assertEqual(xml.count("<blob "), 4)

    def test_plain_xyz_lines_are_read_too(self):
        result = self.run_with("C 0.0 0.0 0.0\nO 1.5 0.0 0.0\n3 4 5\n", "s.xyz")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual((self.dir / result["documents"][0]["path"]).read_text().count("<blob "), 3)

    def test_the_blob_budget_is_stated_and_kept_by_even_decimation(self):
        module = self.example().read_text()
        self.assertIn("BLOB_BUDGET", module)
        budget = int(next(l for l in module.splitlines()
                          if l.startswith("BLOB_BUDGET")).split("=")[1].split("#")[0])
        many = "".join(f"{i % 50} {i // 50} {i % 7}\n" for i in range(budget * 3 + 5))
        result = self.run_with(many, "big.xyz")
        self.assertTrue(result["ok"], result.get("error"))
        xml = (self.dir / result["documents"][0]["path"]).read_text()
        self.assertEqual(xml.count("<blob "), budget)

    def test_the_still_stays_within_the_kit_budget(self):
        result = self.run_with(FIXTURE_PDB)
        doc = self.dir / result["documents"][0]["path"]
        with mock.patch("ytstudio.render.engine_render_still",
                        return_value=(png_bytes(), "ok")) as render:
            path, findings = sdkkit.frame(doc, 0.5, host="h7")
        self.assertIsNotNone(path)
        self.assertEqual(render.call_args.kwargs["timeout"], sdkkit.STILL_BUDGET)
        note = next(f for f in findings if sdkkit.RENDER_NOTE in f)
        self.assertIn("4 blobs", note)

    def test_a_missing_structure_file_is_a_clear_exit(self):
        with mock.patch.dict(os.environ, {"KIT_STRUCTURE": "nowhere.pdb"}):
            result = sdkkit.build(self.program(self.example().read_text(), "structure.py"),
                                  validate=False)
        self.assertFalse(result["ok"])
        self.assertIn("KIT_STRUCTURE", result["error"])

    def test_the_example_is_universal_and_says_its_values_are_placeholders(self):
        self.assertEqual(leaks(self.example().read_text()), [])
        self.assertIn("placeholder", sdkkit._docstring(self.example()))


# --------------------------------------------------------------------------- #
# AUTO-15
# --------------------------------------------------------------------------- #

CLAY_DOC = ('<scene><project width="8" height="8" fps="30" duration="1"/><symbols><symbol id="w">'
            '<object3D id="o" primitive="clay" resolution="96">'
            + '<blob x="0" y="0" z="0"/>' * 1690 +
            '</object3D><camera id="c" renderer="pathtrace" pathSamples="128" maxBounces="6"/>'
            '</symbol></symbols></scene>')


class StillBudget(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="kit-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.doc = self.d / "a.scene.xml"
        self.doc.write_text(CLAY_DOC)

    def test_the_budget_is_well_under_the_ceiling(self):
        self.assertLessEqual(sdkkit.STILL_BUDGET, 0.75 * sdkkit.STILL_DEADLINE)

    def test_blobs_and_cells_are_cost_drivers_in_the_settings_phrase(self):
        phrase = sdkkit.render_settings(CLAY_DOC)
        self.assertIn("1690 blobs", phrase)
        self.assertIn("96 cells", phrase)
        self.assertIn("pathtrace, 128 samples, 6 bounces", phrase)
        self.assertEqual(sdkkit.render_settings('<camera renderer="raster"/>'), "raster")

    def test_the_kit_budget_is_the_engines_timeout(self):
        with mock.patch("ytstudio.render.engine_render_still",
                        return_value=(png_bytes(), "ok")) as render:
            sdkkit.frame(self.doc, 0.5, host="h7")
            sdkkit.frame(self.doc, 0.5, host="h7", budget=sdkkit.STILL_DEADLINE)
        self.assertEqual([c.kwargs["timeout"] for c in render.call_args_list],
                         [sdkkit.STILL_BUDGET, sdkkit.STILL_DEADLINE])

    def test_the_wait_for_the_host_is_reported_apart_from_the_render(self):
        # asked at 0, the lock is free at 30, the render ends at 34
        ticks = iter([0.0, 30.0, 34.0])
        with mock.patch("ytstudio.render.engine_render_still", return_value=(png_bytes(), "ok")), \
                mock.patch.object(sdkkit.time, "monotonic", side_effect=lambda: next(ticks)):
            path, findings = sdkkit.frame(self.doc, 0.5, host="h7")
        self.assertIsNotNone(path)
        notes = " ".join(findings)
        self.assertIn(f"{sdkkit.RENDER_NOTE} 4 s on h7", notes)
        self.assertIn("waited 30 s for the host", notes)

    def test_no_wait_note_when_the_host_was_free(self):
        ticks = iter([0.0, 0.2, 3.0])
        with mock.patch("ytstudio.render.engine_render_still", return_value=(png_bytes(), "ok")), \
                mock.patch.object(sdkkit.time, "monotonic", side_effect=lambda: next(ticks)):
            _, findings = sdkkit.frame(self.doc, 0.5, host="h7")
        self.assertNotIn("waited", " ".join(findings))

    def test_a_still_killed_at_the_budget_names_its_cost_driver(self):
        ticks = iter([0.0, 0.0, float(sdkkit.STILL_BUDGET)])
        with mock.patch("ytstudio.render.engine_render_still",
                        return_value=(None, "render failed\nerror: exit 124")), \
                mock.patch.object(sdkkit.time, "monotonic", side_effect=lambda: next(ticks)):
            path, findings = sdkkit.frame(self.doc, 0.5, host="h7")
        self.assertIsNone(path)
        first = findings[0]
        self.assertTrue(first.startswith("error: frame at 0.5s of a.scene.xml"), first)
        self.assertIn(f"{sdkkit.STILL_BUDGET} s budget", first)
        self.assertIn("1690 blobs", first)
        self.assertIn("128 samples", first)

    def test_a_local_render_that_raises_the_timeout_is_the_same_message(self):
        boom = subprocess.TimeoutExpired("sr", sdkkit.STILL_BUDGET)
        with mock.patch("ytstudio.render.engine_render_still", side_effect=boom):
            path, findings = sdkkit.frame(self.doc, 0.5, host="h7")
        self.assertIsNone(path)
        self.assertIn("1690 blobs", findings[0])

    def test_a_fast_failure_is_not_blamed_on_the_budget(self):
        with mock.patch("ytstudio.render.engine_render_still",
                        return_value=(None, "render failed\nerror: bad document")):
            path, findings = sdkkit.frame(self.doc, 0.5, host="h7")
        self.assertIsNone(path)
        self.assertNotIn("budget", findings[0])

    def test_probe_frames_keep_the_full_deadline(self):
        ws = Path(tempfile.mkdtemp(prefix="probe-"))
        self.addCleanup(shutil.rmtree, ws, True)
        sdkkit.install(ws, frame=probes.FRAME)
        (ws / "brief.md").write_text("b")
        (ws / probes.PROGRAM).write_text("x = 1\n")
        (ws / probes.DOCUMENT).write_text("<scene/>")
        seen = []

        def fake_frame(doc, t, **kw):
            seen.append(kw.get("budget"))
            return None, []
        with mock.patch.object(sdkkit, "build", return_value={"ok": True, "documents": []}), \
                mock.patch.object(sdkkit, "frame", side_effect=fake_frame):
            probes.verify(ws, probes.PROBES[0], host="h")
        self.assertTrue(seen)
        self.assertEqual(set(seen), {sdkkit.STILL_DEADLINE})

    def test_engine_md_states_the_kit_budget(self):
        text = sdkkit.engine_text({"manifest": MANIFEST}, [])
        self.assertIn(f"{sdkkit.STILL_BUDGET} s", text)


# --------------------------------------------------------------------------- #
# AUTO-16
# --------------------------------------------------------------------------- #

class Clock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class ScriptedEngine:
    """Streams scripted rounds; a round may raise the engine's timeout."""

    def __init__(self, clock: Clock, script: list, timeout: float = 3600.0):
        self.config = type("C", (), {})()
        self.config.timeout = timeout
        self.config.env = {"KEEP": "1"}
        self.clock, self.script, self.calls = clock, list(script), []
        self.session_id = None
        self.usage = Usage()

    def stream(self, prompt, system=None, resume=False):
        self.calls.append({"prompt": prompt, "resume": resume, "timeout": self.config.timeout,
                           "env": dict(self.config.env)})
        self.session_id = self.session_id or "sess"
        step = self.script.pop(0)
        self.clock.t += step.get("advance", 0)
        yield Event(type="assistant", text=step.get("text", "working"))
        if step.get("fail"):
            raise ClaudeError(step["fail"])


class Clocked(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        patcher = mock.patch.object(sdkkit, "now", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_round(self, engine, **kw):
        return [e.text for e in sdkkit.stream_round(engine, "do it", system="sys", **kw)]

    def test_the_session_timeout_is_the_80_percent_mark_of_the_stated_budget(self):
        self.assertEqual(sdkkit.CUTOFF, 0.8)
        self.assertEqual(sdkkit.round_budget(3600.0), 4500.0)

    def test_the_deadline_reaches_the_kit_through_the_session_env(self):
        engine = ScriptedEngine(self.clock, [{}])
        self.run_round(engine)
        self.assertEqual(engine.calls[0]["env"]["SRKIT_DEADLINE"], f"{self.clock.t + 4500:.0f}")
        self.assertEqual(engine.calls[0]["env"]["KEEP"], "1")
        self.assertEqual(engine.calls[0]["timeout"], 3600.0)

    def test_a_round_that_reaches_80_percent_is_resumed_to_build_and_render_now(self):
        engine = ScriptedEngine(self.clock, [
            {"advance": 3600, "fail": "claude timed out after 3600.0s"}, {"advance": 200}])
        texts = self.run_round(engine, finish="FINISH build and render")
        self.assertEqual(len(engine.calls), 2)
        second = engine.calls[1]
        self.assertTrue(second["resume"])
        self.assertIn("FINISH build and render", second["prompt"])
        self.assertEqual(second["timeout"], 900.0)               # what is left of the budget
        self.assertEqual(second["env"]["SRKIT_DEADLINE"], engine.calls[0]["env"]["SRKIT_DEADLINE"])
        self.assertEqual(engine.config.timeout, 3600.0)          # restored for the next round
        self.assertEqual(texts, ["working", "working"])

    def test_the_default_finish_prompt_asks_for_the_build_and_a_still(self):
        engine = ScriptedEngine(self.clock, [
            {"advance": 3600, "fail": "claude timed out after 3600.0s"}, {}])
        self.run_round(engine)
        text = engine.calls[1]["prompt"]
        self.assertIn("kit.py build", text)
        self.assertIn("frame", text)
        self.assertIn("now", text)

    def test_a_failure_before_the_mark_is_not_a_cutoff(self):
        engine = ScriptedEngine(self.clock, [{"advance": 60, "fail": "claude timed out after 3600.0s"}])
        with self.assertRaises(ClaudeError):
            self.run_round(engine)
        self.assertEqual(len(engine.calls), 1)

    def test_a_failure_that_is_not_a_timeout_is_raised(self):
        engine = ScriptedEngine(self.clock, [{"advance": 3600, "fail": "the model is down"}])
        with self.assertRaises(ClaudeError):
            self.run_round(engine)
        self.assertEqual(len(engine.calls), 1)

    def test_no_session_means_nothing_to_resume(self):
        engine = ScriptedEngine(self.clock, [{"advance": 3600, "fail": "claude timed out"}])
        engine.stream = lambda *a, **k: (_ for _ in ()).throw(ClaudeError("claude timed out"))
        self.clock.t += 0
        with self.assertRaises(ClaudeError):
            self.run_round(engine)

    def test_a_finish_that_times_out_too_is_raised_and_the_timeout_restored(self):
        engine = ScriptedEngine(self.clock, [
            {"advance": 3600, "fail": "claude timed out after 3600.0s"},
            {"advance": 900, "fail": "claude timed out after 900.0s"}])
        with self.assertRaises(ClaudeError):
            self.run_round(engine)
        self.assertEqual(engine.config.timeout, 3600.0)

    def test_no_time_left_to_finish_raises_instead_of_a_call_that_cannot_run(self):
        engine = ScriptedEngine(self.clock, [{"advance": 4480, "fail": "claude timed out"}])
        with self.assertRaises(ClaudeError):
            self.run_round(engine)

    def test_a_revision_resume_is_passed_through(self):
        engine = ScriptedEngine(self.clock, [{}])
        self.run_round(engine, resume=True)
        self.assertTrue(engine.calls[0]["resume"])
        engine = ScriptedEngine(self.clock, [{}])
        self.run_round(engine)
        self.assertFalse(engine.calls[0]["resume"])

    def test_time_left_is_printed_from_the_deadline(self):
        env = {"SRKIT_DEADLINE": f"{self.clock.t + 754:.0f}"}
        self.assertEqual(sdkkit.time_left_line(env), "time left: 12 min 34 s of this round; "
                         "keep a program that builds and a still you have looked at")
        self.assertIn("past the deadline",
                      sdkkit.time_left_line({"SRKIT_DEADLINE": f"{self.clock.t - 5:.0f}"}))
        self.assertEqual(sdkkit.time_left_line({}), "")

    def test_kit_build_and_frame_print_the_time_left(self):
        d = Path(tempfile.mkdtemp(prefix="kit-"))
        self.addCleanup(shutil.rmtree, d, True)
        sdkkit.install(d, frame=(64, 36))
        prog = d / "compose.py"
        prog.write_text('from srkit import FRAME, S, save\nscene = S.Scene(version="auto")\n'
                        'scene.add_project(width=64, height=36, fps="30", duration=1)\n'
                        'scene.add_composition()\nsave(scene, "m.scene.xml")\n')
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"SRKIT_DEADLINE": f"{self.clock.t + 600:.0f}"}), \
                mock.patch("ytstudio.render.engine_validate", return_value=[]), \
                mock.patch("sys.stdout", out):
            sdkkit.main(["build", str(prog)])
        self.assertIn("time left: 10 min 0 s", out.getvalue())
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"SRKIT_DEADLINE": f"{self.clock.t + 120:.0f}"}), \
                mock.patch.object(sdkkit, "frame", return_value=(None, ["error: x"])), \
                mock.patch("sys.stdout", out):
            sdkkit.main(["frame", str(d / "m.scene.xml"), "--at", "0"])
        self.assertIn("time left: 2 min 0 s", out.getvalue())

    def test_the_briefs_state_the_time_budget(self):
        line = sdkkit.time_line(3600.0)
        self.assertIn("75 minutes", line)
        self.assertIn("60 minutes", line)
        self.assertIn("SRKIT_DEADLINE", sdkkit.BRIEF)
        text = probes.brief_text(probes.PROBES[0], props=[])
        self.assertIn(sdkkit.time_line(probes.AUTHOR_TIMEOUT), text)
        self.assertIn(sdkkit.time_line(vfx.AUTHOR_TIMEOUT).split("\n")[2][:20], line)


class Wired(Clocked):
    """Every author stage goes through the shared round, so probes measure the same system."""

    def test_probes_author_gets_a_still_when_cut_off_at_80_percent(self):
        engines = []

        class Fake(ScriptedEngine):
            def __init__(inner, config):
                ScriptedEngine.__init__(inner, self.clock, [
                    {"advance": 1800, "fail": "claude timed out after 1800.0s"}, {}],
                    timeout=config.timeout)
                inner.config = config
                engines.append(inner)
        ws = Path(tempfile.mkdtemp(prefix="probe-"))
        self.addCleanup(shutil.rmtree, ws, True)
        with mock.patch.object(harness, "ENGINE", Fake):
            out = probes.author(ws, probes.PROBES[0], model="sonnet", effort=None,
                                budget_usd=1, verbose=False)
        self.assertEqual(out["error"], "")
        self.assertEqual(len(engines[0].calls), 2)
        self.assertIn("kit.py", engines[0].calls[1]["prompt"])

    def test_keyframe_stage_asks_for_the_still_of_a_cut_off_author(self):
        from tests import test_keyframe as tk
        clock = self.clock

        class Cut(tk.FakeEngine):
            def stream(inner, prompt, system=None, resume=False):
                tools = tuple(inner.config.tools or ())
                authored = sum(1 for c in tk.FakeEngine.stream_calls if "Write" in c["tools"])
                if "Write" in tools and authored == 0:
                    tk.FakeEngine.stream_calls.append({
                        "prompt": prompt, "system": system, "tools": tools,
                        "cwd": inner.config.cwd, "resume": resume})
                    inner.session_id = "author-session"
                    clock.t += float(inner.config.timeout)
                    raise harness.ClaudeError(f"claude timed out after {inner.config.timeout}s")
                yield from tk.FakeEngine.stream(inner, prompt, system, resume)
        base = tk.Loop("test_the_review_reads_the_frame_and_scores_the_four_categories")
        base.setUp()
        self.addCleanup(base.tearDown)
        harness.ENGINE = Cut
        record = base.run_stage()
        authors = base.authors()
        self.assertEqual(len(authors), 2)
        self.assertTrue(authors[1]["resume"])
        self.assertIn(keyframe.PROGRAM_NAME, authors[1]["prompt"])
        self.assertEqual(record["score"], 8)               # the review still happened


class VfxRound(Clocked):
    def test_a_round_cut_off_at_80_percent_still_gets_its_still_and_its_review(self):
        from tests import test_vfx_world as world
        clock = self.clock

        class Cut(world.FakeEngine):
            def stream(inner, prompt, system=None, resume=False):
                tools = tuple(inner.config.tools or ())
                authored = sum(1 for c in world.FakeEngine.stream_calls if "Write" in c["tools"])
                if "Write" in tools and authored == 0:
                    world.FakeEngine.stream_calls.append({
                        "prompt": prompt, "system": system, "tools": tools,
                        "cwd": inner.config.cwd, "timeout": inner.config.timeout,
                        "resume": resume})
                    inner.session_id = "author-session"
                    clock.t += vfx.AUTHOR_TIMEOUT
                    raise harness.ClaudeError(f"claude timed out after {vfx.AUTHOR_TIMEOUT}s")
                yield from world.FakeEngine.stream(inner, prompt, system, resume)
        base = world.Review("test_a_score_at_the_bar_needs_no_revision")
        base.setUp()
        self.addCleanup(base.tearDown)
        harness.ENGINE = Cut
        world.FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "reads"}]
        doc = base.author()[0]
        authors = [c for c in world.FakeEngine.stream_calls if "Write" in c["tools"]]
        self.assertEqual(len(authors), 2)
        self.assertTrue(authors[1]["resume"])
        self.assertIn(doc["file"].rsplit("/", 1)[1], authors[1]["prompt"])
        self.assertEqual(doc["score"], 8)                  # a review happened
        self.assertFalse(doc["rounds"][0]["timed_out"])


# --------------------------------------------------------------------------- #
# AUTO-77
# --------------------------------------------------------------------------- #

#: An approved look as `yt keyframe` keeps it: constants, a helper, and the
#: top-level build and save of its own section.
LOOK = '''"""Section 1: the look."""
from srkit import FRAME, S, save

INK = "#F2F5F7"
DURATION = 3


def project(scene):
    """The project every section opens with."""
    scene.add_project(width=FRAME["width"], height=FRAME["height"], fps=str(FRAME["fps"]),
                      duration=DURATION)


scene = S.Scene(version="auto")
project(scene)
scene.add_composition()
save(scene, "01-open.scene.xml")
'''

#: Another section's program, composed against the look's module.
SECTION = '''from srkit import S, save
from look import project

scene = S.Scene(version="auto")
project(scene)
scene.add_composition()
save(scene, "02-two.scene.xml")
'''

#: A look that wraps `save` in a helper it ends with, as the brief lets it.
LOOK_HELPER = LOOK.replace('save(scene, "01-open.scene.xml")', '''

def finish(scene, name):
    """What every section ends with."""
    return save(scene, name)


finish(scene, "01-open.scene.xml")''')

#: A section that saves through the look's helper, redefining nothing.
SECTION_HELPER = '''from srkit import S
from look import project, finish

scene = S.Scene(version="auto")
project(scene)
scene.add_composition()
finish(scene, "02-two.scene.xml")
'''


class LookModule(KitCase):
    """The approved look is an importable module in the workspace, and the
    build's provenance carries the hash of every workspace module a program
    imported, so a hand edit to the look lapses it."""

    def setUp(self):
        super().setUp()
        self.approved = self.dir.parent / f"{self.dir.name}-keyframe" / "look.py"
        self.approved.parent.mkdir()
        self.addCleanup(shutil.rmtree, self.approved.parent, True)
        self.approved.write_text(LOOK, encoding="utf-8")

    def test_install_copies_the_approved_look_byte_for_byte(self):
        installed = sdkkit.install(self.dir, look=self.approved)
        self.assertEqual((self.dir / sdkkit.LOOK_MODULE).read_bytes(), self.approved.read_bytes())
        self.assertEqual(installed["look"], sdkkit.LOOK_MODULE)
        self.assertFalse((self.dir / "01-open.scene.xml").exists())

    def test_no_look_means_no_module(self):
        self.assertFalse((self.dir / sdkkit.LOOK_MODULE).exists())
        self.assertIsNone(sdkkit.install(self.dir)["look"])
        self.assertIsNone(sdkkit.install(self.dir, look=self.approved.parent / "gone.py")["look"])
        self.assertFalse((self.dir / sdkkit.LOOK_MODULE).exists())

    def test_a_section_program_importing_look_builds_with_the_looks_hash_in_provenance(self):
        sdkkit.install(self.dir, look=self.approved)
        result = sdkkit.build(self.program(SECTION, "02-two.py"), validate=False)
        self.assertTrue(result["ok"], result.get("error"))
        # the look's own save ran at import and wrote nothing: one document, the program's
        self.assertEqual([d["path"] for d in result["documents"]], ["02-two.scene.xml"])
        self.assertFalse((self.dir / "01-open.scene.xml").exists())
        record = sdkkit.provenance_of(self.dir / "02-two.scene.xml")
        self.assertEqual(record["imports"][sdkkit.LOOK_MODULE],
                         hashlib.sha256(LOOK.encode("utf-8")).hexdigest())
        self.assertIn("srkit.py", record["imports"])        # every workspace module it loaded
        self.assertNotIn("02-two.py", record["imports"])    # the program is `program_sha256`

    def test_a_section_saving_through_a_look_helper_writes_its_document(self):
        # the guard is the import, not the caller: a save reached through a
        # function the look defines writes when the program is what runs
        self.approved.write_text(LOOK_HELPER, encoding="utf-8")
        sdkkit.install(self.dir, look=self.approved)
        result = sdkkit.build(self.program(SECTION_HELPER, "02-two.py"), validate=False)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual([d["path"] for d in result["documents"]], ["02-two.scene.xml"])
        self.assertFalse((self.dir / "01-open.scene.xml").exists())  # the look's own finish, at import
        record = sdkkit.provenance_of(self.dir / "02-two.scene.xml")
        self.assertEqual(record["imports"][sdkkit.LOOK_MODULE],
                         hashlib.sha256(LOOK_HELPER.encode("utf-8")).hexdigest())

    def test_a_changed_look_lapses_the_provenance(self):
        sdkkit.install(self.dir, look=self.approved)
        sdkkit.build(self.program(SECTION, "02-two.py"), validate=False)
        doc = self.dir / "02-two.scene.xml"
        self.assertIsNotNone(sdkkit.provenance_of(doc))
        look = self.dir / sdkkit.LOOK_MODULE
        look.write_text(look.read_text().replace('"#F2F5F7"', '"#FFFFFF"'), encoding="utf-8")
        self.assertIsNone(sdkkit.provenance_of(doc))
        look.unlink()
        self.assertIsNone(sdkkit.provenance_of(doc))        # gone is no better than changed

    def test_a_changed_kit_lapses_the_provenance_by_design(self):
        # srkit.py is among the imports: a kit release re-installed into a
        # workspace lapses what was built with the old one, carried sections too
        sdkkit.install(self.dir, look=self.approved)
        sdkkit.build(self.program(SECTION, "02-two.py"), validate=False)
        doc = self.dir / "02-two.scene.xml"
        kit = self.dir / "srkit.py"
        kit.write_text(kit.read_text() + "\n# a release\n", encoding="utf-8")
        self.assertIsNone(sdkkit.provenance_of(doc))
        sdkkit.build(self.program(SECTION, "02-two.py"), validate=False)   # rebuilt: current again
        self.assertIsNotNone(sdkkit.provenance_of(doc))

    def test_a_program_that_imports_nothing_records_only_the_kit(self):
        from tests.test_sdkkit import MINIMAL
        sdkkit.build(self.program(MINIMAL), validate=False)
        record = sdkkit.provenance_of(self.dir / "minimal.scene.xml")
        self.assertEqual(list(record["imports"]), ["srkit.py"])

    def test_the_brief_says_import_and_srkit_says_when_save_writes(self):
        self.assertIn("import", sdkkit.LOOK_BRIEF)
        self.assertIn("redefine nothing", sdkkit.LOOK_BRIEF)
        self.assertIn("__main__", (self.dir / "srkit.py").read_text())


if __name__ == "__main__":
    unittest.main()
