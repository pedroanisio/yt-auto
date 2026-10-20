"""Regression tests for the parts of ytstudio that do not need a model or the web.

    python -m unittest discover -s tests -v

These pin down behaviour that was wrong once: the licence gate, how --title
resolves, the shared primitives, the download bookkeeping, the VFX arithmetic.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("YT_NO_REMOTE", "1")      # the suite never reaches a render host

from ytstudio import assets, core, research, vfx, workspace  # noqa: E402


class LicencePolicy(unittest.TestCase):
    """One verdict, read by the catalogue search and the download gate alike."""

    USABLE = ["Public domain (NASA)", "CC BY 4.0", "Attribution", "PDM", "CC0 1.0",
              "NASA media usage guidelines", "ESA/Hubble", "Unsplash License",
              "no known copyright", "Creative Commons Attribution 3.0 Unported",
              "CC PDM 1.0"]
    REFUSED = ["CC BY-SA 2.0", "cc-by-sa 3.0", "CC BY-NC 4.0", "CC BY-ND 4.0",
               "Rights managed", "Editorial use only",
               "arXiv non-exclusive distribution licence",
               "Unknown; media terms not opened", "Attribution-NonCommercial",
               "Attribution-ShareAlike 3.0", ""]

    def test_modules_agree(self):
        for licence in self.USABLE + self.REFUSED:
            with self.subTest(licence=licence):
                gate = research.is_free({"licence": licence})[0]
                search = assets.licence_tier(licence) is not None
                self.assertEqual(gate, search)
                self.assertEqual(gate, licence in self.USABLE)

    def test_veto_beats_the_models_flag(self):
        self.assertFalse(research.is_free({"licence": "CC BY-NC 4.0",
                                           "commercial_ok": True})[0])

    def test_unknown_string_defers_to_the_flag(self):
        self.assertTrue(research.is_free({"licence": "Museum terms",
                                          "commercial_ok": True})[0])
        self.assertFalse(research.is_free({"licence": "Museum terms"})[0])

    def test_verified_licence_outranks_claimed(self):
        self.assertFalse(research.is_free({"licence": "Public domain",
                                           "licence_verified": "CC BY-NC 2.0"})[0])

    def test_share_alike_only_on_request(self):
        self.assertIsNone(assets.licence_tier("CC BY-SA 4.0"))
        self.assertEqual(assets.licence_tier("CC BY-SA 4.0", allow_sa=True),
                         assets.TIER_SHARE_ALIKE)
        self.assertTrue(research.is_free({"licence": "CC BY-SA 4.0"}, allow_sa=True)[0])
        self.assertIsNone(assets.licence_tier("CC BY-NC-SA 4.0", allow_sa=True))


class Primitives(unittest.TestCase):

    def test_timecode(self):
        self.assertEqual(core.timecode(1070), "17:50")
        self.assertEqual(core.timecode(59.6), "1:00")

    def test_slugify_folds_accents_and_cuts_on_a_word(self):
        self.assertEqual(core.slugify("Explicação Visual"), "explicacao-visual")
        self.assertEqual(core.slugify("a" * 30 + " " + "b" * 50, limit=40), "a" * 30)

    def test_compact_drops_empty_fields(self):
        self.assertEqual(core.compact(core.Idea("t", "g", "d")),
                         {"title": "t", "genre": "g", "description": "d"})

    def test_read_json_respects_shape(self):
        path = Path(tempfile.mkdtemp()) / "m.json"
        path.write_text("[1, 2]", encoding="utf-8")
        self.assertIsNone(core.read_json(path, warn=False))
        self.assertEqual(core.read_json(path, warn=False, shape=list), [1, 2])
        self.assertIsNone(core.read_json(None))

    def test_idea_block_carries_every_field(self):
        block = core.Idea("t", "g", "d", hook="h", length="9 min",
                          keywords=["k"]).block()
        for needle in ("Planned hook", "Target length", "Search intent"):
            self.assertIn(needle, block)


class TitleResolution(unittest.TestCase):
    """What `--title` means, with and without an ideas.json in the way."""

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="yt-test-"))
        self.ideas = [{"title": "Why Lithography Machines Cost So Much",
                       "genre": "explainer", "description": "x"},
                      {"title": "The Ship That Moved Chips", "genre": "essay",
                       "description": "y"}]
        (self.work / "ideas.json").write_text(json.dumps(self.ideas), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def args(self, **kw):
        base = dict(work=str(self.work), project=None, source=None, index=None,
                    title=None, stdin=None, genre=None, description=None)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_substring_matches_an_idea(self):
        idea, _ = workspace.resolve(self.args(title="lithography"))
        self.assertEqual(idea.title, self.ideas[0]["title"])

    def test_unmatched_title_becomes_its_own_project(self):
        idea, project = workspace.resolve(self.args(title="A Brand New Title"))
        self.assertEqual(idea.title, "A Brand New Title")
        self.assertTrue(project.root.is_dir())
        self.assertEqual(project.root.name, "a-brand-new-title")

    def test_explicit_from_without_a_match_is_an_error(self):
        with self.assertRaises(SystemExit):
            workspace.resolve(self.args(source=str(self.work / "ideas.json"),
                                        title="nothing like this"))

    def test_index_out_of_range_is_a_clean_error(self):
        with self.assertRaises(SystemExit) as caught:
            workspace.resolve(self.args(source=str(self.work / "ideas.json"), index=9))
        self.assertIn("out of range", str(caught.exception))

    def test_missing_from_file_is_a_clean_error(self):
        with self.assertRaises(SystemExit) as caught:
            workspace.resolve(self.args(source=str(self.work / "nope.json")))
        self.assertIn("cannot read", str(caught.exception))

    def test_existing_project_wins_over_the_ideas_file(self):
        first, project = workspace.resolve(self.args(title="lithography"))
        again, same = workspace.resolve(self.args(title="lithography"))
        self.assertEqual(project.root, same.root)
        self.assertEqual(first, again)


class Downloads(unittest.TestCase):

    def test_arxiv_abstract_page_becomes_the_pdf(self):
        self.assertEqual(research.direct_file_url("https://arxiv.org/abs/1805.07283"),
                         "https://arxiv.org/pdf/1805.07283")

    def test_filename_extension_is_alphabetic(self):
        self.assertTrue(research._filename("https://arxiv.org/pdf/1805.07283", 3,
                                           "application/pdf").endswith(".pdf"))
        self.assertTrue(research._filename("https://x/a/Photo.JPG", 1,
                                           "image/jpeg").endswith("Photo.JPG"))

    def test_cooldown_doubles_and_clears(self):
        cooldown = research.HostCooldown(Path(tempfile.mkdtemp()) / "cd.json")
        waits = [cooldown.strike("host") for _ in range(4)]
        self.assertEqual(waits, [900, 1800, 3600, 7200])
        self.assertGreater(cooldown.remaining("host"), 0)
        cooldown.clear("host")
        self.assertEqual(cooldown.remaining("host"), 0)


class Catalogues(unittest.TestCase):

    def test_pdf_is_a_document(self):
        self.assertEqual(assets.kind_for("application/pdf"), "document")
        self.assertEqual(assets.kind_for("image/svg+xml"), "image")

    def test_long_queries_shorten_to_keywords(self):
        self.assertEqual(assets.shorten("A real close-up shot showing the tin "
                                        "droplet being flattened"),
                         "close-up tin droplet flattened")

    def test_relevance_outranks_resolution(self):
        junk = assets.AssetHit("Binoculars Zeiss", "u1", "openverse", "CC PDM 1.0",
                               width=4000, height=3000, rank=3, query="zeiss mirror")
        hit = assets.AssetHit("EUV multilayer mirror", "u2", "commons", "CC BY 4.0",
                              width=600, height=400, rank=0, query="zeiss mirror")
        junk.verified = hit.verified = True
        ranked = sorted([junk, hit], key=lambda h: assets.score(h, []))
        self.assertEqual(ranked[0].title, "EUV multilayer mirror")


class VfxArithmetic(unittest.TestCase):

    def effect(self, name, template="", hours=4.0):
        return {"name": name, "type": "2d-diagram", "difficulty": 2,
                "hours": hours, "seconds": 8, "template": template,
                "conveys": "c", "on_screen": "o", "technique": [], "section": "S"}

    def test_reuse_is_counted_from_shared_template_names(self):
        sections = [{"section": "S", "seconds": 60, "effects": [
            self.effect("a", "Log-scale ladder"), self.effect("b", "log-scale ladder "),
            self.effect("c")]}]
        totals = vfx.summarise(sections)
        self.assertEqual(totals["reusable"], 2)
        self.assertEqual(list(totals["templates"]), ["log-scale ladder"])
        self.assertEqual(totals["hours"], 12.0)

    def test_tokens_come_from_the_palette_roles(self):
        style = {"palette": [{"name": "Fundo", "hex": "#14213D", "use": "fundo da tela"},
                             {"name": "Texto", "hex": "#F2F5F7", "use": "texto e rótulos"},
                             {"name": "Destaque", "hex": "#3DD6FF", "use": "o número principal"},
                             {"name": "Apoio", "hex": "#8E9BB0", "use": "grade e apoio"}]}
        tokens = vfx.tokens_from(style)
        self.assertEqual(tokens["background"], "#14213D")
        self.assertEqual(tokens["ink"], "#F2F5F7")
        self.assertEqual(tokens["accent"], "#3DD6FF")
        self.assertEqual(tokens["muted"], "#8E9BB0")
        self.assertNotIn("#xyz", vfx.tokens_from({"palette": [{"hex": "#xyz", "use": "bg"}]}).values())

    def test_workspace_is_generated_not_hand_written(self):
        root = Path(tempfile.mkdtemp())
        ws = vfx.Workspace(root)
        ws.prepare(tokens={"background": "#101418", "ink": "#FFFFFF"}, frame=(1920, 1080))
        sheet = (ws.dir / "schema.md").read_text(encoding="utf-8")
        self.assertIn("## <textAnimator>", sheet)
        self.assertIn("counter", sheet)                 # the preset the engine has
        self.assertIn("## every element", sheet)
        self.assertIn('<token name="ink" value="#FFFFFF"/>', (ws.dir / "tokens.xml").read_text())
        self.assertTrue((ws.dir / "sr.py").is_file())
        # the examples document is itself a valid scene-render document
        from ytstudio.scene_sdk import sdk
        doc = sdk().read((ws.dir / "examples.scene.xml").read_bytes())
        self.assertFalse([f for f in doc.check() if str(f).startswith("error")])

    def test_scene_map_reads_what_was_written(self):
        root = Path(tempfile.mkdtemp())
        ws = vfx.Workspace(root)
        from ytstudio.scene_sdk import sdk
        S = sdk()
        sc = S.Scene(version="auto")
        sc.add_project(width=1920, height=1080, fps="30", duration=4, background="#000000")
        syms = sc.add_symbols()
        syms.add_symbol(id="fx-a", width=1920, height=1080).add_shape(
            id="a1", shape="rect", x=0, y=0, width=10, height=10, fill="#FFFFFF")
        sc.add_composition().add_instance(id="u", symbol="fx-a")
        sc.add_output(path="p.mp4", codec="h264")
        section = {"heading": "Open"}
        name = ws.doc_name(1, section)
        (ws.dir / name).write_bytes(sc.to_bytes(layout="pretty"))
        symbols, findings = ws.validate(name, frames=False)
        self.assertEqual(symbols, ["fx-a"])
        self.assertFalse([f for f in findings if f.startswith("error")])
        effects = [{"section": "Open", "effects": [
            {"id": "fx-a", "name": "A", "shots": ["1.1", "1.2"]},
            {"id": "fx-b", "name": "B", "shots": ["1.3"]}]}]
        authored = [{"n": 1, "section": "Open", "file": f"vfx/{name}",
                     "symbols": symbols, "findings": findings, "tool_calls": 0}]
        planner = vfx.VfxPlanner.__new__(vfx.VfxPlanner)
        scene = vfx.VfxPlanner.scene_map(planner, effects, authored)
        self.assertEqual(scene["by_label"], {"1.1": {"file": f"vfx/{name}", "symbol": "fx-a"},
                                             "1.2": {"file": f"vfx/{name}", "symbol": "fx-a"}})
        self.assertIn("fx-b", scene["dropped"])

if __name__ == "__main__":
    unittest.main()


class FrameGate(unittest.TestCase):
    """Rendered frames are judged by the tooling before the author looks."""

    @staticmethod
    def _png(pattern: str) -> bytes:
        import io
        from PIL import Image
        im = Image.new("RGB", (64, 36), (8, 8, 8))
        if pattern == "varied":
            for x in range(64):
                for y in range(36):
                    im.putpixel((x, y), ((x * 4) % 256, (y * 7) % 256, 120))
        elif pattern == "speck":
            im.putpixel((3, 3), (255, 255, 255))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()

    def test_black_frame_is_an_error(self):
        found = vfx.judge_frame(self._png("black"), 2.0, "sym")
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("error"))
        self.assertIn("black", found[0])

    def test_varied_frame_passes(self):
        self.assertEqual(vfx.judge_frame(self._png("varied"), 2.0, "sym"), [])

    def test_nearly_flat_frame_is_a_warning(self):
        found = vfx.judge_frame(self._png("speck"), 2.0, "sym")
        self.assertTrue(found and found[0].startswith("warning"))

    def test_proof_document_hoists_lights_and_includes_the_symbol(self):
        doc = ('<scene version="1.2"><project width="1920" height="1080" fps="30" '
               'duration="6" background="#101010"/><symbols><symbol id="fx" width="1920" '
               'height="1080" duration="4" background="#202020"/></symbols><composition/>'
               '<lights><light id="key" type="point" intensity="3"/></lights></scene>')
        facts = vfx.symbol_facts(doc)
        self.assertEqual(facts, [{"id": "fx", "duration": 4.0, "background": "#202020"}])
        proof = vfx.proof_document("01-x.scene.xml", doc, "fx", (1280, 720), 4.0, "#202020")
        self.assertIn('<include id="shot" src="01-x.scene.xml" symbol="fx"/>', proof)
        self.assertIn('<light id="key"', proof)
        self.assertIn('width="1280" height="720"', proof)
        self.assertLess(proof.index("</composition>"), proof.index("<lights>"))

    def test_frame_times_stay_inside_the_symbol(self):
        self.assertEqual(vfx.frame_times(0.5), [0.25])
        times = vfx.frame_times(10)
        self.assertEqual(len(times), 3)
        self.assertTrue(all(0 < t < 10 for t in times))


class RenderRouting(unittest.TestCase):
    """docs/compute.md: memory decides, not the GPU; slices cross machines."""

    def setUp(self):
        from ytstudio import render
        self.render = render
        self.big = render.Host("root@big", engine="/x/scene-render", ram_gb=125, cores=12, gpu="3050")
        self.small = render.Host("root@small", engine="/x/scene-render", ram_gb=15, cores=20, gpu="3070")
        self.dead = render.Host("root@dead", engine="", error="refused")

    def test_encode_goes_to_memory_and_stills_to_the_other(self):
        hosts = [self.small, self.big, self.dead]
        self.assertIs(self.render.pick_host(hosts, "encode"), self.big)
        self.assertIs(self.render.pick_host(hosts, "audio"), self.big)
        self.assertIs(self.render.pick_host(hosts, "still"), self.small)
        self.assertIsNone(self.render.pick_host([self.dead], "encode"))

    def test_host_names_merge_flag_and_env(self):
        import os
        args = argparse.Namespace(host="root@c", local=False)
        old = dict(os.environ)
        try:
            os.environ.pop("YT_NO_REMOTE", None)       # host_names is pure; only the flag hides hosts
            os.environ["RS_RENDER_HOSTS"] = "root@a, root@b"
            os.environ["RS_RENDER_HOST"] = "root@a"
            self.assertEqual(self.render.host_names(args), ["root@c", "root@a", "root@b"])
            self.assertEqual(self.render.host_names(argparse.Namespace(host=None, local=True)), [])
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_slices_cover_the_range_exactly(self):
        ranges = self.render._slice_ranges(0.0, 30.5, 4)
        self.assertEqual(ranges[0][0], 0.0)
        self.assertEqual(ranges[-1][1], 30.5)
        for (a, b), (c, d) in zip(ranges, ranges[1:]):
            self.assertEqual(b, c)

    def test_still_message_reads_diagnostics_and_findings(self):
        report = json.dumps({"diagnostics": [{"code": "E12", "severity": "warning",
                                              "message": "include carries no lights"}],
                             "result": {"warnings": 1, "findings": [
                                 {"severity": "warning", "message": "drop is off camera"}]},
                             "error": None})
        msg = self.render._still_message(0, report, "", "rendered on x")
        self.assertEqual(msg.splitlines()[0], "rendered on x")
        self.assertIn("warning[E12]: include carries no lights", msg)
        self.assertIn("warning: drop is off camera", msg)
        failed = self.render._still_message(1, "", "sea: ocean resource limit: seek work", "rendered")
        self.assertTrue(failed.splitlines()[1].startswith("error:"))
        self.assertIn("ocean resource limit", failed)


class Standalone(unittest.TestCase):
    """Every stage works on files handed to it, not only inside a project."""

    def test_vfx_takes_its_inputs_as_files_and_anchors_there(self):
        import argparse as ap
        parser = ap.ArgumentParser()
        sub = parser.add_subparsers()
        vfx.register(sub, [])
        with tempfile.TemporaryDirectory() as tmp:
            script_file = Path(tmp) / "script.json"
            script_file.write_text("{}")
            args = parser.parse_args(["vfx", "--script", str(script_file), "--voice",
                                      str(Path(tmp) / "voice.json")])
            self.assertEqual(workspace.override(args, "script"), str(script_file))
            idea, project = workspace.resolve(args, inputs=("script", "voice", "styles"))
            self.assertTrue(project.standalone)
            self.assertEqual(project.root, Path(tmp))
            self.assertFalse((Path(tmp) / "idea.json").exists())


class RenderEstimate(unittest.TestCase):
    """One-second windows project the full encode and name the hot shots."""

    def test_windows_sit_inside_the_programme(self):
        from ytstudio import render
        starts = render.window_starts(32.0, 6)
        self.assertEqual(len(starts), 6)
        self.assertTrue(all(0 <= t <= 31 for t in starts))
        self.assertEqual(render.window_starts(0.5, 2), [0.0, 0.0])

    def test_projection_names_the_solver_shot(self):
        from ytstudio import render
        rows = [{"at": 1, "seconds": 1.2, "shot": "1.1", "section": "fall"},
                {"at": 12, "seconds": 0.9, "shot": "2.1", "section": "impact"},
                {"at": 23, "seconds": 150.0, "shot": "3.1", "section": "ripples"},
                {"at": 30, "seconds": None, "shot": "3.3", "section": "ripples"}]
        est = render.project_cost(rows, 32.0, "root@x")
        self.assertAlmostEqual(est["per_second"], (1.2 + 0.9 + 150.0) / 3, places=2)
        self.assertEqual([h["shot"] for h in est["hot"]], ["3.1"])
        self.assertEqual(est["projected_seconds"], round(est["per_second"] * 32))


class ScriptArithmetic(unittest.TestCase):
    """A section count and runtimes that fit the target, short pieces included."""

    def test_a_ten_second_piece_is_one_section_of_ten_seconds(self):
        from ytstudio import script
        self.assertEqual(script.section_count(10), 1)
        fitted = script.fit_sections([{"seconds": 5}, {"seconds": 5}, {"seconds": 5},
                                      {"seconds": 5}], 10)
        self.assertEqual(sum(s["seconds"] for s in fitted), 10)
        self.assertTrue(all(s["seconds"] >= 1 for s in fitted))

    def test_section_counts_scale_with_runtime(self):
        from ytstudio import script
        self.assertEqual(script.section_count(30), 2)
        self.assertEqual(script.section_count(60), 4)
        self.assertEqual(script.section_count(600), 7)
        self.assertEqual(script.section_count(3600), 12)
        self.assertEqual(script.section_count(600, requested=3), 3)

    def test_fit_holds_the_total_and_keeps_proportions(self):
        from ytstudio import script
        fitted = script.fit_sections([{"seconds": 100}, {"seconds": 50}, {"seconds": 0}], 300)
        self.assertEqual(sum(s["seconds"] for s in fitted), 300)
        self.assertGreater(fitted[0]["seconds"], fitted[1]["seconds"])
        self.assertGreaterEqual(fitted[2]["seconds"], 5)     # nothing gets no time
        fitted = script.fit_sections([{"seconds": 0}, {"seconds": 0}], 120)
        self.assertEqual([s["seconds"] for s in fitted], [60, 60])

    def test_a_shot_longer_than_its_section_is_held_to_it(self):
        from ytstudio import script
        self.assertEqual(script.fit_sections([{"seconds": 7}], 10)[0]["seconds"], 10)
