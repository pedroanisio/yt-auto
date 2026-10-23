"""Vercel AI Gateway: the variables that send the claude CLI through it.

Opt-in only. Nothing is routed unless `YT_GATEWAY=vercel` is set: a gateway
key in the environment changes nothing by itself, so direct access to each
provider stays the default. With the switch unset, or set to one of the
usual ways of saying off (off, 0, false, no, none), `claude_env` returns
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
ANTHROPIC_BASE_URL and ANTHROPIC_CUSTOM_HEADERS are set. ANTHROPIC_API_KEY
is left as it is, so a direct key still set in the shell is what the CLI
sends to the gateway in place of the login; the docs say to unset it.
`YT_GATEWAY_URL` replaces the host, for a proxy in front of the gateway.

The codex CLI goes through the gateway by another door: not variables but a
model provider, named with `-c` pairs on its command line (`codex_config`).
Vercel's Codex page (https://vercel.com/docs/ai-gateway/coding-agents/openai-codex,
manual setup) gives the provider:

    model_provider                   vercel
    model_providers.vercel.name      Vercel AI Gateway
    model_providers.vercel.base_url  https://ai-gateway.vercel.sh/codex/v1
    model_providers.vercel.env_key   AI_GATEWAY_API_KEY: codex reads the key
                                     from the variable this names, so the key
                                     itself never goes on a command line
    model_providers.vercel.wire_api  responses: Codex removed Chat Completions

Nothing is written to $CODEX_HOME/config.toml, and no WebSocket setting is
sent. YT_GATEWAY_AUTH is the claude CLI's alone: codex always sends the key.

A switch that is set but cannot be honoured raises GatewayError, a permanent
failure: a typo must not quietly mean "direct". That covers an unknown value,
no key, a key with whitespace or control characters inside it (in a header
a newline would start another header), a URL that already names a path, and
a CLAUDE_CODE_USE_BEDROCK, _VERTEX or _FOUNDRY switch, which Claude Code
honours ahead of ANTHROPIC_BASE_URL so the call would not reach the gateway
at all (the claude CLI's only: codex does not read them). On the codex route
a key with whitespace around it is refused as well, since codex reads the
variable as it is rather than the stripped value yt checked. No message here
carries the key, and nothing writes ~/.claude/settings.json or runs a setup
command.

Stdlib only, and no import of claude_engine, which imports this.
"""

from __future__ import annotations

import os
from typing import Mapping
from urllib.parse import urlsplit

#: The opt-in switch, and the one gateway it may name.
SWITCH = "YT_GATEWAY"
VERCEL = "vercel"
#: Values of the switch, and of Claude Code's provider switches, that mean
#: off. Compared after strip() and lower().
OFF = ("", "off", "0", "false", "no", "none")
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
#: The Codex endpoint's path on the host, /v1 included: codex appends
#: /responses and /models to a provider's base_url.
CODEX_PATH = "/codex/v1"
#: The provider's id and display name in Codex's config, as Vercel's page
#: writes them.
CODEX_PROVIDER = "vercel"
CODEX_PROVIDER_NAME = "Vercel AI Gateway"
#: The header that carries the key in subscription mode.
HEADER = "x-ai-gateway-api-key"
#: Claude Code's own provider switches. Any one of them on sends the CLI to
#: that cloud whatever ANTHROPIC_BASE_URL says.
CLOUD_SWITCHES = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                  "CLAUDE_CODE_USE_FOUNDRY")


class GatewayError(ValueError):
    """The gateway was asked for but cannot be used as configured. Running
    again would fail the same way, so callers treat it as permanent."""


def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _switch(environ: Mapping[str, str]) -> str:
    """The switch's value, "" for any way of saying off."""
    value = (environ.get(SWITCH) or "").strip().lower()
    return "" if value in OFF else value


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
    if path.endswith(("/v1", CLAUDE_PATH, "/codex")):
        raise GatewayError(
            f"{URL} is the gateway's host: leave off /v1, {CLAUDE_PATH} and "
            f"{CODEX_PATH}, which yt and the CLIs add themselves")
    return raw.rstrip("/")


def base_url(environ: Mapping[str, str] | None = None) -> str | None:
    """The host YT_GATEWAY_URL names, checked as `host` checks it, or None
    when it is unset. It does not read the switch: the gateway engine
    (gateway_engine.py, AUTO-124) is its own opt-in, and hands its program a
    host only when the operator named one, so the program's own default
    stands otherwise."""
    if not (_env(environ).get(URL) or "").strip():
        return None
    return host(environ)


def claude_base_url(environ: Mapping[str, str] | None = None) -> str:
    """The claude CLI's ANTHROPIC_BASE_URL: the host plus /claude-code."""
    return (base_url(environ) or DEFAULT_HOST) + CLAUDE_PATH


def codex_base_url(environ: Mapping[str, str] | None = None) -> str:
    """The codex provider's base_url: the host plus /codex/v1."""
    return host(environ) + CODEX_PATH


def api_key(environ: Mapping[str, str] | None = None) -> str:
    """The gateway key from `environ` (default os.environ), checked as the
    routes check it, for a caller that sends it itself (`yt doctor
    --gateway-ping`). Raises GatewayError, whose message never carries it."""
    return _key(_env(environ))


def _key(environ: Mapping[str, str]) -> str:
    key = (environ.get(KEY) or "").strip()
    if not key:
        raise GatewayError(f"{SWITCH}={VERCEL} needs the gateway key in {KEY}")
    # A key is one printable token. A newline from a pasted .env line would
    # split ANTHROPIC_CUSTOM_HEADERS into an extra header.
    if any(ch.isspace() or not ch.isprintable() for ch in key):
        raise GatewayError(
            f"{KEY} holds whitespace or a control character inside the key; "
            "set it to the key alone")
    return key


def _cloud_switch(environ: Mapping[str, str]) -> str | None:
    """The first of Claude Code's provider switches that is on, if any."""
    for name in CLOUD_SWITCHES:
        if (environ.get(name) or "").strip().lower() not in OFF:
            return name
    return None


def _vercel(environ: Mapping[str, str]) -> bool:
    """True when the switch names the gateway, False when it is off; a
    value that names neither is refused, not read as off."""
    switch = _switch(environ)
    if switch and switch != VERCEL:
        raise GatewayError(
            f"{SWITCH} names no known gateway: set it to {VERCEL}, "
            "or unset it to call the providers directly")
    return bool(switch)


def claude_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The variables to lay over the claude CLI's environment, from `environ`
    (default os.environ). Empty when the switch is unset."""
    environ = _env(environ)
    if not _vercel(environ):
        return {}
    cloud = _cloud_switch(environ)
    if cloud:
        raise GatewayError(
            f"{cloud} is set, and Claude Code would route to that cloud ahead of "
            f"the gateway: unset it, or unset {SWITCH}")
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


def codex_config(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The `-c` pairs that make the codex CLI's model provider the gateway,
    from `environ` (default os.environ), in the order Vercel's page lists
    them. Empty when the switch is unset.

    The key is checked here although only its variable's name is sent:
    without it codex would start and fail on its first request, after the
    run had begun, and a retry would fail the same way."""
    environ = _env(environ)
    if not _vercel(environ):
        return {}
    base = codex_base_url(environ)
    _key(environ)
    raw = environ.get(KEY) or ""
    if raw != raw.strip():
        # codex sends the variable as it is, not the value checked above.
        raise GatewayError(
            f"{KEY} has whitespace or a newline around the key, and codex sends "
            "it as it is; set it to the key alone")
    return {
        "model_provider": CODEX_PROVIDER,
        f"model_providers.{CODEX_PROVIDER}.name": CODEX_PROVIDER_NAME,
        f"model_providers.{CODEX_PROVIDER}.base_url": base,
        f"model_providers.{CODEX_PROVIDER}.env_key": KEY,
        f"model_providers.{CODEX_PROVIDER}.wire_api": "responses",
    }
