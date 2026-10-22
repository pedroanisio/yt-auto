"""Compile the project into a scene-render document the engine can render.

Everything upstream writes for people: a screenplay, a cue sheet, an animation
plan. This turns those into one scene-render 1.x document -- timed layers,
narration tracks, caption cues and markers -- and runs the SDK's own checks
over it, so a scene that cannot render fails here with the schema's words
rather than at the engine.

    yt build --title monty                      # scene.xml + build.json
    yt build --title monty --width 1080 --height 1920 --fps 30
    yt build --title monty --no-captions --force

The timeline comes from `yt voice` when it has run -- measured audio is the
only honest runtime -- and from the screenplay's own estimates otherwise.

Every shot becomes a marker (`kind="beat"`) and every section a `kind="section"`
marker, because a marker survives re-timing and a timecode does not: pilots and
segment outputs anchor to these.

A shot the animation plan covers becomes an `<include>` of that effect's symbol
from the document the model authored for its section under vfx/. A
shot whose asset is a real local file becomes an image layer, with the licence
and credit the research recorded carried into the document. Everything else
becomes a titled placeholder card, counted and listed, so the document renders
today and the gaps are visible. Consecutive shots the same symbol draws share
one `<include>`, so the symbol's clock runs continuously across them.

The mix is the sound design's: every cue of sound.json that has a file becomes
an audio asset and a track on the bus of its role -- music, effects, ambience
-- at the cue's time, with its fades, its level staged across bus and track;
the narration keeps its own bus, and music and ambience duck under it. A
silence cue is a window on the music and effects buses' volume; the
narration and the room carry on through it. A cue with no file yet is
listed in build.json as missing, not invented.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import wave
from dataclasses import asdict, dataclass, field
from xml.etree import ElementTree
from pathlib import Path

from . import fonts, house, workspace
from .core import Idea, _key, read_json, timecode
from . import design as designer
from .harness import Harness, HarnessError, brief
from .scene_sdk import enum_of, sdk
from .script import Section, Shot, legibility, load_script

#: Where a background colour comes from when nothing else says.
FALLBACK_BACKGROUND = "#101418"
HEX = re.compile(r"#[0-9a-fA-F]{6}")

#: What the model may do with a shot nothing has drawn.
COVERAGE_ACTIONS = ("extend_previous", "extend_next", "image", "block")

ROLE = (
    "You are the editor assembling this film from what the stages produced: a "
    "timed screenplay, the recorded narration, the effects the animation stage "
    "authored per shot, the licensed images the research found, and the design "
    "system. You decide what the composition does where the plan has a gap, "
    "where captions would fight the picture, and how long the film holds after "
    "the last word. You never invent art: a gap is covered by stretching a "
    "neighbouring effect that genuinely carries the shot, by a licensed image "
    "that shows what the shot describes, or it is blocked and sent back to the "
    "animation stage. A placeholder card is disqualifying, so `block` is an "
    "honest answer, not a failure. Say what blocks publication in `blocking`."
)

EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "coverage": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "action": {"type": "string", "enum": list(COVERAGE_ACTIONS)},
                    "image": {"type": "string",
                              "description": "the image path, for action=image"},
                    "reason": {"type": "string"},
                },
                "required": ["label", "action", "reason"],
            },
            "description": "one entry per uncovered shot",
        },
        "captions_off": {"type": "array", "items": {"type": "string"},
                         "description": "shots whose caption cue is left out"},
        "tail_seconds": {"type": "number",
                         "description": "hold after the last shot, 0 to 5"},
        "notes": {"type": "string"},
        "blocking": {"type": "array", "items": {"type": "string"},
                     "description": "what prevents publication, if anything"},
    },
    "required": ["coverage", "captions_off", "tail_seconds", "notes", "blocking"],
}


TONE_MAPPERS = ["none", "aces", "aces2", "agx", "filmic", "reinhard"]
LOOK_ID = "film-look"
#: Sane limits on the model's grade (a bound, not a grade): beyond them a CDL
#: stops being a look and becomes damage.
LOOK_BOUNDS = {"exposure": (-2.0, 2.0), "slope": (0.5, 1.5), "offset": (-0.1, 0.1),
               "power": (0.6, 1.6), "saturation": (0.5, 1.5), "mix": (0.0, 1.0)}

_CHANNELS = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
LOOK_SCHEMA = {
    "type": "object",
    "properties": {
        "tone_mapping": {"type": "string", "enum": TONE_MAPPERS},
        "exposure": {"type": "number", "description": "stops; 0 leaves the light as lit"},
        "look": {"type": "string", "enum": ["grade", "none"]},
        "slope": _CHANNELS, "offset": _CHANNELS, "power": _CHANNELS,
        "saturation": {"type": "number"}, "mix": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["tone_mapping", "exposure", "look", "reason"],
}


def clean_look(raw) -> dict | None:
    """The model's look held to the schema's list and to sane bounds.

    None when nothing is to be applied: a 'none' answer is valid, and so is
    an answer too malformed to read.
    """
    if not isinstance(raw, dict):
        return None

    def number(value, name, default):
        lo, hi = LOOK_BOUNDS[name]
        try:
            return min(hi, max(lo, float(value)))
        except (TypeError, ValueError):
            return default

    def channels(name, default):
        value = raw.get(name)
        if not (isinstance(value, (list, tuple)) and len(value) == 3):
            return [default] * 3
        return [number(v, name, default) for v in value]

    tone = raw.get("tone_mapping")
    tone = tone if tone in TONE_MAPPERS else "none"
    grade = "grade" if raw.get("look") == "grade" else "none"
    if tone == "none" and grade == "none":
        return None
    return {"tone_mapping": tone, "exposure": number(raw.get("exposure"), "exposure", 0.0),
            "look": grade, "slope": channels("slope", 1.0),
            "offset": channels("offset", 0.0), "power": channels("power", 1.0),
            "saturation": number(raw.get("saturation"), "saturation", 1.0),
            "mix": number(raw.get("mix"), "mix", 1.0),
            "reason": str(raw.get("reason", ""))}


VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["apply", "revise", "none"]},
        "reason": {"type": "string"},
        "revision": LOOK_SCHEMA,
    },
    "required": ["verdict", "reason"],
}


def look_host() -> str:
    """The render host a look can be seen on, or "" when none is reachable.

    Under YT_NO_REMOTE (the suite) there is none, and so no look is applied.
    """
    from . import render
    if os.environ.get(render.NO_REMOTE_ENV):
        return ""
    try:
        return render.remote_host(None, "still") or ""
    except Exception:
        return ""


def look_question(first: bool) -> str:
    return ("Read the two PNGs in this folder: ungraded.png is the frame as authored, "
            "graded.png is the same frame with the proposed colour look. Judge whether "
            "the graded frame keeps the palette's roles (each colour still reads as "
            "what it is for: backgrounds, surfaces, accents, text) and the contrast "
            "better than the ungraded one, and does not wash out, grey or shift it "
            "against the treatment. Answer `apply` only if it is better; `revise` with "
            "one corrected look in `revision` if a change would fix it; otherwise "
            "`none`." + ("" if first else " This is the revision's only re-check: "
                         "answer `apply` or `none`."))


def look_frame_time(builder) -> float:
    """The midpoint of the film's longest shot (0.5 s when it has none)."""
    placed = timeline(builder.sections, builder.voice)
    if not placed:
        return 0.5
    longest = max(placed, key=lambda p: p.seconds)
    return round(longest.at + longest.seconds / 2, 3)


def shipped_with(doc_text: str, root: Path) -> dict[str, bytes]:
    """Every local file a document needs, including what its <include>s need.

    `referenced_files` skips `.scene.xml` references, but the include copies
    (written by build()) are exactly that, and each carries asset paths of its
    own, relative to its own directory. Both travel, to any depth.
    """
    from .vfx import referenced_files
    root = Path(root).resolve()
    files = referenced_files(doc_text, root, root)
    seen: set[str] = set()
    pending = [(doc_text, root)]
    while pending:
        text, base = pending.pop()
        for ref in re.findall(r'<include\b[^>]*?\bsrc="([^"]+)"', text):
            path = (base / ref).resolve()
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                continue
            if rel in seen or not path.is_file():
                continue
            seen.add(rel)
            files[rel] = path.read_bytes()
            inner = path.read_text(encoding="utf-8", errors="replace")
            files.update(referenced_files(inner, path.parent, root))
            pending.append((inner, path.parent))
    return files


def check_look(builder, engine, look, *, host: str | None = None,
               render_still=None) -> tuple[dict | None, dict]:
    """The look, seen on a rendered frame before it is applied: (look, record).

    One still with the look and one without go to the harness, which says
    apply | revise | none. Only 'apply' applies it; a revision is checked once
    more. With no render host nothing is seen, so nothing is graded.
    """
    import tempfile
    from . import render
    if not look:
        return None, {"status": "none chosen"}
    host = look_host() if host is None else host
    if not host:
        return None, {"status": "not checked",
                      "reason": "no render host reachable; the look was not applied"}
    render_still = render_still or render.engine_render_still
    at = look_frame_time(builder)
    kept = builder.look

    def still(candidate):
        builder.look = candidate
        try:
            data, _ = builder.build()
        finally:
            builder.look = kept
        files = shipped_with(data.decode("utf-8", "replace"), builder.root)
        return render_still(data, at, host=host, files=files)

    def judge(candidate, first):
        plain, note = still(None)
        graded, note2 = still(candidate)
        if plain is None or graded is None:
            return None, (note if plain is None else note2)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "ungraded.png").write_bytes(plain)
            (Path(tmp) / "graded.png").write_bytes(graded)
            return engine.inspect("look-check", look_question(first), VERDICT_SCHEMA,
                                  cwd=Path(tmp)), ""

    answer, why = judge(look, True)
    if answer is None:
        return None, {"status": "not checked", "at": at,
                      "reason": f"a frame was not rendered: {why}"}
    verdict = answer.get("verdict") if isinstance(answer, dict) else None
    record = {"at": at, "verdict": verdict, "reason": str((answer or {}).get("reason", ""))}
    if verdict == "apply":
        return look, dict(record, status="applied")
    revision = clean_look(answer.get("revision")) if verdict == "revise" else None
    if revision is None:
        return None, dict(record, status="dropped")
    second, why = judge(revision, False)
    if second is None:
        return None, dict(record, status="dropped", first=record,
                          reason=f"the revision was not checked: {why}")
    again = second.get("verdict") if isinstance(second, dict) else None
    if again == "apply":
        return revision, dict(record, status="revised", first=dict(record),
                              verdict="apply", reason=str(second.get("reason", "")))
    return None, dict(record, status="dropped", first=dict(record), verdict=again,
                      reason=str(second.get("reason", "")))


def ident(text: str, prefix: str = "x") -> str:
    """An xs:ID: letters, digits, dashes, and never starting with a digit."""
    clean = re.sub(r"[^A-Za-z0-9_.-]", "-", text).strip("-") or "x"
    return clean if clean[0].isalpha() or clean[0] == "_" else f"{prefix}-{clean}"


def pick(enum: list[str], *wanted: str) -> str | None:
    """The first wanted value the schema actually allows."""
    return next((w for w in wanted if w in enum), None)


def facets_of(element: str, attribute: str,
              fallback: tuple[float, float] = (-120.0, 24.0)) -> tuple[float, float]:
    """The numeric range the schema gives one attribute (min, max).

    `gain` is the one that bites: it saturates at +24 dB per track and per
    bus, so a quiet library has to be lifted across both stages. The range is
    read from the schema's own facets rather than remembered.
    """
    try:
        described = sdk().describe(element).to_dict()
    except Exception:
        return fallback
    for attr in described.get("attributes", []):
        if attr.get("xml_name") != attribute:
            continue
        kind = attr.get("type")
        facets = kind.get("facets") or {} if isinstance(kind, dict) else {}
        try:
            lo = float(facets.get("minInclusive", fallback[0]))
            hi = float(facets.get("maxInclusive", fallback[1]))
        except (TypeError, ValueError):
            return fallback
        return lo, hi
    return fallback


#: A level the cue sheet states in absolute terms ("-30 dBFS peak", "-34
#: LUFS"), and failing that the first signed decibel figure ("-20 dB"); a
#: figure glued to another ("6-9 dB below") is a relative note, not a level.
#: The sheets write the minus as U+2212, and sometimes an en dash (U+2013) in
#: front of a figure; all three are a sign.
_MINUS = "-−–"
_ABSOLUTE_DB = re.compile(
    r"(?<![\d.\-−])([" + _MINUS + r"]?\d+(?:\.\d+)?)\s*(?:dBFS|LUFS)\b")
_SIGNED_DB = re.compile(
    r"(?<![\d.\-−])([" + _MINUS + r"]\d+(?:\.\d+)?)\s*dB\b")
#: A range or list before the unit ("-36 to -30 dBFS", "-18, -22, -26 dBFS"):
#: the first figure is where the cue sits.
_SERIES_BEFORE = re.compile(
    r"([" + _MINUS + r"]?\d+(?:\.\d+)?)"
    r"(?:\s*(?:to|,|–)\s*[" + _MINUS + r"]?\d+(?:\.\d+)?)*\s*(?:to|,|–)\s*$")


def _signed(figure: str) -> float:
    """A figure as written, whichever dash is its minus."""
    return float(re.sub("[−–]", "-", figure))


def level_db(text: str) -> float | None:
    """The level a cue sits at, as the sheet wrote it, in dB; None if the
    sheet only said it in words."""
    text = str(text or "")
    found = _ABSOLUTE_DB.search(text)
    if found:
        series = _SERIES_BEFORE.search(text[:found.start()])
        return _signed(series.group(1) if series else found.group(1))
    found = _SIGNED_DB.search(text)
    return _signed(found.group(1)) if found else None


def audio_seconds(path: Path) -> float | None:
    """How long a sound file plays, when the standard library can tell (WAV);
    None for anything it cannot read."""
    try:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            return handle.getnframes() / rate if rate else None
    except (OSError, wave.Error, EOFError):
        return None


def stage_gain(totals: list[float], lo: float, hi: float) -> tuple[float, list[float]]:
    """One bus gain and a trim per track, each inside the schema's range.

    The bus carries what the role's cues have in common, the track the
    deviation, so a total neither stage could reach alone is reached by the
    two together. Both are clamped, so what is left over is reported by the
    caller rather than silently lost.
    """
    if not totals:
        return 0.0, []
    bus = min(hi, max(lo, sum(totals) / len(totals)))
    return round(bus, 3), [round(min(hi, max(lo, t - bus)), 3) for t in totals]


#: Short effects -- impacts, hits, stingers -- are transients: they set the
#: programme's peaks without carrying its loudness.
TRANSIENT_KINDS = ("sfx", "stinger", "transition")
TRANSIENT_SECONDS = 2.0
#: The room the master's ceiling leaves above the integrated target is
#: shared between the bed's own crest and the transients. The crest belongs
#: to each film's mix, so it is measured, not fixed: a generic start for the
#: first build, then corrected by what the last render of this project
#: measured (`measured_crest`). Nothing here is fitted to any one film.
DEFAULT_CREST_DB = 6.0
#: The least room the transients keep above the bed however the crest grows.
MIN_TRANSIENT_ROOM_DB = 2.0
#: A measured loudness this close to the target is on target: no correction.
LOUDNESS_TOLERANCE_LU = 0.5


def measured_crest(root: Path, *, target: float, true_peak: float = -1.0
                   ) -> tuple[float, str]:
    """The bed crest for this project's next mix, and where it came from.

    When the last build recorded the crest it used and a render made after
    that build measured the programme's integrated loudness, a shortfall
    against the target is the share of the ceiling the crest needed beyond
    it: raise the crest by that much. A mix on target keeps its crest.
    Without such a pair of measurements, the generic start is used.
    """
    build_json, render_json = root / "build.json", root / "render.json"
    if not (build_json.is_file() and render_json.is_file()):
        return DEFAULT_CREST_DB, "default"
    if render_json.stat().st_mtime <= build_json.stat().st_mtime:
        return DEFAULT_CREST_DB, "default"
    staging = (read_json(build_json, warn=False) or {}).get("audio_staging") or {}
    outputs = ((read_json(render_json, warn=False) or {}).get("result") or {}).get("outputs") or []
    used = staging.get("crest_db")
    measured = next((o.get("loudness") for o in outputs if o.get("loudness") is not None), None)
    if used is None or measured is None:
        return DEFAULT_CREST_DB, "default"
    shortfall = float(target) - float(measured)
    if shortfall <= LOUDNESS_TOLERANCE_LU:
        return round(float(used), 3), "measured"
    ceiling = (true_peak - target) - MIN_TRANSIENT_ROOM_DB
    return round(min(ceiling, float(used) + shortfall), 3), "measured"


def transient_headroom(loudness: float, true_peak: float, *,
                       crest: float = DEFAULT_CREST_DB) -> float:
    """How far a transient's peak may sit above the loudest bed.

    `normalize` lifts the programme to `loudness`; the limiter then holds
    every peak under `true_peak`. A peak more than their difference above
    the sustained level is limited after the lift, and the limiter takes the
    loudness with it (docs/compute.md). The bed's own crest takes a share.
    """
    return round((true_peak - loudness) - crest, 3)


def transient_shift(rows: list[tuple[str, float, float]], headroom: float
                    ) -> tuple[float, float | None]:
    """The dB the transients come down by, as a group, and the bed level
    they were measured against (None when nothing sustained plays).

    `rows` are (kind, seconds, total dB). Beds are everything that is not a
    short effect; a group shift keeps the sheet's balance between the
    transients while the loudest lands `headroom` above the loudest bed.
    """
    transients = [t for k, s, t in rows if k in TRANSIENT_KINDS and s <= TRANSIENT_SECONDS]
    beds = [t for k, s, t in rows
            if k != "narration" and not (k in TRANSIENT_KINDS and s <= TRANSIENT_SECONDS)]
    if not transients or not beds:
        return 0.0, (max(beds) if beds else None)
    bed = max(beds)
    return round(max(0.0, max(transients) - (bed + headroom)), 3), bed


#: The bus each cue kind is routed to; the track's `role` is asked of the
#: schema, these are only the ids the document uses.
CUE_BUS = {"music": "music", "sfx": "effects", "stinger": "effects",
           "transition": "effects", "ambience": "ambience"}
NARRATION_BUS = "narration"
def ducks(bus_id: str) -> bool:
    """Every bus but the narration sits under the voice while it speaks: a
    bed or an effect that does not duck is what makes a narration inaudible."""
    return bus_id != NARRATION_BUS
#: What a designed silence takes out: the score and the effects. The voice
#: and the room stay -- the cue sheet's silences read "no music", "narration
#: only, with the room", "voice over room tone only".
SILENCED_BUSES = ("music", "effects")


# --------------------------------------------------------------------------- #
# what the earlier stages left behind
# --------------------------------------------------------------------------- #

@dataclass
class Media:
    """A file on disk a shot can actually show."""
    path: str
    title: str = ""
    licence: str = ""
    credit: str = ""
    kind: str = "image"
    about: str = ""          # what the research kept the file for


def local_media(project_root: Path, dossier: dict | None) -> list[Media]:
    """Assets the research downloaded, with the licence it verified."""
    if not dossier:
        return []
    by_title = {_key(a.get("title", "")): a for a in dossier.get("assets", [])}
    # The manifest is a JSON list, and a project with no downloads has none,
    # which is normal rather than worth a warning.
    rows = read_json(project_root / "raw" / "08-downloads.json",
                     warn=False, shape=list) or []
    out = []
    for row in rows:
        if row.get("status") != "ok" or not row.get("file"):
            continue
        if not (project_root / row["file"]).is_file():
            continue
        asset = by_title.get(_key(row.get("title", ""))) or {}
        out.append(Media(path=row["file"], title=row.get("title", ""),
                         licence=row.get("licence") or asset.get("licence", ""),
                         credit=asset.get("credit_line", ""),
                         kind=asset.get("kind", "image"),
                         about=str(asset.get("use_for", ""))[:200]))
    return out


def match_media(shot: Shot, media: list[Media]) -> Media | None:
    """The downloaded file a shot's asset line names, if any."""
    wanted = _key(shot.asset)
    if not wanted or not media:
        return None
    for item in media:
        key = _key(item.title)
        if key and (key in wanted or wanted in key):
            return item
    return None


#: How many even slices stand in for shots when a document has no markers.
EVEN_SHOTS = 8


def shots_from_scene(document: bytes) -> list[dict]:
    """The shots as the document itself records them: `kind="beat"` markers
    with their section, in the shape of build.json's `shots`. A document with
    no markers is sliced evenly, so a standalone tool still has somewhere to
    look."""
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError:
        return []
    project = root.find("project")
    duration = float((project.get("duration") if project is not None else 0) or 0)
    sections, beats = [], []
    for marker in root.iter("marker"):
        kind, label = marker.get("kind", ""), marker.get("label", "")
        try:
            at = float(marker.get("time", 0) or 0)
            seconds = float(marker.get("duration", 0) or 0)
        except ValueError:
            continue
        if kind == "section":
            sections.append((at, label))
        elif kind == "beat" or seconds:
            beats.append({"label": label or marker.get("id", ""), "at": at,
                          "seconds": seconds, "asset": "document"})
    sections.sort()
    beats.sort(key=lambda b: b["at"])
    for n, beat in enumerate(beats):
        if not beat["seconds"]:
            nxt = beats[n + 1]["at"] if n + 1 < len(beats) else duration
            beat["seconds"] = max(0.0, nxt - beat["at"])
        beat["section"] = next((label for at, label in reversed(sections)
                                if at <= beat["at"]), "")
    if beats:
        return beats
    if duration <= 0:
        return []
    step = duration / EVEN_SHOTS
    return [{"label": f"slice-{n + 1}", "at": round(n * step, 3),
             "seconds": round(step, 3), "section": "", "asset": "document"}
            for n in range(EVEN_SHOTS)]


#: The file-valued attributes of what the build hoists out of an included
#: document (xs:anyURI in the schema): a light's IES profile and environment
#: map, a text style's font. Paints carry no file and tokens are values.
HOISTED_FILES = {"light": ("ies", "environment"), "textStyle": ("fontFile",)}
_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def rebase(value: str, src_dir: Path, scene_dir: Path) -> str:
    """A path written relative to `src_dir`, rewritten relative to
    `scene_dir`; an absolute path, a URL or an empty value is left alone."""
    if not value or Path(value).is_absolute() or _URL.match(value):
        return value
    return Path(os.path.relpath(os.path.normpath(Path(src_dir) / value),
                                os.path.normpath(scene_dir))).as_posix()


def hoisted(path: Path, scene_dir: Path | None = None) -> dict:
    """What an included document declares that an <include> does not carry.

    The engine lights included content with the including document's lights
    (warning E12) and resolves its tokens against the including document's
    styles, and the schema allows <lights> and <styles> at scene level only.
    So the builder lifts them: every <light> and <token> of the file, with
    any children the light carries noted, because those are not copied.
    A hoisted element's file paths (`HOISTED_FILES`) were written relative
    to the authored file; with `scene_dir` they are rebased to the scene
    that now declares them, or the engine looks for them beside the scene.
    """
    out = {"lights": [], "tokens": [], "text_styles": [], "paints": [],
           "downloads": [], "findings": []}
    src_dir = Path(path).parent

    def carried(element) -> dict:
        row = dict(element.attrib)
        if scene_dir is not None:
            for key in HOISTED_FILES.get(element.tag, ()):
                if row.get(key):
                    row[key] = rebase(row[key], src_dir, Path(scene_dir))
        return row

    try:
        tree = ElementTree.parse(path)
    except (OSError, ElementTree.ParseError) as exc:
        out["findings"].append(f"warning: {path.name}: could not be read for its "
                               f"lights and styles: {exc}")
        return out
    root = tree.getroot()
    for paints in root.findall("paints"):
        for paint in paints:
            if paint.get("id"):
                out["paints"].append((paint.get("id"),
                                      ElementTree.tostring(paint, encoding="unicode")))
    for light in root.iter("light"):
        row = carried(light)
        # Its <animate> children are carried, to be re-timed by the include's
        # start; any other child is not.
        animates = [ElementTree.tostring(c, encoding="unicode")
                    for c in light if c.tag == "animate"]
        other = sorted({c.tag for c in light if c.tag != "animate"})
        if other:
            out["findings"].append(
                f"warning: {path.name}: light {row.get('id', '?')!r} has its own "
                f"{', '.join(other)}; only its attributes and animate are carried "
                "into the scene")
        if animates:
            row["animate"] = animates
        out["lights"].append(row)
    # Project files under downloads/ the document names itself: the build
    # credits them from the download manifest.
    base = Path(scene_dir) if scene_dir is not None else src_dir
    for element in root.iter():
        value = element.get("src") or ""
        if not value or Path(value).is_absolute() or _URL.match(value):
            continue
        rel = Path(os.path.relpath(os.path.normpath(src_dir / value),
                                   os.path.normpath(base))).as_posix()
        if rel.startswith("downloads/") and rel not in out["downloads"]:
            out["downloads"].append(rel)
    for token in root.iter("token"):
        if token.get("name") and token.get("value") is not None:
            out["tokens"].append((token.get("name"), token.get("value")))
    for style in root.iter("textStyle"):
        if style.get("id"):
            out["text_styles"].append(carried(style))
    return out


#: What an <include> does not merge (engine warning E12): the including
#: document's own are what light and paint the included content, and its
#: colour tokens are read from the included file's own styles.
#: colorManagement is the film's one pipeline; a section's own would grade it
#: apart from the others.
NOT_MERGED = ("styles", "paints", "lights", "colorManagement")
TOKEN_REF = re.compile(r"var\(--([A-Za-z0-9_.-]+)\)")
PAINT_REF = re.compile(r"url\(#([A-Za-z0-9_.-]+)\)")
#: How far a token may alias another before it is given up on (the engine's
#: own limit).
TOKEN_HOPS = 8


def include_copy_of(file: str) -> str:
    """Where the cleaned copy of an authored document goes: beside it, with
    the same relative asset paths, as `<stem>.include.scene.xml`."""
    for suffix in (".scene.xml", ".xml"):
        if file.endswith(suffix):
            return f"{file[:-len(suffix)]}.include.scene.xml"
    return f"{file}.include.scene.xml"


def resolve_tokens(tokens: dict[str, str]) -> dict[str, str]:
    """Each token's final value, aliases (`var(--other)`) followed."""
    out = {}
    for name in tokens:
        value = tokens[name]
        for _ in range(TOKEN_HOPS):
            alias = TOKEN_REF.fullmatch(value.strip())
            if not alias or alias.group(1) not in tokens:
                break
            value = tokens[alias.group(1)]
        out[name] = value
    return out


def cleaned_include(src: Path, dest: Path,
                    pinner: fonts.Pinner | None = None) -> dict:
    """Write the copy of an authored document that an <include> points at.

    The engine validates an included document on its own and reads its
    colour tokens from its own <styles>, so the copy inlines every
    `var(--name)` with the file's resolved value first, then drops the
    sections an include does not merge (NOT_MERGED); the build hoists the
    paints and lights into the scene instead. The authored file is read,
    never written. With a `pinner`, each text node of the copy is pinned to a
    shipped font file (fonts.py) before the copy is written.
    """
    out = {"symbols": [], "durations": {}, "paint_refs": set(), "unresolved": [],
           "removed": [], "findings": []}
    try:
        root = ElementTree.parse(src).getroot()
    except (OSError, ElementTree.ParseError) as exc:
        out["findings"].append(f"error: {src.name}: could not be read for its "
                               f"include copy: {exc}")
        return out
    values = resolve_tokens({t.get("name"): t.get("value", "")
                             for t in root.iter("token") if t.get("name")})
    styles = fonts.Pinner.styles_of(root)
    for tag in NOT_MERGED:
        for child in root.findall(tag):
            root.remove(child)
            out["removed"].append(tag)
    # An include does not merge <markers> either (E12, which --strict refuses).
    # The copy drops them when nothing else in it names one -- an author's note
    # for the sound, say; one that is referenced stays, and the author moves it.
    for markers in root.findall("markers"):
        ids = {m.get("id") for m in markers.iter("marker") if m.get("id")}
        root.remove(markers)
        rest = ElementTree.tostring(root, encoding="unicode")
        named = sorted(i for i in ids if f'"{i}"' in rest or f"'{i}'" in rest)
        if named:
            root.append(markers)
            out["findings"].append(
                f"warning: {src.name}: its <markers> ({', '.join(named)}) are referenced inside "
                "it, and an include does not merge them (E12); move the reference to a time")
        else:
            out["removed"].append("markers")
    unresolved: set[str] = set()
    for element in root.iter():
        for key, value in element.attrib.items():
            if "var(--" in value:
                value = TOKEN_REF.sub(lambda m: values.get(m.group(1), m.group(0)),
                                      value)
                element.set(key, value)
                unresolved.update(TOKEN_REF.findall(value))
            out["paint_refs"].update(PAINT_REF.findall(value))
    out["unresolved"] = sorted(unresolved)
    out["symbols"] = [s.get("id") for s in root.iter("symbol") if s.get("id")]
    project = root.find("project")
    project_seconds = project.get("duration") if project is not None else None
    for s in root.iter("symbol"):
        length = s.get("duration") or project_seconds
        if s.get("id") and length:
            try:
                out["durations"][s.get("id")] = float(length)
            except ValueError:
                pass
    if pinner is not None:
        pinner.pin_xml(root, Path(dest).parent, styles)
    try:
        dest.write_bytes(ElementTree.tostring(root, encoding="utf-8",
                                              xml_declaration=True))
    except OSError as exc:
        out["findings"].append(f"error: {dest.name}: the include copy could not "
                               f"be written: {exc}")
    return out


def background_of(styles: dict | None) -> tuple[str, str]:
    """A background colour, and where it came from."""
    for style in (styles or {}).get("styles", []):
        found = HEX.findall(str(style.get("palette", "")))
        if found:
            return found[0], f"styles.json ({style.get('name', 'first treatment')})"
    return FALLBACK_BACKGROUND, "the builder's fallback"


#: A symbol may fall this far short of its shot before the build refuses it.
TIMING_SLACK = 0.25


@dataclass
class Placed:
    """One shot, as it will sit in the document."""
    label: str
    at: float
    seconds: float
    section: str = ""          # so the manifest is self-describing
    source: str = "script"     # voice | script
    asset: str = "placeholder"  # placeholder | image | effect
    file: str = ""
    include: str = ""          # the <include> that draws it, for an effect
    narration: str = ""
    speaker: str = ""
    audio: str = ""            # the take's WAV, when there is one
    words: list[dict] = field(default_factory=list)


def timeline(sections: list[Section], voice: dict | None) -> list[Placed]:
    """Where every shot sits: measured audio first, the screenplay's guess after."""
    takes = {t["label"]: t for t in (voice or {}).get("takes", [])}
    out: list[Placed] = []
    cursor = 0.0
    for section in sections:
        for shot in section.shots:
            take = takes.get(shot.label)
            if take:
                at, seconds, source = (float(take["at"]), float(take["seconds"]),
                                       "voice")
            else:
                # A silent shot keeps its planned length, placed after what came
                # before so the two sources cannot overlap each other.
                at, seconds, source = cursor, float(shot.seconds or 1), "script"
            out.append(Placed(
                label=shot.label, at=round(at, 3), seconds=round(seconds, 3),
                section=section.heading, source=source,
                narration=shot.narration,
                speaker=str(getattr(shot, "speaker", "") or ""),
                audio=(take or {}).get("file", ""),
                words=(take or {}).get("words", [])))
            cursor = max(cursor, at + seconds)
    return sorted(out, key=lambda p: p.at)


# --------------------------------------------------------------------------- #
# the document
# --------------------------------------------------------------------------- #

class SceneBuilder:
    """Assembles one scene-render document from the project's artifacts."""

    def __init__(self, idea: Idea, sections: list[Section], root: Path, *,
                 voice: dict | None = None, styles: dict | None = None,
                 dossier: dict | None = None, vfx: dict | None = None,
                 sound: dict | None = None,
                 width: int = 1920,
                 height: int = 1080, fps: str = "30", codec: str = "h264",
                 background: str = "", lang: str = "", captions: bool = True,
                 burn_captions: bool = False,
                 audio: bool = True, loudness: float = -14.0,
                 true_peak: float = -1.0, normalize: str = "integrated",
                 normalize_reason: str = "", limiter: bool = True,
                 bitrate: int = 16_000_000,
                 max_bitrate: int = 24_000_000, buffer_size: int = 48_000_000,
                 preset: str = "slow", edit: dict | None = None,
                 look: dict | None = None,
                 verbose: bool = True) -> None:
        self.idea = idea
        self.edit = edit or {}
        self.look = look
        self.sections = sections
        self.root = root
        self.voice = voice or {}
        self.styles = styles
        self.dossier = dossier
        # The sound stage's contract: a cue sheet, each cue with `at`,
        # `seconds`, `kind`, `level`, and once fetched a `file` beside the
        # project with its licence and credit.
        raw_cues = (sound or {}).get("cues")
        self.cues = [c for c in raw_cues if isinstance(c, dict)] \
            if isinstance(raw_cues, list) else []
        # The animation stage's contract: a document of symbols beside the
        # project, and which symbol each shot label gets.
        self.vfx_report = vfx or {}
        scene_fx = (vfx or {}).get("scene") or {}
        self.vfx_symbols = {
            label: hit for label, hit in (scene_fx.get("by_label") or {}).items()
            if isinstance(hit, dict) and hit.get("file") and hit.get("symbol")
            and (root / hit["file"]).is_file()}
        self.width, self.height, self.fps = width, height, fps
        self.codec = codec
        self.captions, self.audio = captions, audio
        self.burn_captions = burn_captions
        self.loudness, self.true_peak = loudness, true_peak
        self.normalize = normalize
        self.normalize_reason = normalize_reason
        self.sound_normalize = (sound or {}).get("normalize")
        self.limiter = limiter
        self.bitrate, self.max_bitrate = bitrate, max_bitrate
        self.buffer_size, self.preset = buffer_size, preset
        self.verbose = verbose
        self.background, self.background_from = (
            (background, "the --background flag") if background
            else background_of(styles))
        self.lang = lang or self.voice.get("lang") or "en"

    def _say(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr)

    # -- what the model decides ---------------------------------------------

    def _drawn(self, placed: list[Placed], media: list[Media]) -> dict[str, tuple]:
        """Per shot: what draws it before any decision -- an authored effect,
        a licensed image, or nothing."""
        shots = {s.label: s for sec in self.sections for s in sec.shots}
        out = {}
        for p in placed:
            hit = self.vfx_symbols.get(p.label)
            if hit:
                out[p.label] = ("effect", hit)
                continue
            shot = shots.get(p.label)
            image = match_media(shot, media) if shot is not None else None
            out[p.label] = ("image", image) if image is not None else ("none", None)
        return out

    def brief(self) -> dict:
        """Everything the editor sees before deciding: the shots and what
        draws each, the images available, the design system, the gaps."""
        placed = timeline(self.sections, self.voice)
        media = local_media(self.root, self.dossier)
        drawn = self._drawn(placed, media)
        design = designer.Design.from_styles(self.styles, height=self.height)
        shots = {s.label: s for sec in self.sections for s in sec.shots}
        rows = []
        for p in placed:
            shot = shots.get(p.label)
            kind, hit = drawn[p.label]
            rows.append({
                "label": p.label, "section": p.section, "at": p.at,
                "seconds": p.seconds, "timed_by": p.source,
                "visual": (shot.visual if shot else "")[:300],
                "on_screen": shot.on_screen if shot else "",
                "narration": " ".join(p.narration.split())[:300],
                "style": shot.style if shot else "",
                "drawn_by": kind,
                "effect": (f"{hit['file']}#{hit['symbol']}" if kind == "effect"
                           else ""),
                # How long the authored symbol runs, beside how long the shot
                # does: the editor can see a picture that ends early.
                "symbol_seconds": self._symbol_seconds(hit) if kind == "effect" else None,
                "image": hit.path if kind == "image" else ""})
        return {
            "title": self.idea.title,
            "treatment": design.treatment,
            "frame": {"width": self.width, "height": self.height, "fps": self.fps},
            "design": {"roles": design.tokens(), "source": design.source},
            "shots": rows,
            "uncovered": [p.label for p in placed if drawn[p.label][0] == "none"],
            "images": [{"path": m.path, "licence": m.licence, "credit": m.credit,
                        "about": m.about} for m in media],
        }

    def _symbol_seconds(self, hit: dict) -> float | None:
        """The authored length of the symbol that draws a shot, or None."""
        from .vfx import symbol_facts
        try:
            text = (self.root / hit["file"]).read_text(encoding="utf-8")
        except OSError:
            return None
        for fact in symbol_facts(text):
            if fact["id"] == hit["symbol"]:
                return fact["duration"] or None
        return None

    def _decisions(self, placed: list[Placed], drawn: dict[str, tuple],
                   media: list[Media]) -> dict:
        """The edit decisions, resolved against what actually exists.

        An `extend` becomes a span on the neighbouring effect; an `image`
        becomes that file, if it is one the project has; anything else -- a
        block, an image that is not there, an extension with nothing to
        extend -- stays a placeholder, with the reason recorded.
        """
        order = [p.label for p in placed]
        index = {label: n for n, label in enumerate(order)}
        by_path = {m.path: m for m in media}
        applied: dict[str, str] = {}
        reasons: dict[str, str] = {}
        covered_by: dict[str, str] = {}
        images: dict[str, Media] = {}
        findings: list[str] = []
        wanted = {row.get("label"): row for row in self.edit.get("coverage", [])
                  if isinstance(row, dict)}
        for p in placed:
            kind, _ = drawn[p.label]
            if kind != "none":
                continue
            row = wanted.get(p.label)
            action = str((row or {}).get("action") or "block")
            reasons[p.label] = str((row or {}).get("reason") or
                                   ("no decision was made for this shot"
                                    if row is None else ""))
            if action in ("extend_previous", "extend_next"):
                step = -1 if action == "extend_previous" else 1
                n = index[p.label] + step
                source = None
                while 0 <= n < len(order):
                    if drawn[order[n]][0] == "effect":
                        source = order[n]
                        break
                    if order[n] not in covered_by:
                        break
                    n += step
                if source is None:
                    findings.append(f"warning: {p.label}: {action} asked, but no "
                                    "effect lies that way; left as a placeholder")
                    applied[p.label] = "block"
                    continue
                covered_by[p.label] = source
                applied[p.label] = action
            elif action == "image":
                path = str((row or {}).get("image") or "")
                if path in by_path:
                    images[p.label] = by_path[path]
                    applied[p.label] = "image"
                else:
                    findings.append(f"warning: {p.label}: the image {path!r} is "
                                    "not one the project has; left as a placeholder")
                    applied[p.label] = "block"
            else:
                applied[p.label] = "block"
        # The span of each effect, stretched over the shots it now covers.
        spans: dict[str, list[float]] = {}
        for p in placed:
            source = covered_by.get(p.label, p.label if drawn[p.label][0] == "effect"
                                    else None)
            if source is None:
                continue
            span = spans.setdefault(source, [p.at, p.at + p.seconds])
            span[0] = min(span[0], p.at)
            span[1] = max(span[1], p.at + p.seconds)
        asked = self.edit.get("tail_seconds", 0.5)
        try:
            tail = float(asked)
            if tail != tail:
                raise ValueError(asked)
        except (TypeError, ValueError):
            tail = 0.5
            findings.append(f"warning: the editor's tail_seconds {asked!r} is not a "
                            f"number; the film holds {tail:g} s after the last shot")
        held = min(5.0, max(0.0, tail))
        if held != tail:
            findings.append(f"warning: the editor's tail_seconds {asked!r} is outside "
                            f"0..5; the film holds {held:g} s after the last shot")
        tail = held
        # The hold after the last shot is a hold on its last frame, so the
        # effect that ends the film runs through the tail; otherwise the tail
        # shows the bare project background.
        if spans and tail:
            film_end = max(p.at + p.seconds for p in placed)
            for span in spans.values():
                if span[1] >= film_end:
                    span[1] = round(film_end + tail, 3)
        # Consecutive shots the same symbol draws are one include: the symbol
        # carries its whole run on one clock, so restarting it per shot would
        # snap its camera back to t=0 at every cut. Spans that name the same
        # {file, symbol} fold into the first when nothing else is placed
        # between them: measured takes sit a breath apart, and the world runs
        # on through the breath. A symbol used again after another shot stays
        # a separate include.
        def gap_is_empty(after: float, before: float) -> bool:
            return not any(after - 1e-3 < q.at < before - 1e-3 for q in placed)

        merged: dict[str, str] = {}
        owners = sorted(spans, key=lambda label: spans[label][0])
        run_owner = None
        for label in owners:
            hit = self.vfx_symbols[label]
            if (run_owner is not None
                    and self.vfx_symbols[run_owner]["file"] == hit["file"]
                    and self.vfx_symbols[run_owner]["symbol"] == hit["symbol"]
                    and gap_is_empty(spans[run_owner][1], spans[label][0])):
                spans[run_owner][1] = max(spans[run_owner][1], spans[label][1])
                merged[label] = run_owner
                del spans[label]
                continue
            run_owner = label
        # A run the fold left in more than one include restarts the world at
        # t=0 in each: name it, since no later gate reads the include count.
        # Two shots of one symbol count as one run when no other effect's shot
        # lies between them (a gap or a placeholder does not end a run).
        split_worlds: list[dict] = []
        runs: list[tuple[tuple, list[tuple[str, str]]]] = []
        for p in placed:
            if drawn[p.label][0] != "effect":
                continue
            hit = self.vfx_symbols[p.label]
            key = (p.section, hit["file"], hit["symbol"])
            row = (p.label, merged.get(p.label, p.label))
            if runs and runs[-1][0] == key:
                runs[-1][1].append(row)
            else:
                runs.append((key, [row]))
        for (section, file, symbol), rows in runs:
            owners = list(dict.fromkeys(owner for _, owner in rows))
            if len(owners) > 1:
                split_worlds.append({"symbol": symbol, "file": file,
                                     "section": section,
                                     "shots": [label for label, _ in rows],
                                     "includes": len(owners)})
        for row in split_worlds:
            findings.append(
                f"warning: symbol {row['symbol']!r} draws shots "
                f"{', '.join(row['shots'])} of {row['section']!r} but ends up in "
                f"{row['includes']} includes, each restarting the world at t=0; "
                "the cut between them will snap")
        return {"applied": applied, "reasons": reasons, "covered_by": covered_by,
                "merged": merged, "split_worlds": split_worlds,
                "images": images, "spans": spans, "findings": findings,
                "captions_off": {str(x) for x in self.edit.get("captions_off", [])},
                "tail": tail,
                "notes": str(self.edit.get("notes", "")),
                "blocking": [str(x) for x in self.edit.get("blocking", [])]}

    # -- the mix ---------------------------------------------------------------

    def _mix(self, scene, assets, placed: list[Placed], duration: float) -> dict:
        """The narration takes and the sound design's cues, as one <audioMix>.

        A bus per role -- narration, music, effects, ambience -- with the
        cue's level staged across bus and track; music and ambience duck
        under the narration bus when there is one. A silence cue is a window
        on the master's volume. A cue with no file, or a file that is not
        there, is listed as missing and not invented.
        """
        report = {"tracks": 0, "narration": 0, "buses": {}, "missing": [],
                  "silence": 0, "unplaced": False, "findings": [],
                  "staging": {"bed_db": None, "headroom_db": None,
                              "shift_db": 0.0, "shifted": []}}
        if not self.audio:
            return report
        findings = report["findings"]
        roles = enum_of("audioTrack", "role")
        role_of = {"narration": pick(roles, "voiceover", "dialogue"),
                   "music": pick(roles, "music"),
                   "effects": pick(roles, "effects"),
                   "ambience": pick(roles, "ambience")}
        lo, hi = facets_of("audioTrack", "gain")
        bus_lo, bus_hi = facets_of("bus", "gain")

        # (bus, track node, the gain it asks for, kind, seconds, cue id)
        routed: list[list] = []
        windows: list[tuple[float, float]] = []
        mix = None

        def mixer():
            nonlocal mix
            if mix is None:
                mix = scene.add_audioMix()
            return mix

        for p in placed:
            if not (p.audio and p.narration):
                continue
            src = f"voice/{p.audio}"
            if not (self.root / src).is_file():
                continue
            asset = assets.add_audio(id=ident(f"vo-{p.label}"), src=src)
            asset.duration = p.seconds
            asset.language = self.lang
            track = mixer().add_audioTrack(id=ident(f"vo-track-{p.label}"),
                                           asset=asset.id)
            track.start = p.at
            track.bus = NARRATION_BUS
            if role_of["narration"]:
                track.role = role_of["narration"]
            routed.append([NARRATION_BUS, track, 0.0, "narration", p.seconds, p.label])
            report["narration"] += 1

        designed = 0
        for n, cue in enumerate(self.cues, 1):
            kind = str(cue.get("kind") or "").lower()
            cue_id = str(cue.get("cue_id") or cue.get("id") or f"C{n}")
            about = " ".join(str(cue.get("description") or cue.get("name")
                                 or "").split())
            try:
                at = float(cue.get("at") or 0)
                seconds = float(cue.get("seconds") or 0)
            except (TypeError, ValueError):
                findings.append(f"warning: cue {cue_id} has no usable time; left out")
                continue
            if kind == "silence":
                if seconds > 0 and at < duration:
                    windows.append((round(at, 3), round(min(duration, at + seconds), 3)))
                continue
            designed += 1
            bus = CUE_BUS.get(kind)
            if bus is None:
                findings.append(f"warning: cue {cue_id} has kind {kind!r}, which "
                                "the mix has no bus for; placed with the effects")
                bus = CUE_BUS["sfx"]
            file = str(cue.get("file") or "")
            reason = ("no file yet" if not file
                      else "file not on disk" if not (self.root / file).is_file()
                      else "starts after the film ends" if at >= duration
                      else "")
            if reason:
                report["missing"].append({"cue": cue_id, "kind": kind,
                                          "reason": reason, "description": about,
                                          "file": file})
                continue
            window = round(min(seconds, duration - at), 3)
            if window <= 0:
                report["missing"].append({"cue": cue_id, "kind": kind,
                                          "reason": "no length", "description": about,
                                          "file": file})
                continue
            length = audio_seconds(self.root / file)

            asset = assets.add_audio(id=ident(f"cue-asset-{cue_id}"), src=file)
            licence = str(cue.get("licence") or cue.get("license") or "")
            credit = str(cue.get("credit") or "")
            if licence:
                asset.license = licence
            if credit:
                asset.credit = credit
            if length:
                asset.duration = round(length, 3)
            track = mixer().add_audioTrack(id=ident(f"cue-{cue_id}"), asset=asset.id)
            track.start = round(at, 3)
            track.bus = bus
            if role_of[bus]:
                track.role = role_of[bus]
            if cue.get("loop"):
                if length:
                    plays = max(1, math.ceil(window / length))
                    if plays > 1:
                        track.loop = plays - 1
                else:
                    findings.append(f"warning: cue {cue_id} asks to loop, but the "
                                    f"length of {file} could not be read; it plays "
                                    "once")
            elif length is None or length > window:
                track.clipOut = window
            def number(key: str) -> float:
                try:
                    return float(cue.get(key) or 0)
                except (TypeError, ValueError):
                    findings.append(f"warning: cue {cue_id} has {key} "
                                    f"{cue.get(key)!r}, which is not a number; "
                                    "0 is used")
                    return 0.0

            for key, attr in (("fade_in", "fadeIn"), ("fade_out", "fadeOut")):
                fade = number(key)
                if fade > 0:
                    setattr(track, attr, round(min(fade, window), 3))
            trim = number("gain_db")
            said = str(cue.get("level") or "").strip()
            level = level_db(said)
            if level is None and said:
                findings.append(f"warning: cue {cue_id} has a level written in "
                                f"words only ({said[:60]!r}); it is mixed at 0 dB "
                                "trim")
            routed.append([bus, track, (level or 0.0) + trim, kind, window, cue_id])

        # Transients first: the limiter runs after normalisation, so a hit
        # far above the bed is limited after the lift and the programme loses
        # loudness it never gets back. The short effects come down as a
        # group until the loudest sits within the master's own headroom.
        if self.normalize != "none":
            crest, crest_from = measured_crest(self.root, target=self.loudness,
                                               true_peak=self.true_peak)
            headroom = transient_headroom(self.loudness, self.true_peak, crest=crest)
            shift, bed = transient_shift([(r[3], r[4], r[2]) for r in routed], headroom)
            report["staging"].update({"bed_db": bed, "headroom_db": headroom,
                                      "shift_db": shift, "crest_db": crest,
                                      "crest_from": crest_from})
            transients = [r for r in routed
                          if r[3] in TRANSIENT_KINDS and r[4] <= TRANSIENT_SECONDS]
            if transients and bed is None:
                findings.append(
                    f"warning: no bed under {len(transients)} transient cue(s): "
                    "the limiter, which runs after normalisation, will take the "
                    "loudness and the integrated target will be missed; the sound "
                    "design needs music or ambience under them")
            elif shift:
                loudest = max(r[2] for r in transients)
                for r in transients:
                    r[2] = round(r[2] - shift, 3)
                report["staging"]["shifted"] = [r[5] for r in transients]
                findings.append(
                    f"note: the cue sheet puts its loudest transient {loudest - bed:+.1f} "
                    f"dB above the bed ({bed:+.1f} dB); delivery at {self.loudness} "
                    f"LUFS under {self.true_peak} dBTP leaves {headroom:.1f} dB of "
                    f"headroom, so {len(transients)} short effect(s) come down "
                    f"{shift:.1f} dB together, keeping their balance")

        # Gain staging: the bus carries the role, the track the deviation, each
        # inside the schema's range, which saturates at +24 dB per stage.
        by_bus: dict[str, list[tuple[object, float]]] = {}
        for bus, track, total, *_ in routed:
            by_bus.setdefault(bus, []).append((track, total))
        bus_nodes: dict[str, object] = {}
        for bus_id, rows in by_bus.items():
            bus_gain, trims = stage_gain([t for _, t in rows],
                                         max(lo, bus_lo), min(hi, bus_hi))
            node = mixer().add_bus(id=bus_id)
            bus_nodes[bus_id] = node
            if bus_gain:
                node.gain = bus_gain
            if ducks(bus_id) and NARRATION_BUS in by_bus:
                node.duckUnder = [NARRATION_BUS]
            for (track, total), trim in zip(rows, trims):
                if trim:
                    track.gain = trim
                if abs(bus_gain + trim - total) > 0.01:
                    findings.append(
                        f"warning: {track.id} asks for {total:+.1f} dB; the schema "
                        f"caps gain at {hi:+.0f} dB per track and per bus, so it "
                        f"sits at {bus_gain + trim:+.1f} dB")
            report["buses"][bus_id] = len(rows)
        report["tracks"] = len(routed)
        report["unplaced"] = designed > 0 and not any(
            r[0] != NARRATION_BUS for r in routed)
        if report["unplaced"]:
            findings.append(
                f"warning: the sound design has {designed} cue(s) and none "
                "could be placed; fetch its files (yt sound) and build again")
        if mix is None and not windows:
            return report

        # Loudness belongs to the programme, not to the file: <output>
        # describes the container, <master> describes the mix, and
        # `integrated` measures the whole programme once -- which is why
        # cutting a range never makes the audio cheaper.
        master = mixer().add_master()
        normalize, reason = self.normalize, self.normalize_reason
        asked = self.sound_normalize
        if isinstance(asked, dict) and asked.get("mode") in ("dynamic", "integrated", "none"):
            normalize, reason = asked["mode"], str(asked.get("reason") or "")
        if normalize == "dynamic" and not reason.strip():
            # a gain riding 3-second loudness lifts the beds between sentences
            findings.append(
                "warning: normalize=dynamic asked with no reason; the master "
                "stays integrated (one gain to the target)")
            normalize = "integrated"
        if normalize:
            master.normalize = normalize
        if normalize != "none":
            master.loudness = self.loudness
        master.truePeak = self.true_peak
        # The limiter runs AFTER normalisation, so strong short peaks --
        # stingers, clicks -- eat loudness and the integrated target is
        # never reached. Raising the target does not help, because the
        # limiter is what is in charge; the fix is to quieten those
        # effects. Hence a flag: with it off, the shortfall is visible.
        master.limiter = self.limiter
        if windows:
            # Designed silence is a held 1 -> 0 -> 1 on the score and the
            # effects buses, never the master: a master window mutes the
            # narration with them (measured on the Euler draft: whole lines
            # of voice gone, 30 s of digital silence). `key/@value` is xs:string.
            keys: dict[float, str] = {}
            windows.sort()
            if windows[0][0] > 0:
                keys[0.0] = "1"
            for start, end in windows:
                keys[start] = "0"
                keys.setdefault(end, "1")
            for bus_id in SILENCED_BUSES:
                if bus_id not in bus_nodes:
                    continue
                gate = bus_nodes[bus_id].add_animate(property="volume",
                                                     defaultInterpolation="hold")
                for time in sorted(keys):
                    gate.add_key(time=time, value=keys[time])
            report["silence"] = len(windows)
        return report

    # -- the document ----------------------------------------------------------

    def _colour(self, scene) -> None:
        """The film's one colour pipeline, as the model decided it."""
        look = self.look
        if not look:
            return
        grade = look["look"] == "grade"
        pipeline = scene.add_colorManagement(
            workingSpace="acescg", toneMapping=look["tone_mapping"],
            exposure=look["exposure"])
        if grade:
            pipeline.add_look(
                id=LOOK_ID, space="acescct", mix=look["mix"],
                saturation=look["saturation"],
                **{k: ",".join(f"{v:g}" for v in look[k])
                   for k in ("slope", "offset", "power")})
            pipeline.looks = LOOK_ID

    def build(self) -> tuple[bytes, dict]:
        S = sdk()
        placed = timeline(self.sections, self.voice)
        if not placed:
            raise RuntimeError("the screenplay has no shots to place")
        media = local_media(self.root, self.dossier)
        drawn = self._drawn(placed, media)
        edit = self._decisions(placed, drawn, media)
        duration = round(max(p.at + p.seconds for p in placed) + edit["tail"], 3)

        scene = S.Scene(version="auto")
        scene.add_project(width=self.width, height=self.height, fps=str(self.fps),
                          duration=duration, background=self.background)

        self._colour(scene)

        meta = scene.add_metadata()
        meta.add_meta(name="title", value=self.idea.title)
        meta.add_meta(name="premise", value=self.idea.description)
        meta.add_meta(name="built-by", value="ytstudio yt build")
        meta.add_meta(name="timeline", value=(
            "measured narration" if any(p.source == "voice" for p in placed)
            else "screenplay estimates"))
        if self.dossier:
            meta.add_meta(name="sources",
                          value=str(len(self.dossier.get("sources", []))))

        assets = scene.add_assets()
        composition = scene.add_composition()
        markers = scene.add_markers()
        section_kind = pick(enum_of("marker", "kind"), "section", "chapter")
        beat_kind = pick(enum_of("marker", "kind"), "beat", "cue")
        fit = pick(enum_of("layer", "fit"), "contain", "cover")

        # section markers first: a pilot asks for "from this section to that one"
        at = 0.0
        by_label = {p.label: p for p in placed}
        for n, section in enumerate(self.sections, 1):
            first = next((by_label[s.label] for s in section.shots
                          if s.label in by_label), None)
            if first is None:
                continue
            marker = markers.add_marker(time=first.at, label=section.heading)
            marker.id = ident(f"section-{n}")
            if section_kind:
                marker.kind = section_kind

        # The design system, read from styles.json: its tokens are what the
        # authored effects paint with, and the manifest records the roles.
        design = designer.Design.from_styles(self.styles, height=self.height)

        placeholders = []
        placed_srcs: set[str] = set()      # images the build placed itself
        includes: list[tuple[str, str, float, float]] = []
        # The copy each include points at, written once per authored file.
        cleaned: dict[str, dict] = {}
        pinner = fonts.Pinner(self.root)
        include_findings: list[str] = []
        timing_errors: list[str] = []      # a symbol shorter than its shot

        def copy_of(file: str) -> str:
            info = cleaned.get(file)
            if info is None:
                target = include_copy_of(file)
                info = cleaned_include(self.root / file, self.root / target,
                                       pinner)
                info["src"] = target if not any(
                    f.startswith("error") for f in info["findings"]) else file
                info["file"] = file
                include_findings.extend(info["findings"])
                for name in info["unresolved"]:
                    include_findings.append(
                        f"warning: {Path(file).name}: var(--{name}) names a token "
                        "the file does not declare; left as written")
                cleaned[file] = info
            return info["src"]

        for p in placed:
            shot_id = ident(f"shot-{p.label}")
            marker = markers.add_marker(time=p.at, duration=p.seconds,
                                        label=p.label)
            marker.id = ident(f"beat-{p.label}")
            if beat_kind:
                marker.kind = beat_kind

            shot = next((s for sec in self.sections for s in sec.shots
                         if s.label == p.label), None)
            kind, hit = drawn[p.label]
            if kind == "effect":
                p.asset, p.file = "effect", f"{hit['file']}#{hit['symbol']}"
                owner = edit["merged"].get(p.label)
                if owner:
                    # The same symbol, still running from an earlier shot.
                    p.include = ident(f"shot-{owner}")
                    continue
                # The effect the model authored for this shot, running over
                # whatever neighbouring gaps the editor gave it and over the
                # following shots the same symbol draws.
                start, end = edit["spans"][p.label]
                src = copy_of(hit["file"])
                authored = cleaned[hit["file"]].get("durations", {}).get(hit["symbol"])
                if authored is not None and authored + TIMING_SLACK < p.seconds:
                    # The include would hold the symbol's last frame under
                    # the rest of the narration: picture and voice apart.
                    timing_errors.append(
                        f"error: shot {p.label} runs {p.seconds:g}s (timed by "
                        f"{p.source}) but symbol {hit['symbol']!r} in "
                        f"{Path(hit['file']).name} is authored for {authored:g}s, "
                        f"so the picture ends {p.seconds - authored:.1f}s before the "
                        "narration. Run `yt retime`, then author it again with `yt vfx`")
                    include_findings.append(timing_errors[-1])
                if hit["symbol"] not in cleaned[hit["file"]]["symbols"]:
                    include_findings.append(
                        f"error: {shot_id} names symbol {hit['symbol']!r}, which "
                        f"{Path(hit['file']).name} does not declare")
                node = composition.add_include(id=shot_id, src=src,
                                               symbol=hit["symbol"])
                node.start = round(start, 3)
                node.end = round(end, 3)
                includes.append((shot_id, hit["file"], round(start, 3), round(end, 3)))
                p.include = shot_id
                continue
            source = edit["covered_by"].get(p.label)
            if source:
                owner = self.vfx_symbols[source]
                p.asset = "effect"
                p.file = f"{owner['file']}#{owner['symbol']} (extended from {source})"
                p.include = ident(f"shot-{edit['merged'].get(source, source)}")
                continue
            if kind == "image":
                hit = hit
            else:
                hit = edit["images"].get(p.label)
            if hit is not None:
                asset = assets.add_image(id=ident(f"img-{p.label}"),
                                         src=hit.path, width=self.width,
                                         height=self.height)
                if hit.licence:
                    asset.license = hit.licence
                if hit.credit:
                    asset.credit = hit.credit
                p.asset, p.file = "image", hit.path
                placed_srcs.add(hit.path)
            else:
                # An honest placeholder: the shot's own description on screen,
                # so the cut reads and the gap is visible in the render.
                body = (shot.visual if shot is not None else p.label)[:220]
                asset = assets.add_text(
                    id=ident(f"card-{p.label}"),
                    text=f"{p.label}\n\n{body}",
                    width=int(self.width * 0.8), height=int(self.height * 0.7),
                    size=design.size("label"))
                asset.align = "center"
                asset.color = design.role("ink")
                placeholders.append(p.label)

            layer = composition.add_layer(id=shot_id, asset=asset.id)
            layer.start = p.at
            layer.end = round(p.at + p.seconds, 3)
            if hit is not None and fit:
                # A fitted layer needs its box, or the engine refuses it (C16).
                layer.fit = fit
                layer.boxWidth = str(self.width)
                layer.boxHeight = str(self.height)

        # What the includes do not carry: the design's tokens first, then
        # each included document's tokens and lights, the lights gated to the
        # span of their include so one section's key light never lights another.
        lifted: list[str] = []
        # The authored documents were composed and proven against their own
        # tokens, so those are the truth; the design's roles fill in what no
        # file declares. Two files disagreeing is reported once per name.
        tokens: dict[str, str] = {}
        declared_in: dict[str, str] = {}
        disputed: set[str] = set()
        text_styles: dict[str, dict] = {}
        hoist_lights = 0
        parsed: dict[str, dict] = {}
        credited: set[str] = set()
        downloads = local_media(self.root, self.dossier)
        lights = None
        # Paints the included content names (`url(#id)`) resolve against the
        # scene's own, so each file's are grafted in through the SDK; the
        # first declaration of an id wins and a different later one is named.
        paints = None
        paint_defs: dict[str, tuple[str, str]] = {}
        for include_id, file, start, end in includes:
            info = parsed.get(file)
            if info is None:
                # The scene is written at the project root, beside the
                # includes' `src` paths, so that is where hoisted paths point.
                info = parsed[file] = hoisted(self.root / file, scene_dir=self.root)
                lifted += info["findings"]
                wanted_paints = []
                for paint_id, canonical in info["paints"]:
                    seen = paint_defs.get(paint_id)
                    if seen is None:
                        paint_defs[paint_id] = (canonical, Path(file).name)
                        wanted_paints.append(paint_id)
                    elif seen[0] != canonical:
                        lifted.append(f"warning: paint {paint_id!r} is declared in "
                                      f"{seen[1]} and differently in {Path(file).name}; "
                                      "the first wins")
                if wanted_paints:
                    try:
                        authored = sdk().read((self.root / file).read_bytes())
                        declared = authored.find("paints")
                        for paint in (declared.elements() if declared is not None
                                      else []):
                            if getattr(paint, "id", None) in wanted_paints:
                                if paints is None:
                                    paints = scene.add_paints()
                                paints.add(paint.copy())
                    except Exception as exc:
                        lifted.append(f"error: {Path(file).name}: its paints could "
                                      f"not be carried into the scene: {exc}")
                for name, value in info["tokens"]:
                    if name in tokens and tokens[name] != value and name not in disputed:
                        disputed.add(name)
                        lifted.append(f"warning: token {name!r} is {tokens[name]} in "
                                      f"{declared_in[name]} and {value} in "
                                      f"{Path(file).name}; the first wins")
                    if name not in tokens:
                        tokens[name] = value
                        declared_in[name] = Path(file).name
                for row in info["text_styles"]:
                    text_styles.setdefault(row["id"], row)
            for row in info["lights"]:
                if not row.get("type"):
                    lifted.append(f"warning: {Path(file).name}: a light without a "
                                  "type was not carried")
                    continue
                if lights is None:
                    lights = scene.add_lights()
                attrs = {k: v for k, v in row.items() if k not in ("id", "animate")}
                light = lights.add_light(id=ident(f"{include_id}-{row.get('id', 'light')}"),
                                         **attrs)
                on = str(row.get("intensity", "1"))
                # The light's own animation, keyed at section time t, arrives
                # at include start + t, its curve and every key attribute
                # kept; the gate keeps it inside the span. A key's curve
                # governs the segment to the next key and the schema's
                # default is linear, so each gate segment is an explicit hold:
                # 0 until the first carried key, the last carried key until
                # the include's end, 0 from there. The author's extrapolation
                # is what the gate replaces, so it is the one thing not carried.
                own = [ElementTree.fromstring(x) for x in row.get("animate", ())]
                keyed = [a for a in own if a.get("property") == "intensity"]
                curve = {k: v for k, v in (keyed[0].attrib if keyed else {}).items()
                         if k not in ("extrapolateBefore", "extrapolateAfter")}
                gate = light.add_animate(**dict(curve, property="intensity"))
                if start > 0:
                    gate.add_key(time=0, value="0", interpolation="hold")
                keys = sorted(((float(k.get("time", 0)), k) for a in keyed
                               for k in a.iter("key")), key=lambda tk: tk[0])
                carried = [(round(start + t, 3), dict(k.attrib))
                           for t, k in keys if start + t < end]
                if not keys or keys[0][0] > 0:
                    # Before its first key the authored document holds that
                    # key's value; with no key it is the light's intensity.
                    first = keys[0][1].get("value", on) if keys else on
                    carried.insert(0, (start, {"value": first}))
                if carried:
                    carried[-1][1]["interpolation"] = "hold"
                for t, attrs in carried:
                    gate.add_key(**dict(attrs, time=t))
                gate.add_key(time=end, value="0")
                for a in own:
                    if a in keyed:
                        continue
                    extra = light.add_animate(**dict(a.attrib))
                    for k in a.iter("key"):
                        extra.add_key(**dict(k.attrib,
                                             time=round(start + float(k.get("time", 0)), 3)))
                hoist_lights += 1
            # Images the author embedded in the world are credited from the
            # download manifest; a file the manifest does not list is an error.
            for rel in info["downloads"]:
                if rel in credited:
                    continue
                credited.add(rel)
                hit = next((m for m in downloads if m.path == rel), None)
                if hit is None:
                    lifted.append(f"error: {Path(file).name} uses {rel}, which "
                                  "raw/08-downloads.json does not list, so it has "
                                  "no licence or credit")
                    continue
                if rel in placed_srcs:
                    continue
                asset = assets.add_image(id=ident(f"credit-{Path(rel).stem}"),
                                         src=rel, width=self.width,
                                         height=self.height)
                if hit.licence:
                    asset.license = hit.licence
                if hit.credit:
                    asset.credit = hit.credit
        # Every paint an include copy names must now be the scene's own.
        for info in cleaned.values():
            for ref in sorted(info["paint_refs"] - set(paint_defs)):
                include_findings.append(
                    f"error: {Path(info['file']).name}: url(#{ref}) names no paint "
                    "the scene carries")
        for name, value in design.tokens().items():
            tokens.setdefault(name.lstrip("-"), value)
        if tokens or text_styles:
            styles = scene.add_styles()
            for name, value in tokens.items():
                styles.add_token(name=name, value=value)
            for row in text_styles.values():
                try:
                    styles.add_textStyle(**row)
                except Exception as exc:
                    lifted.append(f"warning: text style {row.get('id')!r} was not "
                                  f"carried: {exc}")

        sound = self._mix(scene, assets, placed, duration)

        cues = 0
        if self.captions and any(p.narration for p in placed):
            tracks = scene.add_captions()
            track = tracks.add_captionTrack(id="vo", language=self.lang)
            kind = pick(enum_of("captionTrack", "kind"), "subtitles", "captions")
            if kind:
                track.kind = kind
            # Burned captions share the lower band with the authored type
            # (measured on the Euler draft: tallies, formulas and name labels
            # printed under them in four sections); a platform takes the
            # sidecar track, which the viewer turns on. --burn-captions burns.
            mode = pick(enum_of("captionTrack", "mode"),
                        "burn" if self.burn_captions else "sidecar")
            if mode:
                track.mode = mode
            spoken = [p for p in placed if p.narration
                      and p.label not in edit["captions_off"]]
            # The cues are the one splitter's (design.caption_cues), from the
            # voice.json takes, so burned-in and sidecar captions are the same
            # cues; a spoken shot with no take is split from its own timing.
            taken = {t.get("label"): t for t in (self.voice or {}).get("takes", [])}
            takes = []
            for p in spoken:
                take = dict(taken.get(p.label) or {})
                take.setdefault("text", p.narration)
                take.setdefault("at", p.at)
                take.setdefault("seconds", p.seconds)
                takes.append(take)
            split, _, illegal = designer.caption_cues(takes, film_end=duration)
            if illegal:
                # AUTO-64: a take faster than the reading speed is named, not hidden
                include_findings.extend(designer.caption_findings(split))
            for chunk in split:
                cue = track.add_cue(start=chunk.start, end=chunk.end)
                cue.text = chunk.text
                owner = next((p for p in reversed(spoken)
                              if p.at <= chunk.start + 0.001), None)
                if owner is not None and owner.speaker:
                    cue.speaker = owner.speaker
                cues += 1

        output = scene.add_output(path="video.mp4", codec=self.codec)
        output.id = "full"
        # Never `crf`: NVENC ignores it, falls back to a default, and the file
        # has nothing to do with the request -- with no warning anywhere. An
        # explicit bitrate is the only thing that survives that encoder.
        if self.bitrate:
            output.bitrate = self.bitrate
        if self.max_bitrate:
            output.maxBitrate = self.max_bitrate
        if self.buffer_size:
            output.bufferSize = self.buffer_size
        # `preset` is a free string in the schema, not an enumeration, so
        # pick() would discard it: an empty enum means anything is allowed.
        allowed = enum_of("output", "preset")
        if self.preset and (not allowed or self.preset in allowed):
            output.preset = self.preset

        pinner.pin_scene(scene)
        findings = [str(f) for f in scene.check()]
        data = scene.to_bytes(layout="pretty")
        # Read the document back and check it again: the version is resolved
        # only on serialisation, and a re-read proves the file itself parses.
        reparse: list[str] = []
        version = str(getattr(scene, "version", ""))
        try:
            again = sdk().read(data)
            version = str(getattr(again, "version", version))
            reparse = [str(f) for f in again.check()]
        except Exception as exc:
            reparse = [f"error: the written scene did not parse back: {exc}"]
        reparse += duplicate_animations(data)
        reparse += (edit["findings"] + include_findings + lifted + sound["findings"]
                    + pinner.errors)
        reparse += [f"warning: blocking (editorial): {x}" for x in edit["blocking"]]
        manifest = {
            "idea": asdict(self.idea),
            "scene": {"version": version,
                      "width": self.width, "height": self.height,
                      "fps": str(self.fps), "duration": duration,
                      "background": self.background,
                      "background_from": self.background_from,
                      "codec": self.codec, "language": self.lang,
                      "bitrate": self.bitrate,
                      "max_bitrate": self.max_bitrate,
                      "loudness": self.loudness,
                      "true_peak": self.true_peak,
                      "normalize": self.normalize,
                      "limiter": self.limiter},
            "look": self.look,
            "timeline": {"shots": len(placed),
                         "from_voice": sum(1 for p in placed if p.source == "voice"),
                         "from_script": sum(1 for p in placed if p.source == "script"),
                         "runtime": duration},
            "layers": {"effect": sum(1 for p in placed if p.asset == "effect"),
                       "drawn": sum(1 for p in placed if p.asset == "drawn"),
                       "image": sum(1 for p in placed if p.asset == "image"),
                       "placeholder": len(placeholders)},
            # The production brief: a placeholder is disqualifying, not deductible,
            # and so is anything the editor named as blocking, and so is a
            # sound design of which nothing reached the mix.
            # ... and so is a picture that ends before its narration does.
            "publishable": (not placeholders and not edit["blocking"]
                            and not sound["unplaced"] and not timing_errors),
            "timing": timing_errors,
            "editorial": {"applied": edit["applied"], "reasons": edit["reasons"],
                          "captions_off": sorted(edit["captions_off"]),
                          "tail_seconds": edit["tail"], "notes": edit["notes"],
                          "blocking": edit["blocking"]},
            "design": {"treatment": design.treatment, "source": design.source,
                       "roles": {r: design.role(r) for r in
                                 ("background", "object", "choice", "positive",
                                  "negative", "muted", "ink")}},
            "placeholders": placeholders,
            "hoisted": {"lights": hoist_lights, "tokens": len(tokens),
                        "text_styles": len(text_styles),
                        "paints": len(paint_defs),
                        "from": sorted(parsed),
                        # include copy -> the authored document it was cut from
                        "cleaned": {info["src"]: file for file, info in cleaned.items()
                                    if info["src"] != file}},
            "includes": len(includes),
            "split_worlds": edit["split_worlds"],
            "fonts": pinner.report(),
            "audio_tracks": sound["tracks"],
            "narration_tracks": sound["narration"],
            "buses": sound["buses"],
            "audio_missing": sound["missing"],
            "audio_staging": sound["staging"],
            "silence_windows": sound["silence"],
            "cues": cues,
            "markers": {"sections": len(self.sections), "beats": len(placed)},
            # The timings here are the measured ones, so this reading check
            # is sharper than the screenplay's own.
            "legibility": legibility(self.sections,
                                     {p.label: p.seconds for p in placed}),
            "stale_takes": stale_takes(self.sections, self.voice),
            "findings": findings + [f for f in reparse if f not in findings],
            "shots": [asdict(p) | {"words": len(p.words)} for p in placed],
        }
        return data, manifest


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #

def stale_takes(sections: list[Section], voice: dict | None) -> list[dict]:
    """Shots whose recording no longer matches the screenplay's words.

    The narration lives in script.json and again in voice.json, because a take
    records the text it was given. Edit a line after recording and the audio
    says the old words while the captions this stage writes say the new ones,
    with nothing to warn you -- the same silent divergence a peer session hit
    by keeping the text in three places. So the two are compared here.
    """
    takes = {t["label"]: t for t in (voice or {}).get("takes", [])}
    out = []
    for section in sections:
        for shot in section.shots:
            take = takes.get(shot.label)
            if not take:
                continue
            said = " ".join(str(take.get("text", "")).split())
            written = " ".join((shot.narration or "").split())
            if said and written and said != written:
                out.append({"label": shot.label, "recorded": said[:60],
                            "written": written[:60]})
    return out


def duplicate_animations(document: bytes) -> list[str]:
    """Properties animated twice on the same node, which silently trample.

    Two <animate> on one property of one node do not compose -- the later one
    wins, the schema accepts it, and the engine says nothing. The result is
    elements appearing at the wrong time with no error anywhere, so the
    document is parsed after it is written and the collisions are named.
    `additive` is how a document asks for composition on purpose, so an
    animate that declares it is left alone.

    Found the hard way by a peer session, on a third of an episode.
    """
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError as exc:
        return [f"error: the written scene is not parseable XML: {exc}"]
    seen: dict[tuple[str, str], int] = {}
    out = []
    for parent in root.iter():
        owner = parent.get("id") or parent.tag
        for child in parent:
            if not child.tag.endswith("animate") or child.get("additive"):
                continue
            key = (owner, child.get("property", "?"))
            seen[key] = seen.get(key, 0) + 1
            if seen[key] == 2:
                out.append(f"error: {key[1]!r} on {owner!r} is animated more "
                           "than once; the later animate wins silently. Merge "
                           "them, or set additive.")
    return out


def look_prompt(data: dict) -> str:
    """The question the colourist answers: one look for the whole film."""
    return "\n\n".join([
        "Decide the ONE colour look every section of this film shares. Choose "
        "the `tone_mapping` operator and `exposure` (stops) that keep lit "
        "surfaces inside the palette, and, if the treatment calls for a grade, "
        "an ASC CDL: per-channel `slope`, `offset`, `power` and a `saturation`, "
        "with `mix` for its strength. Answer `look: none` (and tone_mapping "
        "none) when the treatment wants the colours as authored. Give the "
        "`reason` in terms of the treatment and the palette.",
        brief("Film", {k: data.get(k) for k in ("title", "treatment", "design")}),
    ])


def edit_prompt(data: dict) -> str:
    """The question the editor answers, over the builder's brief."""
    return "\n\n".join([
        "Decide the edit for this film's composition. For EVERY shot listed in "
        "`uncovered` give one `coverage` entry: `extend_previous` or "
        "`extend_next` when the neighbouring effect genuinely carries this "
        "shot's visual (read both shots' `visual` and `narration`), `image` "
        "with the exact `path` of one of the `images` when it shows what the "
        "shot describes and its licence allows it, otherwise `block` with the "
        "reason the animation stage needs. List in `captions_off` any shot "
        "whose caption would fight a dense super or a full-frame figure. Set "
        "`tail_seconds` for the hold after the last shot. In `blocking`, name "
        "anything that prevents publication regardless of coverage; leave it "
        "empty when nothing does.",
        brief("Film", {k: data[k] for k in ("title", "treatment", "frame", "design")}),
        brief("Shots, in order", data["shots"]),
        brief("Uncovered shots", data["uncovered"]),
        brief("Images the project has", data["images"]),
    ])


def print_build(manifest: dict) -> None:
    scene, line = manifest["scene"], manifest["timeline"]
    print(f"\n{manifest['idea']['title']} — SCENE")
    print(f"scene-render {scene['version']} · {scene['width']}x{scene['height']}"
          f" @ {scene['fps']} fps · {timecode(scene['duration'])}"
          f" · {scene['codec']}")
    print(f"background {scene['background']} (from {scene['background_from']})")
    print(f"\n  {line['shots']} shots: {line['from_voice']} timed by the "
          f"narration, {line['from_script']} by the screenplay")
    layers = manifest["layers"]
    print(f"  layers: {layers.get('effect', 0)} compiled effects, "
          f"{layers['image']} from "
          f"real files, {layers['placeholder']} placeholder cards")
    print(f"  publishable: {'yes' if manifest.get('publishable') else 'NO'}")
    buses = manifest.get("buses") or {}
    print(f"  {manifest['audio_tracks']} audio tracks "
          f"({manifest.get('narration_tracks', 0)} narration) on "
          f"{len(buses)} bus(es): "
          f"{', '.join(f'{b} ×{n}' for b, n in buses.items()) or 'none'} · "
          f"{manifest.get('silence_windows', 0)} silence window(s)")
    print(f"  {manifest['cues']} captions · "
          f"{manifest['markers']['sections']} section markers + "
          f"{manifest['markers']['beats']} shot markers · "
          f"{manifest.get('includes', 0)} include(s) for "
          f"{manifest['markers']['beats']} shots")
    for row in manifest.get("split_worlds") or []:
        print(f"  warning: {row['symbol']} is split into {row['includes']} "
              f"include(s) over shots {', '.join(row['shots'])}: the world "
              "restarts at t=0 at each cut")
    if manifest.get("audio_missing"):
        rows = manifest["audio_missing"]
        print(f"\n  {len(rows)} cue(s) with no file to place:")
        for row in rows[:10]:
            print(f"    {row['cue']:<4} {row['kind']:<10} {row['reason']}: "
                  f"{row['description'][:60]}")
    editorial = manifest.get("editorial") or {}
    if editorial.get("applied"):
        print("\n  the editor's coverage decisions:")
        for label, action in list(editorial["applied"].items())[:12]:
            reason = (editorial.get("reasons") or {}).get(label, "")
            print(f"    {label:<6} {action:<16} {reason[:70]}")
    if editorial.get("captions_off"):
        print(f"  captions left out on: {', '.join(editorial['captions_off'])}")
    if editorial.get("notes"):
        print(f"  editor's notes: {editorial['notes'][:200]}")
    if editorial.get("blocking"):
        print("\n  BLOCKING, by the editor:")
        for item in editorial["blocking"][:8]:
            print(f"    - {item}")
    if manifest["placeholders"]:
        shown = ", ".join(manifest["placeholders"][:12])
        more = (f" (+{len(manifest['placeholders']) - 12})"
                if len(manifest["placeholders"]) > 12 else "")
        print(f"\n  no art yet: {shown}{more}")
    if manifest.get("stale_takes"):
        print(f"\n  {len(manifest['stale_takes'])} take(s) recorded with text "
              "that no longer matches the screenplay — the audio says one "
              "thing and the captions another:")
        for row in manifest["stale_takes"][:6]:
            print(f"    {row['label']:<6} recorded:   {row['recorded']}")
            print(f"    {'':<6} screenplay: {row['written']}")
        print("    fix with: yt voice --redo "
              + ",".join(r["label"] for r in manifest["stale_takes"][:6]))
    if manifest.get("legibility"):
        print(f"\n  {len(manifest['legibility'])} super(s) with no time to be "
              "read:")
        for row in manifest["legibility"][:8]:
            print(f"    {row['label']:<6} {row['cps']} cps, needs "
                  f"{row['needs_seconds']}s and has {row['seconds']}s")
    if manifest["findings"]:
        print(f"\n  the SDK raised {len(manifest['findings'])} finding(s):")
        for finding in manifest["findings"][:12]:
            print(f"    {finding}")


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "build", parents=list(parents),
        help="compile the project into a scene-render document",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--width", type=int, default=1920, help="frame width (1920)")
    p.add_argument("--height", type=int, default=1080, help="frame height (1080)")
    p.add_argument("--fps", default="30", help="frame rate, as the schema wants it")
    p.add_argument("--codec", default="h264",
                   help="output codec; the schema's list is checked")
    p.add_argument("--loudness", type=float, default=-14.0,
                   help="integrated LUFS target for the programme (default "
                        "-14, what platforms normalise to). Delivering AAC "
                        "costs about 1 dB: the engine checks the decoded "
                        "stream against the true-peak ceiling and pulls the "
                        "programme down, so a measured -13.1 here arrived at "
                        "-14.4 in one peer's file")
    p.add_argument("--true-peak", type=float, default=-1.0, dest="true_peak",
                   help="true-peak ceiling in dBTP (default -1)")
    p.add_argument("--normalize", default="integrated",
                   choices=["integrated", "dynamic", "none"],
                   help="how the master is normalised (default integrated; "
                        "dynamic needs --normalize-reason)")
    p.add_argument("--normalize-reason", default="", dest="normalize_reason",
                   help="why the master is not integrated: dynamic without a "
                        "reason falls back to integrated")
    p.add_argument("--no-limiter", action="store_true", dest="no_limiter",
                   help="leave the master's limiter off. It runs after "
                        "normalisation, so strong short peaks cost loudness "
                        "and the target is missed; raising the target cannot "
                        "fix that, quietening the effects can. Turning it off "
                        "is how you see the shortfall")
    p.add_argument("--bitrate", type=int, default=16_000_000,
                   help="video bitrate, stated explicitly because NVENC "
                        "ignores constant-quality and silently substitutes a "
                        "default (default 16 Mbps, measured good at 1080p30)")
    p.add_argument("--max-bitrate", type=int, default=24_000_000,
                   dest="max_bitrate", help="ceiling (default 24 Mbps)")
    p.add_argument("--buffer-size", type=int, default=48_000_000,
                   dest="buffer_size", help="VBV buffer (default 48 Mbit)")
    p.add_argument("--preset", default="slow",
                   help="encoder preset, if the schema has it (default slow)")
    p.add_argument("--background", default="",
                   help="background colour; default comes from styles.json")
    p.add_argument("--lang", default="",
                   help="caption language (default: the narration's)")
    p.add_argument("--burn-captions", action="store_true", dest="burn_captions",
                   help="burn the captions into the picture instead of a sidecar track "
                        "(they share the lower band with the authored type)")
    p.add_argument("--no-captions", action="store_true", dest="no_captions",
                   help="leave the caption track out")
    p.add_argument("--no-audio", action="store_true", dest="no_audio",
                   help="leave the mix out: no narration tracks, no cues")
    p.add_argument("--out", dest="scene_out", metavar="FILE",
                   help="where to write the scene (default: the project's scene.xml)")
    p.add_argument("--report", dest="report_out", metavar="FILE",
                   help="where to write build.json (default: beside the scene)")
    workspace.add_inputs(p, "script", "voice", "styles", "dossier", "vfx", "sound")
    p.add_argument("--force", action="store_true",
                   help="write the scene even when the checks found errors")
    p.add_argument("--fresh", action="store_true",
                   help="ask the editor again even if the inputs did not change")
    p.set_defaults(run=run)
    return p


def run(args) -> int:
    idea, project = workspace.resolve(args, inputs=("script",))
    script_file = project.source("script", workspace.override(args, "script"))
    if not script_file:
        print("error: no screenplay; run `yt script` first, or pass --script FILE",
              file=sys.stderr)
        return 1
    sections, _ = load_script(script_file)

    codecs = enum_of("output", "codec")
    if codecs and args.codec not in codecs:
        print(f"error: --codec {args.codec!r} is not in the schema's list: "
              f"{', '.join(codecs)}", file=sys.stderr)
        return 1

    # The schema permits what delivery does not: truePeak goes up to +24 and
    # loudness up to 0, so a positive ceiling or a programme at -3 LUFS passes
    # validation and clips in the file. The sane band is this layer's job.
    if args.true_peak > 0:
        print(f"warning: --true-peak {args.true_peak} is above full scale. The "
              "schema allows it; delivery will clip. -1 dBTP is the usual "
              "ceiling.", file=sys.stderr)
    if args.loudness > -6:
        print(f"warning: --loudness {args.loudness} is far hotter than any "
              "platform target (-14 LUFS); the limiter will fight it and the "
              "programme will sound squashed.", file=sys.stderr)

    voice = read_json(project.source("voice", workspace.override(args, "voice")))
    styles = read_json(project.source("styles", workspace.override(args, "styles")))
    dossier = read_json(project.source("dossier", workspace.override(args, "dossier")))
    vfx = read_json(project.source("vfx", workspace.override(args, "vfx")))
    sound = read_json(project.source("sound", workspace.override(args, "sound")))
    if not voice and not args.quiet:
        print("note: no narration recorded yet, so the timeline is the "
              "screenplay's estimate. Run `yt voice` for the real one.",
              file=sys.stderr)

    builder = SceneBuilder(
        idea, sections, project.root, voice=voice, styles=styles,
        dossier=dossier, vfx=vfx, sound=sound,
        width=args.width, height=args.height, fps=args.fps,
        codec=args.codec, background=args.background, lang=args.lang,
        captions=not args.no_captions, burn_captions=args.burn_captions,
        audio=not args.no_audio,
        loudness=args.loudness, true_peak=args.true_peak,
        normalize=args.normalize, normalize_reason=args.normalize_reason,
        limiter=not args.no_limiter,
        bitrate=args.bitrate,
        max_bitrate=args.max_bitrate,
        buffer_size=args.buffer_size, preset=args.preset,
        verbose=not args.quiet)

    # The edit is the model's: what covers each gap, where captions stay out,
    # how long the film holds. The builder only applies it.
    rules, digest = house.for_args(args, project, "build")
    engine = Harness(project.root, "build", model=args.model, role=ROLE,
                     house=rules, house_digest=digest,
                     fresh=getattr(args, "fresh", False), verbose=not args.quiet)
    try:
        builder.edit = engine.decide("edit", edit_prompt(builder.brief()), EDIT_SCHEMA)
        chosen = clean_look(engine.decide(
            "look", look_prompt(builder.brief()), LOOK_SCHEMA))
        builder.look, check = check_look(builder, engine, chosen)
        data, manifest = builder.build()
        manifest["look_check"] = check
        if chosen and not builder.look:
            manifest["look_declined"] = chosen
    except (HarnessError, Exception) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    manifest["engine"] = engine.report()

    print_build(manifest)
    errors = [f for f in manifest["findings"] if f.startswith("error")]
    if errors and not args.force:
        print(f"\nerror: {len(errors)} check error(s); the scene was not "
              "written. Fix them, or pass --force to write it anyway.",
              file=sys.stderr)
        return 1

    target = Path(args.scene_out or project.out("scene"))
    target.write_bytes(data)
    report = Path(args.report_out or (
        target.with_name("build.json") if (args.scene_out or project.standalone)
        else project.out("build")))
    report.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    print(f"wrote {target}", file=sys.stderr)
    print(f"wrote {report}", file=sys.stderr)
    if errors:
        print(f"warning: written with {len(errors)} check error(s); the engine "
              "will refuse it", file=sys.stderr)
    return 0
