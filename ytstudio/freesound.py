"""Freesound, as `yt sound --fetch` uses it: search, pick, download a preview.

Freesound's API (https://freesound.org/apiref/) answers text searches and
serves each sound's previews with a plain token -- the client secret, passed
as `token`. The original uploads need OAuth, so what gets downloaded is the
high-quality mp3 preview: fine for a scratch mix and for placing against
picture, and the manifest says so, so nobody mistakes it for a master.

Only CC0 and CC-BY sounds are kept: the filter asks Freesound for those two
licences, and every hit is judged again here through the pipeline's one
licence policy (`assets.licence_verdict`), so Noncommercial, Sampling+ and
Share-Alike never get through even if the filter changes under us.

The two HTTP seams are module functions, `fetch_json` and `fetch_bytes`, so
tests replace them and never touch the network. A key's value is never
printed: the token travels in the URL, and `describe()` is what an error or a
log line says about a URL.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import env
from .assets import licence_verdict

API = "https://freesound.org/apiv2"
#: Environment variables that hold the token, first one set wins. The name
#: is what gets reported when one is missing; the value, never.
TOKEN_VARS = ("FREESOUND_CLIENT_SECRET", "FREESOUND_API_KEY")
#: The preview that gets downloaded. Originals need OAuth; this does not.
PREVIEW = "preview-hq-mp3"
FIELDS = ("id,name,username,license,duration,avg_rating,num_ratings,previews,"
          "url,type,filesize")
USER_AGENT = "yt-sound/1 (scratch-mix previews; https://freesound.org/apiref/)"
#: What Freesound calls the licences the project allows, for its filter.
ALLOWED_FILTER = 'license:("Attribution" OR "Creative Commons 0")'


class FreesoundError(RuntimeError):
    """A search or a download that did not come back usable."""


# --------------------------------------------------------------------------- #
# HTTP seams
# --------------------------------------------------------------------------- #

def fetch_json(url: str, *, timeout: float = 30.0) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_bytes(url: str, *, timeout: float = 180.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def describe(url: str) -> str:
    """A URL as it may be printed: the path, never its query (the token)."""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def token() -> str:
    """The token from the environment, loading the key file if need be."""
    env.load()
    for name in TOKEN_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


# --------------------------------------------------------------------------- #
# licences
# --------------------------------------------------------------------------- #

_URL_LICENCES = (
    (re.compile(r"/publicdomain/zero/"), "CC0"),
    (re.compile(r"/licenses/sampling\+/"), "Sampling+"),
    (re.compile(r"/licenses/by-nc-sa/"), "CC-BY-NC-SA"),
    (re.compile(r"/licenses/by-nc-nd/"), "CC-BY-NC-ND"),
    (re.compile(r"/licenses/by-nc/"), "CC-BY-NC"),
    (re.compile(r"/licenses/by-sa/"), "CC-BY-SA"),
    (re.compile(r"/licenses/by-nd/"), "CC-BY-ND"),
    (re.compile(r"/licenses/by/"), "CC-BY"),
)
_NAME_LICENCES = {
    "creative commons 0": "CC0",
    "attribution": "CC-BY",
    "attribution noncommercial": "CC-BY-NC",
    "attribution share alike": "CC-BY-SA",
    "sampling+": "Sampling+",
}
_VERSION = re.compile(r"/(\d+\.\d+)/")


def licence_name(raw: str) -> str:
    """Freesound's licence, as a short name the credit line and the licence
    policy both read: a URL becomes "CC-BY 4.0", a display name "CC-BY"."""
    text = (raw or "").strip()
    if not text:
        return ""
    for pattern, name in _URL_LICENCES:
        if pattern.search(text):
            version = _VERSION.search(text)
            return f"{name} {version.group(1)}" if version else name
    return _NAME_LICENCES.get(text.lower(), text)


def allowed(licence: str) -> bool:
    """CC0 and CC-BY only, judged by the pipeline's one licence policy."""
    name = licence_name(licence)
    if not name or name.startswith("Sampling"):
        return False
    return licence_verdict(name, allow_sa=False)[0] == "free"


# --------------------------------------------------------------------------- #
# hits
# --------------------------------------------------------------------------- #

@dataclass
class Hit:
    id: int
    name: str
    author: str
    licence: str
    licence_url: str
    duration: float
    rating: float
    ratings: int
    preview: str
    ext: str
    url: str
    filesize: int = 0

    @classmethod
    def from_raw(cls, row: dict) -> Hit | None:
        previews = row.get("previews") or {}
        preview = previews.get(PREVIEW) or previews.get("preview-lq-mp3") or ""
        if not preview:
            return None
        try:
            sound_id = int(row.get("id") or 0)
        except (TypeError, ValueError):
            return None
        if not sound_id:
            return None
        ext = preview.rsplit(".", 1)[-1].lower() if "." in preview else "mp3"
        return cls(
            id=sound_id, name=str(row.get("name") or ""),
            author=str(row.get("username") or ""),
            licence=licence_name(str(row.get("license") or "")),
            licence_url=str(row.get("license") or ""),
            duration=float(row.get("duration") or 0.0),
            rating=float(row.get("avg_rating") or 0.0),
            ratings=int(row.get("num_ratings") or 0),
            preview=preview, ext=ext if ext in ("mp3", "ogg") else "mp3",
            url=str(row.get("url") or f"{API}/sounds/{sound_id}/"),
            filesize=int(row.get("filesize") or 0))

    @property
    def credit(self) -> str:
        """The attribution line CC-BY requires, and CC0 deserves anyway."""
        return f'"{self.name}" by {self.author} ({self.licence}) {self.url}'


# --------------------------------------------------------------------------- #
# ranking
# --------------------------------------------------------------------------- #

def duration_window(want: float) -> tuple[float, float]:
    """Durations worth asking Freesound for: not much shorter than the cue,
    and not absurdly longer (a long file is trimmed; a short one cannot be
    stretched)."""
    want = max(0.5, float(want))
    return round(want * 0.25, 2), round(max(want * 6, want + 30), 2)


def fit(hit: Hit, want: float) -> float:
    """How well a hit's length serves the cue, 0..1. Covering the cue is
    what matters; being far longer costs a little, being shorter a lot."""
    want = max(0.5, float(want))
    if hit.duration <= 0:
        return 0.0
    if hit.duration < want:
        return max(0.0, hit.duration / want) * 0.6
    if hit.duration <= want * 4:
        return 1.0
    return max(0.4, 1.0 - (hit.duration - want * 4) / (want * 40))


def score(hit: Hit, want: float) -> float:
    """Fit first, then a rating that counts for more the more votes it has."""
    confidence = min(hit.ratings, 10) / 10
    quality = (hit.rating / 5.0) * confidence
    return fit(hit, want) * 2.0 + quality


def rank(hits: list[Hit], *, want: float) -> list[Hit]:
    return sorted(hits, key=lambda h: (-score(h, want), -h.ratings, h.id))


# --------------------------------------------------------------------------- #
# search and download
# --------------------------------------------------------------------------- #

def search(query: str, *, want_seconds: float, token: str,
           limit: int = 15) -> list[Hit]:
    """Allowed hits for a query, unranked. No token asks nothing; a network
    failure is an empty result the caller reports as an unfilled cue."""
    query = " ".join(str(query or "").split())
    if not token or not query:
        return []
    low, high = duration_window(want_seconds)
    params = {"query": query, "fields": FIELDS, "page_size": limit,
              "filter": f"{ALLOWED_FILTER} duration:[{low} TO {high}]",
              "sort": "score", "token": token}
    url = f"{API}/search/text/?" + urllib.parse.urlencode(params)
    try:
        data = fetch_json(url)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"  freesound: search failed at {describe(url)} "
              f"({type(exc).__name__})", file=sys.stderr)
        return []
    hits = []
    for row in (data.get("results") or []) if isinstance(data, dict) else []:
        hit = Hit.from_raw(row) if isinstance(row, dict) else None
        if hit is not None and allowed(hit.licence_url or hit.licence):
            hits.append(hit)
    return hits


def ranked(queries: list[str], *, want_seconds: float, token: str
           ) -> tuple[list[Hit], str]:
    """Every allowed hit of the first query that has any, best first, and
    that query. One search serves every fallback a caller may need."""
    for query in queries:
        hits = rank(search(query, want_seconds=want_seconds, token=token),
                    want=want_seconds)
        if hits:
            return hits, query
    return [], ""


def best(queries: list[str], *, want_seconds: float, token: str
         ) -> tuple[Hit | None, str]:
    """The first query with an allowed hit wins; returns (hit, query used)."""
    hits, query = ranked(queries, want_seconds=want_seconds, token=token)
    return (hits[0], query) if hits else (None, "")


def download(hit: Hit, dest: Path) -> int:
    """Fetch the preview into `dest`; a file already there is left alone.
    Returns the size on disk."""
    dest = Path(dest)
    if dest.is_file() and dest.stat().st_size > 0:
        return dest.stat().st_size
    try:
        blob = fetch_bytes(hit.preview)
    except (urllib.error.URLError, OSError) as exc:
        raise FreesoundError(f"download failed at {describe(hit.preview)} "
                             f"({type(exc).__name__})") from exc
    if not blob:
        raise FreesoundError(f"empty download at {describe(hit.preview)}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(blob)
    return len(blob)
