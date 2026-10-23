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

A stage that needs a model neither CLI reaches (one that listens to audio or
watches video, or a reviewer of another family) builds its own harness with
`engine="gateway"` behind its own flag (gateway_engine.py, AUTO-124): that
harness decides through Vercel AI Gateway, with media files attached, while
every other harness of the run stays on the global ENGINE.

    judge = Harness(project.root, "qa", model="google/gemini-3.6-flash",
                    engine="gateway", gateway={"providers": ["google"]})
    verdict = judge.decide("listen", prompt, VERDICT, media=[mix_wav])

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
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import claude_engine
from claude_engine import (READ_ONLY_TOOLS, ClaudeEngine, ClaudeError,
                           EngineConfig, Usage)
from . import spend
from .core import _merge_usage, read_json, write_json

# Every model has a price (AUTO-73): the engine estimates a timed-out call
# from its own table, which starts empty, so the one door to the engine fills
# it from spend's. $CLAUDE_PRICES still overrides a row.
spend.install_prices()

__all__ = ["Harness", "HarnessError", "ENGINE", "READ_ONLY_TOOLS", "ClaudeError",
           "brief", "missing_fields", "enum_violations",
           "DECISIONS_DIR", "FEEDBACK_DIR", "feedback_for", "with_feedback", "use_engine", "ENGINE_NAME", "build_engine"]

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

#: The one engine a Harness may name for itself, beside the global ENGINE.
GATEWAY_ENGINE = "gateway"
#: What `Harness.gateway` may set: the GatewayConfig fields a stage chooses.
GATEWAY_OPTIONS = ("providers", "has", "cost_poll_tries", "max_media_mb")

#: The engine class; tests replace it with a fake.
ENGINE: Callable[[EngineConfig], Any] = ClaudeEngine


def build_engine(config: EngineConfig, bound: Any = None) -> Any:
    """The engine for `config`: whatever `ENGINE` is now (claude, or codex under
    YT_ENGINE). `bound` is the caller's own `ClaudeEngine` name; only when a test
    replaced it with something else does that replacement win."""
    if bound is not None and bound is not ClaudeEngine:
        return bound(config)
    return ENGINE(config)

#: Every `claude -p` session a stage starts refuses cross-session messages.
#: Without it an interactive session on the machine sees each one as a peer,
#: and its broadcast lands in a model mid-task (measured: a probe author
#: received and answered one). Verified: a message sent to a session started
#: with these settings is refused and never reaches its model.
ISOLATED_SETTINGS = '{"crossSessionInbound": "refuse"}'


def isolate_sessions() -> None:
    """Make every engine this process starts refuse cross-session messages,
    unless the operator chose settings of their own (CLAUDE_ENGINE_SETTINGS)."""
    if not claude_engine.default_for("settings"):
        claude_engine.set_defaults(settings=ISOLATED_SETTINGS)


class HarnessError(ClaudeError):
    """The model could not be asked, or did not answer the question. A
    `ClaudeError`, so a stage that already carries on past a failed ask
    carries on past an unanswered one."""


#: Which CLI `ENGINE` drives, by name. Anything but "claude" is part of a
#: decision's cache key, so a Claude answer is never served for Codex.
ENGINE_NAME = "claude"


def use_engine(name: str) -> None:
    """Run every harness call on `name`: "claude" (claude_engine, the
    default), "codex" (the OpenAI Codex CLI), or "bridge" (active chat).
    `YT_ENGINE`
    in the environment picks it at import."""
    global ENGINE, ENGINE_NAME
    if name == "claude":
        ENGINE = ClaudeEngine
    elif name == "codex":
        import codex_engine
        ENGINE = codex_engine.CodexEngine
    elif name == "bridge":
        from bridge_engine import BridgeEngine
        ENGINE = BridgeEngine
    else:
        raise HarnessError(f"unknown engine {name!r} (claude | codex | bridge)")
    ENGINE_NAME = name


if os.environ.get("YT_ENGINE"):
    use_engine(os.environ["YT_ENGINE"])


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


def missing_fields(result: Any, schema: dict, path: str = "") -> list[str]:
    """Required fields the answer lacks, as `path` (`previous.verdict`,
    `rows[1].k`), through nested objects and arrays that are present: a nested
    object the answer holds must hold its own required fields; one it lacks is
    named once, by its parent. The CLI enforces the schema when it can; this is
    the check that holds when the answer came another way. Types are
    `type_errors`' business."""
    required = list(schema.get("required", []))
    if not path and not isinstance(result, dict):
        return required
    missing: list[str] = []
    if isinstance(result, dict):
        missing += [f"{path}.{name}" if path else name for name in required if name not in result]
        for name, sub in (schema.get("properties") or {}).items():
            if name in result and isinstance(sub, dict):
                missing += missing_fields(result[name], sub, f"{path}.{name}" if path else name)
    elif isinstance(result, list) and isinstance(schema.get("items"), dict):
        for n, item in enumerate(result):
            missing += missing_fields(item, schema["items"], f"{path}[{n}]")
    return missing


_JSON_TYPES: dict[str, Callable[[Any], bool]] = {
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: (isinstance(v, int) and not isinstance(v, bool))
    or (isinstance(v, float) and v.is_integer()),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}


def type_errors(result: Any, schema: dict, path: str = "") -> list[str]:
    """Where an answer holds a value of another type than the schema declares, as
    `path is 'pytype', not jsontype`, through nested objects and arrays (AUTO-65:
    a string where the schema says number passed missing_fields and reached the
    stage). Missing fields are `missing_fields`' business."""
    bad: list[str] = []
    declared = schema.get("type")
    kinds = [declared] if isinstance(declared, str) else list(declared or [])
    known = [k for k in kinds if k in _JSON_TYPES]
    if path and known and not any(_JSON_TYPES[k](result) for k in known):
        bad.append(f"{path} is {type(result).__name__!r}, not {' or '.join(known)}")
        return bad
    if isinstance(result, dict):
        for name, sub in (schema.get("properties") or {}).items():
            if name in result and isinstance(sub, dict):
                bad += type_errors(result[name], sub, f"{path}.{name}" if path else name)
    elif isinstance(result, list) and isinstance(schema.get("items"), dict):
        for n, item in enumerate(result):
            bad += type_errors(item, schema["items"], f"{path}[{n}]")
    return bad


#: Appended to the question when the first answer did not hold to the schema.
RETRY_INSTRUCTION = (
    "\n\nYour previous answer did not hold to the schema: %s. Answer the same "
    "question again, with every required field present and of the declared type, "
    "and every enumerated field holding one of its listed values.")


def enum_violations(result: Any, schema: dict, path: str = "") -> list[str]:
    """Where an answer holds a value outside the schema's `enum`, as `path=value`,
    through nested objects and arrays. Missing fields are `missing_fields`' business."""
    bad: list[str] = []
    if "enum" in schema and result is not None and result not in schema["enum"]:
        bad.append(f"{path or 'answer'}={result!r}")
    if isinstance(result, dict):
        for name, sub in (schema.get("properties") or {}).items():
            if name in result and isinstance(sub, dict):
                bad += enum_violations(result[name], sub, f"{path}.{name}" if path else name)
    elif isinstance(result, list) and isinstance(schema.get("items"), dict):
        for n, item in enumerate(result):
            bad += enum_violations(item, schema["items"], f"{path}[{n}]")
    return bad


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
    """The files an inspection can see, by name, size and content hash.

    The tree is shared: another section's thread, or a build's subprocess,
    writes beside the inspection (a record, a still, a .pyc) as a
    `.<name>.<pid>.tmp` it renames into place. A writer's temporary is never
    evidence, and a file gone between the listing and its read is left out
    rather than failing the inspection."""
    rows = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.name.startswith(".") and path.name.endswith(".tmp"):
            continue
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            continue
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
    #: The model that answered: a stage may share one account between
    #: harnesses of different models (an author and its reviewer), so the
    #: row says whose answer it was.
    model: str = ""


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
    #: Whether the stage's open feedback notes join the system. A reader
    #: meant to know nothing but the work it is shown (the script's cold
    #: reader, judging 14) sets this off, as it leaves `house` empty.
    feedback: bool = True
    #: None: the global ENGINE (claude, or what YT_ENGINE chose). "gateway":
    #: this harness alone decides through gateway_engine.GatewayEngine, a
    #: stage's own opt-in; it takes media and never inspects.
    engine: str | None = None
    #: The gateway engine's options (GATEWAY_OPTIONS), e.g. {"providers":
    #: ["google"]} to serve the model from Google only.
    gateway: dict | None = None
    #: Held while an answer is booked, so threads may share one harness.
    lock: Any = field(default_factory=threading.Lock, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        if self.engine is not None:
            self._gateway_post_init()
            return
        if self.gateway is not None:
            raise HarnessError(f"[{self.stage}] gateway options need engine='gateway'")
        if self.effort is None:
            self.effort = claude_engine.default_for("effort")
        if self.fallback_model is None:
            self.fallback_model = claude_engine.default_for("fallback_model")

    def _gateway_post_init(self) -> None:
        """Check a gateway harness when it is built, not when it first asks:
        the model must be a gateway id, and effort and fallback stay None.
        Neither reaches the gateway request, so an effort would change the
        digest without changing the question, and a fallback would be
        ignored; claude's defaults for them are not taken either."""
        if self.engine != GATEWAY_ENGINE:
            raise HarnessError(f"[{self.stage}] unknown engine {self.engine!r}: a harness "
                               f"names only {GATEWAY_ENGINE!r}; the others are YT_ENGINE's")
        import gateway_engine
        problem = gateway_engine.model_problem(self.model)
        if problem:
            raise HarnessError(f"[{self.stage}] {problem}")
        if self.effort is not None or self.fallback_model is not None:
            raise HarnessError(f"[{self.stage}] a gateway harness takes no effort or "
                               "fallback model: neither reaches the request")
        unknown = sorted(set(self.gateway or {}) - set(GATEWAY_OPTIONS))
        if unknown:
            raise HarnessError(f"[{self.stage}] unknown gateway option(s) "
                               f"{', '.join(unknown)} (one of {', '.join(GATEWAY_OPTIONS)})")
        try:   # the options' types, now rather than at the first decision
            gateway_engine.GatewayConfig(model=self.model, **(self.gateway or {}))
        except TypeError as exc:
            raise HarnessError(f"[{self.stage}] {exc}") from exc

    @property
    def _asked_as(self) -> str:
        """What a decision was asked with, for its cache digest: the model,
        and the effort when one is set. Effort changes the answer, so a
        different effort is a different question; no effort keeps the
        digests of decisions made before it existed. Another engine than
        claude is named too (`sonnet~codex`), and so is a harness's own
        gateway engine (`google/gemini-3.6-flash~gateway`), which has no effort."""
        if self.engine == GATEWAY_ENGINE:
            return f"{self.model}~{GATEWAY_ENGINE}"
        asked = f"{self.model}@{self.effort}" if self.effort else self.model
        return asked if ENGINE_NAME == "claude" else f"{asked}~{ENGINE_NAME}"

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
        notes = feedback_for(self.root, self.stage) if self.feedback else ""
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

    def forget(self, name: str) -> None:
        """Drop a kept answer, so the next run asks the question again. For a
        stage whose own check, past the schema, found the answer unusable:
        kept, it would be served on every rerun and fail the same way."""
        self.path(name).unlink(missing_ok=True)

    def _check(self, name: str, result: Any, schema: dict, *, enums: bool = False) -> Any:
        missing = missing_fields(result, schema)
        if missing:
            raise HarnessError(f"[{self.stage}] the model's {name} answer lacks "
                               f"{', '.join(missing)}")
        bad = enum_violations(result, schema) if enums else []
        if bad:
            raise HarnessError(f"[{self.stage}] the model's {name} answer has values "
                               f"outside the schema's enum: {', '.join(bad[:5])}")
        return result

    def _book(self, usage: Usage) -> None:
        with self.lock:
            _merge_usage(self.usage, usage)

    def _note(self, decision: Decision) -> None:
        decision.model = decision.model or self.model
        with self.lock:
            self.decisions.append(decision)

    def _failed(self, name: str, digest: str, cost: float, tool_calls: int = 0) -> None:
        """A decision that raised still cost money: a `failed` row says so,
        and `report()` bills it to the stage. Nothing is saved, so the
        failure is never served as an answer."""
        self._note(Decision(name, False, digest, cost, kind="failed", tool_calls=tool_calls))

    def _engine(self, **config: Any):
        if self.engine == GATEWAY_ENGINE:
            import gateway_engine
            return gateway_engine.GatewayEngine(gateway_engine.GatewayConfig(
                model=self.model, timeout=self.timeout,
                max_turns_budget_usd=self.budget_usd, **(self.gateway or {}), **config))
        return ENGINE(EngineConfig(model=self.model, effort=self.effort,
                                   fallback_model=self.fallback_model,
                                   timeout=self.timeout,
                                   max_turns_budget_usd=self.budget_usd, **config))

    # -- asking ------------------------------------------------------------

    def _media_digest(self, media: Sequence[Any]) -> list[list[str]]:
        """Each file's bytes and media type, for the digest: the same
        evidence under another name is the same question. A labelled file
        ({"path": ..., "label": ...}) adds its label, since "Cut A:" before
        one file is a different question from "Cut A:" before the other; an
        unlabelled one digests as it did before labels existed."""
        import gateway_engine
        rows = []
        for entry in media:
            path, label = gateway_engine.media_item(entry)
            sha = hashlib.sha256()
            try:   # in chunks: a full render may be hundreds of MB
                with open(path, "rb") as handle:
                    for chunk in iter(lambda: handle.read(1 << 20), b""):
                        sha.update(chunk)
            except OSError as exc:
                raise HarnessError(f"[{self.stage}] cannot read media {path}: {exc}") from exc
            rows.append([sha.hexdigest(), gateway_engine.media_type(path) or "",
                         *([label] if label else [])])
        return rows

    def decide(self, name: str, prompt: str, schema: dict, *,
               media: Sequence[Any] = ()) -> Any:
        """One structured answer to one question, served from the project
        when the same question was answered before. `media` (files) go to
        a gateway harness only; with none, the digest and the engine calls
        are exactly what they were before media existed."""
        media = tuple(media or ())
        if media and self.engine != GATEWAY_ENGINE:
            raise HarnessError(f"[{self.stage}] {name}: media need a harness with "
                               "engine='gateway'; claude and codex take none here")
        parts: list[Any] = [self.system, prompt, schema, self._asked_as]
        if media:
            parts.append(self._media_digest(media))
        digest = _digest(*parts)
        ask = {"media": media} if media else {}
        saved = self._cached(name, digest)
        if saved is not None:
            self._say(f"[{self.stage}] {name}: decided earlier, same inputs")
            self._note(Decision(name, True, digest, float(saved.get("cost_usd") or 0)))
            return saved["result"]
        self._say(f"[{self.stage}] {name}: asking {self._asked_as}")
        engine = self._engine(tools=())
        try:
            result = engine.ask_json(prompt, schema, system=self.system, **ask)
            # invariant 3: validate, retry once with the problem named, then
            # fail (a missing field) or let the stage record its fallback (a
            # type, a value outside its enum)
            problems = ([f"{f} is missing" for f in missing_fields(result, schema)]
                        + type_errors(result, schema)
                        + [f"{v} is not one of the schema's values"
                           for v in enum_violations(result, schema)])
            if problems:
                self._say(f"[{self.stage}] {name}: asking again ({'; '.join(problems[:3])})")
                result = engine.ask_json(prompt + RETRY_INSTRUCTION % "; ".join(problems[:10]),
                                         schema, system=self.system, **ask)
            self._check(name, result, schema)
        except (ClaudeError, HarnessError) as exc:
            self._failed(name, digest, engine.usage.cost_usd)
            if isinstance(exc, ClaudeError):
                raise HarnessError(f"[{self.stage}] {name}: {exc}") from exc
            raise
        finally:
            self._book(engine.usage)
        self._save(name, digest, result, kind="decide", cost=engine.usage.cost_usd)
        self._note(Decision(name, False, digest, engine.usage.cost_usd))
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
        if self.engine == GATEWAY_ENGINE:
            raise HarnessError(f"[{self.stage}] {name}: a gateway harness cannot inspect, "
                               "it makes one call with no tools; pass the evidence files "
                               "to decide as media")
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
            self._note(Decision(name, True, digest, float(saved.get("cost_usd") or 0),
                                kind="inspect",
                                tool_calls=int(saved.get("tool_calls") or 0)))
            return saved["result"]

        self._say(f"[{self.stage}] {name}: {self._asked_as} is reading {cwd}")
        prompt = prompt + FORM_INSTRUCTION % json.dumps(schema, ensure_ascii=False)
        engine = self._engine(tools=tuple(tools), cwd=cwd, permission_mode="dontAsk")
        texts: list[str] = []
        calls = 0
        cost = 0.0
        try:
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
            finally:
                self._book(engine.usage)
                cost += engine.usage.cost_usd

            result = read_json(cwd / form, warn=False) if form else None
            if result is None or missing_fields(result, schema) \
                    or enum_violations(result, schema):
                # The agent that looked is the one to answer: its closing JSON
                # block is the form. Only when it left none does a text-only
                # pass fill it from its words -- and that pass saw no image.
                result = fenced_json(texts)
            if result is None or missing_fields(result, schema) \
                    or enum_violations(result, schema):
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
                finally:
                    self._book(extractor.usage)
                    cost += extractor.usage.cost_usd
            self._check(name, result, schema, enums=True)
        except (ClaudeError, HarnessError) as exc:
            self._failed(name, digest, cost, tool_calls=calls)
            if isinstance(exc, ClaudeError):
                raise HarnessError(f"[{self.stage}] {name}: {exc}") from exc
            raise
        self._save(name, digest, result, kind="inspect", cost=cost, tool_calls=calls)
        self._note(Decision(name, False, digest, cost, kind="inspect", tool_calls=calls))
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
            "unmetered_calls": self.usage.unmetered_calls,
            "by_model": self.usage.by_model,
            "decisions": [{"name": d.name, "kind": d.kind, "cached": d.cached,
                           "digest": d.digest, "cost_usd": round(d.cost_usd, 4),
                           "tool_calls": d.tool_calls, "model": d.model}
                          for d in self.decisions],
        }
