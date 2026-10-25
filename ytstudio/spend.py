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
import re
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
    # Haiku 5.5's rate above 100K prompt tokens (5x) is not in the row: a
    # long cut-off call is under-estimated, and a call through the gateway
    # above it is booked at the CLI's figure (claude_engine.TIERED_PROMPT_TOKENS).
    "claude-haiku-5-5": _row(0.10, 0.50),
    "claude-haiku-4-5": _row(1.0, 5.0),
}
# The API serves Haiku 4.5 under its dated snapshot id, which is what the
# stream reports; the engine looks a row up exactly.
PRICES["claude-haiku-4-5-20251001"] = PRICES["claude-haiku-4-5"]

# The gateway reviewer pool's pinned rows (AUTO-125), by gateway id, so the
# gateway engine's budget check and `yt doctor` find a price for each. A row
# is the dearest of its pinned providers; no cache discount is assumed.
from .reviewers import POOL as _POOL  # noqa: E402

for _pooled in _POOL:
    PRICES[_pooled["model"]] = {"input": _pooled["price"]["input"],
                                "cached_input": _pooled["price"]["input"],
                                "output": _pooled["price"]["output"],
                                "cache_creation": _pooled["price"]["input"]}

#: The short names a stage is configured with (`--model sonnet`), priced by
#: `price_for` as the generation the CLI resolves them to. Not rows.
ALIASES = {"fable": "claude-fable-5-1", "opus": "claude-opus-5-5",
           "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}


def price_for(model: Any, env: dict | None = None) -> dict[str, float] | None:
    """The price row the engine would use for `model`: this table, the
    engine's own, then `$CLAUDE_PRICES`; an Anthropic gateway id
    (`anthropic/claude-haiku-4.5`) resolves to its direct id's row when it
    has none of its own, a short name to its generation's row; None when
    nobody priced it (another provider's id, `bedrock/claude-opus-5`, a
    1M-token context, `...[1m]`, and a gateway virtual model, `vmc/<slug>`,
    unless a row names them)."""
    table: dict[str, Any] = {**PRICES, **claude_engine.PRICES}
    raw = (os.environ if env is None else env).get("CLAUDE_PRICES")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                table.update(parsed)
        except (ValueError, TypeError):
            pass
    row = claude_engine._price_row(table, str(model or ""), ALIASES)
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


def merge_records(first: dict, *others: dict) -> dict:
    """Records of several harnesses as one (AUTO-126: QA's reviewer and its
    listener): `cost_usd`, `calls` and `unmetered_calls` summed, `by_model`
    merged model by model with each numeric counter summed (a text field
    keeps the first value seen), and `model` the first record's, whose
    stage it is. Neither input is changed."""
    def number(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    by_model: dict[str, dict] = {}
    for doc in (first, *others):
        for model, row in ((doc or {}).get("by_model") or {}).items():
            have = by_model.setdefault(model, {})
            for key, value in (row or {}).items():
                if number(value) and number(have.get(key, 0)):
                    total = have.get(key, 0) + value
                    have[key] = round(total, 6) if isinstance(total, float) else total
                else:
                    have.setdefault(key, value)
    docs = [d or {} for d in (first, *others)]
    return record(sum(float(d.get("cost_usd") or 0) for d in docs), (first or {}).get("model"),
                  sum(int(d.get("calls") or 0) for d in docs), by_model=by_model,
                  unmetered_calls=sum(int(d.get("unmetered_calls") or 0) for d in docs))


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
#: One scoring sample of a QA pass, assumed until a scorecard measures it. A
#: pass asks qa.QA_SAMPLES of them (AUTO-107); qa imports this module, so
#: the default below is that product written out, and test_qa_samples pins it.
QA_SAMPLE_USD = 0.5
#: The script's default is a run with the fact-check, which `yt run` has
#: always asked and a standalone `yt script` with a dossier now asks too
#: (AUTO-133): the reviewer's `verify` over the whole script and its
#: re-checks. A guess until a real 8-12 minute film measures it (TO VERIFY);
#: a measured script.json already holds its verify decisions.
DEFAULT_USD = {"research": 5.0, "styles": 2.0, "script": 3.0, "props": 0.0,
               "keyframe": 5.0, "voice": 2.0, "retime": 0.5, "vfx": VFX_PASS_USD,
               "sound": 0.5, "build": 0.1, "render": 0.0, "gate": 0.1,
               "qa": 3 * QA_SAMPLE_USD}

#: What the side-by-side comparison adds to a QA pass (AUTO-108), assumed
#: until a scorecard measures it: one inspection reading every sampled
#: still beside the previous kept cut's, about what one scoring sample
#: costs. It is asked once a pass, never per sample.
COMPARE_PASS_USD = 0.5

#: What `yt qa --recheck` adds (AUTO-106), assumed until a scorecard
#: measures it: one inspection reading the stills against the previous
#: pass's findings, about what the side-by-side comparison costs. Asked
#: once a pass, never per sample. (TO VERIFY: a scorecard with a measured
#: `<pass>-recheck` replaces it.)
RECHECK_USD = 0.5

#: What the video comparison adds to a QA pass (`--against`, AUTO-128),
#: assumed until a scorecard measures it: two gateway requests, each
#: carrying both cuts. A guess until a live run prices one (TO VERIFY).
#: Asked once a pass, never per sample.
COMPARE_USD = 0.10

#: The gateway key (gateway.KEY): without it the video comparison is
#: skipped (cutcompare.skip_reason), so the estimate prices none.
GATEWAY_KEY = "AI_GATEWAY_API_KEY"


def _compare_usd(root: Path | None, argv: list[str]) -> float:
    """What `--against` adds: COMPARE_USD, or nothing without the key."""
    return COMPARE_USD if os.environ.get(GATEWAY_KEY) else 0.0

#: What the plan review adds to a script (AUTO-92), assumed until a record
#: measures it: the reviewer's one reading of the plan and, at most, a
#: second plan and its reading.
PLAN_REVIEW_USD = 0.5

#: What build's joins add (AUTO-142), assumed until a build.json measures
#: them: the editor's `joins` decision (and at most a `joins-2`), the
#: reviewer's `joins-review` and at most one `joins-revise`. The review is
#: capped at vfx.REVIEW_USD (2), a cap and not a price: one reading of a
#: short brief listing the joins. A guess until a real build prices it
#: from engine.report()["by_model"] (TO VERIFY).
JOINS_REVIEW_USD = 0.5

#: What `yt research --paper` adds (AUTO-131): the agentic read of the
#: paper (`00-paper`) and the digest decision (`06b-paper`, at most one
#: `06b-paper-again`). A guess until one real paper measures it (TO VERIFY).
PAPER_USD = 1.0

#: What the picture-check adds under the same flag (AUTO-98), assumed until
#: a record measures it: the reviewer's reading of every shot's picture (and
#: at most one `picture-check-again`) plus the author's one `revise-picture`.
PICTURE_CHECK_USD = 0.5

#: The story review's reading of one whole script (AUTO-93), in tokens: what
#: each sample is sent beyond the script (the mission, the role with every
#: rubric level, the house, the brief and the plan), how many tokens a word
#: of narration takes in the listing (the picture, the supers and the label
#: ride beside each line), and what a sample writes back (the synopsis, the
#: grades, the flags). The cold read is sent the narration alone, its
#: answers reading the narration and the plan's questions; the match two
#: sentences. Assumed until records measure them.
REVIEW_OVERHEAD_TOKENS = 8000
REVIEW_TOKENS_PER_WORD = 3.0
REVIEW_OUTPUT_TOKENS = 2500
COLD_READ_TOKENS = (1000, 1.5, 1500)          # overhead, per word, output
ANSWERS_TOKENS = (1200, 1.5, 300)             # overhead, per word, output
MATCH_TOKENS = (1200, 200)                    # input, output
#: The viewer quiz (AUTO-115), in tokens and assumed until records measure
#: it: the author reads the plan and the research (a dossier's worth of
#: input, its size unknown before the script runs) and writes up to eight
#: items, once; the before reads the items alone, once; the after reads the
#: narration and the items as the cold read's answers do, beside each cold
#: read.
QUIZ_TOKENS = {"author": (6000, 1500),                    # input, output
               "before": (900, 150),                      # input, output
               "after": (1500, 1.5, 150)}                 # overhead, per word, output


def _argv_value(argv: Iterable[str], flag: str, default: Any) -> Any:
    argv = list(argv or ())
    if flag in argv and argv.index(flag) + 1 < len(argv):
        return argv[argv.index(flag) + 1]
    return default


def _script_words(root: Path | None) -> int | None:
    """The narration's words in the project's script.json, when it has one."""
    doc = read_json(Path(root) / "script.json", warn=False) if root is not None else None
    if not isinstance(doc, dict):
        return None
    words = sum(len(str(shot.get("narration") or "").split())
                for sec in doc.get("sections") or [] if isinstance(sec, dict)
                for shot in sec.get("shots") or [] if isinstance(shot, dict))
    return words or None


def story_review_usd(root: Path | None, argv: Iterable[str] = ()) -> float:
    """What the story review adds to a script (AUTO-93): its samples
    (script.STORY_SAMPLES a round) times the rounds (`--rounds`, default 2:
    the reading and one re-reading on average), each a reading of the whole
    script, plus the cold read, its answers and its match, once on the
    first draft and, with a second round, once on the draft that stands,
    and the viewer quiz (AUTO-115): its items and its before once, its
    after beside each cold read; priced at the reviewer's model
    (`--reviewer-model`, default opus). The script's words are the
    project's own narration when script.json has it, else what `--minutes`
    at `--wpm` would say (script.words_for, at the script stage's own
    defaults). The writer's revisions are left to the base:
    `revise-story-N`, `redraft-NN` and the question ledger's
    `revise-ledger-2` (AUTO-96) are the author's decisions, a few hundred
    words each against the reviewer's whole-script readings priced here."""
    # script imports this module, so its constants are read here, when the
    # estimate is made, not at import.
    from .script import DEFAULT_MINUTES, DEFAULT_WPM, STORY_SAMPLES, words_for
    try:
        minutes = float(_argv_value(argv, "--minutes", DEFAULT_MINUTES))
        wpm = int(_argv_value(argv, "--wpm", DEFAULT_WPM))
        rounds = max(1, int(_argv_value(argv, "--rounds", 2)))
    except (TypeError, ValueError):
        minutes, wpm, rounds = DEFAULT_MINUTES, DEFAULT_WPM, 2
    words = _script_words(root) or words_for(round(minutes * 60), wpm)
    # A pool reviewer is priced at its pinned row (AUTO-125): `pool` as the
    # row it would choose for the author, with no network.
    from .reviewers import priced_as
    reviewer = priced_as(_argv_value(argv, "--reviewer-model", "opus"),
                         _argv_value(argv, "--model", "sonnet"))
    row = price_for(reviewer) or price_for("opus") or {}
    per_in, per_out = row.get("input", 0.0) / 1e6, row.get("output", 0.0) / 1e6

    def reading(overhead: float, per_word: float, out: float) -> float:
        return (overhead + words * per_word) * per_in + out * per_out
    sample = reading(REVIEW_OVERHEAD_TOKENS, REVIEW_TOKENS_PER_WORD, REVIEW_OUTPUT_TOKENS)
    cold = (reading(*COLD_READ_TOKENS) + reading(*ANSWERS_TOKENS)
            + MATCH_TOKENS[0] * per_in + MATCH_TOKENS[1] * per_out)
    # the viewer quiz (AUTO-115): written and taken before once, taken
    # after beside each cold read
    author, before, after = (QUIZ_TOKENS[k] for k in ("author", "before", "after"))
    quiz = (author[0] + before[0]) * per_in + (author[1] + before[1]) * per_out
    return round(STORY_SAMPLES * rounds * sample
                 + min(rounds, 2) * (cold + reading(*after)) + quiz, 2)


def listen_usd(root: Path | None, argv: Iterable[str] = ()) -> float:
    """What `yt qa --listen` adds (AUTO-126): the listen's preflight
    estimate for the film under `root` at `--listen-model`, its retry
    included; 0 when the model has no price, which the stage refuses."""
    from .listen import estimate_for   # listen reads this module; asked only here
    return estimate_for(root, argv) or 0.0


def watch_usd(root: Path | None, argv: Iterable[str] = ()) -> float:
    """What `yt qa --watch` adds (AUTO-127): the programme's seconds of
    video and an answer a segment at `--watch-model`'s pinned price
    (watch.WATCH_PRICE); it never fetches."""
    from .watch import watch_usd as estimate   # watch reads qa, which reads this module
    return estimate(root, argv)


#: A flag that adds to what its stage costs: stage -> flag -> its parts, each
#: (the increment, the decision whose row in the stage's record shows that
#: part was on). An increment is dollars, or a function of the project and
#: the stage's argv when it scales with the work. The stage's DEFAULT_USD is
#: the base. A record holding a part's decision (or a numbered one,
#: `plan-review-2`, `review-r1-s1`) already counts it; one without it, or no
#: record at all, gains the increment when the flag is asked. A part whose
#: decision is None is one the base never counts: it is added every time. `--review` is
#: the plan review (AUTO-92), the story review (AUTO-93) and the
#: picture-check (AUTO-98), so a record written before one of them gains
#: only what it lacks.
FLAG_USD = {"research": {"--paper": ((PAPER_USD, "06b-paper"),)},
            "script": {"--review": ((PLAN_REVIEW_USD, "plan-review"),
                                    (story_review_usd, "review-r1"),
                                    (PICTURE_CHECK_USD, "picture-check"))},
            # QA compares the new cut side by side with the pass `--previous`
            # names, or with the latest scored one when there is one, so
            # stage_estimates asks the flag of a plain `yt qa` too (AUTO-108).
            # `--listen` is the listening pass (AUTO-126), priced as its
            # preflight prices it. Its decisions are on the scorecard's
            # `listen.engine`, never among the reviewer's, so no record holds
            # it: qa.pass_usd leaves a past listen out of the base, and the
            # flag adds the next one's estimate each time it is asked.
            # `--watch` (AUTO-127) is priced the same way: its decisions are
            # on the scorecard's `watch_engine`, which pass_usd leaves out
            # and this record never reads.
            "qa": {"--previous": ((COMPARE_PASS_USD, "compare-pass"),),
                   # the video comparison's two orders, `cutcompare-<pass>-ab`
                   # and `-ba`, on the scorecard's compare_engine (AUTO-128)
                   "--against": ((_compare_usd, "cutcompare"),),
                   "--listen": ((listen_usd, "listen"),),
                   "--watch": ((watch_usd, "watch"),),
                   # the previous findings rechecked after the blind
                   # scoring (AUTO-106). Its `<pass>-recheck` is on the
                   # reviewer's own record, but --recheck is opt-in like
                   # --listen and --watch: qa.pass_usd leaves a past recheck
                   # out of the base, so this part names no decision and is
                   # added each time the flag is asked.
                   "--recheck": ((RECHECK_USD, None),)}}


#: A part a stage pays unless a flag turns it off: stage -> (that flag, the
#: name the estimate gives the part, its parts as in FLAG_USD). yt build
#: asks for its joins unless it is handed --hard-cuts, as `yt run --quick`
#: does (AUTO-142).
UNLESS_USD = {"build": ("--hard-cuts", "joins review", ((JOINS_REVIEW_USD, "joins"),))}


def qa_compares(root: Path | None) -> bool:
    """Whether a `yt qa` under `root` pays for the side-by-side comparison
    without being told --previous: a pass it can compare with, one scored
    (not `not_evidenced`), is already under qa/ (AUTO-108)."""
    if root is None:
        return False
    from . import qa  # qa reads this module; it is only asked where its passes live
    for path in reversed(qa._passes(Path(root))):
        card = read_json(path / "scorecard.json", warn=False)
        if isinstance(card, dict) and card.get("verdict") != qa.NOT_EVIDENCED:
            return True
    return False


def flag_increments(root: Path | None, stage: str, argv: Iterable[str]) -> list[tuple[str, float]]:
    """The flags asked of `stage` (in its `argv`) whose cost the record
    under `root` does not hold, each with what its missing parts add; with
    no `root`, every part of every flag asked."""
    argv = list(argv or ())
    names: set[str] = set()
    if root is not None:
        rel = MANIFEST.get(stage)
        # QA has no manifest of its own: its record is the latest scorecard,
        # whose decisions are named by their pass (`pass-03-compare-pass`).
        doc = (_qa_card(root) if stage == "qa" else
               read_json(Path(root) / rel, warn=False) if rel else None)
        rows: list = []
        for key in ("engine", "compare_engine"):     # compare_engine: AUTO-128
            engine = doc.get(key) if isinstance(doc, dict) else None
            rows += (engine.get("decisions") if isinstance(engine, dict) else None) or []
        names = {re.sub(r"^pass-\d+-", "", str(d.get("name")))
                 for d in rows if isinstance(d, dict)}
        # A video comparison that runs stands in for the still pairs, which
        # are asked only when it fails: its record measures --previous too.
        if (stage == "qa" and "--against" in argv and os.environ.get(GATEWAY_KEY)
                and any(n.startswith("cutcompare-") for n in names)):
            names.add("compare-pass")
    def missing(parts) -> float:
        return sum((cost(root, argv) if callable(cost) else cost)
                   for cost, decision in parts
                   if decision is None
                   or not any(n == decision or n.startswith(decision + "-") for n in names))
    out = []
    for flag in dict.fromkeys(argv):
        parts = FLAG_USD.get(stage, {}).get(flag)
        if not parts:
            continue
        usd = missing(parts)
        if usd:
            out.append((flag, round(usd, 2)))
    if stage in UNLESS_USD:
        off, label, parts = UNLESS_USD[stage]
        usd = 0.0 if off in argv else missing(parts)
        if usd:
            out.append((label, round(usd, 2)))
    return out


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


def _qa_card(root: Path) -> dict:
    from . import qa  # qa reads this module; it is only asked where its passes live
    qa_json = read_json(Path(root) / "qa.json", warn=False) or {}
    return read_json(Path(root) / qa.QA_DIR / str(qa_json.get("pass", "")) / "scorecard.json",
                     warn=False) or qa_json


def _qa_cost(root: Path) -> float | None:
    """What the latest QA pass cost."""
    return cost_in(_qa_card(root))


def _qa_pass_usd(root: Path) -> float | None:
    """What the next QA pass costs, priced from the latest: its cost per
    scoring sample, times qa.QA_SAMPLES (AUTO-107), so a pass scored once
    prices the samples it did not ask."""
    from . import qa
    return qa.pass_usd(_qa_card(root))


def _reported(root: Path, stage: str, sections: list[int] | None) -> float | None:
    """What one project's own report says a run of the stage costs, or None.
    A zero on a stage that is never free is a cache replay, not a measurement."""
    root = Path(root)
    if stage == "qa":
        cost = _qa_pass_usd(root)
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
                added = flag_increments(other, stage, flags)
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
        if stage == "qa" and "--previous" not in flags and qa_compares(root):
            flags = [*flags, "--previous"]      # `yt run`'s plain qa compares all the same
        have = _reported(root, stage, wanted)
        like = _like(root, stage, flags) if have is None and not wanted else None
        if have is not None:
            usd, source = _with_flags(
                have, (MANIFEST.get(stage) or "qa.json")
                + (f" sections {','.join(map(str, wanted))}" if wanted else ""),
                flag_increments(root, stage, flags))
            row = {"stage": stage, "usd": usd, "basis": "measured", "source": source}
        elif like is not None:
            row = {"stage": stage, "usd": like[0], "basis": "like", "source": like[1]}
        elif folded:
            row = {"stage": stage, "usd": 0.0, "basis": "assumed",
                   "source": "included in the vfx default"}
        else:
            usd, source = _with_flags(
                VFX_PASS_USD if stage == "vfx" else DEFAULT_USD.get(stage, 0.5),
                "default, nothing reported", flag_increments(None, stage, flags))
            row = {"stage": stage, "usd": usd, "basis": "assumed", "source": source}
        folded = folded or (stage == "vfx" and row["basis"] == "assumed")
        if stage == "qa" and (Path(root) / "dossier.json").is_file() \
                and not isinstance(_qa_card(root).get("accuracy_engine"), dict):
            # AUTO-150: a dossier film's QA asks the on-screen accuracy check
            # too; a pass that measured it holds it, any other is assumed.
            from . import qa
            row = dict(row, usd=round(row["usd"] + qa.ACCURACY_USD, 2),
                       source=f"{row['source']} + on-screen accuracy "
                              f"${qa.ACCURACY_USD:.2f} (assumed)")
        rows.append(row)
    return rows


def total(rows: Iterable[dict]) -> float:
    return round(sum(r["usd"] for r in rows), 2)
