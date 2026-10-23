"""One model under every name a route gives it (AUTO-120).

The direct CLIs name a model `opus` or `claude-opus-5-5`; Vercel's AI Gateway
names the same model `anthropic/claude-opus-5`, writes Anthropic's versions
with a dot (`anthropic/claude-haiku-4.5`), and its Claude Code picker shows
`claude-code/anthropic/claude-opus-5[1m]`. `canonical` turns any of them into
`(provider, model)`, so a family (`flags.model_family`), a price row
(`spend.price_for`, the engines' tables) and the Claude check
(`codex_engine.is_claude_model`) see one model. A virtual model,
`vmc/<slug>`, may route anywhere: it has no family and no price, and a stage
refuses it as an author or a reviewer.

The function lives in `claude_engine`, since the engines are single stdlib
files that do not import this package; this module is where the pipeline
names it. Nothing here asks the network for a model list or a price.

The decision cache is not canonical: a harness digest names the model as it
was passed (`Harness._asked_as`), so a gateway id is a new question, and an
alias passed on both routes (`--model opus`) asks the same one, which is
meant, since it is the same model.
"""

from __future__ import annotations

from claude_engine import VIRTUAL_PROVIDER, canonical_model as canonical

__all__ = ["canonical", "VIRTUAL_PROVIDER", "is_virtual"]


def is_virtual(name: str | None) -> bool:
    """Whether `name` is a gateway virtual model, whose family nobody knows."""
    return canonical(name)[0] == VIRTUAL_PROVIDER
