"""Posts scheduled for later, kept in `$IG_HOME/queue.json`, sent by `ig run`.

An item is an account name, a `when` (ISO 8601 with its offset), the post
as publish.Post stores it, and a state: `pending` until `ig run` finds it
due, then `done` with the published media's row or `failed` with the
error. A transient failure (rate limit, 5xx, a container still processing)
leaves the item pending with its attempt counted, and MAX_ATTEMPTS ends
that. Nothing here runs by itself: a cron line or a systemd timer calls
`ig run` as often as the schedule needs, and `ig run --loop` is the same
in one process.

`parse_when` reads what you type: `2026-10-28T09:00`, `2026-10-28 09:00`,
either with an offset, or `+2h`, `+30m`, `+1d`, `now`. A time with no
offset is in the account's timezone (accounts.Account.timezone), else the
machine's.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from pathlib import Path
from typing import Callable

from ytstudio.core import read_json, write_json

from .graph import GraphError
from .publish import Post, check

QUEUE_FILE = "queue.json"
MAX_ATTEMPTS = 3
STATES = ("pending", "done", "failed")
RELATIVE = re.compile(r"^\+(\d+)\s*([mhd])$", re.I)
UNITS = {"m": 60, "h": 3600, "d": 86400}


def parse_when(text: str, tz: dt.tzinfo, *, now: dt.datetime | None = None) -> dt.datetime:
    text = text.strip()
    base = now or dt.datetime.now(dt.timezone.utc)
    if text.lower() == "now":
        return base
    m = RELATIVE.match(text)
    if m:
        return base + dt.timedelta(seconds=int(m.group(1)) * UNITS[m.group(2).lower()])
    try:
        when = dt.datetime.fromisoformat(text.replace(" ", "T", 1))
    except ValueError:
        raise ValueError(f"cannot read a time from {text!r}: use 2026-10-28T09:00, "
                         "2026-10-28 09:00+01:00, +2h, +30m, +1d or now") from None
    if when.tzinfo is None:
        when = when.replace(tzinfo=tz)
    return when


class Queue:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / QUEUE_FILE
        data = read_json(self.path, warn=False) or {}
        rows = data.get("items") if isinstance(data.get("items"), list) else []
        self.items: list[dict] = [r for r in rows if isinstance(r, dict) and r.get("id")]

    def save(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return write_json(self.path, {"items": self.items})

    def add(self, account: str, when: dt.datetime, post: Post, *,
            now: dt.datetime | None = None) -> dict:
        problems = check(post)
        if problems:
            raise ValueError("; ".join(problems))
        item = {
            "id": uuid.uuid4().hex[:8],
            "account": account,
            "when": when.replace(microsecond=0).isoformat(),
            "post": post.to_dict(),
            "state": "pending",
            "created_at": (now or dt.datetime.now(dt.timezone.utc)).replace(microsecond=0).isoformat(),
            "attempts": 0,
            "error": "",
            "result": None,
        }
        self.items.append(item)
        return item

    def get(self, item_id: str) -> dict:
        for item in self.items:
            if item["id"] == item_id:
                return item
        raise KeyError(f"no queued item {item_id!r}")

    def remove(self, item_id: str) -> dict:
        item = self.get(item_id)
        self.items.remove(item)
        return item

    def clear(self, state: str | None = None) -> int:
        before = len(self.items)
        self.items = [i for i in self.items if state is not None and i.get("state") != state]
        return before - len(self.items)

    def listing(self, state: str | None = None, account: str | None = None) -> list[dict]:
        rows = [i for i in self.items
                if (state is None or i.get("state") == state)
                and (account is None or i.get("account") == account)]
        return sorted(rows, key=lambda i: i.get("when", ""))

    def due(self, now: dt.datetime) -> list[dict]:
        out = []
        for item in self.listing("pending"):
            try:
                when = dt.datetime.fromisoformat(item["when"])
            except (KeyError, ValueError):
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=dt.timezone.utc)
            if when <= now:
                out.append(item)
        return out


def run(queue: Queue, now: dt.datetime, publisher: Callable[[str, Post], dict], *,
        say: Callable[[str], None] = lambda line: None,
        max_attempts: int = MAX_ATTEMPTS, dry_run: bool = False) -> list[dict]:
    """Publish every due item through `publisher(account, post)`; the items touched.

    Each item is saved as soon as its state changes, so a crash between two
    posts loses nothing and repeats nothing. A dry run only names what is
    due and changes nothing, on disk or in memory.
    """
    touched = []
    for item in queue.due(now):
        if dry_run:
            say(f"{item['id']} {item['account']}: due, {item['post'].get('kind')} "
                f"(dry run, not sent)")
            touched.append(item)
            continue
        item["attempts"] = int(item.get("attempts") or 0) + 1
        item["last_try"] = now.replace(microsecond=0).isoformat()
        try:
            result = publisher(item["account"], Post.from_dict(item["post"]))
        except GraphError as exc:
            item["error"] = str(exc)
            if exc.transient and item["attempts"] < max_attempts:
                say(f"{item['id']} {item['account']}: {exc}; will retry")
            else:
                item["state"] = "failed"
                say(f"{item['id']} {item['account']}: failed: {exc}")
        except (LookupError, ValueError) as exc:
            item["error"] = str(exc)
            item["state"] = "failed"
            say(f"{item['id']} {item['account']}: failed: {exc}")
        else:
            item["state"] = "done"
            item["error"] = ""
            item["result"] = result
            say(f"{item['id']} {item['account']}: published {result.get('permalink') or result.get('id')}")
        touched.append(item)
        queue.save()
    return touched
