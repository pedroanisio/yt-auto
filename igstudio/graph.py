"""The Graph API client: one call shape, mapped errors, retries and paging.

Zero dependencies: urllib does the HTTP. The token goes in the Authorization
header, never in the URL, so it is not in a log line, a shell history or an
error message. Every response is JSON; an `error` object becomes a
GraphError with Meta's code, subcode and message, and the few transient
codes (rate limits, 5xx) are retried with backoff.

Two hosts, chosen by how the account was authorised (accounts.py):

    instagram   https://graph.instagram.com   Instagram API with Instagram Login
    facebook    https://graph.facebook.com    Instagram API with Facebook Login

The transport is a plain callable `(method, url, headers, body) -> (status,
text)` so tests swap it for a fake and the suite never touches the network;
with IG_NO_NET set the real transport refuses to start.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterator

#: The Graph API version every call names. Meta supports a version for about
#: two years from release; IG_API_VERSION overrides without a code change.
DEFAULT_VERSION = "v24.0"

HOSTS = {
    "instagram": "https://graph.instagram.com",
    "facebook": "https://graph.facebook.com",
}

#: Meta error codes that mean "try again later", not "you are wrong".
TRANSIENT_CODES = {1, 2, 4, 17, 32, 613}
TRANSIENT_STATUS = {429, 500, 502, 503, 504}

Transport = Callable[[str, str, dict, bytes | None], tuple[int, str]]


class GraphError(Exception):
    """An error the Graph API returned, with Meta's code and message."""

    def __init__(self, message: str, *, status: int = 0, code: int = 0,
                 subcode: int = 0, type: str = "", fbtrace: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.subcode = subcode
        self.type = type
        self.fbtrace = fbtrace

    def __str__(self) -> str:
        bits = [self.message]
        if self.code:
            bits.append(f"code {self.code}" + (f"/{self.subcode}" if self.subcode else ""))
        if self.status:
            bits.append(f"http {self.status}")
        return " (".join([bits[0], ", ".join(bits[1:])]) + (")" if len(bits) > 1 else "")

    @property
    def transient(self) -> bool:
        return self.status in TRANSIENT_STATUS or self.code in TRANSIENT_CODES

    @property
    def expired_token(self) -> bool:
        return self.code == 190


class NoNetwork(RuntimeError):
    """Raised when IG_NO_NET is set and a real call was about to be made."""


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None,
                     *, timeout: float = 60.0) -> tuple[int, str]:
    if os.environ.get("IG_NO_NET"):
        raise NoNetwork(f"IG_NO_NET is set; refusing {method} {url.split('?')[0]}")
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def version() -> str:
    return os.environ.get("IG_API_VERSION", "").strip() or DEFAULT_VERSION


class Graph:
    """One account's view of the API: a host, a version and a token."""

    def __init__(self, token: str, *, login: str = "instagram",
                 transport: Transport | None = None, retries: int = 3,
                 backoff: float = 2.0, sleep: Callable[[float], None] = time.sleep,
                 api_version: str | None = None) -> None:
        if login not in HOSTS:
            raise ValueError(f"login must be one of {sorted(HOSTS)}, not {login!r}")
        self.token = token
        self.login = login
        self.base = f"{HOSTS[login]}/{api_version or version()}"
        self.transport = transport or urllib_transport
        self.retries = retries
        self.backoff = backoff
        self.sleep = sleep
        self.calls = 0

    # -- the one call shape ------------------------------------------------

    def call(self, method: str, path: str, params: dict | None = None) -> dict:
        """GET/POST/DELETE `path` with `params`; the parsed JSON object."""
        params = {k: _encode(v) for k, v in (params or {}).items() if v is not None}
        url = self.base + "/" + path.lstrip("/")
        headers = {"Authorization": f"Bearer {self.token}",
                   "Accept": "application/json"}
        body = None
        if method == "GET" or method == "DELETE":
            if params:
                url += "?" + urllib.parse.urlencode(params)
        else:
            body = urllib.parse.urlencode(params).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        attempt = 0
        while True:
            attempt += 1
            self.calls += 1
            status, text = self.transport(method, url, headers, body)
            data = _parse(text, status)
            if "error" not in data:
                return data
            err = _error(data["error"], status)
            if err.transient and attempt <= self.retries:
                self.sleep(self.backoff * attempt)
                continue
            raise err

    def get(self, path: str, **params: Any) -> dict:
        return self.call("GET", path, params)

    def post(self, path: str, **params: Any) -> dict:
        return self.call("POST", path, params)

    def delete(self, path: str, **params: Any) -> dict:
        return self.call("DELETE", path, params)

    def pages(self, path: str, *, limit: int | None = None, **params: Any) -> Iterator[dict]:
        """The rows of a paged edge, following `paging.next` up to `limit`."""
        seen = 0
        data = self.get(path, **params)
        while True:
            for row in data.get("data", []):
                yield row
                seen += 1
                if limit is not None and seen >= limit:
                    return
            after = (data.get("paging") or {}).get("cursors", {}).get("after")
            if not after or not data.get("paging", {}).get("next"):
                return
            data = self.get(path, after=after, **params)


def _encode(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def _parse(text: str, status: int) -> dict:
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        raise GraphError(f"non-JSON response: {text[:200]!r}", status=status) from None
    if not isinstance(data, dict):
        return {"data": data}
    if status >= 400 and "error" not in data:
        data = {"error": {"message": f"http {status}: {text[:200]}"}}
    return data


def _error(err: Any, status: int) -> GraphError:
    if not isinstance(err, dict):
        return GraphError(str(err), status=status)
    message = err.get("error_user_msg") or err.get("message") or "unknown error"
    title = err.get("error_user_title")
    if title:
        message = f"{title}: {message}"
    return GraphError(message, status=status, code=int(err.get("code") or 0),
                      subcode=int(err.get("error_subcode") or 0),
                      type=str(err.get("type") or ""), fbtrace=str(err.get("fbtrace_id") or ""))
