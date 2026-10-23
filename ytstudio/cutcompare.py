"""The previous kept cut against the new one, watched whole (AUTO-128).

QA reads stills, bursts and listening reviews; it never watches the film.
A model that takes video through the gateway can watch two cuts, picture
and sound, in one request. `yt qa --against FILE --compare-model MODEL`
asks it here, on a gateway harness of its own, and files what it says as
AUTO-108's tally entries with `source: "video"`.

How it is asked:
  - Twice, once in each order: the previous cut as Cut A and the new one
    as Cut B (`ab`), then the other way round (`ba`). A model's pick leans
    towards a position (Zheng et al. 2023, "Judging LLM-as-a-Judge", §3.3),
    and swapping the two and keeping only the verdicts that agree is that
    paper's remedy (§3.4). Each order is one decision,
    `cutcompare-<pass>-<ab|ba>`, cached like any other: the digest is the
    two files' bytes and labels, so the same two cuts are not paid for
    twice.
  - Blind. The files are linked into a temporary directory as
    `cut-a.mp4` and `cut-b.mp4`, each labelled by a text part ("Cut A:")
    immediately before it; nothing in the request names a pass, a score,
    a finding, which cut came first or which one `yt improve` kept. The
    harness takes no revision notes (feedback=False).
  - Per category of docs/production.md §2, all ten: the video carries the
    sound, so the audio categories, which no still bears on, are judged
    here too. Each category is A, B, same, or cannot_tell when the video
    does not let the model judge it.

How it is filed:
  - A category both orders call for the same cut is better (the new one)
    or worse (the previous one); same in both is same; two answers that
    name different cuts are a tie, filed as same (the position decided
    it, not the cut); cannot_tell in either order files nothing.
  - Each category is its own moment in the tally (`moment` is the
    category), so `total` counts one vote a category, and `t` is the
    earliest timestamp either order gave for it.
  - Nothing else about the answer is trusted: one that breaks the schema
    (a winner outside its values, an unknown, repeated or missing
    category, a timestamp that is not a number) fails the comparison and
    is forgotten, so the next run asks again rather than serving it; the
    harness has already asked once more with the problem named. A failed
    comparison files no entries, and QA's still pairs run instead.

TO VERIFY with the gateway (no key or network here): that the model
accepts two video files and the text parts between them in one request,
how long two full cuts take to answer (COMPARE_TIMEOUT), and what a
comparison costs (spend.COMPARE_USD, the estimate's prior).
"""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from . import flags, models, qa, spend
from . import gateway as _gateway
from .core import read_json
from .harness import Harness, HarnessError

#: Names the compare model when --compare-model is not given. Read only
#: when a comparison is asked for (--against); alone it turns nothing on.
MODEL_ENV = "YT_COMPARE_MODEL"
#: Two full cuts take longer to send and watch than a still pair; the
#: gateway program's own timeout and the process's follow it.
COMPARE_TIMEOUT = 1200.0
#: Both cuts together, in MB. Larger is skipped before anything is sent
#: (the engine's own cap, max_media_mb, is 20 and would refuse it later).
COMPARE_MAX_MB = 15
#: What an order's overall answer may be, and a category's.
WINNERS = ("A", "B", "same")
CRITERION_WINNERS = WINNERS + ("cannot_tell",)
#: The text part sent immediately before each file, and its name there.
LABELS = ("Cut A:", "Cut B:")
FILES = ("cut-a.mp4", "cut-b.mp4")
#: Which cut is A and which B, per order.
ORDERS = {"ab": ("previous", "new"), "ba": ("new", "previous")}
#: What a comparison record may say it is.
STATUSES = ("done", "failed", "skipped")
#: Where `yt qa` writes the record, under the pass.
RECORD = "comparison.json"

COMPARE_ROLE = (
    "You compare two cuts of one film, watching and listening to each in full, "
    "and say which is better by professional standards: work that would not look "
    "out of place on a channel people subscribe to. You are told nothing else about "
    "either cut, and you assume nothing about how either was made."
)

#: What each category asks of the two cuts, in docs/production.md §2's order.
CRITERIA = {
    "visual_quality": "the pictures themselves: legibility, sharpness, colour, "
                      "composition, artefacts and placeholders",
    "animation_vfx": "motion and effects: whether things move with purpose, and "
                     "transitions that are more than a dissolve",
    "cinematography": "framing, camera moves and the use of the stage",
    "editing_pacing": "where the cuts fall, the rhythm, and how long each moment holds",
    "graphics_overlays": "titles, supers, captions and data graphics, legible and in "
                         "their bands (supers top, stage middle, captions bottom)",
    "voiceover": "the narration's delivery, clarity, level and sync with the pictures",
    "sound_design": "effects and ambience: whether what is seen is heard, at the "
                    "right moment and level",
    "music_score": "the music's fit, level under the voice, and its transitions",
    "narrative_clarity": "whether the story reads: the hook, the stakes, the questions "
                         "it opens closed, and the payoff on its promise",
    "overall_production": "the film as a finished piece",
}

PROMPT = "\n\n".join([
    "Two cuts of one film follow, each a complete video with its sound: Cut A, then "
    "Cut B. Watch and listen to both all the way through, and judge them against each "
    "other.",
    "For each category below, say which cut is better (A or B), same when neither is, "
    "or cannot_tell when the videos do not let you judge it; give the reason in what "
    "you saw or heard, and the timestamps, in seconds into the film, that show it. "
    "Then say which cut is better as a whole (A, B or same), with your reasons.",
    "Which cut is A says nothing about either: judge only what is in the videos. "
    "Answer every category exactly once, by its name.",
    "Categories:\n" + "\n".join(f"- {k}: {v}" for k, v in CRITERIA.items()),
])

COMPARE_SCHEMA = {
    "type": "object",
    "properties": {
        "winner": {"type": "string", "enum": list(WINNERS),
                   "description": "the better cut as a whole, or same"},
        "reasons": {"type": "array", "items": {"type": "string"},
                    "description": "why, in what was seen and heard"},
        "criteria": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": list(qa.CATEGORIES)},
                "winner": {"type": "string", "enum": list(CRITERION_WINNERS)},
                "reason": {"type": "string"},
                "timestamps": {"type": "array", "items": {"type": "number"},
                               "description": "seconds into the film"}},
            "required": ["category", "winner", "reason", "timestamps"]}}},
    "required": ["winner", "reasons", "criteria"],
}


#: The stages whose model made what the cuts show: the compare model must
#: be of none of their families, since a model does not grade its own work.
AUTHORS = ("script", "styles", "keyframe", "vfx", "sound", "build")


def author_models(reports: dict[str, Any]) -> list[tuple[str, str]]:
    """(file, model) for each author report given, {stage: path or None}:
    the model its `usage` and its `engine` name, or (file, "") for a report
    that names none."""
    out = []
    for stage in AUTHORS:
        path = reports.get(stage)
        doc = read_json(Path(path), warn=False) if path else None
        if not isinstance(doc, dict):
            continue
        named = [str(row["model"]) for row in (doc.get("usage"), doc.get("engine"))
                 if isinstance(row, dict) and row.get("model")]
        for model in named or [""]:
            if (Path(path).name, model) not in out:
                out.append((Path(path).name, model))
    return out


#: The providers of the default CLIs (claude, codex): a report that names
#: no model was most likely made by one of them.
CLI_PROVIDERS = ("anthropic", "openai")


def refusal(against: Any, flag_model: Any, model: Any,
            authors: list[tuple[str, str]] = ()) -> str:
    """The error that stops `yt qa` before anything renders, or "": one
    option without the other (`flag_model` is --compare-model as given;
    $YT_COMPARE_MODEL alone asks for nothing), a model the gateway engine
    cannot ask, or one of an author's family (`authors`, from
    `author_models`, or improve's --model)."""
    import gateway_engine
    if flag_model and not against:
        return ("error: --compare-model needs --against FILE, the previous cut to compare "
                "the new one with")
    if not against:
        return ""
    if not model:
        return (f"error: --against needs --compare-model MODEL (or ${MODEL_ENV}): the "
                "gateway video model that watches both cuts")
    problem = gateway_engine.model_problem(model)
    if problem:
        return f"error: --compare-model {model}: {problem}"
    if authors and not any(author for _, author in authors) and (
            models.canonical(model)[0] in CLI_PROVIDERS):
        # Nothing says who made them, and the default CLIs' models are
        # these providers': whether it is the author's family is unknown.
        # A family across one provider's models is AUTO-125's maker().
        return (f"error: {', '.join(dict.fromkeys(f for f, _ in authors))} names no model, "
                f"so whether --compare-model {model} is of the author's family cannot be "
                "known; pass --compare-model with a model of another provider")
    for file, author in authors:
        if not author:
            continue
        if models.is_virtual(author):
            return (f"error: {file} was made with {author}, a virtual gateway model that "
                    "may route to any model, so whether --compare-model "
                    f"{model} is the author's own family cannot be known")
        if flags.model_family(author) == flags.model_family(model):
            return (f"error: --compare-model {model} is of the family of {author}, which "
                    f"made {file}: a model does not grade its own work; pass "
                    "--compare-model with another family's model")
    return ""


def skip_reason(previous: Path, new: Path, scene: Path) -> str:
    """Why the two cuts are not compared, or "": a cut missing, the new
    one older than the document this pass scored (it is not that cut),
    the two over COMPARE_MAX_MB together, or no gateway key. Each is a
    comparison skipped, never a CLI call in its place."""
    if not previous.is_file():
        return f"the previous cut {previous} does not exist"
    if not new.is_file():
        return f"no rendered cut to compare: {new} does not exist; run `yt render` first"
    try:
        stale = new.stat().st_mtime < scene.stat().st_mtime
    except OSError:
        stale = False
    if stale:
        return (f"{new} is older than {scene.name}: it is not the cut this pass scored; "
                "run `yt render` first")
    sizes = previous.stat().st_size, new.stat().st_size
    if sum(sizes) > COMPARE_MAX_MB * 1024 * 1024:
        return (f"the two cuts come to {sizes[0] + sizes[1]} bytes ({sizes[0]} the previous, "
                f"{sizes[1]} the new), over {COMPARE_MAX_MB:g} MB together; render them "
                "smaller to compare them")
    if not os.environ.get(_gateway.KEY):
        return (f"no {_gateway.KEY}: the video comparison runs only through the gateway, "
                "so it is skipped and the still pairs stand in")
    return ""


def merged_usage(*reports: dict) -> dict:
    """One `usage` for the scorecard from several `Harness.report()`s (the
    reviewer's, the comparison's): costs and calls summed, by_model merged,
    the first report's model named."""
    by_model: dict[str, dict] = {}
    for report in reports:
        for name, row in (report.get("by_model") or {}).items():
            have = by_model.setdefault(name, {})
            for key, value in (row or {}).items():
                number = isinstance(value, (int, float)) and not isinstance(value, bool)
                have[key] = (round(have.get(key, 0) + value, 6) if number else value)
    return spend.record(sum(float(r.get("cost_usd") or 0) for r in reports),
                        reports[0].get("model") if reports else "",
                        sum(int(r.get("calls") or 0) for r in reports),
                        by_model=by_model,
                        unmetered_calls=sum(int(r.get("unmetered_calls") or 0)
                                            for r in reports))


def decision_name(pass_name: str, order: str) -> str:
    """An order's decision, `cutcompare-<pass>-<ab|ba>`."""
    return f"cutcompare-{pass_name}-{order}"


def _number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def problems(answer: Any) -> list[str]:
    """What is wrong with one order's answer, past what the harness checks
    (it lets a type or a value outside an enum through, for the stage to
    decide): here any of them makes the answer unusable."""
    if not isinstance(answer, dict):
        return ["the answer is not an object"]
    out = []
    if answer.get("winner") not in WINNERS:
        out.append(f"winner is {answer.get('winner')!r}, not one of {', '.join(WINNERS)}")
    reasons = answer.get("reasons")
    if not isinstance(reasons, list) or not all(isinstance(r, str) for r in reasons):
        out.append("reasons is not a list of strings")
    criteria = answer.get("criteria")
    if not isinstance(criteria, list):
        return out + ["criteria is not a list"]
    seen = []
    for k, c in enumerate(criteria):
        if not isinstance(c, dict):
            out.append(f"criteria[{k}] is not an object")
            continue
        cat = c.get("category")
        if cat not in qa.CATEGORIES:
            out.append(f"criteria[{k}] names an unknown category {cat!r}")
        elif cat in seen:
            out.append(f"{cat} is answered more than once")
        else:
            seen.append(cat)
        if c.get("winner") not in CRITERION_WINNERS:
            out.append(f"{cat}: winner is {c.get('winner')!r}, not one of "
                       f"{', '.join(CRITERION_WINNERS)}")
        if not isinstance(c.get("reason"), str):
            out.append(f"{cat}: reason is not a string")
        stamps = c.get("timestamps")
        if not isinstance(stamps, list) or not all(_number(t) for t in stamps):
            out.append(f"{cat}: timestamps are not a list of numbers")
    missing = [c for c in qa.CATEGORIES if c not in seen]
    if missing:
        out.append(f"no answer for {', '.join(missing)}")
    return out


def _cut(order: str, said: str) -> str | None:
    """The cut an order's A or B was, "previous" or "new"; None for same."""
    if said not in ("A", "B"):
        return None
    return ORDERS[order][0 if said == "A" else 1]


def _result(ab: str, ba: str) -> tuple[str | None, bool]:
    """Two orders' answers as one result and whether they agreed: better or
    worse when both name the same cut, same when both say same, a tie
    (same, not agreed) when they differ, None when either cannot tell."""
    if "cannot_tell" in (ab, ba):
        return None, False
    first, second = _cut("ab", ab), _cut("ba", ba)
    if first == second:
        return ({"new": "better", "previous": "worse", None: "same"}[first], True)
    return "same", False


def reconcile(ab: dict, ba: dict) -> tuple[list[dict], list[dict], str]:
    """Both orders' answers, already checked by `problems`, as one row per
    category, the tally entries, and the overall result (better, worse or
    same). See the module's docstring for the rules."""
    by_ab = {c["category"]: c for c in ab["criteria"]}
    by_ba = {c["category"]: c for c in ba["criteria"]}
    rows, entries = [], []
    for cat in qa.CATEGORIES:
        a, b = by_ab[cat], by_ba[cat]
        result, agreed = _result(a["winner"], b["winner"])
        stamps = sorted({float(t) for t in [*a["timestamps"], *b["timestamps"]]})
        rows.append({"category": cat, "result": result, "agreed": agreed,
                     "ab": {"said": a["winner"], "cut": _cut("ab", a["winner"])},
                     "ba": {"said": b["winner"], "cut": _cut("ba", b["winner"])},
                     "reasons": {"ab": a["reason"], "ba": b["reason"]},
                     "timestamps": stamps})
        if result is not None:
            entries.append({"category": cat, "result": result, "source": "video",
                            "moment": cat, "t": stamps[0] if stamps else None,
                            "timestamps": stamps})
    overall, _ = _result(ab["winner"], ba["winner"])
    return rows, entries, overall or "same"


def record(status: str, reason: str = "", *, model: str | None = None,
           previous: Path | None = None, new: Path | None = None, **more) -> dict:
    """A comparison record, qa/<pass>/comparison.json: whether it was asked
    and answered (`done`), asked and not usable (`failed`) or not asked
    (`skipped`), why, with which model, the two cuts, each order's
    decision and answer, the reconciled rows, entries and tally. Only a
    `done` record carries entries."""
    cuts = {}
    for key, path in (("previous", previous), ("new", new)):
        if path is not None:
            cuts[key] = {"path": str(path)}
            if Path(path).is_file():
                cuts[key].update(sha256=_sha(Path(path)), bytes=Path(path).stat().st_size)
    out = {"status": status, "reason": reason, "model": model, "cuts": cuts,
           "orders": {}, "overall": None, "criteria": [], "entries": [],
           "tally": qa.tally([]), "cost_usd": 0.0}
    out.update(more)
    return out


def _sha(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _place(source: Path, dest: Path) -> None:
    """`source` at `dest`: a hard link, or a copy across file systems."""
    try:
        os.link(source, dest)
    except OSError:
        shutil.copyfile(source, dest)


def compare_cuts(harness: Harness, previous: Path, new: Path, *, pass_name: str,
                 workdir: Path | None = None) -> dict:
    """Ask `harness` (a gateway harness) to compare `previous` with `new` in
    both orders, and reconcile the answers (see the module's docstring).
    The cuts are linked, as cut-a.mp4 and cut-b.mp4 per order, into a
    temporary directory under `workdir` (default: beside `new`) that is
    gone afterwards. Returns the record; never raises for a model that did
    not answer or answered badly."""
    previous, new = Path(previous), Path(new)
    base = dict(model=harness.model, previous=previous, new=new)
    orders: dict[str, dict] = {}

    def cost() -> float:
        """What this run paid for the two orders: a cached one cost nothing
        now, and a failed one is billed (its `failed` row)."""
        return round(sum(d.cost_usd for d in harness.decisions if not d.cached
                         and d.name.startswith(decision_name(pass_name, ""))), 4)

    with tempfile.TemporaryDirectory(prefix=".cuts-", dir=workdir or new.parent) as tmp:
        for order, (first, second) in ORDERS.items():
            folder = Path(tmp) / order
            folder.mkdir()
            media = []
            for label, name, cut in zip(LABELS, FILES, (first, second)):
                _place(previous if cut == "previous" else new, folder / name)
                media.append({"path": folder / name, "label": label})
            name = decision_name(pass_name, order)
            orders[order] = {"decision": name, "A": first, "B": second}
            try:
                answer = harness.decide(name, PROMPT, COMPARE_SCHEMA, media=media)
            except HarnessError as exc:
                return record("failed", str(exc), orders=orders, cost_usd=cost(), **base)
            orders[order]["answer"] = answer
            wrong = problems(answer)
            if wrong:
                # kept, it would be served again on every rerun
                harness.forget(name)
                return record("failed", f"{name}: " + "; ".join(wrong[:5]), orders=orders,
                              cost_usd=cost(), **base)
    rows, entries, overall = reconcile(orders["ab"]["answer"], orders["ba"]["answer"])
    return record("done", "", orders=orders, overall=overall, criteria=rows,
                  entries=entries, tally=qa.tally(entries), cost_usd=cost(), **base)
