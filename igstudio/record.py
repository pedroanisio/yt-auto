"""The account record: what each published post opened on, asked for and
tagged, and the repeats a new post would make.

The same idea as ytstudio.channel (AUTO-137): nothing inside one post can
say whether the account is repeating itself, so `$IG_HOME/record/<account>.json`
keeps one row per published post, and `repeats` returns measured lines,
prefixed `account:`, that `ig post` prints as warnings and `ig caption`
hands the writer to steer away from. It decides nothing.

A row is written by `ig post` and `ig run` when a post is published (hook =
the caption's first line, hashtags parsed, the trigger and CTA type when
the caption came from `ig caption` or `--trigger`/`--cta` named them), and
`ig record --sync` backfills rows from the API's media list, where the
trigger and CTA type stay unknown.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path
from typing import Sequence

from ytstudio.core import read_json, write_json
from ytstudio.narrative import HOOK_TRIGGERS  # noqa: F401  (one vocabulary with yt)

CTA_TYPES = ("save", "send", "comment", "follow", "none")
EARLIER_LIMIT = 12
HASHTAG = re.compile(r"#(\w+)", re.U)
#: Opening words compared between hooks: enough to catch a formula, few
#: enough that two posts on one subject are not the same hook.
OPENING_WORDS = 4


def folder(root: Path) -> Path:
    return root / "record"


def path(root: Path, account: str) -> Path:
    return folder(root) / f"{account}.json"


def rows(root: Path, account: str) -> list[dict]:
    data = read_json(path(root, account), warn=False) or {}
    out = data.get("posts") if isinstance(data.get("posts"), list) else []
    return [r for r in out if isinstance(r, dict) and r.get("media_id")]


def save(root: Path, account: str, posts: list[dict]) -> Path:
    folder(root).mkdir(parents=True, exist_ok=True)
    posts = sorted(posts, key=lambda r: r.get("published_at", ""))
    return write_json(path(root, account), {"account": account, "posts": posts})


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[\w']+", text.lower()) if w][:OPENING_WORDS]


def fingerprint(caption: str, *, kind: str = "", trigger: str = "", cta: str = "",
                media_id: str = "", permalink: str = "", published_at: str = "",
                source: str = "ig post", variant_of: str = "") -> dict:
    """One row: what later posts are compared with."""
    lines = [ln.strip() for ln in (caption or "").splitlines() if ln.strip()]
    hook = lines[0] if lines else ""
    tags = sorted({t.lower() for t in HASHTAG.findall(caption or "")})
    return {
        "media_id": str(media_id), "kind": kind, "permalink": permalink,
        "published_at": published_at or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "hook": hook, "opening": _words(hook),
        "trigger": trigger if trigger in HOOK_TRIGGERS else "",
        "cta": cta if cta in CTA_TYPES else "",
        "hashtags": tags,
        "caption_sha256": hashlib.sha256((caption or "").encode("utf-8")).hexdigest()[:16],
        "source": source,
        "variant_of": str(variant_of or ""),
    }


def add(root: Path, account: str, row: dict) -> list[dict]:
    posts = [r for r in rows(root, account) if r.get("media_id") != row.get("media_id")]
    posts.append(row)
    save(root, account, posts)
    return posts


def sync(root: Path, account: str, media: Sequence[dict]) -> tuple[int, int]:
    """Rows from the API's media list: a new media id is added with what the
    API says; a known one keeps its trigger and CTA and takes the permalink
    and time. Returns (added, updated)."""
    posts = {r["media_id"]: r for r in rows(root, account)}
    added = updated = 0
    for m in media:
        mid = str(m.get("id") or "")
        if not mid:
            continue
        kind = {"REELS": "reel", "FEED": "feed", "STORY": "story"}.get(
            str(m.get("media_product_type") or "").upper(), "")
        if kind == "feed":
            kind = "carousel" if str(m.get("media_type") or "").upper() == "CAROUSEL_ALBUM" else "image"
        if mid in posts:
            row = posts[mid]
            row["permalink"] = m.get("permalink") or row.get("permalink", "")
            row["published_at"] = _iso(m.get("timestamp")) or row.get("published_at", "")
            updated += 1
            continue
        posts[mid] = fingerprint(m.get("caption") or "", kind=kind, media_id=mid,
                                 permalink=m.get("permalink") or "",
                                 published_at=_iso(m.get("timestamp")), source="sync")
        added += 1
    save(root, account, list(posts.values()))
    return added, updated


def _iso(stamp) -> str:
    """The API's `2026-10-27T10:00:00+0000` as ISO 8601 with a colon."""
    if not stamp:
        return ""
    s = str(stamp)
    if re.search(r"[+-]\d{4}$", s):
        s = s[:-2] + ":" + s[-2:]
    try:
        return dt.datetime.fromisoformat(s).astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()
    except ValueError:
        return s


def earlier(root: Path, account: str, limit: int = EARLIER_LIMIT, *, before: str = "") -> list[dict]:
    """The last `limit` posts, newest first, published before `before` when given."""
    out = [r for r in rows(root, account) if not before or r.get("published_at", "") < before]
    out.sort(key=lambda r: r.get("published_at", ""), reverse=True)
    return out[:limit]


def repeats(new: dict, earlier_rows: Sequence[dict]) -> list[str]:
    """Measured lines, `account: ...`, where `new` repeats the earlier posts:
    the same opening words as one of them, the same hashtag set as one of
    them, the same trigger as more than half of those that declare one, or
    the same CTA type as the last three in a row. Decides nothing."""
    posts = [r for r in earlier_rows if isinstance(r, dict)]
    total = len(posts)
    out: list[str] = []
    mine = new.get("opening") or []
    if mine:
        for e in posts:
            if (e.get("opening") or []) == mine:
                out.append(f"account: hook: opens on the same words as {e.get('permalink') or e.get('media_id')} "
                           f"(\"{e.get('hook', '')[:60]}\")")
    tags = set(new.get("hashtags") or [])
    if tags:
        for e in posts:
            if set(e.get("hashtags") or []) == tags:
                out.append(f"account: hashtags: the same set as {e.get('permalink') or e.get('media_id')} "
                           f"({', '.join('#' + t for t in sorted(tags))})")
    trigger = new.get("trigger")
    if trigger:
        declared = [e for e in posts if e.get("trigger")]
        same = [e for e in declared if e.get("trigger") == trigger]
        if declared and len(same) * 2 > len(declared):
            out.append(f"account: hook: {trigger} is the trigger {len(same)} of the last "
                       f"{len(declared)} declared posts opened on")
    cta = new.get("cta")
    if cta and cta != "none":
        last = [e.get("cta") for e in posts[:3]]
        if len(last) == 3 and all(c == cta for c in last):
            out.append(f"account: cta: the last 3 posts all asked for a {cta}; this one does too")
    return out


def summary(earlier_rows: Sequence[dict]) -> str:
    """What the writer is told about the account's recent posts."""
    posts = [r for r in earlier_rows if isinstance(r, dict)]
    if not posts:
        return ""
    lines = [f"The account's last {len(posts)} posts, newest first (do not repeat an opening, "
             "a hook formula or a hashtag set):"]
    for e in posts:
        bits = [e.get("published_at", "")[:10], e.get("kind") or "post"]
        if e.get("trigger"):
            bits.append(f"trigger {e['trigger']}")
        if e.get("cta"):
            bits.append(f"cta {e['cta']}")
        if e.get("variant_of"):
            bits.append(f"variant of {e['variant_of']}")
        lines.append(f"- {' '.join(b for b in bits if b)}: \"{e.get('hook', '')[:90]}\""
                     + (f"  [{' '.join('#' + t for t in e.get('hashtags') or [])}]" if e.get("hashtags") else ""))
    return "\n".join(lines)
