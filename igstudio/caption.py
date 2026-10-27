"""A caption, hashtags and alt text written by the model, for `ig caption`.

The model is Claude Code through claude_engine (no tools, schema-enforced
output). It is held to the account's house standards (house.py: the hook,
caption, cta and alt sections), told what the account's recent posts opened
on (record.summary) so it does not repeat them, and asked to declare the
hook's curiosity trigger in the vocabulary yt uses (ytstudio.narrative
.HOOK_TRIGGERS) and the CTA's type, so the published post's row in the
account record carries both. Facts come from the brief; none are invented.
"""

from __future__ import annotations

from ytstudio.narrative import HOOK_TRIGGERS

from .record import CTA_TYPES

GOALS = ("saves", "shares", "comments", "follows", "reach")
#: The CTA type each goal asks for (the playbook's goal table).
CTA_FOR_GOAL = {"saves": "save", "shares": "send", "comments": "comment", "follows": "follow",
                "reach": "save"}

SCHEMA = {
    "type": "object",
    "properties": {
        "hook_line": {"type": "string", "description": "first line, at most 12 words"},
        "hook_trigger": {"type": "string", "enum": list(HOOK_TRIGGERS),
                         "description": "the curiosity trigger the hook line pulls"},
        "body": {"type": "string", "description": "2 to 5 short lines, specific"},
        "cta": {"type": "string", "description": "one line matching the goal, with a reason"},
        "cta_type": {"type": "string", "enum": list(CTA_TYPES)},
        "hashtags": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 5},
        "alt_text": {"type": "string", "description": "what the image shows, one sentence"},
        "hook_variants": {"type": "array", "items": {"type": "string"}, "minItems": 2,
                          "maxItems": 2},
    },
    "required": ["hook_line", "hook_trigger", "body", "cta", "cta_type", "hashtags", "alt_text",
                 "hook_variants"],
}


def prompt(brief: str, *, goal: str, handle: str = "", kind: str = "image",
           language: str = "", house_block: str = "", earlier_summary: str = "") -> str:
    parts = ["Write an Instagram caption for the post described below."]
    if house_block:
        parts.append(house_block)
    if earlier_summary:
        parts.append(earlier_summary)
    parts.append("\n".join([
        f"Post kind: {kind}. Goal: {goal}, so the CTA is a {CTA_FOR_GOAL.get(goal, 'save')}.",
        f"Account: {handle or 'unnamed'}.",
        f"Language: {language or 'the language of the brief'}.",
        "",
        "Brief:",
        brief.strip(),
        "",
        "Return: hook_line (the first line, at most 12 words), hook_trigger (which of "
        f"{', '.join(HOOK_TRIGGERS)} it pulls), body (2 to 5 short lines), cta and cta_type, "
        "hashtags (3 to 5, with #), alt_text, and hook_variants (two different hooks of the "
        "same length to test against). Facts, numbers and names come from the brief only.",
    ]))
    return "\n\n".join(parts)


def write(brief: str, *, goal: str = "saves", handle: str = "", kind: str = "image",
          language: str = "", model: str | None = None, engine=None, house=None,
          earlier: list | None = None) -> dict:
    """The caption, with the house digest, the repeats it would make and the
    engine's usage beside it."""
    from . import record as record_mod

    if goal not in GOALS:
        raise ValueError(f"goal must be one of {GOALS}, not {goal!r}")
    if engine is None:
        from claude_engine import ClaudeEngine, EngineConfig
        engine = ClaudeEngine(EngineConfig(tools=(), model=model))
    block = house.for_stage("caption") if house is not None else ""
    summary = record_mod.summary(earlier or [])
    data = engine.ask_json(prompt(brief, goal=goal, handle=handle, kind=kind, language=language,
                                  house_block=block, earlier_summary=summary), SCHEMA)
    data["caption"] = render(data)
    row = record_mod.fingerprint(data["caption"], kind=kind, trigger=data.get("hook_trigger", ""),
                                 cta=data.get("cta_type", ""))
    data["repeats"] = record_mod.repeats(row, earlier or [])
    if house is not None:
        data["house"] = house.digest("caption")
    usage = getattr(engine, "usage", None)
    if usage is not None:
        data["usage"] = {"cost_usd": round(float(getattr(usage, "cost_usd", 0.0)), 4),
                         "calls": int(getattr(usage, "calls", 0))}
    return data


def render(data: dict) -> str:
    """The caption as it is posted: hook, blank line, body, CTA, hashtags."""
    tags = " ".join(t if t.startswith("#") else f"#{t}" for t in data.get("hashtags", []))
    parts = [data.get("hook_line", "").strip(), "", data.get("body", "").strip(), "",
             data.get("cta", "").strip(), "", tags]
    text = "\n".join(parts)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()
