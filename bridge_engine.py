"""Cooperative, file-based engine serviced by an active chat session.

No API client, credentials, network listener or background model is involved.
The pipeline waits while the operator reads a request, uses the chat's tools,
and submits an answer. See docs/chat-bridge.md for the worker contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import uuid
from dataclasses import replace
from pathlib import Path

from claude_engine import (Answer, ClaudeEngine, ClaudeExecutionError,
                           EngineConfig, Event, READ_ONLY_TOOLS, Usage)

PROTOCOL = "yt-chat-bridge/1"


class BridgeError(ClaudeExecutionError):
    def __init__(self, message):
        super().__init__(message, transient=False)


def queue_root() -> Path:
    value = os.environ.get("YT_BRIDGE_DIR")
    if not value:
        raise BridgeError("Set YT_BRIDGE_DIR to a dedicated local request queue")
    return Path(value).resolve()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic_json(path: Path, value, *, exclusive=False):
    """Publish complete JSON; an exclusive response can never replace another."""
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        if exclusive:
            os.link(name, path)
        else:
            os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode()).hexdigest()


def validator(schema):
    try:
        from jsonschema.validators import validator_for
        from referencing import Registry
    except ImportError as exc:
        raise BridgeError("Bridge schema validation requires pip install -r requirements-bridge.txt") from exc

    def no_remote(uri):
        raise BridgeError(f"External schema references are unsupported: {uri}")

    cls = validator_for(schema)
    cls.check_schema(schema)
    return cls(schema, registry=Registry(retrieve=no_remote))


def validate_response(request, response):
    if not isinstance(response, dict):
        raise BridgeError("Response must be an object")
    if (response.get("request_id") != request["id"]
            or response.get("request_digest") != request["digest"]):
        raise BridgeError("Response does not match this request ID and digest")
    if response.get("status") not in ("ok", "error"):
        raise BridgeError("Response status must be ok or error")
    if response["status"] == "error":
        if not isinstance(response.get("error"), str) or not response["error"].strip():
            raise BridgeError("An error response needs an explanation")
        return
    if not isinstance(response.get("text"), str):
        raise BridgeError("Response needs text (use an empty string for structured output)")
    if request["schema"] is not None:
        if "structured" not in response:
            raise BridgeError("Response needs structured output")
        try:
            validator(request["schema"]).validate(response["structured"])
        except Exception as exc:
            raise BridgeError(f"Response fails its JSON schema: {exc}") from exc
    evidence = response.get("evidence", [])
    if not isinstance(evidence, list):
        raise BridgeError("evidence must be a list")
    seen = set()
    for event in evidence:
        if not isinstance(event, dict):
            raise BridgeError("Each evidence item must be an object")
        tool = event.get("tool")
        if tool not in request["tools"]:
            raise BridgeError(f"Evidence uses a tool this request did not allow: {tool}")
        args = event.get("input")
        field = {"WebSearch": "query", "WebFetch": "url", "Read": "file_path",
                 "Glob": "pattern", "Grep": "pattern"}[tool]
        if not isinstance(args, dict) or not isinstance(args.get(field), str) or not args[field]:
            raise BridgeError(f"{tool} evidence needs input.{field}")
        if not isinstance(event.get("reference"), str) or not event["reference"]:
            raise BridgeError("Evidence needs a reference to the actual tool result")
        if not isinstance(event.get("summary"), str) or not event["summary"]:
            raise BridgeError("Evidence needs a short factual summary")
        seen.add(tool)
    # Web requests must be researched, not silently answered from memory.
    required = set(request["tools"]) & {"WebSearch", "WebFetch"}
    if not required <= seen:
        raise BridgeError(f"Missing actual web-tool evidence: {sorted(required - seen)}")
    if request["tools"] and not evidence:
        raise BridgeError("A tool-enabled request needs evidence or an error response")


def request_dir(root, request_id):
    if not re.fullmatch(r"[0-9a-f]{32}", request_id):
        raise BridgeError("Invalid request ID")
    return Path(root) / request_id


def submit(root, request_id, response):
    folder = request_dir(root, request_id)
    request = read(folder / "request.json")
    if (folder / "finished.json").exists() or time.time() >= request["expires_at"]:
        raise BridgeError("Request is no longer active; do not submit a stale answer")
    validate_response(request, response)
    try:
        atomic_json(folder / "response.json", response, exclusive=True)
    except FileExistsError as exc:
        raise BridgeError("A response has already been submitted") from exc


def pending(root):
    for path in sorted(Path(root).glob("*/request.json")):
        if (path.parent / "response.json").exists() or (path.parent / "finished.json").exists():
            continue
        request = read(path)
        if time.time() < request["expires_at"]:
            yield request


class BridgeEngine(ClaudeEngine):
    """The research/script engine API, with buffered tool evidence on stream()."""

    def spawn(self, **overrides):
        return BridgeEngine(replace(self.config), **overrides)

    def _exchange(self, prompt, *, resume=False, tools=..., system=None, schema=None):
        if resume:
            raise BridgeError("Bridge v1 does not support session resume")
        if self.config.max_turns_budget_usd is not None:
            raise BridgeError("Bridge cannot meter chat billing or enforce a USD budget")
        allowed = list((self.config.tools if tools is ... else tools) or ())
        if any(t not in READ_ONLY_TOOLS for t in allowed):
            raise BridgeError("Bridge v1 supports reasoning and read-only research, not code authors")
        if schema is not None:
            validator(schema)
        timeout = self.config.timeout or 900
        root = queue_root()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        request_id = uuid.uuid4().hex
        folder = root / request_id
        folder.mkdir(mode=0o700)
        request = {
            "protocol": PROTOCOL, "id": request_id,
            "created_at": time.time(), "expires_at": time.time() + timeout,
            "prompt": prompt, "system": system if system is not None else self.config.system_prompt,
            "append_system": self.config.append_system_prompt,
            "schema": schema, "tools": allowed,
            "cwd": str(Path(self.config.cwd or os.getcwd()).resolve()),
            "requested_model": self.config.model, "executor": "active-chat-session",
            "accounting": "unmetered", "streaming": "buffered",
        }
        request["digest"] = digest(request)
        atomic_json(folder / "request.json", request, exclusive=True)
        self.pending_session_id = request_id
        print(f"[bridge] awaiting {request_id}", file=sys.stderr, flush=True)
        started = time.monotonic()
        state = "error"
        try:
            while time.monotonic() - started < timeout:
                response_file = folder / "response.json"
                if response_file.exists():
                    response = read(response_file)
                    validate_response(request, response)
                    if response["status"] == "error":
                        raise BridgeError(response["error"])
                    payload = {"type": "result", "subtype": "success",
                               "result": response["text"], "session_id": request_id,
                               "model": "active-chat-session", "total_cost_usd": None,
                               "unmetered": True, "duration_ms": int((time.monotonic() - started) * 1000),
                               "structured_output": response.get("structured"),
                               "bridge_request": request_id, "bridge_digest": request["digest"]}
                    usage = Usage()
                    usage.add(payload)
                    self.usage.merge(usage)
                    self._log.append(payload)
                    self.session_id = request_id
                    answer = Answer(text=response["text"], structured=response.get("structured"),
                                    session_id=request_id, model="active-chat-session",
                                    usage=usage, raw=payload)
                    state = "completed"
                    return answer, response.get("evidence", [])
                time.sleep(0.1)
            state = "expired"
            raise BridgeError(f"Bridge request {request_id} timed out; no active worker answered")
        finally:
            atomic_json(folder / "finished.json", {"status": state, "at": time.time()})

    def ask(self, prompt, *, resume=False, tools=..., system=None, schema=None):
        answer, _ = self._exchange(prompt, resume=resume, tools=tools, system=system, schema=schema)
        return answer

    def stream(self, prompt, *, resume=False, tools=..., system=None, on_event=None):
        answer, evidence = self._exchange(prompt, resume=resume, tools=tools, system=system)
        events = [Event(type="assistant", subtype="tool_use", tool_name=e["tool"],
                        tool_input=e["input"], raw={"bridge_evidence": e}) for e in evidence]
        events += [Event(type="assistant", text=answer.text),
                   Event(type="result", subtype="success", text=answer.text, raw=answer.raw)]
        for event in events:
            if on_event:
                on_event(event)
            yield event

    def agent(self, prompt, **kwargs):
        return self.ask(prompt, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pending", help="list active request IDs and prompt excerpts")
    show = sub.add_parser("show", help="read the complete request, including schema")
    show.add_argument("id")
    accept = sub.add_parser("submit", help="validate and publish a worker response")
    accept.add_argument("id")
    accept.add_argument("file", help="response JSON path, or - for stdin")
    args = parser.parse_args()
    try:
        root = queue_root()
        if args.command == "pending":
            for request in pending(root):
                print(json.dumps({"id": request["id"], "tools": request["tools"],
                                  "prompt": request["prompt"][:180]}, ensure_ascii=False))
        elif args.command == "show":
            print(json.dumps(read(request_dir(root, args.id) / "request.json"),
                             ensure_ascii=False, indent=2))
        else:
            response = json.load(sys.stdin) if args.file == "-" else read(args.file)
            submit(root, args.id, response)
            print(f"Accepted {args.id}")
    except (BridgeError, ValueError, OSError) as exc:
        parser.exit(1, f"bridge: {exc}\n")


if __name__ == "__main__":
    main()
