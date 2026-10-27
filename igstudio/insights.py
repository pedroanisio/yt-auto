"""Reading an account: profile, recent media, account and media insights.

Metric names follow the API after April 2025: `views` replaced
`impressions` and the Reels play counts. Account totals come back with
`metric_type=total_value`; `follower_count` is a daily series and the API
refuses it for accounts under 100 followers, so each group is asked for
on its own and a refused group is reported, not fatal.
"""

from __future__ import annotations

import datetime as dt
import statistics

from .graph import Graph, GraphError

MEDIA_FIELDS = ("id", "caption", "media_type", "media_product_type", "permalink",
                "timestamp", "like_count", "comments_count", "thumbnail_url", "media_url")

#: Account metrics that take metric_type=total_value over a period.
ACCOUNT_TOTALS = ("views", "reach", "profile_views", "accounts_engaged", "total_interactions",
                  "likes", "comments", "shares", "saves")
#: Account metrics that are daily series.
ACCOUNT_SERIES = ("follower_count",)

#: Media metrics by product type; the API rejects a metric the type lacks.
MEDIA_METRICS = {
    "FEED": ("views", "reach", "likes", "comments", "saved", "shares", "total_interactions"),
    "REELS": ("views", "reach", "likes", "comments", "saved", "shares", "total_interactions",
              "ig_reels_avg_watch_time", "ig_reels_video_view_total_time"),
    "STORY": ("views", "reach", "replies", "shares", "total_interactions", "navigation"),
}
MEDIA_METRICS_MIN = ("reach", "views")


def profile(graph: Graph, ig_user_id: str, fields=None) -> dict:
    from .accounts import PROFILE_FIELDS
    return graph.get(ig_user_id, fields=fields or PROFILE_FIELDS)


def media(graph: Graph, ig_user_id: str, *, limit: int = 20, fields=MEDIA_FIELDS) -> list[dict]:
    return list(graph.pages(f"{ig_user_id}/media", limit=limit, fields=fields))


def media_row(graph: Graph, media_id: str, fields=MEDIA_FIELDS) -> dict:
    return graph.get(media_id, fields=fields)


def _window(days: int, until: dt.datetime | None) -> tuple[int, int]:
    end = until or dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=max(1, min(days, 30)))
    return int(start.timestamp()), int(end.timestamp())


def account_insights(graph: Graph, ig_user_id: str, *, days: int = 7,
                     until: dt.datetime | None = None) -> dict:
    """`{"totals": {metric: value}, "series": {metric: [[date, value], ...]},
    "warnings": [...]}` for the last `days` (at most 30, the API's window)."""
    since, until_ts = _window(days, until)
    out: dict = {"days": days, "totals": {}, "series": {}, "warnings": []}
    try:
        data = graph.get(f"{ig_user_id}/insights", metric=ACCOUNT_TOTALS, period="day",
                         metric_type="total_value", since=since, until=until_ts)
        for row in data.get("data", []):
            value = (row.get("total_value") or {}).get("value")
            out["totals"][row.get("name", "?")] = value
    except GraphError as exc:
        out["warnings"].append(f"totals: {exc}")
    try:
        data = graph.get(f"{ig_user_id}/insights", metric=ACCOUNT_SERIES, period="day",
                         since=since, until=until_ts)
        for row in data.get("data", []):
            points = [[v.get("end_time", "")[:10], v.get("value")] for v in row.get("values", [])]
            out["series"][row.get("name", "?")] = points
    except GraphError as exc:
        out["warnings"].append(f"follower_count: {exc}")
    return out


def media_insights(graph: Graph, media_id: str, product_type: str = "") -> dict:
    """`{metric: value}` for one media, by its product type (FEED, REELS, STORY)."""
    if not product_type:
        product_type = media_row(graph, media_id, ("id", "media_product_type")) \
            .get("media_product_type", "FEED")
    metrics = MEDIA_METRICS.get(product_type.upper(), MEDIA_METRICS["FEED"])
    try:
        data = graph.get(f"{media_id}/insights", metric=metrics)
    except GraphError as exc:
        if exc.code not in (100,):
            raise
        data = graph.get(f"{media_id}/insights", metric=MEDIA_METRICS_MIN)
        data.setdefault("warnings", []).append(f"full metric set refused: {exc}")
    out = {"media_id": media_id, "product_type": product_type.upper(), "metrics": {}}
    for row in data.get("data", []):
        values = row.get("values") or [{}]
        out["metrics"][row.get("name", "?")] = values[0].get("value")
    if data.get("warnings"):
        out["warnings"] = data["warnings"]
    return out


#: A post whose metric is this many times the account's median is a winner
#: worth re-cutting. NO STANDARD: a house prior, unmeasured (TO VERIFY).
WINNER_RATIO = 1.5
#: What `ig top` ranks by, and the metrics it shows beside it.
TOP_METRICS = ("views", "reach", "saved", "shares", "likes", "comments")


def rank(rows: list[dict], *, metric: str = "views") -> list[dict]:
    """The rows sorted by `metrics[metric]`, each with the account's median
    of that metric, its `ratio` to it and whether it is a `winner`."""
    out = []
    values = [float((r.get("metrics") or {}).get(metric) or 0) for r in rows]
    median = statistics.median(values) if values else 0.0
    for r, v in zip(rows, values):
        ratio = (v / median) if median else 0.0
        out.append({**r, "metric": metric, "value": v, "median": median, "ratio": ratio,
                    "winner": bool(median) and ratio >= WINNER_RATIO})
    out.sort(key=lambda r: r["value"], reverse=True)
    return out


def top(graph: Graph, ig_user_id: str, *, limit: int = 20, metric: str = "views",
        days: int = 30, until: dt.datetime | None = None) -> list[dict]:
    """The last `limit` posts (within `days`) with their insights, ranked."""
    end = until or dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=days)
    rows = []
    for m in media(graph, ig_user_id, limit=limit, fields=("id", "caption", "media_product_type",
                                                             "media_type", "permalink", "timestamp")):
        stamp = str(m.get("timestamp") or "")
        if stamp:
            try:
                when = dt.datetime.fromisoformat(stamp[:-2] + ":" + stamp[-2:] if stamp[-5] in "+-" else stamp)
                if when < start:
                    continue
            except ValueError:
                pass
        try:
            ins = media_insights(graph, m["id"], m.get("media_product_type") or "")
            metrics = {k: v for k, v in ins["metrics"].items() if k in TOP_METRICS}
        except GraphError as exc:
            metrics, m["warning"] = {}, str(exc)
        rows.append({**m, "metrics": metrics})
    return rank(rows, metric=metric)
