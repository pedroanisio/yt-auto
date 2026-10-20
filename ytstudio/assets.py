#!/usr/bin/env python3
"""Find genuinely usable media: real URLs, real licences, verified live.

The model is good at saying what a video needs to show and bad at remembering
file URLs, so it never supplies one here. It writes a shot list; these catalogue
APIs supply the files, with the licence the host itself reports:

    nasa             NASA Image & Video Library: stills, footage and audio,
                     public domain, the real file resolved for footage
    openverse        CC-filtered image aggregator (Flickr, museums, NASA)
    openverse-audio  the same aggregator's sound recordings
    commons          Wikimedia Commons stills, footage, audio and 3D meshes,
                     with the licence Commons itself records per file
    archive          Internet Archive stills, footage and audio
    pdb              RCSB Protein Data Bank structures (atomic coordinates,
                     CC0): geometry a 3D author builds from
    papers           open-access papers in PMC (Europe PMC search, NCBI's
                     open-data copy): the PDF, every figure with its caption
                     and any supplementary footage, under the paper's licence

Every hit is then fetched (first 2 KB) to prove the URL serves a media file
rather than a landing page -- by the bytes themselves when the host labels
every file alike -- and scored: public domain first, then how well the title
matches the query, then resolution. A shot's shortlist takes the best of each
kind first, so footage, sound, figures and structures are not crowded out by
stills. `cited_papers` turns the research's own citations into the same
records.

    yt assets "EUV plasma tin droplet" --limit 8
    yt assets "ITER tokamak assembly" --sources nasa,commons --json out.json
    yt assets "lithography cleanroom" --allow-sa --no-verify
"""

from __future__ import annotations

import argparse
import html
import http.client
import json
import re
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

#: Media hosts ask a client to say what it is and how to reach its maker;
#: Wikimedia answers every request from an agent with no contact with
#: HTTP 429, so Commons files could neither be checked nor downloaded.
DEFAULT_USER_AGENT = ("yt-research/1.1 (+https://github.com/pedroanisio/yt-auto; "
                      "research dossier for an educational video) python-urllib")

#: Licences we will use, best first. Anything NC or ND is excluded outright: a
#: monetised video is a commercial use, and a cut is a derivative work.
TIER_PUBLIC_DOMAIN, TIER_ATTRIBUTION, TIER_SHARE_ALIKE = 0, 1, 2

#: Public domain, or a steward whose own material is released as such.
_PD = re.compile(
    r"public ?domain|\bpdm\b|\bcc0\b|^pd\b|government work|no known copyright|"
    r"\bnasa\b|\busgs\b|\bnoaa\b|\bnih\b|library of congress", re.I)
#: Attribution licences, and hosts whose terms amount to one.
_BY = re.compile(
    r"^by$|cc[-_ ]?by(?![-_ ]?(nc|nd|sa))|^attribution\b|"
    r"creative commons attribution(?![-_ ]?(non|no))|"
    r"\beso\b|\besa/|\bunsplash\b|\bpexels\b|open ?access", re.I)
_SA = re.compile(r"\bby[-_ ]?sa\b|share[-_ ]?alike", re.I)
#: Vetoes, checked first: a veto wins over anything else the string says.
_VETO = re.compile(
    r"[-_ ]nc\b|\bnc[-_ ]|non-?commercial|[-_ ]nd\b|no ?derivat|"
    r"rights[- ]managed|editorial|all rights reserved|royalty[- ]free licen|"
    r"getty|shutterstock|adobe stock|\bunknown\b|not opened|non-?exclusive", re.I)

MEDIA_TYPES = ("image/", "video/", "audio/", "model/")
#: Media served under other names: Ogg sound, STL meshes, atomic coordinates.
EXTRA_TYPES = ("application/ogg", "application/sla", "chemical/")
DOCUMENT_TYPES = ("application/pdf",)
#: Labels a host gives every file alike (a bucket's binary/octet-stream, a
#: coordinates file served as text): the first bytes decide what it is.
GENERIC_TYPES = ("", "binary/octet-stream", "application/octet-stream",
                 "text/plain")
VERIFY_BYTES = 2048
#: The kinds a record can carry. A paper's figure is an `image` with a
#: `paper` field; a `structure` is coordinates to build geometry from, a
#: `3d-model` a mesh to import; an `article` is a news page the film cites.
KINDS = ("image", "video", "audio", "document", "3d-model", "structure",
         "article")


#: Ogg is a container: hosts label sound and footage alike application/ogg,
#: and only the file's extension says which it holds.
_OGG_FOOTAGE = (".ogv", ".webm")


def kind_for(mime: str, url: str = "") -> str:
    """Map a content type to an asset kind. A PDF is a document, not an image."""
    mime = (mime or "").lower()
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("application/ogg"):
        path = urllib.parse.urlparse(url or "").path.lower()
        return "video" if path.endswith(_OGG_FOOTAGE) else "audio"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("application/pdf"):
        return "document"
    if mime.startswith(("model/", "application/sla")):
        return "3d-model"
    if mime.startswith("chemical/"):
        return "structure"
    return "image"


def settle_kinds(records: list[dict]) -> list[dict]:
    """Re-derive the kind of records whose type was ambiguous (Ogg), so a
    cached search does not keep an older guess about what the file holds."""
    for row in records:
        if str(row.get("content_type") or "").lower().startswith("application/ogg"):
            row["kind"] = kind_for(row["content_type"], row.get("url", ""))
    return records


def stl_to_obj(data: bytes) -> str | None:
    """An STL mesh (binary or ASCII) as Wavefront OBJ text, or None.

    Pure parsing of the bytes, nothing executed: the engine imports OBJ and
    not STL, and Commons serves its meshes as STL.
    """
    triangles: list[tuple] = []
    if len(data) >= 84:
        count = struct.unpack_from("<I", data, 80)[0]
        if count and len(data) == 84 + 50 * count:
            for n in range(count):
                values = struct.unpack_from("<12f", data, 84 + 50 * n)
                triangles.append((values[3:6], values[6:9], values[9:12]))
    if not triangles and data.lstrip().startswith(b"solid"):
        text = data.decode("ascii", "replace")
        try:
            points = [tuple(float(v) for v in m) for m in re.findall(
                r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", text)]
        except ValueError:
            return None
        triangles = [tuple(points[i:i + 3]) for i in range(0, len(points) - 2, 3)]
    if not triangles:
        return None
    lines = ["# converted from STL by yt research"]
    for a, b, c in triangles:
        lines += [f"v {x:.6g} {y:.6g} {z:.6g}" for x, y, z in (a, b, c)]
    lines += [f"f {3 * n + 1} {3 * n + 2} {3 * n + 3}" for n in range(len(triangles))]
    return "\n".join(lines) + "\n"


def sniff(head: bytes) -> str:
    """The content type a file's first bytes declare, or "" when unknown."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"GIF8"):
        return "image/gif"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head.startswith(b"OggS"):
        return "application/ogg"
    if head.startswith(b"fLaC"):
        return "audio/flac"
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "audio/wav"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp":
        return "audio/mp4" if head[8:11] == b"M4A" else "video/mp4"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm"
    if head.startswith(b"glTF"):
        return "model/gltf-binary"
    text = head.lstrip()[:80]
    if text.startswith(b"data_"):
        return "chemical/x-mmcif"
    if text.startswith((b"HEADER", b"ATOM ", b"HETATM", b"CRYST1", b"REMARK")):
        return "chemical/x-pdb"
    if text.startswith(b"solid "):
        return "application/sla"     # an ASCII mesh; binary ones carry no magic
    return ""


def licence_verdict(licence: str, *, allow_sa: bool = False
                    ) -> tuple[str, int | None]:
    """What a licence string permits, as the whole pipeline judges it.

    Returns ("free", tier) when the file may be used, ("vetoed", None) when it
    must not be, and ("unknown", None) when the string says nothing this
    recognises -- the caller decides what an unknown is worth. This is the one
    licence policy: the catalogue search, the download gate and the dossier
    all read it, so they cannot disagree.
    """
    text = (licence or "").strip()
    if not text:
        return "unknown", None
    if _VETO.search(text):
        return "vetoed", None
    if _SA.search(text):
        return ("free", TIER_SHARE_ALIKE) if allow_sa else ("vetoed", None)
    if _PD.search(text):
        return "free", TIER_PUBLIC_DOMAIN
    if _BY.search(text):
        return "free", TIER_ATTRIBUTION
    return "unknown", None


def licence_tier(licence: str, *, allow_sa: bool = False) -> int | None:
    """Rank a licence string, or None when we must not (or cannot) use it."""
    return licence_verdict(licence, allow_sa=allow_sa)[1]


@dataclass
class AssetHit:
    title: str
    url: str
    source: str
    licence: str
    kind: str = "image"
    page_url: str = ""
    credit_line: str = ""
    width: int = 0
    height: int = 0
    bytes: int = 0
    content_type: str = ""
    query: str = ""
    use_for: str = ""
    rank: int = 99
    verified: bool = False
    note: str = ""
    paper: str = ""          # the citation, when the file comes from a paper

    @property
    def pixels(self) -> int:
        return self.width * self.height

    def record(self, *, allow_sa: bool = False) -> dict:
        """The shape `yt research` stores in a dossier."""
        tier = licence_tier(self.licence, allow_sa=allow_sa)
        row = asdict(self)
        row.pop("verified")
        # A reference with no free licence was not licensed by anyone: its
        # licence is this policy's reading, not the host's, and it must not
        # pass for a usable file downstream.
        reference = self.kind in REFERENCE_KINDS and tier is None
        return row | {
            "licence_verified": "" if reference else self.licence,
            "licence_check": "reference only: cite, do not reproduce" if reference
                             else f"from {self.source} API",
            "commercial_ok": tier is not None,
            "share_alike": tier == TIER_SHARE_ALIKE,
            "reference_only": reference,
            "resolution": f"{self.width}x{self.height}" if self.width else "",
        }


# --------------------------------------------------------------------------- #
# http helpers
# --------------------------------------------------------------------------- #

def _get_json(url: str, user_agent: str, timeout: float = 30.0):
    request = urllib.request.Request(url, headers={"User-Agent": user_agent,
                                                   "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def _post_json(url: str, body: dict, user_agent: str, timeout: float = 30.0):
    """POST a JSON query; an empty answer (HTTP 204, nothing matched) is None."""
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"User-Agent": user_agent, "Accept": "application/json",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw) if raw.strip() else None


def _get_text(url: str, user_agent: str, timeout: float = 30.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


class _Pace:
    """At most one request per `interval` seconds to one host, across threads.

    Openverse allows an anonymous client 20 requests a minute; a shot list
    searched in parallel bursts past that, and every refused search used to
    come back as "search failed" and quietly cost the shot its candidates.
    """

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self.interval
        if slot > now:
            time.sleep(slot - now)


_OPENVERSE_PACE = _Pace(60.0 / 18)
#: Checks against one media host are spaced: a burst of range requests to a
#: file server (Wikimedia's among them) is answered with HTTP 429.
_HOST_INTERVAL = 0.5
_HOST_PACES: dict[str, _Pace] = {}
_HOST_PACES_LOCK = threading.Lock()


def _host_pace(url: str) -> _Pace:
    host = urllib.parse.urlparse(url).netloc.lower()
    with _HOST_PACES_LOCK:
        return _HOST_PACES.setdefault(host, _Pace(_HOST_INTERVAL))


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text or "")).strip()


# --------------------------------------------------------------------------- #
# catalogue searches
# --------------------------------------------------------------------------- #

#: The rendition of NASA footage and sound worth fetching, best first: small
#: enough to download, large enough to cut in.
_NASA_RENDITIONS = {
    "video": ("~medium.mp4", "~small.mp4", "~orig.mp4", "~mobile.mp4"),
    "audio": ("~128k.mp3", "~orig.mp3", "~orig.wav", "~orig.m4a"),
}


def _nasa_file(item: dict, media: str, user_agent: str) -> str:
    """The real file behind a NASA video or audio item.

    The search answers with a preview still for footage; the files themselves
    are listed in the item's collection manifest.
    """
    # NASA ids carry raw spaces, which the http client refuses.
    manifest = urllib.parse.quote(item.get("href", ""), safe=":/?&=%~")
    try:
        files = _get_json(manifest, user_agent)
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
        return ""
    for suffix in _NASA_RENDITIONS[media]:
        for url in files:
            if isinstance(url, str) and url.lower().endswith(suffix):
                return re.sub(r"^http://", "https://", url.replace(" ", "%20"))
    return ""


def search_nasa(query: str, limit: int, user_agent: str) -> list[AssetHit]:
    """NASA Image & Video Library. Public domain stills, footage and audio."""
    url = ("https://images-api.nasa.gov/search?"
           + urllib.parse.urlencode({"q": query, "page_size": limit,
                                     "media_type": "image,video,audio"}))
    try:
        items = _get_json(url, user_agent)["collection"]["items"]
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        return [AssetHit("", "", "nasa", "", note=f"search failed: {exc}")]

    hits: list[AssetHit] = []
    for position, item in enumerate(items[:limit]):
        data = (item.get("data") or [{}])[0]
        links = item.get("links") or []
        media = data.get("media_type", "image")
        if media in _NASA_RENDITIONS:
            file_url = _nasa_file(item, media, user_agent)
        elif links:
            # Previews come back as ~thumb; the large rendition is the usable one.
            file_url = re.sub(r"~(thumb|small|medium|orig)\.(jpg|jpeg|png|tif)",
                              "~large.jpg", links[0].get("href", ""), flags=re.I)
        else:
            file_url = ""
        if not file_url:
            continue
        credit = ", ".join(filter(None, [data.get("center"),
                                         data.get("secondary_creator")]))
        hits.append(AssetHit(
            title=data.get("title", "")[:120],
            url=file_url,
            source="nasa",
            licence="Public domain (NASA media usage guidelines)",
            kind=media,
            page_url=f"https://images.nasa.gov/details/{data.get('nasa_id', '')}",
            credit_line=f"NASA{'/' + credit if credit else ''}",
            query=query,
            rank=position,
            note=_strip_html(data.get("description", ""))[:200],
        ))
    return hits


def _openverse(media: str, query: str, limit: int, user_agent: str, *,
               allow_sa: bool) -> list[AssetHit]:
    """One Openverse search of `media` ("images" or "audio"), paced."""
    name = "openverse" if media == "images" else "openverse-audio"
    licences = "pdm,cc0,by" + (",by-sa" if allow_sa else "")
    url = (f"https://api.openverse.org/v1/{media}/?"
           + urllib.parse.urlencode({"q": query, "license": licences,
                                     "page_size": limit}))
    _OPENVERSE_PACE.wait()
    try:
        results = _get_json(url, user_agent)["results"]
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        return [AssetHit("", "", name, "", note=f"search failed: {exc}")]

    hits = []
    for position, row in enumerate(results[:limit]):
        licence = f"CC {row.get('license', '').upper()} {row.get('license_version', '')}".strip()
        seconds = (row.get("duration") or 0) / 1000
        hits.append(AssetHit(
            title=(row.get("title") or "")[:120],
            url=row.get("url", ""),
            source=f"openverse/{row.get('source', '?')}",
            licence=licence,
            kind="image" if media == "images" else "audio",
            page_url=row.get("foreign_landing_url", "") or row.get("license_url", ""),
            credit_line=_strip_html(row.get("attribution", ""))[:200]
                        or f"{row.get('creator', 'unknown')} / {licence}",
            width=int(row.get("width") or 0),
            height=int(row.get("height") or 0),
            query=query,
            rank=position,
            note=f"{seconds:.0f} s" if seconds else "",
        ))
    return hits


def search_openverse(query: str, limit: int, user_agent: str, *,
                     allow_sa: bool = False) -> list[AssetHit]:
    """Openverse: CC-licensed images across Flickr, museums, NASA and more."""
    return _openverse("images", query, limit, user_agent, allow_sa=allow_sa)


def search_openverse_audio(query: str, limit: int, user_agent: str, *,
                           allow_sa: bool = False) -> list[AssetHit]:
    """Openverse: CC-licensed sound recordings (Freesound, Jamendo, Commons)."""
    return _openverse("audio", query, limit, user_agent, allow_sa=allow_sa)


def search_commons(query: str, limit: int, user_agent: str, *,
                   documents: bool = False) -> list[AssetHit]:
    """Wikimedia Commons file search, with the licence Commons itself reports."""
    # Without a filetype filter, full-text search returns scanned PDFs whose
    # body text mentions the query -- useless as b-roll. Sound and meshes are
    # media too.
    if "filetype:" not in query and not documents:
        query = f"{query} filetype:bitmap|drawing|video|audio|3d"
    url = ("https://commons.wikimedia.org/w/api.php?"
           + urllib.parse.urlencode({
               "action": "query", "format": "json", "generator": "search",
               "gsrnamespace": 6, "gsrsearch": query, "gsrlimit": limit,
               "prop": "imageinfo", "iiprop": "url|size|extmetadata|mime",
           }))
    try:
        pages = _get_json(url, user_agent).get("query", {}).get("pages", {})
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        return [AssetHit("", "", "commons", "", note=f"search failed: {exc}")]

    hits = []
    for position, page in enumerate(list(pages.values())[:limit]):
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        licence = _strip_html(meta.get("LicenseShortName", {}).get("value", ""))
        artist = _strip_html(meta.get("Artist", {}).get("value", ""))
        mime = info.get("mime", "")
        hits.append(AssetHit(
            title=page.get("title", "").removeprefix("File:")[:120],
            url=info.get("url", ""),
            source="commons",
            licence=licence,
            kind=kind_for(mime, info.get("url", "")),
            page_url=info.get("descriptionurl", ""),
            credit_line=f"{artist} / {licence} (Wikimedia Commons)" if artist else licence,
            width=int(info.get("width") or 0),
            height=int(info.get("height") or 0),
            content_type=mime,
            query=query,
            rank=position,
        ))
    return hits


_ARCHIVE_KINDS = {"image": "image", "movies": "video", "audio": "audio"}
_ARCHIVE_FILES = {"image": (".jpg", ".jpeg", ".png", ".tif"),
                  "video": (".mp4", ".webm", ".ogv"),
                  "audio": (".flac", ".wav", ".ogg", ".mp3")}


def search_archive(query: str, limit: int, user_agent: str, *,
                   allow_sa: bool = False) -> list[AssetHit]:
    """Internet Archive stills, footage and sound, kept only when the licence
    is clear."""
    search = ("https://archive.org/advancedsearch.php?"
              + urllib.parse.urlencode({
                  "q": f'{query} AND (mediatype:image OR mediatype:movies '
                       'OR mediatype:audio)',
                  "rows": limit, "output": "json"})
              + "&fl%5B%5D=identifier&fl%5B%5D=title&fl%5B%5D=licenseurl"
                "&fl%5B%5D=mediatype&fl%5B%5D=creator&fl%5B%5D=rights")
    try:
        docs = _get_json(search, user_agent)["response"]["docs"]
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        return [AssetHit("", "", "archive", "", note=f"search failed: {exc}")]

    hits = []
    for position, doc in enumerate(docs):
        licence = doc.get("licenseurl") or doc.get("rights") or ""
        if licence_tier(licence, allow_sa=allow_sa) is None:
            continue    # no usable licence stated; skip rather than guess
        try:
            meta = _get_json(f"https://archive.org/metadata/{doc['identifier']}",
                             user_agent)
        except (urllib.error.URLError, OSError, ValueError):
            continue
        kind = _ARCHIVE_KINDS.get(doc.get("mediatype"), "image")
        files = [f for f in meta.get("files", [])
                 if f.get("name", "").lower().endswith(_ARCHIVE_FILES[kind])]
        if not files:
            continue
        best = max(files, key=lambda f: int(f.get("size") or 0))
        hits.append(AssetHit(
            title=str(doc.get("title", ""))[:120],
            url=f"https://archive.org/download/{doc['identifier']}/"
                + urllib.parse.quote(best["name"]),
            source="archive",
            licence=licence,
            kind=kind,
            page_url=f"https://archive.org/details/{doc['identifier']}",
            credit_line=f"{doc.get('creator', 'Internet Archive')} / {licence}",
            bytes=int(best.get("size") or 0),
            query=query,
            rank=position,
        ))
    return hits


#: Entries opened per structure search: each costs a record lookup.
PDB_PER_QUERY = 3


def search_pdb(query: str, limit: int, user_agent: str) -> list[AssetHit]:
    """RCSB PDB entries: atomic coordinates (mmCIF) under the archive's CC0.

    A structure is not a picture: it is the geometry a 3D author builds a
    molecule or assembly from, so its record says what it holds (title, the
    paper that deposited it, method, resolution, atom count).
    """
    body = {"query": {"type": "terminal", "service": "full_text",
                      "parameters": {"value": query}},
            "return_type": "entry",
            "request_options": {"paginate": {"start": 0,
                                             "rows": min(limit, PDB_PER_QUERY)}}}
    try:
        found = _post_json("https://search.rcsb.org/rcsbsearch/v2/query",
                           body, user_agent) or {}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return [AssetHit("", "", "pdb", "", note=f"search failed: {exc}")]

    hits = []
    for position, row in enumerate((found.get("result_set") or [])[:PDB_PER_QUERY]):
        entry = str(row.get("identifier", "")).upper()
        if not re.fullmatch(r"[0-9A-Z]{4}", entry):
            continue
        try:
            info = _get_json(f"https://data.rcsb.org/rest/v1/core/entry/{entry}",
                             user_agent)
        except (urllib.error.URLError, OSError, ValueError):
            continue
        cite = info.get("rcsb_primary_citation") or {}
        authors = cite.get("rcsb_authors") or []
        who = (authors[0].split(",")[0] + (" et al." if len(authors) > 1 else "")
               if authors else "")
        stats = info.get("rcsb_entry_info") or {}
        method = ((info.get("exptl") or [{}])[0].get("method") or "").lower()
        resolution = (stats.get("resolution_combined") or [None])[0]
        hits.append(AssetHit(
            title=f"PDB {entry}: {(info.get('struct') or {}).get('title', '')}"[:160],
            url=f"https://files.rcsb.org/download/{entry}.cif",
            source="pdb",
            licence="CC0 1.0 (RCSB PDB data policy)",
            kind="structure",
            page_url=f"https://www.rcsb.org/structure/{entry}",
            credit_line=f"PDB {entry}"
                        + (f", {who} ({cite.get('year')})" if who else "")
                        + "; RCSB PDB, CC0",
            query=query,
            rank=position,
            note=", ".join(filter(None, [
                method,
                f"{resolution} Å" if resolution else "",
                f"{stats.get('deposited_atom_count')} atoms"
                if stats.get("deposited_atom_count") else ""])),
        ))
    return hits


_PMC_BUCKET = "https://pmc-oa-opendata.s3.amazonaws.com"
#: A paper's figures offered per article; the rest of a long paper is noise.
FIGURES_PER_PAPER = 4
#: Articles opened per paper search: each costs a listing, a metadata record
#: and the article's XML for the captions.
PAPERS_PER_QUERY = 2
_PAPER_MEDIA = {".jpg": "image", ".jpeg": "image", ".png": "image",
                ".gif": "image", ".tif": "image", ".tiff": "image",
                ".mp4": "video", ".mov": "video", ".avi": "video",
                ".webm": "video", ".m4v": "video", ".mp3": "audio",
                ".wav": "audio", ".m4a": "audio", ".ogg": "audio"}


def _s3_https(url: str) -> str:
    """s3://bucket/key?md5=... as the bucket's public https URL."""
    match = re.match(r"s3://([^/]+)/([^?]+)", url or "")
    return (f"https://{match.group(1)}.s3.amazonaws.com/"
            + urllib.parse.quote(match.group(2))) if match else ""


def _stem(name: str) -> str:
    base = name.rsplit("/", 1)[-1].lower()
    return base.rsplit(".", 1)[0] if "." in base else base


def _figure_captions(xml: str) -> dict[str, str]:
    """Each figure's and media item's file stem -> "Figure 2: caption"."""
    out: dict[str, str] = {}
    for match in re.finditer(
            r"<(fig|supplementary-material|media)\b(.*?)</\1>", xml, re.S):
        body = match.group(2)
        label = re.search(r"<label>(.*?)</label>", body, re.S)
        caption = re.search(r"<caption>(.*?)</caption>", body, re.S)
        text = ": ".join(filter(None, [
            html.unescape(_strip_html(label.group(1))) if label else "",
            html.unescape(_strip_html(caption.group(1))) if caption else ""]))
        for href in re.findall(r'xlink:href="([^"]+)"', match.group(0)):
            out.setdefault(_stem(href), text)
    return out


def _pmc_metadata(pmcid: str, user_agent: str) -> dict | None:
    """NCBI's open-data record of one PMC article, latest version."""
    listing = _get_text(f"{_PMC_BUCKET}/?list-type=2&max-keys=20&prefix="
                        f"metadata/{pmcid}.", user_agent)
    keys = re.findall(rf"<Key>(metadata/{pmcid}\.(\d+)\.json)</Key>", listing)
    if not keys:
        return None
    return _get_json(f"{_PMC_BUCKET}/{max(keys, key=lambda k: int(k[1]))[0]}",
                     user_agent)


def pmc_article(pmcid: str, user_agent: str = DEFAULT_USER_AGENT, *,
                allow_sa: bool = False, query: str = "", rank: int = 0,
                figures: int = FIGURES_PER_PAPER) -> list[AssetHit]:
    """One open-access paper as assets: its PDF, its figures with their
    captions, and any footage or sound it ships, under the paper's licence.

    The licence is NCBI's own record of the article, not the search's, and a
    retracted or non-open-access article yields nothing.
    """
    try:
        meta = _pmc_metadata(pmcid, user_agent)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return [AssetHit("", "", "papers", "", note=f"lookup failed: {exc}")]
    if not meta or meta.get("is_retracted") or not meta.get("is_pmc_openaccess"):
        return []
    licence = str(meta.get("license_code") or "")
    if licence_tier(licence, allow_sa=allow_sa) is None:
        return []
    title = html.unescape(_strip_html(meta.get("title") or ""))
    citation = str(meta.get("citation") or "")
    paper = f"{title} ({citation})" if citation else title
    common = dict(source="papers", licence=licence, query=query, rank=rank,
                  page_url=f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/",
                  credit_line=f"{citation} {licence}, via PubMed Central".strip(),
                  paper=paper)
    hits = []
    pdf = _s3_https(meta.get("pdf_url", ""))
    if pdf:
        hits.append(AssetHit(title=f"Paper: {title}"[:200], url=pdf,
                             kind="document", **common))
    try:
        captions = _figure_captions(_get_text(_s3_https(meta.get("xml_url", "")),
                                              user_agent))
    except (urllib.error.URLError, OSError, ValueError):
        captions = {}
    # One file per figure (a JPEG over the GIF thumbnail of the same figure),
    # and a still only when the article captions it: inline formulae and
    # icons are images too.
    preference = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif")
    chosen: dict[str, str] = {}
    for media in meta.get("media_urls") or []:
        url = _s3_https(media)
        ext = "." + url.rsplit(".", 1)[-1].lower() if "." in url else ""
        kind = _PAPER_MEDIA.get(ext)
        stem = _stem(url)
        if not kind or (kind == "image" and stem not in captions):
            continue
        held = chosen.get(stem)
        if held and kind == "image" and \
                preference.index("." + held.rsplit(".", 1)[-1].lower()) <= preference.index(ext):
            continue
        chosen[stem] = url
    still = 0
    for stem, url in chosen.items():
        kind = _PAPER_MEDIA["." + url.rsplit(".", 1)[-1].lower()]
        if kind == "image":
            still += 1
            if still > figures:
                continue
        caption = captions.get(stem) or stem
        hits.append(AssetHit(title=f"{caption} [{title}]"[:240], url=url,
                             kind=kind, **common))
    return hits


def search_papers(query: str, limit: int, user_agent: str, *,
                  allow_sa: bool = False) -> list[AssetHit]:
    """Open-access papers in PMC on the query: PDFs, figures, footage."""
    licences = ['"cc by"', '"cc0"'] + (['"cc by-sa"'] if allow_sa else [])
    search = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
              + urllib.parse.urlencode({
                  "query": f"({query}) AND OPEN_ACCESS:y AND IN_PMC:y AND ("
                           + " OR ".join(f"LICENSE:{x}" for x in licences) + ")",
                  "format": "json", "resultType": "lite",
                  "pageSize": min(limit, PAPERS_PER_QUERY)}))
    try:
        rows = _get_json(search, user_agent)["resultList"]["result"]
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        return [AssetHit("", "", "papers", "", note=f"search failed: {exc}")]
    hits: list[AssetHit] = []
    for position, row in enumerate(rows[:PAPERS_PER_QUERY]):
        if row.get("pmcid"):
            hits += pmc_article(row["pmcid"], user_agent, allow_sa=allow_sa,
                                query=query, rank=position)
    return hits


#: What a citation URL names: a PMC article, a PubMed record, a DOI.
_PMCID = re.compile(r"\b(PMC\d{4,9})\b", re.I)
_PMID = re.compile(r"(?:pubmed\.ncbi\.nlm\.nih\.gov/|/abstract/MED/|/article/MED/"
                   r"|EXT_ID:)(\d{5,9})", re.I)
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>?#&]+)", re.I)


def cited_papers(urls: list[str], *, allow_sa: bool = False,
                 user_agent: str = DEFAULT_USER_AGENT, limit: int = 12,
                 figures: int = 3, verbose: bool = True) -> list[dict]:
    """The research's own citations that are open access, as asset records.

    A source URL naming a PMC article, a PubMed record or a DOI is looked up
    in Europe PMC; each open-access one gives its PDF, figures and media
    (`pmc_article`). Dossier-shaped records, verified like a search's.
    """
    say = (lambda m: print(m, file=sys.stderr)) if verbose else (lambda m: None)
    pmcids: list[str] = []

    def add(pmcid: str) -> None:
        if pmcid and pmcid.upper() not in pmcids:
            pmcids.append(pmcid.upper())

    for url in urls:
        if len(pmcids) >= limit:
            break
        url = urllib.parse.unquote(url or "")
        if (match := _PMCID.search(url)):
            add(match.group(1))
            continue
        if (match := _PMID.search(url)):
            query = f"EXT_ID:{match.group(1)} AND SRC:MED"
        elif (match := _DOI.search(url)):
            query = f'DOI:"{match.group(1).rstrip(".)")}"'
        else:
            continue
        try:
            rows = _get_json("https://www.ebi.ac.uk/europepmc/webservices/rest/"
                             "search?" + urllib.parse.urlencode(
                                 {"query": query, "format": "json",
                                  "resultType": "lite", "pageSize": 1}),
                             user_agent)["resultList"]["result"]
        except (urllib.error.URLError, OSError, KeyError, ValueError):
            continue
        if rows and rows[0].get("isOpenAccess") == "Y":
            add(rows[0].get("pmcid", ""))

    hits: list[AssetHit] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for found in pool.map(lambda p: pmc_article(p, user_agent, allow_sa=allow_sa,
                                                    figures=figures), pmcids[:limit]):
            hits += [h for h in found if h.url]
    checked = [verify_hit(h, user_agent) for h in hits]
    out = []
    for hit in checked:
        if hit.verified:
            hit.use_for = "cited by the research: " + hit.paper
            out.append(hit.record(allow_sa=allow_sa))
    say(f"  cited papers: {len(pmcids)} open access, {len(out)} files")
    return out


#: News coverage is copyrighted almost everywhere: an article is a reference
#: the film cites by outlet and headline, never a file it reproduces. Openly
#: licensed news (Wikinews) is the exception and stays usable.
NEWS_REFERENCE = "All rights reserved (news article: cite, do not reproduce)"


def search_news(query: str, limit: int, user_agent: str) -> list[AssetHit]:
    """News articles on the query: Wikinews (CC BY 2.5) and Google News.

    Google News answers with headlines, outlets and dates from the whole
    press; those records are references, marked so by their licence, and the
    download gate and the authors' briefs leave them alone.
    """
    hits: list[AssetHit] = []
    try:
        found = _get_json("https://en.wikinews.org/w/api.php?" + urllib.parse.urlencode({
            "action": "query", "list": "search", "srsearch": query, "format": "json",
            "srlimit": max(1, limit // 2), "srprop": "timestamp"}), user_agent)
        for position, row in enumerate(found.get("query", {}).get("search", [])):
            title = row.get("title", "")
            hits.append(AssetHit(
                title=f"{title} (Wikinews)"[:160],
                url="https://en.wikinews.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
                source="news/wikinews", licence="CC BY 2.5 (Wikinews)", kind="article",
                page_url="https://en.wikinews.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
                credit_line=f"Wikinews, \"{title}\", CC BY 2.5",
                query=query, rank=position, note=row.get("timestamp", "")[:10]))
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        hits.append(AssetHit("", "", "news", "", note=f"search failed: {exc}"))
    try:
        feed = _get_text("https://news.google.com/rss/search?" + urllib.parse.urlencode(
            {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}), user_agent)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return hits + [AssetHit("", "", "news", "", note=f"search failed: {exc}")]
    for position, item in enumerate(re.findall(r"<item>(.*?)</item>", feed, re.S)[:limit]):
        def field(tag: str) -> str:
            found = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", item, re.S)
            return html.unescape(_strip_html(found.group(1))) if found else ""
        outlet = field("source")
        headline = field("title")
        if outlet and headline.endswith(f" - {outlet}"):
            headline = headline[:-len(outlet) - 3]
        hits.append(AssetHit(
            title=f"{headline} ({outlet})"[:160] if outlet else headline[:160],
            url=field("link"), source=f"news/{outlet or 'press'}",
            licence=NEWS_REFERENCE, kind="article", page_url=field("link"),
            credit_line=", ".join(filter(None, [outlet, field("pubDate")[5:16]])),
            query=query, rank=position, note=field("pubDate")))
    return hits


SOURCES = {
    "nasa": search_nasa,
    "openverse": search_openverse,
    "openverse-audio": search_openverse_audio,
    "commons": search_commons,
    "archive": search_archive,
    "pdb": search_pdb,
    "papers": search_papers,
    "news": search_news,
}
DEFAULT_SOURCES = ("nasa", "openverse", "openverse-audio", "commons", "archive",
                   "pdb", "papers", "news")
#: Searchers that take the licence policy.
_TAKES_SA = ("openverse", "openverse-audio", "archive", "papers")
#: Queries a need sends to a slow or rationed catalogue: the need's best ones.
QUERY_CAP = {"openverse": 2, "openverse-audio": 1, "archive": 2, "pdb": 2,
             "papers": 2, "news": 1}
#: Kinds kept whatever their licence, as references: cited, never reproduced.
REFERENCE_KINDS = ("article",)
#: Catalogues searched only for needs of these kinds ("any" is the subject
#: as a whole): sound is wanted where a shot asks for it.
ONLY_FOR = {"openverse-audio": ("audio", "any")}


# --------------------------------------------------------------------------- #
# verification and ranking
# --------------------------------------------------------------------------- #

def verify_hit(hit: AssetHit, user_agent: str, *,
               documents: bool = False) -> AssetHit:
    """Prove the URL serves a media file, not a web page or a 404.

    A host that labels every file alike (a bucket's binary/octet-stream, a
    coordinates file served as text/plain) is answered by the file's own
    first bytes. A PDF counts when documents are wanted or the catalogue
    offered the hit as a paper.
    """
    if not hit.url.lower().startswith(("http://", "https://")):
        hit.note = "not an http URL"
        return hit
    request = urllib.request.Request(
        hit.url, headers={"User-Agent": user_agent, "Range": f"bytes=0-{VERIFY_BYTES}"})
    for attempt in range(2):
        _host_pace(hit.url).wait()
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                content_type = (response.headers.get("Content-Type") or "").lower()
                length = response.headers.get("Content-Range") or \
                    response.headers.get("Content-Length") or ""
                head = response.read(VERIFY_BYTES)
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt == 0:
                # A media host refuses a burst of checks; one wait, as long
                # as it asks for (within reason), then one more try.
                try:
                    delay = float(exc.headers.get("Retry-After") or 3)
                except (TypeError, ValueError):
                    delay = 3.0
                time.sleep(min(max(delay, 1.0), 15.0))
                continue
            hit.note = f"HTTP {exc.code}" + (" (host rate-limiting)" if exc.code == 429 else "")
            return hit
        except (urllib.error.URLError, OSError, ValueError) as exc:
            hit.note = f"unreachable: {type(exc).__name__}"
            return hit
    else:
        return hit

    hit.content_type = content_type.split(";")[0].strip()
    if hit.content_type in GENERIC_TYPES:
        hit.content_type = sniff(head) or hit.content_type
    total = re.search(r"/(\d+)$", length) or re.fullmatch(r"(\d+)", length)
    if total:
        hit.bytes = int(total.group(1))
    if hit.kind in REFERENCE_KINDS:
        # The page is the asset: it only has to answer.
        hit.verified = True
        return hit
    allowed = MEDIA_TYPES + EXTRA_TYPES + (
        DOCUMENT_TYPES if documents or hit.kind == "document" else ())
    if not any(hit.content_type.startswith(m) for m in allowed):
        hit.note = f"serves {hit.content_type or 'unknown'}, not a usable media file"
        return hit
    hit.kind = kind_for(hit.content_type, hit.url)
    hit.verified = True
    return hit


STOPWORDS = {"the", "and", "with", "from", "that", "this", "into", "over",
             "shot", "showing", "show", "footage", "image", "photo", "video",
             "diagram", "closeup", "close", "real", "actual", "inside",
             "being", "been", "has", "have", "when", "where", "while", "after",
             "before", "using", "used", "during", "between", "through", "its",
             "their", "ultra", "very", "one", "two", "for", "are", "was"}


def shorten(query: str, keep: int = 4) -> str:
    """A long sentence finds nothing in an image catalogue; keywords do."""
    words = [w for w in re.findall(r"[A-Za-z0-9-]+", query)
             if len(w) > 2 and w.lower() not in STOPWORDS]
    return " ".join(words[:keep])


def score(hit: AssetHit, terms: list[str], *, allow_sa: bool = False) -> tuple:
    """Sort key: usable first, then public domain, relevance, resolution."""
    tier = licence_tier(hit.licence, allow_sa=allow_sa)
    title = hit.title.lower()
    # Relevance to the query that FOUND the hit is the signal that works: a
    # long shot description matches nothing, so scoring on it let big
    # unrelated images win on resolution alone.
    query_terms = [w for w in re.findall(r"[a-z0-9]+", hit.query.lower())
                   if len(w) > 2 and w not in STOPWORDS]
    on_query = sum(1 for t in query_terms if t in title)
    on_shot = sum(1 for t in terms if t in title)
    return (0 if hit.verified else 1,
            -(2 * on_query + on_shot),
            hit.rank,
            tier if tier is not None else 9,
            -hit.pixels)


#: The need a film's topic searches become: material on the subject as a
#: whole, searched once and offered once.
SUBJECT = "Material on the subject as a whole"


def _relevant(hit: AssetHit, terms: list[str]) -> bool:
    """Whether the hit's title names anything its query or its shot does."""
    return score(hit, terms)[1] < 0


def _diverse(ranked: list[AssetHit], keep: int, terms: list[str]) -> list[AssetHit]:
    """`keep` hits of a ranked list, the best relevant hit of each kind first.

    Every catalogue holds far more stills than footage, sound, figures or
    structures, so a shortlist taken by rank alone is all stills.
    """
    picked: list[AssetHit] = []
    kinds: set[str] = set()
    for hit in ranked:
        if len(picked) >= keep:
            break
        if hit.kind not in kinds and _relevant(hit, terms):
            picked.append(hit)
            kinds.add(hit.kind)
    for hit in ranked:
        if len(picked) >= keep:
            break
        if not any(hit is p for p in picked):
            picked.append(hit)
    order = {id(h): n for n, h in enumerate(ranked)}
    return sorted(picked, key=lambda h: order[id(h)])


def find_assets(needs: list[dict], *, topic_searches: list[str] | None = None,
                sources: tuple[str, ...] = DEFAULT_SOURCES,
                per_need: int = 3, per_source: int = 6, jobs: int = 8,
                shortlist: int = 0,
                allow_sa: bool = False, verify: bool = True,
                user_agent: str = DEFAULT_USER_AGENT, verbose: bool = True
                ) -> list[dict]:
    """Fill a shot list from the catalogues.

    `needs` are dicts with at least `shot`; optional `searches` (a list of
    queries), `kind` and `why`. `topic_searches` name the subject itself and
    become one more need, searched once: appended to every shot's queries,
    the same general file came back for every shot that found nothing of its
    own. Returns dossier-shaped asset records.
    """
    say = (lambda m: print(m, file=sys.stderr)) if verbose else (lambda m: None)
    out: list[dict] = []
    failed: dict[str, list[str]] = {}
    needs = list(needs)
    if topic_searches:
        needs.append({"shot": f"{SUBJECT}: " + "; ".join(topic_searches[:4]),
                      "searches": list(topic_searches[:4]), "kind": "any"})

    for need in needs:
        # Deduplicate within a shot, not across shots: a file rejected for one
        # shot must stay available to the shot it actually suits.
        seen_urls: set[str] = set()
        seen_titles: set[str] = set()
        shot = need.get("shot") or need.get("question") or ""
        queries = [q for q in (need.get("searches") or [shot]) if q][:4]
        for query in list(queries):
            brief = shorten(query)
            if brief and brief.lower() != query.lower() and brief not in queries:
                queries.append(brief)
        queries = queries[:6]
        terms = [w for w in re.findall(r"[a-z0-9]+", shot.lower()) if len(w) > 3]
        kind = need.get("kind") or ""
        say(f"  shot: {shot[:70]}")

        hits: list[AssetHit] = []
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = []
            for name in sources:
                if name in ONLY_FOR and kind not in ONLY_FOR[name]:
                    continue
                searcher = SOURCES[name]
                kwargs = {"allow_sa": allow_sa} if name in _TAKES_SA else {}
                for query in queries[:QUERY_CAP.get(name, len(queries))]:
                    futures.append((name, pool.submit(searcher, query, per_source,
                                                      user_agent, **kwargs)))
            for name, future in futures:
                for hit in future.result():
                    if hit.url:
                        hits.append(hit)
                    elif hit.note:
                        failed.setdefault(name, []).append(hit.note)

        # Drop unusable licences and duplicates before spending requests; a
        # reference is kept whatever its licence, since it is only cited.
        fresh: list[AssetHit] = []
        for hit in hits:
            key = hit.title.lower().strip()
            if licence_tier(hit.licence, allow_sa=allow_sa) is None \
                    and hit.kind not in REFERENCE_KINDS:
                continue
            if hit.url in seen_urls or (key and key in seen_titles):
                continue
            seen_urls.add(hit.url)
            if key:
                seen_titles.add(key)
            fresh.append(hit)

        keep = shortlist or per_need
        if verify and fresh:
            # Verify the most promising of each kind; verification fixes a
            # hit's kind and fills in its type and size in place.
            fresh.sort(key=lambda h: score(h, terms, allow_sa=allow_sa))
            with ThreadPoolExecutor(max_workers=jobs) as pool:
                list(pool.map(lambda h: verify_hit(h, user_agent),
                              _diverse(fresh, max(keep, per_need) * 2, terms)))

        fresh.sort(key=lambda h: score(h, terms, allow_sa=allow_sa))
        picked = _diverse([h for h in fresh if h.verified or not verify], keep, terms)
        for hit in picked:
            hit.use_for = shot
            out.append(hit.record(allow_sa=allow_sa))
        kinds: dict[str, int] = {}
        for hit in picked:
            kinds[hit.kind] = kinds.get(hit.kind, 0) + 1
        say(f"    {len(picked)} usable of {len(fresh)} candidates"
            + (" — " + ", ".join(f"{n} {k}" for k, n in kinds.items()) if picked else ""))
    for name, notes in failed.items():
        say(f"  ! {name}: {len(notes)} search(es) failed, e.g. {notes[0][:90]}")
    return out


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "assets", parents=list(parents),
        help="search open catalogues for a shot's footage",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("query", nargs="+", help="what the shot needs to show")
    p.add_argument("--sources", default=",".join(DEFAULT_SOURCES),
                   help=f"comma-separated: {', '.join(SOURCES)}")
    p.add_argument("--limit", type=int, default=5, help="assets to return")
    p.add_argument("--per-source", type=int, default=6,
                   help="candidates pulled from each catalogue")
    p.add_argument("--allow-sa", action="store_true",
                   help="also accept CC BY-SA (share-alike obligations)")
    p.add_argument("--no-verify", action="store_true",
                   help="skip proving each URL serves a media file")
    p.add_argument("--json", dest="json_out", help="write records here")
    p.set_defaults(run=run)
    return p


def run(args) -> int:
    bad = [s for s in args.sources.split(",") if s and s not in SOURCES]
    if bad:
        print(f"error: unknown source(s): {', '.join(bad)}", file=sys.stderr)
        return 1

    query = " ".join(args.query)
    records = find_assets(
        [{"shot": query, "searches": [query]}],
        sources=tuple(s for s in args.sources.split(",") if s),
        per_need=args.limit, per_source=args.per_source,
        allow_sa=args.allow_sa, verify=not args.no_verify,
        verbose=not args.quiet)

    for n, row in enumerate(records, 1):
        print(f"\n{n}. {row['title']}  [{row['kind']}]")
        print(f"   {row['licence']}  ·  {row['source']}"
              + (f"  ·  {row['resolution']}" if row.get("resolution") else "")
              + (f"  ·  {row['bytes'] // 1024} KB" if row.get("bytes") else ""))
        print(f"   {row['url']}")
        if row.get("page_url"):
            print(f"   page: {row['page_url']}")
        print(f"   credit: {row['credit_line'][:100]}")
    if not records:
        print("nothing usable found; try different search terms or --allow-sa")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=2, ensure_ascii=False)
        print(f"\nwrote {args.json_out}", file=sys.stderr)
    return 0
