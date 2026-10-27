"""Comments on an account's media: read them, answer, hide, delete.

Replying and hiding need the `instagram_business_manage_comments`
permission on the token; reading needs only the basic one, so a token
that reads but cannot reply fails at the reply with the API's own error.
"""

from __future__ import annotations

from .graph import Graph
from .insights import media as list_media

COMMENT_FIELDS = ("id", "text", "username", "timestamp", "like_count", "hidden",
                  "replies{id,text,username,timestamp}")


def comments(graph: Graph, media_id: str, *, limit: int = 50) -> list[dict]:
    return list(graph.pages(f"{media_id}/comments", limit=limit, fields=COMMENT_FIELDS))


def recent(graph: Graph, ig_user_id: str, *, media_limit: int = 10,
           per_media: int = 50) -> list[dict]:
    """Comments on the last `media_limit` posts, each row naming its media."""
    out = []
    for row in list_media(graph, ig_user_id, limit=media_limit,
                          fields=("id", "permalink", "timestamp", "comments_count")):
        if not row.get("comments_count"):
            continue
        for c in comments(graph, row["id"], limit=per_media):
            c["media_id"] = row["id"]
            c["permalink"] = row.get("permalink", "")
            out.append(c)
    return out


def unanswered(rows: list[dict], own_username: str) -> list[dict]:
    """The comments nobody from the account has replied to."""
    out = []
    for c in rows:
        if c.get("username") == own_username:
            continue
        replies = (c.get("replies") or {}).get("data", [])
        if any(r.get("username") == own_username for r in replies):
            continue
        out.append(c)
    return out


def reply(graph: Graph, comment_id: str, text: str) -> dict:
    return graph.post(f"{comment_id}/replies", message=text)


def comment(graph: Graph, media_id: str, text: str) -> dict:
    return graph.post(f"{media_id}/comments", message=text)


def hide(graph: Graph, comment_id: str, hidden: bool = True) -> dict:
    return graph.post(comment_id, hide=hidden)


def delete(graph: Graph, comment_id: str) -> dict:
    return graph.delete(comment_id)
