"""A fake Graph API for the ig tests: no network, scripted answers.

`FakeGraph` is the transport igstudio.graph.Graph takes. It records every
request (method, path, params) and answers from `routes`: a dict keyed by
`"METHOD path"` whose value is a JSON object, an exception to raise, or a
callable `(params) -> object`; a list is consumed one answer per call.
Unrouted calls answer with a default that keeps the flows moving.
"""

from __future__ import annotations

import json
import urllib.parse


class FakeGraph:
    def __init__(self, routes: dict | None = None) -> None:
        self.routes = dict(routes or {})
        self.requests: list[tuple[str, str, dict]] = []
        self.tokens: list[str] = []
        self.containers = 0

    def __call__(self, method: str, url: str, headers: dict, body: bytes | None):
        self.tokens.append(headers.get("Authorization", ""))
        parsed = urllib.parse.urlsplit(url)
        path = "/".join(parsed.path.split("/")[2:])     # drop /vXX.0/
        params = dict(urllib.parse.parse_qsl(parsed.query))
        if body:
            params.update(urllib.parse.parse_qsl(body.decode("utf-8")))
        self.requests.append((method, path, params))
        answer = self.routes.get(f"{method} {path}")
        if isinstance(answer, list):
            answer = answer.pop(0) if answer else None
        if callable(answer) and not isinstance(answer, type):
            answer = answer(params)
        if isinstance(answer, BaseException):
            raise answer
        if answer is None:
            answer = self.default(method, path, params)
        if isinstance(answer, tuple):
            status, data = answer
        else:
            status, data = (400 if "error" in answer else 200), answer
        return status, json.dumps(data)

    def default(self, method: str, path: str, params: dict) -> dict:
        if method == "POST" and path.endswith("/media"):
            self.containers += 1
            return {"id": f"c{self.containers}"}
        if method == "POST" and path.endswith("/media_publish"):
            return {"id": "m" + params.get("creation_id", "")}
        if method == "GET" and path.startswith("c") and "status_code" in params.get("fields", ""):
            return {"status_code": "FINISHED", "id": path}
        if method == "GET" and path.endswith("/content_publishing_limit"):
            return {"data": [{"quota_usage": 3, "config": {"quota_total": 100}}]}
        if method == "GET" and path.startswith("m"):
            return {"id": path, "permalink": f"https://www.instagram.com/p/{path}/",
                    "media_type": "IMAGE", "media_product_type": "FEED"}
        if method == "GET" and "fields" in params and "username" in params["fields"]:
            return {"id": path, "username": "shop", "followers_count": 1200, "media_count": 40}
        return {"id": path}

    def sent(self, method: str, path_suffix: str) -> list[dict]:
        return [p for m, path, p in self.requests if m == method and path.endswith(path_suffix)]
