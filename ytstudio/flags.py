"""The stage flags that mean the same thing everywhere, spelled once.

    --rounds      revision rounds the author gets
    --bar         the review score under which work goes back
    --budget-usd  a spend cap in dollars
    --fresh       ignore what is cached and do the stage again

A stage adds the ones it has with its own default and help. The spellings
earlier releases used stay as hidden aliases that land in the same place and
print a one-line note, so no operator command or script breaks.
"""

from __future__ import annotations

import argparse
import sys


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


def fresh(p, *, help="ignore what is cached and do this stage again") -> None:
    p.add_argument("--fresh", action="store_true", help=help)


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
