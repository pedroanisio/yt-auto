"""The one shape a stage's record states its spend in.

Every `<stage>.json` carries `usage`: `{cost_usd, model, calls}` (an engine's
own counters may ride along). `engine` is not the spend's key: it names a TTS
engine in voice.json, the scene-render binary in render.json. Records written
before this carried the cost at `cost_usd` or `engine.cost_usd`; `cost_in`
reads those too, so nothing on disk is rewritten.

Every model has a price. A call cut off by its timeout never gets the CLI's
`result` event that carries the real cost, so the engine estimates it from the
stream's tokens and `PRICES`; an empty table books such a call at $0 and
leaves the session's budget meter blind. `PRICES` below is keyed by the model
ids the CLI reports in `modelUsage`; `ytstudio.harness` installs it into the
engine's own table on import, and `$CLAUDE_PRICES` still overrides a row. The
short names a stage is configured with (`sonnet`) are `price_for`'s to
resolve, not rows: the engine's table stays without them, so a session on a
short name keeps the CLI's own budget stop beside the stream meter.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any

import claude_engine
from claude_engine import Usage

KEYS = ("cost_usd", "model", "calls")


def _row(input: float, output: float, cached_input: float | None = None) -> dict[str, float]:
    """USD per 1M tokens. A cache read costs a tenth of the input price and a
    cache write a quarter more, unless the model's sheet says otherwise."""
    return {"input": input, "cached_input": cached_input if cached_input is not None
            else round(input / 10, 4), "output": output, "cache_creation": input * 1.25}


#: USD per 1M tokens (`input`, `cached_input`, `output`, `cache_creation`) by
#: the model id the CLI reports in `modelUsage`. First-party API rates as
#: published; a model missing here is booked at $0 when cut off, and `yt
#: doctor` warns when a record bills one.
PRICES: dict[str, dict[str, float]] = {
    "claude-fable-5-1": _row(10.0, 50.0, 0.25),
    "claude-fable-5": _row(10.0, 50.0),
    "claude-opus-5-5": _row(4.0, 20.0, 0.20),
    "claude-opus-5": _row(5.0, 25.0),
    "claude-opus-4-8": _row(5.0, 25.0),
    "claude-opus-4-7": _row(5.0, 25.0),
    "claude-opus-4-6": _row(5.0, 25.0),
    "claude-sonnet-5-5": _row(2.0, 10.0, 0.20),
    "claude-sonnet-5": _row(2.0, 10.0),
    "claude-sonnet-4-6": _row(3.0, 15.0),
    # Haiku 5.5's rate above 100K prompt tokens (5x) is not modelled: a long
    # cut-off call is under-estimated.
    "claude-haiku-5-5": _row(0.10, 0.50),
    "claude-haiku-4-5": _row(1.0, 5.0),
}
# The API serves Haiku 4.5 under its dated snapshot id, which is what the
# stream reports; the engine looks a row up exactly.
PRICES["claude-haiku-4-5-20251001"] = PRICES["claude-haiku-4-5"]

#: The short names a stage is configured with (`--model sonnet`), priced by
#: `price_for` as the generation the CLI resolves them to. Not rows.
ALIASES = {"fable": "claude-fable-5-1", "opus": "claude-opus-5-5",
           "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}


def price_for(model: Any, env: dict | None = None) -> dict[str, float] | None:
    """The price row the engine would use for `model`: this table, the
    engine's own, then `$CLAUDE_PRICES`; a short name resolves to its
    generation's row; None when nobody priced it."""
    table: dict[str, Any] = {**PRICES, **claude_engine.PRICES}
    raw = (os.environ if env is None else env).get("CLAUDE_PRICES")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                table.update(parsed)
        except (ValueError, TypeError):
            pass
    name = str(model or "")
    row = table.get(name)
    if row is None and name in ALIASES:
        row = table.get(ALIASES[name])
    return row if isinstance(row, dict) and row else None


def install_prices(table: dict[str, dict[str, float]] | None = None) -> None:
    """Give the engine this table, keeping any row it already holds (a test
    or an operator set it first)."""
    table = claude_engine.PRICES if table is None else table
    for model, row in PRICES.items():
        table.setdefault(model, dict(row))


def record(cost_usd: Any = 0.0, model: Any = "", calls: Any = 0, **more: Any) -> dict:
    """The shared keys, normalised; `more` rides along (e.g. `by_model`)."""
    return {"cost_usd": round(float(cost_usd or 0.0), 4), "model": str(model or ""),
            "calls": int(calls or 0), **more}


def from_report(report: dict) -> dict:
    """From `Harness.report()`: what the stage's decisions paid, under which model."""
    return record(report.get("cost_usd"), report.get("model"), report.get("calls"),
                  by_model=report.get("by_model") or {},
                  unmetered_calls=report.get("unmetered_calls", 0))


def from_usage(usage: Any, model: Any = "") -> dict:
    """From an engine's `Usage`, keeping its counters beside the shared keys."""
    return {**asdict(usage), **record(usage.cost_usd, model, usage.calls)}


def to_usage(doc: dict) -> Usage:
    """The engine's `Usage` back from a record's `usage`, leaving out the
    keys that are not its fields (`model`)."""
    return Usage(**{k: v for k, v in doc.items() if k in Usage.__dataclass_fields__})


def stamp(doc: dict, model: Any = "") -> dict:
    """`doc` with its `usage` carrying the shared keys, whatever counters it
    already held. A copy: the caller's dict is not touched."""
    have = doc.get("usage") if isinstance(doc.get("usage"), dict) else {}
    return {**doc, "usage": {**have, **record(have.get("cost_usd"),
                                              have.get("model") or model,
                                              have.get("calls"))}}


def cost_in(doc: Any) -> float | None:
    """What a stage's record says it cost: `usage`, else the older `cost_usd`,
    else the older `engine.cost_usd`; None when it says nothing. Voice's
    synthesis cost, when it has one, is added."""
    if not isinstance(doc, dict):
        return None
    # A bridge call has no bill to measure. Do not turn a legacy numeric zero
    # into a measured-free estimate for a subsequent production run.
    for item in (doc, doc.get("usage"), doc.get("engine")):
        if isinstance(item, dict) and item.get("unmetered_calls"):
            return None
    for path in (("usage", "cost_usd"), ("cost_usd",), ("engine", "cost_usd")):
        value: Any = doc
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            synth = doc.get("synth")
            extra = synth.get("cost_usd") if isinstance(synth, dict) else 0
            return float(value) + float(extra or 0)
    return None
