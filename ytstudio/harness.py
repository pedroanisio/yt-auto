"""The one door from a stage to claude_engine.

The owner's rule: every tool in this pipeline builds through claude_engine;
deterministic code is plumbing, and the decisions -- what to do with a shot
nobody drew, which takes to record again, which cuts make the pilot, whether a
gate finding blocks -- are the model's, under a schema, against the house
standards. This module is how a stage asks, so each stage does not grow its
own copy of the same plumbing:

    h = Harness(project.root, "build", model=args.model, role=ROLE,
                house=rules, house_digest=digest, fresh=args.fresh)
    edit = h.decide("edit", prompt, EDIT_SCHEMA)        # one structured answer
    card = h.inspect("pass-01", prompt, CARD, cwd=dir)  # an agent reads files first
    manifest["engine"] = h.report()

A decision is written under `<project>/decisions/<stage>/<name>.json` with a
digest of everything the model saw (system, prompt, schema -- and for an
inspection, the files in its directory). The same inputs are served from that
file, so re-running a stage is free until something upstream changes; `fresh`
asks again regardless. The answer is checked against the schema's required
fields, and a failure is an error the stage reports -- never a quiet default,
because a default is a decision nobody made.

Tests replace `ENGINE` with a fake; there is no flag to skip the model.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import claude_engine
from claude_engine import (READ_ONLY_TOOLS, ClaudeEngine, ClaudeError,
                           EngineConfig, Usage)
from .core import _merge_usage, read_json, write_json

__all__ = ["Harness", "HarnessError", "ENGINE", "READ_ONLY_TOOLS", "ClaudeError",
           "brief", "missing_fields", "DECISIONS_DIR", "FEEDBACK_DIR",
           "feedback_for", "with_feedback"]

#: Where a project keeps what the model decided, per stage.
DECISIONS_DIR = "decisions"

#: Where `yt improve` leaves each stage its open revision brief,
#: `<project>/feedback/<stage>.md`; a consumed one moves under `closed/`.
FEEDBACK_DIR = "feedback"
FEEDBACK_LEAD = ("The operator's revision notes for this pass, which outrank the house "
                 "standards. Every finding below was seen in the rendered film by the "
                 "last QA pass; change what they ask for, and keep what they do not "
                 "mention:\n\n")

#: The tools an inspection may use: it looks, it never changes anything.
INSPECT_TOOLS = ("Read", "Glob", "Grep")

#: The engine class; tests replace it with a fake.
ENGINE: Callable[[EngineConfig], Any] = ClaudeEngine


class HarnessError(RuntimeError):
    """The model could not be asked, or did not answer the question."""


def mission() -> str:
    """The pipeline's statement of purpose, kept once, beside the authoring stage."""
    try:
        from .vfx import MISSION
        return MISSION
    except Exception:          # pragma: no cover - vfx is always importable here
        return ("This pipeline creates and composes top-tier YouTube videos on "
                "scene-render and sr-core, multi-aesthetic according to the "
                "editorial decisions.")


def feedback_for(root: Any, stage: str) -> str:
    """The stage's open revision notes from `yt improve`, led by what they
    are, or "" when there are none. Every prompt a stage builds itself
    (an authoring agent, a planner call) passes through `with_feedback`;
    a Harness adds it to its own system prompt."""
    if not root or not stage:
        return ""
    try:
        text = (Path(root) / FEEDBACK_DIR / f"{stage}.md").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""
    return FEEDBACK_LEAD + text if text else ""


def with_feedback(system: str, root: Any, stage: str) -> str:
    """`system` with the stage's open revision notes after it, if any."""
    notes = feedback_for(root, stage)
    return f"{system}\n\n{notes}" if notes else system


def brief(title: str, data: Any) -> str:
    """One labelled JSON block for a prompt: the model reads data, not prose."""
    return f"{title}:\n{json.dumps(data, ensure_ascii=False, indent=1, default=str)}"


def missing_fields(result: Any, schema: dict) -> list[str]:
    """Required top-level fields the answer lacks. The CLI enforces the schema
    when it can; this is the check that holds when the answer came another way."""
    required = list(schema.get("required", []))
    if not isinstance(result, dict):
        return required
    return [name for name in required if name not in result]


#: Appended to every inspection, so the agent that saw the evidence files the form.
FORM_INSTRUCTION = (
    "\n\nWhen you have looked at everything, end your answer with ONE fenced "
    "```json block containing only the completed form, valid against this "
    "JSON schema (no prose inside the block):\n%s")


def fenced_json(texts: Sequence[str]) -> Any | None:
    """The last fenced JSON object in the agent's words, or None."""
    blob = "\n".join(texts)
    start = blob.rfind("```json")
    if start < 0:
        start = blob.rfind("```")
    if start < 0:
        return None
    body = blob[start:].split("\n", 1)[-1]
    end = body.find("```")
    if end >= 0:
        body = body[:end]
    try:
        value = json.loads(body.strip())
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _digest(*parts: Any) -> str:
    blob = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _tree_digest(root: Path) -> str:
    """The files an inspection can see, by name, size and content hash."""
    rows = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        data = path.read_bytes()
        rows.append((str(path.relative_to(root)), len(data),
                     hashlib.sha256(data).hexdigest()[:12]))
    return _digest(rows)


@dataclass
class Decision:
    name: str
    cached: bool
    digest: str
    cost_usd: float = 0.0
    kind: str = "decide"
    tool_calls: int = 0


@dataclass
class Harness:
    """A stage's engine: asks, caches, accounts."""

    root: Path
    stage: str
    model: str = "sonnet"
    effort: str | None = None           # None: claude_engine's default
    fallback_model: str | None = None   # None: claude_engine's default
    role: str = ""
    house: str = ""
    house_digest: dict = field(default_factory=dict)
    fresh: bool = False
    verbose: bool = True
    budget_usd: float | None = None
    timeout: float = 900.0
    usage: Usage = field(default_factory=Usage)
    decisions: list[Decision] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        if self.effort is None:
            self.effort = claude_engine.default_for("effort")
        if self.fallback_model is None:
            self.fallback_model = claude_engine.default_for("fallback_model")

    @property
    def _asked_as(self) -> str:
        """What a decision was asked with, for its cache digest: the model,
        and the effort when one is set. Effort changes the answer, so a
        different effort is a different question; no effort keeps the
        digests of decisions made before it existed."""
        return f"{self.model}@{self.effort}" if self.effort else self.model

    # -- plumbing ----------------------------------------------------------

    def _say(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr)

    @property
    def system(self) -> str:
        parts = [mission()]
        if self.role:
            parts.append(self.role)
        if self.house:
            parts.append("The house standards, which every decision serves:\n"
                         + self.house)
        notes = feedback_for(self.root, self.stage)
        if notes:
            parts.append(notes)
        return "\n\n".join(parts)

    def path(self, name: str) -> Path:
        return self.root / DECISIONS_DIR / self.stage / f"{name}.json"

    def _cached(self, name: str, digest: str) -> Any | None:
        if self.fresh:
            return None
        saved = read_json(self.path(name), warn=False)
        if isinstance(saved, dict) and saved.get("digest") == digest:
            return saved
        return None

    def _save(self, name: str, digest: str, result: Any, *, kind: str,
              cost: float, tool_calls: int = 0) -> None:
        self.path(name).parent.mkdir(parents=True, exist_ok=True)
        write_json(self.path(name), {
            "stage": self.stage, "name": name, "kind": kind, "digest": digest,
            "model": self.model, "effort": self.effort, "house": self.house_digest,
            "cost_usd": round(cost, 4), "tool_calls": tool_calls,
            "when": time.strftime("%Y-%m-%dT%H:%M:%S"), "result": result})

    def _check(self, name: str, result: Any, schema: dict) -> Any:
        missing = missing_fields(result, schema)
        if missing:
            raise HarnessError(f"[{self.stage}] the model's {name} answer lacks "
                               f"{', '.join(missing)}")
        return result

    def _engine(self, **config: Any):
        return ENGINE(EngineConfig(model=self.model, effort=self.effort,
                                   fallback_model=self.fallback_model,
                                   timeout=self.timeout,
                                   max_turns_budget_usd=self.budget_usd, **config))

    # -- asking ------------------------------------------------------------

    def decide(self, name: str, prompt: str, schema: dict) -> Any:
        """One structured answer to one question, served from the project
        when the same question was answered before."""
        digest = _digest(self.system, prompt, schema, self._asked_as)
        saved = self._cached(name, digest)
        if saved is not None:
            self._say(f"[{self.stage}] {name}: decided earlier, same inputs")
            self.decisions.append(Decision(name, True, digest,
                                           float(saved.get("cost_usd") or 0)))
            return saved["result"]
        self._say(f"[{self.stage}] {name}: asking {self._asked_as}")
        engine = self._engine(tools=())
        try:
            result = engine.ask_json(prompt, schema, system=self.system)
        except ClaudeError as exc:
            raise HarnessError(f"[{self.stage}] {name}: {exc}") from exc
        finally:
            _merge_usage(self.usage, engine.usage)
        self._check(name, result, schema)
        self._save(name, digest, result, kind="decide", cost=engine.usage.cost_usd)
        self.decisions.append(Decision(name, False, digest, engine.usage.cost_usd))
        return result

    def inspect(self, name: str, prompt: str, schema: dict, *, cwd: Path,
                tools: Sequence[str] = INSPECT_TOOLS, form: str | None = None,
                on_tool: Callable[[str, str], None] | None = None) -> Any:
        """Let an agent read the files under `cwd` before it answers.

        The agent may file the form itself (`form`, a JSON file under `cwd`);
        otherwise its own words are handed to a structured pass that fills the
        schema from them, adding nothing. Tools are read-only: an inspection
        changes no file, so its evidence stays what it was.
        """
        cwd = Path(cwd)
        bad = [t for t in tools if t not in READ_ONLY_TOOLS]
        if bad:
            raise HarnessError(f"[{self.stage}] an inspection cannot use "
                               f"{', '.join(bad)}; it only looks")
        digest = _digest(self.system, prompt, schema, self._asked_as, tuple(tools),
                         _tree_digest(cwd))
        saved = self._cached(name, digest)
        if saved is not None:
            self._say(f"[{self.stage}] {name}: inspected earlier, same evidence")
            self.decisions.append(Decision(name, True, digest,
                                           float(saved.get("cost_usd") or 0),
                                           kind="inspect",
                                           tool_calls=int(saved.get("tool_calls") or 0)))
            return saved["result"]

        self._say(f"[{self.stage}] {name}: {self._asked_as} is reading {cwd}")
        prompt = prompt + FORM_INSTRUCTION % json.dumps(schema, ensure_ascii=False)
        engine = self._engine(tools=tuple(tools), cwd=cwd, permission_mode="dontAsk")
        texts: list[str] = []
        calls = 0
        try:
            for event in engine.stream(prompt, system=self.system):
                if event.subtype == "tool_use" and event.tool_input:
                    calls += 1
                    what = (event.tool_input.get("file_path")
                            or event.tool_input.get("pattern") or "")
                    if on_tool:
                        on_tool(str(event.tool_name), str(what))
                    elif self.verbose:
                        print(f"    {event.tool_name}: {str(what)[:100]}",
                              file=sys.stderr)
                if event.type == "assistant" and event.text:
                    texts.append(event.text)
        except ClaudeError as exc:
            raise HarnessError(f"[{self.stage}] {name}: {exc}") from exc
        finally:
            _merge_usage(self.usage, engine.usage)
        cost = engine.usage.cost_usd

        result = read_json(cwd / form, warn=False) if form else None
        if result is None or missing_fields(result, schema):
            # The agent that looked is the one to answer: its closing JSON
            # block is the form. Only when it left none does a text-only
            # pass fill it from its words -- and that pass saw no image.
            result = fenced_json(texts)
        if result is None or missing_fields(result, schema):
            if not texts:
                raise HarnessError(f"[{self.stage}] {name}: the inspection ended "
                                   "with no words and no form")
            self._say(f"[{self.stage}] {name}: filling the form from the inspection")
            extractor = self._engine(tools=())
            try:
                result = extractor.ask_json(
                    "From this inspection, fill the form exactly as the schema "
                    "asks, adding nothing that is not in the text. Where the text "
                    "gives no support for a field, say so in it.\n\n"
                    + "\n\n".join(texts)[-16000:], schema, system=self.system)
            except ClaudeError as exc:
                raise HarnessError(f"[{self.stage}] {name}: {exc}") from exc
            finally:
                _merge_usage(self.usage, extractor.usage)
            cost += extractor.usage.cost_usd
        self._check(name, result, schema)
        self._save(name, digest, result, kind="inspect", cost=cost, tool_calls=calls)
        self.decisions.append(Decision(name, False, digest, cost, kind="inspect",
                                       tool_calls=calls))
        return result

    # -- accounting --------------------------------------------------------

    def report(self) -> dict:
        """What the stage asked, what it cost, and under which standards."""
        return {
            "model": self.model,
            "effort": self.effort,
            "house": self.house_digest,
            "cost_usd": round(sum(d.cost_usd for d in self.decisions
                                  if not d.cached), 4),
            "calls": self.usage.calls,
            "by_model": self.usage.by_model,
            "decisions": [{"name": d.name, "kind": d.kind, "cached": d.cached,
                           "digest": d.digest, "cost_usd": round(d.cost_usd, 4),
                           "tool_calls": d.tool_calls} for d in self.decisions],
        }
