"""A script reviewer from another maker, through the gateway engine (AUTO-125).

Opt-in only. The claude and codex CLIs stay every stage's reviewers, and a
`--reviewer-model` that names one of their models takes exactly today's
path: `resolve` returns None for it before anything here runs. Two values
are new, on `yt script --review` and `python -m ytstudio.narrative
calibrate` only:

    pool               the first row of POOL whose maker is not the author's
    sdk:<provider/id>  that row of POOL, by its gateway id

Either one needs the gateway: YT_GATEWAY=vercel and the gateway engine's
own prerequisites (`gateway_engine.readiness`: the key, Node 22 or later,
gateway/node_modules). Without them the stage is refused with exit 2
before any engine is built, naming every missing piece; nothing falls
back to a CLI reviewer, since that would be a reviewer nobody asked for.

Why a maker and not a family: every CLI reviewer is another Claude or
Codex model, so `--model opus` could be read only by another Claude line.
A model from another maker (Z.ai, Moonshot, Google, DeepSeek, Alibaba,
Mistral) is the reviewer the CLIs cannot give. The maker is the gateway
id's prefix, the catalogue's `owned_by` (`models.maker`), never the
provider that serves it: Bedrock serves Z.ai and DeepSeek models too.

Only text decisions go this way. The plan review, the picture check (which
reads each shot's `visual` as text) and the story review's readers are
`Harness.decide` calls, which the gateway engine answers; keyframe, vfx,
probe and qa review through `Harness.inspect`, an agent that opens PNGs,
which it does not (`agent_refusal`).

POOL is pinned, never widened by what the gateway lists today:

  - Each row names its maker, an upper-bound price (USD per 1M tokens, the
    dearest of its providers on `priced_on`) and the providers allowed to
    serve it, sent as the gateway's `only`, with `has:
    ["structured-output"]`. A row with no pinned provider is not offered:
    without `only` the gateway may route to any provider, an fp4 one or one
    priced above the row.
  - `preflight` reads the catalogue (for `deprecated_at`) and the model's
    endpoints, unauthenticated, and keeps the pinned providers that are
    live now (status 0), offer structured output, are not of a quantization
    in $YT_REVIEWER_EXCLUDE_QUANT (default fp4), and are priced at or below
    the row. When the fetch fails the pinned list stands, with one note,
    but only for a row whose providers were checked live (`verified`); an
    unchecked row is refused then, since nothing would keep an fp4
    provider or one billing above the row off it. What it found is kept
    in this process only and recorded in script.json.
  - Every row's price is at or below opus's, so the plan review's and the
    picture check's fixed estimates still bound a pool reviewer.

The endpoint fields read here (`status`, `quantization`, `tags` or
`supported_parameters`, per-token `pricing`) are the research's reading of
`/v1/models/{id}/endpoints`, which Vercel does not document; an endpoint
whose fields cannot be read is left out, so a changed shape narrows the
pool, never widens it. Python validates every answer whatever the provider
says it enforces (invariant 3).

Stdlib only. The scripts reviewed this way are sent to the listed
third-party providers.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from . import flags, gateway, models
from .models import maker

__all__ = ["POOL", "POOL_VALUE", "PREFIX", "HAS", "EXCLUDE_QUANT_ENV", "Resolved",
           "maker", "is_gateway", "choose", "preflight", "admit", "resolve",
           "harness_kwargs",
           "agent_refusal", "priced_as", "clear_cache"]

#: The value that asks for the pool's choice, and the mark of one pinned row.
POOL_VALUE = "pool"
PREFIX = models.SDK_PREFIX
#: What every provider serving a pool reviewer must offer.
HAS = ("structured-output",)
#: Quantizations a provider may not serve the reviewer at, comma-separated.
EXCLUDE_QUANT_ENV = "YT_REVIEWER_EXCLUDE_QUANT"
DEFAULT_EXCLUDE_QUANT = "fp4"
#: The gateway's catalogue and a model's providers; neither needs the key.
MODELS_URL = "https://ai-gateway.vercel.sh/v1/models"
ENDPOINTS_URL = "https://ai-gateway.vercel.sh/v1/models/{id}/endpoints"
FETCH_TIMEOUT = 10.0


def _row(model: str, input: float, output: float, providers: tuple[str, ...],
         verified: bool = False) -> dict:
    return {"model": model, "maker": model.split("/", 1)[0],
            "price": {"input": input, "output": output},
            "providers": providers, "priced_on": "2026-10-23", "verified": verified}


#: The reviewers, in the order `pool` tries them (the owner decides it: the
#: card's open question 1). List prices from the gateway's catalogue on
#: 2026-10-23. Only the rows whose SO providers the research named carry
#: them; the others wait for the card's live check (each provider's
#: endpoint fields and one call per script schema) and are passed over
#: until then. `verified` says that check was made for the row's providers
#: and price; none has been yet, so a row whose providers come from the
#: research alone is used only when preflight reads them live, never on
#: its pinned list after a failed read. No anthropic/ or openai/ row: the
#: CLIs are those makers'.
POOL: tuple[dict, ...] = (
    # 22 of 24 providers offer SO; baseten, crusoe, deepinfra, inceptron and
    # togetherai serve fp4. Which of the rest pass is the live check's.
    _row("zai/glm-5.3", 1.20, 4.00, ()),
    # SO on 17 of 17; relace and wafer bill output above the list price.
    _row("moonshotai/kimi-k3", 3.00, 15.00, ()),
    # Its providers and which offer SO are unread.
    _row("google/gemini-3.6-flash", 0.75, 3.75, ()),
    # SO on 18 of 22.
    _row("deepseek/deepseek-v4.1-flash", 0.075, 0.30, ()),
    # SO on these five; alibaba and novita serve it without.
    _row("alibaba/qwen3.8-27b", 0.50, 3.00,
         ("cerebras", "deepinfra", "parasail", "runinfra", "wafer")),
    # One provider, so no failover.
    _row("mistral/mistral-large-3", 0.50, 1.50, ("mistral",)),
)


@dataclass(frozen=True)
class Resolved:
    """The pool reviewer a stage builds its reviewing harnesses on."""

    model: str                      # the gateway id, never `pool` or `sdk:...`
    maker: str
    providers: tuple[str, ...]      # sent as `only`
    has: tuple[str, ...]
    price: dict                     # the row's, USD per 1M tokens
    price_source: str               # "live" or "pinned"
    fetched_at: str | None
    live_price: dict | None = field(default=None, compare=False)

    def record(self) -> dict:
        """What script.json keeps beside each reading it made."""
        return {"model": self.model, "maker": self.maker, "providers": list(self.providers),
                "price_source": self.price_source, "fetched_at": self.fetched_at}


def is_gateway(value: Any) -> bool:
    """Whether a --reviewer-model value asks for the pool (`pool`, `sdk:...`)."""
    text = str(value or "").strip().lower()
    return text == POOL_VALUE or text.startswith(PREFIX)


def agent_refusal(stage: str, value: Any) -> str:
    """The error for a pool value on a stage whose reviewer is an agent
    that opens images (keyframe, vfx, probe), else ""."""
    if not is_gateway(value):
        return ""
    return (f"error: --reviewer-model {value} is the gateway reviewer pool, which `yt "
            f"{stage}` cannot use: its reviewer opens images through an agent, and the "
            "gateway engine answers text decisions only; pass a claude or codex model")


def choose(author: str, avoid: tuple[str, ...] = ()) -> dict:
    """The first row of POOL with pinned providers whose maker is neither
    the author's nor in `avoid`. No network."""
    own = maker(author)
    if own is None:
        raise ValueError(f"error: nobody can say who made --model {author} (its maker is "
                         "unknown), so no pool reviewer can be shown to come from another "
                         "maker; pass the author as a claude, codex or provider/model id")
    for row in POOL:
        if row["maker"] != own and row["maker"] not in avoid and row["providers"]:
            return row
    raise ValueError(f"error: the reviewer pool (reviewers.POOL) has no row left for "
                     f"--model {author}: none with pinned providers comes from another "
                     f"maker than {', '.join((own, *avoid))}")


def _pinned(model_id: str) -> dict:
    row = next((r for r in POOL if r["model"] == model_id), None)
    if row is None:
        raise ValueError(f"error: {model_id} has no pinned row in reviewers.POOL ("
                         f"{', '.join(r['model'] for r in POOL)}): a reviewer needs a price "
                         "and an allowlist of providers before it is asked")
    if not row["providers"]:
        raise ValueError(f"error: {model_id}'s row in reviewers.POOL has no pinned "
                         "providers yet: its live check is outstanding")
    return row


def priced_as(reviewer: Any, author: Any = "sonnet") -> str:
    """The model an estimate prices for a --reviewer-model value: the pool's
    choice for the author (no network), a pinned id without its `sdk:`, any
    other value as given."""
    value = str(reviewer or "").strip()
    if value.lower() == POOL_VALUE:
        try:
            return choose(str(author or ""))["model"]
        except ValueError:
            return value
    if value.lower().startswith(PREFIX):
        return value[len(PREFIX):]
    return value


# --------------------------------------------------------------------------- #
# preflight
# --------------------------------------------------------------------------- #

#: What preflight read live, for this process only, by model id, the
#: quantizations excluded and the row's providers: a stricter exclusion or
#: another allowlist is another answer. A failed read is not kept, so the
#: next resolve in the process reads again.
_CACHE: dict[tuple, dict] = {}


def clear_cache() -> None:
    _CACHE.clear()


def _get_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _excluded(env: Mapping[str, str]) -> set[str]:
    raw = env.get(EXCLUDE_QUANT_ENV)
    raw = DEFAULT_EXCLUDE_QUANT if raw is None else raw
    return {q.strip().lower() for q in raw.split(",") if q.strip()}


def _per_million(value: Any) -> float | None:
    """A per-token price (a decimal string) in USD per 1M tokens."""
    try:
        return round(float(value) * 1_000_000, 6)
    except (TypeError, ValueError):
        return None


def _endpoint(raw: Any) -> dict | None:
    """One provider of the endpoints answer, or None when its name cannot be read."""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("provider_name") or raw.get("provider")
               or str(raw.get("tag") or "").split("/")[0]).strip().lower()
    if not name:
        return None
    pricing = raw.get("pricing") if isinstance(raw.get("pricing"), dict) else {}
    tags = [str(t).lower() for t in raw.get("tags") or []]
    params = [str(t).lower() for t in raw.get("supported_parameters") or []]
    return {"provider": name, "status": raw.get("status"),
            "quantization": str(raw.get("quantization") or "").lower(),
            "structured": "structured-output" in tags or "structured_outputs" in params,
            "input": _per_million(pricing.get("prompt", pricing.get("input"))),
            "output": _per_million(pricing.get("completion", pricing.get("output")))}


def _endpoints(raw: Any) -> list[dict]:
    data = raw.get("data") if isinstance(raw, dict) else None
    rows = (data.get("endpoints") if isinstance(data, dict)
            else raw.get("endpoints") if isinstance(raw, dict) else None)
    if not isinstance(rows, list):
        raise ValueError("the endpoints answer holds no list of endpoints")
    return [e for e in (_endpoint(r) for r in rows) if e is not None]


def _listed(raw: Any, model_id: str) -> dict | None:
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, list):
        raise ValueError("the catalogue answer holds no list of models")
    return next((m for m in data if isinstance(m, dict) and m.get("id") == model_id), None)


def _why_not(found: dict | None, price: dict, excluded: set[str]) -> str | None:
    """Why a pinned provider may not serve the reviewer now, or None."""
    if found is None:
        return "not listed now"
    if found["status"] != 0:
        return f"status {found['status']}"
    if not found["structured"]:
        return "no structured output"
    if found["quantization"] in excluded:
        return found["quantization"]
    if found["input"] is None or found["output"] is None:
        return "no price listed"
    if found["input"] > price["input"] or found["output"] > price["output"]:
        return (f"priced {found['input']:g}/{found['output']:g}, above the row's "
                f"{price['input']:g}/{price['output']:g}")
    return None


def preflight(row: dict, fetch: Callable[[str], Any] | None = None,
              env: Mapping[str, str] | None = None) -> dict:
    """The row's providers that may serve it now: {providers, price_source,
    live_price, fetched_at}. Narrows, never widens. A deprecated model, or
    one with no provider left, raises ValueError naming why; a fetch that
    fails keeps the pinned list ("pinned") with one note."""
    model_id = row["model"]
    env = os.environ if env is None else env
    pinned = tuple(row["providers"])
    excluded = _excluded(env)
    key = (model_id, frozenset(excluded), pinned)
    if key in _CACHE:
        return dict(_CACHE[key])
    fetch = fetch or _get_json
    try:
        listed = _listed(fetch(MODELS_URL), model_id)
        endpoints = _endpoints(fetch(ENDPOINTS_URL.format(id=model_id))) if listed else []
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        if not row.get("verified"):
            raise ValueError(
                f"error: the gateway's list of {model_id}'s providers could not be read "
                f"({exc}), and the row's pinned providers ({', '.join(pinned)}) were not "
                "checked live for quantization, structured output or price (reviewers.POOL "
                "`verified`), so nothing would keep an excluded or dearer provider off it; "
                "run again when the gateway's catalogue can be read") from exc
        print(f"note: reviewer pool: the gateway's list of {model_id}'s providers could not "
              f"be read ({exc}); its pinned providers stand: {', '.join(pinned)}",
              file=sys.stderr)
        return {"providers": pinned, "price_source": "pinned", "live_price": None,
                "fetched_at": None}
    if listed is None:
        raise ValueError(f"error: {model_id} is not in the gateway's catalogue now")
    if listed.get("deprecated_at"):
        raise ValueError(f"error: {model_id} is deprecated (deprecated_at "
                         f"{listed['deprecated_at']}): a pool reviewer must be current")
    by_name = {e["provider"]: e for e in endpoints}
    kept, dropped = [], []
    for name in pinned:
        why = _why_not(by_name.get(name), row["price"], excluded)
        (dropped.append(f"{name} ({why})") if why else kept.append(name))
    if not kept:
        raise ValueError(f"error: none of {model_id}'s pinned providers may serve it now: "
                         + "; ".join(dropped))
    if dropped:
        print(f"note: reviewer pool: {model_id} is served by {', '.join(kept)} only; left "
              f"out: {'; '.join(dropped)}", file=sys.stderr)
    found = {"providers": tuple(kept), "price_source": "live",
             "live_price": {k: max(by_name[n][k] for n in kept) for k in ("input", "output")},
             "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _CACHE[key] = found
    return dict(found)


# --------------------------------------------------------------------------- #
# the entry point
# --------------------------------------------------------------------------- #

def admit(author: str, value: Any, *, env: Mapping[str, str] | None = None) -> dict:
    """The POOL row `value` (`pool` or `sdk:...`) names for `author`, once
    everything this machine can say without the network allows it. Raises
    ValueError naming, together, what the row (unknown author, no pinned
    row) and the gateway (the switch, the engine's readiness) lack; then
    the maker rule. `yt run --dry-run` stops here: it fetches nothing, and
    the script stage reads the providers when it runs."""
    env = os.environ if env is None else env
    value = str(value or "").strip()
    problems: list[str] = []
    row = None
    try:
        row = (choose(author) if value.lower() == POOL_VALUE
               else _pinned(value[len(PREFIX):].strip()))
    except ValueError as exc:
        problems.append(str(exc).removeprefix("error: "))
    if not gateway.active(env):
        problems.append(f"{gateway.SWITCH}={gateway.VERCEL} is not set: a pool reviewer runs "
                        "on the gateway engine, which is opt-in")
    import gateway_engine           # a top-level engine file, as the harness imports it
    problems += gateway_engine.readiness(env=dict(env))
    if problems:
        raise ValueError(f"error: --reviewer-model {value} cannot be used: "
                         + "; ".join(problems))
    own = flags.own_reviewer(author, PREFIX + row["model"])
    if own:
        raise ValueError(own)
    return row


def resolve(author: str, value: Any, *, env: Mapping[str, str] | None = None,
            fetch: Callable[[str], Any] | None = None) -> Resolved | None:
    """The pool reviewer `value` names for `author`, or None for any value
    that is not `pool` or `sdk:...` (a CLI reviewer: nothing here runs).
    Raises ValueError, a message for the operator, when it cannot be used:
    what `admit` refuses, before anything is fetched; then the providers
    live now (`preflight`)."""
    if not is_gateway(value):
        return None
    env = os.environ if env is None else env
    row = admit(author, value, env=env)
    found = preflight(row, fetch=fetch, env=env)
    return Resolved(model=row["model"], maker=row["maker"], providers=found["providers"],
                    has=HAS, price=dict(row["price"]), price_source=found["price_source"],
                    fetched_at=found["fetched_at"], live_price=found["live_price"])


def harness_kwargs(resolved: Resolved | None) -> dict:
    """What a reviewing Harness is built with: nothing for a CLI reviewer,
    so it is today's harness; the gateway engine, the gateway id and its
    providers and capability for a pool reviewer. Effort and fallback stay
    unset (None): a gateway harness takes neither claude default, so its
    digest is `<id>~gateway` and no Claude name sits on its record."""
    if resolved is None:
        return {}
    return {"engine": "gateway", "model": resolved.model,
            "gateway": {"providers": list(resolved.providers), "has": list(resolved.has)}}
