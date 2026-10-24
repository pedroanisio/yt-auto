"""One colour pipeline for the whole film, and one look the model chose.

The builder holds no grade: it validates the model's answer against the
schema's own list and sane bounds, writes one colorManagement with one CDL
look, and records it. A 'none' answer writes nothing.
"""

import tempfile
import unittest
from pathlib import Path

from tests.test_keyframe import STYLES
from ytstudio import build
from ytstudio.core import Idea
from ytstudio.script import Section, Shot

LOOK = {"tone_mapping": "aces", "exposure": 0.5, "look": "grade",
        "slope": [1.05, 1.0, 0.95], "offset": [0.01, 0.0, -0.01],
        "power": [1.0, 1.0, 1.05], "saturation": 1.1, "mix": 0.8,
        "reason": "warm and contained, as the treatment asks"}


def _builder(root, look=None, styles=None):
    idea = Idea.from_raw({"title": "A film", "genre": "explainer", "description": "p"})
    sections = [Section(heading="Open", shots=[
        Shot(label="1.1", n=1, visual="a thing", narration="Hello", seconds=4, at=0)])]
    return build.SceneBuilder(idea, sections, root, look=look, styles=styles,
                              verbose=False, audio=False)


class CleanLook(unittest.TestCase):
    def test_an_answer_within_bounds_is_kept(self):
        got = build.clean_look(LOOK)
        self.assertEqual(got["tone_mapping"], "aces")
        self.assertEqual(got["slope"], [1.05, 1.0, 0.95])
        self.assertEqual(got["saturation"], 1.1)

    def test_out_of_range_numbers_are_clamped_not_trusted(self):
        got = build.clean_look(dict(LOOK, exposure=9, saturation=7, mix=3,
                                    slope=[9, 0, 1], offset=[1, -1, 0], power=[0, 9, 1]))
        self.assertLessEqual(got["exposure"], 2)
        self.assertLessEqual(got["saturation"], 1.5)
        self.assertEqual(got["mix"], 1)
        self.assertEqual(got["slope"][0], build.LOOK_BOUNDS["slope"][1])
        self.assertEqual(got["slope"][1], build.LOOK_BOUNDS["slope"][0])
        self.assertEqual(got["offset"][0], build.LOOK_BOUNDS["offset"][1])
        self.assertEqual(got["power"][0], build.LOOK_BOUNDS["power"][0])

    def test_an_unknown_tone_mapper_is_none(self):
        self.assertEqual(build.clean_look(dict(LOOK, tone_mapping="neon"))["tone_mapping"],
                         "none")

    def test_none_is_a_valid_answer_and_malformed_is_none(self):
        for raw in ({"tone_mapping": "none", "look": "none"}, None, "x", {}):
            with self.subTest(raw=raw):
                self.assertIsNone(build.clean_look(raw))


class Applied(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_one_colour_management_with_one_look_that_passes_the_check(self):
        # a palette of its own, so the house-fallback warning (AUTO-155) is
        # not among the findings this asserts empty
        data, manifest = _builder(self.root, build.clean_look(LOOK), styles=STYLES).build()
        text = data.decode()
        self.assertEqual(text.count("<colorManagement"), 1)
        self.assertEqual(text.count("<look "), 1)
        self.assertIn('workingSpace="acescg"', text)
        self.assertIn('toneMapping="aces"', text)
        self.assertRegex(text, r'looks="film-look"')
        self.assertRegex(text, r'<look id="film-look"')
        self.assertEqual(manifest["findings"], [])
        self.assertEqual(manifest["look"]["tone_mapping"], "aces")

    def test_no_look_writes_no_colour_management(self):
        data, manifest = _builder(self.root, None).build()
        self.assertNotIn("<colorManagement", data.decode())
        self.assertIsNone(manifest["look"])

    def test_tone_mapping_alone_writes_no_look_element(self):
        data, _ = _builder(self.root, build.clean_look(
            {"tone_mapping": "agx", "look": "none", "exposure": 0})).build()
        text = data.decode()
        self.assertIn('toneMapping="agx"', text)
        self.assertNotIn("<look ", text)


class Sections(unittest.TestCase):
    def test_an_included_section_does_not_bring_its_own_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest = Path(tmp) / "a.xml", Path(tmp) / "copy.xml"
            src.write_text('<scene version="1.3"><project width="8" height="8" '
                           'fps="30" duration="1"/><colorManagement toneMapping="aces"/>'
                           '<composition/></scene>')
            info = build.cleaned_include(src, dest)
            self.assertNotIn("colorManagement", dest.read_text())
            self.assertIn("colorManagement", info["removed"])


class Asked(unittest.TestCase):
    def test_the_question_carries_the_treatment_and_palette_and_no_grade(self):
        data = {"title": "T", "treatment": "dusk, restrained",
                "design": {"roles": {"accent": "#AA3300"}, "source": "styles.json"}}
        prompt = build.look_prompt(data)
        self.assertIn("dusk, restrained", prompt)
        self.assertIn("#AA3300", prompt)
        self.assertIn("none", prompt)
        for number in ("1.05", "0.98"):
            self.assertNotIn(number, prompt)

    def test_the_schema_offers_the_engines_tone_mappers(self):
        enum = build.LOOK_SCHEMA["properties"]["tone_mapping"]["enum"]
        self.assertIn("none", enum)
        self.assertIn("aces", enum)


class FakeEngine:
    """Stands in for Harness.inspect: answers from a queue, notes what it saw."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def inspect(self, name, prompt, schema, *, cwd, **kw):
        self.calls.append({"name": name, "prompt": prompt, "files": sorted(
            p.name for p in Path(cwd).iterdir())})
        return self.answers.pop(0)


class Checked(unittest.TestCase):
    """The look is seen on a rendered frame before it is applied."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.renders = []

    def tearDown(self):
        self._tmp.cleanup()

    def still(self, data, at, **kw):
        self.renders.append((b"<colorManagement" in data, at))
        return b"\x89PNG", "rendered"

    def run_check(self, engine, host="render-1", look=None, still=None):
        builder = _builder(self.root)
        got, record = build.check_look(
            builder, engine, build.clean_look(look or LOOK), host=host,
            render_still=still or self.still)
        return builder, got, record

    def test_apply_keeps_the_look_after_seeing_both_frames(self):
        engine = FakeEngine({"verdict": "apply", "reason": "palette kept"})
        _, got, record = self.run_check(engine)
        self.assertEqual(got["tone_mapping"], "aces")
        self.assertEqual(sorted(self.renders), [(False, 2.0), (True, 2.0)])
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(engine.calls[0]["files"], ["graded.png", "ungraded.png"])
        self.assertEqual(record["status"], "applied")
        self.assertEqual(record["verdict"], "apply")

    def test_revise_then_apply_uses_the_revision_after_one_recheck(self):
        revised = dict(LOOK, tone_mapping="none", exposure=0, saturation=1.0)
        engine = FakeEngine({"verdict": "revise", "reason": "greyer", "revision": revised},
                            {"verdict": "apply", "reason": "better"})
        builder, got, record = self.run_check(engine)
        self.assertEqual(got["tone_mapping"], "none")
        self.assertEqual(len(engine.calls), 2)
        self.assertEqual(record["status"], "revised")
        self.assertEqual(record["first"]["verdict"], "revise")
        self.assertIsNone(builder.look)       # the check leaves the builder as it found it

    def test_a_revision_that_is_not_approved_is_dropped(self):
        engine = FakeEngine({"verdict": "revise", "reason": "r", "revision": LOOK},
                            {"verdict": "revise", "reason": "still bad", "revision": LOOK})
        _, got, record = self.run_check(engine)
        self.assertIsNone(got)
        self.assertEqual(len(engine.calls), 2)
        self.assertEqual(record["status"], "dropped")
        self.assertIn("still bad", record["reason"])

    def test_none_applies_no_look_and_says_why(self):
        engine = FakeEngine({"verdict": "none", "reason": "washed out"})
        _, got, record = self.run_check(engine)
        self.assertIsNone(got)
        self.assertEqual(record["status"], "dropped")
        self.assertIn("washed out", record["reason"])

    def test_no_host_means_no_look_and_no_question(self):
        engine = FakeEngine()
        _, got, record = self.run_check(engine, host="")
        self.assertIsNone(got)
        self.assertEqual(record["status"], "not checked")
        self.assertEqual(self.renders, [])
        self.assertEqual(engine.calls, [])

    def test_the_record_names_the_host_it_used(self):
        # so the build's later checks reuse it instead of probing again
        engine = FakeEngine({"verdict": "apply", "reason": "palette kept"})
        _, _, record = self.run_check(engine)
        self.assertEqual(record["host"], "render-1")
        _, _, record = self.run_check(FakeEngine(), host="")
        self.assertEqual(record["host"], "")

    def test_a_frame_that_did_not_render_means_not_checked(self):
        engine = FakeEngine()
        _, got, record = self.run_check(engine, still=lambda d, a, **k: (None, "boom"))
        self.assertIsNone(got)
        self.assertEqual(record["status"], "not checked")
        self.assertIn("boom", record["reason"])

    def test_every_include_copy_is_shipped_with_the_frame_it_draws(self):
        rel = "vfx/v02/01-a.include.scene.xml"

        class Built(build.SceneBuilder):
            def build(inner):
                # build() writes the include copy, as the real one does
                copy = inner.root / rel
                copy.parent.mkdir(parents=True, exist_ok=True)
                copy.write_text('<scene><image src="tex.png"/></scene>')
                (copy.parent / "tex.png").write_bytes(b"png")
                return (f'<scene><include src="{rel}"/></scene>'.encode(), {})

        shipped = []

        def still(data, at, *, files=None, **kw):
            shipped.append(sorted(files or {}))
            import re
            for src in re.findall(rb'<include src="([^"]+)"', data):
                if src.decode() not in (files or {}):
                    return None, f'error[A01]: @src of <include>: file "{src.decode()}" does not exist'
            return b"\x89PNG", "rendered"

        base = _builder(self.root)
        builder = Built(base.idea, base.sections, self.root, verbose=False, audio=False)
        engine = FakeEngine({"verdict": "apply", "reason": "ok"})
        got, record = build.check_look(builder, engine, build.clean_look(LOOK),
                                       host="h", render_still=still)
        self.assertEqual(record["status"], "applied", record)
        self.assertIsNotNone(got)
        self.assertIn(rel, shipped[0])
        self.assertIn("vfx/v02/tex.png", shipped[0])     # the copy's own references

    def test_the_suite_has_no_host(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"YT_NO_REMOTE": "1"}):
            self.assertEqual(build.look_host(), "")

    def test_nothing_chosen_is_nothing_to_check(self):
        engine = FakeEngine()
        got, record = build.check_look(_builder(self.root), engine, None, host="h",
                                       render_still=self.still)
        self.assertIsNone(got)
        self.assertEqual(record["status"], "none chosen")


if __name__ == "__main__":
    unittest.main()
