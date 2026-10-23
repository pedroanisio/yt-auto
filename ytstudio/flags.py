"""The stage flags that mean the same thing everywhere, spelled once.

    --rounds             revision rounds the author gets
    --bar                the review score under which work goes back
    --budget-usd         a spend cap in dollars
    --reviewer-model     the model that reviews, never the author's own
    --review-budget-usd  what one review may cost
    --fresh              ignore what is cached and do the stage again
    --jobs               how many of the stage's independent pieces run at once

A stage adds the ones it has with its own default and help. The spellings
earlier releases used stay as hidden aliases that land in the same place and
print a one-line note, so no operator command or script breaks.
"""

from __future__ import annotations

import argparse
import sys

from . import models


class _Alias(argparse.Action):
    """An old spelling: stores like the flag it was renamed to, and says so."""

    def __init__(self, option_strings, dest, canonical="", **kwargs):
        self.canonical = canonical
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        print(f"note: {option_string} is now {self.canonical}; the old spelling "
              "still works for this release", file=sys.stderr)
        setattr(namespace, self.dest, values)


def _flag(p, canonical, old, dest, **kwargs):
    p.add_argument(canonical, dest=dest, **kwargs)
    for spelling in old:
        p.add_argument(spelling, dest=dest, action=_Alias, canonical=canonical,
                       type=kwargs.get("type"), help=argparse.SUPPRESS)


def rounds(p, *, default, help, old=(), dest="rounds") -> None:
    _flag(p, "--rounds", old, dest, type=int, default=default, help=help)


def bar(p, *, default, help, old=(), dest="bar") -> None:
    _flag(p, "--bar", old, dest, type=float, default=default, help=help)


def budget(p, *, default, help, old=(), dest="budget_usd", metavar=None) -> None:
    _flag(p, "--budget-usd", old, dest, type=float, default=default, help=help,
          metavar=metavar)


def reviewer_model(p, *, default, help, dest="reviewer_model") -> None:
    p.add_argument("--reviewer-model", dest=dest, default=default, metavar="MODEL", help=help)


def review_budget(p, *, default, help, dest="review_budget_usd") -> None:
    p.add_argument("--review-budget-usd", dest=dest, type=float, default=default, help=help)


def model_family(name: str) -> str:
    """`opus`, `claude-opus-5-5` and `opus[1m]` are one model family, and so
    are a gateway id and its direct name (`openai/gpt-5` and `gpt-5`,
    `anthropic/claude-opus-5` and `opus`): the provider is not the model."""
    provider, model = models.canonical(name)
    low = model.lower()
    if provider == models.VIRTUAL_PROVIDER:
        return f"{provider}/{low}"
    return next((f for f in ("fable", "opus", "sonnet", "haiku") if f in low), low)


#: The makers the claude and codex CLIs reach. A pair of their models is
#: judged by family, as it always was.
CLI_MAKERS = ("anthropic", "openai")


def own_reviewer(model: str, reviewer_model: str) -> str:
    """The error when the reviewer is the author's own model (one family),
    else "". A model never grades its own work, so every stage that reviews
    refuses this before anything is paid, and says what to pass. A gateway
    virtual model (`vmc/<slug>`) may route to any model, the author's too,
    so it is refused on either side.

    A model from another maker (AUTO-125) is judged by maker, not family:
    `sdk:<id>` on either side, or a known maker that is neither Anthropic
    nor OpenAI, compares `models.maker`, so google/gemini-3.7-flash refuses
    google/gemini-3.6-flash, which `model_family` alone would let through,
    and an unknown maker on either side is refused, since nobody can say it
    differs. A pair of CLI models is judged by family exactly as before (a
    sonnet author may still be read by opus). `pool` is a choice still to
    be made (`reviewers.resolve`); every caller resolves it first, so this
    only catches a call site that did not."""
    pending = [m for m in (model, reviewer_model)
               if str(m or "").strip().lower() == "pool"]
    if pending:
        return (f"error: --reviewer-model pool must be resolved to one model before it is "
                f"checked against --model {model} (ytstudio/reviewers.py resolve)")
    virtual = [m for m in (model, reviewer_model) if models.is_virtual(m)]
    if virtual:
        return (f"error: {virtual[0]} is a virtual gateway model (--model {model}, "
                f"--reviewer-model {reviewer_model}): it may route to any model, so "
                "whether the reviewer is the author's own cannot be known; pass "
                "--model and --reviewer-model as provider/model ids")
    sides = (model, reviewer_model)
    makers = [models.maker(m) for m in sides]
    if (any(str(m or "").strip().lower().startswith(models.SDK_PREFIX) for m in sides)
            or any(m is not None and m not in CLI_MAKERS for m in makers)):
        if None in makers:
            unknown = sides[makers.index(None)]
            return (f"error: nobody can say who made {unknown} (--model {model}, "
                    f"--reviewer-model {reviewer_model}), so a reviewer from another maker "
                    "cannot be shown to differ from the author; pass provider/model ids")
        if makers[0] != makers[1]:
            return ""
        return (f"error: the reviewer must come from another maker than the author (--model "
                f"{model}, --reviewer-model {reviewer_model}, both {makers[0]}'s): a maker's "
                "models share their training, so one does not grade another's work; pass "
                "--reviewer-model with another maker's model")
    if model_family(reviewer_model) != model_family(model):
        return ""
    return (f"error: the reviewer must be a different model from the author (--model {model}, "
            f"--reviewer-model {reviewer_model}): a model does not grade its own work; pass "
            "--reviewer-model with another model")


def fresh(p, *, help="ignore what is cached and do this stage again") -> None:
    p.add_argument("--fresh", action="store_true", help=help)


def jobs(p, *, default, help, dest="jobs") -> None:
    """How many of the stage's independent pieces run at once; 1 is serial."""
    p.add_argument("--jobs", dest=dest, type=int, default=default, metavar="N",
                   help=f"{help} (default {default}; 1 does them one at a time)")


class _Ignored(argparse.Action):
    """A flag the stage no longer needs: takes its value, changes nothing, says so."""

    def __init__(self, option_strings, dest, stage="", **kwargs):
        self.stage = stage
        super().__init__(option_strings, dest, default=argparse.SUPPRESS,
                         help=argparse.SUPPRESS, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        print(f"note: {self.stage} calls no model; {option_string} is ignored",
              file=sys.stderr)


def ignore_model(p, stage) -> None:
    """For a stage that calls no model: --model, --effort and --fallback-model still
    parse, so `yt run` habits and old scripts that pass them to every stage keep working."""
    for flag in ("--model", "--effort", "--fallback-model"):
        p.add_argument(flag, action=_Ignored, stage=stage)
