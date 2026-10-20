#!/usr/bin/env python3
"""Fetch CC0 3D props for one video: models, HDRIs and textures from Poly Haven.

A 3D film built from primitives alone is a blockout: boxes for furniture,
extruded triangles, emissive planes for light sources. This stage supplies what the schema
can place but no stage could provide -- a glTF model with its textures, an
HDRI to light a set, PBR texture maps for a surface -- so `yt vfx` can author
against real assets and list them in its brief.

    yt props --title "..." "<a set piece>" "<a lighting setup> hdri" "<a surface material>"
    yt props --title "..."                # phrases derived from script + styles
    yt props --title "..." --kinds model,hdri --limit 2 --redownload

Everything on Poly Haven (https://polyhaven.com) is CC0, so there is no
licence to check and no key to load. The catalogue listing is cached beside
the work directory for a day; `--fresh` re-reads it.

What lands in the project:

    props/<kind>/<id>/...          the files, exactly as the host served them
    props.json                     {"items": [{"id", "kind", "file", "files",
                                   "licence", "credit", "about", "source_url"}]}

Downloaded files are untrusted bytes from the open web: they are written to
disk and never opened, parsed or executed by this script. The search phrases
come from the operator, or (derived runs) from one harness decision that reads
the project's own script and styles against the catalogue's vocabulary; a second
decision keeps only the candidates that fit, and "none fit" is a valid answer.
The tooling itself knows no subject.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

from . import house, workspace
from .core import read_json, write_json
from .harness import Harness, HarnessError

API = "https://api.polyhaven.com"
SITE = "https://polyhaven.com/a/"
DEFAULT_USER_AGENT = "yt-props/1.0 (3D props for a scene-render project; +local script)"

KINDS = ("model", "hdri", "texture")
#: The catalogue's own name for each kind, as `/assets?t=` wants it.
LISTING_TYPES = {"model": "models", "hdri": "hdris", "texture": "textures"}
#: Enough for a 1080p frame; the 4k and 8k variants run to hundreds of MB.
RESOLUTION = "2k"
#: The maps a PBR material is built from, most important first.
TEXTURE_MAPS = ("Diffuse", "nor_gl", "Rough", "AO", "arm", "Displacement")
TEXTURE_FORMATS = ("jpg", "png")
HDRI_FORMATS = ("hdr", "exr")
LICENCE = "CC0 1.0 (Poly Haven)"
MAX_FILE_BYTES = 96 * 1024 * 1024
CATALOGUE_TTL = 24 * 3600

PROPS_DIR = "props"
CACHE_DIR = ".props-cache"

STOPWORDS = {
    "the", "and", "with", "from", "that", "this", "into", "for", "of", "a", "an",
    "in", "on", "at", "to", "by", "is", "are", "be", "as", "or", "its", "it",
    "made", "make", "built", "build", "shot", "shots", "scene", "frame", "frames",
    "model", "models", "hdri", "hdris", "texture", "textures", "asset", "assets",
    "prop", "props", "reference", "only", "not", "no", "one", "two", "3d", "2d",
    "same", "then", "than", "over", "under", "through", "behind", "where", "when",
    "very", "more", "most", "just", "like", "about", "still", "also", "all",
    "any", "each", "per", "via", "some", "out", "up", "down", "off",
}


# --------------------------------------------------------------------------- #
# http seams -- the tests replace these two names, nothing else touches the net
# --------------------------------------------------------------------------- #

def fetch_json(url: str, *, user_agent: str = DEFAULT_USER_AGENT,
               timeout: float = 30.0) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent,
                                                   "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def fetch_bytes(url: str, *, user_agent: str = DEFAULT_USER_AGENT,
                timeout: float = 600.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(MAX_FILE_BYTES + 1)
    if len(body) > MAX_FILE_BYTES:
        raise ValueError(f"larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    return body


# --------------------------------------------------------------------------- #
# the catalogue
# --------------------------------------------------------------------------- #

def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) > 1 and w not in STOPWORDS]


def _stem(word: str) -> str:
    """Just enough to let 'wooden' find 'wood' and 'planes' find 'plane'."""
    for suffix in ("ies", "ing", "ed", "en", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[:-len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", text).strip("._") or "asset"


def _safe_relpath(rel: str) -> str | None:
    """A path from the catalogue, kept only if it stays under its directory."""
    parts = PurePosixPath(rel).parts
    if not parts or rel.startswith("/") or any(p in ("..", ".", "") for p in parts):
        return None
    return "/".join(_safe_name(p) for p in parts)


def _basename(url: str) -> str:
    return _safe_name(urllib.parse.unquote(PurePosixPath(urllib.parse.urlparse(url).path).name))


class Catalogue:
    """Poly Haven's listings and per-asset file maps, cached on disk.

    The listing of each kind is a few hundred KB and changes slowly, so it is
    kept for a day; an asset's file map is kept until `fresh` says otherwise.
    """

    def __init__(self, cache_dir: Path, *, fresh: bool = False,
                 user_agent: str = DEFAULT_USER_AGENT, ttl: float = CATALOGUE_TTL) -> None:
        self.dir = Path(cache_dir)
        self.fresh = fresh
        self.user_agent = user_agent
        self.ttl = ttl
        self._listings: dict[str, dict] = {}
        self._files: dict[str, dict] = {}

    def _cached(self, name: str, url: str, ttl: float | None) -> dict:
        path = self.dir / f"{name}.json"
        if not self.fresh and path.is_file() and (
                ttl is None or time.time() - path.stat().st_mtime < ttl):
            data = read_json(path, warn=False)
            if data is not None:
                return data
        data = fetch_json(url, user_agent=self.user_agent)
        if not isinstance(data, dict):
            raise ValueError(f"{url} did not return an object")
        self.dir.mkdir(parents=True, exist_ok=True)
        write_json(path, data)
        return data

    def listing(self, kind: str) -> dict[str, dict]:
        if kind not in self._listings:
            t = LISTING_TYPES[kind]
            self._listings[kind] = self._cached(t, f"{API}/assets?t={t}", self.ttl)
        return self._listings[kind]

    def files(self, asset_id: str) -> dict:
        if asset_id not in self._files:
            self._files[asset_id] = self._cached(
                f"files-{_safe_name(asset_id)}", f"{API}/files/{urllib.parse.quote(asset_id)}",
                None)
        return self._files[asset_id]

    def vocabulary(self, kinds: tuple[str, ...]) -> set[str]:
        """Every tag, category and name word the listings use, lower-cased."""
        words: set[str] = set()
        for kind in kinds:
            for asset_id, meta in self.listing(kind).items():
                if not isinstance(meta, dict):
                    continue
                for tag in list(meta.get("tags") or []) + list(meta.get("categories") or []):
                    words.add(re.sub(r"[^a-z0-9]+", " ", str(tag).lower()).strip())
                words.update(_words(str(meta.get("name") or "")))
        return {w for w in words if len(w) >= 3 and not w.isdigit()
                and w not in STOPWORDS}


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #

def terms(phrase: str) -> list[str]:
    return list(dict.fromkeys(_words(phrase)))


def _hits(asset_id: str, meta: dict, phrase: str) -> list[tuple[str, float]]:
    """Each term of the phrase with the weight of where it was found: the
    name, then the tags, then the categories, then the description; zero
    when nowhere."""
    if not isinstance(meta, dict):
        return []
    name = str(meta.get("name") or asset_id)
    tiers = (
        (3.0, {_stem(w) for w in _words(name) + _words(asset_id.replace("_", " "))}),
        (2.0, {_stem(w) for t in meta.get("tags") or [] for w in _words(str(t))}),
        (1.5, {_stem(w) for c in meta.get("categories") or [] for w in _words(str(c))}),
        (0.5, {_stem(w) for w in _words(str(meta.get("description") or ""))}),
    )
    out = []
    for term in terms(phrase):
        stem = _stem(term)
        out.append((term, next((weight for weight, words in tiers if stem in words), 0.0)))
    return out


def matched_terms(asset_id: str, meta: dict, phrase: str) -> list[str]:
    return [term for term, weight in _hits(asset_id, meta, phrase) if weight]


def score(asset_id: str, meta: dict, phrase: str) -> float:
    """How well one listing entry answers a phrase: the sum of where its
    terms were found, plus extra for the whole phrase in the name or as a
    tag and for every term matched. Zero means no match."""
    hits = _hits(asset_id, meta, phrase)
    matched = [term for term, weight in hits if weight]
    if not matched:
        return 0.0
    total = sum(weight for _, weight in hits)
    whole = " ".join(term for term, _ in hits)
    tags = [str(t).lower() for t in meta.get("tags") or []]
    if whole in str(meta.get("name") or asset_id).lower() or whole in tags:
        total += 4.0
    if len(matched) == len(hits) > 1:
        total += 2.0
    return total


def rank(listing: dict[str, dict], phrase: str, *, limit: int) -> list[dict]:
    """The best `limit` entries for a phrase, best first, matches only."""
    hits = []
    for asset_id, meta in listing.items():
        points = score(asset_id, meta, phrase)
        if points > 0:
            hits.append({"id": asset_id, "meta": meta, "score": points})
    hits.sort(key=lambda h: (-h["score"], h["id"]))
    return hits[:max(0, limit)]


# --------------------------------------------------------------------------- #
# files
# --------------------------------------------------------------------------- #

def _pixels(res: str) -> int:
    match = re.match(r"(\d+)k", str(res))
    return int(match.group(1)) if match else 0


def _nearest(available: dict, wanted: str = RESOLUTION) -> str | None:
    """`wanted` if offered, else the largest below it, else the smallest above."""
    keys = [k for k in available if _pixels(k)]
    if not keys:
        return None
    if wanted in keys:
        return wanted
    below = [k for k in keys if _pixels(k) < _pixels(wanted)]
    return max(below, key=_pixels) if below else min(keys, key=_pixels)


def _entry(entry: dict, path: str) -> dict:
    return {"path": path, "url": entry["url"], "size": int(entry.get("size") or 0),
            "md5": str(entry.get("md5") or "")}


def pick_files(kind: str, files: dict, resolution: str = RESOLUTION
               ) -> tuple[str, list[dict]]:
    """What to download for one asset: the main file's name (relative to the
    asset's directory) and every file, main first. ("", []) when the host
    offers nothing usable in the format this pipeline takes."""
    out: list[dict] = []
    if kind == "model":
        variants = files.get("gltf") or {}
        res = _nearest(variants, resolution)
        entry = (variants.get(res) or {}).get("gltf") if res else None
        if not isinstance(entry, dict) or not entry.get("url"):
            return "", []
        main = _basename(entry["url"])
        out.append(_entry(entry, main))
        for rel, inc in (entry.get("include") or {}).items():
            safe = _safe_relpath(str(rel))
            if safe and isinstance(inc, dict) and inc.get("url"):
                out.append(_entry(inc, safe))
        return main, out
    if kind == "hdri":
        variants = files.get("hdri") or {}
        res = _nearest(variants, resolution)
        for fmt in HDRI_FORMATS if res else ():
            entry = (variants.get(res) or {}).get(fmt)
            if isinstance(entry, dict) and entry.get("url"):
                main = _basename(entry["url"])
                return main, [_entry(entry, main)]
        return "", []
    if kind == "texture":
        for name in TEXTURE_MAPS:
            variants = files.get(name) or {}
            res = _nearest(variants, resolution)
            if not res:
                continue
            for fmt in TEXTURE_FORMATS:
                entry = (variants.get(res) or {}).get(fmt)
                if isinstance(entry, dict) and entry.get("url"):
                    out.append(_entry(entry, _basename(entry["url"])))
                    break
        return (out[0]["path"] if out else ""), out
    raise ValueError(f"unknown kind {kind!r}")


def download(root: Path, kind: str, asset_id: str, files: list[dict], *,
             redownload: bool = False, user_agent: str = DEFAULT_USER_AGENT,
             say=lambda m: None) -> list[str]:
    """Write each file under props/<kind>/<id>/, as served, never opened.
    A file already on disk is kept unless `redownload`."""
    base = PurePosixPath(PROPS_DIR) / kind / _safe_name(asset_id)
    written: list[str] = []
    for entry in files:
        rel = base / entry["path"]
        target = root / Path(*rel.parts)
        if target.is_file() and not redownload:
            written.append(rel.as_posix())
            continue
        body = fetch_bytes(entry["url"], user_agent=user_agent)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        say(f"    + {rel.as_posix()} ({len(body) // 1024} KB)")
        written.append(rel.as_posix())
    return written


# --------------------------------------------------------------------------- #
# the stage
# --------------------------------------------------------------------------- #

def item_record(hit: dict, main: str, written: list[str]) -> dict:
    """The shape props.json carries, and the vfx brief lists."""
    meta, asset_id, kind = hit["meta"], hit["id"], hit["kind"]
    name = str(meta.get("name") or asset_id)
    authors = ", ".join(str(a) for a in (meta.get("authors") or {})) or "Poly Haven"
    about = name
    if meta.get("description"):
        about += f" -- {str(meta['description'])[:200]}"
    # A look-alike found on one word of the phrase must say so: the catalogue
    # has no entry for everything a script asks for, and the author who
    # reads this list decides whether a stand-in serves the shot.
    phrase = str(hit.get("phrase", ""))
    wanted, got = terms(phrase), matched_terms(asset_id, meta, phrase)
    if wanted and len(got) < len(wanted):
        about += f" (matches only {', '.join(repr(t) for t in got)} of {phrase!r})"
    tags = [str(t) for t in (meta.get("tags") or [])][:12]
    return {
        "id": asset_id,
        "kind": kind,
        "name": name,
        "file": f"{PROPS_DIR}/{kind}/{_safe_name(asset_id)}/{main}",
        "files": written,
        "licence": LICENCE,
        "credit": f"{name} by {authors} / CC0 (Poly Haven)",
        "about": about,
        "source_url": SITE + asset_id,
        "tags": tags,
        "query": hit.get("phrase", ""),
        "match": hit["score"],
    }


def gather(root: Path, phrases: list[str], *, kinds: tuple[str, ...] = KINDS,
           limit: int = 3, catalogue: Catalogue, redownload: bool = False,
           user_agent: str = DEFAULT_USER_AGENT, verbose: bool = True,
           judge=None) -> list[dict]:
    """Search every phrase in every kind, fetch the best `limit` of each, and
    return the manifest items. An asset found by two phrases is listed once,
    under the phrase that matched it best. `judge`, when given, sees the
    candidates before anything is fetched and returns the ones to keep (with a
    `fit` note); the rest are never listed, downloaded or uploaded."""
    say = (lambda m: print(m, file=sys.stderr)) if verbose else (lambda m: None)
    best: dict[str, dict] = {}
    for phrase in phrases:
        for kind in kinds:
            hits = rank(catalogue.listing(kind), phrase, limit=limit)
            say(f"  {kind:<7} {phrase!r}: " + (", ".join(h["id"] for h in hits) or "nothing"))
            for hit in hits:
                have = best.get(hit["id"])
                if have is None or hit["score"] > have["score"]:
                    best[hit["id"]] = hit | {"kind": kind, "phrase": phrase}

    items: list[dict] = []
    order = {p: n for n, p in enumerate(phrases)}
    candidates = sorted(best.values(),
                        key=lambda h: (order[h["phrase"]], -h["score"], h["id"]))
    if judge is not None:
        candidates = judge(candidates) if candidates else []
        say(f"  kept {len(candidates)} of {len(best)} candidate(s)")
    for hit in candidates:
        try:
            files = catalogue.files(hit["id"])
        except (urllib.error.URLError, OSError, ValueError) as exc:
            say(f"    x {hit['id']}: file list unavailable ({exc})")
            continue
        main, picked = pick_files(hit["kind"], files)
        if not main:
            say(f"    - {hit['id']}: no {RESOLUTION} download in a format this pipeline takes")
            continue
        try:
            written = download(root, hit["kind"], hit["id"], picked,
                               redownload=redownload, user_agent=user_agent, say=say)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            say(f"    x {hit['id']}: download failed ({exc})")
            continue
        record = item_record(hit, main, written)
        if hit.get("fit"):
            record["fit"] = hit["fit"]
        items.append(record)
    return items


def project_text(root: Path) -> str:
    """What the project already says it needs on screen: the script's asset
    and visual fields, the styles' shots, and the premise."""
    bits: list[str] = []
    idea = read_json(root / workspace.ARTIFACTS["idea"], warn=False) or {}
    bits.append(str(idea.get("description") or ""))
    script = read_json(root / workspace.ARTIFACTS["script"], warn=False) or {}
    for section in script.get("sections") or []:
        for shot in (section.get("shots") or []) if isinstance(section, dict) else []:
            if isinstance(shot, dict):
                bits += [str(shot.get("asset") or ""), str(shot.get("visual") or "")]
    styles = read_json(root / workspace.ARTIFACTS["styles"], warn=False) or {}
    for style in styles.get("styles") or []:
        if isinstance(style, dict):
            bits += [str(s) for s in style.get("shots") or []]
            bits += [str(style.get("shows_well") or ""), str(style.get("palette") or "")]
    return "\n".join(b for b in bits if b)


def derive_phrases(text: str, vocabulary: set[str], limit: int = 8) -> list[str]:
    """The catalogue terms the project's own words contain, most useful first:
    multi-word tags, then by how often the text says them."""
    if not text.strip():
        return []
    haystack = " " + re.sub(r"[^a-z0-9]+", " ", text.lower()) + " "
    counts: dict[str, int] = {}
    for term in vocabulary:
        clean = re.sub(r"[^a-z0-9]+", " ", term.lower()).strip()
        if len(clean) < 3 or clean in STOPWORDS:
            continue
        n = haystack.count(f" {clean} ")
        if n:
            counts[clean] = n
    ranked = sorted(counts, key=lambda t: (-(" " in t), -counts[t], -len(t), t))
    return ranked[:limit]


ROLE = (
    "You are the set dresser for this film. You choose which free 3D props, "
    "HDRIs and textures from a fixed catalogue the animation authors will "
    "build with. Every asset you keep is downloaded, uploaded to the render "
    "hosts and weighed by every author, so a look-alike that merely shares a "
    "word with the script costs them time and misleads them. You judge fit "
    "against what the script and styles actually put on screen, and 'nothing "
    "fits' is an honest, welcome answer."
)

PHRASES_SCHEMA = {
    "type": "object",
    "properties": {"phrases": {"type": "array", "items": {"type": "string"}}},
    "required": ["phrases"],
}

KEEP_SCHEMA = {
    "type": "object",
    "properties": {"keep": {"type": "array", "items": {
        "type": "object",
        "properties": {"n": {"type": "integer"}, "as": {"type": "string"}},
        "required": ["n", "as"]}}},
    "required": ["keep"],
}

#: How many catalogue terms the phrase question may carry.
VOCABULARY_CAP = 4000


def decide_phrases(h: Harness, text: str, vocabulary: set[str], limit: int = 8) -> list[str]:
    """The search phrases the model picks for this project: plain words from
    the catalogue's vocabulary naming things the film shows. Empty when the
    catalogue holds nothing the film needs."""
    if not text.strip():
        return []
    words = sorted(w for w in vocabulary if w)[:VOCABULARY_CAP]
    answer = h.decide(
        "phrases",
        f"What the project says it puts on screen:\n{text}\n\n"
        "The free catalogue is searched by these tag, category and name "
        f"words:\n{', '.join(words)}\n\n"
        f"Pick at most {limit} search phrases of one to three plain words "
        "each, for set pieces, HDRIs or materials this film genuinely shows "
        "and the catalogue could serve. Do not search for a word only because "
        "it appears in the text; a word with a different sense or an object "
        "the film never shows is a wrong search. Return an empty list when "
        "the catalogue cannot serve this subject.",
        PHRASES_SCHEMA)
    phrases = [re.sub(r"\s+", " ", str(p)).strip() for p in answer.get("phrases") or []]
    return list(dict.fromkeys(p for p in phrases if p))[:limit]


def vet_candidates(h: Harness, text: str, hits: list[dict]) -> list[dict]:
    """The candidates the model keeps, each with its `fit` note. Keyword
    search finds look-alikes; none kept is a valid verdict."""
    if not hits:
        return []
    listing = "\n".join(
        f"{n}. [{hit['kind']}] {hit['meta'].get('name') or hit['id']}"
        f" -- tags: {', '.join(str(t) for t in (hit['meta'].get('tags') or [])[:12])}"
        f"; {str(hit['meta'].get('description') or '')[:160]}"
        f"\n     found for: {hit['phrase']}"
        for n, hit in enumerate(hits, 1))
    verdict = h.decide(
        "keep",
        f"What the project says it puts on screen:\n{text}\n\n"
        f"Candidates the catalogue returned:\n\n{listing}\n\n"
        "Keep only the candidates an author would genuinely build this film "
        "with. Reject look-alikes that share a word with the script but are "
        "not what any shot shows. A loose but honest stand-in is acceptable "
        "only if you say so in `as`. Keep none rather than something "
        "misleading.",
        KEEP_SCHEMA)
    kept, seen = [], set()
    for row in verdict.get("keep") or []:
        try:
            n = int(row.get("n", 0))
        except (TypeError, ValueError):
            continue
        if 1 <= n <= len(hits) and n not in seen:
            seen.add(n)
            kept.append(hits[n - 1] | {"fit": str(row.get("as") or "")})
    return kept


def write_manifest(root: Path, items: list[dict], *, phrases: list[str]) -> Path:
    return write_json(root / workspace.ARTIFACTS["props"], {
        "source": "Poly Haven (https://polyhaven.com), everything CC0",
        "phrases": phrases,
        "resolution": RESOLUTION,
        "count": len(items),
        "items": items,
    })


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "props", parents=list(parents),
        help="fetch CC0 3D props (models, HDRIs, textures) for a video from Poly Haven",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("phrase", nargs="*",
                   help="what to look for: phrases naming set pieces, HDRIs or materials; default: "
                        "derived from the script's assets and the styles")
    # --project comes from the shared `work` parent; --title is this stage's own.
    p.add_argument("--title", help="a project on disk, or an idea title")
    p.add_argument("--limit", type=int, default=3,
                   help="assets per phrase and kind (default 3)")
    p.add_argument("--kinds", default=",".join(KINDS),
                   help=f"comma-separated: {', '.join(KINDS)} (default all)")
    p.add_argument("--redownload", action="store_true",
                   help="fetch files already on disk again")
    p.add_argument("--fresh", action="store_true",
                   help="re-read the catalogue instead of the day-old cache, and ask the "
                        "model again")
    p.add_argument("--model", default="sonnet",
                   help="model that picks the phrases and keeps the fitting results "
                        "when the phrases are derived (default sonnet)")
    p.add_argument("--user-agent", default=DEFAULT_USER_AGENT,
                   help="User-Agent for the requests")
    p.set_defaults(run=run, source=None, index=None, stdin=None, genre=None,
                   description=None)
    return p


def run(args) -> int:
    kinds = tuple(k.strip() for k in args.kinds.split(",") if k.strip())
    if bad := [k for k in kinds if k not in KINDS]:
        print(f"error: unknown kind(s) {', '.join(bad)}; pick from {', '.join(KINDS)}",
              file=sys.stderr)
        return 1
    idea, project = workspace.resolve(args)
    verbose = not args.quiet
    say = (lambda m: print(m, file=sys.stderr)) if verbose else (lambda m: None)
    catalogue = Catalogue(project.root.parent / CACHE_DIR, fresh=args.fresh,
                          user_agent=args.user_agent)

    phrases = list(dict.fromkeys(p.strip() for p in args.phrase if p.strip()))
    derived = not phrases
    text = project_text(project.root) if derived else ""
    if derived and not text.strip():
        print("error: nothing to search for: give phrases, or run `yt script` / "
              "`yt styles` first so they can be derived", file=sys.stderr)
        return 1
    judge = None
    try:
        if derived:
            rules, digest = house.for_args(args, project, "props")
            h = Harness(project.root, "props", model=args.model, role=ROLE,
                        house=rules, house_digest=digest,
                        fresh=getattr(args, "fresh", False), verbose=verbose)
            phrases = decide_phrases(h, text, catalogue.vocabulary(kinds))
            judge = lambda hits: vet_candidates(h, text, hits)  # noqa: E731
        if not phrases:
            if not derived:
                print("error: nothing to search for", file=sys.stderr)
                return 1
            say("props: the model found nothing in the catalogue that fits this project")
        say(f"props: {idea.title}\n  -> {project.root}/{PROPS_DIR}/\n  "
            f"phrases{' (derived from the project)' if derived else ''}: "
            + ", ".join(phrases))
        items = gather(project.root, phrases, kinds=kinds, limit=args.limit,
                       catalogue=catalogue, redownload=args.redownload,
                       user_agent=args.user_agent, verbose=verbose, judge=judge)
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"error: Poly Haven unreachable: {exc}", file=sys.stderr)
        return 1

    path = write_manifest(project.root, items, phrases=phrases)
    for item in items:
        print(f"{item['kind']:<8} {item['id']:<28} {item['file']}")
        print(f"         {item['about'][:100]}")
    if not items:
        print("nothing matched; try other phrases (the catalogue is searched by tag "
              "and name, two or three plain words work best)")
    print(f"\n{len(items)} prop(s) · wrote {path}", file=sys.stderr)
    return 0
