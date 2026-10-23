#!/usr/bin/env python3
"""
gateway_engine.py -- one structured decision through Vercel AI Gateway, by way
of the AI SDK, with claude_engine's API.

Opt-in only. The claude and codex CLIs stay the engines of every stage. This
one exists for the questions neither can answer: a model that listens to the
mix or watches the cut (no Anthropic or OpenAI model on the gateway takes
audio or video), or a reviewer from another family. A stage reaches it only
by building `Harness(engine="gateway")` behind its own flag; YT_GATEWAY,
which reroutes the claude CLI (ytstudio/gateway.py), neither turns it on nor
is needed by it, and YT_ENGINE does not name it.

It runs `node gateway/gateway-engine.mjs`, a small program on the official
AI SDK (`ai` 7.0.150, pinned in gateway/package.json), with one JSON request
on stdin, and reads one JSON line back. The Python side stays stdlib only
(README: "No dependencies"); Node 22 or later and `npm ci --ignore-scripts`
in gateway/ are needed only when a stage opts in.

    python3 gateway_engine.py json "Judge this clip" --schema verdict.json \\
        --model google/gemini-3.6-flash --provider google --media clip.mp4

What it does, and why:

  - `ask` / `ask_json` answer one prompt, with media files attached
    (images, video, audio, PDF, sent inline). `stream` and `agent` are
    refused: the program makes one HTTP call, it cannot read files with
    tools, so a pass that inspects a directory stays on claude.
  - `ask_json` returns the parsed answer without checking it. The SDK does
    not validate a schema either (it only parses), and checking is
    `Harness.decide`'s job, so an answer that breaks the schema gets the
    harness's one ask-again, then fails (invariant 3).
  - Preconditions are checked before anything starts, each a permanent
    error naming the fix: the key (AI_GATEWAY_API_KEY), a `provider/model`
    id that is not a virtual model (`vmc/`, which may route anywhere) nor a
    Claude Code picker id, Node 22 or later (read once per process), the
    pinned `ai` in gateway/node_modules, every media file present, of a
    known type and under `max_media_mb` together, and no effort or
    fallback model (neither reaches the request; the fallbacks the gateway
    offers may cross families, so the program strips them).
  - Python owns retries: the program is told `maxRetries: 0`, so every paid
    attempt is one this engine counted. Only a transient failure is tried
    again (a retryable 429 or 5xx, a timeout, an unexplained crash); a
    budget 402 that surfaces as a retryable 500 is recognised by its text.
  - The model that answered is checked against the one asked for (its
    `modelId` and every routing attempt's `canonicalSlug`): a different
    model is a permanent error, booked first. A different provider serving
    the same model passes.
  - Cost: the gateway's own figure (`providerMetadata.gateway.cost`), else
    the generation record's `total_cost` when the poll is on
    (`cost_poll_tries`, off by default, so /v1/generation stays out of the
    hot path), else a price row times the tokens ($GATEWAY_PRICES, then
    spend.price_for with $CLAUDE_PRICES), else $0. Which one it was is an
    integer counter on the model's `by_model` row (cost_gateway_calls,
    cost_generation_calls, cost_table_calls, cost_unpriced_calls), so rows
    from every engine still merge.
  - The key never appears in a message: it is replaced by *** in every
    error and stderr the engine raises. The program is handed only the
    environment it needs (PROGRAM_ENV: PATH, HOME, temp dir, locale,
    NODE_*, proxies, the key) and the config's `env`; the other
    credentials in the process (Anthropic, OpenAI, Codex) stay out of it.

Requires: `node` (or $YT_GATEWAY_NODE) at 22 or later, and gateway/
installed with `cd gateway && npm ci --ignore-scripts`. `npm ci` needs
gateway/package-lock.json; until it is committed, `npm install
--package-lock-only --ignore-scripts` writes it first (LOCK_HINT).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, Iterator, Sequence

from claude_engine import (PERMANENT_MARKERS, VIRTUAL_PROVIDER, Answer, ClaudeEngine,
                           ClaudeError, ClaudeExecutionError, ClaudeNotFound, EngineConfig,
                           Event, _loads_loose, canonical_model, default_for)
from ytstudio import gateway as _gateway

__all__ = ["GatewayEngine", "GatewayConfig", "GatewayExecutionError", "GatewayNotFound",
           "AI_VERSION", "MIN_NODE", "PROGRAM_DIR", "PROGRAM", "MEDIA_TYPES",
           "GATEWAY_PERMANENT_MARKERS", "model_problem", "media_type", "media_item",
           "node_major",
           "installed_ai_version", "price_for"]

#: The `ai` release gateway/package.json pins and the research verified.
AI_VERSION = "7.0.150"
#: `ai` 7 declares engines node >=22.
MIN_NODE = 22
#: Where the Node program and its node_modules live.
PROGRAM_DIR = Path(__file__).resolve().parent / "gateway"
PROGRAM = "gateway-engine.mjs"
INSTALL_HINT = "cd gateway && npm ci --ignore-scripts"
#: `npm ci` refuses to run without a lockfile; until gateway/package-lock.json
#: is committed, the first install writes one (no package is installed and no
#: script runs), then installs from it.
LOCK_HINT = ("cd gateway && npm install --package-lock-only --ignore-scripts "
             "&& npm ci --ignore-scripts")
#: Names the node binary when `executable` is left at "node".
NODE_ENV = "YT_GATEWAY_NODE"
#: Price rows for gateway ids, over spend's table: '{"google/gemini-3.6-flash":
#: {"input": 0.75, "cached_input": 0.075, "output": 3.75}}', USD per 1M tokens.
PRICES_ENV = "GATEWAY_PRICES"

#: The media a request may carry, by extension. Anything else is refused
#: before the call, rather than sent with a guessed type.
MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".mp4": "video/mp4", ".webm": "video/webm",
    ".mov": "video/quicktime", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
    ".wav": "audio/wav", ".pdf": "application/pdf",
}

#: Failures that a retry cannot fix, whatever status or retryable flag came
#: with them. A key budget's 402 (`quota_for_entity_exceeded`) may reach the
#: SDK as a retryable 500, so its text decides. claude_engine's own markers
#: apply too.
GATEWAY_PERMANENT_MARKERS = ("quota_for_entity_exceeded", "quota limit exceeded",
                             "budget exceeded")

#: HTTP statuses worth another attempt, when the gateway also says retryable.
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})

#: How a call's cost was found; each is an integer counter on its by_model row.
COST_SOURCES = ("gateway", "generation", "table", "unpriced")

#: The program's exit codes.
EXIT_OK, EXIT_OTHER, EXIT_SCHEMA, EXIT_GATEWAY, EXIT_PROVIDERS = 0, 1, 2, 3, 4


# --------------------------------------------------------------------------- #
# errors
# --------------------------------------------------------------------------- #

class GatewayNotFound(ClaudeNotFound):
    """The `node` executable could not be located."""


class GatewayExecutionError(ClaudeExecutionError):
    """The gateway program ran but did not answer, or the call was refused
    before it started. `transient` is always given; `kind` is the program's
    classification (schema, no_output, gateway, providers_unavailable,
    other) and `status` the HTTP status, when there was one."""

    def __init__(self, message: str, *, transient: bool, kind: str | None = None,
                 status: int | None = None, **kwargs: Any) -> None:
        super().__init__(message, transient=transient, **kwargs)
        self.kind = kind
        self.status = status


def _permanent(message: str, **kwargs: Any) -> GatewayExecutionError:
    return GatewayExecutionError(message, transient=False, **kwargs)


def _marked_permanent(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in PERMANENT_MARKERS + GATEWAY_PERMANENT_MARKERS)


def _scrub(text: str, key: str) -> str:
    return text.replace(key, "***") if key else text


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #

@dataclass
class GatewayConfig(EngineConfig):
    """EngineConfig plus what only the gateway program has.

    Unlike EngineConfig, nothing is filled from claude_engine's defaults
    (CLAUDE_ENGINE_EFFORT and the like): those are the claude CLI's, and an
    effort or fallback model would be refused here."""

    executable: str = "node"            # $YT_GATEWAY_NODE when left "node"
    providers: Sequence[str] = ()       # sent as `only`; () pins none
    has: Sequence[str] = ()             # capabilities a provider must have
    cost_poll_tries: int = 0            # polls of /v1/generation; 0 = never
    cost_poll_interval_ms: int = 2000
    max_media_mb: float = 20            # all media of one request together
    program_dir: str | Path | None = None   # None: PROGRAM_DIR

    def __post_init__(self) -> None:
        self.providers = _names("providers", self.providers)
        self.has = _names("has", self.has)


def _names(field_name: str, value: Any) -> tuple[str, ...]:
    """A list of provider or capability names as a tuple. A bare string is
    one name: tuple("google") would send its letters as six providers."""
    if value is None:
        return ()
    if isinstance(value, str):
        value = (value,) if value else ()
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        raise TypeError(f"GatewayConfig.{field_name} takes a name or a list of names, "
                        f"not {value!r}")
    return tuple(value)


def _as_gateway_config(config: EngineConfig) -> GatewayConfig:
    """A plain EngineConfig as a GatewayConfig. Its effort and fallback
    model are dropped when they are only claude's defaults (set_defaults,
    $CLAUDE_ENGINE_EFFORT), which EngineConfig filled in, not the caller;
    one the caller chose stays, and is refused when the engine asks."""
    if isinstance(config, GatewayConfig):
        return config
    values = {f.name: getattr(config, f.name) for f in fields(EngineConfig)}
    if values.get("executable") == "claude":
        values["executable"] = "node"
    for name in ("effort", "fallback_model"):
        if values[name] is not None and values[name] == default_for(name):
            values[name] = None
    return GatewayConfig(**values)


# --------------------------------------------------------------------------- #
# checks the engine and `yt doctor` share
# --------------------------------------------------------------------------- #

def model_problem(model: Any) -> str | None:
    """Why `model` cannot be asked through the gateway engine, or None. The
    id must be `provider/model`; a virtual model (`vmc/<slug>`) may route to
    any family, and a Claude Code picker id (`claude-code/...`) is the
    claude CLI's."""
    text = str(model or "").strip()
    lead = (f"the gateway engine takes a model in provider/model form "
            f"(google/gemini-3.6-flash), not {text!r}")
    if text.lower().startswith("claude-code/"):
        return lead + ": claude-code/ ids belong to the claude CLI's gateway route"
    provider, name = canonical_model(text)
    if provider == VIRTUAL_PROVIDER:
        return lead + ": a virtual model (vmc/) may route to any model"
    if not provider or not name:
        return lead
    return None


def media_type(path: str | Path) -> str | None:
    """The media type of a file the request may carry, by its extension."""
    return MEDIA_TYPES.get(Path(path).suffix.lower())


def media_item(entry: Any) -> tuple[Any, str]:
    """A media entry's path and label: a path alone (no label), or a dict
    {"path": ..., "label": ...} whose label names the file to the model."""
    if isinstance(entry, dict):
        return entry["path"], str(entry.get("label") or "")
    return entry, ""


#: `node --version` per executable path, read once per process.
_NODE_VERSIONS: dict[str, str | None] = {}


def node_version(executable: str) -> str | None:
    """`node --version` ("v24.11.0"), or None when it would not say."""
    if executable not in _NODE_VERSIONS:
        try:
            proc = subprocess.run([executable, "--version"], capture_output=True,
                                  text=True, timeout=15)
            text = proc.stdout.strip() if proc.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            text = None
        _NODE_VERSIONS[executable] = text or None
    return _NODE_VERSIONS[executable]


def node_major(executable: str) -> int | None:
    found = re.match(r"v?(\d+)\.", node_version(executable) or "")
    return int(found.group(1)) if found else None


def installed_ai_version(program_dir: str | Path | None = None) -> str | None:
    """The `ai` version installed beside the program, or None."""
    path = Path(program_dir or PROGRAM_DIR) / "node_modules" / "ai" / "package.json"
    try:
        version = json.loads(path.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return str(version) if version else None


#: The environment the Node program is handed, besides the config's `env`:
#: what Node needs to start, find its temp dir and locale, reach the gateway
#: through a proxy or a private CA (NODE_EXTRA_CA_CERTS, NODE_OPTIONS), and
#: the key. Nothing else.
PROGRAM_ENV = frozenset({"PATH", "HOME", "USERPROFILE", "TMPDIR", "TEMP", "TMP", "LANG",
                         "LANGUAGE", "TZ", "SYSTEMROOT", "SSL_CERT_FILE", "SSL_CERT_DIR",
                         _gateway.KEY,
                         "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY"})
PROGRAM_ENV_PREFIXES = ("LC_", "NODE_")


def passed_to_program(name: str) -> bool:
    """Whether the variable `name` reaches the Node program. Proxy names
    count in either case, as curl and undici read both."""
    upper = name.upper()
    return ((upper if upper.endswith("PROXY") else name) in PROGRAM_ENV
            or name.startswith(PROGRAM_ENV_PREFIXES))


def has_lockfile(program_dir: str | Path | None = None) -> bool:
    return (Path(program_dir or PROGRAM_DIR) / "package-lock.json").is_file()


def install_hint(program_dir: str | Path | None = None) -> str:
    """The command that installs gateway/: `npm ci` from the lockfile, or,
    while there is none, the command that writes it first."""
    return INSTALL_HINT if has_lockfile(program_dir) else LOCK_HINT


def not_installed(installed: str | None, program_dir: str | Path | None = None) -> str:
    """What is wrong with gateway/node_modules when it does not hold AI_VERSION."""
    said = f"gateway/node_modules holds ai {installed or 'nothing'}, not {AI_VERSION}"
    if not has_lockfile(program_dir):
        said += " (no lockfile yet: gateway/package-lock.json is written on the first install)"
    return said


def node_executable(environ: dict | None = None, executable: str = "node") -> str:
    """The node to run: `executable`, or $YT_GATEWAY_NODE when that is "node"."""
    environ = os.environ if environ is None else environ
    if not executable or executable == "node":
        return environ.get(NODE_ENV) or "node"
    return executable


def price_for(model: Any, env: dict | None = None) -> dict[str, float] | None:
    """The price row for a gateway id: $GATEWAY_PRICES by the id as given or
    as `provider/model` after canonical spelling, then spend.price_for (its
    table, the engine's, $CLAUDE_PRICES), which prices an Anthropic gateway
    id by its direct id. None when nobody priced it."""
    env = os.environ if env is None else env
    raw = env.get(PRICES_ENV)
    if raw:
        try:
            table = json.loads(raw)
        except ValueError:
            table = None
        if isinstance(table, dict):
            provider, name = canonical_model(str(model or ""))
            for each in (str(model or ""), f"{provider}/{name}"):
                row = table.get(each)
                if isinstance(row, dict) and row:
                    return row
    from ytstudio import spend
    return spend.price_for(model, env=env)


def table_cost(tokens: dict[str, int], row: dict[str, float]) -> float:
    """USD for `tokens` at a per-1M-token price row."""
    return (tokens.get("input_tokens", 0) * row.get("input", 0)
            + tokens.get("cache_read_input_tokens", 0) * row.get("cached_input", 0)
            + tokens.get("cache_creation_input_tokens", 0)
            * row.get("cache_creation", row.get("input", 0))
            + tokens.get("output_tokens", 0) * row.get("output", 0)) / 1_000_000


def _tokens(usage: Any) -> dict[str, int]:
    """The AI SDK 7 usage as the claude CLI's four counters. inputTokens
    includes the cache reads and writes, so they are taken out of it. The
    field names are from the v7 migration guide, unchecked against a live
    call."""
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get("inputTokenDetails") or {}
    read = int(details.get("cacheReadTokens") or 0)
    write = int(details.get("cacheWriteTokens") or 0)
    return {"input_tokens": max(0, int(usage.get("inputTokens") or 0) - read - write),
            "output_tokens": int(usage.get("outputTokens") or 0),
            "cache_read_input_tokens": read, "cache_creation_input_tokens": write}


def _last_json_line(stdout: str) -> dict | None:
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and "ok" in value:
            return value
    return None


def _same_model(a: str, b: str, anthropic: bool) -> bool:
    if anthropic:
        a, b = (re.sub(r"(?<=\d)\.(?=\d)", "-", x) for x in (a, b))
    return a.lower() == b.lower()


# --------------------------------------------------------------------------- #
# the engine
# --------------------------------------------------------------------------- #

class GatewayEngine(ClaudeEngine):
    """Vercel AI Gateway, through the AI SDK, as a single-decision engine with
    ClaudeEngine's API.

    >>> GatewayEngine(GatewayConfig(model="google/gemini-3.6-flash")).ask_json(
    ...     "Is the voice clipped?", schema, media=["mix.wav"])
    """

    def __init__(self, config: EngineConfig | None = None, **overrides: Any) -> None:
        super().__init__(_as_gateway_config(config or GatewayConfig()), **overrides)

    def spawn(self, **overrides: Any) -> "GatewayEngine":
        return type(self)(replace(self.config), **overrides)

    # -- public API --------------------------------------------------------

    def ask(self, prompt: str, *, resume: bool | str = False,
            tools: Sequence[str] | None | type(...) = ...,
            system: str | None = None, schema: dict | None = None,
            media: Sequence[str | Path] = ()) -> Answer:
        """One prompt, with `media` attached, one answer."""
        if resume:
            raise _permanent("the gateway engine keeps no session to resume")
        tool_set = self.config.tools if tools is ... else tools
        if tool_set:
            raise _permanent(f"the gateway engine runs no tools ({', '.join(tool_set)}): "
                             "it answers one decision; an inspection stays on claude")
        argv, request, env = self._prepare(prompt, system=system, schema=schema, media=media)
        return self._call(argv, request, env, schema)

    def ask_json(self, prompt: str, schema: dict, *, system: str | None = None,
                 media: Sequence[str | Path] = (), **kwargs: Any) -> Any:
        """The parsed answer, unchecked: `Harness.decide` validates it and
        asks once more, so a reply that breaks the schema is not raised here."""
        return self.ask(prompt, schema=schema, system=system, media=media, **kwargs).json()

    def stream(self, prompt: str, **kwargs: Any) -> Iterator[Event]:
        raise _permanent("the gateway engine answers single decisions; it cannot stream "
                         "an agent (pass the evidence files to decide as media)")

    def agent(self, prompt: str, **kwargs: Any) -> Answer:
        raise _permanent("the gateway engine answers single decisions; it runs no agent")

    def process_timeout(self) -> float:
        """The subprocess's limit: the call's own, 30 s for Node to start and
        stop, and the cost poll's longest wait."""
        cfg = self.config
        return ((cfg.timeout or 900.0) + 30
                + cfg.cost_poll_tries * cfg.cost_poll_interval_ms / 1000)

    # -- preparation -------------------------------------------------------

    def _env(self) -> dict[str, str]:
        """What the engine reads its settings from: the process's
        environment and the config's `env` over it."""
        env = dict(os.environ)
        env.update(self.config.env or {})
        return env

    def _program_env(self) -> dict[str, str]:
        """What the program is handed: the names `passed_to_program` lets
        through, and the config's `env` as given. The program reads only the
        key; the other credentials here (Anthropic, OpenAI, Codex, ...) have
        no reason to reach an npm-installed process."""
        env = {k: v for k, v in os.environ.items() if passed_to_program(k)}
        env.update(self.config.env or {})
        return env

    def _transport_env(self, environ: dict[str, str]) -> dict[str, str]:
        """Nothing: the claude CLI's gateway variables are not this program's."""
        return {}

    def _media(self, media: Sequence[str | Path | dict]) -> list[dict[str, str]]:
        """Each file as the program reads it. An entry may be a dict,
        {"path": ..., "label": "Cut A:"}: the program sends the label as a
        text part immediately before that file, so a model shown two files
        can tell which is which. A plain path is sent exactly as before."""
        files, total = [], 0
        for entry in media or ():
            item, label = media_item(entry)
            path = Path(item).expanduser().resolve()
            if not path.is_file():
                raise _permanent(f"media file {item} does not exist")
            kind = media_type(path)
            if kind is None:
                raise _permanent(f"media file {path.name} has an extension ({path.suffix}) "
                                 f"the gateway engine does not send: one of "
                                 f"{', '.join(sorted(MEDIA_TYPES))}")
            total += path.stat().st_size
            files.append({"path": str(path), "mediaType": kind,
                          **({"label": label} if label else {})})
        cap = self.config.max_media_mb
        if files and total > cap * 1024 * 1024:
            raise _permanent(f"the media come to {total / 1024 / 1024:.1f} MB, over "
                             f"max_media_mb={cap:g}; send less, or downscale")
        return files

    def _prepare(self, prompt: str, *, system: str | None, schema: dict | None,
                 media: Sequence[str | Path]) -> tuple[list[str], dict, dict[str, str]]:
        """The argv, request and environment of one call, after every
        precondition holds. Nothing has started when one fails."""
        cfg = self.config
        env = self._env()
        if not (env.get(_gateway.KEY) or "").strip():
            raise _permanent(f"the gateway engine needs the gateway key in {_gateway.KEY}")
        problem = model_problem(cfg.model)
        if problem:
            raise _permanent(problem)
        if cfg.effort or cfg.fallback_model:
            raise _permanent("the gateway engine takes no effort or fallback model: neither "
                             "reaches the request (a Harness with engine='gateway' clears both)")
        files = self._media(media)
        exe = node_executable(env, cfg.executable)
        node = shutil.which(exe)
        if not node:
            raise GatewayNotFound(f"{exe!r} not found. Install Node {MIN_NODE} or later, "
                                  f"or set ${NODE_ENV}.")
        major = node_major(node)
        if major is None or major < MIN_NODE:
            raise _permanent(f"{node} is Node {node_version(node) or 'of unknown version'}; "
                             f"the gateway engine needs Node {MIN_NODE} or later")
        program_dir = Path(cfg.program_dir or PROGRAM_DIR)
        installed = installed_ai_version(program_dir)
        if installed != AI_VERSION:
            raise _permanent(f"{not_installed(installed, program_dir)}: "
                             f"run `{install_hint(program_dir)}`")
        if cfg.max_turns_budget_usd is not None and price_for(cfg.model, env) is None:
            raise _permanent(f"refusing to start: the ${cfg.max_turns_budget_usd:g} budget "
                             f"cannot be held, {cfg.model!r} has no price. Set its row in "
                             f"${PRICES_ENV}.")
        try:
            host = _gateway.base_url(env)
        except _gateway.GatewayError as exc:
            raise _permanent(f"gateway: {exc}") from exc
        options: dict[str, list[str]] = {}
        if cfg.providers:
            options["only"] = list(cfg.providers)
        if cfg.has:
            options["has"] = list(cfg.has)
        request: dict[str, Any] = {
            "model": cfg.model,
            "instructions": system if system is not None else (cfg.system_prompt or ""),
            "prompt": prompt, "schema": schema, "schemaName": "answer",
            "media": files, "gateway": options,
            "timeoutMs": int((cfg.timeout or 900.0) * 1000),
            "maxRetries": 0,
            "costPoll": {"tries": int(cfg.cost_poll_tries),
                         "intervalMs": int(cfg.cost_poll_interval_ms)},
        }
        if host:
            request["host"] = host
        return [node, str(program_dir / PROGRAM)], request, self._program_env()

    # -- the call ----------------------------------------------------------

    def _call(self, argv: list[str], request: dict, env: dict[str, str],
              schema: dict | None) -> Answer:
        cfg = self.config
        key = (env.get(_gateway.KEY) or "").strip()
        stdin = json.dumps(request, ensure_ascii=False)
        spent = 0.0
        last: GatewayExecutionError | None = None
        for attempt in range(cfg.max_retries + 1):
            started = time.monotonic()
            try:
                proc = subprocess.run(argv, input=stdin, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace",
                                      timeout=self.process_timeout(), env=env)
            except subprocess.TimeoutExpired:
                last = GatewayExecutionError(
                    f"the gateway program timed out after {self.process_timeout():g}s",
                    transient=True, kind="timeout")
            else:
                duration = int((time.monotonic() - started) * 1000)
                if cfg.verbose and proc.stderr:
                    print(_scrub(proc.stderr, key), file=sys.stderr, end="")
                line = _last_json_line(proc.stdout)
                if line is not None and line.get("ok"):
                    return self._answer(line, schema, duration)
                last, cost = self._failure(line, proc, duration, key)
                spent += cost
            if not last.transient:
                raise last
            budget = cfg.max_turns_budget_usd
            if budget is not None and spent >= budget:
                raise _permanent(f"budget exceeded: ${spent:.4f} booked against the "
                                 f"${budget:g} cap before another attempt ({last})")
            if not self._sleep_before_retry(attempt):
                raise last
        raise last or _permanent("the gateway program failed for an unknown reason")

    def _cost(self, line: dict, tokens: dict[str, int]) -> tuple[float, str]:
        """The call's cost and where it came from (COST_SOURCES)."""
        for field_name, source, wanted in (("cost", "gateway", True),
                                           ("generationCost", "generation",
                                            self.config.cost_poll_tries > 0)):
            if wanted and line.get(field_name) is not None:
                try:
                    return float(line[field_name]), source
                except (TypeError, ValueError):
                    pass
        row = price_for(self.config.model, self._env())
        if row:
            return table_cost(tokens, row), "table"
        return 0.0, "unpriced"

    def _payload(self, line: dict, model: str, duration: int, **more: Any) -> dict:
        """A claude-shaped result payload, so `Usage.add` books it unchanged."""
        tokens = _tokens(line.get("usage"))
        cost, source = self._cost(line, tokens)
        return {"type": "result", "num_turns": 1, "duration_ms": duration,
                "total_cost_usd": cost, "usage": tokens, "model": model,
                "modelUsage": {model: {
                    "inputTokens": tokens["input_tokens"],
                    "outputTokens": tokens["output_tokens"],
                    "cacheReadInputTokens": tokens["cache_read_input_tokens"],
                    "cacheCreationInputTokens": tokens["cache_creation_input_tokens"],
                    "costUSD": cost}},
                "cost_source": source, "generation_id": line.get("generationId"), **more}

    def _absorb_result(self, payload: dict) -> None:
        """Book the payload as every engine does (`Usage.add`, unchanged),
        then count where its cost came from on the answering model's row.
        `_to_answer` absorbs a payload once, so each call counts once; the
        per-call `Answer.usage` is built by `Usage.add` alone and has no
        counter, while the engine's `usage`, which the harness books, does."""
        super()._absorb_result(payload)
        source, model = payload.get("cost_source"), payload.get("model")
        if source in COST_SOURCES and model in self.usage.by_model:
            row = self.usage.by_model[model]
            name = f"cost_{source}_calls"
            row[name] = row.get(name, 0) + 1

    def _answer(self, line: dict, schema: dict | None, duration: int) -> Answer:
        served = str(line.get("modelId") or self.config.model)
        output = line.get("output")
        payload = self._payload(
            line, served, duration, subtype="success", is_error=False,
            result=output if isinstance(output, str) else json.dumps(output, ensure_ascii=False),
            structured_output=output if schema is not None else None,
            stop_reason=line.get("finishReason"), routing=line.get("routing"))
        if schema is not None and isinstance(output, str):
            try:
                payload["structured_output"] = _loads_loose(output)
            except ValueError:
                pass
        other = self._other_model(served, line.get("routing"))
        if other:
            self._absorb_result(payload)       # it was paid for either way
            raise _permanent(f"the gateway answered with {other}, not {self.config.model}: "
                             "a different model is not this decision's", payload=payload)
        return self._to_answer(payload)

    def _other_model(self, served: str, routing: Any) -> str | None:
        """The first model, served or attempted, that is not the one asked
        for. The model parts must match; the providers only when both say."""
        want_provider, want = canonical_model(self.config.model)
        names = [served]
        if isinstance(routing, dict):
            names += [a.get("canonicalSlug") for a in routing.get("modelAttempts") or []
                      if isinstance(a, dict) and a.get("canonicalSlug")]
        for name in names:
            provider, model = canonical_model(str(name))
            anthropic = "anthropic" in (provider, want_provider)
            if not _same_model(model, want, anthropic) or (
                    provider and want_provider and provider != want_provider):
                return str(name)
        return None

    def _failure(self, line: dict | None, proc: subprocess.CompletedProcess, duration: int,
                 key: str) -> tuple[GatewayExecutionError, float]:
        """The error for an attempt that did not answer, and what it booked.
        The rules run in order and the first that matches decides."""
        stderr = _scrub((proc.stderr or "").strip()[-4000:], key)
        code = proc.returncode
        if line is None:
            if "ERR_MODULE_NOT_FOUND" in stderr:
                return _permanent(f"the gateway program could not load its packages: run "
                                  f"`{install_hint(self.config.program_dir)}`", returncode=code,
                                  stderr=stderr), 0.0
            transient = not _marked_permanent(stderr)
            return GatewayExecutionError(
                f"the gateway program exited with code {code} without an answer",
                transient=transient, returncode=code, stderr=stderr, kind="crash"), 0.0
        error = line.get("error") if isinstance(line.get("error"), dict) else {}
        kind = str(error.get("kind") or "other")
        message = _scrub(str(error.get("message") or ""), key)
        status = error.get("status") if isinstance(error.get("status"), int) else None
        spent = 0.0
        if isinstance(line.get("usage"), dict):
            payload = self._payload(line, str(self.config.model), duration,
                                    subtype=f"error_{kind}", is_error=True, result=message)
            self._absorb_result(payload)
            spent = payload["total_cost_usd"]
        said = f"gateway {kind}" + (f" {status}" if status else "") + f": {message}"
        common = {"returncode": code, "stderr": stderr, "kind": kind, "status": status}
        if _marked_permanent(f"{message} {stderr}"):
            return _permanent(said, **common), spent
        if kind == "providers_unavailable":
            pinned = ", ".join(self.config.providers) or "none pinned"
            return _permanent(f"none of the providers pinned with only ({pinned}) can serve "
                              f"{self.config.model}: widen `providers` ({message})",
                              **common), spent
        if kind in ("schema", "no_output"):
            return _permanent(said, **common), spent
        if kind == "gateway":
            transient = status in TRANSIENT_STATUSES and bool(error.get("retryable"))
            return GatewayExecutionError(said, transient=transient, **common), spent
        if error.get("timeout"):
            return GatewayExecutionError(said, transient=True, **common), spent
        if "ERR_MODULE_NOT_FOUND" in f"{message} {stderr}":
            return _permanent(f"{said}; run `{install_hint(self.config.program_dir)}`",
                              **common), spent
        return GatewayExecutionError(said, transient=True, **common), spent


# --------------------------------------------------------------------------- #
# CLI: the live smoke test
# --------------------------------------------------------------------------- #

def _read_prompt(text: str | None) -> str:
    if text:
        return text
    if sys.stdin.isatty():
        raise SystemExit("error: give a prompt, or pipe one on stdin")
    return sys.stdin.read()


def _read_schema(source: str) -> dict:
    path = Path(source)
    return json.loads(path.read_text() if path.is_file() else source)


def _cmd_json(args: argparse.Namespace) -> int:
    engine = GatewayEngine(GatewayConfig(
        model=args.model, executable=args.executable, timeout=args.timeout,
        max_turns_budget_usd=args.budget, providers=tuple(args.provider or ()),
        has=tuple(args.has or ()), verbose=args.verbose))
    result = engine.ask_json(_read_prompt(args.prompt), _read_schema(args.schema),
                             media=args.media or ())
    print(json.dumps(result, indent=2, ensure_ascii=False))
    for payload in engine.log:
        print(f"[{payload.get('generation_id')}: ${payload.get('total_cost_usd') or 0:.6f} "
              f"from {payload.get('cost_source')}]", file=sys.stderr)
    print(f"[{engine.usage}]", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gateway_engine",
        description="Ask one structured question through Vercel AI Gateway (the live "
                    "smoke test of the opt-in gateway engine).")
    parser.add_argument("--executable", default="node",
                        help=f"the node binary (${NODE_ENV}, then node on PATH)")
    parser.add_argument("--timeout", type=float, default=900.0,
                        help="seconds per call (default 900)")
    parser.add_argument("--budget", type=float, help="max USD for one call")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="echo the program's stderr")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("json", help="one answer against a JSON schema")
    p.add_argument("prompt", nargs="?")
    p.add_argument("--schema", required=True, help="JSON Schema file or literal")
    p.add_argument("--model", required=True, help="a gateway id, provider/model")
    p.add_argument("--media", action="append", metavar="FILE",
                   help="attach a file (repeatable)")
    p.add_argument("--provider", action="append", metavar="P",
                   help="serve only from this provider (repeatable)")
    p.add_argument("--has", action="append", metavar="CAP",
                   help="a capability every provider must have (repeatable)")
    p.set_defaults(func=_cmd_json)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ClaudeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if isinstance(exc, ClaudeExecutionError) and exc.stderr:
            print(exc.stderr, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
