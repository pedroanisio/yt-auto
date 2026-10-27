"""Publishing: the container flow for images, reels, stories and carousels.

The API publishes in two steps. A *container* is created from a public
URL of the media (Meta fetches it; a local file cannot be sent this way),
is processed until its `status_code` is FINISHED, and is then published
with `media_publish`. A carousel makes one container per item, then a
parent that names them. `plan` builds those requests without sending
anything, which is what `--dry-run` prints and what the queue stores;
`publish` sends them.

Kinds:

    image      a feed photo            image_url
    reel       a Reel (also the feed video format)   video_url
    story      a story, photo or video   image_url or video_url
    carousel   2 to 10 photos/videos     image_url / video_url each

The quota is 100 API-published posts per account per rolling 24 hours (a
carousel counts once); `quota` reads it and `publish` refuses to start a
post the quota would reject.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Callable

from .graph import Graph, GraphError

KINDS = ("image", "reel", "story", "carousel")
#: A trial reel is shown to non-followers first; `auto` lets Instagram share
#: it with followers when it performs, `manual` leaves that to the app.
TRIALS = {"auto": "SS_PERFORMANCE", "manual": "MANUAL"}
VIDEO_EXT = re.compile(r"\.(mp4|mov|m4v)(\?.*)?$", re.I)
FINISHED = "FINISHED"
FAILED = {"ERROR", "EXPIRED"}
CAROUSEL_MIN, CAROUSEL_MAX = 2, 10
CAPTION_MAX = 2200


@dataclass
class Post:
    kind: str
    media: list[str] = field(default_factory=list)   # public URLs
    caption: str = ""
    cover_url: str = ""          # reel cover image
    thumb_offset: int | None = None   # reel cover frame, ms
    alt_text: str = ""           # image only
    location_id: str = ""
    share_to_feed: bool = True   # reels
    collaborators: list[str] = field(default_factory=list)
    audio_name: str = ""         # reels
    trial: str = ""              # reels: "auto" (graduates on performance) or "manual"
    ai_generated: bool = False   # Meta's AI label on the post
    variant_of: str = ""         # the media id this post is a re-cut of (record only)

    @classmethod
    def from_dict(cls, row: dict) -> "Post":
        known = {k: v for k, v in row.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def to_dict(self) -> dict:
        return asdict(self)


def is_video(url: str) -> bool:
    return bool(VIDEO_EXT.search(url))


def is_public_url(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def check(post: Post) -> list[str]:
    """What stops the post before any call. Empty means it can go."""
    problems = []
    if post.kind not in KINDS:
        problems.append(f"kind must be one of {KINDS}, not {post.kind!r}")
        return problems
    if not post.media:
        problems.append("no media: give at least one public URL")
    for url in post.media:
        if not is_public_url(url):
            problems.append(f"not a public URL: {url!r} (Meta fetches the media itself; "
                            "host the file where https reaches it)")
    n = len(post.media)
    if post.kind in ("image", "reel", "story") and n != 1:
        problems.append(f"a {post.kind} takes exactly one media URL, not {n}")
    if post.kind == "carousel" and not (CAROUSEL_MIN <= n <= CAROUSEL_MAX):
        problems.append(f"a carousel takes {CAROUSEL_MIN} to {CAROUSEL_MAX} items, not {n}")
    if post.kind == "reel" and post.media and not is_video(post.media[0]):
        problems.append("a reel needs a video URL (.mp4/.mov)")
    if post.kind == "image" and post.media and is_video(post.media[0]):
        problems.append("an image post got a video URL; use --reel")
    if len(post.caption) > CAPTION_MAX:
        problems.append(f"caption is {len(post.caption)} characters; the limit is {CAPTION_MAX}")
    if post.caption.count("#") > 30:
        problems.append("more than 30 hashtags")
    if post.kind == "story" and post.caption:
        problems.append("a story has no caption (the API ignores it); drop --caption")
    if post.trial and post.kind != "reel":
        problems.append("a trial post must be a reel (--reel)")
    if post.trial and post.trial not in TRIALS:
        problems.append(f"trial must be one of {sorted(TRIALS)}, not {post.trial!r}")
    return problems


def plan(post: Post) -> list[dict]:
    """The container requests, in order; the last one is the one published.

    Each step is `{"step": name, "params": {...}}`; a parent step's
    `children` is filled in from the item steps' results at publish time.
    """
    problems = check(post)
    if problems:
        raise ValueError("; ".join(problems))
    common = {}
    if post.ai_generated:
        common["is_ai_generated"] = True
    if post.location_id:
        common["location_id"] = post.location_id
    if post.collaborators:
        common["collaborators"] = post.collaborators
    steps: list[dict] = []
    if post.kind == "image":
        params = {"image_url": post.media[0], "caption": post.caption, **common}
        if post.alt_text:
            params["alt_text"] = post.alt_text
        steps.append({"step": "image", "params": params})
    elif post.kind == "reel":
        params = {"media_type": "REELS", "video_url": post.media[0], "caption": post.caption,
                  "share_to_feed": post.share_to_feed, **common}
        if post.cover_url:
            params["cover_url"] = post.cover_url
        if post.thumb_offset is not None:
            params["thumb_offset"] = post.thumb_offset
        if post.audio_name:
            params["audio_name"] = post.audio_name
        if post.trial:
            params["trial_params"] = {"graduation_strategy": TRIALS[post.trial]}
        steps.append({"step": "reel", "params": params})
    elif post.kind == "story":
        url = post.media[0]
        key = "video_url" if is_video(url) else "image_url"
        steps.append({"step": "story", "params": {"media_type": "STORIES", key: url}})
    else:
        for i, url in enumerate(post.media, 1):
            params = {"is_carousel_item": True}   # the parent carries location, collaborators, AI
            if is_video(url):
                params.update(media_type="VIDEO", video_url=url)
            else:
                params["image_url"] = url
            steps.append({"step": f"item {i}", "params": params})
        steps.append({"step": "carousel", "params": {"media_type": "CAROUSEL",
                                                     "caption": post.caption,
                                                     "children": [], **common}})
    return steps


def quota(graph: Graph, ig_user_id: str) -> tuple[int, int]:
    """(posts published through the API in the last 24 h, the cap)."""
    data = graph.get(f"{ig_user_id}/content_publishing_limit", fields=("quota_usage", "config"))
    rows = data.get("data") or [{}]
    row = rows[0] if rows else {}
    used = int(row.get("quota_usage") or 0)
    total = int((row.get("config") or {}).get("quota_total") or 100)
    return used, total


def wait_ready(graph: Graph, container_id: str, *, poll: float = 5.0, timeout: float = 900.0,
               sleep: Callable[[float], None] = time.sleep,
               clock: Callable[[], float] = time.monotonic) -> dict:
    """Poll a container until FINISHED; GraphError on ERROR/EXPIRED or a timeout."""
    start = clock()
    while True:
        status = graph.get(container_id, fields=("status_code", "status"))
        code = status.get("status_code", "")
        if code == FINISHED:
            return status
        if code in FAILED:
            raise GraphError(f"container {container_id} {code}: {status.get('status', '')}")
        if clock() - start > timeout:
            raise GraphError(f"container {container_id} still {code or 'unknown'} after "
                             f"{int(timeout)} s: {status.get('status', '')}")
        sleep(poll)


def publish(graph: Graph, ig_user_id: str, post: Post, *, poll: float = 5.0,
            timeout: float = 900.0, sleep: Callable[[float], None] = time.sleep,
            clock: Callable[[], float] = time.monotonic, check_quota: bool = True,
            say: Callable[[str], None] = lambda line: None) -> dict:
    """Create, wait for and publish the post; the published media's row."""
    steps = plan(post)
    if check_quota:
        used, total = quota(graph, ig_user_id)
        if used >= total:
            raise GraphError(f"publishing quota used up: {used}/{total} in the last 24 h")
        say(f"quota {used}/{total} used in the last 24 h")
    children: list[str] = []
    container = ""
    for step in steps:
        params = dict(step["params"])
        if "children" in params:
            params["children"] = children
        created = graph.post(f"{ig_user_id}/media", **params)
        container = str(created.get("id", ""))
        if not container:
            raise GraphError(f"{step['step']}: no container id in {created}")
        say(f"{step['step']}: container {container}")
        wait_ready(graph, container, poll=poll, timeout=timeout, sleep=sleep, clock=clock)
        if params.get("is_carousel_item"):
            children.append(container)
    published = graph.post(f"{ig_user_id}/media_publish", creation_id=container)
    media_id = str(published.get("id", ""))
    say(f"published media {media_id}")
    try:
        row = graph.get(media_id, fields=("id", "permalink", "media_type",
                                          "media_product_type", "timestamp"))
    except GraphError:
        row = {"id": media_id}
    row.setdefault("id", media_id)
    row["container_id"] = container
    return row
