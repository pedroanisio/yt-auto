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
from pathlib import Path
from typing import Any, Iterable

import claude_engine
from claude_engine import Usage

from .core import read_json

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


# --------------------------------------------------------------------------- #
# what a stage is expected to cost
# --------------------------------------------------------------------------- #

#: The manifest whose cost a stage reports, and where it keeps its decisions.
MANIFEST = {"research": "dossier.json", "styles": "styles.json", "script": "script.json",
            "props": "props.json", "keyframe": "keyframe.json", "voice": "voice.json",
            "retime": "retime.json", "vfx": "vfx.json", "sound": "sound.json",
            "build": "build.json", "render": "render.json", "gate": "gate.json"}

#: What a pass is guessed to cost before one has been measured.
VFX_PASS_USD = 10.0
DEFAULT_USD = {"research": 5.0, "styles": 2.0, "script": 3.0, "props": 0.0,
               "keyframe": 5.0, "voice": 2.0, "retime": 0.5, "vfx": VFX_PASS_USD,
               "sound": 0.5, "build": 0.1, "render": 0.0, "gate": 0.1, "qa": 0.5}

#: What the plan review adds to a script (AUTO-92), assumed until a record
#: measures it: the reviewer's one reading of the plan and, at most, a
#: second plan and its reading.
PLAN_REVIEW_USD = 0.5

#: What the picture-check adds under the same flag (AUTO-98), assumed until
#: a record measures it: the reviewer's reading of every shot's picture (and
#: at most one `picture-check-again`) plus the author's one `revise-picture`.
PICTURE_CHECK_USD = 0.5

#: A flag that adds to what its stage costs: stage -> flag -> (the increment,
#: the decision whose row in the stage's record shows the flag was on). The
#: stage's DEFAULT_USD is the base. A record holding that decision (or a
#: numbered one, `plan-review-2`) already counts it; one without it, or no
#: record at all, gains the increment when the flag is asked.
FLAG_USD = {"script": {"--review": (PLAN_REVIEW_USD + PICTURE_CHECK_USD,
                                    "plan-review")}}


def _flag_increments(root: Path | None, stage: str, flags: Iterable[str]) -> list[tuple[str, float]]:
    """The flags asked of `stage` whose cost the record under `root` does
    not hold, each with its increment; with no `root`, every one asked."""
    asked = [(f, *FLAG_USD[stage][f]) for f in dict.fromkeys(flags or ())
             if f in FLAG_USD.get(stage, {})]
    if not asked or root is None:
        return [(f, usd) for f, usd, _ in asked]
    rel = MANIFEST.get(stage)
    doc = read_json(Path(root) / rel, warn=False) if rel else None
    engine = doc.get("engine") if isinstance(doc, dict) else None
    rows = engine.get("decisions") if isinstance(engine, dict) else None
    names = {str(d.get("name")) for d in rows or [] if isinstance(d, dict)}
    return [(f, usd) for f, usd, decision in asked
            if not any(n == decision or n.startswith(decision + "-") for n in names)]


def _with_flags(usd: float, source: str, added: list[tuple[str, float]]) -> tuple[float, str]:
    """A price and its source with the flags' increments added and named."""
    return (round(usd + sum(x for _, x in added), 2),
            source + "".join(f" + {f} ${x:.2f}" for f, x in added))


def _section_costs(root: Path) -> dict[int, float]:
    """What each authored section cost, from the active vfx.json: its own
    spend, else the spend it was carried forward with."""
    out = {}
    for rec in (read_json(Path(root) / "vfx.json", warn=False) or {}).get("authored") or []:
        if isinstance(rec, dict) and isinstance(rec.get("n"), int):
            cost = rec.get("cost_usd") or rec.get("carried_cost_usd")
            if isinstance(cost, (int, float)) and cost > 0:
                out[rec["n"]] = float(cost)
    return out


def _qa_cost(root: Path) -> float | None:
    from . import qa  # qa reads this module; it is only asked where its passes live
    qa_json = read_json(Path(root) / "qa.json", warn=False) or {}
    card = read_json(Path(root) / qa.QA_DIR / str(qa_json.get("pass", "")) / "scorecard.json",
                     warn=False) or qa_json
    return cost_in(card)


def _reported(root: Path, stage: str, sections: list[int] | None) -> float | None:
    """What one project's own report says a run of the stage costs, or None.
    A zero on a stage that is never free is a cache replay, not a measurement."""
    root = Path(root)
    if stage == "qa":
        cost = _qa_cost(root)
    elif stage == "vfx":
        per = _section_costs(root)
        if sections:
            cost = sum(per[n] for n in sections) if all(n in per for n in sections) else None
        else:
            cost = sum(per.values()) if per else cost_in(read_json(root / "vfx.json",
                                                                    warn=False))
    else:
        rel = MANIFEST.get(stage)
        cost = cost_in(read_json(root / rel, warn=False)) if rel else None
    if cost is None or (cost <= 0 and DEFAULT_USD.get(stage, 0.5) >= 1.0):
        return None
    return round(float(cost), 2)


def _like(root: Path, stage: str, flags: Iterable[str] = ()) -> tuple[float, str] | None:
    """The median of what the stage cost in the other projects beside this
    one, each priced with the asked `flags` its own record lacks; the
    source names a flag only when it added to the project the median is."""
    root = Path(root)
    found = []
    for other in sorted(root.parent.iterdir()) if root.parent.is_dir() else []:
        if other.is_dir() and other != root:
            cost = _reported(other, stage, None)
            if cost is not None:
                added = _flag_increments(other, stage, flags)
                found.append((round(cost + sum(x for _, x in added), 2),
                              other.name + "".join(f" (with {f})" for f, _ in added)))
    if not found:
        return None
    found.sort()
    return found[len(found) // 2][0], ", ".join(n for _, n in found)


def stage_estimates(root: Path, stages: list[str],
                    sections: list[int] | None = None,
                    extras: dict[str, list[str]] | None = None) -> list[dict]:
    """One row per stage of a pass, gate and QA included: its cost from its
    own report (`measured`), else from like projects' (`like`), else the
    default (`assumed`). An assumed vfx pass is guessed whole, its downstream
    included, so the stages after it add nothing then. `extras` is the argv
    each stage would be handed: a flag in FLAG_USD adds its increment where
    the record priced from does not already hold it, and the row names it."""
    rows = []
    folded = False
    for stage in list(stages) + [s for s in ("gate", "qa") if s not in stages]:
        wanted = sections if stage == "vfx" else None
        flags = (extras or {}).get(stage) or ()
        have = _reported(root, stage, wanted)
        like = _like(root, stage, flags) if have is None and not wanted else None
        if have is not None:
            usd, source = _with_flags(
                have, (MANIFEST.get(stage) or "qa.json")
                + (f" sections {','.join(map(str, wanted))}" if wanted else ""),
                _flag_increments(root, stage, flags))
            row = {"stage": stage, "usd": usd, "basis": "measured", "source": source}
        elif like is not None:
            row = {"stage": stage, "usd": like[0], "basis": "like", "source": like[1]}
        elif folded:
            row = {"stage": stage, "usd": 0.0, "basis": "assumed",
                   "source": "included in the vfx default"}
        else:
            usd, source = _with_flags(
                VFX_PASS_USD if stage == "vfx" else DEFAULT_USD.get(stage, 0.5),
                "default, nothing reported", _flag_increments(None, stage, flags))
            row = {"stage": stage, "usd": usd, "basis": "assumed", "source": source}
        folded = folded or (stage == "vfx" and row["basis"] == "assumed")
        rows.append(row)
    return rows


def total(rows: Iterable[dict]) -> float:
    return round(sum(r["usd"] for r in rows), 2)
