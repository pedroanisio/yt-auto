"""Physical plausibility of a world: does the hero pass through the set?

A proof frame shows a hero over a surface; it does not show that the hero is
inside that surface, because a render has no notion of a solid, and the vision
reviewers mention such a merge without ever blocking on it. This is the
mechanical check. The engine's `eval` prints the frame graph at one time
with every animated property evaluated (`scene-render eval scene.xml --time
T --format json`: per node its `kind`, `id` such as `shot-1.1/hero-subject`,
and `props`); the document says what each object3D is (its primitive and
dimensions, static position, rotation and scale, and the node it follows by
`parent`, which eval does not resolve). Together they give every node's
world axis-aligned box at that time: the eight corners of the primitive's
box scaled, rotated and moved by its own transform and its parents', and
the extent taken. A hero (an id starting `hero-`, with every node that
follows it by `parent`) whose box overlaps a set solid by more than a tenth
of the hero's smallest dimension is a penetration -- a resting contact
passes, a merge does not -- recorded with the time, the hero, the object,
the depth and the axis. Where a document declares that the hero comes to
rest on a surface (`<meta name="hero-rests" value="<hero> <surface>
<time>"/>`), a hero floating above it at that time is a finding too. A
hero is no penetration of what it is attached to: the nodes it follows by
`parent`, and the objects a document declares it joined to by design
(`<meta name="hero-attached" value="<hero> <object or prefix*> ..."/>`, a
cap on the strand it caps); those overlaps still count for every other
check.

Axes, from the schema: y points down (the camera's doc: "y points down",
"z points into the scene"), so a floor is y = 0 and up is negative; the
object3D doc says the object is turned about x, then y, then z, all about
the parent's axes (Rz*Ry*Rx), and `height` is the y extent of every
primitive that has one. The sign of the z rotation was read off eval's own
2D `local` matrix; x and y take the same handedness. A box symmetric about
its origin has the same extent under either sign, so only an off-centre
child could differ, by no more than its offset. A set solid is an object3D
of a solid primitive that is opaque enough (opacity >= SOLID_OPACITY), is
no shadow catcher and takes part in shadowing (not castShadow="false" with
receiveShadow="false": light shafts, contact blobs and flares declare
themselves that way). A mesh has no declared bounds in the schema, so it is
taken as a unit cube under its scale and its records are marked
approximate; text is a rough advance per character, marked the same.

Everything above the runner is pure: text and the engine's JSON in, records
out. The runner is one job on a render host -- the pipeline's mirror and
hardlinked job directory, through `render`'s own helpers -- whose shell loop
runs eval once per time and prints one envelope per line, so sixty samples
cost one round trip. `yt vfx`, `yt qa` and `yt gate` each call it through a
seam a test replaces.
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

from . import render
from .framecheck import is_hero

#: The sampling step, in seconds, of every hook.
STEP = 0.25
#: A merge deeper than this share of the hero's smallest dimension is a penetration.
TOLERANCE_SHARE = 0.10
#: Below this opacity an object is a light fake, not a solid.
SOLID_OPACITY = 0.5
#: `<meta name="hero-rests" value="<hero id> <surface id> <seconds>"/>`
REST_META = "hero-rests"
#: `<meta name="hero-attached" value="<hero id> <object id or prefix*> ..."/>`
ATTACH_META = "hero-attached"
#: The schema's defaults: radius 50 (width and height twice it), depth 10, text size 100.
DEFAULT_RADIUS, DEFAULT_DEPTH, TEXT_SIZE = 50.0, 10.0, 100.0
TEXT_ADVANCE = 0.6          # rough width of a character as a share of the text size
AXES = "xyz"
DOWN = 1                    # the axis that points down: y
#: Primitives with an extent this module knows how to box.
SOLIDS = ("box", "plane", "sphere", "cylinder", "cone", "torus", "capsule",
          "extrude", "text", "mesh")

NUMERIC = ("x", "y", "z", "rotation", "rotationX", "rotationY", "scaleX", "scaleY", "scaleZ",
           "width", "height", "depth", "radius", "opacity", "start", "end")
FLAGS = ("castShadow", "receiveShadow", "shadowCatcher", "visible")
TEXTS = ("primitive", "parent", "path", "text", "mesh", "material")
#: What a camera and a light carry, typed, from the document and from eval.
CAMERA_NUMERIC = ("x", "y", "z", "yaw", "pitch", "roll", "focalLength", "fov", "sensorWidth",
                  "near", "start", "end")
LIGHT_NUMERIC = ("x", "y", "z", "yaw", "pitch", "intensity")

#: The screen-space ambiguity check (`reads-merged`): a hero whose screen
#: rectangle lies over a farther surface's top face by more than this share
#: of its own area, at a height above that surface under this share of its
#: distance from the camera, with no shadow of it on the surface, for at
#: least MERGED_SECONDS, reads as lying on (or in) the surface.
OVERLAP_SHARE = 0.30
CLEARANCE_RATIO = 0.25
MERGED_SECONDS = 0.5
#: The schema's camera defaults: sensorWidth 36 mm, near 0.1; with neither
#: focalLength nor fov the engine's default field of view is used.
SENSOR_WIDTH, NEAR, DEFAULT_FOV = 36.0, 0.1, 60.0
SHADOW_TYPES = ("directional", "point", "spot", "rect-area", "disk-area", "sphere-area")

Box = dict      # {"lo": [x, y, z], "hi": [x, y, z], "approximate": bool}


# --------------------------------------------------------------------------- #
# the document
# --------------------------------------------------------------------------- #

def _float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _bool(value) -> bool | None:
    if value is None:
        return None
    return str(value).strip().lower() in ("true", "1", "yes")


def _parse(doc_text: str):
    try:
        return ET.fromstring(doc_text.encode("utf-8") if isinstance(doc_text, str) else doc_text)
    except ET.ParseError:
        return None


def _row(el, prefix: str) -> dict:
    row: dict = {"id": prefix + (el.get("id") or ""), "name": el.get("id") or ""}
    for key in TEXTS:
        row[key] = el.get(key)
    if row["parent"]:
        row["parent"] = prefix + row["parent"]
    for key in NUMERIC:
        row[key] = _float(el.get(key))
    for key in FLAGS:
        row[key] = _bool(el.get(key))
    if row["primitive"] == "text" and not row["text"]:
        row["text"] = (el.text or "").strip()
    return row


def document_objects(doc_text: str, *, symbol: str | None = None, prefix: str = "") -> list[dict]:
    """The object3D nodes of one `<symbol id=symbol>` (or, with no symbol,
    those drawn outside any symbol), typed, in document order, their ids and
    parents qualified by `prefix` (`shot-1.1/`, the include's id as eval
    names them)."""
    root = _parse(doc_text)
    if root is None:
        return []
    if symbol is None:
        inside = {id(o) for s in root.iter("symbol") for o in s.iter("object3D")}
        elements = [o for o in root.iter("object3D") if id(o) not in inside]
    else:
        elements = next((list(s.iter("object3D")) for s in root.iter("symbol")
                         if s.get("id") == symbol), [])
    return [_row(el, prefix) for el in elements if el.get("id")]


def _composed(doc_text: str, prefix: str, base: Path, depth: int = 0) -> list[dict]:
    root = _parse(doc_text)
    if root is None:
        return []
    out: list[dict] = []
    for inc in root.iter("include"):
        src, sym, iid = inc.get("src"), inc.get("symbol"), inc.get("id") or ""
        path = Path(base) / (src or "")
        if not src or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        sub = f"{prefix}{iid}/" if iid else prefix
        if sym:
            out += document_objects(text, symbol=sym, prefix=sub)
        elif depth < 3:
            out += _composed(text, sub, path.parent, depth + 1)
    for inst in root.iter("instance"):
        sym, iid = inst.get("symbol"), inst.get("id") or ""
        if sym:
            out += document_objects(doc_text, symbol=sym, prefix=f"{prefix}{iid}/" if iid else prefix)
    out += document_objects(doc_text, symbol=None, prefix=prefix)
    return out


def scene_objects(scene_text: str, base: Path) -> list[dict]:
    """Every object3D a scene draws: through each `<include src symbol>`
    (the file read from `base`, the ids under the include's id), each
    `<instance symbol>` of its own symbols, and the loose ones."""
    return _composed(scene_text, "", Path(base))


def _meta_values(doc_text: str, name: str) -> list[str]:
    """The `value` of every `<meta name=name value>` in the document."""
    out: list[str] = []
    for tag in re.findall(r"<meta\b[^>]*>", doc_text):
        key = re.search(r'\bname="([^"]*)"', tag)
        value = re.search(r'\bvalue="([^"]*)"', tag)
        if key and value and key.group(1) == name:
            out.append(value.group(1))
    return out


def rests_declared(doc_text: str, prefix: str = "") -> list[dict]:
    """Where the document says the hero comes to rest: each
    `<meta name="hero-rests" value="<hero> <surface> <seconds>"/>`."""
    out: list[dict] = []
    for value in _meta_values(doc_text, REST_META):
        parts = value.split()
        at = _float(parts[2]) if len(parts) == 3 else None
        if at is not None:
            out.append({"hero": prefix + parts[0], "surface": prefix + parts[1], "at": at})
    return out


def attachments_declared(doc_text: str, prefix: str = "") -> list[dict]:
    """What the document says the hero is joined to by design: each
    `<meta name="hero-attached" value="<hero> <object> [more ...]"/>`, one
    row per object. An object ending `*` names every id starting with what
    precedes it; a bare `*` would switch the check off and is ignored."""
    out: list[dict] = []
    for value in _meta_values(doc_text, ATTACH_META):
        hero, *objects = value.split() or [""]
        out += [{"hero": prefix + hero, "object": prefix + obj}
                for obj in objects if obj != "*"]
    return out


def _scene_declared(scene_text: str, base: Path, declared) -> list[dict]:
    root = _parse(scene_text)
    if root is None:
        return []
    out = declared(scene_text)
    for inc in root.iter("include"):
        src, iid = inc.get("src"), inc.get("id") or ""
        path = Path(base) / (src or "")
        if src and path.is_file():
            out += declared(path.read_text(encoding="utf-8", errors="replace"),
                            prefix=f"{iid}/" if iid else "")
    return out


def scene_rests(scene_text: str, base: Path) -> list[dict]:
    """The declared rests of a scene and of every file it includes, qualified."""
    return _scene_declared(scene_text, base, rests_declared)


def scene_attachments(scene_text: str, base: Path) -> list[dict]:
    """The declared attachments of a scene and of every file it includes,
    qualified."""
    return _scene_declared(scene_text, base, attachments_declared)


def document_duration(doc_text: str) -> float:
    """The `<project duration>` in seconds, 0 when absent."""
    root = _parse(doc_text)
    project = root.find("project") if root is not None else None
    return _float(project.get("duration")) or 0.0 if project is not None else 0.0


def document_frame(doc_text: str, default=(1920, 1080)) -> tuple[int, int]:
    """The `<project width height>` in pixels."""
    root = _parse(doc_text)
    project = root.find("project") if root is not None else None
    if project is None:
        return default
    w, h = _float(project.get("width")), _float(project.get("height"))
    return (int(w), int(h)) if w and h else default


def _camera_row(el, prefix: str) -> dict:
    row = {"id": prefix + (el.get("id") or ""), "kind": "camera",
           "target": (prefix + el.get("target")) if el.get("target") else None,
           "projection": el.get("projection") or "perspective",
           "active": _bool(el.get("active"))}
    for key in CAMERA_NUMERIC:
        row[key] = _float(el.get(key))
    return row


def document_cameras(doc_text: str, *, symbol: str | None = None, prefix: str = "") -> list[dict]:
    """The `<camera>` nodes of one symbol (or those outside any), typed,
    their ids and targets qualified by `prefix`, in document order."""
    root = _parse(doc_text)
    if root is None:
        return []
    if symbol is None:
        inside = {id(c) for s in root.iter("symbol") for c in s.iter("camera")}
        elements = [c for c in root.iter("camera") if id(c) not in inside]
    else:
        elements = next((list(s.iter("camera")) for s in root.iter("symbol")
                         if s.get("id") == symbol), [])
    return [_camera_row(el, prefix) for el in elements if el.get("id")]


def scene_cameras(scene_text: str, base: Path) -> list[dict]:
    """Every camera a scene draws through its includes, instances and its own."""
    root = _parse(scene_text)
    if root is None:
        return []
    out: list[dict] = []
    for inc in root.iter("include"):
        src, sym, iid = inc.get("src"), inc.get("symbol"), inc.get("id") or ""
        path = Path(base) / (src or "")
        if src and sym and path.is_file():
            out += document_cameras(path.read_text(encoding="utf-8", errors="replace"),
                                    symbol=sym, prefix=f"{iid}/" if iid else "")
    for inst in root.iter("instance"):
        if inst.get("symbol"):
            iid = inst.get("id") or ""
            out += document_cameras(scene_text, symbol=inst.get("symbol"),
                                    prefix=f"{iid}/" if iid else "")
    return out + document_cameras(scene_text)


def document_lights(doc_text: str) -> list[dict]:
    """The document's `<lights>`: id, type, position, yaw/pitch, intensity
    (default 1) and castShadow (the schema's default: false)."""
    root = _parse(doc_text)
    if root is None:
        return []
    out = []
    for lights in root.findall("lights"):
        for el in lights.findall("light"):
            row = {"id": el.get("id") or "", "type": el.get("type") or "",
                   "castShadow": bool(_bool(el.get("castShadow")))}
            for key in LIGHT_NUMERIC:
                row[key] = _float(el.get(key))
            if row["intensity"] is None:
                row["intensity"] = 1.0
            out.append(row)
    return out


# --------------------------------------------------------------------------- #
# boxes
# --------------------------------------------------------------------------- #

_PATH_TOKEN = re.compile(r"[A-Za-z]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_PATH_ARITY = {"M": 2, "L": 2, "T": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "A": 7}


def path_bounds(path: str) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """The extent of SVG path data, control points included (conservative):
    ((min x, min y), (max x, max y)), or None for no point."""
    tokens = _PATH_TOKEN.findall(path or "")
    x = y = 0.0
    start = (0.0, 0.0)
    cmd = None
    points: list[tuple[float, float]] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.isalpha():
            cmd = tok
            i += 1
            if cmd in "Zz":
                x, y = start
            continue
        if cmd is None or cmd in "Zz":
            break
        upper, rel = cmd.upper(), cmd.islower()
        n = _PATH_ARITY.get(upper)
        if n is None or i + n > len(tokens):
            break
        nums = [float(t) for t in tokens[i:i + n]]
        i += n
        if upper == "H":
            x = x + nums[0] if rel else nums[0]
            points.append((x, y))
        elif upper == "V":
            y = y + nums[0] if rel else nums[0]
            points.append((x, y))
        elif upper == "A":
            nx, ny = nums[5], nums[6]
            x, y = (x + nx, y + ny) if rel else (nx, ny)
            points.append((x, y))
        else:
            for k in range(0, n, 2):
                px, py = nums[k], nums[k + 1]
                px, py = (x + px, y + py) if rel else (px, py)
                points.append((px, py))
            x, y = points[-1]
        if upper == "M":
            start = (x, y)
            cmd = "l" if rel else "L"
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (min(xs), min(ys)), (max(xs), max(ys))


def _box(lo, hi, approximate: bool = False) -> Box:
    return {"lo": [float(v) for v in lo], "hi": [float(v) for v in hi],
            "approximate": approximate}


def local_box(obj: dict) -> Box | None:
    """The primitive's box in its own frame, from the schema's attributes
    and defaults; None for a primitive with no extent this module can give."""
    kind = obj.get("primitive")
    r = obj.get("radius") if obj.get("radius") is not None else DEFAULT_RADIUS
    w = obj.get("width") if obj.get("width") is not None else 2 * r
    h = obj.get("height")
    d = obj.get("depth") if obj.get("depth") is not None else DEFAULT_DEPTH
    if kind == "box":
        h = 2 * r if h is None else h
        return _box((-w / 2, -h / 2, -d / 2), (w / 2, h / 2, d / 2))
    if kind == "plane":
        h = 2 * r if h is None else h
        return _box((-w / 2, -h / 2, 0.0), (w / 2, h / 2, 0.0))
    if kind == "sphere":
        return _box((-r, -r, -r), (r, r, r))
    if kind in ("cylinder", "cone", "capsule"):
        h = (4 * r if kind == "capsule" else 2 * r) if h is None else h
        return _box((-r, -h / 2, -r), (r, h / 2, r))
    if kind == "torus":
        tube = 0.7 * r if h is None else h
        return _box((-(r + tube / 2), -tube / 2, -(r + tube / 2)),
                    (r + tube / 2, tube / 2, r + tube / 2))
    if kind == "extrude":
        span = path_bounds(obj.get("path") or "")
        if span is None:
            return None
        (x0, y0), (x1, y1) = span
        return _box((x0, y0, -d / 2), (x1, y1, d / 2))
    if kind == "text":
        size = TEXT_SIZE if h is None else h
        width = TEXT_ADVANCE * size * max(1, len(obj.get("text") or ""))
        return _box((0.0, -size / 2, -d / 2), (width, size / 2, d / 2), approximate=True)
    if kind == "mesh":
        return _box((-0.5, -0.5, -0.5), (0.5, 0.5, 0.5), approximate=True)
    return None


def _rotation(obj: dict) -> list[list[float]]:
    """Rz * Ry * Rx in the stored coordinates (the schema's order)."""
    ax, ay, az = (math.radians(obj.get(k) or 0.0) for k in ("rotationX", "rotationY", "rotation"))
    cx, sx, cy, sy, cz, sz = math.cos(ax), math.sin(ax), math.cos(ay), math.sin(ay), math.cos(az), math.sin(az)
    rx = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    ry = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    rz = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    return _mul(rz, _mul(ry, rx))


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _matrix(obj: dict) -> tuple[list[list[float]], list[float]]:
    """(linear part, translation): scale, then rotate, then move."""
    rot = _rotation(obj)
    scale = [obj.get(k) if obj.get(k) is not None else 1.0 for k in ("scaleX", "scaleY", "scaleZ")]
    linear = [[rot[i][j] * scale[j] for j in range(3)] for i in range(3)]
    return linear, [obj.get(k) or 0.0 for k in ("x", "y", "z")]


def _compose(parent, local):
    (pl, pt), (ll, lt) = parent, local
    return _mul(pl, ll), [sum(pl[i][k] * lt[k] for k in range(3)) + pt[i] for i in range(3)]


def _apply(transform, p):
    linear, t = transform
    return [sum(linear[i][k] * p[k] for k in range(3)) + t[i] for i in range(3)]


def world_bounds(objects: list[dict]) -> dict[str, Box]:
    """Each object's world axis-aligned box: the eight corners of its local
    box through its transform and its parents' (by `parent`), the extent
    taken. An object with no box is skipped; a parent cycle is cut."""
    by_id = {o["id"]: o for o in objects}
    memo: dict[str, tuple] = {}

    def world(oid: str, seen: frozenset) -> tuple:
        if oid in memo:
            return memo[oid]
        obj = by_id[oid]
        local = _matrix(obj)
        pid = obj.get("parent")
        if pid and pid in by_id and pid not in seen:
            local = _compose(world(pid, seen | {oid}), local)
        memo[oid] = local
        return local

    out: dict[str, Box] = {}
    for obj in objects:
        box = local_box(obj)
        if box is None:
            continue
        transform = world(obj["id"], frozenset({obj["id"]}))
        lo, hi = box["lo"], box["hi"]
        corners = [_apply(transform, [lo[0] if i & 1 else hi[0], lo[1] if i & 2 else hi[1],
                                      lo[2] if i & 4 else hi[2]]) for i in range(8)]
        out[obj["id"]] = _box([min(c[k] for c in corners) for k in range(3)],
                              [max(c[k] for c in corners) for k in range(3)],
                              box["approximate"])
    return out


def _short(oid: str) -> str:
    return str(oid).rsplit("/", 1)[-1]


def hero_groups(objects: list[dict]) -> dict[str, list[str]]:
    """Each hero (`hero-` id) with every object that follows it through
    `parent`, the hero first, the rest in document order."""
    by_id = {o["id"]: o for o in objects}
    groups: dict[str, list[str]] = {o["id"]: [o["id"]] for o in objects if is_hero(_short(o["id"]))}
    for obj in objects:
        if obj["id"] in groups:
            continue
        pid, seen = obj.get("parent"), set()
        while pid and pid in by_id and pid not in seen:
            if pid in groups:
                groups[pid].append(obj["id"])
                break
            seen.add(pid)
            pid = by_id[pid].get("parent")
    return groups


def ancestors(objects: list[dict], oid: str) -> list[str]:
    """The nodes `oid` follows through `parent`, the nearest first; a
    parent cycle is cut."""
    by_id = {o["id"]: o for o in objects}
    out: list[str] = []
    pid = (by_id.get(oid) or {}).get("parent")
    while pid and pid in by_id and pid != oid and pid not in out:
        out.append(pid)
        pid = by_id[pid].get("parent")
    return out


def joined(hero: str, oid: str, attached) -> bool:
    """Whether an attachment row joins `hero` to `oid`: the object named,
    or a prefix ending `*` that `oid` starts with."""
    for row in attached:
        obj = row["object"]
        if row["hero"] != hero:
            continue
        if oid == obj or (obj.endswith("*") and oid.startswith(obj[:-1])):
            return True
    return False


def group_bounds(bounds: dict[str, Box], groups: dict[str, list[str]]) -> dict[str, Box]:
    """The union box of each hero's group, under the hero's id."""
    out: dict[str, Box] = {}
    for hero, members in groups.items():
        boxes = [bounds[m] for m in members if m in bounds]
        if boxes:
            out[hero] = _box([min(b["lo"][k] for b in boxes) for k in range(3)],
                             [max(b["hi"][k] for b in boxes) for k in range(3)],
                             any(b["approximate"] for b in boxes))
    return out


def solids(objects: list[dict], exclude=()) -> list[str]:
    """The set's solids: a boxable primitive, opaque enough, drawn, no
    shadow catcher and not opted out of shadowing both ways (which is how
    light shafts, contact blobs and flares declare themselves)."""
    skip = set(exclude)
    out = []
    for obj in objects:
        if obj["id"] in skip or obj.get("primitive") not in SOLIDS:
            continue
        if obj.get("shadowCatcher") or obj.get("visible") is False:
            continue
        if obj.get("opacity") is not None and obj["opacity"] < SOLID_OPACITY:
            continue
        if obj.get("castShadow") is False and obj.get("receiveShadow") is False:
            continue
        out.append(obj["id"])
    return out


def overlay(objects: list[dict], frame: dict | None) -> list[dict]:
    """The objects as the engine evaluated them at one time: each node's
    `props` over the document's values, and only the nodes the frame graph
    holds and draws (an object before its `start`, after its `end` or under
    a false `condition` is not in it). With no frame, the document as is."""
    if frame is None:
        return [dict(o) for o in objects]
    result = frame.get("result") if isinstance(frame, dict) and "result" in frame else frame
    nodes = (result or {}).get("nodes") or []
    by_id = {n.get("id"): n for n in nodes if isinstance(n, dict)}
    out = []
    for obj in objects:
        node = by_id.get(obj["id"])
        if node is None or node.get("draw") is False:
            continue
        row = dict(obj)
        for key, value in (node.get("props") or {}).items():
            if key in NUMERIC and isinstance(value, (int, float)):
                row[key] = float(value)
        out.append(row)
    return out


# --------------------------------------------------------------------------- #
# the records
# --------------------------------------------------------------------------- #

def overlap(a: Box, b: Box) -> tuple[float, str] | None:
    """How deep two boxes interpenetrate: the least overlap over the three
    axes (the shortest way out) and that axis; None when they are apart."""
    depths = [min(a["hi"][k], b["hi"][k]) - max(a["lo"][k], b["lo"][k]) for k in range(3)]
    if min(depths) <= 0:
        return None
    k = min(range(3), key=depths.__getitem__)
    return depths[k], AXES[k]


def tolerance_of(box: Box) -> float:
    """TOLERANCE_SHARE of the box's smallest non-zero dimension."""
    extents = [hi - lo for lo, hi in zip(box["lo"], box["hi"]) if hi - lo > 1e-9]
    return TOLERANCE_SHARE * min(extents) if extents else 0.0


# --------------------------------------------------------------------------- #
# the screen: what the camera sees
# --------------------------------------------------------------------------- #

def overlay_cameras(cameras: list[dict], frame: dict | None) -> tuple[list[dict], str | None]:
    """The cameras with eval's props over the document's values, and the id
    of the one the frame renders (the result's `camera`, an index into the
    nodes), else the last active one."""
    result = (frame or {}).get("result") if isinstance(frame, dict) and "result" in frame else frame
    nodes = (result or {}).get("nodes") or []
    by_id = {n.get("id"): n for n in nodes if isinstance(n, dict)}
    out = []
    for cam in cameras:
        row = dict(cam)
        node = by_id.get(cam["id"])
        for key, value in ((node or {}).get("props") or {}).items():
            if key in CAMERA_NUMERIC and isinstance(value, (int, float)):
                row[key] = float(value)
        if node is not None or not nodes:
            out.append(row)
    active = None
    index = (result or {}).get("camera")
    if isinstance(index, int) and 0 <= index < len(nodes):
        active = nodes[index].get("id")
    if active not in {c["id"] for c in out}:
        live = [c for c in out if c.get("active") is not False]
        active = live[-1]["id"] if live else None
    return out, active


def overlay_lights(lights: list[dict], frame: dict | None) -> list[dict]:
    """The lights with eval's evaluated `elements` (key = the light's id) over them."""
    result = (frame or {}).get("result") if isinstance(frame, dict) and "result" in frame else frame
    props = {e.get("key"): e.get("props") or {} for e in (result or {}).get("elements") or []
             if isinstance(e, dict) and e.get("element") == "lightType"}
    out = []
    for light in lights:
        row = dict(light)
        for key, value in props.get(light["id"], {}).items():
            if key in LIGHT_NUMERIC and isinstance(value, (int, float)):
                row[key] = float(value)
        out.append(row)
    return out


def direction(yaw: float | None, pitch: float | None) -> list[float]:
    """The unit vector a camera (or a light) with this yaw and pitch points
    along: yaw positive turns toward +x, pitch positive looks up, and y
    points down, so (0, 0) is +z and pitch -90 is straight down (+y)."""
    ya, pa = math.radians(yaw or 0.0), math.radians(pitch or 0.0)
    return [math.sin(ya) * math.cos(pa), -math.sin(pa), math.cos(ya) * math.cos(pa)]


def _centre(box: Box) -> list[float]:
    return [(lo + hi) / 2 for lo, hi in zip(box["lo"], box["hi"])]


def camera_pose(cam: dict, bounds: dict[str, Box] | None = None) -> dict:
    """Position, the three axes (forward, screen right, screen down) and
    the focal length in pixels per unit of frame width. A `target` present
    in `bounds` replaces yaw and pitch (aimed at its box's centre), as the
    schema says; roll turns the screen axes about forward."""
    pos = [cam.get(k) or 0.0 for k in ("x", "y", "z")]
    yaw, pitch = cam.get("yaw"), cam.get("pitch")
    target = (bounds or {}).get(cam.get("target") or "")
    if target is not None:
        c = _centre(target)
        dx, dy, dz = c[0] - pos[0], c[1] - pos[1], c[2] - pos[2]
        yaw = math.degrees(math.atan2(dx, dz))
        pitch = math.degrees(math.atan2(-dy, math.hypot(dx, dz)))
    ya, pa = math.radians(yaw or 0.0), math.radians(pitch or 0.0)
    forward = direction(yaw, pitch)
    right = [math.cos(ya), 0.0, -math.sin(ya)]
    down = [math.sin(ya) * math.sin(pa), math.cos(pa), math.cos(ya) * math.sin(pa)]
    ra = math.radians(cam.get("roll") or 0.0)
    if ra:
        cr, sr = math.cos(ra), math.sin(ra)
        right, down = ([cr * r + sr * d for r, d in zip(right, down)],
                       [cr * d - sr * r for r, d in zip(right, down)])
    if cam.get("focalLength"):
        tan_half = (cam.get("sensorWidth") or SENSOR_WIDTH) / 2 / cam["focalLength"]
    else:
        tan_half = math.tan(math.radians(cam.get("fov") or DEFAULT_FOV) / 2)
    return {"pos": pos, "forward": forward, "right": right, "down": down,
            "tan_half": tan_half, "near": cam.get("near") or NEAR, "yaw": yaw, "pitch": pitch}


def project(point, pose: dict, frame: tuple[int, int]) -> tuple[float, float, float] | None:
    """A world point in pixels (x right, y down from the top-left) and its
    depth along the camera's forward axis; None when behind the near plane.
    The focal distance in pixels is (frame width / 2) / tan(fov / 2), the
    same across the height, as the schema's `fov` says."""
    v = [p - c for p, c in zip(point, pose["pos"])]
    depth = sum(a * b for a, b in zip(v, pose["forward"]))
    if depth <= pose["near"]:
        return None
    f = frame[0] / 2 / pose["tan_half"]
    x = sum(a * b for a, b in zip(v, pose["right"]))
    y = sum(a * b for a, b in zip(v, pose["down"]))
    return frame[0] / 2 + f * x / depth, frame[1] / 2 + f * y / depth, depth


def _corners(box: Box) -> list[list[float]]:
    lo, hi = box["lo"], box["hi"]
    return [[lo[0] if i & 1 else hi[0], lo[1] if i & 2 else hi[1], lo[2] if i & 4 else hi[2]]
            for i in range(8)]


def screen_rect(box: Box, pose: dict, frame: tuple[int, int], *, top_only: bool = False
                ) -> tuple[float, float, float, float] | None:
    """The screen rectangle (x0, y0, x1, y1) of a box's corners in front of
    the camera, clipped to the frame; None when nothing is in front or in
    frame. `top_only` takes the top face (the least y) alone."""
    corners = _corners(box)
    if top_only:
        corners = [c for c in corners if c[DOWN] == box["lo"][DOWN]]
    pts = [p for p in (project(c, pose, frame) for c in corners) if p is not None]
    if not pts:
        return None
    x0, x1 = max(0.0, min(p[0] for p in pts)), min(float(frame[0]), max(p[0] for p in pts))
    y0, y1 = max(0.0, min(p[1] for p in pts)), min(float(frame[1]), max(p[1] for p in pts))
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def screen_boxes(objects: list[dict], cameras: list[dict], eval_frame: dict | None,
                 frame: tuple[int, int]) -> dict[str, tuple[float, float, float, float]] | None:
    """Each object3D's screen rectangle at one evaluated time, through the
    camera that frame renders: the objects as eval drew them (`overlay`),
    their world boxes, the active camera with eval's props (`overlay_cameras`)
    and its pose. An object off screen or behind the camera has no entry.
    None when no camera renders the frame or it is orthographic, whose
    projection `project` does not model (AUTO-110)."""
    now = overlay(objects, eval_frame)
    bounds = world_bounds(now)
    cams, active = overlay_cameras(list(cameras), eval_frame)
    cam = next((c for c in cams if c["id"] == active), None)
    if cam is None or cam.get("projection") == "orthographic":
        return None
    pose = camera_pose(cam, bounds)
    out = {}
    for oid, box in bounds.items():
        rect = screen_rect(box, pose, frame)
        if rect is not None:
            out[oid] = rect
    return out


def in_cells(boxes: dict[str, tuple], cells: dict[str, tuple]) -> dict[str, list[str]]:
    """Per cell (name -> pixel rectangle), the ids whose screen rectangle
    overlaps it, in the order of `boxes`; a cell with none gets []."""
    return {name: [oid for oid, rect in boxes.items() if _intersect(rect, cell)]
            for name, cell in cells.items()}


def _area(r) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1]) if r else 0.0


def _intersect(a, b):
    if not a or not b:
        return None
    r = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return r if r[2] > r[0] and r[3] > r[1] else None


def shadow_on(hero: Box, surface: Box, lights: list[dict], pose: dict, frame: tuple[int, int],
              casts: bool) -> str | None:
    """The light whose shadow of the hero (its centre, cast along the
    light) lands on the surface's top face inside the frame, or None. A
    hero that casts no shadow, a light with castShadow false, no intensity
    or of a kind with no direction (ambient, dome) gives none. Occlusion of
    the light by the set is not modelled."""
    if not casts:
        return None
    c = _centre(hero)
    top = surface["lo"][DOWN]
    for light in lights:
        if not light.get("castShadow") or (light.get("intensity") or 0) <= 0:
            continue
        if light.get("type") not in SHADOW_TYPES:
            continue
        if light["type"] == "directional":
            d = direction(light.get("yaw"), light.get("pitch"))
        else:
            src = [light.get(k) or 0.0 for k in ("x", "y", "z")]
            d = [a - b for a, b in zip(c, src)]
        if d[DOWN] <= 1e-9:
            continue                         # travels up, or level: never reaches the top face
        t = (top - c[DOWN]) / d[DOWN]
        if t < 0:
            continue
        p = [a + t * b for a, b in zip(c, d)]
        if not (surface["lo"][0] <= p[0] <= surface["hi"][0]
                and surface["lo"][2] <= p[2] <= surface["hi"][2]):
            continue
        on = project(p, pose, frame)
        if on is not None and 0 <= on[0] <= frame[0] and 0 <= on[1] <= frame[1]:
            return light["id"]
    return None


def _segment_hits(a, b, box: Box) -> bool:
    """Whether the segment a->b passes through the box (slab test)."""
    t0, t1 = 0.0, 1.0
    for k in range(3):
        d = b[k] - a[k]
        lo, hi = box["lo"][k], box["hi"][k]
        if abs(d) < 1e-12:
            if a[k] < lo or a[k] > hi:
                return False
            continue
        u, v = (lo - a[k]) / d, (hi - a[k]) / d
        t0, t1 = max(t0, min(u, v)), min(t1, max(u, v))
        if t0 > t1:
            return False
    return True


def top_visible(sid: str, bounds: dict[str, Box], others, pose: dict) -> bool:
    """Whether the camera sees the surface's top face: the segment from the
    camera to the face's centre (a hair above it) crosses no other solid."""
    sb = bounds[sid]
    c = _centre(sb)
    face = [c[0], sb["lo"][DOWN] - 1e-3 * max(1.0, abs(sb["lo"][DOWN])), c[2]]
    return not any(_segment_hits(pose["pos"], face, bounds[o]) for o in others
                   if o != sid and o in bounds)


def casts_shadow(objects: list[dict], members: list[str]) -> bool:
    """Whether any drawn part of a hero group casts a shadow (the schema's
    default is true)."""
    by_id = {o["id"]: o for o in objects}
    return any(by_id[m].get("castShadow") is not False and local_box(by_id[m]) is not None
               for m in members if m in by_id)


def ambiguous(bounds: dict[str, Box], heroes: dict[str, list[str]], surfaces: list[str],
              objects: list[dict], pose: dict, frame: tuple[int, int], lights: list[dict], *,
              time: float, share: float = OVERLAP_SHARE, ratio: float = CLEARANCE_RATIO
              ) -> list[dict]:
    """One time's candidates for `reads-merged`: a hero over a farther
    surface's top face on screen (by more than `share` of the hero's
    screen area), a little above it (clearance over the tolerance and under
    `ratio` of the camera distance), the camera above the face's plane so
    the face is what it sees, and no shadow of the hero on it."""
    out = []
    for hero, members in heroes.items():
        hb = bounds.get(hero)
        hr = screen_rect(hb, pose, frame) if hb else None
        if not hr:
            continue
        hc = _centre(hb)
        dist = math.dist(hc, pose["pos"])
        h_depth = project(hc, pose, frame)
        casts = casts_shadow(objects, members)
        for sid in surfaces:
            sb = bounds.get(sid)
            if sb is None:
                continue
            top = sb["lo"][DOWN]
            clearance = top - hb["hi"][DOWN]
            if clearance <= tolerance_of(hb) or dist <= 0 or clearance / dist >= ratio:
                continue
            if pose["pos"][DOWN] >= top:
                continue                     # the camera is level with or below the face
            if not top_visible(sid, bounds, surfaces, pose):
                continue                     # another solid hides the face (an apron under a top)
            fr = screen_rect(sb, pose, frame, top_only=True)
            over = _area(_intersect(hr, fr)) / _area(hr)
            if over <= share:
                continue
            s_depth = project(_centre(sb), pose, frame)
            if h_depth is None or s_depth is None or s_depth[2] <= h_depth[2]:
                continue
            light = shadow_on(hb, sb, lights, pose, frame, casts)
            if light:
                continue
            out.append({"time": time, "hero": hero, "object": sid, "overlap": round(over, 3),
                        "clearance": round(clearance, 2), "distance": round(dist, 1),
                        "ratio": round(clearance / dist, 3), "casts": casts})
    return out


def merged_spans(candidates: list[dict], times: list[float], *,
                 step: float = STEP, minimum: float = MERGED_SECONDS) -> list[dict]:
    """The candidates as spans per hero and surface, consecutive sampled
    times joined; a span shorter than `minimum` seconds is dropped (a
    passing overlap reads as a pass, not a merge)."""
    order = {t: i for i, t in enumerate(sorted(float(t) for t in times))}
    by_pair: dict[tuple[str, str], list[dict]] = {}
    for c in sorted(candidates, key=lambda r: r["time"]):
        by_pair.setdefault((c["hero"], c["object"]), []).append(c)
    spans = []
    for (hero, obj), rows in by_pair.items():
        runs: list[list[dict]] = []
        for r in rows:
            if runs and order.get(r["time"], -9) == order.get(runs[-1][-1]["time"], -99) + 1:
                runs[-1].append(r)
            else:
                runs.append([r])
        for run in runs:
            seconds = run[-1]["time"] - run[0]["time"] + step
            if seconds + 1e-9 < minimum:
                continue
            spans.append({"hero": hero, "object": obj, "first": run[0]["time"],
                          "last": run[-1]["time"], "seconds": round(seconds, 3),
                          "samples": len(run), "overlap": max(r["overlap"] for r in run),
                          "clearance": min(r["clearance"] for r in run),
                          "ratio": max(r["ratio"] for r in run),
                          "casts": all(r["casts"] for r in run)})
    return sorted(spans, key=lambda s: (s["first"], s["hero"], s["object"]))


def merged_advice(span: dict) -> str:
    """The fix for a `reads-merged` span: the three grounding cues."""
    obj = _short(span["object"])
    shadow = ("cast a shadow onto it: a shadow-casting light (castShadow=\"true\") whose "
              f"direction lands the hero's shadow on `{obj}` inside the frame"
              if span.get("casts") else
              "cast a shadow onto it: the hero has castShadow=\"false\" on every part, so turn "
              f"it on, and aim a shadow-casting light so the shadow lands on `{obj}` in frame")
    return (f"give the frame a grounding cue: {shadow}; or lift the camera's line of sight "
            f"toward the plane of `{obj}`'s top so the hero is seen against what is behind it; "
            f"or separate them on screen (frame the hero clear of `{obj}`), or keep the overlap "
            f"under {MERGED_SECONDS:g}s")


def penetrations(bounds: dict[str, Box], hero_ids, set_ids, tolerance: float | None = None,
                 *, time: float = 0.0, attached=()) -> list[dict]:
    """Every hero box inside a set solid by more than the tolerance
    (default: a tenth of that hero's smallest dimension, so a resting
    contact passes and a merge fails), as records. A solid an `attached`
    row joins the hero to (`{"hero", "object"}`, see `joined`) touches it
    by design and is skipped."""
    out: list[dict] = []
    for hero in hero_ids:
        hb = bounds.get(hero)
        if hb is None:
            continue
        tol = tolerance_of(hb) if tolerance is None else float(tolerance)
        for oid in set_ids:
            ob = bounds.get(oid)
            if ob is None or oid == hero or joined(hero, oid, attached):
                continue
            hit = overlap(hb, ob)
            if hit and hit[0] > tol:
                out.append({"time": time, "hero": hero, "object": oid,
                            "depth": round(hit[0], 3), "axis": hit[1],
                            "tolerance": round(tol, 3),
                            "approximate": bool(hb["approximate"] or ob["approximate"]),
                            # y points down: a surface's top is its least y,
                            # a hero's lowest point its greatest
                            "surface_top": round(ob["lo"][DOWN], 3),
                            "hero_bottom": round(hb["hi"][DOWN], 3)})
    return out


def floating(bounds: dict[str, Box], hero: str, surface: str, at: float | None = None,
             tolerance: float | None = None) -> dict | None:
    """At a declared rest: the hero's lowest point more than the tolerance
    above the surface's top is a hero floating; None when it touches."""
    hb, sb = bounds.get(hero), bounds.get(surface)
    if hb is None or sb is None:
        return None
    tol = tolerance_of(hb) if tolerance is None else float(tolerance)
    gap = sb["lo"][DOWN] - hb["hi"][DOWN]        # y points down: top is the least y
    if gap <= tol:
        return None
    return {"time": at, "hero": hero, "surface": surface, "gap": round(gap, 3),
            "tolerance": round(tol, 3)}


def sample_times(shots: list[dict], step: float = STEP, *, duration: float | None = None
                 ) -> list[float]:
    """Every `step` seconds through each shot from its start (its end
    excluded); with no shots, through `duration`."""
    times: set[float] = set()
    spans = [(float(s.get("at", 0) or 0), float(s.get("seconds", 0) or 0)) for s in shots or []]
    if not spans and duration:
        spans = [(0.0, float(duration))]
    for at, seconds in spans:
        for k in range(max(0, math.ceil(seconds / step - 1e-9))):
            times.add(round(at + k * step, 3))
    return sorted(times)


def summarise(records: list[dict], *, floating: list[dict], times: list[float],
              evaluated: int, heroes: dict, solids: list[str], rests=(), attached=(),
              reads_merged=(), screen: str = "not checked: no camera") -> dict:
    """The report: the records, the worst per hero and object (its depth,
    time, how many samples and between which), the floats, the declared
    rests and attachments."""
    worst: dict[tuple[str, str], dict] = {}
    for r in records:
        key = (r["hero"], r["object"])
        row = worst.get(key)
        if row is None:
            worst[key] = dict(r, count=1, first=r["time"], last=r["time"])
            continue
        row["count"] += 1
        row["first"], row["last"] = min(row["first"], r["time"]), max(row["last"], r["time"])
        row["approximate"] = row["approximate"] or r["approximate"]
        if r["depth"] > row["depth"]:
            row.update(depth=r["depth"], time=r["time"], axis=r["axis"], tolerance=r["tolerance"],
                       hero_bottom=r.get("hero_bottom"), surface_top=r.get("surface_top"))
    return {"status": "ok", "times": list(times), "evaluated": evaluated, "heroes": heroes,
            "solids": solids, "rests": list(rests), "attached": list(attached),
            "penetrations": records, "worst": list(worst.values()), "floating": list(floating),
            "reads_merged": list(reads_merged), "screen": screen}


def evaluate(objects: list[dict], times: list[float], runner, *, rests=(), attached=(),
             cameras=(), lights=(), frame: tuple[int, int] = (1920, 1080),
             step: float = STEP) -> dict:
    """The check over one document: `runner(times)` -> one eval frame per
    time (the seam; production is one job on a host, a test a function).
    Every hero group's union box is intersected with every set solid at
    each time, except the solids the hero is attached to (the declared
    `attached` rows and the nodes it follows by `parent`); declared rests
    are evaluated too and checked for floating. With `cameras` (and
    `lights`, `frame` in pixels) the screen is checked as well: the spans
    where the hero reads as merged into a surface."""
    heroes = hero_groups(objects)
    parts = {p for ids in heroes.values() for p in ids}
    # A hero is attached to what it follows by `parent` as surely as to
    # what is declared; only the penetration path skips either (floating,
    # reads-merged and the shadows still see both).
    joints = list(attached) + [{"hero": h, "object": a} for h in heroes
                               for a in ancestors(objects, h)]
    base = {"times": list(times), "heroes": heroes, "solids": solids(objects, exclude=parts),
            "rests": list(rests), "attached": list(attached), "evaluated": 0,
            "penetrations": [], "worst": [], "floating": [], "reads_merged": [],
            "screen": "not checked"}
    if not heroes:
        return base | {"status": "skipped", "why": "no `hero-` object3D: nothing to keep out of the set"}
    if runner is None:
        return base | {"status": "skipped", "why": "no engine reachable to evaluate the frame graph "
                                                   "(RS_RENDER_HOSTS, or RS_RENDER_BIN)"}
    want = sorted({float(t) for t in times} | {float(r["at"]) for r in rests})
    frames = runner(want) or []
    sampled = {float(t) for t in times}
    records: list[dict] = []
    floats: list[dict] = []
    candidates: list[dict] = []
    evaluated = 0
    for t, fr in zip(want, frames):
        if not isinstance(fr, dict) or fr.get("ok") is False:
            continue
        now = overlay(objects, fr)
        evaluated += 1
        bounds = world_bounds(now)
        bounds |= group_bounds(bounds, heroes)
        present = solids(now, exclude=parts)
        if t in sampled:
            records += penetrations(bounds, list(heroes), present, time=t, attached=joints)
            if cameras:
                cams, active = overlay_cameras(list(cameras), fr)
                cam = next((c for c in cams if c["id"] == active), None)
                if cam is not None and cam.get("projection") != "orthographic":
                    pose = camera_pose(cam, bounds)
                    candidates += ambiguous(bounds, heroes, present, now, pose, frame,
                                            overlay_lights(list(lights), fr), time=t)
        for rest in rests:
            if float(rest["at"]) == t:
                row = floating(bounds, rest["hero"], rest["surface"], at=t)
                if row:
                    floats.append(row)
    screen = ("checked" if cameras else "not checked: no camera in the document")
    return summarise(records, floating=floats, times=times, evaluated=evaluated,
                     heroes=heroes, solids=base["solids"], rests=rests, attached=attached,
                     reads_merged=merged_spans(candidates, times, step=step), screen=screen)


def _shot_at(shots, t: float) -> str:
    for s in shots or []:
        at, seconds = float(s.get("at", 0) or 0), float(s.get("seconds", 0) or 0)
        if at <= t < at + seconds:
            return str(s.get("label", ""))
    return ""


def advice(row: dict, shots=(), rests=()) -> str:
    """The fix for one worst record, in the author's terms."""
    hero, obj = _short(row["hero"]), _short(row["object"])
    lift = row["depth"] + row.get("tolerance", 0.0)
    where = _shot_at(shots, row["time"])
    text = (f"move the path of / key {AXES[DOWN]} of `{hero}` so it clears {obj} by at least "
            f"{lift:.1f} units" + (f" through shot {where}" if where else ""))
    if row.get("axis") == AXES[DOWN] and row.get("surface_top") is not None \
            and row.get("hero_bottom") is not None and row["hero_bottom"] > row["surface_top"]:
        sink = row["hero_bottom"] - row["surface_top"]
        text += (f"; a resting hero's lowest point equals the surface's top: y points down, so "
                 f"`{obj}`'s top is y={row['surface_top']:g} and the hero's lowest point is at "
                 f"y={row['hero_bottom']:g}, {sink:.1f} below it -- key the hero's y {sink:.1f} "
                 "lower (more negative) where it rests")
    rest_shots = sorted({_shot_at(shots, float(r["at"])) for r in rests
                         if r["hero"] == row["hero"]} - {""})
    if rest_shots:
        text += f"; the hero rests only in shot {', '.join(rest_shots)}"
    else:
        text += (f"; if it is meant to come to rest on `{obj}`, declare it "
                 f"(`<meta name=\"{REST_META}\" value=\"{hero} {obj} <seconds>\"/>`) "
                 "and key the contact to touch, not sink")
    text += (f"; if `{hero}` and `{obj}` touch by design (one is attached to the other), do not "
             f"pull them apart: declare them joined (`<meta name=\"{ATTACH_META}\" "
             f"value=\"{hero} {obj}\"/>`; an id ending in * names every id with that prefix) "
             "and that overlap is no longer a penetration")
    return text


def findings(report: dict, *, symbol: str, shots=()) -> list[str]:
    """The report as the pipeline's finding strings: an error per worst
    penetration (a warning when a box is approximate), an error per float,
    a note when the check could not run or found nothing."""
    where = f"symbol {symbol}" if symbol else "the scene"
    if report.get("status") != "ok":
        return [f"note: {where}: geometry not checked: {report.get('why', '?')}"]
    out: list[str] = []
    rests = report.get("rests") or []
    for w in report.get("worst") or []:
        level = "warning" if w.get("approximate") else "error"
        about = (" (bounds approximate: a mesh is taken as its scale, text as an advance)"
                 if w.get("approximate") else "")
        out.append(f"{level}: {where}: hero `{_short(w['hero'])}` penetrates `{_short(w['object'])}` "
                   f"by {w['depth']:.1f} units along {w['axis']} at {w['time']:g}s (deepest of "
                   f"{w['count']} of {len(report.get('times') or [])} samples, {w['first']:g}s to "
                   f"{w['last']:g}s; tolerance {w['tolerance']:.1f}){about}: "
                   f"{advice(w, shots, rests)}")
    for f in report.get("floating") or []:
        out.append(f"error: {where}: hero `{_short(f['hero'])}` floats {f['gap']:.1f} units above "
                   f"`{_short(f['surface'])}` at {f['time']:g}s where it is declared to rest: "
                   f"lower the final keys so it touches the surface (within {f['tolerance']:.1f})")
    for m in report.get("reads_merged") or []:
        out.append(f"error: {where}: hero `{_short(m['hero'])}` reads merged into "
                   f"`{_short(m['object'])}` from {m['first']:g}s to {m['last']:g}s "
                   f"({m['seconds']:g}s, {m['samples']} samples): on screen {m['overlap']:.0%} of "
                   f"it lies over the surface's top, {m['clearance']:.1f} units above it "
                   f"({m['ratio']:.0%} of its distance from the camera, too little for parallax), "
                   f"and no shadow of it falls on the surface: {merged_advice(m)}")
    if not out:
        out.append(f"note: {where}: no penetration: the hero clears every set solid at "
                   f"{report.get('evaluated', 0)} sampled times"
                   + ("; and never reads merged on screen" if report.get("screen") == "checked"
                      else ""))
    return out


# --------------------------------------------------------------------------- #
# the runner
# --------------------------------------------------------------------------- #

def eval_script(mirror: str, job: str, binary: str, doc: str, times) -> str:
    """The one-line shell of the job: link the job directory from the
    mirror, enter it, then one `eval --format json` per time, each envelope
    on a line of its own. `binary` is already quoted (or a lookup)."""
    listed = " ".join(f"{float(t):g}" for t in times)
    return (f"{render.link_job_script(mirror, job)} && cd {shlex.quote(job)} && B={binary}; "
            f"for t in {listed}; do \"$B\" eval {shlex.quote(doc)} --time $t --format json; "
            "echo; done")


def parse_frames(stdout: str, n: int) -> list[dict | None]:
    """The envelopes on the job's stdout, one per time in order; None where
    the engine failed or stopped early."""
    decoder = json.JSONDecoder()
    out: list[dict | None] = []
    i = 0
    while len(out) < n:
        j = stdout.find("{", i)
        if j < 0:
            break
        try:
            obj, end = decoder.raw_decode(stdout, j)
        except ValueError:
            break
        i = end
        ok = isinstance(obj, dict) and obj.get("ok", True) is not False and obj.get("result")
        out.append(obj if ok else None)
    while len(out) < n:
        out.append(None)
    return out


def _binary(host: str) -> str:
    known = render.probe(host)
    if known.engine:
        return shlex.quote(known.engine)
    return os.environ.get("RS_RENDER_REMOTE_BIN") or render.REMOTE_BIN_LOOKUP


def remote_frames(root: Path, doc: str, times, host: str, *, scratch: bool = False
                  ) -> list[dict | None]:
    """One job on `host`: refresh `root`'s mirror (the pipeline's incremental
    rsync), run the eval loop in a job directory hardlinked from it, remove
    the directory -- and the mirror too when `root` was a scratch bundle."""
    root = Path(root)
    mirror = render.mirror_dir(root.name)
    job = render.remote_dir(root.name, "eval", render.job_tag())
    render.refuse_remote()
    subprocess.run(render.sync_up_args(root, host, mirror), check=True,
                   capture_output=True, text=True)
    try:
        proc = subprocess.run(render.ssh_argv(host, eval_script(mirror, job, _binary(host), doc, times)),
                              capture_output=True, text=True)
    finally:
        render.remove_remote(host, job)
        if scratch:
            render.remove_remote(host, mirror)
    return parse_frames(proc.stdout or "", len(times))


def local_frames(binary: Path, root: Path, doc: str, times) -> list[dict | None]:
    """The same loop against a binary on this machine (RS_RENDER_BIN)."""
    out: list[dict | None] = []
    for t in times:
        try:
            proc = subprocess.run([str(binary), "eval", doc, "--time", f"{float(t):g}",
                                   "--format", "json"], cwd=root, capture_output=True,
                                  text=True, timeout=120)
            out += parse_frames(proc.stdout or "", 1)
        except (subprocess.SubprocessError, OSError):
            out.append(None)
    return out


def engine_runner(doc_path: str, *, root: Path | None = None, files: dict[str, bytes] | None = None,
                  host: str = ""):
    """The production seam: a `runner(times)` over `doc_path` -- relative to
    `root`, a project already mirrored on the hosts, or to a scratch bundle
    made of `files` (path -> bytes) for the call. None when no engine is
    reachable: no local binary and no host."""
    binary = render.engine()
    if binary is None:
        host = host or render.remote_host(None, "validate")
        if not host:
            return None

    def frames(base: Path, scratch: bool):
        if binary is not None:
            return local_frames(binary, base, doc_path, times_now)
        return remote_frames(base, doc_path, times_now, host, scratch=scratch)

    def run(times):
        nonlocal times_now
        times_now = [float(t) for t in times]
        if files is None:
            return frames(Path(root or "."), False)
        with tempfile.TemporaryDirectory(prefix="geometry-") as tmp:
            base = Path(tmp)
            for rel, data in files.items():
                (base / rel).parent.mkdir(parents=True, exist_ok=True)
                (base / rel).write_bytes(data)
            return frames(base, True)

    times_now: list[float] = []
    return run


def check_project(scene: Path, root: Path, shots: list[dict], *, host: str = "",
                  step: float = STEP, runner=None) -> dict:
    """The check `yt qa` and `yt gate` run over a project's scene: its
    includes resolved, every shot stepped (the document's duration when it
    has no shots), the declared rests and attachments read from the
    included files."""
    scene = Path(scene)
    text = scene.read_text(encoding="utf-8", errors="replace")
    objects = scene_objects(text, scene.parent)
    rests = scene_rests(text, scene.parent)
    attached = scene_attachments(text, scene.parent)
    cameras = scene_cameras(text, scene.parent)
    times = sample_times(shots, step, duration=document_duration(text))
    try:
        doc_rel = scene.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        doc_rel = scene.name
    run = runner if runner is not None else engine_runner(doc_rel, root=Path(root), host=host)
    report = evaluate(objects, times, run, rests=rests, attached=attached, cameras=cameras,
                      lights=document_lights(text), frame=document_frame(text), step=step)
    report["scene"] = str(scene)
    report["step"] = step
    return report
