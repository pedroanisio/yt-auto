#!/usr/bin/env python3
"""
codex_engine.py -- the claude_engine API, driven by the OpenAI Codex CLI.

`CodexEngine` is a drop-in for `claude_engine.ClaudeEngine`: the same
ask / ask_json / stream / agent / spawn / absorb / reset / log, the same
`Answer`, `Usage` and `Event` objects, the same error classes, and it takes
the same `EngineConfig` -- so code written against ClaudeEngine (the
`ytstudio.harness.ENGINE` seam above all: `YT_ENGINE=codex`) runs on Codex
unchanged. Zero dependencies: it launches `codex exec --json` (non-
interactive) over a subprocess pipe, prompt on stdin, and maps the JSONL
events onto claude_engine's.

    python3 codex_engine.py ask   "Explain this repo's layout"
    python3 codex_engine.py json  "List 3 risks" --schema schema.json
    python3 codex_engine.py agent "Add type hints to utils.py" --write
    python3 codex_engine.py chat | run tasks.json [--jobs 4]

Requires `codex` on PATH (or `executable=` / $CODEX_ENGINE_EXECUTABLE),
authenticated (`codex login`). Verified against codex-cli 0.163.0.

Feature map (S = supported by a Codex flag, E = emulated here, N = no Codex
meaning: accepted, ignored, one warning when set to a non-default value):

    ClaudeEngine / EngineConfig     Codex                                   how
    ------------------------------  --------------------------------------  ---
    ask(prompt)                     codex exec --json -  (prompt on stdin)   S
    ask_json / schema=              --output-schema <tmp file>; the schema   E
                                    is made strict first (OpenAI demands
                                    additionalProperties:false and every
                                    property required: optional ones become
                                    nullable and a null is dropped from the
                                    answer; unsupported keywords such as
                                    default/minLength are stripped). A schema
                                    that cannot be strict (free-form object,
                                    non-object root) or that the API rejects
                                    is asked in the prompt instead, parsed
                                    loosely (claude_engine._loads_loose).
                                    Required fields are checked either way.
    stream / agent                  --json events -> Event: agent_message   S
                                    -> assistant text, reasoning -> thinking,
                                    command_execution -> tool_use "Bash"
                                    {command} + tool_result, file_change ->
                                    "Write"/"Edit" {file_path}, mcp_tool_call
                                    -> "mcp__server__tool", web_search ->
                                    "WebSearch", todo_list -> "TodoWrite",
                                    thread.started -> system/init,
                                    turn.completed -> result
    tools                           sandbox: Write/Edit/MultiEdit/            E
                                    NotebookEdit/Bash -> workspace-write
                                    (Bash also turns on network access);
                                    otherwise (incl. tools=() and None) ->
                                    read-only. Codex cannot remove its shell:
                                    read-only is the closest to "no tools".
    skip_permissions                -s danger-full-access (only this; never   S
                                    --dangerously-bypass-approvals-and-sandbox)
    permission_mode                 "plan" -> read-only; others no effect     E
    system_prompt / append_...      -c developer_instructions=... (on top of  S
                                    Codex's base prompt, not replacing it);
                                    above INSTRUCTIONS_ARG_LIMIT bytes it is
                                    prepended to the prompt under
                                    SYSTEM_HEADING instead                    E
    model                           -m; a Claude name (sonnet, opus, fable,   S/E
                                    haiku, claude-*) is not passed: Codex's
                                    default (or $CODEX_ENGINE_MODEL) answers
    effort                          -c model_reasoning_effort; xhigh/max are  S/E
                                    passed only when ~/.codex/models_cache
                                    lists them for the model, else -> high
    images (CodexConfig / kwarg)    -i FILE                                  S
    cwd / add_dirs                  -C + process cwd / --add-dir; on resume   S
                                    -c sandbox_mode / writable_roots
    persist_session=False           --ephemeral                              S
    resume / fork_session           codex exec resume|fork <id>              S
    timeout, max_retries            watchdog + is_transient retries (ours)   E
    env, executable                 child env / binary ("claude" -> codex)   S
    usage / cost_usd                tokens from turn.completed; cost from     E
                                    PRICES (per 1M tokens), overridable by
                                    $CODEX_PRICES (JSON) or
                                    CodexConfig.prices; unknown model ->
                                    cost None in the log + a warning
    max_turns_budget_usd            enforced in the stream: when priced usage E
                                    passes it the process is killed and
                                    ClaudeExecutionError(error_max_budget_usd)
                                    raised. Codex reports usage once per turn,
                                    so a single exec can overrun before it is
                                    stopped. An unpriced model (a ChatGPT
                                    sign-in's default has no public price)
                                    cannot be held in dollars: a budgeted
                                    call on it is REFUSED before codex starts
                                    (CodexBudgetRefused) unless a price is
                                    configured or a token cap is set
    max_budget_tokens               per-call cap on input + output tokens,    E
                                    or $CODEX_BUDGET_TOKENS; works for priced
                                    and unpriced models; same stop as above
                                    (error_max_budget_tokens)
    session id pinning              not available: the id is Codex's          N
                                    thread_id, known once thread.started
    fallback_model, betas, system_prompt_snapshot,                            N
    exclude_dynamic_system_prompt_sections, agent, allowed_tools,
    disallowed_tools, restricted, disable_slash_commands, mcp_config, agents,
    plugin_dirs, plugin_urls, settings, autocompact, name, setting_sources,
    safe_mode, bare, include_partial_messages, include_hook_events,
    forward_subagent_text, debug, debug_file. (strict_mcp_config and
    auto_allow_tools are no-ops: Codex loads the MCP servers of its own
    config.toml, and the sandbox, not an allow-list, decides.)

The defaults for `None` fields come from `codex_engine.set_defaults()`, then
$CODEX_ENGINE_<FIELD> (CODEX_ENGINE_MODEL, CODEX_ENGINE_EFFORT, ...), then
Codex's own config -- its default model is read from $CODEX_HOME/config.toml
(`model`), else the first listed model in $CODEX_HOME/models_cache.json, for
pricing and `by_model`. Task / TaskResult / TaskRunner are claude_engine's own
(they take any engine with this API).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import warnings
from dataclasses import MISSING, dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from claude_engine import (DEFAULTABLE, EDIT_TOOLS, FULL_TOOLS, PERMANENT_MARKERS,
                           READ_ONLY_TOOLS, Answer, ClaudeEngine, ClaudeError,
                           ClaudeExecutionError, ClaudeNotFound, EngineConfig, Event,
                           Task, TaskResult, TaskRunner, Usage, _loads_loose)

__all__ = [
    "CodexEngine", "CodexConfig", "EngineConfig", "DEFAULTS", "DEFAULTABLE",
    "set_defaults", "default_for", "PRICES", "price_for", "strict_schema",
    "drop_nulls", "Answer", "Usage", "Event", "Task", "TaskResult", "TaskRunner",
    "ClaudeError", "ClaudeNotFound", "ClaudeExecutionError", "CodexNotFound",
    "CodexExecutionError", "CodexBudgetRefused", "CodexEngineWarning", "is_transient",
    "READ_ONLY_TOOLS", "EDIT_TOOLS", "FULL_TOOLS",
]

#: USD per 1M tokens: input (uncached), cached input, output (reasoning
#: included). OpenAI's published API prices for the models listed; a model
#: missing here is recorded with tokens and a cost of None. Extend or correct
#: it with $CODEX_PRICES='{"model": {"input": .., "cached_input": .., "output": ..}}'
#: or CodexConfig(prices=...). Lookup is by exact model id.
PRICES: dict[str, dict[str, float]] = {
    "gpt-5":              {"input": 1.25, "cached_input": 0.125, "output": 10.00},
    "gpt-5-codex":        {"input": 1.25, "cached_input": 0.125, "output": 10.00},
    "gpt-5-mini":         {"input": 0.25, "cached_input": 0.025, "output": 2.00},
    "gpt-5-nano":         {"input": 0.05, "cached_input": 0.005, "output": 0.40},
    "gpt-5.1":            {"input": 1.25, "cached_input": 0.125, "output": 10.00},
    "gpt-5.1-codex":      {"input": 1.25, "cached_input": 0.125, "output": 10.00},
    "gpt-5.1-codex-mini": {"input": 0.25, "cached_input": 0.025, "output": 2.00},
    "gpt-5.2":            {"input": 1.75, "cached_input": 0.175, "output": 14.00},
    "o3":                 {"input": 2.00, "cached_input": 0.50, "output": 8.00},
    "o4-mini":            {"input": 1.10, "cached_input": 0.275, "output": 4.40},
    "gpt-4.1":            {"input": 2.00, "cached_input": 0.50, "output": 8.00},
}

#: Tools that change files or run code: they need a writable sandbox.
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"})

#: Efforts every Codex model takes; the rest only when the model lists them.
BASE_EFFORTS = ("minimal", "low", "medium", "high")

#: Above this many bytes a system prompt goes in the prompt, not in argv
#: (Linux refuses a single argument over 128 KiB).
INSTRUCTIONS_ARG_LIMIT = 100_000
SYSTEM_HEADING = "# Instructions (system prompt)\n\n"
SCHEMA_INSTRUCTION = ("\n\nReply with ONLY one JSON value (no prose, no code fence) "
                      "valid against this JSON schema:\n%s")

#: Keywords OpenAI's strict structured output refuses; stripped from a schema.
UNSUPPORTED_KEYWORDS = frozenset({
    "default", "examples", "minLength", "maxLength", "minProperties",
    "maxProperties", "uniqueItems", "$schema", "$id", "contentEncoding",
    "contentMediaType", "dependentRequired", "dependentSchemas"})

#: Codex/OpenAI failures that a retry cannot fix (claude_engine's list too).
CODEX_PERMANENT_MARKERS = (
    "invalid_request_error", "invalid_json_schema", "invalid schema",
    "usage_not_included", "quota_exceeded", "insufficient_quota",
    "context_window_exceeded", "unexpected argument", "invalid value",
    "not logged in", "please log in", "codex login", "error_max_budget_usd")

#: EngineConfig fields with no Codex meaning: accepted, ignored, warned once.
IGNORED_FIELDS = (
    "fallback_model", "betas", "system_prompt_snapshot",
    "exclude_dynamic_system_prompt_sections", "agent", "allowed_tools",
    "disallowed_tools", "restricted", "disable_slash_commands", "mcp_config",
    "agents", "plugin_dirs", "plugin_urls", "settings", "autocompact", "name",
    "setting_sources", "safe_mode", "bare", "include_partial_messages",
    "include_hook_events", "forward_subagent_text", "debug", "debug_file")

#: Model names that mean a Claude model; Codex cannot run them.
CLAUDE_MODEL_NAMES = frozenset({"fable", "opus", "sonnet", "haiku", "default",
                                "best", "opusplan"})


# --------------------------------------------------------------------------- #
# errors and warnings
# --------------------------------------------------------------------------- #

class CodexNotFound(ClaudeNotFound):
    """The `codex` executable could not be located."""


class CodexExecutionError(ClaudeExecutionError):
    """codex ran but failed; `transient` is classified with Codex's markers."""

    def __init__(self, message: str, *, transient: bool | None = None,
                 **kwargs: Any) -> None:
        if transient is None:
            payload = kwargs.get("payload") or {}
            transient = is_transient(" ".join([
                message, kwargs.get("stderr", ""), str(payload.get("subtype") or ""),
                str(payload.get("result") or "")]))
        super().__init__(message, transient=transient, **kwargs)


class CodexBudgetRefused(CodexExecutionError):
    """A budgeted run on a model Codex cannot price, with no token cap."""


class CodexEngineWarning(UserWarning):
    """A setting Codex cannot honour, or a cost it cannot price."""


def is_transient(text: str) -> bool:
    """Whether a failure described by `text` is worth retrying: anything not
    recognised as permanent (claude_engine's markers plus Codex's)."""
    low = text.lower()
    return not any(m in low for m in PERMANENT_MARKERS + CODEX_PERMANENT_MARKERS)


_WARNED: set[str] = set()


def _warn_once(key: str, message: str) -> None:
    if key in _WARNED:
        return
    _WARNED.add(key)
    warnings.warn(message, CodexEngineWarning, stacklevel=3)


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #

#: Process-wide defaults for `None` fields, like claude_engine.DEFAULTS.
DEFAULTS: dict[str, Any] = {}


def set_defaults(**values: Any) -> None:
    """Set process-wide defaults for Codex configs that leave a field None."""
    for key, value in values.items():
        if key not in DEFAULTABLE:
            raise TypeError(f"{key!r} is not a defaultable EngineConfig field "
                            f"(one of {', '.join(DEFAULTABLE)})")
        if value is None:
            DEFAULTS.pop(key, None)
        else:
            DEFAULTS[key] = value


def default_for(field_name: str) -> Any:
    """The value a Codex config gets for `field_name` when it leaves it None."""
    if field_name in DEFAULTS:
        return DEFAULTS[field_name]
    return os.environ.get(f"CODEX_ENGINE_{field_name.upper()}") or None


@dataclass
class CodexConfig(EngineConfig):
    """EngineConfig plus what only Codex has. A plain EngineConfig works too."""

    executable: str = "codex"
    images: Sequence[str | Path] = ()          # -i, attached to every prompt
    prices: dict[str, dict[str, float]] | None = None   # over PRICES
    max_budget_tokens: int | None = None       # per-call token cap (input + output)
    sandbox: str | None = None                 # force a sandbox mode
    codex_config: dict[str, Any] = field(default_factory=dict)  # extra -c pairs

    def __post_init__(self) -> None:
        for name in DEFAULTABLE:
            if getattr(self, name) is None:
                setattr(self, name, default_for(name))


def _field_defaults() -> dict[str, Any]:
    out = {}
    for f in fields(EngineConfig):
        if f.default_factory is not MISSING:
            out[f.name] = f.default_factory()
        else:
            out[f.name] = f.default
    return out


def is_claude_model(name: str | None) -> bool:
    if not name:
        return False
    low = name.lower()
    return (low in CLAUDE_MODEL_NAMES or low.startswith(("claude", "anthropic.",
                                                         "us.anthropic."))
            or "[1m]" in low)


def _toml(value: Any) -> str:
    """A value as TOML, for `-c key=value`."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{json.dumps(str(k))} = {_toml(v)}"
                               for k, v in value.items()) + "}"
    text = json.dumps(str(value), ensure_ascii=False)
    return text.replace("\x7f", "\\u007f")


def _codex_home(env: dict[str, str]) -> Path:
    return Path(env.get("CODEX_HOME") or Path.home() / ".codex")


def _models_cache(env: dict[str, str]) -> list[dict]:
    try:
        data = json.loads((_codex_home(env) / "models_cache.json").read_text())
    except (OSError, ValueError):
        return []
    models = data.get("models") if isinstance(data, dict) else data
    return [m for m in models or [] if isinstance(m, dict)]


def codex_default_model(env: dict[str, str]) -> str | None:
    """The model Codex uses when none is passed: config.toml's `model`, else
    the first listed model in the models cache. Best effort."""
    try:
        conf = tomllib.loads((_codex_home(env) / "config.toml").read_text())
        if isinstance(conf.get("model"), str):
            return conf["model"]
    except (OSError, tomllib.TOMLDecodeError):
        pass
    listed = [m for m in _models_cache(env) if m.get("visibility", "list") == "list"
              and m.get("slug")]
    if not listed:
        return None
    return min(listed, key=lambda m: m.get("priority", 1 << 30))["slug"]


def _supported_efforts(model: str | None, env: dict[str, str]) -> set[str]:
    for m in _models_cache(env):
        if m.get("slug") == model:
            return {str(level.get("effort")) for level in
                    m.get("supported_reasoning_levels") or [] if isinstance(level, dict)}
    return set()


def usage_tokens(usage: dict) -> int:
    """Input plus output tokens of a turn.completed usage block."""
    return int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)


def price_for(model: str | None, *, overrides: dict | None = None,
              env: dict[str, str] | None = None) -> dict[str, float] | None:
    """The price row for `model`: PRICES, then $CODEX_PRICES, then `overrides`."""
    table = dict(PRICES)
    raw = (env or os.environ).get("CODEX_PRICES")
    if raw:
        try:
            table.update(json.loads(raw))
        except ValueError:
            _warn_once("prices-env", "CODEX_PRICES is not valid JSON; ignored")
    table.update(overrides or {})
    return table.get(model or "")


# --------------------------------------------------------------------------- #
# schemas
# --------------------------------------------------------------------------- #

class _NotStrict(Exception):
    pass


def _nullable(schema: dict) -> dict:
    s = dict(schema)
    if "type" in s:
        t = s["type"]
        if isinstance(t, str) and t != "null":
            s["type"] = [t, "null"]
        elif isinstance(t, list) and "null" not in t:
            s["type"] = t + ["null"]
        if "enum" in s and None not in s["enum"]:
            s["enum"] = list(s["enum"]) + [None]
        return s
    if "enum" in s:
        if None not in s["enum"]:
            s["enum"] = list(s["enum"]) + [None]
        return s
    if "anyOf" in s:
        s["anyOf"] = list(s["anyOf"]) + [{"type": "null"}]
        return s
    return {"anyOf": [s, {"type": "null"}]}


def _strict(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return schema
    s = {k: v for k, v in schema.items() if k not in UNSUPPORTED_KEYWORDS}
    if "allOf" in s or "not" in s or "patternProperties" in s or "if" in s:
        raise _NotStrict
    t = s.get("type")
    if t == "object" or (isinstance(t, list) and "object" in t) or "properties" in s:
        props = s.get("properties")
        if not props or s.get("additionalProperties") not in (None, False):
            raise _NotStrict          # a free-form map cannot be strict
        required = set(s.get("required") or [])
        s["properties"] = {k: (_strict(p) if k in required else _nullable(_strict(p)))
                           for k, p in props.items()}
        s["required"] = list(props)
        s["additionalProperties"] = False
    if "items" in s:
        s["items"] = _strict(s["items"])
    if "oneOf" in s:
        s["anyOf"] = s.pop("oneOf")
    if "anyOf" in s:
        s["anyOf"] = [_strict(x) for x in s["anyOf"]]
    for key in ("$defs", "definitions"):
        if isinstance(s.get(key), dict):
            s[key] = {k: _strict(v) for k, v in s[key].items()}
    return s


def strict_schema(schema: dict) -> dict | None:
    """`schema` in the strict form OpenAI's structured output demands, or
    None when it cannot be expressed so (the schema then goes in the prompt)."""
    if not isinstance(schema, dict) or not (
            schema.get("type") == "object" or "properties" in schema):
        return None
    try:
        return _strict(schema)
    except _NotStrict:
        return None


def drop_nulls(value: Any, schema: Any) -> Any:
    """Undo the strict form: drop a null that stands for an optional field
    the model left out."""
    if not isinstance(schema, dict):
        return value
    if isinstance(value, dict) and isinstance(schema.get("properties"), dict):
        required = set(schema.get("required") or [])
        out = {}
        for key, item in value.items():
            if item is None and key not in required:
                continue
            out[key] = drop_nulls(item, schema["properties"].get(key))
        return out
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        return [drop_nulls(v, schema["items"]) for v in value]
    return value


def _missing(result: Any, schema: dict) -> list[str]:
    required = list(schema.get("required", []))
    if not isinstance(result, dict):
        return required
    return [name for name in required if name not in result]


# --------------------------------------------------------------------------- #
# the engine
# --------------------------------------------------------------------------- #

class CodexEngine(ClaudeEngine):
    """The OpenAI Codex CLI as a callable reasoning / execution backend, with
    ClaudeEngine's API.

    >>> CodexEngine().ask("Name the three laws of thermodynamics, tersely.").text
    """

    def __init__(self, config: EngineConfig | None = None, **overrides: Any) -> None:
        super().__init__(config or CodexConfig(), **overrides)
        #: Calls whose model had no price: their tokens are counted, their
        #: cost is None in the log and 0 in `usage.cost_usd`.
        self.unpriced_calls = 0
        self._warn_ignored()

    # -- public API --------------------------------------------------------

    def spawn(self, **overrides: Any) -> "CodexEngine":
        """A worker engine: same configuration, own session and accounting."""
        return type(self)(replace(self.config), **overrides)

    def ask(self, prompt: str, *, resume: bool | str = False,
            tools: Sequence[str] | None | type(...) = ...,
            system: str | None = None, schema: dict | None = None,
            images: Sequence[str | Path] = ()) -> Answer:
        """Send one prompt, get one answer (the turn's last agent message)."""
        strict = strict_schema(schema) if schema is not None else None
        while True:
            try:
                payload = self._ask_once(prompt, resume=resume, tools=tools,
                                         system=system, schema=schema, strict=strict,
                                         images=images)
                break
            except ClaudeExecutionError as exc:
                text = f"{exc} {exc.stderr}".lower()
                if strict is not None and ("invalid_json_schema" in text
                                           or "invalid schema" in text):
                    _warn_once(f"schema:{json.dumps(schema, sort_keys=True)[:200]}",
                               "the API refused the strict schema; asking for it in "
                               f"the prompt instead ({exc})")
                    strict = None
                    continue
                raise
        if schema is not None:
            try:
                value = _loads_loose(payload.get("result") or "")
            except json.JSONDecodeError as exc:
                raise CodexExecutionError(f"codex's answer is not JSON: {exc}",
                                          payload=payload, transient=False) from exc
            payload["structured_output"] = drop_nulls(value, schema)
        return self._to_answer(payload)

    def ask_json(self, prompt: str, schema: dict, **kwargs: Any) -> Any:
        """Reason with a required output shape. Returns parsed JSON; an answer
        without the schema's required fields is an error."""
        value = self.ask(prompt, schema=schema, **kwargs).json()
        missing = _missing(value, schema)
        if missing:
            raise CodexExecutionError(
                f"codex's answer lacks required field(s): {', '.join(missing)}",
                transient=False)
        return value

    def stream(self, prompt: str, *, resume: bool | str = False,
               tools: Sequence[str] | None | type(...) = ...,
               system: str | None = None,
               on_event: Callable[[Event], None] | None = None,
               images: Sequence[str | Path] = ()) -> Iterator[Event]:
        """Run an agentic turn, yielding events as they happen, then one
        `type == "result"` event. Retried like ClaudeEngine.stream: only while
        nothing but `system` events has been delivered."""
        for attempt in range(self.config.max_retries + 1):
            argv, stdin = self._prepare(prompt, resume=resume, tools=tools,
                                        system=system, images=images)
            held: list[Event] = []
            delivered = False
            try:
                for event in self._run_events(argv, stdin):
                    if event.type == "system" and not delivered:
                        held.append(event)
                        continue
                    delivered = True
                    for prologue in held:
                        if on_event:
                            on_event(prologue)
                        yield prologue
                    held.clear()
                    if on_event:
                        on_event(event)
                    yield event
                for prologue in held:
                    if on_event:
                        on_event(prologue)
                    yield prologue
                return
            except ClaudeExecutionError as exc:
                if delivered or not exc.transient or not self._sleep_before_retry(attempt):
                    raise

    # -- resolution --------------------------------------------------------

    def _transport_env(self, environ: dict[str, str]) -> dict[str, str]:
        """Nothing: the gateway's claude variables are not codex's route, and
        a missing gateway key must not stop a codex call. Codex's own route
        is a provider in its config, not these variables."""
        return {}

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(self.config.env or {})
        return env

    def token_cap(self) -> int | None:
        """The per-call token cap: the config, else $CODEX_BUDGET_TOKENS."""
        cap = getattr(self.config, "max_budget_tokens", None)
        if cap is None:
            raw = self._env().get("CODEX_BUDGET_TOKENS")
            try:
                cap = int(raw) if raw else None
            except ValueError:
                _warn_once("tokens-env", "CODEX_BUDGET_TOKENS is not an integer; ignored")
        return cap if cap and cap > 0 else None

    def _refuse_unholdable_budget(self) -> None:
        """A dollar budget needs a price or a token cap; without either it
        could never stop the run, so the run does not start."""
        budget = self.config.max_turns_budget_usd
        if budget is None or self.token_cap() is not None:
            return
        model = self.resolved_model()
        if price_for(model, overrides=getattr(self.config, "prices", None),
                     env=self._env()) is None:
            raise CodexBudgetRefused(
                f"refusing to start: the ${budget:g} budget cannot be held, Codex "
                f"model {model!r} has no price. Set its price in CODEX_PRICES "
                "(or CodexConfig.prices) or a token cap in CODEX_BUDGET_TOKENS.",
                transient=False)

    def _executable_name(self) -> str:
        exe = self.config.executable
        if not exe or exe == "claude":
            exe = os.environ.get("CODEX_ENGINE_EXECUTABLE") or "codex"
        path = shutil.which(exe)
        if not path:
            raise CodexNotFound(f"{exe!r} not found. Install the Codex CLI or set "
                                "executable= / $CODEX_ENGINE_EXECUTABLE.")
        return path

    def _model_flag(self) -> str | None:
        """The model to pass with -m, or None for Codex's own default."""
        model = self.config.model
        if is_claude_model(model):
            _warn_once(f"model:{model}", f"model {model!r} is a Claude model; Codex "
                       "answers with its default ($CODEX_ENGINE_MODEL to choose)")
            model = None
        if not model:
            model = default_for("model")
            if is_claude_model(model):
                model = None
        return model or None

    def resolved_model(self) -> str:
        """The model that answers, for pricing and `by_model`."""
        return (self._model_flag() or codex_default_model(self._env())
                or "codex-default")

    def _effort_flag(self, model: str) -> str | None:
        effort = self.config.effort or default_for("effort")
        if not effort:
            return None
        if effort in BASE_EFFORTS or effort in _supported_efforts(model, self._env()):
            return effort
        return "high"

    def sandbox_for(self, tools: Sequence[str] | None | type(...) = ...) -> str:
        cfg = self.config
        if cfg.skip_permissions:
            return "danger-full-access"
        forced = getattr(cfg, "sandbox", None)
        if forced:
            return forced
        if cfg.permission_mode == "plan":
            return "read-only"
        tool_set = cfg.tools if tools is ... else tools
        names = {t.split("(", 1)[0] for t in tool_set or ()}
        return "workspace-write" if names & WRITE_TOOLS else "read-only"

    def _warn_ignored(self) -> None:
        defaults = _field_defaults()
        for name in IGNORED_FIELDS:
            value = getattr(self.config, name, None)
            if value != defaults.get(name) and not (value in ((), None, [])
                                                   and defaults.get(name) in ((), None, [])):
                _warn_once(f"field:{name}", f"EngineConfig.{name}={value!r} has no "
                           "Codex equivalent; ignored")
        if self.config.permission_mode == "bypassPermissions" and not self.config.skip_permissions:
            _warn_once("bypass", "permission_mode='bypassPermissions' does not widen "
                       "the Codex sandbox; set skip_permissions=True for full access")

    # -- argv --------------------------------------------------------------

    def _prepare(self, prompt: str, *, resume: bool | str = False,
                 tools: Sequence[str] | None | type(...) = ...,
                 system: str | None = None, schema_file: str | Path | None = None,
                 images: Sequence[str | Path] = ()) -> tuple[list[str], str]:
        """The argv of one launch and the text for its stdin."""
        cfg = self.config
        target = self.session_id if resume is True else (resume or None)
        argv = [self._executable_name(), "exec"]
        if target:
            argv += ["fork" if cfg.fork_session else "resume", str(target)]
        argv += ["--json", "--skip-git-repo-check"]

        model = self._model_flag()
        if model:
            argv += ["-m", model]
        effort = self._effort_flag(self.resolved_model())
        if effort:
            argv += ["-c", f"model_reasoning_effort={_toml(effort)}"]

        sandbox = self.sandbox_for(tools)
        if target:
            argv += ["-c", f"sandbox_mode={_toml(sandbox)}"]
        else:
            argv += ["-s", sandbox]
            if cfg.cwd:
                argv += ["-C", str(cfg.cwd)]
        if sandbox == "workspace-write":
            dirs = [str(d) for d in cfg.add_dirs]
            if dirs and target:
                argv += ["-c", f"sandbox_workspace_write.writable_roots={_toml(dirs)}"]
            elif dirs:
                for d in dirs:
                    argv += ["--add-dir", d]
            tool_set = cfg.tools if tools is ... else tools
            if "Bash" in {t.split("(", 1)[0] for t in tool_set or ()}:
                argv += ["-c", "sandbox_workspace_write.network_access=true"]

        parts = [p for p in ((system if system is not None else cfg.system_prompt),
                             cfg.append_system_prompt) if p]
        instructions = "\n\n".join(parts)
        if instructions and len(instructions.encode("utf-8")) <= INSTRUCTIONS_ARG_LIMIT:
            argv += ["-c", f"developer_instructions={_toml(instructions)}"]
        elif instructions:
            prompt = SYSTEM_HEADING + instructions + "\n\n# Task\n\n" + prompt

        if not cfg.persist_session:
            argv.append("--ephemeral")
        for key, value in (getattr(cfg, "codex_config", None) or {}).items():
            argv += ["-c", f"{key}={_toml(value)}"]
        if schema_file:
            argv += ["--output-schema", str(schema_file)]
        argv.append("-")
        for image in list(getattr(cfg, "images", ()) or ()) + list(images):
            argv += ["-i", str(image)]
        return argv, prompt

    # -- running -----------------------------------------------------------

    def _ask_once(self, prompt: str, *, resume: bool | str, tools: Any,
                  system: str | None, schema: dict | None, strict: dict | None,
                  images: Sequence[str | Path]) -> dict:
        """One answer, with retries on a transient failure."""
        if schema is not None and strict is None:
            prompt = prompt + SCHEMA_INSTRUCTION % json.dumps(schema, ensure_ascii=False)
        for attempt in range(self.config.max_retries + 1):
            with tempfile.TemporaryDirectory(prefix="codex-engine-") as tmp:
                schema_file = None
                if strict is not None:
                    schema_file = Path(tmp) / "schema.json"
                    schema_file.write_text(json.dumps(strict), encoding="utf-8")
                argv, stdin = self._prepare(prompt, resume=resume, tools=tools,
                                            system=system, schema_file=schema_file,
                                            images=images)
                try:
                    final = None
                    for event in self._run_events(argv, stdin):
                        if event.type == "result":
                            final = event
                    if final is None:
                        raise CodexExecutionError("codex ended without a result")
                    return final.raw
                except ClaudeExecutionError as exc:
                    if not exc.transient or not self._sleep_before_retry(attempt):
                        raise
        raise CodexExecutionError("codex failed for an unknown reason")

    def _run_events(self, argv: list[str], stdin: str) -> Iterator[Event]:
        """Launch codex, yield normalised events, enforce timeout and budget."""
        cfg = self.config
        self._refuse_unholdable_budget()
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, bufsize=1, **self._popen_kwargs())
        assert proc.stdin and proc.stdout and proc.stderr
        timed_out = threading.Event()

        def expire() -> None:
            timed_out.set()
            proc.kill()

        watchdog = threading.Timer(cfg.timeout, expire) if cfg.timeout else None
        if watchdog:
            watchdog.daemon = True
            watchdog.start()
        stderr_parts: list[str] = []
        drain = threading.Thread(target=lambda: stderr_parts.append(proc.stderr.read() or ""),
                                 daemon=True)
        drain.start()

        state = {"started": time.monotonic(), "text": "", "turns": 0, "tools": set(),
                 "thread": None, "error": "", "failed": None, "done": False}
        abandoned = False
        stop: ClaudeExecutionError | None = None
        try:
            try:
                proc.stdin.write(stdin)
                proc.stdin.close()
            except BrokenPipeError:
                pass
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                try:
                    yield from self._normalise_codex(message, state)
                except ClaudeExecutionError as exc:     # a budget stop
                    stop = exc
                    proc.kill()
                    break
                if state["failed"] is not None:
                    break
        except GeneratorExit:
            abandoned = True
            raise
        finally:
            if watchdog:
                watchdog.cancel()
            if proc.poll() is None and (abandoned or stop or state["failed"] is not None):
                proc.kill()
            code = proc.wait()
            drain.join(timeout=5.0)
            proc.stderr.close()
            proc.stdout.close()
            stderr = "".join(stderr_parts)
            if cfg.verbose and stderr:
                print(stderr, file=sys.stderr, end="")
        if stop is not None:
            raise stop
        if timed_out.is_set():
            raise CodexExecutionError(f"codex timed out after {cfg.timeout}s",
                                      returncode=code, stderr=stderr.strip(),
                                      transient=True)
        if state["failed"] is not None:
            raise CodexExecutionError(f"codex reported an error: {state['failed']}",
                                      returncode=code, stderr=stderr.strip())
        if code != 0:
            raise CodexExecutionError(
                f"codex exited with code {code}"
                + (f": {state['error']}" if state["error"] else ""),
                returncode=code, stderr=stderr.strip())
        if not state["done"]:
            raise CodexExecutionError("codex ended without a completed turn",
                                      stderr=stderr.strip())

    def _normalise_codex(self, message: dict, state: dict) -> Iterator[Event]:
        """One Codex JSONL event as zero or more claude_engine Events."""
        mtype = message.get("type")
        if mtype == "thread.started":
            state["thread"] = message.get("thread_id")
            if state["thread"]:
                self.session_id = self.pending_session_id = state["thread"]
            yield Event("system", subtype="init", raw=message)
        elif mtype == "turn.started":
            yield Event("system", subtype="turn_started", raw=message)
        elif mtype in ("item.started", "item.updated", "item.completed"):
            yield from self._item(mtype.split(".", 1)[1], message.get("item") or {},
                                  message, state)
        elif mtype == "turn.completed":
            state["turns"] += 1
            payload = self._result_payload(message.get("usage") or {}, state)
            budget = self.config.max_turns_budget_usd
            cost = payload["total_cost_usd"]
            cap = self.token_cap()
            spent = usage_tokens(message.get("usage") or {})
            if cap is not None and spent > cap:
                payload.update(subtype="error_max_budget_tokens", is_error=True)
                self._absorb_result(payload)
                raise CodexExecutionError(
                    f"codex reported an error: error_max_budget_tokens (used "
                    f"{spent} tokens, over the {cap} token budget)",
                    payload=payload, transient=False)
            if budget is not None and cost is not None and cost > budget:
                payload.update(subtype="error_max_budget_usd", is_error=True)
                self._absorb_result(payload)
                raise CodexExecutionError(
                    f"codex reported an error: error_max_budget_usd (spent "
                    f"${cost:.4f}, over the ${budget:g} budget)",
                    payload=payload, transient=False)
            state["done"] = True
            self._absorb_result(payload)
            yield Event("result", subtype="success", text=payload["result"], raw=payload)
        elif mtype == "turn.failed":
            state["failed"] = str((message.get("error") or {}).get("message")
                                  or "turn failed")
        elif mtype == "error":
            state["error"] = str(message.get("message") or "")
            yield Event("system", subtype="error", text=state["error"], raw=message)
        else:
            yield Event(str(mtype), raw=message)

    def _item(self, phase: str, item: dict, message: dict, state: dict) -> Iterator[Event]:
        kind = item.get("type")
        iid = item.get("id")
        if kind == "agent_message":
            if phase == "completed" and item.get("text"):
                state["text"] = item["text"]
                yield Event("assistant", text=item["text"], raw=message)
            return
        if kind == "reasoning":
            if phase == "completed" and item.get("text"):
                yield Event("assistant", subtype="thinking", thinking=item["text"],
                            raw=message)
            return
        if kind == "error":
            if phase == "completed":
                yield Event("system", subtype="error", text=str(item.get("message") or ""),
                            raw=message)
            return
        use = _tool_use(item)
        if use is None:
            if phase == "completed":
                yield Event("item", subtype=str(kind), raw=message)
            return
        if phase == "updated":
            return
        if iid not in state["tools"]:
            state["tools"].add(iid)
            yield Event("assistant", subtype="tool_use", tool_name=use[0],
                        tool_input=use[1], raw=message)
        if phase == "completed":
            yield Event("user", subtype="tool_result", tool_result=_tool_output(item),
                        raw=message)

    def _result_payload(self, usage: dict, state: dict) -> dict:
        """A turn's outcome in the shape claude_engine's Usage / Answer read."""
        cached = int(usage.get("cached_input_tokens") or 0)
        written = int(usage.get("cache_write_input_tokens") or 0)
        total_in = int(usage.get("input_tokens") or 0)
        out = int(usage.get("output_tokens") or 0)
        model = self.resolved_model()
        row = price_for(model, overrides=getattr(self.config, "prices", None),
                        env=self._env())
        if row is None:
            cost = None
            self.unpriced_calls += 1
            _warn_once(f"price:{model}", f"no price for Codex model {model!r}: tokens "
                       "are recorded, cost is not (set CODEX_PRICES)")
        else:
            cost = ((total_in - cached) * row.get("input", 0.0)
                    + cached * row.get("cached_input", row.get("input", 0.0))
                    + out * row.get("output", 0.0)) / 1_000_000
        uncached = max(total_in - cached - written, 0)
        return {
            "type": "result", "subtype": "success", "is_error": False,
            "result": state["text"].strip(), "session_id": state["thread"],
            "num_turns": state["turns"], "engine": "codex", "model": model,
            "duration_ms": int((time.monotonic() - state["started"]) * 1000),
            "usage": {"input_tokens": uncached, "output_tokens": out,
                      "cache_read_input_tokens": cached,
                      "cache_creation_input_tokens": written,
                      "reasoning_output_tokens": int(usage.get("reasoning_output_tokens") or 0)},
            "codex_usage": usage,
            "total_cost_usd": cost,
            "modelUsage": {model: {"inputTokens": uncached, "outputTokens": out,
                                   "cacheReadInputTokens": cached,
                                   "cacheCreationInputTokens": written,
                                   "costUSD": cost}},
        }


def _tool_use(item: dict) -> tuple[str, dict] | None:
    """(tool_name, tool_input) for a Codex item that is a tool call."""
    kind = item.get("type")
    if kind == "command_execution":
        return "Bash", {"command": item.get("command", "")}
    if kind == "file_change":
        changes = item.get("changes") or []
        path = changes[0].get("path", "") if changes else ""
        name = "Write" if changes and all(c.get("kind") == "add" for c in changes) else "Edit"
        return name, {"file_path": path, "changes": changes}
    if kind == "mcp_tool_call":
        args = item.get("arguments")
        return (f"mcp__{item.get('server', '')}__{item.get('tool', '')}",
                args if isinstance(args, dict) else {"arguments": args})
    if kind == "web_search":
        return "WebSearch", {"query": item.get("query", "")}
    if kind == "todo_list":
        return "TodoWrite", {"todos": item.get("items") or []}
    return None


def _tool_output(item: dict) -> str:
    kind = item.get("type")
    if kind == "command_execution":
        return str(item.get("aggregated_output") or "")
    if kind == "file_change":
        return "\n".join(f"{c.get('kind')} {c.get('path')}" for c in item.get("changes") or [])
    if kind == "mcp_tool_call":
        got = item.get("result") if item.get("result") is not None else item.get("error")
        return got if isinstance(got, str) else json.dumps(got, default=str)
    return str(item.get("status") or "")


# --------------------------------------------------------------------------- #
# command line
# --------------------------------------------------------------------------- #

def _read_prompt(value: str | None) -> str:
    if value and value != "-":
        return value
    if sys.stdin.isatty():
        raise SystemExit("no prompt given (pass one as an argument or on stdin)")
    return sys.stdin.read()


def _engine_from_args(args: argparse.Namespace) -> CodexEngine:
    if getattr(args, "write", False):
        tools = list(FULL_TOOLS)
    elif args.command == "agent":
        tools = list(READ_ONLY_TOOLS)
    else:
        tools = []
    return CodexEngine(CodexConfig(
        executable=args.executable, model=args.model, effort=args.effort,
        system_prompt=args.system_prompt, append_system_prompt=args.append_system_prompt,
        cwd=args.cwd, add_dirs=args.add_dir or (), images=args.image or (),
        tools=tuple(tools), persist_session=not args.no_persist, timeout=args.timeout,
        max_turns_budget_usd=args.budget, verbose=args.verbose))


def _cmd_ask(args: argparse.Namespace) -> int:
    engine = _engine_from_args(args)
    answer = engine.ask(_read_prompt(args.prompt))
    print(answer.text)
    if args.verbose:
        print(f"\n[{engine.usage}]", file=sys.stderr)
    return 1 if answer.is_error else 0


def _cmd_json(args: argparse.Namespace) -> int:
    engine = _engine_from_args(args)
    path = Path(args.schema)
    schema = json.loads(path.read_text() if path.exists() else args.schema)
    print(json.dumps(engine.ask_json(_read_prompt(args.prompt), schema), indent=2))
    return 0


def _cmd_agent(args: argparse.Namespace) -> int:
    engine = _engine_from_args(args)
    for event in engine.stream(_read_prompt(args.prompt)):
        if event.subtype == "tool_use":
            print(f"  * {event.tool_name}({json.dumps(event.tool_input)[:120]})",
                  file=sys.stderr)
        elif event.subtype == "thinking" and args.verbose:
            print(f"  ... {event.thinking[:200]}", file=sys.stderr)
        elif event.type == "assistant" and event.text:
            print(event.text)
        elif event.type == "result":
            print(f"\n[{engine.usage}]", file=sys.stderr)
    return 0


def _cmd_chat(args: argparse.Namespace) -> int:
    engine = _engine_from_args(args)
    print("Codex engine -- blank line or Ctrl-D to quit, /reset for a new session.")
    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break
        if line == "/reset":
            engine.reset()
            print("(session reset)")
            continue
        print(f"\n{engine.ask(line, resume=bool(engine.session_id)).text}")
    print(f"\n[{engine.usage}]")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    spec = json.loads(Path(args.tasks).read_text())
    engine = CodexEngine(CodexConfig(**spec.get("engine", {})))
    tasks = [Task(**t) for t in spec["tasks"]]
    runner = TaskRunner(engine, jobs=args.jobs or int(spec.get("jobs") or 1),
                        on_progress=lambda n, m: print(f"[{n}] {m}", file=sys.stderr))
    results = runner.run(tasks, stop_on_error=not args.keep_going)
    print(json.dumps({name: {"ok": r.ok, "attempts": r.attempts, "error": r.error,
                             "seconds": round(r.seconds, 2),
                             "cost_usd": round(r.usage.cost_usd, 4), "output": r.output}
                      for name, r in results.items()}, indent=2, default=str))
    print(f"\n[{engine.usage}]", file=sys.stderr)
    return 0 if all(r.ok for r in results.values()) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex_engine",
        description="Run Python programs that use the Codex CLI as their AI engine.")
    parser.add_argument("--executable", default="codex", help="the codex binary")
    parser.add_argument("--model", help="a Codex model id ($CODEX_ENGINE_MODEL, then "
                                        "Codex's own default)")
    parser.add_argument("--effort", choices=["minimal", "low", "medium", "high",
                                             "xhigh", "max"],
                        help="reasoning effort ($CODEX_ENGINE_EFFORT)")
    parser.add_argument("--system-prompt", dest="system_prompt", metavar="TEXT",
                        help="developer instructions for the run")
    parser.add_argument("--append-system-prompt", dest="append_system_prompt",
                        metavar="TEXT", help="more developer instructions")
    parser.add_argument("--cwd", help="working directory for the agent")
    parser.add_argument("--add-dir", dest="add_dir", action="append", metavar="DIR",
                        help="extra writable directory (repeatable)")
    parser.add_argument("--image", action="append", metavar="FILE",
                        help="attach an image to the prompt (repeatable)")
    parser.add_argument("--no-persist", dest="no_persist", action="store_true",
                        help="--ephemeral: nothing written, cannot be resumed")
    parser.add_argument("--timeout", type=float, default=900.0,
                        help="seconds per call (default 900)")
    parser.add_argument("--budget", type=float, help="max USD for one call")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="echo codex's stderr and usage totals")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ask", help="one-shot reasoning, read-only sandbox")
    p.add_argument("prompt", nargs="?")
    p.set_defaults(func=_cmd_ask)
    p = sub.add_parser("json", help="reasoning with an enforced output schema")
    p.add_argument("prompt", nargs="?")
    p.add_argument("--schema", required=True, help="JSON Schema file or literal")
    p.set_defaults(func=_cmd_json)
    p = sub.add_parser("agent", help="agentic turn, streamed")
    p.add_argument("prompt", nargs="?")
    p.add_argument("--write", action="store_true",
                   help="workspace-write sandbox (edit files, run commands)")
    p.set_defaults(func=_cmd_agent)
    p = sub.add_parser("chat", help="multi-turn REPL over one session")
    p.set_defaults(func=_cmd_chat, prompt=None)
    p = sub.add_parser("run", help="run a task pipeline from a JSON file")
    p.add_argument("tasks")
    p.add_argument("--keep-going", action="store_true",
                   help="after a failure, still run tasks that do not depend on it")
    p.add_argument("--jobs", type=int, metavar="N", help="run up to N tasks at once")
    p.set_defaults(func=_cmd_run, prompt=None)
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
