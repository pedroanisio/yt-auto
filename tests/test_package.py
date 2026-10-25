"""`yt package` writes and checks the upload set (AUTO-147, part B).

A gated, QA'd video.mp4 is not a YouTube upload: nothing wrote a title, a
description with the sources and the licence credits, chapters, tags or a
thumbnail, and nothing checked them. `yt package` assembles every timing,
credit line, URL and candidate frame in code from scene.xml, build.json,
script.json and dossier.json, has a model word the title, hook, chapter
titles and tags and pick one candidate, checks the result against
YouTube's limits and writes package/ for an operator to upload by hand.
Nothing here uploads, and no model, network or render host is reached.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import re
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock
from xml.sax.saxutils import quoteattr

from tests.test_harness_decisions import FakeEngine
from ytstudio import cli, harness, package, render, workspace

ROOT = Path(__file__).resolve().parent.parent

CREDIT = "Plate by A. Author / Wikimedia Commons, CC BY 4.0"
CREDITS = [{"credit": CREDIT, "licence": "CC BY 4.0", "src": "downloads/01-plate.png",
            "id": "img-1.1", "tier": 1, "required": True},
           {"credit": "Studio HDRI / CC0 (Poly Haven)", "licence": "CC0 1.0",
            "src": "props/hdri/studio/studio.hdr", "id": "studio", "tier": 0,
            "required": False}]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def png(width: int, height: int, size: int | None = None) -> bytes:
    """A PNG signature and IHDR chunk, padded to `size` bytes: all the check reads."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    head = (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr
            + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr) & 0xFFFFFFFF))
    return head + b"\x00" * max(0, (size or 0) - len(head))


def scene_xml(sections=(), beats=(), duration=60.0, credits_at=None, card_text=None,
              width=1920, height=1080) -> bytes:
    """A document with section and beat markers and, optionally, a credits card."""
    markers = [f'<marker id="section-{n}" kind="section" label={quoteattr(label)} time="{at}"/>'
               for n, (at, label) in enumerate(sections)]
    markers += [f'<marker id="beat-{label}" kind="beat" label="{label}" time="{at}" '
                f'duration="{seconds}"/>' for label, at, seconds in beats]
    assets = ""
    if credits_at is not None:
        markers.append(f'<marker id="credits-1" kind="comment" label="credits" '
                       f'time="{credits_at}"/>')
        assets = f'<text id="credits-card-1" text={quoteattr(card_text or "")}/>'
    return (f'<scene version="1.1"><project width="{width}" height="{height}" fps="30" '
            f'duration="{duration}"/><assets>{assets}</assets><composition/>'
            f'<markers>{"".join(markers)}</markers></scene>').encode()


class Chapters(unittest.TestCase):
    def test_the_first_is_forced_to_zero_and_a_short_one_merges_into_the_one_before(self):
        doc = scene_xml([(0.4, "A"), (12, "B"), (15, "C"), (40, "D")], duration=60)
        chapters, warnings = package.chapters_from_scene(doc, 60)
        self.assertEqual([(c["at"], c["title"]) for c in chapters],
                         [(0, "A"), (15, "C"), (40, "D")])
        self.assertEqual(warnings, [])

    def test_fewer_than_three_is_no_chapters_and_a_warning(self):
        doc = scene_xml([(0, "A"), (20, "B"), (25, "C")], duration=50)
        chapters, warnings = package.chapters_from_scene(doc, 50)
        self.assertEqual(chapters, [])
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith("warning:"))
        self.assertIn("chapter", warnings[0])

    def test_the_last_chapter_ends_where_the_credits_begin(self):
        sections = [(0, "A"), (20, "B"), (40, "C")]
        kept, _ = package.chapters_from_scene(scene_xml(sections, credits_at=55), 60)
        self.assertEqual(len(kept), 3)
        # C would run 40-60 to the duration; to the card it is 5 s and merges
        merged, warnings = package.chapters_from_scene(scene_xml(sections, credits_at=45), 60)
        self.assertEqual(merged, [])
        self.assertTrue(warnings)


    def test_chapter_kind_markers_are_read_as_the_build_may_write_them(self):
        # SceneBuilder picks "section", else "chapter", from the schema's kinds
        doc = scene_xml([(0, "A"), (20, "B"), (40, "C")]).replace(
            b'kind="section"', b'kind="chapter"')
        chapters, _ = package.chapters_from_scene(doc, 60)
        self.assertEqual([c["title"] for c in chapters], ["A", "B", "C"])


SCRIPT = {"promise": "how a lens prints a chip", "payoff": "the light is the tool",
          "train": "can light this short be made at all?", "stakes": "no new chips",
          "sections": [
              {"heading": "Light", "shots": [
                  {"label": "1.1", "visual": "a wafer under blue light",
                   "narration": "Light prints the chip.", "source": "https://ex.org/a"},
                  {"label": "1.2", "visual": "a mirror stack",
                   "narration": "Mirrors carry it.", "source": "Nature 2019, The Big Paper"}]},
              {"heading": "Tin", "shots": [
                  {"label": "2.1", "visual": "a tin droplet",
                   "narration": "Tin makes it.", "source": "my uncle"},
                  {"label": "2.2", "visual": "a plasma flash", "narration": "Flash.",
                   "source": "https://ex.org/a"},
                  {"label": "2.3", "visual": "a dark room", "narration": "", "source": ""}]}]}
DOSSIER = {"claims": [
    {"claim": "x", "source_title": "The Big Paper", "source_url": "https://ex.org/b"},
    {"claim": "y", "source_title": "A page", "source_url": "https://ex.org/a"},
    {"claim": "z", "source_title": "", "source_url": ""}]}


class Sources(unittest.TestCase):
    def test_a_url_match_a_title_match_and_an_unmatched_source(self):
        rows, warnings = package.sources_from_script(SCRIPT, DOSSIER)
        self.assertEqual(rows, [{"title": "A page", "url": "https://ex.org/a"},
                                {"title": "The Big Paper", "url": "https://ex.org/b"},
                                {"title": "my uncle", "url": ""}])
        self.assertEqual(len(warnings), 1)
        self.assertIn("2.1", warnings[0])
        self.assertTrue(warnings[0].startswith("warning:"))

    def test_a_url_is_matched_whole_never_as_the_prefix_of_another(self):
        dossier = {"claims": [
            {"claim": "x", "source_title": "Short", "source_url": "https://a.org/x"},
            {"claim": "y", "source_title": "Long", "source_url": "https://a.org/x2"}]}
        script = {"sections": [{"heading": "H", "shots": [
            {"label": "1.1", "visual": "v", "narration": "n",
             "source": "see https://a.org/x2."},
            {"label": "1.2", "visual": "v", "narration": "n",
             "source": "(https://a.org/x), p. 4"}]}]}
        rows, warnings = package.sources_from_script(script, dossier)
        self.assertEqual(rows, [{"title": "Long", "url": "https://a.org/x2"},
                                {"title": "Short", "url": "https://a.org/x"}])
        self.assertEqual(warnings, [])

    def test_no_dossier_leaves_every_source_text_only(self):
        rows, warnings = package.sources_from_script(SCRIPT, {})
        self.assertEqual([r["url"] for r in rows], ["", "", ""])
        self.assertEqual(len(warnings), 3)


THUMB_SCENE = scene_xml([(0, "Light"), (10, "Tin")],
                        [("1.1", 0, 4), ("1.2", 4, 6.33), ("2.1", 10, 8), ("2.2", 17, 4)],
                        duration=40, credits_at=18, card_text=CREDIT)


class Candidates(unittest.TestCase):
    def test_section_times_and_beat_midpoints_none_inside_the_credits(self):
        got = package.thumbnail_candidates(THUMB_SCENE, SCRIPT)
        # 0 and 10 (sections), 2.0, 7.2 (7.165) and 14.0 (midpoints); 2.2's
        # midpoint (19.0) is inside the card
        self.assertEqual([c["at"] for c in got], [0.0, 2.0, 7.2, 10.0, 14.0])
        first = got[0]
        self.assertEqual((first["label"], first["section"], first["visual"]),
                         ("1.1", "Light", "a wafer under blue light"))
        self.assertEqual(got[-1]["visual"], "a tin droplet")
        for c in got:
            self.assertEqual(set(c), {"at", "label", "section", "visual"})

    def test_at_most_twenty_four_spread_over_the_film(self):
        beats = [(f"1.{n}", n * 2, 2) for n in range(60)]
        got = package.thumbnail_candidates(scene_xml([(0, "All")], beats, duration=120), {})
        self.assertEqual(len(got), package.THUMB_CANDIDATES_MAX)
        ats = [c["at"] for c in got]
        self.assertEqual(ats, sorted(set(ats)))
        self.assertEqual(ats[0], 0.0)
        self.assertGreater(ats[-1], 110)


CHAPTERS = [{"at": 0, "title": "Light"}, {"at": 15, "title": "Mirrors"},
            {"at": 40, "title": "Tin"}]
SOURCES = [{"title": "A page", "url": "https://ex.org/a"}, {"title": "my uncle", "url": ""}]


class Description(unittest.TestCase):
    def test_the_blocks_come_in_order_and_a_clean_credit_is_verbatim(self):
        text, warnings = package.assemble_description("A hook. Then more.", CHAPTERS,
                                                      SOURCES, CREDITS)
        self.assertEqual(warnings, [])
        order = [text.index(s) for s in ("A hook.", "Chapters", "0:00 Light", "0:15 Mirrors",
                                         "Sources", "https://ex.org/a", "Credits", CREDIT)]
        self.assertEqual(order, sorted(order))
        self.assertIn(CREDIT, text.splitlines())

    def test_angle_brackets_are_replaced_and_named_at_their_source(self):
        bracketed = dict(CREDITS[0], credit="Photo by X <x@y.org>, CC BY 4.0")
        text, warnings = package.assemble_description("Hook.", CHAPTERS, SOURCES, [bracketed])
        self.assertNotIn("<", text)
        self.assertNotIn(">", text)
        self.assertIn("Photo by X ‹x@y.org›, CC BY 4.0", text)
        self.assertEqual(len(warnings), 1)
        self.assertIn("credit_line", warnings[0])
        # and the check reads it as the line, substituted
        pkg, scene, video = clean_package(credits=[bracketed], card=bracketed["credit"])
        self.assertEqual(errors_of(pkg, scene, video), [])


def clean_package(credits=CREDITS, card=CREDIT):
    """A package that passes every check, with the scene and video it judged."""
    scene = scene_xml([(0, "Light"), (15, "Mirrors"), (40, "Tin")], duration=64,
                      credits_at=58, card_text=card)
    video = b"a video"
    text, _ = package.assemble_description("A hook.", CHAPTERS, SOURCES, credits)
    pkg = {"title": "How a lens prints a chip", "description": text,
           "chapters": [dict(c) for c in CHAPTERS], "tags": ["euv", "lithography"],
           "credits": credits, "scene_sha256": sha(scene), "video_sha256": sha(video)}
    return pkg, scene, video


def errors_of(pkg, scene, video, *, build=None, thumbnail=None, duration=64.0):
    found = package.check_package(
        pkg, scene, video, duration,
        build={"publishable": True} if build is None else build,
        thumbnail=png(1280, 720) if thumbnail is None else thumbnail)
    return [f for f in found if f.startswith("error:")]


class Check(unittest.TestCase):
    def clean(self, **kw):
        return clean_package(**kw)

    def errors(self, *a, **kw):
        return errors_of(*a, **kw)

    def one(self, pkg, scene, video, needle, **kw):
        errors = self.errors(pkg, scene, video, **kw)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn(needle, errors[0])
        return errors[0]

    def test_a_clean_package_has_no_errors(self):
        self.assertEqual(self.errors(*self.clean()), [])

    def test_the_title_is_one_to_a_hundred_characters(self):
        pkg, scene, video = self.clean()
        pkg["title"] = "x" * 101
        self.one(pkg, scene, video, "title")
        pkg["title"] = ""
        self.one(pkg, scene, video, "title")

    def test_the_description_is_at_most_five_thousand_characters(self):
        pkg, scene, video = self.clean()
        pkg["description"] = pkg["description"] + "x" * (5001 - len(pkg["description"]))
        self.one(pkg, scene, video, "5000")

    def test_a_raw_angle_bracket_in_the_description(self):
        pkg, scene, video = self.clean()
        pkg["description"] += "\nsee <here>"
        self.one(pkg, scene, video, "angle")

    def test_a_raw_angle_bracket_in_the_title(self):
        pkg, scene, video = self.clean()
        pkg["title"] = "Chips <and> lenses"
        error = self.one(pkg, scene, video, "angle")
        self.assertTrue(error.startswith("error: title:"))

    def test_tags_are_at_most_five_hundred_characters(self):
        pkg, scene, video = self.clean()
        pkg["tags"] = ["x" * 501]
        self.one(pkg, scene, video, "tags")

    def test_chapters_start_at_zero(self):
        pkg, scene, video = self.clean()
        pkg["chapters"][0]["at"] = 3
        self.one(pkg, scene, video, "0:00")

    def test_a_required_credit_missing_from_the_description(self):
        pkg, scene, video = self.clean()
        pkg["description"] = pkg["description"].replace(CREDIT, "")
        self.one(pkg, scene, video, "description")

    def test_a_required_credit_missing_from_the_card(self):
        pkg, scene, video = self.clean(card="someone else")
        self.one(pkg, scene, video, "credits card")

    def test_a_credit_owing_nothing_need_not_be_printed(self):
        pkg, scene, video = self.clean()
        pkg["description"] = pkg["description"].replace(CREDITS[1]["credit"], "")
        self.assertEqual(self.errors(pkg, scene, video), [])

    def test_a_small_thumbnail(self):
        self.one(*self.clean(), "1280x720", thumbnail=png(800, 600))

    def test_a_thumbnail_over_two_megabytes(self):
        error = self.one(*self.clean(), "2 MB",
                         thumbnail=png(1280, 720, size=int(2.1 * 1024 * 1024)))
        self.assertIn("export thumbnail.png as JPEG outside yt and upload that", error)

    def test_no_thumbnail(self):
        pkg, scene, video = self.clean()
        found = package.check_package(pkg, scene, video, 64.0, build={"publishable": True},
                                      thumbnail=None)
        self.assertTrue([f for f in found if f.startswith("error:") and "thumbnail" in f])

    def test_a_stale_scene_digest(self):
        pkg, scene, video = self.clean()
        self.one(pkg, scene + b" ", video, "scene.xml")

    def test_a_stale_video_digest(self):
        pkg, scene, video = self.clean()
        self.one(pkg, scene, video + b"!", "video.mp4")

    def test_a_build_that_is_not_publishable(self):
        self.one(*self.clean(), "publishable", build={"publishable": False})


class Thumbnail(unittest.TestCase):
    def test_the_engine_is_handed_a_1280x720_document_at_final_quality(self):
        seen = {}

        def still(xml, at, **kw):
            seen.update(xml=xml, at=at, **kw)
            return png(1280, 720, size=4096), ""
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(render, "engine_render_still", still):
            data, findings = package.render_thumbnail(scene_xml(duration=20), 7.2, Path(tmp))
        self.assertEqual(findings, [])
        self.assertEqual(package.png_size(data), (1280, 720))
        project = re.search(rb"<project [^>]*>", seen["xml"]).group(0)
        self.assertIn(b'width="1280"', project)
        self.assertIn(b'height="720"', project)
        self.assertEqual((seen["at"], seen["quality"]), (7.2, "final"))
        self.assertFalse([f for f in package.thumbnail_problems(data) if "error" in f])

    def test_an_oversized_frame_names_the_jpeg_export(self):
        big = png(1280, 720, size=package.THUMB_MAX_BYTES + 1)
        problems = package.thumbnail_problems(big)
        self.assertEqual(len(problems), 1)
        self.assertIn("export thumbnail.png as JPEG outside yt and upload that", problems[0])

    def test_a_build_that_is_not_sixteen_by_nine_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(render, "engine_render_still") as still:
            data, findings = package.render_thumbnail(
                scene_xml(width=1080, height=1080), 1.0, Path(tmp))
        self.assertIsNone(data)
        still.assert_not_called()
        self.assertEqual(len(findings), 1)
        self.assertIn("thumbnail must be 16:9; the build is 1080x1080", findings[0])


def answer_for(schema: dict) -> dict:
    """What the fake model says: a draft naming the first candidate, a clean review."""
    props = schema.get("properties") or {}
    if "thumbnail_at" in props:
        n = props["chapter_titles"].get("minItems", 3)
        return {"title": "How a lens prints a chip", "hook": "Light this short. Made here.",
                "chapter_titles": [f"Part {i + 1}" for i in range(n)],
                "tags": ["euv", "lithography"],
                "thumbnail_at": props["thumbnail_at"]["enum"][0]}
    return {"flags": []}


class SchemaEngine(FakeEngine):
    """A FakeEngine answering each question by its schema."""

    def ask_json(self, prompt, schema, system=None):
        FakeEngine.json_calls.append({"prompt": prompt, "schema": schema, "system": system,
                                      "model": self.config.model})
        return answer_for(schema)


class ProjectCase(unittest.TestCase):
    """A built, rendered project: everything `yt package` reads."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name)
        self.root = self.work / "a-film"
        self.root.mkdir()
        self.write("idea.json", {"title": "A film", "genre": "explainer",
                                 "description": "how a lens prints a chip",
                                 "promise": "the lens, and the chip it prints"})
        self.scene = scene_xml([(0, "Light"), (15, "Mirrors"), (40, "Tin")],
                               [("1.1", 0, 15), ("1.2", 15, 25), ("2.1", 40, 18)],
                               duration=64, credits_at=58, card_text=CREDIT)
        (self.root / "scene.xml").write_bytes(self.scene)
        (self.root / "video.mp4").write_bytes(b"a video")
        (self.root / "narration.vtt").write_text("WEBVTT\n\n00:00.000 --> 00:01.000\nLight\n")
        self.write("build.json", {"publishable": True, "credits": CREDITS,
                                  "timeline": {"runtime": 64.0},
                                  "scene": {"duration": 64.0, "width": 1920, "height": 1080}})
        self.write("script.json", SCRIPT)
        self.write("dossier.json", DOSSIER)
        self.write("styles.json", {"thumbnail": "one droplet, lit hard, on black"})
        self._engine = harness.ENGINE
        harness.ENGINE = SchemaEngine
        self.addCleanup(setattr, harness, "ENGINE", self._engine)
        FakeEngine.json_calls, FakeEngine.texts, FakeEngine.answer = [], [], None
        self.stills = []

    def write(self, name, data):
        (self.root / name).write_text(json.dumps(data), encoding="utf-8")

    def still(self, xml, at, **kw):
        self.stills.append((xml, at, kw))
        return png(1280, 720, size=4096), ""

    def run_package(self, *extra):
        args = cli.build_parser().parse_args(
            ["package", "--project", str(self.root), "--quiet", *extra])
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(render, "engine_render_still", self.still), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = args.run(args)
        return code, out.getvalue() + err.getvalue()

    def package(self) -> dict:
        return json.loads((self.root / "package" / "package.json").read_text())


class Run(ProjectCase):
    def test_a_run_writes_the_upload_set_and_says_it_is_ready(self):
        code, said = self.run_package()
        self.assertEqual(code, 0, said)
        folder = self.root / "package"
        for name in ("package.json", "description.txt", "thumbnail.png", "captions.vtt",
                     "UPLOAD.md"):
            self.assertTrue((folder / name).is_file(), name)
        self.assertTrue(workspace.Project(self.root).has("package"))
        self.assertEqual(workspace.ARTIFACTS["package"], "package/package.json")
        pkg = self.package()
        self.assertEqual(pkg["verdict"], "ready", pkg["findings"])
        self.assertEqual([c["at"] for c in pkg["chapters"]], [0, 15, 40])
        self.assertEqual([c["title"] for c in pkg["chapters"]], ["Part 1", "Part 2", "Part 3"])
        self.assertEqual(pkg["scene_sha256"], sha(self.scene))
        self.assertEqual(pkg["video_sha256"], sha(b"a video"))
        self.assertEqual(pkg["thumbnail"]["width"], 1280)
        self.assertEqual(pkg["thumbnail"]["file"], "thumbnail.png")
        self.assertIn(pkg["thumbnail"]["at"],
                      [c["at"] for c in package.thumbnail_candidates(self.scene, SCRIPT)])
        self.assertIn(CREDIT, (folder / "description.txt").read_text())
        self.assertEqual((folder / "description.txt").read_text(), pkg["description"])
        self.assertEqual((folder / "captions.vtt").read_text(),
                         (self.root / "narration.vtt").read_text())
        upload = (folder / "UPLOAD.md").read_text()
        for field in ("Title", "Description", "Thumbnail", "Tags", "Subtitles"):
            self.assertIn(field, upload)
        self.assertIn("ready", said)

    def test_the_still_is_rendered_from_a_1280x720_copy_at_the_chosen_time(self):
        self.run_package()
        self.assertEqual(len(self.stills), 1)
        xml, at, kw = self.stills[0]
        self.assertIn(b'width="1280" height="720"', xml)
        self.assertEqual(at, self.package()["thumbnail"]["at"])
        self.assertEqual(kw["quality"], "final")
        # the project's own scene.xml is untouched
        self.assertEqual((self.root / "scene.xml").read_bytes(), self.scene)

    def test_a_build_that_is_not_publishable_is_not_ready_and_exits_non_zero(self):
        self.write("build.json", {"publishable": False, "credits": CREDITS,
                                  "timeline": {"runtime": 64.0}, "scene": {"duration": 64.0}})
        code, _ = self.run_package()
        self.assertEqual(code, 1)
        pkg = self.package()
        self.assertEqual(pkg["verdict"], "not ready")
        self.assertTrue([f for f in pkg["findings"] if "publishable" in f])

    def test_no_video_is_an_error_before_any_model_is_asked(self):
        (self.root / "video.mp4").unlink()
        code, said = self.run_package()
        self.assertEqual(code, 1)
        self.assertIn("yt render", said)
        self.assertEqual(FakeEngine.json_calls, [])


class NoUpload(unittest.TestCase):
    SOURCE = ROOT / "ytstudio" / "package.py"

    def test_it_imports_nothing_that_reaches_the_network(self):
        tree = ast.parse(self.SOURCE.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names.add(node.module.split(".")[0])
        self.assertFalse(names & {"urllib", "http", "socket", "ssl", "requests"}, names)

    def test_it_registers_no_upload_publish_or_schedule_option(self):
        sub = next(a for a in cli.build_parser()._actions
                   if getattr(a, "choices", None) and a.dest == "stage").choices
        options = [o for a in sub["package"]._actions for o in a.option_strings]
        self.assertTrue(options)
        self.assertFalse([o for o in options
                          if re.search(r"upload|publish|schedule", o)], options)

    def test_it_is_not_a_stage_yt_run_walks(self):
        self.assertNotIn("package", [s for s, _ in workspace.STAGES])
        self.assertNotIn("package", cli.PIPELINE)


class InTheTail(unittest.TestCase):
    """AUTO-164: `yt run`'s delivery tail runs `yt package` after a READY QA,
    and every text that described it as outside `yt run` says so now."""

    def test_the_docstring_says_the_tail_runs_it_and_it_is_still_no_stage(self):
        doc = " ".join(package.__doc__.split())
        self.assertNotIn("Not a stage `yt run` walks", doc)
        self.assertIn("`yt run`'s delivery tail runs it after a READY QA", doc)
        self.assertIn("not a PIPELINE stage", doc)

    def test_the_manual_and_readme_say_the_tail_packages_the_film(self):
        skill = (ROOT / "ytstudio" / "SKILL.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        production = (ROOT / "docs" / "production.md").read_text(encoding="utf-8")
        for name, text in (("SKILL.md", skill), ("README.md", readme)):
            row = next(line for line in text.splitlines()
                       if line.startswith("| `yt package` |"))
            self.assertNotIn("not a stage `yt run` walks", row.lower(), name)
            self.assertIn("delivery tail", row, name)
        tail = skill[skill.index("runs the delivery tail, `cli.deliver_tail`"):]
        tail = tail[:tail.index("\n\n")]
        for needle in ("`yt package`", "already ready on this render", "AUTO-164"):
            self.assertIn(needle, tail, needle)
        tail = readme[readme.index("After the render the run finishes the validated path"):]
        tail = tail[:tail.index("\n\n")]
        for needle in ("`yt package`", "already ready on this render", "AUTO-164"):
            self.assertIn(needle, tail, needle)
        beside = production[production.index("`yt package` writes `package/`"):]
        self.assertIn("delivery tail", beside[:beside.index("\n\n")])
        self.assertIn("yt package", cli.build_parser()._subparsers._group_actions[0]
                      .choices["run"].description)


if __name__ == "__main__":
    unittest.main()
