"""Pin every text node to a licensed font file the repo ships.

A document names a face by family, weight and style; the render host has
whatever it has (DejaVu), so a family it lacks used to fall back in silence.
Here each text node is resolved to a file under ytstudio/fonts, the file is
copied into the project (so the render sync ships it), the font asset the
schema expects is added, and a family with no file is an error finding.

Nothing here knows a film: the catalogue is the shipped files, and the only
mapping from a requested family to another is ALIASES, which is explicit and
is reported in the manifest.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

FONT_DIR = Path(__file__).resolve().parent / "fonts"
#: Where, under the project, the shipped files are copied; the scene refers
#: to them relative to itself.
PROJECT_DIR = "fonts"
#: What a text node that names no family gets: the face the hosts render today.
DEFAULT_FAMILY = "DejaVu Sans"

WEIGHTS = {"thin": 100, "extralight": 200, "ultralight": 200, "light": 300,
           "regular": 400, "normal": 400, "book": 400, "medium": 500,
           "semibold": 600, "demibold": 600, "bold": 700, "extrabold": 800,
           "ultrabold": 800, "black": 900, "heavy": 900}

#: Licence of each shipped directory (texts in ytstudio/fonts/LICENSES).
LICENCES = {
    "Inter": ("OFL-1.1", "The Inter Project Authors", "Inter-OFL.txt"),
    "InterDisplay": ("OFL-1.1", "The Inter Project Authors", "Inter-OFL.txt"),
    "Lora": ("OFL-1.1", "The Lora Project Authors", "Lora-OFL.txt"),
    "LiberationSans": ("OFL-1.1", "Liberation Fonts", "fonts-liberation-copyright.txt"),
    "LiberationSerif": ("OFL-1.1", "Liberation Fonts", "fonts-liberation-copyright.txt"),
    "LiberationMono": ("OFL-1.1", "Liberation Fonts", "fonts-liberation-copyright.txt"),
    "DejaVuSans": ("Bitstream-Vera", "Bitstream, Inc.; DejaVu changes public domain",
                   "DejaVu-copyright.txt"),
    "DejaVuSansMono": ("Bitstream-Vera", "Bitstream, Inc.; DejaVu changes public domain",
                       "DejaVu-copyright.txt"),
    "DejaVuSerif": ("Bitstream-Vera", "Bitstream, Inc.; DejaVu changes public domain",
                    "DejaVu-copyright.txt"),
    "SourceSans3": ("OFL-1.1", "Adobe", "SourceSans3-OFL.md"),
    "STIXTwoText": ("OFL-1.1", "The STIX Fonts Project Authors", "STIXTwo-OFL.txt"),
}

#: A directory whose family name cannot be read from its capitals: the
#: family's real name, as the font files and the treatments spell it.
FAMILY_NAMES = {"SourceSans3": "Source Sans 3", "STIXTwoText": "STIX Two Text"}

#: Requested family (case, spaces and hyphens ignored) -> shipped family.
#: Only the metric-compatible and the generic names; a brand face the repo
#: does not ship is not here, and fails the build instead.
ALIASES = {
    "helvetica": "Liberation Sans", "helveticaneue": "Liberation Sans",
    "arial": "Liberation Sans",
    "timesnewroman": "Liberation Serif", "times": "Liberation Serif",
    "couriernew": "Liberation Mono", "courier": "Liberation Mono",
    "sansserif": "DejaVu Sans", "systemui": "DejaVu Sans",
    "uisansserif": "DejaVu Sans",
    "serif": "Liberation Serif", "uiserif": "Liberation Serif",
    "monospace": "DejaVu Sans Mono", "uimonospace": "DejaVu Sans Mono",
}


class FontError(Exception):
    """A requested family the repo ships no file for."""


@dataclass(frozen=True)
class Face:
    family: str
    weight: int
    style: str
    path: Path
    licence: str
    credit: str
    licence_file: str

    @property
    def id(self) -> str:
        return "font-" + re.sub(r"[^A-Za-z0-9]+", "-", self.path.stem).strip("-").lower()


def _key(name: str) -> str:
    return re.sub(r"[\s_-]+", "", name).lower()


def _family_of(directory: str) -> str:
    if directory in FAMILY_NAMES:
        return FAMILY_NAMES[directory]
    spaced = directory.replace("DejaVu", "Deja\0Vu")
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", spaced)
    return spaced.replace("\0", "")


def faces() -> list[Face]:
    """Every shipped file as a face; family from its directory, weight and
    style from its name (`Family-WeightItalic.ttf`)."""
    out = []
    for path in sorted(FONT_DIR.glob("*/*.ttf")):
        directory = path.parent.name
        word = path.stem.split("-", 1)[1] if "-" in path.stem else "Regular"
        italic = word.endswith("Italic")
        word = word[:-len("Italic")] if italic else word
        weight = WEIGHTS.get(_key(word) or "regular", 400)
        licence, credit, text = LICENCES.get(directory, ("see LICENSES", "", ""))
        out.append(Face(_family_of(directory), weight, "italic" if italic else "normal",
                        path, licence, credit,
                        f"ytstudio/fonts/LICENSES/{text}" if text else ""))
    return out


def families() -> list[str]:
    return sorted({f.family for f in faces()})


def weight_of(value) -> tuple[int, str]:
    """A requested weight as a number, and a note when it was not understood."""
    if value in (None, ""):
        return 400, ""
    text = str(value).strip()
    try:
        return max(1, min(1000, int(round(float(text))))), ""
    except ValueError:
        pass
    if _key(text) in WEIGHTS:
        return WEIGHTS[_key(text)], ""
    return 400, f"weight {text!r} is not a number or a weight name; 400 used"


def _pick(family: str, weight: int, style: str) -> tuple[Face, list[str]] | None:
    mine = [f for f in faces() if _key(f.family) == _key(family)]
    if not mine:
        return None
    notes = []
    want = "italic" if style in ("italic", "oblique") else "normal"
    same = [f for f in mine if f.style == want]
    if not same:
        same = mine
        notes.append(f"{mine[0].family} ships no {want} face; "
                     f"{mine[0].style} used")
    face = min(same, key=lambda f: (abs(f.weight - weight), -f.weight))
    if face.weight != weight:
        notes.append(f"{face.family} ships no weight {weight}; "
                     f"{face.path.name} (weight {face.weight}) used")
    return face, notes


def resolve(font: str, weight="400", style: str = "normal") -> tuple[Face, list[str]]:
    """The shipped face for a family list, weight and style, and the notes
    on how it differs from the request; FontError when no family resolves."""
    w, note = weight_of(weight)
    names = [n.strip().strip("'\"").strip() for n in str(font or "").split(",")]
    names = [n for n in names if n] or [DEFAULT_FAMILY]
    notes = [note] if note else []
    for n, name in enumerate(names):
        direct = _pick(name, w, style)
        alias = ALIASES.get(_key(name))
        if direct is None and alias:
            direct = _pick(alias, w, style)
            if direct:
                direct[1].insert(0, f"{name!r} is not shipped; the explicit alias "
                                    f"gives {alias}")
        if direct:
            if n:
                direct[1].insert(0, f"{names[0]!r} is not shipped; {name!r} used")
            return direct[0], notes + direct[1]
    raise FontError(f"font {names[0]!r} (weight {w}, {style}) has no file in "
                    f"ytstudio/fonts; shipped families: {', '.join(families())}")


# --------------------------------------------------------------------------- #
# which nodes are pinned, and to what
# --------------------------------------------------------------------------- #

PINNED_ATTRS = ("fontFile", "fontAsset")


def _request(tag: str, a: dict, styles: dict) -> tuple[str, str, str] | None:
    """(family, weight, style) a node must be pinned for, or None when it
    needs nothing (already pinned, or it inherits from what gets pinned)."""
    if tag == "object3D":
        if a.get("primitive") != "text":
            return None
        return (a.get("font") or DEFAULT_FAMILY, "400", "normal")
    if tag not in ("text", "span", "textStyle") \
            or any(a.get(k) for k in PINNED_ATTRS):
        return None
    font = a.get("font")
    if tag == "textStyle":
        return (font, a.get("weight") or "400", a.get("fontStyle") or "normal") \
            if font else None
    if tag == "span" and not font:
        return None
    sty = styles.get(a.get("style") or "", {})
    weight, slant = a.get("weight"), a.get("fontStyle")
    if not font:
        if any(sty.get(k) for k in PINNED_ATTRS):
            return None
        if sty.get("font"):
            if not (weight or slant):
                return None
            font = sty["font"]
        else:
            font = DEFAULT_FAMILY
    return (font, weight or sty.get("weight") or "400",
            slant or sty.get("fontStyle") or "normal")


class Pinner:
    """Pins the text nodes of one build and records what it did."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.errors: list[str] = []
        self.notes: list[str] = []
        self.counts: dict[str, int] = {}
        self.faces: dict[str, Face] = {}

    # -- shared -------------------------------------------------------------
    def _face(self, where: str, req: tuple[str, str, str]) -> Face | None:
        try:
            face, notes = resolve(*req)
        except FontError as exc:
            msg = f"error: {where}: {exc}"
            if msg not in self.errors:
                self.errors.append(msg)
            return None
        for note in notes:
            line = f"{where}: {note}"
            if line not in self.notes:
                self.notes.append(line)
        rel = self.ship(face)
        self.counts[rel] = self.counts.get(rel, 0) + 1
        self.faces[rel] = face
        return face

    def ship(self, face: Face) -> str:
        """Copy the face's file into the project; its path relative to it."""
        rel = f"{PROJECT_DIR}/{face.path.name}"
        target = self.root / rel
        if not target.is_file() or target.stat().st_size != face.path.stat().st_size:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(face.path, target)
        return rel

    def _asset_row(self, face: Face, src: str) -> dict:
        row = {"id": face.id, "src": src, "family": face.family,
               "weight": str(face.weight), "fontStyle": face.style,
               "sha256": hashlib.sha256(face.path.read_bytes()).hexdigest()}
        if face.licence_file:
            row["license"] = f"{face.licence} ({face.licence_file})"
        if face.credit:
            row["credit"] = face.credit
        return row

    @staticmethod
    def _where(tag: str, a: dict) -> str:
        return f"{tag} {a['id']!r}" if a.get("id") else tag

    # -- the SDK scene ------------------------------------------------------
    def pin_scene(self, scene) -> None:
        nodes = list(_walk(scene))
        styles = {n.attributes().get("id"): dict(n.attributes()) for n in nodes
                  if n.xml_name == "textStyle" and n.attributes().get("id")}
        for node in nodes:
            a = dict(node.attributes())
            req = _request(node.xml_name, a, styles)
            if req is None:
                continue
            face = self._face(self._where(node.xml_name, a), req)
            if face is None:
                continue
            assets = scene.find("assets")
            if assets is None:
                assets = scene.add_assets()
            if not any(f.attributes().get("id") == face.id
                       for f in assets.find_all("font")):
                assets.add_font(**self._asset_row(face, self.ship(face)))
            if node.xml_name == "object3D":
                node.font = face.family
            else:
                node.fontAsset = face.id

    # -- an ElementTree document (the include copies) ------------------------
    @staticmethod
    def styles_of(root: ElementTree.Element) -> dict:
        return {s.get("id"): dict(s.attrib) for s in root.iter("textStyle")
                if s.get("id")}

    def pin_xml(self, root: ElementTree.Element, doc_dir: Path,
                styles: dict | None = None) -> None:
        styles = self.styles_of(root) if styles is None else styles
        for node in list(root.iter()):
            req = _request(node.tag, dict(node.attrib), styles)
            if req is None:
                continue
            face = self._face(self._where(node.tag, node.attrib), req)
            if face is None:
                continue
            src = os.path.relpath(self.root / self.ship(face),
                                  Path(doc_dir)).replace(os.sep, "/")
            if node.tag == "object3D":
                node.set("font", face.family)
                self._asset_xml(root, face, src)
            else:
                node.set("fontFile", src)

    #: Elements that follow <assets> in a document; it goes before the first.
    AFTER_ASSETS = ("paints", "materials", "symbols", "scene360", "markers",
                    "tracking", "composition", "lights", "effects", "physics",
                    "audioMix", "captions")

    def _asset_xml(self, root: ElementTree.Element, face: Face, src: str) -> None:
        assets = root.find("assets")
        if assets is None:
            assets = ElementTree.Element("assets")
            at = next((i for i, c in enumerate(root) if c.tag in self.AFTER_ASSETS),
                      len(root))
            root.insert(at, assets)
        if not any(f.get("id") == face.id for f in assets.findall("font")):
            ElementTree.SubElement(assets, "font", self._asset_row(face, src))

    # -- the manifest -------------------------------------------------------
    def report(self) -> dict:
        return {"pinned": dict(sorted(self.counts.items())),
                "notes": list(self.notes),
                "families": sorted({f.family for f in self.faces.values()})}


def _walk(node):
    yield node
    for child in node.elements():
        yield from _walk(child)
