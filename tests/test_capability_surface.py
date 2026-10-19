"""What the authoring agents can see is what they can choose.

The droplet film failed for want of a route the engine had all along: the
schema cheat-sheet cut every enumeration at twelve values, so `effect
@type="shader"` -- the last of about ninety -- never reached the assess
agent or the section authors, and no proven example showed how to use it.
These tests hold the cheat-sheet to the whole schema and the examples to a
shader that ships with its source.
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import vfx  # noqa: E402
from ytstudio.scene_sdk import enum_of, sdk  # noqa: E402


def attribute_line(sheet: str, element: str, attribute: str) -> str:
    block = sheet.split(f"## <{element}>", 1)[1].split("\n## ", 1)[0]
    for line in block.splitlines():
        if line.startswith(f"- @{attribute}:"):
            return line
    raise AssertionError(f"{element}@{attribute} is not on the cheat-sheet")


class CheatSheet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sheet = vfx.Workspace.cheat_sheet()

    def test_no_enumeration_is_cut_short(self):
        self.assertNotIn(" …", self.sheet)

    def test_every_effect_type_is_listed_shader_included(self):
        line = attribute_line(self.sheet, "effect", "type")
        listed = set(line.split(": ", 1)[1].split("|"))
        allowed = set(enum_of("effect", "type"))
        self.assertIn("shader", allowed)
        self.assertEqual(allowed - listed, set())


class ShaderExample(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = vfx.Workspace(Path(self._tmp.name))
        self.ws.prepare(tokens={"ink": "#FFFFFF"}, frame=(1920, 1080))
        self.doc = ElementTree.parse(self.ws.dir / "examples.scene.xml").getroot()

    def tearDown(self):
        self._tmp.cleanup()

    def shader_effects(self):
        return [e for e in self.doc.iter("effect") if e.get("type") == "shader"]

    def test_the_examples_carry_a_shader_effect_on_a_node(self):
        effects = self.shader_effects()
        self.assertTrue(effects, "no effect type=shader in examples.scene.xml")
        ids = {e.get("id") for e in effects}
        users = [n for n in self.doc.iter()
                 if set((n.get("effects") or "").split()) & ids]
        self.assertTrue(users, "no node applies the shader effect")

    def test_the_shader_source_is_written_beside_the_examples(self):
        for effect in self.shader_effects():
            src = self.ws.dir / effect.get("src")
            self.assertTrue(src.is_file(), f"{effect.get('src')} was not written")
            code = src.read_text(encoding="utf-8")
            self.assertRegex(code, r"void\s+mainImage\s*\(\s*out\s+vec4")
            self.assertIn("iTime", code)

    def test_every_uniform_has_a_default_or_a_param(self):
        for effect in self.shader_effects():
            code = (self.ws.dir / effect.get("src")).read_text(encoding="utf-8")
            params = {p.get("name") for p in effect.iter("param")}
            for line in code.splitlines():
                m = re.match(r"\s*uniform\s+float\s+(\w+)\s*;(.*)", line)
                if m:
                    self.assertTrue(m.group(1) in params or "// =" in m.group(2),
                                    f"uniform {m.group(1)} has neither a default nor a param")

    def test_the_examples_still_pass_the_sdk_check(self):
        doc = sdk().read((self.ws.dir / "examples.scene.xml").read_bytes())
        errors = [str(f) for f in doc.check() if str(f).startswith("error")]
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
