#!/usr/bin/env python3
"""Find genuinely usable b-roll: real URLs, real licences, verified live.

The model is good at saying what a video needs to show and bad at remembering
file URLs, so it never supplies one here. It writes a shot list; these catalogue
APIs supply the files, with the licence the host itself reports:

    NASA Image & Video Library   public domain, direct files, no key
    Openverse                    CC-filtered aggregator (Flickr, museums, NASA)
    Wikimedia Commons            authoritative licence metadata per file
    Internet Archive             archival stills and footage

Every hit is then fetched (first 2 KB) to prove the URL serves a media file
rather than a landing page, and scored: public domain first, then resolution
and how well the title matches the query.

    yt assets "EUV plasma tin droplet" --limit 8
    yt assets "ITER tokamak assembly" --sources nasa,commons --json out.json
    yt assets "lithography cleanroom" --allow-sa --no-verify
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

DEFAULT_USER_AGENT = "yt-research/1.0 (research dossier; +local script)"

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
DOCUMENT_TYPES = ("application/pdf",)
VERIFY_BYTES = 2048


def kind_for(mime: str) -> str:
    """Map a content type to an asset kind. A PDF is a document, not an image."""
    mime = (mime or "").lower()
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("application/pdf"):
        return "document"
    if mime.startswith("model/"):
        return "3d-model"
    return "image"


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

    @property
    def pixels(self) -> int:
        return self.width * self.height

    def record(self, *, allow_sa: bool = False) -> dict:
        """The shape `yt research` stores in a dossier."""
        tier = licence_tier(self.licence, allow_sa=allow_sa)
        row = asdict(self)
        row.pop("verified")
        return row | {
            "licence_verified": self.licence,
            "licence_check": f"from {self.source} API",
            "commercial_ok": tier is not None,
            "share_alike": tier == TIER_SHARE_ALIKE,
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


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text or "")).strip()


# --------------------------------------------------------------------------- #
# catalogue searches
# --------------------------------------------------------------------------- #

def search_nasa(query: str, limit: int, user_agent: str) -> list[AssetHit]:
    """NASA Image & Video Library. Public domain, direct file URLs."""
    url = ("https://images-api.nasa.gov/search?"
           + urllib.parse.urlencode({"q": query, "page_size": limit}))
    try:
        items = _get_json(url, user_agent)["collection"]["items"]
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        return [AssetHit("", "", "nasa", "", note=f"search failed: {exc}")]

    hits: list[AssetHit] = []
    for position, item in enumerate(items[:limit]):
        data = (item.get("data") or [{}])[0]
        links = item.get("links") or []
        if not links:
            continue
        href = links[0].get("href", "")
        media = data.get("media_type", "image")
        # Previews come back as ~thumb; the large rendition is the usable one.
        file_url = re.sub(r"~(thumb|small|medium|orig)\.(jpg|jpeg|png|tif)",
                          "~large.jpg", href, flags=re.I)
        credit = ", ".join(filter(None, [data.get("center"),
                                         data.get("secondary_creator")]))
        hits.append(AssetHit(
            title=data.get("title", "")[:120],
            url=file_url or href,
            source="nasa",
            licence="Public domain (NASA media usage guidelines)",
            kind="video" if media == "video" else media,
            page_url=f"https://images.nasa.gov/details/{data.get('nasa_id', '')}",
            credit_line=f"NASA{'/' + credit if credit else ''}",
            query=query,
            rank=position,
            note=_strip_html(data.get("description", ""))[:200],
        ))
    return hits


def search_openverse(query: str, limit: int, user_agent: str, *,
                     allow_sa: bool = False) -> list[AssetHit]:
    """Openverse: CC-licensed media across Flickr, museums, NASA and more."""
    licences = "pdm,cc0,by" + (",by-sa" if allow_sa else "")
    url = ("https://api.openverse.org/v1/images/?"
           + urllib.parse.urlencode({"q": query, "license": licences,
                                     "page_size": limit}))
    try:
        results = _get_json(url, user_agent)["results"]
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        return [AssetHit("", "", "openverse", "", note=f"search failed: {exc}")]

    hits = []
    for position, row in enumerate(results[:limit]):
        licence = f"CC {row.get('license', '').upper()} {row.get('license_version', '')}".strip()
        hits.append(AssetHit(
            title=(row.get("title") or "")[:120],
            url=row.get("url", ""),
            source=f"openverse/{row.get('source', '?')}",
            licence=licence,
            page_url=row.get("foreign_landing_url", "") or row.get("license_url", ""),
            credit_line=_strip_html(row.get("attribution", ""))[:200]
                        or f"{row.get('creator', 'unknown')} / {licence}",
            width=int(row.get("width") or 0),
            height=int(row.get("height") or 0),
            query=query,
            rank=position,
        ))
    return hits


def search_commons(query: str, limit: int, user_agent: str, *,
                   documents: bool = False) -> list[AssetHit]:
    """Wikimedia Commons file search, with the licence Commons itself reports."""
    # Without a filetype filter, full-text search returns scanned PDFs whose
    # body text mentions the query -- useless as b-roll.
    if "filetype:" not in query and not documents:
        query = f"{query} filetype:bitmap|drawing|video"
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
            kind=kind_for(mime),
            page_url=info.get("descriptionurl", ""),
            credit_line=f"{artist} / {licence} (Wikimedia Commons)" if artist else licence,
            width=int(info.get("width") or 0),
            height=int(info.get("height") or 0),
            content_type=mime,
            query=query,
            rank=position,
        ))
    return hits


def search_archive(query: str, limit: int, user_agent: str, *,
                   allow_sa: bool = False) -> list[AssetHit]:
    """Internet Archive stills and footage, kept only when the licence is clear."""
    search = ("https://archive.org/advancedsearch.php?"
              + urllib.parse.urlencode({
                  "q": f'{query} AND (mediatype:image OR mediatype:movies)',
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
        wanted = (".jpg", ".jpeg", ".png", ".tif") if doc.get("mediatype") == "image" \
            else (".mp4", ".webm", ".ogv")
        files = [f for f in meta.get("files", [])
                 if f.get("name", "").lower().endswith(wanted)]
        if not files:
            continue
        best = max(files, key=lambda f: int(f.get("size") or 0))
        hits.append(AssetHit(
            title=str(doc.get("title", ""))[:120],
            url=f"https://archive.org/download/{doc['identifier']}/"
                + urllib.parse.quote(best["name"]),
            source="archive",
            licence=licence,
            kind="video" if doc.get("mediatype") == "movies" else "image",
            page_url=f"https://archive.org/details/{doc['identifier']}",
            credit_line=f"{doc.get('creator', 'Internet Archive')} / {licence}",
            bytes=int(best.get("size") or 0),
            query=query,
            rank=position,
        ))
    return hits


SOURCES = {
    "nasa": search_nasa,
    "openverse": search_openverse,
    "commons": search_commons,
    "archive": search_archive,
}
DEFAULT_SOURCES = ("nasa", "openverse", "commons")


# --------------------------------------------------------------------------- #
# verification and ranking
# --------------------------------------------------------------------------- #

def verify_hit(hit: AssetHit, user_agent: str, *,
               documents: bool = False) -> AssetHit:
    """Prove the URL serves a media file, not a web page or a 404."""
    if not hit.url.lower().startswith(("http://", "https://")):
        hit.note = "not an http URL"
        return hit
    request = urllib.request.Request(
        hit.url, headers={"User-Agent": user_agent, "Range": f"bytes=0-{VERIFY_BYTES}"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            content_type = (response.headers.get("Content-Type") or "").lower()
            length = response.headers.get("Content-Range") or \
                response.headers.get("Content-Length") or ""
            response.read(VERIFY_BYTES)
    except urllib.error.HTTPError as exc:
        hit.note = f"HTTP {exc.code}" + (" (host rate-limiting)" if exc.code == 429 else "")
        return hit
    except (urllib.error.URLError, OSError, ValueError) as exc:
        hit.note = f"unreachable: {type(exc).__name__}"
        return hit

    hit.content_type = content_type.split(";")[0].strip()
    total = re.search(r"/(\d+)$", length) or re.fullmatch(r"(\d+)", length)
    if total:
        hit.bytes = int(total.group(1))
    allowed = MEDIA_TYPES + (DOCUMENT_TYPES if documents else ())
    if not any(hit.content_type.startswith(m) for m in allowed):
        hit.note = f"serves {hit.content_type or 'unknown'}, not a usable media file"
        return hit
    hit.kind = kind_for(hit.content_type)
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


def find_assets(needs: list[dict], *, topic_searches: list[str] | None = None,
                sources: tuple[str, ...] = DEFAULT_SOURCES,
                per_need: int = 3, per_source: int = 6, jobs: int = 4,
                shortlist: int = 0,
                allow_sa: bool = False, verify: bool = True,
                user_agent: str = DEFAULT_USER_AGENT, verbose: bool = True
                ) -> list[dict]:
    """Fill a shot list from the catalogues.

    `needs` are dicts with at least `shot`; optional `searches` (a list of
    queries), `kind` and `why`. Returns dossier-shaped asset records.
    """
    say = (lambda m: print(m, file=sys.stderr)) if verbose else (lambda m: None)
    out: list[dict] = []

    for need in needs:
        # Deduplicate within a shot, not across shots: a file rejected for one
        # shot must stay available to the shot it actually suits.
        seen_urls: set[str] = set()
        seen_titles: set[str] = set()
        shot = need.get("shot") or need.get("question") or ""
        queries = [q for q in (need.get("searches") or [shot]) if q][:3]
        for query in list(queries):
            brief = shorten(query)
            if brief and brief.lower() != query.lower() and brief not in queries:
                queries.append(brief)
        queries += [t for t in (topic_searches or [])[:3] if t not in queries]
        queries = queries[:7]
        terms = [w for w in re.findall(r"[a-z0-9]+", shot.lower()) if len(w) > 3]
        say(f"  shot: {shot[:70]}")

        hits: list[AssetHit] = []
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = []
            for query in queries:
                for name in sources:
                    searcher = SOURCES[name]
                    kwargs = ({"allow_sa": allow_sa}
                              if name in ("openverse", "archive") else {})
                    futures.append(pool.submit(searcher, query, per_source,
                                               user_agent, **kwargs))
            for future in futures:
                hits += [h for h in future.result() if h.url]

        # Drop unusable licences and duplicates before spending requests.
        fresh: list[AssetHit] = []
        for hit in hits:
            key = hit.title.lower().strip()
            if licence_tier(hit.licence, allow_sa=allow_sa) is None:
                continue
            if hit.url in seen_urls or (key and key in seen_titles):
                continue
            seen_urls.add(hit.url)
            if key:
                seen_titles.add(key)
            fresh.append(hit)

        if verify and fresh:
            # Verify the most promising first, stop once the shot is covered.
            fresh.sort(key=lambda h: score(h, terms, allow_sa=allow_sa))
            checked: list[AssetHit] = []
            with ThreadPoolExecutor(max_workers=jobs) as pool:
                for hit in pool.map(lambda h: verify_hit(h, user_agent),
                                    fresh[:per_need * 4]):
                    checked.append(hit)
            fresh = checked + fresh[per_need * 4:]

        fresh.sort(key=lambda h: score(h, terms, allow_sa=allow_sa))
        keep = shortlist or per_need
        picked = [h for h in fresh if h.verified or not verify][:keep]
        for hit in picked:
            hit.use_for = shot
            out.append(hit.record(allow_sa=allow_sa))
        say(f"    {len(picked)} usable of {len(fresh)} candidates"
            + (f" — {picked[0].source} {picked[0].licence[:28]}" if picked else ""))
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
