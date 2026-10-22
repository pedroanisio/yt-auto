"""The narrative rubric: what a story is judged on, read by the writer and the reviewer.

No rubric existed: the script stage wrote against house prose and nothing
graded the story before voice was paid for. This module is that rubric, in
the shape docs/narrative/judging-narrative.md sets for a model judge:

- seven criteria (`RUBRIC`), each a question with four labelled levels of
  one line, a worked example, the strength of its lead rule from the
  corpus README's table and the corpus rules it is drawn from. Never a
  number: an unbounded or 1-100 score calibrates badly, four labels are read
  at about one level either way (judging 8);
- the review's schema in the shape of script coverage (judging 12): a
  logline and a synopsis written before grading, a grade per criterion, a
  verdict, and every problem tied to a section and a shot;
- the plan review's schema, for the plan before any line is written;
- the level order permuted per sample (`grade_levels`), because the harness
  hands the model an enum in schema order and a judge favours a position.

The verdicts are `reject`, `consider` and `recommend`. `reject` is what
coverage calls a Pass; the word Pass never names a verdict here, because
doctor, improve and the operator read verdict strings, and "pass" there
means the opposite.

Four criteria decide a film: train, stakes, open loops and ending; hook,
reveal setup and honesty are the other three a reader can judge and a lint
cannot. What a lint can measure (hook timing, sentence length, concrete
before abstract by position) belongs to the deterministic checks, not here.

Like `framecheck.CHECKLIST`, one tuple is meant to be rendered twice: into
the writer's prompt (`rubric_text("writer")`, the top level of each
criterion) and into the reviewer's role (`reviewer_role()`, every level with
its strength), so a round is never spent finding out what will be looked at.
`yt qa`'s role renders the writer's lines for the rows a finished film can
be judged on (`qa.STORY_RUBRIC`), and `yt improve`'s router reads them to
name the key a story finding bears on (AUTO-99).
`yt script --review` wires the plan's role (`reviewer_role(plan=True)`),
PLAN_REVIEW_SCHEMA and `check_plan_review` into the plan review (AUTO-92),
and `picture_role()` and PICTURE_SCHEMA into the picture-check that reads
the drafted screenplay's pictures with the narration taken away (AUTO-98),
and the script's role (`reviewer_role(seed=k)`), `review_schema` and
`check_review` into the story review's three samples (AUTO-93). Nothing
here decides a verdict: it is schema and prompt text, `check_review` and
`check_plan_review` only name what the caller asks the model again about
(invariant 3), and `median` only reads the samples' own labels.

A known gap: a criterion's strength is the README's word for its lead rule,
and today none comes out `house`: six lead with a practitioner rule and the
hook with structure 10 (platform), although the hook's window is the
house's choice. So the role's sentence that a Poor on a house-strength
criterion yields consider governs nothing yet; it stays because the
calibration set (judging 10, AUTO-97) may show a criterion should be graded
as a house prior.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from itertools import permutations
from typing import Any, Iterable, Mapping

#: The four labels, best first (coverage's Excellent, Good, Fair, Poor).
LEVELS = ("Excellent", "Good", "Fair", "Poor")

#: The three verdicts, worst first. `reject` is coverage's Pass.
VERDICTS = ("reject", "consider", "recommend")

#: A revision against the round before, worst first (`previous.verdict`).
PREVIOUS = ("worse", "same", "better")

#: How much a flag stands in the way of voicing.
SEVERITIES = ("blocker", "important", "minor")

#: The plan review's verdicts, worst last: the plan is accepted, sent back
#: with the problems to fix, or rejected as not a story.
PLAN_VERDICTS = ("accept", "revise", "reject")

#: Who reads the rubric: the writer is told what Excellent looks like, the
#: reviewer reads every level.
AUDIENCES = ("writer", "reviewer")

#: The hook's window in words. How soon the subject must be confirmed is the
#: house's choice (structure 10 and 13 stop short of a number), so the
#: rubric never writes seconds; a caller that has the house's window in
#: words passes it as `window`.
OPENING_WINDOW = "the opening window the house standards set"

#: Said to the writer and the reviewer alike (judging 9): a judge prefers the
#: longer of two stories unless told otherwise.
LONGER = ("Longer is not better: a line, a beat or a section that adds length "
          "without moving the story counts against it, never for it.")

#: Said to the reviewer: a house rule is a prior to tune, never a gate on its
#: own (docs/narrative/README, strength of each rule).
HOUSE_POOR = "a Poor on a house-strength criterion yields consider, never reject"


@dataclass(frozen=True)
class Criterion:
    """One thing the story is judged on.

    `levels` maps each of LEVELS to one line saying what that grade looks
    like; `example` is one worked case; `strength` is the corpus README's word
    for `rules[0]`, the rule the criterion is chiefly drawn from; `rules` are
    corpus ids in its own spelling ("structure 22")."""
    key: str
    question: str
    levels: Mapping[str, str]
    example: str
    strength: str
    rules: tuple[str, ...] = field(default=())


RUBRIC: tuple[Criterion, ...] = (
    Criterion(
        "train",
        "Is the plan one sentence with a pursuer, a question and a named obstacle, "
        "not a topic, and does every section advance it?",
        {"Excellent": "One sentence names who or what pursues what, the obstacle and the "
                      "question; every section moves it, and swapping two would break it.",
         "Good": "The sentence has a question and an obstacle; one section only loosely "
                 "moves it.",
         "Fair": "The sentence has a question but no obstacle, or several sections "
                 "could be reordered without loss.",
         "Poor": "The sentence names a topic, or the sections are a list that survives "
                 "any order."},
        "Poor: \"This film is about comet tails.\" Excellent: \"In 1951 Biermann had to "
        "explain why comet tails outrun the push of sunlight: what else is blowing?\"",
        "practitioner",
        ("judging 13", "structure 3", "structure 1", "structure 2")),
    Criterion(
        "hook",
        "Does the opening confirm the subject the title promised inside {window}, and "
        "open a question the title does not already answer, without listing the "
        "conclusions?",
        {"Excellent": "The subject is on screen or in words inside {window}; the question "
                      "it opens is specific, and no answer is given away.",
         "Good": "The subject is confirmed inside {window}; the question is a fair one but "
                 "close to what the title says.",
         "Fair": "The subject arrives after {window}, or the question is a generic tease.",
         "Poor": "The opening names a different subject, only restates the title and "
                 "opens no question beyond it, or enumerates the conclusions the film "
                 "will reach."},
        "Poor: \"In this video: tails point away because of light pressure and the solar "
        "wind.\" Excellent: \"This comet is leaving the Sun, and its tail is in front.\"",
        "platform",
        ("structure 10", "structure 13", "structure 14", "structure 6", "attention 4",
         "structure 39")),
    Criterion(
        "stakes",
        "Before the first mechanism, does the viewer hear a concrete consequence, "
        "addressed to them or people like them, unless the plan declares the danger is "
        "withheld as the film's twist?",
        {"Excellent": "A concrete, sourced consequence the viewer can picture comes before "
                      "the first mechanism and is addressed to them.",
         "Good": "The consequence is concrete but reaches the viewer only by implication.",
         "Fair": "The consequence comes after the mechanism with no twist declared to "
                 "withhold it, or is abstract (\"this matters\").",
         "Poor": "Nothing is at risk for anyone, or the film explains before it gives a "
                 "reason to care and no twist is declared to excuse it."},
        "Fair: \"Understanding comets matters for science.\" Excellent: \"The wind that "
        "bends this tail reaches you too: in 1989 a storm in it put Quebec's grid out "
        "for nine hours.\"",
        "practitioner",
        ("attention 8", "attention 10", "structure 12", "attention 9", "attention 16")),
    Criterion(
        "open_loops",
        "Is every question the film opens closed, or left open on purpose, every setup "
        "paid, and is the wait between a question and its answer short of a burden?",
        {"Excellent": "Every question opened is answered or deliberately left; every setup "
                      "pays; no more than a couple of secondary facts stand between a "
                      "question and its answer.",
         "Good": "Every question closes; one answer is kept waiting behind a block of "
                 "side facts.",
         "Fair": "A question opened by a section is never answered, or a setup never pays.",
         "Poor": "Several questions are dropped, or the answers arrive before the "
                 "questions are asked."},
        "Fair: section 2 asks why the tail is two tails and the film never says. "
        "Excellent: the same question closes in section 3 with the dust tail curving "
        "behind.",
        "practitioner",
        ("structure 8", "structure 14", "structure 19", "attention 5", "attention 1")),
    Criterion(
        "reveal_setup",
        "Does a constraint, a failed attempt or the scale come before the main reveal, "
        "and does each section leave the viewer with something?",
        {"Excellent": "The viewer knows why the reveal is hard before it lands, and every "
                      "section ends with a fact, reversal or image they lacked.",
         "Good": "The constraint precedes the reveal; one section's own payoff is slight.",
         "Fair": "The constraint is stated in the same breath as the reveal, so the "
                 "answer arrives unearned, or a section only sets up the next.",
         "Poor": "The answer is given before the viewer knows it was hard, or no section "
                 "pays anything of its own."},
        "Poor: section 1 says the solar wind pushes the tail; section 2 then explains "
        "that sunlight alone is too weak. Excellent: the weakness comes first.",
        "practitioner",
        ("structure 22", "structure 20", "attention 2")),
    Criterion(
        "ending",
        "Does the last section pay the promise in resolved form, call back the opening, "
        "skip the recap and end on a short line, bringing a large subject back to the "
        "viewer's scale?",
        {"Excellent": "The promise is paid in resolved form, the opening returns changed, "
                      "a large subject comes back to the viewer's scale, there is no "
                      "recap, and the last line is short.",
         "Good": "The promise is paid and the line is short; the callback is weak or "
                 "missing, or a large subject never comes back to the viewer's scale.",
         "Fair": "The promise is paid but buried under a recap, a sign-off or a long "
                 "closing line.",
         "Poor": "The promise is not paid, the payoff is empty or generic, or the film "
                 "ends on a summary."},
        "Poor: \"So, to recap: ice, dust, light and wind. Thanks for watching!\" "
        "Excellent: \"So that comet is not flying backwards. It is showing you the wind.\"",
        "practitioner",
        ("structure 7", "structure 6", "attention 6", "structure 27", "structure 33",
         "structure 34", "structure 32")),
    Criterion(
        "honesty",
        "Could any beat be false while every sentence in it is true: an invented "
        "villain, deadline, motive or plot the sources do not support?",
        {"Excellent": "Every stake, motive and turn is one the sources support; any "
                      "dramatisation is labelled as such.",
         "Good": "The beats are supported; one simplification leans further than the "
                 "source but does not change what is true.",
         "Fair": "A beat implies a cause, a rivalry or a deadline the sources do not "
                 "state.",
         "Poor": "A beat manufactures stakes: a villain, a hidden plot or a motive the "
                 "sources do not support."},
        "Poor: \"Astronomers hid the truth about the solar wind for decades.\" "
        "Excellent: \"Biermann's idea was doubted until a probe measured the wind.\"",
        "practitioner",
        ("attention 20", "attention 19")),
)

#: The criteria's keys, in rubric order.
KEYS = tuple(c.key for c in RUBRIC)


def criterion(key: str) -> Criterion:
    """The criterion called `key`; an unknown key is a KeyError naming the rubric."""
    for c in RUBRIC:
        if c.key == key:
            return c
    raise KeyError(f"{key!r} is no rubric criterion (the rubric has {', '.join(KEYS)})")


def grade_levels(seed: int) -> tuple[str, ...]:
    """The four levels in the order sample `seed` is shown them.

    Seed 0 is the rubric's order. Any run of consecutive seeds shorter than
    24 gets distinct orders (the stride is prime to the 24 permutations),
    and seeds 1, 2 and 3 each lead with a different level, so a judge that
    favours the first option favours a different grade in each sample
    (judging 8: shuffle option order across evaluations)."""
    every = list(permutations(LEVELS))
    return every[(seed * 7) % len(every)]


def median(values: Iterable[str], order: Iterable[str], *,
           borderline: str | None = None) -> str | None:
    """The middle label of `values` on the scale `order` (judging 11: the
    majority of several runs, not one).

    LEVELS runs best first; VERDICTS and PREVIOUS run worst first. Three
    labels give the middle one, so a single outlier on either side is out
    voted. An even count whose middle two differ is `borderline` when the
    caller names one (two samples that disagree on the verdict are a
    consider), else the worse of the two: a grade is never rounded up.
    A label off the scale is not counted; nothing counted is None."""
    scale = list(order)
    worst_first = tuple(scale) in (VERDICTS, PREVIOUS)
    ranked = sorted((scale.index(v) for v in values if v in scale),
                    reverse=not worst_first)          # worst first
    if not ranked:
        return None
    mid = len(ranked) // 2
    if len(ranked) % 2:
        return scale[ranked[mid]]
    lo, hi = ranked[mid - 1], ranked[mid]
    if lo == hi:
        return scale[lo]
    return borderline if borderline is not None else scale[lo]


def _filled(text: str, window: str) -> str:
    return text.replace("{window}", window)


def rubric_text(audience: str, *, keys: Iterable[str] | None = None,
                window: str = OPENING_WINDOW, seed: int = 0) -> str:
    """The rubric as numbered lines for one reader.

    `writer`: each criterion's question and what Excellent looks like, for the
    script's prompt. `reviewer`: the question, every level in the order
    `grade_levels(seed)` gives, the worked example, the strength and the
    rules. `keys` keeps only those criteria (the plan review reads the rows
    a plan can show); `window` is the hook's window in the house's words.
    Both end on the sentence that longer is not better."""
    if audience not in AUDIENCES:
        raise ValueError(f"rubric_text is for {' or '.join(AUDIENCES)}, not {audience!r}")
    chosen = [criterion(k) for k in keys] if keys is not None else list(RUBRIC)
    lines: list[str] = []
    for i, c in enumerate(chosen, 1):
        if audience == "writer":
            lines.append(f"{i}. {c.key}: {_filled(c.question, window)} "
                         f"Aim for: {_filled(c.levels['Excellent'], window)}")
            continue
        lines.append(f"{i}. {c.key} [{c.strength}]: {_filled(c.question, window)}")
        lines += [f"   - {level}: {_filled(c.levels[level], window)}"
                  for level in grade_levels(seed)]
        lines.append(f"   Example. {_filled(c.example, window)}")
        lines.append(f"   Drawn from docs/narrative {', '.join(c.rules)}.")
    lines.append(LONGER)
    return "\n".join(lines)


def reviewer_role(*, window: str = OPENING_WINDOW, seed: int = 0, plan: bool = False) -> str:
    """The story reviewer's role: how to read, the rubric with each criterion's
    strength, and what each verdict requires.

    `plan` is the role for the plan review (PLAN_REVIEW_SCHEMA), read before
    any line is written: its verdicts are accept, revise and reject and its
    only grounds are the train, the order, a section that moves nothing and
    the payoff. The script review's rules (a synopsis first, recommend or
    consider, a Poor stakes rejects, the first minute read twice) would
    contradict that prompt in the same call, so they are left out of it;
    the rubric rows a plan shows are in the plan review's prompt."""
    if plan:
        return "\n\n".join([
            "You are the film's story reviewer, reading its plan (the beat sheet) before "
            "any line of it is written, the way a coverage reader reads a treatment "
            "before money is spent on it. You did not write it. This reading is of the "
            "plan only: there is no narration yet, so you summarise nothing first and "
            "give no grade; the criteria in the prompt are what you read the plan "
            "against, and you say whether it holds.",
            "The verdict is accept, revise or reject. accept: the plan holds. revise: it "
            "is a story with concrete problems that planning again can fix, each named "
            "with its section and a fix. reject: it is not a story: the train names "
            "only a topic, the sections are a list that survives any order, or the "
            "payoff is empty. Only the train, the order, a section that moves nothing "
            "and the payoff are grounds to revise or reject. A weak stakes line, an "
            "open loop or a thin reveal setup is a problem to name with its fix, never "
            "on its own a reason to send the plan back.",
            "Your fields say the same as your verdict: an accept has a train that holds, "
            "an order that does not survive reordering, a concrete payoff and no section "
            "that moves nothing; a revise or a reject names at least one problem.",
            LONGER,
        ])
    return "\n\n".join([
        "You are the film's story reviewer, reading a plan and its script the way a "
        "coverage reader reads a screenplay before money is spent on it. You did not "
        "write it. First write the logline (one or two lines) and a short synopsis that "
        "proves you followed the story; then grade each criterion below with one of its "
        "four labels. Do not reason at length before grading: the synopsis is the only "
        "writing that comes first. Grade each criterion on its own and absolutely, never "
        "against another draft.",
        "The criteria, each tagged with the strength of the evidence behind it "
        "(peer-reviewed, platform, practitioner or house):\n"
        + rubric_text("reviewer", window=window, seed=seed),
        "The verdict. recommend: strong in every respect, no Poor anywhere and no "
        "blocker. consider: worth voicing once concrete problems are fixed; every "
        "problem is a flag naming the section, the shot number (0 for the whole "
        "script) and the criterion it fails, with a fix the writer can apply without "
        "asking you anything. reject: it fails in most areas, or one of the four "
        "criteria that decide a film (train, stakes, open_loops, ending) is Poor; "
        f"but {HOUSE_POOR}, because a house rule is a prior to tune, not a gate. "
        "Longer is not better: never grade a draft up for its length.",
        "Read the first minute twice, once for whether it confirms what the title "
        "promised and once for its question and stakes, and say what each pass found.",
        "In a revision round you are also shown the previous review. Grade this draft "
        "as above, then fill `previous`: whether this draft is better, the same or worse "
        "than the one it revises, and for each earlier problem whether it is resolved, "
        "persists or regressed. That is the only comparison you make.",
    ])


# --------------------------------------------------------------------------- #
# schemas
# --------------------------------------------------------------------------- #

def _grades(levels: Iterable[str], window: str = OPENING_WINDOW) -> dict:
    order = list(levels)
    return {"type": "object",
            "properties": {c.key: {"type": "string", "enum": list(order),
                                   "description": _filled(c.question, window)}
                           for c in RUBRIC},
            "required": list(KEYS)}


#: The review of a plan and its script, shaped as coverage (judging 12). The
#: property order is the order the reviewer fills it: the logline and the
#: synopsis before any grade.
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "logline": {"type": "string", "description": "the story in one or two lines"},
        "synopsis": {"type": "string", "description": (
            "what happens, section by section, written before any grade")},
        "grades": _grades(LEVELS),
        "verdict": {"type": "string", "enum": list(VERDICTS), "description": (
            "recommend: no Poor and no blocker; consider: concrete problems to fix "
            "before voicing, each a flag; reject: fails in most areas")},
        "flags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer", "description": "the shot number; 0 for the whole script"},
                    "section": {"type": "integer",
                                "description": "the section number; 0 for the whole script"},
                    "criterion": {"type": "string", "enum": list(KEYS)},
                    "problem": {"type": "string"},
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "fix": {"type": "string", "description": (
                        "what the writer changes, in the script's own terms")},
                },
                "required": ["n", "section", "criterion", "problem", "severity", "fix"],
            },
        },
        # structure 39: the first minute is read twice.
        "first_minute": {
            "type": "object",
            "properties": {
                "promise_match": {"type": "string", "description": (
                    "first pass: does the opening confirm what the title promised")},
                "question_and_stakes": {"type": "string", "description": (
                    "second pass: the question it opens and what is at stake")},
            },
            "required": ["promise_match", "question_and_stakes"],
        },
        # For the cold reader (judging 14): where each of the plan's questions
        # was answered, as the reader found it.
        "answered": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"question_id": {"type": "string"},
                               "shot_index": {"type": "integer"}},
                "required": ["question_id", "shot_index"],
            },
        },
        # From round 1 on (`review_schema(delta=True)` requires it), as in
        # vfx.review_schema: the revision against the round before.
        "previous": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["better", "same", "worse"]},
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "problem": {"type": "string"},
                            "status": {"type": "string",
                                       "enum": ["resolved", "persists", "regressed"]},
                        },
                        "required": ["problem", "status"],
                    },
                },
            },
            "required": ["verdict", "findings"],
        },
    },
    "required": ["logline", "synopsis", "grades", "verdict", "flags", "first_minute"],
}


def review_schema(*, delta: bool, seed: int = 0, window: str = OPENING_WINDOW) -> dict:
    """The review's schema for one sample. A revision's must carry the verdict
    on the round it was shown; `seed` orders each grade's levels as
    `grade_levels(seed)` does; `window` is the hook's window in the house's
    words, the same the caller passes to `reviewer_role`, so one sample's
    role and schema say the same thing. REVIEW_SCHEMA itself is never
    changed."""
    if not delta and not seed and window == OPENING_WINDOW:
        return REVIEW_SCHEMA
    out = copy.deepcopy(REVIEW_SCHEMA)
    if seed or window != OPENING_WINDOW:
        out["properties"]["grades"] = _grades(grade_levels(seed), window)
    if delta:
        out["required"] = out["required"] + ["previous"]
    return out


#: The plan's review, before any line is written: whether the train is a
#: question with an obstacle, whether the sections are a chain or a list,
#: whether the payoff is concrete and every question is answered.
PLAN_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(PLAN_VERDICTS)},
        "train_ok": {"type": "boolean", "description": (
            "the train is one sentence with a question and a named obstacle, not a topic")},
        "reorder_survives": {"type": "boolean", "description": (
            "true when the sections could be reordered without loss: a list, not a chain")},
        "sections_advancing_none": {"type": "array", "items": {"type": "integer"},
                                    "description": "section numbers that move the train not at all"},
        "payoff_concrete": {"type": "boolean", "description": (
            "the payoff answers the promise with something the viewer can picture")},
        "questions_unanswered": {"type": "array", "items": {"type": "string"},
                                 "description": "ledger ids opened and never closed nor left on purpose"},
        "problems": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section": {"type": "integer", "description": "0 for the whole plan"},
                    "problem": {"type": "string"},
                    "fix": {"type": "string"},
                },
                "required": ["section", "problem", "fix"],
            },
        },
    },
    "required": ["verdict", "train_ok", "reorder_survives", "sections_advancing_none",
                 "payoff_concrete", "questions_unanswered", "problems"],
}


def check_plan_review(review: Any) -> list[str]:
    """What a plan review says that contradicts its own verdict, one line
    per problem: a revise or a reject that names no problem (a second plan
    would be told to fix nothing), and an accept whose own fields find the
    train failing, the order a list, the payoff not concrete or a section
    that moves nothing. The schema cannot refuse these. As with
    `check_review`, the caller asks once more (`<name>-again`) with these
    lines and records what still stands; the verdict stays the model's."""
    if not isinstance(review, dict):
        return []
    found: list[str] = []
    verdict = review.get("verdict")
    problems = review.get("problems") if isinstance(review.get("problems"), list) else []
    if verdict in ("revise", "reject") and not problems:
        found.append(f"a {verdict} names the problems that send the plan back, and this "
                     "one carries none: name each with its section and fix, or accept")
    if verdict == "accept":
        if review.get("train_ok") is False:
            found.append("an accept holds the train, and this one says train_ok is false: "
                         "change the verdict or the field")
        if review.get("reorder_survives") is True:
            found.append("an accept holds the order, and this one says reorder_survives "
                         "is true (the sections are a list): change the verdict or the field")
        if review.get("payoff_concrete") is False:
            found.append("an accept holds the payoff, and this one says payoff_concrete is "
                         "false: change the verdict or the field")
        idle = [str(n) for n in review.get("sections_advancing_none") or []]
        if idle:
            found.append(f"an accept has every section move the train, and this one names "
                         f"section {', '.join(idle)} under sections_advancing_none: change "
                         "the verdict or the list")
    return found


def check_review(review: Any) -> list[str]:
    """What a review says that its own rubric forbids, one line per problem.

    The harness checks the schema (fields, types, enums) and asks once
    more; these are the shapes a schema cannot refuse: a recommend that
    carries a Poor or a blocker, a consider with nothing to fix, and a flag
    on a criterion the rubric does not have (the harness lets an enum value
    through after its retry). The caller asks again once under its own
    name (`<name>-again`), with these lines in the prompt, and records a
    second failure; nothing here replaces the verdict."""
    if not isinstance(review, dict):
        return []
    found: list[str] = []
    verdict = review.get("verdict")
    grades = review.get("grades") if isinstance(review.get("grades"), dict) else {}
    flags = review.get("flags") if isinstance(review.get("flags"), list) else []
    poor = [k for k, v in grades.items() if v == "Poor"]
    if verdict == "recommend" and poor:
        found.append(f"a recommend carries no Poor, and this one grades "
                     f"{', '.join(poor)} Poor: lower the verdict or the grade")
    blocking = sorted({str(f.get("criterion")) for f in flags
                       if isinstance(f, dict) and f.get("severity") == "blocker"})
    if verdict == "recommend" and blocking:
        found.append(f"a recommend carries no blocker, and this one flags a blocker on "
                     f"{', '.join(blocking)}: lower the verdict or the severity")
    if verdict == "consider" and not flags:
        found.append("a consider names problems to fix before voicing: it carries no flag")
    unknown = sorted({str(f.get("criterion")) for f in flags
                      if isinstance(f, dict) and f.get("criterion") not in KEYS})
    if unknown:
        found.append(f"flags name {', '.join(unknown)}, which "
                     f"{'is' if len(unknown) == 1 else 'are'} no rubric criterion "
                     f"(the rubric has {', '.join(KEYS)})")
    return found


# --------------------------------------------------------------------------- #
# the picture-check (AUTO-98)
# --------------------------------------------------------------------------- #
#
# Say it, show it. The fact-check and the narration rules read the words;
# this reads what the screenplay puts on screen, with the words taken away.
# Vox's picture-off test (direction 4): a reviewer given only the shot
# descriptions must reconstruct each section's claim. Beside it, three
# things only a reader can see in a picture: a large number left bare
# (attention 15), a mechanism a shot says it demonstrates that its picture
# does not (direction 4's narrated mechanism no shot depicts), and a list
# the pictures present with nothing to tell its parts apart (attention 22).
# What a count can find, a super that prints the line, is
# `rule:wall-of-text` in script.RULES (direction 2); motion is measured from
# frames at vfx and qa, never here.

#: The corpus rules the picture-check reads by, in the corpus's spelling.
PICTURE_RULES = ("direction 4", "direction 2", "attention 15", "attention 22",
                 "attention 23")


def picture_role() -> str:
    """The picture-check's role: the reviewer reads the screen and never the
    narration, reconstructs before it compares, and judges what is shown,
    never how it looks (invariant 2)."""
    return "\n\n".join([
        "You are the film's picture reviewer. You read only what the screenplay puts on "
        "screen: each shot's picture (`visual`), what that picture must demonstrate of its "
        "line (`shows`) and the words printed on screen (`on_screen_text`). You are given "
        "the pictures and never the narration, and you do not guess it: a viewer with the "
        "sound off sees what you see.",
        "For each section, first write the claim a viewer would take from its pictures "
        "alone, as if the plan were not in front of you; then read the beat the plan gave "
        "that section and say whether the pictures carry it, what of it they leave out, "
        "and the shot numbers where the pictures lose the thread.",
        "Then report the shots that show one of three faults, and only those: a figure of "
        "1,000 or more put on screen or asked of the picture with no comparison at human "
        "scale and no picture of the quantity (a bare number); a mechanism or claim the "
        "shot says its picture demonstrates that the picture as described does not show "
        "(an unshown claim); and a list or set of parts the pictures present with no count "
        "and no label telling the viewer which part is which, or which one is being "
        "spoken of (an unsignposted list).",
        "Judge what is shown, never how it looks: the treatment decides the look, so a "
        "report names no colour, typeface, layout or style. Say what the viewer cannot "
        "see or follow; the writer decides how to show it.",
        LONGER,
    ])


#: What the picture-check answers: per section the claim read from the
#: pictures, whether it is the plan's beat and where the thread is lost;
#: per shot, only the shots with a fault, each fault "" when absent. The
#: shot numbers are the screenplay's, as the prompt lists them.
PICTURE_SCHEMA = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section": {"type": "integer", "description": "the section's number"},
                    "claim": {"type": "string", "description": (
                        "the claim a viewer takes from this section's pictures alone, "
                        "written before comparing it with the beat")},
                    "carries_beat": {"type": "boolean", "description": (
                        "true when that claim is the beat the plan gave the section")},
                    "missing": {"type": "string", "description": (
                        "what of the beat the pictures do not carry; empty when they do")},
                    "confused_at": {"type": "array", "items": {"type": "integer"},
                                    "description": "the shot numbers where the pictures "
                                                   "lose the thread"},
                },
                "required": ["section", "claim", "carries_beat", "missing", "confused_at"],
            },
        },
        "reports": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer", "description": "the shot number"},
                    "bare_number": {"type": "string", "description": (
                        "the figure of 1,000 or more shown with no comparison at human "
                        "scale and no picture of the quantity, and where; empty when none")},
                    "unshown_claim": {"type": "string", "description": (
                        "the mechanism or claim the shot says it demonstrates that its "
                        "picture does not show; empty when none")},
                    "unsignposted_list": {"type": "string", "description": (
                        "the list or set of parts shown with no count and no label for "
                        "which is which; empty when none")},
                },
                "required": ["n", "bare_number", "unshown_claim", "unsignposted_list"],
            },
        },
    },
    "required": ["sections", "reports"],
}
