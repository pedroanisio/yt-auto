"""Test helpers: the program a fake author writes now that every authoring
gate builds the author's program itself.

The fixture documents of the stage tests are XML; a fake author turns one
into a program that rebuilds it element by element through the SDK's
`add_<name>` factories, so the gate's build, the determinism check and the
passthrough rule all see a real program. Not a test module (no `test_` prefix).
"""

from __future__ import annotations

from pathlib import Path

PROGRAM = '''from xml.etree import ElementTree as ET
from srkit import S, save

DOC = {doc!r}


def attrs(el):
    return {{("from_" if k == "from" else k): v for k, v in el.attrib.items()}}


def build(parent, el):
    for child in el:
        node = getattr(parent, "add_" + child.tag)(**attrs(child))
        if len(child) == 0 and child.text and child.text.strip():
            node.content = child.text.strip()
        build(node, child)


root = ET.fromstring(DOC)
scene = S.Scene(**attrs(root))
build(scene, root)
save(scene, {name!r})
'''


def program_source(doc: str, name: str) -> str:
    """A program that builds `doc` through the SDK and saves it as `name`."""
    body = doc.split("?>", 1)[1] if doc.lstrip().startswith("<?xml") else doc
    return PROGRAM.format(doc=body.strip(), name=name)


def author_program(cwd: Path, program: str, name: str, doc: str) -> None:
    """Write the program (and, as a careless author might, the document too:
    the gate's build overwrites it with the program's output)."""
    cwd = Path(cwd)
    (cwd / program).write_text(program_source(doc, name), encoding="utf-8")
    (cwd / name).write_text(doc, encoding="utf-8")
