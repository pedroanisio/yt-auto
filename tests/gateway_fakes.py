"""A fake `node` for the gateway engine's tests (AUTO-124).

It stands in for `node gateway/gateway-engine.mjs`: `--version` prints
$FAKE_NODE_VERSION, and any other launch records its argv, the names (never
the values) of its environment and its stdin, counts itself, then answers
from `replies.json`, the n-th launch the n-th reply (the last one repeats).
It finds those files beside itself, not through a variable, because the
engine hands the program only an allowlisted environment.
A reply is {"stdout": object or text, "stderr": text, "code": int}; "$KEY"
in stderr or stdout is replaced by the key the child was given, so a test
can check that no message carries it. No network, no model.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from unittest import mock

import claude_engine
import gateway_engine

KEY = "vck_gateway_test_key_0123456789"
MODEL = "google/gemini-3.6-flash"
#: A gateway id no table prices. MODEL has a row since the reviewer pool
#: pinned it (AUTO-125), so a test of an unpriced call names this one.
UNPRICED = "xiaomi/mimo-v2.6-flash"

FAKE_NODE = r'''#!/usr/bin/env python3
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
if sys.argv[1:] == ["--version"]:
    print(os.environ.get("FAKE_NODE_VERSION", "v24.11.0"))
    sys.exit(0)
stdin = sys.stdin.read()
count = os.path.join(here, "count")
n = int(open(count).read()) if os.path.exists(count) else 0
with open(count, "w") as f:
    f.write(str(n + 1))
with open(os.path.join(here, "calls.jsonl"), "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "env": sorted(os.environ),
                        "stdin": stdin}) + "\n")
replies = json.load(open(os.path.join(here, "replies.json")))
reply = replies[min(n, len(replies) - 1)]
key = os.environ.get("AI_GATEWAY_API_KEY", "")
if reply.get("stderr"):
    sys.stderr.write(reply["stderr"].replace("$KEY", key))
out = reply.get("stdout")
if out is not None:
    text = out if isinstance(out, str) else json.dumps(out)
    print(text.replace("$KEY", key))
sys.exit(reply.get("code", 0))
'''

#: Every variable the engine reads, cleared so the machine running the tests
#: cannot leak into them.
VARS = ("YT_GATEWAY", "YT_GATEWAY_AUTH", "YT_GATEWAY_URL", "AI_GATEWAY_API_KEY",
        "YT_GATEWAY_NODE", "GATEWAY_PRICES", "CLAUDE_PRICES", "YT_ENGINE")

USAGE = {"inputTokens": 1200, "outputTokens": 300, "totalTokens": 1500,
         "inputTokenDetails": {"noCacheTokens": 1000, "cacheReadTokens": 150,
                               "cacheWriteTokens": 50}}


def ok(output=None, *, model=MODEL, cost="0.0012", generation_cost=None,
       routing=None, usage=None) -> dict:
    """The program's line for a call that answered."""
    return {"stdout": {"ok": True, "output": {"answer": "yes"} if output is None else output,
                       "usage": USAGE if usage is None else usage,
                       "finishReason": "stop", "modelId": model,
                       "generationId": "gen_01TEST", "cost": cost,
                       "generationCost": generation_cost, "routing": routing},
            "code": 0}


def err(kind: str, code: int, *, usage=None, **fields) -> dict:
    """The program's line for a call that failed."""
    line = {"ok": False, "error": {"kind": kind, **fields}}
    if usage is not None:
        line["usage"] = usage
    return {"stdout": line, "code": code}


class FakeNode:
    """A temporary program directory with a fake `node` and an installed `ai`."""

    def __init__(self, case, *, ai_version: str = gateway_engine.AI_VERSION) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        case.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.program_dir = self.dir / "gateway"
        (self.program_dir / "node_modules" / "ai").mkdir(parents=True)
        (self.program_dir / gateway_engine.PROGRAM).write_text("// fake\n")
        if ai_version:
            (self.program_dir / "node_modules" / "ai" / "package.json").write_text(
                json.dumps({"name": "ai", "version": ai_version}))
        self.exe = self.dir / "node"
        self.exe.write_text(FAKE_NODE)
        self.exe.chmod(self.exe.stat().st_mode | stat.S_IEXEC)
        saved = {k: os.environ.pop(k) for k in list(os.environ) if k in VARS}
        case.addCleanup(os.environ.update, saved)
        patch = mock.patch.dict(os.environ, {"AI_GATEWAY_API_KEY": KEY,
                                             "YT_GATEWAY_NODE": str(self.exe)})
        patch.start()
        case.addCleanup(patch.stop)
        for target, name, value in ((gateway_engine, "PROGRAM_DIR", self.program_dir),
                                    (claude_engine.time, "sleep", lambda s: None)):
            p = mock.patch.object(target, name, value)
            p.start()
            case.addCleanup(p.stop)
        gateway_engine._NODE_VERSIONS.clear()
        case.addCleanup(gateway_engine._NODE_VERSIONS.clear)
        claude_engine.DEFAULTS.clear()
        case.addCleanup(claude_engine.DEFAULTS.clear)
        self.replies(ok())

    def replies(self, *replies: dict) -> None:
        (self.dir / "replies.json").write_text(json.dumps(list(replies)))

    @property
    def count(self) -> int:
        path = self.dir / "count"
        return int(path.read_text()) if path.exists() else 0

    def calls(self) -> list[dict]:
        path = self.dir / "calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def requests(self) -> list[dict]:
        return [json.loads(c["stdin"]) for c in self.calls()]

    def engine(self, **kw) -> gateway_engine.GatewayEngine:
        kw.setdefault("model", MODEL)
        kw.setdefault("executable", str(self.exe))
        kw.setdefault("timeout", 10.0)
        kw.setdefault("retry_backoff", 0.0)
        return gateway_engine.GatewayEngine(gateway_engine.GatewayConfig(**kw))
