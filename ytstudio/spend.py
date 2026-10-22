"""The one shape a stage's record states its spend in.

Every `<stage>.json` carries `usage`: `{cost_usd, model, calls}` (an engine's
own counters may ride along). `engine` is not the spend's key: it names a TTS
engine in voice.json, the scene-render binary in render.json. Records written
before this carried the cost at `cost_usd` or `engine.cost_usd`; `cost_in`
reads those too, so nothing on disk is rewritten.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from claude_engine import Usage

from .core import read_json

KEYS = ("cost_usd", "model", "calls")


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


def _like(root: Path, stage: str) -> tuple[float, str] | None:
    """The median of what the stage cost in the other projects beside this one."""
    root = Path(root)
    found = []
    for other in sorted(root.parent.iterdir()) if root.parent.is_dir() else []:
        if other.is_dir() and other != root:
            cost = _reported(other, stage, None)
            if cost is not None:
                found.append((cost, other.name))
    if not found:
        return None
    found.sort()
    return found[len(found) // 2][0], ", ".join(n for _, n in found)


def stage_estimates(root: Path, stages: list[str],
                    sections: list[int] | None = None) -> list[dict]:
    """One row per stage of a pass, gate and QA included: its cost from its
    own report (`measured`), else from like projects' (`like`), else the
    default (`assumed`). An assumed vfx pass is guessed whole, its downstream
    included, so the stages after it add nothing then."""
    rows = []
    folded = False
    for stage in list(stages) + [s for s in ("gate", "qa") if s not in stages]:
        wanted = sections if stage == "vfx" else None
        have = _reported(root, stage, wanted)
        like = _like(root, stage) if have is None and not wanted else None
        if have is not None:
            row = {"stage": stage, "usd": have, "basis": "measured",
                   "source": (MANIFEST.get(stage) or "qa.json")
                   + (f" sections {','.join(map(str, wanted))}" if wanted else "")}
        elif like is not None:
            row = {"stage": stage, "usd": like[0], "basis": "like", "source": like[1]}
        elif folded:
            row = {"stage": stage, "usd": 0.0, "basis": "assumed",
                   "source": "included in the vfx default"}
        else:
            usd = VFX_PASS_USD if stage == "vfx" else DEFAULT_USD.get(stage, 0.5)
            row = {"stage": stage, "usd": usd, "basis": "assumed",
                   "source": "default, nothing reported"}
        folded = folded or (stage == "vfx" and row["basis"] == "assumed")
        rows.append(row)
    return rows


def total(rows: Iterable[dict]) -> float:
    return round(sum(r["usd"] for r in rows), 2)
