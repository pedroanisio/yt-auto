"""One model under every name a route gives it (AUTO-120).

The direct CLIs name a model `opus` or `claude-opus-5-5`; Vercel's AI Gateway
names the same model `anthropic/claude-opus-5`, writes Anthropic's versions
with a dot (`anthropic/claude-haiku-4.5`), and its Claude Code picker shows
`claude-code/anthropic/claude-opus-5[1m]`. `canonical` turns any of them into
`(provider, model)`, so a family (`flags.model_family`), a price row
(`spend.price_for`, the engines' tables) and the Claude check
(`codex_engine.is_claude_model`) see one model. A price row is the table's
own provider's (`anthropic/` for Claude's, `openai/` for Codex's), and a
1M-token context (`[1m]`) is unpriced on either route, since it may bill
above the base row. A virtual model,
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

import re

from claude_engine import VIRTUAL_PROVIDER, canonical_model as canonical

__all__ = ["canonical", "VIRTUAL_PROVIDER", "is_virtual", "maker", "SDK_PREFIX",
           "SERVING_PROVIDERS", "LISTEN_MODELS"]

#: The models `yt qa --listen` may send the film's audio to (AUTO-126), by
#: gateway id, each with the providers allowed to serve it (sent as the
#: gateway's `only`). No Anthropic or OpenAI model on the gateway takes
#: audio, and the catalogue's metadata under-reports which do, so this is
#: an allowlist, not a capability filter. A model whose provider list is
#: empty is refused: which of its providers take audio input and return
#: structured output is the card's live check, not yet made. Google's
#: provider slug ("google" here, perhaps "vertex") is checked live too.
LISTEN_MODELS: dict[str, dict] = {
    "google/gemini-3.6-flash": {"providers": ("google",)},
    "xiaomi/mimo-v2.6-flash": {"providers": ()},
    "xiaomi/mimo-v2.6-pro": {"providers": ()},
}

#: What a reviewer value leads with when it names a gateway model for the
#: gateway engine (`sdk:zai/glm-5.3`, ytstudio/reviewers.py, AUTO-125).
SDK_PREFIX = "sdk:"

#: A bare OpenAI name, as the codex CLI takes it: gpt-*, the o-series, codex.
_OPENAI_BARE = re.compile(r"^(gpt-|o\d|codex)")

#: Prefixes that name who serves a model, not who made it: the clouds and
#: the hosts the gateway routes to that make no model of their own in its
#: catalogue. `bedrock/zai.glm-5` is Z.ai's model on Bedrock, so an id led
#: by one of these has no maker `maker` can read (a Claude id excepted:
#: it is Anthropic's wherever it is served).
SERVING_PROVIDERS = frozenset({
    "bedrock", "amazon-bedrock", "vertex", "google-vertex", "azure", "azure-openai",
    "baseten", "cerebras", "crusoe", "deepinfra", "fireworks", "friendli", "gmicloud",
    "groq", "inceptron", "novita", "parasail", "relace", "runinfra", "sambanova",
    "togetherai", "wafer"})


def is_virtual(name: str | None) -> bool:
    """Whether `name` is a gateway virtual model, whose family nobody knows."""
    return canonical(name)[0] == VIRTUAL_PROVIDER


def maker(name: str | None) -> str | None:
    """Who made the model, or None when nobody can tell (AUTO-125).

    A gateway id names its maker in its prefix, which is the catalogue's
    `owned_by` (`zai/glm-5.3` is zai's, `openai/gpt-oss-120b` OpenAI's,
    `google/gemma-4-31b-it` and `google/gemini-3.6-flash` both Google's). A
    Claude model is Anthropic's whichever provider serves it (the
    `codex_engine.is_claude_model` check), and a bare gpt-*, o-series or
    codex name is OpenAI's. A virtual model (`vmc/<slug>`) may route to any
    maker, so it has none; neither has a bare name nobody recognises, nor
    an id led by a serving provider (SERVING_PROVIDERS), which says who
    hosts the model and never who made it.
    `sdk:` in front is the reviewer flag's mark, not part of the id."""
    text = str(name or "").strip()
    if text.lower().startswith(SDK_PREFIX):
        text = text[len(SDK_PREFIX):]
    if not text:
        return None
    provider, model = canonical(text)
    if provider == VIRTUAL_PROVIDER:
        return None
    import codex_engine          # an engine file; models is imported by the engines' users
    if codex_engine.is_claude_model(text):
        return "anthropic"
    if provider in SERVING_PROVIDERS:
        return None
    if provider:
        return provider
    if _OPENAI_BARE.match(model.lower()):
        return "openai"
    return None
