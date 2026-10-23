#!/usr/bin/env python3
"""
claude_engine.py — use Claude Code as the AI engine of a Python program.

Zero dependencies: it drives the `claude` CLI in non-interactive print mode
(`claude -p`) over a subprocess pipe and parses its JSON / stream-JSON output.

Three levels of use:

    1. one-shot reasoning        engine.ask("...")            -> Answer
    2. structured reasoning      engine.ask_json("...", schema) -> dict
    3. agentic work with tools   engine.stream("...")         -> events

Plus a TaskRunner that chains tasks, feeding each task the outputs of the
ones it depends on -- and, with `jobs=N`, runs independent tasks side by
side, each on a worker engine the parent spawns, with the accounting merged
back into the parent.

CLI:
    python claude_engine.py ask     "Explain this repo's layout"
    python claude_engine.py json    "List 3 risks" --schema schema.json
    python claude_engine.py agent   "Add type hints to utils.py" --write
    python claude_engine.py chat
    python claude_engine.py run     tasks.json [--jobs 4]

Requires: the `claude` CLI on PATH and an authenticated session
(`claude auth` / `claude setup-token`, or ANTHROPIC_API_KEY).

Every `claude -p` flag is a field on EngineConfig. A field left `None` falls
back to `set_defaults(...)`, then to `CLAUDE_ENGINE_<FIELD>` in the
environment (CLAUDE_ENGINE_MODEL, CLAUDE_ENGINE_EFFORT, ...), then to the
CLI's own settings -- so with nothing set, the model is whatever
`~/.claude/settings.json` names.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

__all__ = [
    "EngineConfig",
    "DEFAULTS",
    "DEFAULTABLE",
    "set_defaults",
    "default_for",
    "ClaudeEngine",
    "Answer",
    "Usage",
    "Event",
    "Task",
    "TaskResult",
    "TaskRunner",
    "ClaudeError",
    "ClaudeNotFound",
    "ClaudeExecutionError",
    "is_transient",
]


# --------------------------------------------------------------------------- #
# errors
# --------------------------------------------------------------------------- #

class ClaudeError(RuntimeError):
    """Base class for engine failures."""


class ClaudeNotFound(ClaudeError):
    """The `claude` executable could not be located."""


#: Fragments that mark a failure as permanent: running the same launch again
#: would fail the same way, so a retry only spends time and tokens.
PERMANENT_MARKERS = (
    "already in use",            # the pinned --session-id was consumed
    "unknown option", "unknown argument", "unrecognized", "usage:",
    "invalid api key", "not logged in", "authentication", "unauthorized",
    "please run /login",
    "error_max_budget_usd", "error_max_turns", "max budget", "budget exceeded",
    "invalid json schema", "no such file", "permission denied",
)


def is_transient(text: str) -> bool:
    """Whether a failure described by `text` is worth retrying.

    Anything not recognised as permanent is treated as transient (an
    overload, a dropped connection, a timeout, an unexplained crash):
    the cost of one more attempt is small, the cost of giving up on a
    flaky network is a stage that fails.
    """
    low = text.lower()
    return not any(marker in low for marker in PERMANENT_MARKERS)


class ClaudeExecutionError(ClaudeError):
    """The CLI ran but reported an error (non-zero exit, or is_error result).

    `transient` says whether a retry could succeed; it is classified from
    the message, stderr and payload unless given explicitly.
    """

    def __init__(self, message: str, *, returncode: int | None = None,
                 stderr: str = "", payload: dict | None = None,
                 transient: bool | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr
        self.payload = payload or {}
        if transient is None:
            evidence = " ".join([message, stderr,
                                 str(self.payload.get("subtype") or ""),
                                 str(self.payload.get("result") or "")])
            transient = is_transient(evidence)
        self.transient = transient


#: USD per 1M tokens (`input`, `cached_input`, `output`, optional
#: `cache_creation`) by model id, used only to estimate the cost of a call
#: that timed out (its `result` event, which carries the real cost, never
#: arrived). Empty: no price is guessed. Fill it with
#: $CLAUDE_PRICES='{"model": {"input": .., "cached_input": .., "output": ..}}'.
PRICES: dict[str, dict[str, float]] = {}

#: The provider of a virtual model on Vercel's AI Gateway (`vmc/<slug>`): the
#: slug may route to any model, so it has no family and no price.
VIRTUAL_PROVIDER = "vmc"


def canonical_model(name: str | None) -> tuple[str, str]:
    """`(provider, model)` for a model name, so a gateway id and its direct
    name are one model. Vercel's AI Gateway names models `provider/model`
    (`anthropic/claude-opus-5`, `openai/gpt-5`) and writes Anthropic's
    versions with a dot (`claude-haiku-4.5`, which Anthropic's own ids write
    `claude-haiku-4-5`); its Claude Code picker leads with `claude-code/` and
    marks a 1M-token context with `[1m]`. Both marks are dropped, the
    provider is split off, and only an Anthropic version has its dots made
    dashes (OpenAI's `gpt-5.1` is spelled with the dot). A name with no
    provider (`sonnet`, `claude-opus-5-5`) has provider "". A virtual model
    comes back as `("vmc", slug)`."""
    text = str(name or "").strip()
    if text.lower().startswith("claude-code/"):
        text = text[len("claude-code/"):]
    if text.lower().endswith("[1m]"):
        text = text[:-len("[1m]")]
    provider, _, model = text.partition("/") if "/" in text else ("", "", text)
    provider = provider.lower()
    if provider == "anthropic":
        model = re.sub(r"(?<=\d)\.(?=\d)", "-", model)
    return provider, model


#: The mark Claude Code's gateway picker (and the CLI's own `opus[1m]`) puts on
#: a model run with a 1M-token context.
LONG_CONTEXT_MARK = "[1m]"


def _price_row(table: dict, model: str | None, aliases: dict | None = None,
               provider: str = "anthropic") -> Any:
    """`model`'s row in `table`: the id exactly as reported, then, for a
    gateway id of the table's own `provider` (`anthropic/claude-opus-5`),
    its direct id, then the generation `aliases` resolves a short name to.
    A name with no provider is looked up as it always was. Another
    provider's id has no row here, a Claude model on Bedrock or Vertex
    included, since its rate is that provider's; nor has a virtual model.
    A 1M-token context (`...[1m]`) has no row unless one names it: it may
    bill above the base rate, so neither route prices it at the base row,
    and its session keeps the CLI's own budget stop (sdkkit.stream_round)."""
    name = model or ""
    names = [name]
    found, direct = canonical_model(name)
    if (found == provider and not name.lower().endswith(LONG_CONTEXT_MARK)
            and direct not in names):
        names.append(direct)
    for each in names:
        if table.get(each) is not None:
            return table[each]
    for each in names:
        if aliases and each in aliases:
            return table.get(aliases[each])
    return None


def _partial_cost(tokens: dict[str, int], model: str | None) -> float | None:
    """The estimated cost of `tokens` on `model`, None when it has no price."""
    table = dict(PRICES)
    raw = os.environ.get("CLAUDE_PRICES")
    if raw:
        try:
            table.update(json.loads(raw))
        except ValueError:
            pass
    row = _price_row(table, model)
    if not row:
        return None
    return (tokens.get("input_tokens", 0) * row.get("input", 0)
            + tokens.get("cache_read_input_tokens", 0) * row.get("cached_input", 0)
            + tokens.get("cache_creation_input_tokens", 0)
            * row.get("cache_creation", row.get("input", 0))
            + tokens.get("output_tokens", 0) * row.get("output", 0)) / 1_000_000


# --------------------------------------------------------------------------- #
# value objects
# --------------------------------------------------------------------------- #

@dataclass
class Usage:
    """Token / cost accounting, accumulated across calls."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    # Chat-session bridge calls have no exposed billing/token measurements.
    unmetered_calls: int = 0
    duration_ms: int = 0
    #: The same counters per model that actually answered (the CLI's
    #: `modelUsage`), so a fallback model's share is visible.
    by_model: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Calls cut off by their timeout, whose spend came from the stream's
    #: per-message usage (no `result` event arrived). It is already inside
    #: the totals above; these say how much of it, and whether the cost is
    #: an estimate from PRICES (`timed_out_cost_estimated`) or unknown (0).
    timed_out_calls: int = 0
    timed_out_tokens: dict[str, int] = field(default_factory=dict)
    timed_out_cost_usd: float = 0.0
    timed_out_cost_estimated: bool = False

    def add_partial(self, tokens: dict, *, cost: float | None = None,
                    estimated: bool = False) -> None:
        """Book a call that was cut off: tokens from its stream, `cost` when
        it could be priced. Counted as a call, and marked as timed out."""
        names = {"input_tokens": "input_tokens", "output_tokens": "output_tokens",
                 "cache_read_input_tokens": "cache_read_tokens",
                 "cache_creation_input_tokens": "cache_creation_tokens"}
        for src, dst in names.items():
            n = int(tokens.get(src) or 0)
            setattr(self, dst, getattr(self, dst) + n)
            self.timed_out_tokens[dst] = self.timed_out_tokens.get(dst, 0) + n
        self.cost_usd += cost or 0.0
        self.timed_out_cost_usd += cost or 0.0
        self.timed_out_cost_estimated = self.timed_out_cost_estimated or (
            estimated and cost is not None)
        self.timed_out_calls += 1
        self.calls += 1

    def add(self, result: dict) -> None:
        self.unmetered_calls += int(bool(result.get("unmetered")))
        u = result.get("usage") or {}
        self.input_tokens += u.get("input_tokens", 0)
        self.output_tokens += u.get("output_tokens", 0)
        self.cache_read_tokens += u.get("cache_read_input_tokens", 0)
        self.cache_creation_tokens += u.get("cache_creation_input_tokens", 0)
        self.cost_usd += result.get("total_cost_usd") or 0.0
        self.duration_ms += result.get("duration_ms") or 0
        self.calls += 1
        for model, m in (result.get("modelUsage") or {}).items():
            row = self.by_model.setdefault(model, {
                "calls": 0, "input_tokens": 0, "output_tokens": 0,
                "cache_read_tokens": 0, "cache_creation_tokens": 0,
                "cost_usd": 0.0})
            row["calls"] += 1
            row["input_tokens"] += m.get("inputTokens", 0)
            row["output_tokens"] += m.get("outputTokens", 0)
            row["cache_read_tokens"] += m.get("cacheReadInputTokens", 0)
            row["cache_creation_tokens"] += m.get("cacheCreationInputTokens", 0)
            row["cost_usd"] += m.get("costUSD") or 0.0

    def merge(self, other: "Usage") -> None:
        """Add another accounting into this one, per-model rows included."""
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_creation_tokens += other.cache_creation_tokens
        self.cost_usd += other.cost_usd
        self.calls += other.calls
        self.unmetered_calls += other.unmetered_calls
        self.duration_ms += other.duration_ms
        self.timed_out_calls += other.timed_out_calls
        self.timed_out_cost_usd += other.timed_out_cost_usd
        self.timed_out_cost_estimated = (self.timed_out_cost_estimated
                                         or other.timed_out_cost_estimated)
        for key, n in other.timed_out_tokens.items():
            self.timed_out_tokens[key] = self.timed_out_tokens.get(key, 0) + n
        for model, src in other.by_model.items():
            row = self.by_model.setdefault(model, {k: type(v)() for k, v in src.items()})
            for key, value in src.items():
                row[key] = row.get(key, 0) + value

    def as_dict(self) -> dict[str, Any]:
        """A JSON-ready copy, for manifests and reports."""
        return asdict(self)

    def __str__(self) -> str:
        if self.unmetered_calls:
            return (f"{self.calls} call(s) · {self.unmetered_calls} unmetered; "
                    "token and cost totals are incomplete")
        return (f"{self.calls} call(s) · {self.input_tokens}in/"
                f"{self.output_tokens}out tokens · "
                f"{self.cache_read_tokens} cached · "
                f"${self.cost_usd:.4f} · {self.duration_ms / 1000:.1f}s")


@dataclass
class Answer:
    """The outcome of a single engine call."""

    text: str
    session_id: str | None = None
    usage: Usage = field(default_factory=Usage)
    num_turns: int = 0
    stop_reason: str | None = None
    is_error: bool = False
    permission_denials: list[dict] = field(default_factory=list)
    model: str | None = None        # the model that actually answered (it
                                    # may be the fallback, not the one asked)
    structured: Any = None          # the CLI-validated output of a schema call
    raw: dict = field(default_factory=dict)

    @property
    def denied_tools(self) -> list[str]:
        """Names of tools the permission layer refused during this call.

        A denial is not an error: it comes back to the model as ordinary
        tool_result text, so the model apologises and carries on -- often
        answering from memory instead. Check this whenever an answer was
        supposed to be grounded in a tool.
        """
        return [d.get("tool_name", "?") for d in self.permission_denials]

    def json(self) -> Any:
        """The answer as JSON.

        A schema call returns the output the CLI validated (`structured`);
        otherwise `text` is parsed, tolerating a ```json fence around it.
        """
        if self.structured is not None:
            return self.structured
        return _loads_loose(self.text)

    def __str__(self) -> str:
        return self.text


@dataclass
class Event:
    """One message from the agent's stream."""

    type: str                      # system | assistant | user | result | ...
    subtype: str | None = None
    text: str = ""                 # assistant prose, if any
    thinking: str = ""             # extended-thinking blocks, if any
    tool_name: str | None = None   # tool the agent invoked
    tool_input: dict | None = None
    tool_result: str | None = None # output handed back to the agent
    permission_denials: list[dict] = field(default_factory=list)  # result only
    raw: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #

#: Tools that only observe. Safe default for an unattended engine.
READ_ONLY_TOOLS = ("Read", "Glob", "Grep", "WebFetch", "WebSearch")

#: Observe + modify files, no shell.
EDIT_TOOLS = READ_ONLY_TOOLS + ("Edit", "Write", "NotebookEdit")

#: Everything, shell and subagents included. Only with a permission mode you
#: trust. "Agent" is the subagent tool; the CLI still accepts its old name
#: "Task" as an alias (both probed on 2026-10-19), but new code names it
#: Agent. A model given it fans out inside the one `claude -p` process,
#: under that process's single budget cap; for fan-out the program controls
#: and accounts for per worker, use `TaskRunner(jobs=N)` instead.
FULL_TOOLS = EDIT_TOOLS + ("Bash", "Agent", "TodoWrite")


#: Process-wide defaults for the `None`-able EngineConfig fields, set with
#: `set_defaults()`. An explicit value on a config always wins; a field left
#: `None` falls back to these, then to `CLAUDE_ENGINE_<FIELD>` in the
#: environment, then to whatever the `claude` CLI resolves on its own
#: (its settings.json, e.g. `"model": "fable"`).
DEFAULTS: dict[str, Any] = {}

#: Fields that `set_defaults()` / the environment may fill in.
DEFAULTABLE = ("model", "fallback_model", "effort", "agent", "autocompact",
               "settings", "setting_sources", "system_prompt_snapshot", "name")


def set_defaults(**fields: Any) -> None:
    """Set process-wide defaults for configs that leave a field `None`.

    >>> set_defaults(model="opus", effort="high")

    Pass `None` to clear one. Only the fields in `DEFAULTABLE` are accepted.
    """
    for key, value in fields.items():
        if key not in DEFAULTABLE:
            raise TypeError(f"{key!r} is not a defaultable EngineConfig field "
                            f"(one of {', '.join(DEFAULTABLE)})")
        if value is None:
            DEFAULTS.pop(key, None)
        else:
            DEFAULTS[key] = value


def default_for(field_name: str) -> Any:
    """The value a config would get for `field_name` if it left it `None`."""
    if field_name in DEFAULTS:
        return DEFAULTS[field_name]
    return os.environ.get(f"CLAUDE_ENGINE_{field_name.upper()}") or None


@dataclass
class EngineConfig:
    """How the underlying `claude` process is launched.

    Every field maps onto one `claude -p` flag (named in the comment). A
    `None` means "don't pass the flag": the CLI then uses its own setting.
    """

    # --- model / reasoning -------------------------------------------------
    model: str | None = None            # --model: "fable" | "opus" | "sonnet"
                                        # | "haiku" | a full model id
    fallback_model: str | None = None   # --fallback-model: one id, or a
                                        # comma-separated list tried in order
    effort: str | None = None           # --effort: low|medium|high|xhigh|max
    betas: Sequence[str] = ()           # --betas: API beta headers (API key
                                        # users only)

    # --- prompting ---------------------------------------------------------
    system_prompt: str | None = None            # --system-prompt: replaces
                                                # the default prompt
    append_system_prompt: str | None = None     # --append-system-prompt
    system_prompt_snapshot: str | None = None   # --system-prompt-snapshot:
                                                # "on" (default) | "off"
    exclude_dynamic_system_prompt_sections: bool = False  # better cross-run
                                                # prompt-cache reuse
    agent: str | None = None            # --agent: run as this named agent

    # --- capability surface ------------------------------------------------
    tools: Sequence[str] | None = ()    # () = no BUILT-IN tools; None = CLI
                                        # default set. NOTE: this is the
                                        # *availability* layer only -- see
                                        # auto_allow_tools for permission, and
                                        # strict_mcp_config for MCP tools,
                                        # which --tools does not filter.
    allowed_tools: Sequence[str] = ()   # auto-approved, e.g. ("Bash(git *)",)
    disallowed_tools: Sequence[str] = ()
    auto_allow_tools: bool = True       # auto-approve the tools we selected,
                                        # so a preset actually runs instead of
                                        # being denied by the permission layer
    permission_mode: str = "dontAsk"    # manual|acceptEdits|auto|plan|dontAsk|
                                        # bypassPermissions
    skip_permissions: bool = False      # --dangerously-skip-permissions
    restricted: bool = False            # --restricted: no code-running tools
                                        # or WebFetch unless `tools` names
                                        # them; file tools confined to cwd +
                                        # add_dirs; bypassPermissions refused
    disable_slash_commands: bool = False  # --disable-slash-commands (skills)

    # --- workspace ---------------------------------------------------------
    cwd: str | Path | None = None
    add_dirs: Sequence[str | Path] = ()         # --add-dir
    mcp_config: Sequence[str | Path] = ()       # --mcp-config
    strict_mcp_config: bool = True   # ignore the user's configured MCP
                                    # servers; False inherits them, and
                                    # their tools bypass `tools=()`
    agents: str | Path | None = None    # --agents: JSON file defining
                                        # custom subagents
    plugin_dirs: Sequence[str | Path] = ()      # --plugin-dir (repeatable)
    plugin_urls: Sequence[str] = ()             # --plugin-url (repeatable)
    settings: str | Path | None = None  # --settings: file or JSON string

    # --- guard rails -------------------------------------------------------
    max_turns_budget_usd: float | None = None   # --max-budget-usd: hard
                                                # spend cap per call
    autocompact: str | int | None = None        # --autocompact: "auto" or a
                                                # window in tokens
    timeout: float | None = 900.0               # seconds per call (ours)
    max_retries: int = 2
    retry_backoff: float = 2.0

    # --- session -----------------------------------------------------------
    persist_session: bool = True        # False => --no-session-persistence
    fork_session: bool = False          # --fork-session: a resume gets a new
                                        # session id
    name: str | None = None             # --name: display name for the session
    setting_sources: str | None = None  # --setting-sources: "user,project,local"
    safe_mode: bool = False             # --safe-mode: all customisation off
    bare: bool = False                  # --bare: no hooks, plugins, memory,
                                        # CLAUDE.md discovery or keychain;
                                        # auth is ANTHROPIC_API_KEY only

    # --- streaming (stream-json only) --------------------------------------
    include_partial_messages: bool = False  # --include-partial-messages
    include_hook_events: bool = False       # --include-hook-events
    forward_subagent_text: bool = False     # --forward-subagent-text

    # --- misc --------------------------------------------------------------
    executable: str = "claude"
    env: dict[str, str] | None = None
    verbose: bool = False               # echo stderr from the CLI
    debug: str | bool | None = None     # --debug [filter], e.g. "api,hooks"
    debug_file: str | Path | None = None  # --debug-file

    def __post_init__(self) -> None:
        for name in DEFAULTABLE:
            if getattr(self, name) is None:
                setattr(self, name, default_for(name))

    def resolve_executable(self) -> str:
        path = shutil.which(self.executable)
        if not path:
            raise ClaudeNotFound(
                f"{self.executable!r} not found on PATH. Install Claude Code "
                "(https://claude.com/claude-code) or set EngineConfig.executable."
            )
        return path


# --------------------------------------------------------------------------- #
# the engine
# --------------------------------------------------------------------------- #

class ClaudeEngine:
    """Claude Code as a callable reasoning / execution backend.

    >>> engine = ClaudeEngine()
    >>> engine.ask("Name the three laws of thermodynamics, tersely.").text
    """

    def __init__(self, config: EngineConfig | None = None, **overrides: Any) -> None:
        self.config = config or EngineConfig()
        for key, value in overrides.items():
            if not hasattr(self.config, key):
                raise TypeError(f"unknown EngineConfig field: {key!r}")
            setattr(self.config, key, value)
        self.usage = Usage()
        self.session_id: str | None = None
        #: The id pinned on the launch in flight (or the last one), so a
        #: caller can `resume` it after a crash or timeout. Each attempt
        #: pins a fresh one: the CLI refuses an id it has already used.
        self.pending_session_id: str | None = None
        self._log: list[dict] = []
        #: Guards `usage` and the log when workers report back (`absorb`).
        #: One engine's own calls are not thread-safe: run concurrent work
        #: on workers from `spawn()`, never on a shared instance.
        self._lock = threading.Lock()

    # -- public API --------------------------------------------------------

    def spawn(self, **overrides: Any) -> "ClaudeEngine":
        """A worker engine: the same configuration, its own session and its
        own accounting. Run concurrent work on workers, one per thread, and
        hand each back with `absorb()` so the parent's totals stay whole.

        >>> worker = engine.spawn(name="section-3")
        """
        return ClaudeEngine(replace(self.config), **overrides)

    def absorb(self, worker: "ClaudeEngine") -> None:
        """Fold a worker's usage and call log into this engine's. Safe to
        call from several threads at once."""
        with self._lock:
            self.usage.merge(worker.usage)
            self._log.extend(worker._log)

    def ask(self, prompt: str, *, resume: bool | str = False,
            tools: Sequence[str] | None | type(...) = ...,
            system: str | None = None,
            schema: dict | None = None) -> Answer:
        """Send one prompt, get one answer. No streaming.

        `resume=True` continues this engine's own last session; a string
        resumes that specific session id.
        """
        argv = self._build_argv(
            output_format="json", resume=resume, tools=tools,
            system=system, schema=schema,
        )
        payload = self._run_once(argv, prompt)
        return self._to_answer(payload)

    def ask_json(self, prompt: str, schema: dict, **kwargs: Any) -> Any:
        """Reason with a required output shape. Returns parsed JSON.

        The schema is enforced by the CLI (`--json-schema`), so the result
        is structurally valid or the call fails.
        """
        answer = self.ask(prompt, schema=schema, **kwargs)
        return answer.json()

    def stream(self, prompt: str, *, resume: bool | str = False,
               tools: Sequence[str] | None | type(...) = ...,
               system: str | None = None,
               on_event: Callable[[Event], None] | None = None,
               ) -> Iterator[Event]:
        """Run an agentic turn, yielding events as they happen.

        Yields `Event`s for assistant text, thinking, each tool call and each
        tool result, then a final `type == "result"` event.

        A transient failure is retried like `ask()`, but only while nothing
        beyond the CLI's own `system` messages has been delivered: once the
        consumer has seen content, a retry would replay it, so the error
        is raised instead. System events are held back until the first
        content event so a retried launch does not show up twice.
        """
        argv = self._build_argv(
            output_format="stream-json", resume=resume, tools=tools, system=system,
        )
        argv.append("--verbose")  # stream-json requires it in print mode
        for attempt in range(self.config.max_retries + 1):
            held: list[Event] = []
            delivered = False
            try:
                for event in self._run_stream(self._launch_argv(argv), prompt):
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

    def agent(self, prompt: str, **kwargs: Any) -> Answer:
        """Run an agentic turn to completion and return the final answer.

        Convenience wrapper over `stream()` for when you want the tools to
        run but only care about the end state.
        """
        final: Event | None = None
        texts: list[str] = []
        for event in self.stream(prompt, **kwargs):
            if event.type == "assistant" and event.text:
                texts.append(event.text)
            if event.type == "result":
                final = event
        if final is None:
            raise ClaudeExecutionError("stream ended without a result message")
        answer = self._to_answer(final.raw)
        if final.permission_denials and not answer.permission_denials:
            answer.permission_denials = final.permission_denials
        if not answer.text:
            answer.text = "\n".join(texts).strip()
        return answer

    def reset(self) -> None:
        """Forget the current session; the next call starts fresh."""
        self.session_id = None

    @property
    def log(self) -> list[dict]:
        """Raw result payloads for every call this engine has made."""
        return list(self._log)

    # -- argv construction -------------------------------------------------

    def _build_argv(self, *, output_format: str, resume: bool | str,
                    tools: Sequence[str] | None | type(...),
                    system: str | None, schema: dict | None = None) -> list[str]:
        cfg = self.config
        argv: list[str] = [cfg.resolve_executable(), "-p",
                           "--output-format", output_format]

        if cfg.model:
            argv += ["--model", cfg.model]
        if cfg.fallback_model:
            argv += ["--fallback-model", cfg.fallback_model]
        if cfg.effort:
            argv += ["--effort", cfg.effort]
        if cfg.betas:
            argv += ["--betas", ",".join(cfg.betas)]

        sys_prompt = system if system is not None else cfg.system_prompt
        if sys_prompt:
            argv += ["--system-prompt", sys_prompt]
        if cfg.append_system_prompt:
            argv += ["--append-system-prompt", cfg.append_system_prompt]
        if cfg.system_prompt_snapshot:
            argv += ["--system-prompt-snapshot", cfg.system_prompt_snapshot]
        if cfg.exclude_dynamic_system_prompt_sections:
            argv.append("--exclude-dynamic-system-prompt-sections")
        if cfg.agent:
            argv += ["--agent", cfg.agent]

        tool_set = cfg.tools if tools is ... else tools
        if tool_set is not None:
            # "" drops every built-in tool; a list selects from the built-in
            # set. Neither touches MCP tools -- strict_mcp_config does that.
            argv += ["--tools", ",".join(tool_set) if tool_set else ""]

        # Availability and permission are separate layers: a tool listed in
        # --tools is still denied by dontAsk unless it is also allow-listed.
        allow = list(cfg.allowed_tools)
        if cfg.auto_allow_tools and tool_set:
            allow += [t for t in tool_set if t not in allow]
        if allow:
            argv += ["--allowed-tools", ",".join(allow)]
        if cfg.disallowed_tools:
            argv += ["--disallowed-tools", ",".join(cfg.disallowed_tools)]
        argv += ["--permission-mode", cfg.permission_mode]
        if cfg.skip_permissions:
            argv.append("--dangerously-skip-permissions")
        elif cfg.permission_mode != "bypassPermissions":
            # Nobody is at the keyboard: deny rather than hang on a prompt.
            argv += ["--permission-prompts", "none"]
        if cfg.restricted:
            argv.append("--restricted")
        if cfg.disable_slash_commands:
            argv.append("--disable-slash-commands")

        for directory in cfg.add_dirs:
            argv += ["--add-dir", str(directory)]
        for mcp in cfg.mcp_config:
            argv += ["--mcp-config", str(mcp)]
        if cfg.strict_mcp_config:
            argv.append("--strict-mcp-config")
        if cfg.agents:
            argv += ["--agents", str(cfg.agents)]
        for plugin in cfg.plugin_dirs:
            argv += ["--plugin-dir", str(plugin)]
        for url in cfg.plugin_urls:
            argv += ["--plugin-url", url]
        if cfg.settings:
            argv += ["--settings", str(cfg.settings)]

        if cfg.max_turns_budget_usd is not None:
            argv += ["--max-budget-usd", str(cfg.max_turns_budget_usd)]
        if cfg.autocompact is not None:
            argv += ["--autocompact", str(cfg.autocompact)]
        if cfg.setting_sources:
            argv += ["--setting-sources", cfg.setting_sources]
        if cfg.safe_mode:
            argv.append("--safe-mode")
        if cfg.bare:
            argv.append("--bare")
        if not cfg.persist_session:
            argv.append("--no-session-persistence")
        if cfg.name:
            argv += ["--name", cfg.name]

        if output_format == "stream-json":
            if cfg.include_partial_messages:
                argv.append("--include-partial-messages")
            if cfg.include_hook_events:
                argv.append("--include-hook-events")
            if cfg.forward_subagent_text:
                argv.append("--forward-subagent-text")

        if cfg.debug_file:
            argv += ["--debug-file", str(cfg.debug_file)]
        elif cfg.debug:
            argv.append("--debug")
            if isinstance(cfg.debug, str):
                argv.append(cfg.debug)

        if schema is not None:
            argv += ["--json-schema", json.dumps(schema)]

        target = self.session_id if resume is True else (resume or None)
        if target:
            argv += ["--resume", str(target)]
            if cfg.fork_session:
                argv.append("--fork-session")
        # A new session's id is pinned per launch, in _launch_argv.

        return argv

    def _launch_argv(self, argv: list[str]) -> list[str]:
        """The argv for one attempt: a fresh pinned session id when this is
        a new, persisted session. The CLI refuses an id it has used, so the
        id must change on every retry; it is recorded on the engine first so
        a caller can resume it even if this process dies."""
        if "--resume" in argv or not self.config.persist_session:
            return list(argv)
        self.pending_session_id = str(uuid.uuid4())
        return argv + ["--session-id", self.pending_session_id]

    # -- process plumbing --------------------------------------------------

    def _popen_kwargs(self) -> dict:
        env = dict(os.environ)
        if self.config.env:
            env.update(self.config.env)
        # Keep the child from trying to paint a TUI into our pipe.
        env.setdefault("CI", "1")
        return {
            "cwd": str(self.config.cwd) if self.config.cwd else None,
            "env": env,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        }

    def _run_once(self, argv: list[str], prompt: str) -> dict:
        """Run to completion, parse the single JSON result, with retries.

        Each attempt is a fresh launch (its own session id). Only a
        transient failure is retried; a permanent one is raised at once.
        """
        last_error: ClaudeExecutionError | None = None
        for attempt in range(self.config.max_retries + 1):
            launch = self._launch_argv(argv)
            try:
                proc = subprocess.run(
                    launch, input=prompt, capture_output=True,
                    timeout=self.config.timeout, **self._popen_kwargs(),
                )
            except subprocess.TimeoutExpired as exc:
                last_error = ClaudeExecutionError(
                    f"claude timed out after {self.config.timeout}s", transient=True)
                if not self._sleep_before_retry(attempt):
                    raise last_error from exc
                continue

            if self.config.verbose and proc.stderr:
                print(proc.stderr, file=sys.stderr, end="")

            if proc.returncode != 0:
                last_error = ClaudeExecutionError(
                    f"claude exited with code {proc.returncode}",
                    returncode=proc.returncode, stderr=proc.stderr.strip(),
                )
                if not last_error.transient or not self._sleep_before_retry(attempt):
                    raise last_error
                continue

            try:
                payload = json.loads(proc.stdout)
            except json.JSONDecodeError as exc:
                raise ClaudeExecutionError(
                    f"could not parse claude output as JSON: {exc}",
                    stderr=proc.stdout[:2000],
                ) from exc

            if payload.get("is_error"):
                last_error = ClaudeExecutionError(
                    f"claude reported an error: "
                    f"{payload.get('subtype') or payload.get('result')}",
                    payload=payload,
                )
                if not last_error.transient or not self._sleep_before_retry(attempt):
                    raise last_error
                continue

            return payload

        raise last_error or ClaudeExecutionError("claude failed for an unknown reason")

    def _run_stream(self, argv: list[str], prompt: str) -> Iterator[Event]:
        """Run with stream-json output, yielding normalised events.

        A watchdog kills the child at `timeout` whether or not it is
        talking, since a blocked read never gets to check a clock; stderr
        is drained on its own thread so a chatty child cannot fill that
        pipe and stall. A consumer that stops iterating early kills the
        child too.
        """
        cfg = self.config
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=1, **self._popen_kwargs(),
        )
        assert proc.stdin and proc.stdout and proc.stderr

        timed_out = threading.Event()

        def expire() -> None:
            timed_out.set()
            proc.kill()

        watchdog = threading.Timer(cfg.timeout, expire) if cfg.timeout else None
        if watchdog:
            watchdog.daemon = True
            watchdog.start()

        #: Per-message usage seen so far, latest per message id: what a
        #: stream cut off before its `result` event has spent.
        spent: dict[str, tuple[str | None, dict]] = {}

        stderr_parts: list[str] = []
        drain = threading.Thread(
            target=lambda: stderr_parts.append(proc.stderr.read() or ""),
            daemon=True)
        drain.start()

        abandoned = False
        try:
            try:
                proc.stdin.write(prompt)
                proc.stdin.close()
            except BrokenPipeError:
                pass  # the child died first; its exit code says why
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue  # stray non-JSON line; ignore
                self._note_spend(message, spent)
                yield from self._normalise(message)
        except GeneratorExit:
            abandoned = True
            raise
        finally:
            if watchdog:
                watchdog.cancel()
            if proc.poll() is None and abandoned:
                proc.kill()
            code = proc.wait()
            drain.join(timeout=5.0)
            proc.stderr.close()
            stderr = "".join(stderr_parts)
            if cfg.verbose and stderr:
                print(stderr, file=sys.stderr, end="")
            if not abandoned:
                if timed_out.is_set():
                    self._book_timed_out(spent)
                    raise ClaudeExecutionError(
                        f"claude timed out after {cfg.timeout}s",
                        returncode=code, stderr=stderr.strip(), transient=True)
                if code != 0:
                    raise ClaudeExecutionError(
                        f"claude exited with code {code}",
                        returncode=code, stderr=stderr.strip())

    @staticmethod
    def _note_spend(message: dict, spent: dict) -> None:
        """Remember an assistant message's usage; a result supersedes them all."""
        if message.get("type") == "result":
            spent.clear()
        elif message.get("type") == "assistant":
            inner = message.get("message") or {}
            if isinstance(inner.get("usage"), dict):
                key = str(inner.get("id") or len(spent))
                spent[key] = (inner.get("model"), inner["usage"])

    def _book_timed_out(self, spent: dict) -> None:
        """A timeout ends the stream with no `result`: book what it spent."""
        if not spent:
            return
        tokens: dict[str, int] = {}
        cost: float | None = 0.0
        for model, usage in spent.values():
            mine = {k: int(usage.get(k) or 0) for k in (
                "input_tokens", "output_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens")}
            for k, n in mine.items():
                tokens[k] = tokens.get(k, 0) + n
            part = _partial_cost(mine, model)
            cost = None if part is None or cost is None else cost + part
        with self._lock:
            self.usage.add_partial(tokens, cost=cost, estimated=cost is not None)

    def _normalise(self, message: dict) -> Iterator[Event]:
        """Turn one stream-json message into zero or more `Event`s."""
        mtype = message.get("type")

        if mtype == "assistant":
            for block in (message.get("message") or {}).get("content", []):
                kind = block.get("type")
                if kind == "text" and block.get("text"):
                    yield Event("assistant", text=block["text"], raw=message)
                elif kind == "thinking" and block.get("thinking"):
                    yield Event("assistant", subtype="thinking",
                                thinking=block["thinking"], raw=message)
                elif kind == "tool_use":
                    yield Event("assistant", subtype="tool_use",
                                tool_name=block.get("name"),
                                tool_input=block.get("input") or {}, raw=message)
            return

        if mtype == "user":
            for block in (message.get("message") or {}).get("content", []):
                if block.get("type") == "tool_result":
                    yield Event("user", subtype="tool_result",
                                tool_result=_flatten_content(block.get("content")),
                                raw=message)
            return

        if mtype == "result":
            self._absorb_result(message)
            yield Event("result", subtype=message.get("subtype"),
                        text=message.get("result") or "",
                        permission_denials=message.get("permission_denials") or [],
                        raw=message)
            return

        if mtype == "system":
            if message.get("session_id"):
                self.session_id = message["session_id"]
            yield Event("system", subtype=message.get("subtype"), raw=message)
            return

        yield Event(str(mtype), raw=message)

    def _absorb_result(self, payload: dict) -> None:
        if payload.get("session_id"):
            self.session_id = payload["session_id"]
        self.usage.add(payload)
        self._log.append(payload)

    def _to_answer(self, payload: dict) -> Answer:
        if not any(seen is payload for seen in self._log):
            self._absorb_result(payload)
        call_usage = Usage()
        call_usage.add(payload)
        return Answer(
            text=(payload.get("result") or "").strip(),
            session_id=payload.get("session_id"),
            usage=call_usage,
            num_turns=payload.get("num_turns", 0),
            stop_reason=payload.get("stop_reason"),
            is_error=bool(payload.get("is_error")),
            permission_denials=payload.get("permission_denials") or [],
            model=_answering_model(payload),
            structured=payload.get("structured_output"),
            raw=payload,
        )

    def _sleep_before_retry(self, attempt: int) -> bool:
        if attempt >= self.config.max_retries:
            return False
        delay = self.config.retry_backoff ** attempt
        if self.config.verbose:
            print(f"[claude_engine] retrying in {delay:.1f}s…", file=sys.stderr)
        time.sleep(delay)
        return True


# --------------------------------------------------------------------------- #
# task orchestration
# --------------------------------------------------------------------------- #

@dataclass
class Task:
    """One unit of work for the engine.

    `prompt` may reference earlier task outputs with `{name}` placeholders.
    """

    name: str
    prompt: str
    depends_on: Sequence[str] = ()
    schema: dict | None = None          # structured output for this task
    tools: Sequence[str] | None | type(...) = ...   # per-task capability override
    system: str | None = None
    agentic: bool = False               # run tools to completion, not one shot
    resume: bool = False                # continue a session: serially, the
                                        # engine's last; with jobs>1, the
                                        # session of the last dependency
    check: Callable[[Any], bool] | None = None   # validate the output
    retries: int = 1


@dataclass
class TaskResult:
    name: str
    output: Any
    answer: Answer
    attempts: int
    ok: bool
    error: str | None = None
    seconds: float = 0.0                # wall clock, all attempts included

    @property
    def usage(self) -> Usage:
        """What this task alone cost (its final attempt's call)."""
        return self.answer.usage


class TaskRunner:
    """Run a dependency-ordered set of tasks through one engine.

    With `jobs=1` (the default) the tasks run one after another on the
    engine itself, in dependency order, and `resume=True` continues whatever
    ran last. With `jobs>1`, every task whose dependencies are done runs at
    once, up to `jobs` at a time, each on a worker from `engine.spawn()`:
    its own session, its own accounting, merged back into the engine when
    it ends (`engine.usage` is the total, `engine.log` every call). There
    `resume=True` continues the session of the task's last dependency, so
    a chain still shares context while unrelated chains run side by side.

    A task whose dependency failed is recorded as failed itself, never run.
    With `stop_on_error` (the default) nothing new starts after a failure;
    work already running is left to finish and its result is kept. Tasks
    that never started are absent from `results`.
    """

    def __init__(self, engine: ClaudeEngine, *, jobs: int = 1,
                 on_progress: Callable[[str, str], None] | None = None) -> None:
        if jobs < 1:
            raise ValueError("jobs must be at least 1")
        self.engine = engine
        self.jobs = jobs
        self.results: dict[str, TaskResult] = {}
        self._on_progress = on_progress or (lambda name, msg: None)

    def run(self, tasks: Iterable[Task], *, stop_on_error: bool = True
            ) -> dict[str, TaskResult]:
        ordered = _topo_sort(list(tasks))
        if self.jobs == 1:
            self._run_serial(ordered, stop_on_error)
        else:
            self._run_parallel(ordered, stop_on_error)
        return self.results

    # -- schedulers --------------------------------------------------------

    def _run_serial(self, ordered: list[Task], stop_on_error: bool) -> None:
        for task in ordered:
            blocked = self._blocked_by(task)
            if blocked:
                result = blocked
            else:
                self._on_progress(task.name, "start")
                result = self._execute(task, self.engine, resume=task.resume)
            self._finish(result)
            if not result.ok and stop_on_error:
                break

    def _run_parallel(self, ordered: list[Task], stop_on_error: bool) -> None:
        pending = list(ordered)            # dependency order, for determinism
        running: dict[Any, Task] = {}
        halted = False
        with ThreadPoolExecutor(max_workers=self.jobs,
                                thread_name_prefix="claude_engine") as pool:
            while pending or running:
                if not halted:
                    ready = [t for t in pending
                             if all(d in self.results for d in t.depends_on)]
                    for task in ready:
                        pending.remove(task)
                        blocked = self._blocked_by(task)
                        if blocked:
                            self._finish(blocked)
                            if stop_on_error:
                                halted = True
                                break
                            continue
                        self._on_progress(task.name, "start")
                        running[pool.submit(self._run_on_worker, task)] = task
                if not running:
                    break                  # halted, or nothing can become ready
                done, _ = wait(list(running), return_when=FIRST_COMPLETED)
                for future in done:
                    running.pop(future)
                    result = future.result()
                    self._finish(result)
                    if not result.ok and stop_on_error:
                        halted = True

    def _run_on_worker(self, task: Task) -> TaskResult:
        """One task on its own worker engine, in a pool thread. The worker
        reports back to the parent before the result is returned, so the
        parent's totals are complete by the time `run()` ends."""
        worker = self.engine.spawn()
        resume: bool | str = False
        if task.resume:
            for dep in reversed(task.depends_on):
                sid = self.results[dep].answer.session_id
                if sid:
                    resume = sid
                    break
            else:
                resume = self.engine.session_id or False
        try:
            return self._execute(task, worker, resume=resume)
        finally:
            self.engine.absorb(worker)

    # -- one task ----------------------------------------------------------

    def _execute(self, task: Task, engine: ClaudeEngine, *,
                 resume: bool | str) -> TaskResult:
        started = time.monotonic()
        prompt = self._render(task)
        last_error: str | None = None
        answer: Answer | None = None

        for attempt in range(1, task.retries + 1):
            try:
                if task.agentic:
                    answer = engine.agent(
                        prompt, resume=resume, tools=task.tools, system=task.system)
                else:
                    answer = engine.ask(
                        prompt, resume=resume, tools=task.tools,
                        system=task.system, schema=task.schema)
            except ClaudeError as exc:
                last_error = str(exc)
                continue

            output: Any = answer.text
            if task.schema is not None:
                try:
                    output = answer.json()
                except (json.JSONDecodeError, ValueError) as exc:
                    last_error = f"output was not valid JSON: {exc}"
                    continue

            if task.check and not task.check(output):
                last_error = "check() rejected the output"
                continue

            return TaskResult(task.name, output, answer, attempt, ok=True,
                              seconds=time.monotonic() - started)

        return TaskResult(
            task.name, None, answer or Answer(""), task.retries,
            ok=False, error=last_error, seconds=time.monotonic() - started,
        )

    def _blocked_by(self, task: Task) -> TaskResult | None:
        """A failed result for a task whose dependency did not succeed, or
        None when every dependency is done and ok."""
        for dep in task.depends_on:
            got = self.results.get(dep)
            if got is None or not got.ok:
                return TaskResult(
                    task.name, None, Answer(""), 0, ok=False,
                    error=f"depends on {dep!r}, which did not succeed")
        return None

    def _finish(self, result: TaskResult) -> None:
        self.results[result.name] = result
        self._on_progress(result.name,
                          "ok" if result.ok else f"failed: {result.error}")

    def _render(self, task: Task) -> str:
        if not task.depends_on:
            return task.prompt
        context = {}
        for dep in task.depends_on:
            got = self.results[dep]      # scheduled only once every dep is ok
            context[dep] = (json.dumps(got.output, indent=2)
                            if not isinstance(got.output, str) else got.output)
        try:
            return task.prompt.format(**context)
        except KeyError as exc:
            raise ClaudeError(
                f"task {task.name!r} references {exc} but does not depend on it"
            ) from exc


def _topo_sort(tasks: list[Task]) -> list[Task]:
    by_name = {t.name: t for t in tasks}
    if len(by_name) != len(tasks):
        raise ClaudeError("duplicate task names")
    ordered: list[Task] = []
    state: dict[str, int] = {}

    def visit(task: Task) -> None:
        mark = state.get(task.name, 0)
        if mark == 1:
            raise ClaudeError(f"dependency cycle through task {task.name!r}")
        if mark == 2:
            return
        state[task.name] = 1
        for dep in task.depends_on:
            if dep not in by_name:
                raise ClaudeError(f"task {task.name!r} depends on unknown task {dep!r}")
            visit(by_name[dep])
        state[task.name] = 2
        ordered.append(task)

    for task in tasks:
        visit(task)
    return ordered


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _answering_model(payload: dict) -> str | None:
    """The model that wrote the answer: with a fallback in play `modelUsage`
    can name several, and the one that produced the output tokens is it."""
    usage = payload.get("modelUsage") or {}
    if not usage:
        return None
    return max(usage, key=lambda m: (usage[m] or {}).get("outputTokens", 0))


def _flatten_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [block.get("text", "") for block in content
                 if isinstance(block, dict) and block.get("type") == "text"]
        return "\n".join(p for p in parts if p)
    return "" if content is None else str(content)


def _loads_loose(text: str) -> Any:
    """json.loads, but tolerant of a ```json fence or surrounding prose."""
    text = text.strip()
    if text.startswith("```"):
        body = text.split("\n", 1)[1] if "\n" in text else ""
        text = body.rsplit("```", 1)[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise json.JSONDecodeError("no JSON value found in output", text, 0)


# --------------------------------------------------------------------------- #
# command line
# --------------------------------------------------------------------------- #

def _read_prompt(value: str | None) -> str:
    if value and value != "-":
        return value
    if sys.stdin.isatty():
        raise SystemExit("no prompt given (pass one as an argument or on stdin)")
    return sys.stdin.read()


def _engine_from_args(args: argparse.Namespace) -> ClaudeEngine:
    if getattr(args, "write", False):
        tools, mode = list(FULL_TOOLS), "acceptEdits"
    elif getattr(args, "agentic", False) or args.command == "agent":
        tools, mode = list(READ_ONLY_TOOLS), "dontAsk"
    else:
        tools, mode = (), "dontAsk"
    return ClaudeEngine(EngineConfig(
        model=args.model,
        fallback_model=args.fallback_model,
        effort=args.effort,
        betas=args.betas or (),
        system_prompt=args.system_prompt,
        append_system_prompt=args.append_system_prompt,
        system_prompt_snapshot=args.system_prompt_snapshot,
        exclude_dynamic_system_prompt_sections=args.exclude_dynamic_sections,
        agent=args.agent,
        cwd=args.cwd,
        tools=tools,
        permission_mode=mode,
        restricted=args.restricted,
        disable_slash_commands=args.no_skills,
        add_dirs=args.add_dir or (),
        mcp_config=args.mcp_config or (),
        strict_mcp_config=not args.inherit_mcp,
        agents=args.agents,
        plugin_dirs=args.plugin_dir or (),
        settings=args.settings,
        setting_sources=args.setting_sources,
        safe_mode=args.safe_mode,
        bare=args.bare,
        persist_session=not args.no_persist,
        name=args.name,
        autocompact=args.autocompact,
        timeout=args.timeout,
        verbose=args.verbose,
        debug=args.debug,
        debug_file=args.debug_file,
        max_turns_budget_usd=args.budget,
    ))


def _cmd_ask(args: argparse.Namespace) -> int:
    engine = _engine_from_args(args)
    answer = engine.ask(_read_prompt(args.prompt))
    print(answer.text)
    if args.verbose:
        print(f"\n[{engine.usage}]", file=sys.stderr)
    return 1 if answer.is_error else 0


def _cmd_json(args: argparse.Namespace) -> int:
    engine = _engine_from_args(args)
    schema = json.loads(Path(args.schema).read_text()) if Path(args.schema).exists() \
        else json.loads(args.schema)
    data = engine.ask_json(_read_prompt(args.prompt), schema)
    print(json.dumps(data, indent=2))
    return 0


def _cmd_agent(args: argparse.Namespace) -> int:
    engine = _engine_from_args(args)
    for event in engine.stream(_read_prompt(args.prompt)):
        if event.type == "assistant" and event.subtype == "tool_use":
            summary = json.dumps(event.tool_input)[:120]
            print(f"  ⚙ {event.tool_name}({summary})", file=sys.stderr)
        elif event.type == "assistant" and event.subtype == "thinking":
            if args.verbose:
                print(f"  … {event.thinking[:200]}", file=sys.stderr)
        elif event.type == "assistant" and event.text:
            print(event.text)
        elif event.type == "result":
            print(f"\n[{engine.usage}]", file=sys.stderr)
            return 1 if event.raw.get("is_error") else 0
    return 0


def _cmd_chat(args: argparse.Namespace) -> int:
    engine = _engine_from_args(args)
    print("Claude engine — blank line or Ctrl-D to quit, /reset for a new session.")
    while True:
        try:
            line = input("\n› ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break
        if line == "/reset":
            engine.reset()
            print("(session reset)")
            continue
        answer = engine.ask(line, resume=True)
        print(f"\n{answer.text}")
    print(f"\n[{engine.usage}]")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    spec = json.loads(Path(args.tasks).read_text())
    engine_kwargs = spec.get("engine", {})
    engine = ClaudeEngine(EngineConfig(**engine_kwargs))
    tasks = [Task(**t) for t in spec["tasks"]]
    jobs = args.jobs or int(spec.get("jobs") or 1)
    runner = TaskRunner(engine, jobs=jobs,
                        on_progress=lambda n, m: print(f"[{n}] {m}", file=sys.stderr))
    results = runner.run(tasks, stop_on_error=not args.keep_going)
    out = {name: {"ok": r.ok, "attempts": r.attempts, "error": r.error,
                  "seconds": round(r.seconds, 2), "cost_usd": round(r.usage.cost_usd, 4),
                  "output": r.output}
           for name, r in results.items()}
    print(json.dumps(out, indent=2, default=str))
    print(f"\n[{engine.usage}]", file=sys.stderr)
    return 0 if all(r.ok for r in results.values()) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude_engine",
        description="Run Python programs that use Claude Code as their AI engine.",
    )
    g = parser.add_argument_group("model")
    g.add_argument("--model", help="fable | opus | sonnet | haiku | full model id "
                                   "(default: $CLAUDE_ENGINE_MODEL, then the "
                                   "claude CLI's own setting)")
    g.add_argument("--fallback-model", dest="fallback_model", metavar="MODEL",
                   help="model(s) to try when --model is overloaded; comma-"
                        "separated, in order ($CLAUDE_ENGINE_FALLBACK_MODEL)")
    g.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"],
                   help="reasoning effort ($CLAUDE_ENGINE_EFFORT)")
    g.add_argument("--betas", action="append", metavar="HEADER",
                   help="API beta header to send (repeatable; API key users)")

    g = parser.add_argument_group("prompting")
    g.add_argument("--system-prompt", dest="system_prompt", metavar="TEXT",
                   help="replace the default system prompt")
    g.add_argument("--append-system-prompt", dest="append_system_prompt",
                   metavar="TEXT", help="add to the default system prompt")
    g.add_argument("--system-prompt-snapshot", dest="system_prompt_snapshot",
                   choices=["on", "off"],
                   help="record the prompt once and reuse it on resume")
    g.add_argument("--exclude-dynamic-sections", dest="exclude_dynamic_sections",
                   action="store_true",
                   help="move per-machine prompt sections into the first user "
                        "message, for better prompt-cache reuse")
    g.add_argument("--agent", help="run as this named agent ($CLAUDE_ENGINE_AGENT)")

    g = parser.add_argument_group("workspace")
    g.add_argument("--cwd", help="working directory for the agent")
    g.add_argument("--add-dir", dest="add_dir", action="append", metavar="DIR",
                   help="extra directory the tools may touch (repeatable)")
    g.add_argument("--mcp-config", dest="mcp_config", action="append",
                   metavar="FILE", help="MCP servers to load (repeatable)")
    g.add_argument("--inherit-mcp", dest="inherit_mcp", action="store_true",
                   help="also use the user's configured MCP servers "
                        "(default: only those from --mcp-config)")
    g.add_argument("--agents", metavar="FILE", help="JSON file of custom subagents")
    g.add_argument("--plugin-dir", dest="plugin_dir", action="append",
                   metavar="PATH", help="load a plugin for this run (repeatable)")
    g.add_argument("--settings", metavar="FILE-OR-JSON",
                   help="extra settings to load ($CLAUDE_ENGINE_SETTINGS)")
    g.add_argument("--setting-sources", dest="setting_sources", metavar="LIST",
                   help="which settings to load: user,project,local "
                        "($CLAUDE_ENGINE_SETTING_SOURCES)")
    g.add_argument("--safe-mode", dest="safe_mode", action="store_true",
                   help="disable all customisation (CLAUDE.md, hooks, plugins...)")
    g.add_argument("--bare", action="store_true",
                   help="minimal mode: no hooks, plugins, memory, CLAUDE.md "
                        "discovery or keychain; auth is ANTHROPIC_API_KEY only")
    g.add_argument("--restricted", action="store_true",
                   help="no code-running tools or WebFetch unless listed; file "
                        "tools confined to the working directories")
    g.add_argument("--no-skills", dest="no_skills", action="store_true",
                   help="disable slash-command skills")

    g = parser.add_argument_group("session / limits")
    g.add_argument("--name", help="display name for the session")
    g.add_argument("--no-persist", dest="no_persist", action="store_true",
                   help="write nothing to disk; the session cannot be resumed")
    g.add_argument("--autocompact", metavar="auto|TOKENS",
                   help="auto-compact window: auto, or 100k-1M tokens")
    g.add_argument("--timeout", type=float, default=900.0,
                   help="seconds per call (default 900)")
    g.add_argument("--budget", type=float, help="max USD to spend on one call")

    g = parser.add_argument_group("diagnostics")
    g.add_argument("-v", "--verbose", action="store_true",
                   help="echo the CLI's stderr and usage totals")
    g.add_argument("--debug", nargs="?", const=True, metavar="FILTER",
                   help='CLI debug mode, optionally filtered ("api,hooks")')
    g.add_argument("--debug-file", dest="debug_file", metavar="PATH",
                   help="write CLI debug logs here (implies --debug)")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ask", help="one-shot reasoning, no tools")
    p.add_argument("prompt", nargs="?")
    p.set_defaults(func=_cmd_ask)

    p = sub.add_parser("json", help="reasoning with an enforced output schema")
    p.add_argument("prompt", nargs="?")
    p.add_argument("--schema", required=True, help="JSON Schema file or literal")
    p.set_defaults(func=_cmd_json)

    p = sub.add_parser("agent", help="agentic turn with tools, streamed")
    p.add_argument("prompt", nargs="?")
    p.add_argument("--write", action="store_true",
                   help="allow Edit/Write/Bash (acceptEdits mode)")
    p.set_defaults(func=_cmd_agent)

    p = sub.add_parser("chat", help="multi-turn REPL over one session")
    p.set_defaults(func=_cmd_chat, prompt=None)

    p = sub.add_parser("run", help="run a task pipeline from a JSON file")
    p.add_argument("tasks")
    p.add_argument("--keep-going", action="store_true",
                   help="after a failure, still run the tasks that do not depend on it")
    p.add_argument("--jobs", type=int, metavar="N",
                   help="run up to N independent tasks at once, each on its own "
                        "worker session (default: the file's \"jobs\", else 1)")
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
