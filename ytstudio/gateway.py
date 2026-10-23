"""Vercel AI Gateway: the variables that send the claude CLI through it.

Opt-in only. Nothing is routed unless `YT_GATEWAY=vercel` is set: a gateway
key in the environment changes nothing by itself, so direct access to each
provider stays the default. With the switch unset `claude_env` returns
nothing, and every engine's child environment is exactly what it was before
this module existed.

The Claude Code endpoint (https://vercel.com/docs/ai-gateway/coding-agents/claude-code,
manual setup) takes three variables:

    ANTHROPIC_BASE_URL    https://ai-gateway.vercel.sh/claude-code, with no
                          /v1: the CLI's SDK appends /v1/messages itself, so
                          a /v1 here is a 404
    ANTHROPIC_AUTH_TOKEN  the gateway key ($AI_GATEWAY_API_KEY)
    ANTHROPIC_API_KEY     "": the CLI takes a non-empty one first, so a real
                          Anthropic key left in the shell would win silently

With a Claude subscription (`YT_GATEWAY_AUTH=subscription`) the login stays
the CLI's own and the key travels as a header instead: only
ANTHROPIC_BASE_URL and ANTHROPIC_CUSTOM_HEADERS are set. `YT_GATEWAY_URL`
replaces the host, for a proxy in front of the gateway.

A switch that is set but cannot be honoured (an unknown value, no key, a URL
that already names a path) raises GatewayError, a permanent failure: a typo
must not quietly mean "direct". No message here carries the key, and nothing
writes ~/.claude/settings.json or runs a setup command.

Stdlib only, and no import of claude_engine, which imports this.
"""

from __future__ import annotations

import os
from typing import Mapping
from urllib.parse import urlsplit

#: The opt-in switch, and the one gateway it may name.
SWITCH = "YT_GATEWAY"
VERCEL = "vercel"
#: The gateway key, under the name Vercel's own docs give it
#: (https://vercel.com/docs/ai-gateway/authentication-and-byok).
KEY = "AI_GATEWAY_API_KEY"
#: How the claude CLI authenticates: "key" (the default) or "subscription".
AUTH = "YT_GATEWAY_AUTH"
AUTH_MODES = ("key", "subscription")
#: Replaces the gateway's host; the endpoint's path is still added to it.
URL = "YT_GATEWAY_URL"
DEFAULT_HOST = "https://ai-gateway.vercel.sh"
CLAUDE_PATH = "/claude-code"
#: The header that carries the key in subscription mode.
HEADER = "x-ai-gateway-api-key"


class GatewayError(ValueError):
    """The gateway was asked for but cannot be used as configured. Running
    again would fail the same way, so callers treat it as permanent."""


def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _switch(environ: Mapping[str, str]) -> str:
    return (environ.get(SWITCH) or "").strip().lower()


def active(environ: Mapping[str, str] | None = None) -> bool:
    """Whether calls go through the gateway: only when YT_GATEWAY=vercel."""
    return _switch(_env(environ)) == VERCEL


def auth_mode(environ: Mapping[str, str] | None = None) -> str:
    """"key" or "subscription"; anything else is refused."""
    mode = (_env(environ).get(AUTH) or "key").strip().lower()
    if mode not in AUTH_MODES:
        raise GatewayError(f"{AUTH} must be one of {', '.join(AUTH_MODES)}")
    return mode


def host(environ: Mapping[str, str] | None = None) -> str:
    """The gateway's host, without a trailing slash. The value is not echoed
    in an error, since a proxy URL may carry credentials."""
    raw = (_env(environ).get(URL) or "").strip() or DEFAULT_HOST
    parts = urlsplit(raw)
    if parts.scheme not in ("https", "http") or not parts.netloc:
        raise GatewayError(f"{URL} must be an http(s) URL such as {DEFAULT_HOST}")
    path = parts.path.rstrip("/")
    if path.endswith("/v1") or path.endswith(CLAUDE_PATH):
        raise GatewayError(
            f"{URL} is the gateway's host: leave off /v1 and {CLAUDE_PATH}, "
            "which yt and the CLI add themselves")
    return raw.rstrip("/")


def claude_base_url(environ: Mapping[str, str] | None = None) -> str:
    """The claude CLI's ANTHROPIC_BASE_URL: the host plus /claude-code."""
    return host(environ) + CLAUDE_PATH


def _key(environ: Mapping[str, str]) -> str:
    key = (environ.get(KEY) or "").strip()
    if not key:
        raise GatewayError(f"{SWITCH}={VERCEL} needs the gateway key in {KEY}")
    return key


def claude_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The variables to lay over the claude CLI's environment, from `environ`
    (default os.environ). Empty when the switch is unset."""
    environ = _env(environ)
    switch = _switch(environ)
    if not switch:
        return {}
    if switch != VERCEL:
        raise GatewayError(
            f"{SWITCH} names no known gateway: set it to {VERCEL}, "
            "or unset it to call the providers directly")
    mode = auth_mode(environ)
    base = claude_base_url(environ)
    key = _key(environ)
    if mode == "subscription":
        # The operator's own headers stay; an older gateway header does not.
        kept = [line for line in (environ.get("ANTHROPIC_CUSTOM_HEADERS") or "").split("\n")
                if line.strip() and not line.strip().lower().startswith(HEADER + ":")]
        return {"ANTHROPIC_BASE_URL": base,
                "ANTHROPIC_CUSTOM_HEADERS": "\n".join(kept + [f"{HEADER}: Bearer {key}"])}
    return {"ANTHROPIC_BASE_URL": base, "ANTHROPIC_AUTH_TOKEN": key,
            "ANTHROPIC_API_KEY": ""}
