"""The channel record: how each film opened, unfolded, closed and looked.

Every check the pipeline has for "templated" or "generic" work looks inside
one film: the train, the order, the stock openers and closers. Nothing
compared a film with the channel's earlier ones, so a channel could ship
the same film shape every time without anything noticing (AUTO-137).

`work/channel.json` keeps one row per film: its opening device (the plan's
hook trigger, AUTO-136), its closing device (the plan's `ending.device`),
its section shape, its primary treatment, the palette roles its own
swatches fill, and the shipped type families its typography names. Only a
film whose qa.json says READY counts: drafts, test runs and a film being
remade are recorded with `counted: false` and never compared against. A
counted row keeps `counted_at`, when it first counted, and the earlier
films are ordered by it, so rewriting an old film's files reorders nothing.

This module decides nothing. `repeats` returns measured lines, prefixed
`channel:`; the plan review's reviewer of another family is the one that
judges whether a returning viewer would notice (script.py), and `yt
styles` only prints and saves its lines as warnings. Typography is kept
for the reader and never compared: with eleven shipped families, two films
sharing one mostly measures the catalogue.
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
from pathlib import Path
from typing import Iterable, Sequence

from . import design, fonts
from .core import _key, write_json

RECORD_FILE = "channel.json"

#: Where a plan (and script.json) holds its opening and closing devices.
#: The opening is AUTO-136's curiosity trigger; the closing is the plan's
#: `ending.device` (script.CLOSINGS). Read through these keys only, so a
#: card that moves either field changes one constant.
OPENING_KEY = ("hook", "trigger")
CLOSING_KEY = ("ending", "device")

#: How many counted earlier films a new one is compared with, newest first.
#: NO STANDARD: a house prior, unmeasured (TO VERIFY).
EARLIER_LIMIT = 12

#: How close two films' background and accent are, each, to be the same
#: palette: CIE76 Delta E in CIELAB. NO STANDARD: a house prior, unmeasured
#: (TO VERIFY); about 2.3 is a just-noticeable difference side by side, and
#: a returning viewer compares from memory, so the bar is looser.
PALETTE_DISTANCE = 12.0

#: The dimensions `repeats` measures. Typography is not one of them.
DIMENSIONS = ("opening", "closing", "structure", "treatment", "palette")

#: What the plan review is shown, and what `yt styles` warns on.
PLAN_DIMENSIONS = ("opening", "structure", "closing")
STYLE_DIMENSIONS = ("treatment", "palette")

#: qa.READY, spelled here so the record does not import the QA stage;
#: tests/test_channel.py holds the two the same.
READY = "READY TO PUBLISH"

#: The artifacts a film's row is built from; a project with neither of the
#: first two has nothing to record.
SOURCES = ("script.json", "styles.json")
QA_FILE = "qa.json"


# --------------------------------------------------------------------------- #
# reading one film
# --------------------------------------------------------------------------- #

def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _at(data: dict | None, key: tuple[str, ...]) -> str:
    value = data
    for part in key:
        value = value.get(part) if isinstance(value, dict) else None
    return " ".join(str(value).split()) if isinstance(value, str) else ""


def _collapsed(functions: Iterable) -> list[str]:
    out: list[str] = []
    for fn in functions:
        fn = str(fn or "").strip()
        if fn and (not out or out[-1] != fn):
            out.append(fn)
    return out


def shape(data: dict | None) -> dict | None:
    """A plan's or a screenplay's section shape: the section count, one
    token per section and each section's shot functions with repeats
    collapsed. A section's token is, in this order: `open` for section 1,
    `constraint` for reveal.constraint_section, `reveal` for reveal.section,
    `payoff` for the last section, `rehook` for one with a rehook, else its
    connective. `functions` is None for a plan, which has no shots yet.
    None when there are no sections."""
    sections = [s for s in (data or {}).get("sections") or [] if isinstance(s, dict)]
    if not sections:
        return None
    reveal = (data or {}).get("reveal")
    reveal = reveal if isinstance(reveal, dict) else {}
    tokens = []
    for n, spec in enumerate(sections, 1):
        tokens.append(
            "open" if n == 1
            else "constraint" if reveal.get("constraint_section") == n
            else "reveal" if reveal.get("section") == n
            else "payoff" if n == len(sections)
            else "rehook" if str(spec.get("rehook") or "").strip()
            else str(spec.get("connective") or "").strip().lower())
    drafted = any(isinstance(s.get("shots"), list) for s in sections)
    functions = ([_collapsed(shot.get("function") for shot in s.get("shots") or []
                             if isinstance(shot, dict)) for s in sections]
                 if drafted else None)
    return {"count": len(sections), "tokens": tokens, "functions": functions}


def story(data: dict | None) -> dict:
    """The opening, closing and shape of a plan or a script.json."""
    return {"opening": _at(data, OPENING_KEY), "closing": _at(data, CLOSING_KEY),
            "shape": shape(data)}


def typography_families(text: str) -> list[str]:
    """The shipped families a typography string names, as their directory
    names (`InterDisplay`, `SourceSans3`, `LiberationSans`), sorted. Words
    are matched whole, the longest run of words first, so "Inter Display"
    is InterDisplay and not Inter; a metric-compatible name (Helvetica,
    Arial, Times New Roman) is the family it resolves to, as `yt build`
    resolves it. The generic names (serif, monospace) name no family."""
    by_name: dict[str, str] = {}
    for face in fonts.faces():
        directory = face.path.parent.name
        by_name[fonts._key(face.family)] = directory
        by_name[fonts._key(directory)] = directory
    for directory, name in fonts.FAMILY_NAMES.items():
        by_name[fonts._key(name)] = directory
    for alias, family in fonts.ALIASES.items():
        if fonts._key(family) in by_name and not alias.startswith(
                ("sans", "serif", "mono", "system", "ui")):
            by_name[alias] = by_name[fonts._key(family)]
    words = re.findall(r"[A-Za-z0-9]+", str(text or ""))
    found, i = set(), 0
    while i < len(words):
        for size in range(min(4, len(words) - i), 0, -1):
            hit = by_name.get(fonts._key("".join(words[i:i + size])))
            if hit:
                found.add(hit)
                i += size
                break
        else:
            i += 1
    return sorted(found)


def look(styles: dict | None) -> dict:
    """The primary treatment's name, the palette roles its own swatches
    fill (design.Design.parsed_roles) and its typography's families."""
    rows = (styles or {}).get("styles") or []
    d = design.Design.from_styles(styles)
    return {"treatment": d.treatment if rows else "", "palette": d.parsed_roles(),
            "typography": typography_families(d.typography)}


def _root(project) -> Path:
    """A Project's directory, or a path given as one (a Path's own `root`
    attribute is its anchor, "/", so it is not read for a path)."""
    return Path(project) if isinstance(project, (str, Path)) else Path(project.root)


def fingerprint(project) -> dict:
    """One film's row, built from the files its stages already wrote:
    script.json for the story, styles.json for the look, qa.json for
    whether it counts. A file that is missing gives empty values, which
    `repeats` reads as not evidenced."""
    root = _root(project)
    script = _read(root / "script.json")
    styles = _read(root / "styles.json")
    verdict = (_read(root / QA_FILE) or {}).get("verdict")
    idea = _read(root / "idea.json") or {}
    named = lambda data: (data.get("idea") if isinstance(data.get("idea"), dict)  # noqa: E731
                          else {}).get("title")
    title = next((str(t) for t in (named(script or {}), idea.get("title"),
                                   named(styles or {})) if t), "")
    times = [p.stat().st_mtime for p in (root / name for name in (*SOURCES, QA_FILE))
             if p.is_file()]
    return {"slug": root.name, "title": title, "counted": verdict == READY,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(max(times)))
            if times else "",
            **story(script), **look(styles)}


# --------------------------------------------------------------------------- #
# the record
# --------------------------------------------------------------------------- #

def _recordable(root: Path) -> bool:
    return any((root / name).is_file() for name in SOURCES)


def _carried(fresh: dict, saved: dict | None) -> dict:
    """`fresh` with its `counted_at`: when the film first counted, carried
    from its saved row while it keeps counting, else this row's `at`. The
    earlier films are ordered by it, not by `at`, because `at` moves with
    any rewrite of the film's files (a re-run `yt qa` on an old film), and
    a reorder of the channel block changes the plan prompt's digest, so
    every film in progress would be planned again for a file touch."""
    if fresh.get("counted") is True:
        kept = (saved.get("counted_at") if isinstance(saved, dict)
                and saved.get("counted") is True else None)
        fresh["counted_at"] = str(kept or fresh.get("at") or "")
    return fresh


def _rows(work: Path) -> dict[str, dict]:
    """The record's rows by slug, each film still on disk read afresh: a
    film that became READY since its last record counts, and a project
    never recorded is filled in. A row whose project is gone is kept, and
    so is the last good row of a film whose files cannot be read now: one
    broken project never stops another film's script or styles stage."""
    work = Path(work)
    saved = (_read(work / RECORD_FILE) or {}).get("films") or []
    rows = {str(r.get("slug")): r for r in saved if isinstance(r, dict) and r.get("slug")}
    if work.is_dir():
        for root in sorted(work.iterdir()):
            if root.is_dir() and not root.name.startswith(".") and _recordable(root):
                _refresh(rows, root)
    return rows


def _refresh(rows: dict[str, dict], root: Path) -> None:
    """Read one film's row afresh into `rows`; a film whose artifacts do
    not parse as their stages write them keeps its last row, or none, with
    a warning naming it."""
    try:
        fresh = fingerprint(root)
    except Exception as exc:  # noqa: BLE001 (any malformed artifact)
        print(f"warning: channel: {root.name} cannot be read ({type(exc).__name__}: "
              f"{exc}); " + ("its last record is kept" if root.name in rows
                            else "it is left out of the record"), file=sys.stderr)
        return
    rows[root.name] = _carried(fresh, rows.get(root.name))


def _newest(row: dict) -> tuple[str, str]:
    return str(row.get("counted_at") or row.get("at") or ""), str(row.get("slug"))


def record(work, project) -> Path | None:
    """Write this film's row into work/channel.json, by slug, with every
    other film on disk refreshed. A standalone run (an input file, not a
    project) has no channel: nothing is written and None is returned."""
    if getattr(project, "standalone", False):
        return None
    rows = _rows(Path(work))
    root = _root(project)
    if _recordable(root) and root.parent.resolve() != Path(work).resolve():
        _refresh(rows, root)
    films = sorted(rows.values(), key=lambda r: (str(r.get("at") or ""), r["slug"]),
                   reverse=True)
    return write_json(Path(work) / RECORD_FILE, {"films": films})


def earlier(work, slug: str, limit: int = EARLIER_LIMIT) -> list[dict]:
    """The counted rows of the other films, the latest to count first, at
    most `limit`."""
    rows = [r for r in _rows(Path(work)).values()
            if r.get("counted") is True and r.get("slug") != slug]
    rows.sort(key=_newest, reverse=True)
    return rows[:max(0, limit)]


def for_project(work, project) -> list[dict]:
    """`earlier` for a stage's project; none for a standalone run."""
    if getattr(project, "standalone", False):
        return []
    return earlier(work, _root(project).name)


# --------------------------------------------------------------------------- #
# the repeats
# --------------------------------------------------------------------------- #

def _lab(hexcode: str) -> tuple[float, float, float]:
    """sRGB (D65) to CIELAB, by the published formulas."""
    raw = hexcode.lstrip("#")[:6]
    rgb = []
    for i in (0, 2, 4):
        c = int(raw[i:i + 2], 16) / 255
        rgb.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = rgb
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > (6 / 29) ** 3 else t / (3 * (6 / 29) ** 2) + 4 / 29

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(a: str, b: str) -> float:
    """CIE76 Delta E between two hex colours."""
    return math.dist(_lab(a), _lab(b))


_HEXLIKE = re.compile(r"^#?[0-9A-Fa-f]{6}")


def _same_shape(a: dict, b: dict) -> bool:
    if a.get("count") != b.get("count") or a.get("tokens") != b.get("tokens"):
        return False
    fa, fb = a.get("functions"), b.get("functions")
    # A plan has no shots yet: what both evidence is compared, exactly.
    return fa is None or fb is None or fa == fb


def repeats(new: dict, earlier: Sequence[dict], dimensions: Iterable[str]) -> list[str]:
    """Measured lines, `channel: ...`, one per earlier film and dimension
    where `new` is the same: the same non-empty opening or closing device,
    the exact section shape, the same treatment name (case and spacing
    aside), or a background and an accent each within PALETTE_DISTANCE.
    A dimension either side lacks is not evidenced and gives no line."""
    films = [e for e in earlier if isinstance(e, dict)]
    total = len(films)
    out: list[str] = []
    for dim in dimensions:
        if dim in ("opening", "closing"):
            mine = str(new.get(dim) or "")
            if not mine:
                continue
            sharing = [e for e in films if str(e.get(dim) or "") == mine]
            verb = "opens" if dim == "opening" else "closes"
            for e in sharing:
                out.append(f"channel: {dim}: this film {verb} on {mine}, as {e['slug']} "
                           f"did ({len(sharing)} of the last {total} counted films "
                           f"{verb[:-1]} on it)")
        elif dim == "structure":
            mine = new.get("shape")
            if not isinstance(mine, dict):
                continue
            for e in films:
                theirs = e.get("shape")
                if isinstance(theirs, dict) and _same_shape(mine, theirs):
                    out.append(f"channel: structure: the section shape "
                               f"({', '.join(mine.get('tokens') or [])}) is exactly "
                               f"{e['slug']}'s")
        elif dim == "treatment":
            mine = _key(str(new.get("treatment") or ""))
            if not mine:
                continue
            for e in films:
                if _key(str(e.get("treatment") or "")) == mine:
                    out.append(f"channel: treatment: {new.get('treatment')} is the "
                               f"treatment {e['slug']} used")
        elif dim == "palette":
            mine = new.get("palette") if isinstance(new.get("palette"), dict) else {}
            for e in films:
                theirs = e.get("palette") if isinstance(e.get("palette"), dict) else {}
                pairs = [(mine.get(role), theirs.get(role)) for role in ("background", "accent")]
                if not all(a and b and _HEXLIKE.match(a) and _HEXLIKE.match(b)
                           for a, b in pairs):
                    continue
                distances = [delta_e(a, b) for a, b in pairs]
                if all(d <= PALETTE_DISTANCE for d in distances):
                    out.append(f"channel: palette: the background and accent are within "
                               f"{PALETTE_DISTANCE:g} Delta E of {e['slug']}'s "
                               f"({distances[0]:.1f}, {distances[1]:.1f})")
    return out
