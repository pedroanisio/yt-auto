"""The narrative rubric: what a story is judged on, read by the writer and the reviewer.

No rubric existed: the script stage wrote against house prose and nothing
graded the story before voice was paid for. This module is that rubric, in
the shape docs/narrative/judging-narrative.md sets for a model judge:

- ten criteria (`RUBRIC`), each a question with four labelled levels of
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
reveal setup, tension, honesty, voice and devices are the other six a reader can
judge and a lint cannot. What a lint can measure (hook timing, sentence length, concrete
before abstract by position) belongs to the deterministic checks, not here.

Like `framecheck.CHECKLIST`, one tuple is meant to be rendered twice: into
the writer's prompt (`rubric_text("writer")`, the top level of each
criterion) and into the reviewer's role (`reviewer_role()`, every level with
its strength), so a round is never spent finding out what will be looked at.
`yt qa`'s role renders the writer's lines for the rows a finished film can
be judged on (`qa.STORY_RUBRIC`), and `yt improve`'s router reads them to
name the key a story finding bears on (AUTO-99).
`yt script --review` wires the plan's role (`reviewer_role(plan=True)`),
PLAN_REVIEW_SCHEMA and `check_plan_review` into the plan review (AUTO-92;
its grounds to reject are the train, the order, an idle section and the
payoff, and AUTO-161 adds stakes as a ground to revise, never to reject),
and `picture_role()` and PICTURE_SCHEMA into the picture-check that reads
the drafted screenplay's pictures with the narration taken away (AUTO-98),
and the script's role (`reviewer_role(seed=k)`), `review_schema` and
`check_review` into the story review's three samples (AUTO-93). Nothing
here decides a verdict: it is schema and prompt text, `check_review` and
`check_plan_review` only name what the caller asks the model again about
(invariant 3), and `median` only reads the samples' own labels.

A criterion's strength is the README's word for its lead rule. `devices`
(AUTO-135) is the first row whose lead rule is house strength (attention 2,
the guess asked before a reveal and scored by it, which the corpus marks as
the best available proxy, not a tested device), so the role's sentence that
a Poor on a house-strength criterion yields consider now governs a row: a
device that teaches something false is a consider, never on its own a
reject. The other rows lead with a practitioner rule, with structure 10
(platform) for the hook, although the hook's window is the house's choice,
or with attention 25 (peer-reviewed) for voice.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from dataclasses import dataclass, field
from itertools import permutations
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

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

#: The curiosity triggers an opening may pull (AUTO-136), from attention 1:
#: Loewenstein's four triggers of an information gap, a question posed
#: (`question`), a sequence whose end is withheld (`unresolved_outcome`), an
#: expectation violated and information someone has that the viewer lacks
#: (`someone_knows`), with the expectation violation split in two: a
#: contradiction the viewer can state (`paradox`) and a thing seen that
#: should not be so (`unexpected_observation`, the rubric's own comet line).
#: The plan declares one (script.STORY_PROPERTIES["hook"]); script.py reads
#: this tuple and keeps no copy.
HOOK_TRIGGERS = ("question", "paradox", "unexpected_observation", "unresolved_outcome",
                 "someone_knows")

#: What the story reviewer may say it heard in the first three spoken
#: sentences: one of HOOK_TRIGGERS; `none`, narration exists and pulls no
#: trigger; `not_heard`, no narration is drafted to hear (a review of the
#: plan alone, as calibrate's plan-only cases are).
HEARD_TRIGGERS = HOOK_TRIGGERS + ("none", "not_heard")

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

#: Said to the story reviewer (AUTO-138): voice is graded like the rest but
#: never decides a film, and its flag is a line's, so it reaches the writer
#: as a shot to revise (`revise-story-N`), never as a plan to write again.
VOICE_RULE = ("Voice is graded and flagged like every other criterion but is not one of "
              "the four that decide a film: a Poor voice yields consider, never reject on "
              "its own. A voice flag names the shot whose line reads as a lecture and "
              "quotes it; never flag voice on shot 0.")


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
        "open a question the title does not already answer, on a named curiosity "
        "trigger (a question, a paradox, an unexpected observation, an unresolved "
        "outcome, or something someone knows that the viewer does not), without "
        "listing the conclusions?",
        {"Excellent": "The subject is on screen or in words inside {window}; the question "
                      "it opens is specific, the trigger is concrete within the first "
                      "three sentences, and no answer is given away.",
         "Good": "The subject is confirmed inside {window}; the question is a fair one but "
                 "close to what the title says.",
         "Fair": "The subject arrives after {window}, or the question is a generic tease, "
                 "or the first three sentences hold no trigger.",
         "Poor": "The opening names a different subject, only restates the title and "
                 "opens no question beyond it, or enumerates the conclusions the film "
                 "will reach."},
        "Poor: \"In this video: tails point away because of light pressure and the solar "
        "wind.\" Excellent: \"This comet is leaving the Sun, and its tail is in front.\"",
        "platform",
        ("structure 10", "structure 13", "structure 14", "structure 6", "attention 4",
         "structure 39", "attention 1")),
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
        # attention 2, the guess before the reveal, is graded by `devices`
        # (AUTO-135): no level here mentioned a guess, so nothing graded it
        ("structure 22", "structure 20")),
    # AUTO-136: rising stakes had no row; escalation reached a reviewer only
    # as the writer's own `escalation` field. Not one of the four criteria
    # that decide a film, so a Poor here is a consider.
    Criterion(
        "tension",
        "Does each section put more at risk, or make the question harder, than the one "
        "before, and does the film re-hook the viewer where one section hands over to "
        "the next, rather than holding one level throughout? The final section "
        "resolves; it is not asked to raise the stakes.",
        {"Excellent": "Each middle section raises the cost or the difficulty, with a "
                      "rehook at the hand-over and at the later drop points.",
         "Good": "The stakes rise overall, but one section plateaus.",
         "Fair": "The stakes stay flat after the opening, or the rehooks only summarise.",
         "Poor": "The stakes fall after the opening, or the middle is a list with nothing "
                 "new at risk."},
        "Fair: sections 2 and 3 both say sunlight is too weak, and nothing new is at "
        "risk. Excellent: section 2 finds the known force too weak, section 3 finds the "
        "wind that replaces it blowing at Earth too.",
        "practitioner",
        ("attention 9", "structure 23", "structure 25", "structure 26")),
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
    # AUTO-138: the brief asks for a narrator who sounds human, and until
    # this row nothing graded the register before voice was paid for:
    # rule:person only counts you/we words, at house tier. Not one of the
    # four criteria that decide a film. Humour is graded only when it is
    # there: no level asks for a joke, so a film with none loses nothing.
    Criterion(
        "voice",
        "Does the narrator find things out alongside the viewer, rather than read a "
        "lecture at them: speaking to them, asking aloud the question they are about to "
        "ask, flagging the step most likely to lose them before it comes, and letting a "
        "discovery land as a discovery; and does any humour serve the point being made?",
        {"Excellent": "The narrator speaks to the viewer and finds out with them: asks "
                      "aloud what they are about to ask, flags the hardest step before it "
                      "comes, lets at least one discovery land as found, and any humour "
                      "lands on the point being made.",
         "Good": "Conversational and addressed to the viewer throughout; questions are "
                 "asked, but the likeliest point of confusion passes unflagged or "
                 "discoveries are stated rather than found.",
         "Fair": "Correct but read like a lecture in stretches: conclusions asserted in an "
                 "expert voice with no question or address to the viewer, or a joke or "
                 "aside that serves no claim.",
         "Poor": "An encyclopedia read aloud: impersonal throughout, never a question or a "
                 "word to the viewer, nothing found out; or humour at the expense of the "
                 "viewer or of people the subject harmed."},
        "Poor: \"Comet tails are oriented anti-sunward owing to radiation pressure and the "
        "solar wind.\" Excellent: \"A tail that leads? That looks backwards. Hold that "
        "thought: the answer is blowing past you right now.\"",
        "peer-reviewed",
        ("attention 25", "attention 18", "direction 24", "attention 21")),
    # AUTO-135: the ways in a human explainer uses for a mechanism the viewer
    # cannot picture (an analogy, a visual metaphor, a guess before the
    # reveal, a thought experiment), graded on whether each maps correctly.
    # A film that shows its mechanism directly needs none, so a missing
    # device is never a fault here; an unframed imagining or an invented
    # precedent stays honesty's (attention 20), so no fault is graded twice.
    # House strength: a Poor is a consider (HOUSE_POOR), and devices is not
    # one of the four criteria that decide a film.
    Criterion(
        "devices",
        "Where a mechanism is hard to picture, does the film give the viewer a way in, "
        "by showing it directly or through an analogy or visual metaphor whose parts "
        "and relations map onto the mechanism's, with where it breaks said aloud? Is a "
        "guess asked before the main reveal scored by that reveal? Does any device teach "
        "something false? (An unframed imagining or an unsourced precedent is honesty's, "
        "not this row's.)",
        {"Excellent": "Every hard mechanism is either shown directly or given a device "
                      "that maps correctly and says where it breaks; any guess asked is "
                      "scored by the reveal.",
         "Good": "Devices are correct; one is left half-done: an analogy's limit unsaid, "
                 "or a guess asked and never scored.",
         "Fair": "A device is decoration that maps nothing, or a mechanism the viewer "
                 "cannot picture is left with neither a picture nor a device.",
         "Poor": "A device maps a part or relation onto the wrong thing, so the viewer "
                 "learns something false."},
        "Poor: \"the tail streams behind like smoke behind a train\", drawn trailing the "
        "comet's path. Excellent: \"which way do you think the tail points as it "
        "leaves?\", then \"a windsock points where the wind goes, not where the pole "
        "moves; unlike a windsock, this one has two tails\".",
        "house",
        ("attention 2", "direction 24")),
)

#: The criteria's keys, in rubric order.
KEYS = tuple(c.key for c in RUBRIC)

#: Rows only the plan review reads (AUTO-157). The film's form, its macro
#: shape (a mystery, a journey, a debate ...), is chosen per film: structure
#: 20 quotes Fong's "no fixed template because every story has its own
#: needs", and the corpus names no list of forms, so the vocabulary is a
#: house prior (script.FORMS). It is kept out of RUBRIC on purpose: the
#: story review does not grade it, its three-sample medians, the level
#: permutations, REVIEW_SCHEMA, qa.STORY_RUBRIC and improve's router are
#: unchanged, and rubric_text() with no keys does not render it. The plan
#: review reads it through rubric_text(keys=script.PLAN_CRITERIA), and a
#: form that does not fit is a ground to revise only (FORM_GROUND).
PLAN_ONLY: tuple[Criterion, ...] = (
    Criterion(
        "form",
        "Does the film's declared form, its shape as a whole, follow from something "
        "this subject has, and do the sections carry it, or is it a label any topic "
        "could wear?",
        {"Excellent": "The form follows from something this subject has (a deadline, a "
                      "dispute, a trail of evidence), the sections carry it, and the "
                      "obvious form passed over is named with why.",
         "Good": "The form fits; one section breaks from it without reason.",
         "Fair": "The form fits, but its reason would fit most subjects, or the middle "
                 "drifts from it.",
         "Poor": "The form is a label any topic could wear, or the sections ignore it."},
        "Poor: \"mystery: mysteries are engaging\". Excellent: \"investigation: Biermann "
        "found the wind by reading comet photographs; the film follows his evidence in "
        "the order he had it\".",
        "practitioner",
        ("structure 20",)),
)


def criterion(key: str) -> Criterion:
    """The criterion called `key`, from RUBRIC or PLAN_ONLY; an unknown key
    is a KeyError naming the rubric."""
    for c in (*RUBRIC, *PLAN_ONLY):
        if c.key == key:
            return c
    raise KeyError(f"{key!r} is no rubric criterion (the rubric has {', '.join(KEYS)}; "
                   f"the plan review also reads {', '.join(c.key for c in PLAN_ONLY)})")


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


#: The plan review's sameness ground (AUTO-137), added to the role only when
#: the channel has counted earlier films: a repeat sends a plan back to be
#: planned again, never out of the pipeline.
CHANNEL_GROUND = (", and so is a repeat of an earlier channel film that a returning "
                  "viewer would notice, which is never a ground to reject")

#: The plan review's form ground (AUTO-157), in the role and in the attack
#: alike (script.PLAN_ATTACK ends its grounds sentence on it): a form that
#: does not follow from the subject sends the plan back once, and a second
#: reading that objects to the form alone drafts on with an advisory
#: (script.Screenwriter.gate_plan), so it never stops the stage by itself.
FORM_GROUND = (", and a form that does not follow from the subject is a ground to "
               "revise only, never to reject")


def reviewer_role(*, window: str = OPENING_WINDOW, seed: int = 0, plan: bool = False,
                  channel: bool = False) -> str:
    """The story reviewer's role: how to read, the rubric with each criterion's
    strength, and what each verdict requires.

    `plan` is the role for the plan review (PLAN_REVIEW_SCHEMA), read before
    any line is written: its verdicts are accept, revise and reject, its
    only grounds to reject are the train, the order, a section that moves
    nothing and the payoff, and stakes (no concrete consequence before the
    mechanism, and no declared twist) is a ground to revise too (AUTO-161:
    AUTO-92 had made stakes only a problem to name, so a plan with no reason
    to care reached the screenplay and was caught after it was paid for).
    The script review's rules (a synopsis first, recommend or
    consider, a Poor stakes rejects, the first minute read twice) would
    contradict that prompt in the same call, so they are left out of it;
    the rubric rows a plan shows are in the plan review's prompt.

    `channel` (the plan review with counted earlier films, AUTO-137) adds
    one ground to revise after the four and stakes: a repeat a returning
    viewer would notice, never a ground to reject. Without it the role is today's, byte for byte.

    Both variants end the grounds sentence on the form (AUTO-157,
    FORM_GROUND): a declared form that does not follow from the subject is a
    ground to revise only."""
    if plan:
        grounds = ("Only the train, the order, a section that moves nothing and the "
                   "payoff are grounds to reject. Those four, and stakes, are grounds to "
                   "revise" + (CHANNEL_GROUND if channel else "") + FORM_GROUND + ".")
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
            "payoff is empty. " + grounds + " A plan has no stakes when it explains its "
            "mechanism before it gives a concrete consequence the viewer can picture, "
            "unless the plan says the danger is withheld as its twist. An open loop, a "
            "thin reveal setup or a device that maps onto the wrong thing is a problem "
            "to name with its fix, never on its own a reason to send the plan back.",
            "Your fields say the same as your verdict: an accept has a train that holds, "
            "an order that does not survive reordering, a concrete payoff and no section "
            "that moves nothing, and an accept has stakes_concrete true and form_fits "
            "true; a revise or a reject names at least one problem, and form_fits false "
            "says what does not fit in form_problem.",
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
        "asking you anything. A flag quotes the words it is about exactly as the "
        "narration says them and never paraphrases, and a contradiction quotes the "
        "other line too. reject: it fails in most areas, or one of the four "
        "criteria that decide a film (train, stakes, open_loops, ending) is Poor; "
        f"but {HOUSE_POOR}, because a house rule is a prior to tune, not a gate. "
        f"{VOICE_RULE} "
        "Longer is not better: never grade a draft up for its length.",
        "Read the first minute twice, once for whether it confirms what the title "
        "promised and once for its question and stakes, and say what each pass found. "
        "The second pass also names the curiosity trigger the first three spoken "
        "sentences pull (trigger: question, paradox, unexpected_observation, "
        "unresolved_outcome or someone_knows; none if they pull none; not_heard if no "
        "narration is drafted) and the gap they open, as heard (gap; empty when "
        "not_heard).",
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
                    # AUTO-112: which words the flag is about, so the writer
                    # does not guess the clause and two samples' flags can be
                    # told apart. Checked against the script, never trusted.
                    "quote": {"type": "string", "description": (
                        "the whole words the flag is about, the full clause, copied "
                        "exactly from the narration, never paraphrased: from shot n's "
                        "line, or for a section flag (shot 0) from one of that section's "
                        "lines; empty for the whole script (shot 0, section 0) and for a "
                        "silent shot or section, which says no words")},
                    "conflicts_with": {
                        "type": "object",
                        "description": ("only when this line contradicts another: the "
                                        "other line, its words copied exactly"),
                        "properties": {
                            "n": {"type": "integer",
                                  "description": "the shot number of the other line"},
                            "quote": {"type": "string", "description": (
                                "the other line's words, copied exactly")},
                        },
                        "required": ["n", "quote"],
                    },
                },
                "required": ["n", "section", "criterion", "problem", "severity", "fix",
                             "quote"],
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
                # AUTO-136: attention 1's trigger, as heard, not as planned
                "trigger": {"type": "string", "enum": list(HEARD_TRIGGERS),
                            "description": (
                                "the curiosity trigger the first three spoken sentences "
                                "use; none if they use none; not_heard if no narration "
                                "is drafted")},
                "gap": {"type": "string", "description": (
                    "the gap the opening opens, as heard; empty when not_heard")},
            },
            "required": ["promise_match", "question_and_stakes", "trigger", "gap"],
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
#: whether the payoff is concrete, whether the viewer is given a reason to
#: care before the mechanism (AUTO-161) and every question is answered.
#: `stakes_concrete` is one named field, not a grade, so the plan role's
#: "give no grade" still holds.
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
        "stakes_concrete": {"type": "boolean", "description": (
            "a concrete consequence the viewer can picture comes before the first section "
            "that explains a mechanism, or the plan says the danger is withheld as its "
            "twist")},
        "questions_unanswered": {"type": "array", "items": {"type": "string"},
                                 "description": "ledger ids opened and never closed nor left on purpose"},
        # AUTO-157: two named fields, not a grade, so "give no grade" holds
        "form_fits": {"type": "boolean", "description": (
            "the declared form follows from this subject and the sections carry it; true "
            "when the plan names no form")},
        "form_problem": {"type": "string",
                         "description": "what in the form does not fit, or empty"},
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
                 "payoff_concrete", "stakes_concrete", "questions_unanswered", "form_fits",
                 "form_problem", "problems"],
}


#: What the plan review adds with counted earlier films (AUTO-137): the
#: repeats a returning viewer would notice, each naming the earlier film by
#: its slug, and, optionally, the measured `channel:` lines it dismisses.
REPEATS_PROPERTY = {
    "type": "array",
    "description": (
        "only the repeats of an earlier channel film a returning viewer would notice: "
        "the film by its slug as listed, which of its devices this plan repeats, and "
        "the problem. Each is also a problem of section 0 that names the film, with "
        "its fix; a repeat is a ground to revise, never to reject. Empty when none "
        "would be noticed."),
    "items": {
        "type": "object",
        "properties": {
            "film": {"type": "string", "description": "the earlier film's slug, as listed"},
            "dimension": {"type": "string", "enum": ["opening", "structure", "closing"]},
            "problem": {"type": "string"},
        },
        "required": ["film", "dimension", "problem"],
    },
}
DISMISSED_PROPERTY = {
    "type": "array",
    "description": ("each measured `channel:` pre-check you do not list under repeats, "
                    "with why a returning viewer would not notice it"),
    "items": {
        "type": "object",
        "properties": {"film": {"type": "string"},
                       "dimension": {"type": "string"},
                       "why": {"type": "string"}},
        "required": ["film", "why"],
    },
}
CHANNEL_PLAN_REVIEW_SCHEMA = copy.deepcopy(PLAN_REVIEW_SCHEMA)
CHANNEL_PLAN_REVIEW_SCHEMA["properties"]["repeats"] = REPEATS_PROPERTY
CHANNEL_PLAN_REVIEW_SCHEMA["properties"]["dismissed"] = DISMISSED_PROPERTY
CHANNEL_PLAN_REVIEW_SCHEMA["required"] = [*PLAN_REVIEW_SCHEMA["required"], "repeats"]


def plan_review_schema(channel: bool) -> dict:
    """The plan review's schema: PLAN_REVIEW_SCHEMA itself, or with counted
    earlier films (AUTO-137) one copy that also requires `repeats` (and
    may carry `dismissed`). The same object each call, so the stage can
    route the decision to its reviewer by identity."""
    return CHANNEL_PLAN_REVIEW_SCHEMA if channel else PLAN_REVIEW_SCHEMA


def _names(text: Any, slug: str) -> bool:
    return bool(slug) and re.search(rf"(?<![\w-]){re.escape(slug.lower())}(?![\w-])",
                                    str(text or "").lower()) is not None


def check_plan_review(review: Any, earlier_slugs: Sequence[str] = ()) -> list[str]:
    """What a plan review says that contradicts its own verdict, one line
    per problem: a revise or a reject that names no problem (a second plan
    would be told to fix nothing; a revise whose own ground fields find a
    fault has named it), and an accept whose own fields find the
    train failing, the order a list, the payoff not concrete, no stakes or a
    section that moves nothing. Stakes are grounds to revise and never to
    reject (AUTO-161), so a reject that finds no stakes and each of the four
    reject grounds clean in its own fields contradicts itself too. With
    earlier channel films (AUTO-137): an accept that lists repeats, a
    repeat of a film not listed or with no section 0 problem naming it, and
    a reject whose only grounds are repeats. The schema cannot refuse these. A field the review leaves out is no contradiction
    (old stubs and cached answers carry no `stakes_concrete`). As with
    `check_review`, the caller asks once more (`<name>-again`) with these
    lines and records what still stands; the verdict stays the model's."""
    if not isinstance(review, dict):
        return []
    found: list[str] = []
    verdict = review.get("verdict")
    problems = review.get("problems") if isinstance(review.get("problems"), list) else []
    # A revise whose own ground fields find a fault has named it: the second
    # plan is told each as a field line (script.review_field_lines), so an
    # empty problems list is no reason to ask again. A reject is still asked:
    # it stops the stage, and the operator's --notes need a section and a fix.
    named = verdict == "revise" and (
        review.get("train_ok") is False or review.get("reorder_survives") is True
        or review.get("payoff_concrete") is False or review.get("stakes_concrete") is False
        or bool(review.get("sections_advancing_none"))
        or (review.get("form_fits") is False and bool(_text(review.get("form_problem")))))
    if verdict in ("revise", "reject") and not problems and not named:
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
        if review.get("stakes_concrete") is False:
            found.append("an accept gives a reason to care before the mechanism, and this "
                         "one says stakes_concrete is false: change the verdict or the field")
        if review.get("form_fits") is False:
            found.append("an accept holds the form, and this one says form_fits is false: "
                         "change the verdict or the field")
        idle = [str(n) for n in review.get("sections_advancing_none") or []]
        if idle:
            found.append(f"an accept has every section move the train, and this one names "
                         f"section {', '.join(idle)} under sections_advancing_none: change "
                         "the verdict or the list")
    # "Alone" only when each of the four reject grounds is there and clean:
    # a reading that leaves one out has not said it is clean.
    clean = all((review.get("train_ok") is True, review.get("reorder_survives") is False,
                 review.get("payoff_concrete") is True,
                 review.get("sections_advancing_none") == []))
    if verdict == "reject" and review.get("stakes_concrete") is False and clean:
        found.append("a reject needs the train, the order, an idle section or the "
                     "payoff; stakes alone is grounds to revise: revise, or name the "
                     "reject ground")
    # AUTO-157: the form is a ground to revise only (the stakes line above
    # already covers a reject on stakes and form), and form_fits false
    # tells the second plan what to change only through form_problem.
    if (verdict == "reject" and review.get("form_fits") is False and clean
            and review.get("stakes_concrete") is not False):
        found.append("a reject needs the train, the order, an idle section or the "
                     "payoff; the form alone is grounds to revise: revise, or name the "
                     "reject ground")
    if review.get("form_fits") is False and not _text(review.get("form_problem")):
        found.append("form_fits is false and form_problem is empty: say what in the form "
                     "does not follow from the subject, or set form_fits true")
    # AUTO-137: the repeats of earlier channel films, only in a review that
    # was asked for them. A repeat is a ground to revise, so an accept that
    # lists one contradicts it; each names a film the prompt listed and is
    # mirrored in problems (section 0, the film named, a fix), so a revise
    # carrying only repeats still tells the second plan what to change.
    repeats = [r for r in review.get("repeats") or [] if isinstance(r, dict)]
    if verdict == "accept" and repeats:
        found.append("an accept sends nothing back, and this one lists repeats of earlier "
                     "films: change the verdict to revise, or empty the repeats")
    # A repeat sends a plan back to be planned again, never out of the
    # pipeline: a reject whose fields find the story sound and whose every
    # problem is the section 0 mirror of a listed repeat rejects on
    # sameness alone, which the role and the attack both forbid.
    films = [str(r.get("film") or "").strip() for r in repeats]
    story_failing = (review.get("train_ok") is False or review.get("reorder_survives") is True
                     or review.get("payoff_concrete") is False
                     or bool(review.get("sections_advancing_none")))
    if (verdict == "reject" and repeats and problems and not story_failing
            and all(isinstance(p, dict) and p.get("section") == 0
                    and any(_names(p.get("problem"), f) or _names(p.get("fix"), f)
                            for f in films)
                    for p in problems)):
        found.append("a repeat is a ground to revise, never to reject, and every problem "
                     "this reject names is a repeat: change the verdict to revise, or name "
                     "the story ground that rejects it")
    known = [str(s) for s in earlier_slugs]
    for r in repeats:
        film = str(r.get("film") or "").strip()
        if film not in known:
            found.append(f"a repeat names {film!r}, which is not one of the earlier films "
                         f"listed ({', '.join(known) or 'none'}): name a listed slug, or "
                         "drop it")
            continue
        if not any(isinstance(p, dict) and p.get("section") == 0
                   and str(p.get("fix") or "").strip()
                   and (_names(p.get("problem"), film) or _names(p.get("fix"), film))
                   for p in problems):
            found.append(f"the repeat of {film} has no matching entry in problems: add a "
                         f"section 0 problem naming {film}, with its fix")
    return found


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def form_only_revise(review: Any) -> bool:
    """Whether a plan review is a revise on the form alone (AUTO-157):
    form_fits false and every other ground its fields hold clean (the
    train, the order, no idle section, no unanswered question, a concrete
    payoff, stakes and, with earlier channel films, no repeat). The stage
    drafts on after a second such reading, since the form is a ground to
    revise only; any other second revise stops it as before. A field the
    reading leaves out is not clean."""
    if not isinstance(review, dict):
        return False
    return (review.get("verdict") == "revise" and review.get("form_fits") is False
            and review.get("train_ok") is True and review.get("reorder_survives") is False
            and review.get("sections_advancing_none") == []
            and review.get("questions_unanswered") == []
            and not review.get("repeats")
            and review.get("payoff_concrete") is True
            and review.get("stakes_concrete") is True)


def _shot_of(flag: Mapping) -> int | None:
    """A flag's shot number, or None when it is not a number (the stage
    names that broken answer itself)."""
    try:
        return int(flag.get("n", 0) or 0)
    except (TypeError, ValueError):
        return None


def check_review(review: Any, drafted: bool | None = None) -> list[str]:
    """What a review says that its own rubric forbids, one line per problem.

    The harness checks the schema (fields, types, enums) and asks once
    more; these are the shapes a schema cannot refuse: a recommend that
    carries a Poor or a blocker, a consider with nothing to fix, and a flag
    on a criterion the rubric does not have (the harness lets an enum value
    through after its retry), a voice flag on shot 0 of a drafted script
    (the role ties voice to a named line), and a first minute that heard
    no trigger (`none`) under a hook graded Excellent or Good, which the hook's own Fair
    describes; `not_heard` is no contradiction, since a review of the plan
    alone grades the hook from the plan. `drafted` False is a review of the
    plan before any line exists (a calibration case, AUTO-97), where shot 0
    is the only place a flag can go; None reads it from the review, as
    drafted unless its first minute is `not_heard`. The caller asks again
    once under its own name (`<name>-again`), with these lines in the
    prompt, and records a second failure; nothing here replaces the
    verdict."""
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
    first = review.get("first_minute") if isinstance(review.get("first_minute"), dict) else {}
    if drafted is None:
        drafted = first.get("trigger") != "not_heard"
    # AUTO-138: a voice flag is a line's. On shot 0 it would leave a whole
    # section or the plan for the writer to guess at, and send a register
    # fault to a redraft or to planning again (REPLAN_FIX). A plan with no
    # line yet has nowhere else to put it, so asking again there would only
    # spend a call and lose the sample.
    unplaced = [f for f in flags if isinstance(f, dict) and f.get("criterion") == "voice"
                and _shot_of(f) == 0] if drafted else []
    if unplaced:
        found.append(f"a voice flag names the shot whose line reads as a lecture, and "
                     f"{len(unplaced)} {'is' if len(unplaced) == 1 else 'are'} on shot 0: "
                     "name the shot and quote its words")
    if first.get("trigger") == "none" and grades.get("hook") in ("Excellent", "Good"):
        found.append(f"a first minute whose three opening sentences pull no trigger "
                     f"(trigger none) is a Fair hook at best, and this one grades the hook "
                     f"{grades['hook']}: change the grade or the trigger")
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
# does not (direction 4's narrated mechanism no shot depicts), a list
# the pictures present with nothing to tell its parts apart (attention 22),
# and a change over time a shot says it demonstrates while its picture is
# one fixed state (direction 7's discrete steps, direction 9's worked
# example or simulation before the rule; AUTO-144): a mechanism drawn but
# frozen, which the unshown claim, a mechanism not drawn at all, misses.
# Two more since AUTO-145: a formula put on screen before anything showed
# what it relates, or that the beat does not need (direction 9: the rule
# comes after the viewer has seen it happen), and a size, arrangement,
# path, interior or geometry the picture gives only as words or a flat
# label. Its scale part is attention 15's; arrangement, path and interior
# are a house rule with no corpus evidence, so no other rule is cited
# (direction 15 is a cutting rule, not a spatial one).
# What a count can find, a super that prints the line, is
# `rule:wall-of-text` in script.RULES (direction 2); motion is measured from
# frames at vfx and qa, never here.

#: The corpus rules the picture-check reads by, in the corpus's spelling.
PICTURE_RULES = ("direction 4", "direction 2", "attention 15", "attention 22",
                 "attention 23", "direction 7", "direction 9")


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
        "Then report the shots that show one of six faults, and only those: a figure of "
        "1,000 or more put on screen or asked of the picture with no comparison at human "
        "scale and no picture of the quantity (a bare number); a mechanism or claim the "
        "shot says its picture demonstrates that the picture as described does not show "
        "(an unshown claim); a list or set of parts the pictures present with no count "
        "and no label telling the viewer which part is which, or which one is being "
        "spoken of (an unsignposted list); a change over time the shot says its "
        "picture demonstrates (a sequence of states, a flow, growth, a cascade, a cause "
        "acting on something) while the picture as described is one fixed state, with no "
        "steps, change or build the viewer watches (a static process); a formula or "
        "equation the shot puts on screen when no earlier shot of its section built it "
        "first from a worked case or a picture of the quantities it relates, or when the "
        "section's beat does not need it (an unneeded formula); and a size, arrangement, "
        "path, interior or geometry the shot asks the viewer to grasp while the picture "
        "gives it only as words or a flat label, with no comparison at human scale, no "
        "cutaway or cross-section and no camera move through it (a flat spatial idea). "
        "When the mechanism "
        "is drawn but shown in one state, report static_process and leave unshown_claim "
        "empty; unshown_claim is for a mechanism the picture does not show at all.",
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
                        "picture does not show at all; a mechanism drawn but shown in "
                        "one state is static_process, and this is left empty; empty "
                        "when none")},
                    "unsignposted_list": {"type": "string", "description": (
                        "the list or set of parts shown with no count and no label for "
                        "which is which; empty when none")},
                    "static_process": {"type": "string", "description": (
                        "a change over time that the shot's `shows` says its picture "
                        "demonstrates (a sequence of states, a flow, growth, a cascade, a "
                        "cause acting on something), while `visual` describes one fixed "
                        "state with no steps, change or build the viewer watches; when "
                        "the mechanism is drawn but shown in one state it is reported "
                        "here and unshown_claim is left empty; empty when `shows` asks "
                        "for no change")},
                    "unneeded_formula": {"type": "string", "description": (
                        "the formula or equation the shot puts on screen (in `visual`, "
                        "`shows` or `on_screen_text`) when no earlier shot of its section "
                        "builds it intuitively first (a worked case, or a picture or "
                        "simulation of the quantities it relates), or when the section's "
                        "beat does not need it; empty when none")},
                    "flat_spatial": {"type": "string", "description": (
                        "the size, arrangement, path, interior or geometry the shot asks "
                        "the viewer to grasp while the picture gives it only as words or "
                        "a flat label, with no comparison at human scale, no cutaway or "
                        "cross-section and no camera move through it; empty when none")},
                },
                "required": ["n", "bare_number", "unshown_claim", "unsignposted_list",
                             "static_process", "unneeded_formula", "flat_spatial"],
            },
        },
    },
    "required": ["sections", "reports"],
}


# --------------------------------------------------------------------------- #
# calibration (AUTO-97)
# --------------------------------------------------------------------------- #
#
# The reviewer is checked before it is trusted as a gate. judging 9: a model
# judge disagrees with a person about one script in four and favours the
# longer of two stories, so it is calibrated on human-labelled scripts of the
# channel's own and audited for length bias before it gates anything.
# judging 10: known-bad scripts it must fail are what make leniency
# observable. tests/fixtures/narrative/ is that floor: one case per seeded
# fault, each `expected.json` naming the criterion the fault is graded on
# and the best verdict a correct reviewer may give (an aside that serves no
# claim, aside-joke, is graded on voice), a good plan and script, and a
# second good script whose one joke makes the point (claim-joke), which
# must not be graded down for it: its `at_least` holds voice to Good or
# better, since a recommend may carry a Fair (AUTO-159). The good script and
# the recap ending each have a padded copy for
# the length audit: the good pair may be read at the ceiling, where the
# longer copy cannot win, and the consider-level one leaves it room. Two
# faults are not the story review's: a premise the narration never voices
# and terms a lay viewer is never given have no rubric criterion, judging
# 14 puts them in the cold read, and the stage files the cold read's
# mismatch without touching the verdict. Their cases say `"story": false`
# and are judged on the cold read alone. The fixtures are not the
# channel's scripts: the twenty a person labels in work/review-log.jsonl
# are the set, these are what any reviewer must clear first.
#
# `python -m ytstudio.narrative calibrate <dir>` reads every case with the
# script stage's own readers (the plan review, the three samples, the cold
# read) and prints what each said against what it should. Nothing is
# revised, nothing is appended to review-log.jsonl, and every verdict is the
# model's: `judge_case` only compares it with the case's expectation.

#: How many story-review verdicts in work/review-log.jsonl a person must have
#: labelled before `yt script --gate` is turned on (judging 9: a set of at
#: least 20 of the channel's own scripts).
GATE_LABELLED = 20

#: The length audit fails when the padded script wins more than this share
#: of its pairs (judging 9).
LENGTH_BIAS_LIMIT = 0.6

#: What a live calibration run is expected to cost a case at the reviewer's
#: model: the three samples, the cold read and, for a plan case, the plan
#: review. An estimate for the operator, never a cap.
CALIBRATE_USD_PER_CASE = 1.0

#: What `calibrate` spends at most unless told otherwise.
CALIBRATE_BUDGET_USD = 20.0


def read_review_log(path: Any) -> list[dict]:
    """The rows of a review log (work/review-log.jsonl), in order. A missing
    file is no rows; a line that is not a JSON object is skipped, since a
    person edits this file by hand to label it."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


#: Coverage's word for a reject (rules 10 and 12 of judging-narrative.md):
#: a person labelling in the corpus's words writes Pass.
COVERAGE_PASS = "pass"


def label_of(row: Any) -> str | None:
    """The verdict a person labelled a review-log row with, or None. Case
    and spaces do not matter, and coverage's Pass is read as reject. A null
    label is a row not yet read; any other word is no verdict the
    reviewer's own can be compared with."""
    label = row.get("label") if isinstance(row, dict) else None
    if not isinstance(label, str):
        return None
    label = label.strip().lower()
    label = "reject" if label == COVERAGE_PASS else label
    return label if label in VERDICTS else None


def labelled(rows: Iterable[Any]) -> int:
    """How many rows a person has labelled with a verdict (label_of)."""
    return sum(1 for r in rows if label_of(r))


def agreement(rows: Iterable[Any]) -> tuple[int, int]:
    """How many labelled rows carry the label the reviewer's own verdict
    matches, out of how many are labelled. judging 9 asks for the
    agreement to be reported; it sets no bar for it, so neither does this."""
    rows = [r for r in rows if label_of(r)]
    return sum(1 for r in rows if r.get("verdict") == label_of(r)), len(rows)


def unreadable_labels(rows: Iterable[Any]) -> int:
    """Rows a person labelled with a word that is not a verdict: counted so
    a label is never dropped without a word."""
    return sum(1 for r in rows if isinstance(r, dict) and r.get("label") not in (None, "")
               and not label_of(r))


def story_judged(expected: Mapping) -> bool:
    """Whether a case holds the story review to anything. A case whose fault
    only the cold read can catch says `"story": false`: the review is read
    and shown, and a reviewer is never failed for following a rubric that
    has no criterion for the fault."""
    return expected.get("story", True) is not False


def _term_named(names: Any, listed: Iterable[str]) -> bool:
    """Whether a reader's list of unexplained terms names a seeded term. The
    term is one name or the list of names a reader may give it ("IMF" for
    the interplanetary magnetic field); one of them inside one listed entry
    is enough, so a reader is not failed for its own words."""
    names = [names] if isinstance(names, str) else list(names or [])
    return any(str(n).lower() in t for n in names for t in listed)


def judge_case(expected: Mapping, got: Mapping) -> list[str]:
    """What the reviewer got wrong on one calibration case, one line each;
    empty when it caught what the case seeds.

    `expected` is the case's expected.json; `got` holds the story review's
    median `verdict`, `grades` and merged `flags`, the round's heard
    `trigger`, the plan review's verdict (`plan_review`) and its
    `stakes_concrete` (`plan_stakes_concrete`), and the cold read
    (`cold_read`). A seeded case fails the
    reviewer when the verdict is better than the case allows, the named
    criterion is graded above Fair, or no flag names it (judging 10 and the
    case's `judging_10`); a good case when it is not recommended; a case
    with `"story": false` holds the story review to nothing. A case naming
    `at_least` (criterion -> level, AUTO-159) also fails a reviewer that
    grades that criterion below the level, or not at all, or flags it: the
    good script whose one joke makes the point must not be marked down for
    it, which its verdict alone cannot show, since a recommend may carry a
    Fair; a level that is no LEVELS label fails the case. The failure line
    names the floor and adds the case's `at_least_why` when it sets one, so
    the humour reason is the humour case's and a floor on another criterion
    never prints it. The floor is read only when the story review gave a
    verdict: a reading without one already fails on that alone. A case
    without `at_least` is judged as before. A plan
    case's plan review must send a bad plan back and accept the good one,
    and a plan review that finds stakes in a plan whose case says it has
    none (`stakes_concrete` false, AUTO-161) fails it; a
    cold-read case's reader must find the mismatch or the terms it hides. A
    case naming a `trigger` (AUTO-136) fails a round that heard another, or
    none it could agree on. A reading that gave no verdict (`not_evidenced`,
    a failed call) is a failure, never a pass."""
    failed: list[str] = []
    rank = {v: i for i, v in enumerate(VERDICTS)}
    want, verdict = expected.get("verdict"), got.get("verdict")
    key = expected.get("criterion")
    if not story_judged(expected):
        pass                    # the cold read below is all this case judges
    elif verdict not in rank:
        failed.append(f"the story review gave no verdict ({verdict or 'none'}): a reading "
                      "that cannot judge is never a pass")
    elif key is None:
        if verdict != want:
            failed.append(f"the story review said {verdict}; the good case is {want}")
    else:
        if want in rank and rank[verdict] > rank[want]:
            failed.append(f"the story review said {verdict}; the best this case allows "
                          f"is {want}")
        grade = (got.get("grades") or {}).get(key)
        if grade not in ("Fair", "Poor"):
            failed.append(f"{key} graded {grade or 'nothing'}; the seeded fault is graded "
                          "Fair or Poor")
        if not any(isinstance(f, dict) and f.get("criterion") == key
                   for f in got.get("flags") or []):
            failed.append(f"no flag on {key}, where the fault is")
    if story_judged(expected) and verdict in rank:
        # AUTO-159: check_review lets a recommend carry a Fair, so a good
        # case judged on its verdict alone would pass a reviewer that grades
        # down a joke that makes the point; the case's floor catches it
        level = {v: i for i, v in enumerate(LEVELS)}           # best first
        flags = [f for f in got.get("flags") or [] if isinstance(f, dict)]
        for floor_key, floor in (expected.get("at_least") or {}).items():
            grade = (got.get("grades") or {}).get(floor_key)
            if floor not in level:
                # a typo would otherwise pass every grade; fail the row closed
                failed.append(f"{floor_key}: at_least names no level ({floor})")
            elif grade not in level or level[grade] > level[floor]:
                why = expected.get("at_least_why")
                failed.append(f"{floor_key} graded {grade or 'nothing'}; this case holds "
                              f"{floor_key} no lower than {floor}"
                              + (f": {why}" if why else ""))
            if any(f.get("criterion") == floor_key for f in flags):
                failed.append(f"a flag on {floor_key}, where this case has nothing to fix")
    trigger = expected.get("trigger")
    if trigger and got.get("trigger") != trigger:
        failed.append(f"the story review heard the opening's trigger as "
                      f"{got.get('trigger') or 'nothing'}; this opening's is {trigger}")
    plan_want = (expected.get("plan_review") or {}).get("verdict")
    if plan_want:
        plan_got = got.get("plan_review")
        if plan_got not in PLAN_VERDICTS:
            failed.append(f"the plan review gave no verdict ({plan_got or 'none'})")
        elif (plan_want == "accept") != (plan_got == "accept"):
            failed.append(f"the plan review said {plan_got}; this plan is to be "
                          + ("accepted" if plan_want == "accept"
                             else "sent back (revise or reject)"))
        # AUTO-161: sending a plan with no stakes back on other grounds is not
        # finding its fault; only a reading that says it has none is missing
        if ((expected.get("plan_review") or {}).get("stakes_concrete") is False
                and got.get("plan_stakes_concrete") is True):
            failed.append("the plan review found stakes in a plan that has none")
    cold_want = expected.get("cold_read") or {}
    if cold_want:
        cold = got.get("cold_read") or {}
        if cold_want.get("match") and cold.get("match") != cold_want["match"]:
            failed.append(f"the cold read was {cold.get('match') or 'not matched'} against "
                          f"the train; this case is a {cold_want['match']}")
        listed = [str(t).lower() for t in cold.get("unexplained_terms") or []]
        missing = [t if isinstance(t, str) else t[0] for t in cold_want.get("terms") or []
                   if not _term_named(t, listed)]
        if missing:
            failed.append(f"the cold read did not list {', '.join(missing)} among the "
                          "unexplained terms")
    return failed


def pair_winner(shorter: Mapping, longer: Mapping) -> str:
    """Which of two readings of the same content at two lengths came out
    ahead: `longer`, `shorter` or `tie`. The verdict decides; on the same
    verdict, whether more criteria were graded a level higher in one than in
    the other. Labels are compared as labels, never summed into a score."""
    rank = {v: i for i, v in enumerate(VERDICTS)}
    a, b = rank.get(shorter.get("verdict"), -1), rank.get(longer.get("verdict"), -1)
    if a != b:
        return "longer" if b > a else "shorter"
    level = {v: i for i, v in enumerate(LEVELS)}           # best first
    ga, gb = shorter.get("grades") or {}, longer.get("grades") or {}
    both = [k for k in KEYS if ga.get(k) in level and gb.get(k) in level]
    up = sum(1 for k in both if level[gb[k]] < level[ga[k]])
    down = sum(1 for k in both if level[gb[k]] > level[ga[k]])
    return "longer" if up > down else "shorter" if down > up else "tie"


def at_ceiling(shorter: Mapping, longer: Mapping) -> bool:
    """Whether both readings of a pair are already at the top: recommend,
    every criterion Excellent. The longer copy has nothing left to win
    there, so such a tie says nothing about length bias."""
    def top(reading: Mapping) -> bool:
        grades = reading.get("grades") or {}
        return (reading.get("verdict") == VERDICTS[-1]
                and all(grades.get(k) == LEVELS[0] for k in KEYS))
    return top(shorter) and top(longer)


def length_audit(pairs: Iterable[tuple[Mapping, Mapping]]) -> dict:
    """judging 9's audit: each pair is one sample of a script read at its
    own length and the same sample of it padded. It fails when the padded
    one wins more than LENGTH_BIAS_LIMIT of the pairs. With no pair it has
    not run, and with every pair at the ceiling (at_ceiling) it could not
    have seen a bias; neither is a pass. The calibration set pads a
    consider-level case beside the good one so the longer copy has room
    to win."""
    pairs = list(pairs)
    winners = [pair_winner(short, long) for short, long in pairs]
    n, longer = len(winners), winners.count("longer")
    ceiling = sum(1 for short, long in pairs if at_ceiling(short, long))
    return {"pairs": n, "longer": longer, "shorter": winners.count("shorter"),
            "ties": winners.count("tie"), "ceiling": ceiling,
            "passed": bool(n) and ceiling < n and longer / n <= LENGTH_BIAS_LIMIT}


def calibration_cases(directory: Any) -> list[dict]:
    """Every case under `directory`: a folder with plan.json and
    expected.json, and script.json (and padded.json) when the case has a
    screenplay."""
    def read(path: Path):
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    out = []
    for path in sorted(Path(directory).iterdir()):
        if not (path / "expected.json").is_file() or not (path / "plan.json").is_file():
            continue
        out.append({"name": path.name, "plan": read(path / "plan.json"),
                    "script": read(path / "script.json"),
                    "padded": read(path / "padded.json"),
                    "expected": read(path / "expected.json")})
    return out


def calibrate(directory: Any, *, work: Any, model: str = "sonnet",
              reviewer_model: str = "opus", budget_usd: float | None = None,
              review_budget_usd: float | None = None, fresh: bool = False,
              verbose: bool = True) -> dict:
    """Read every calibration case with the script stage's own readers and
    compare each reading with what the case expects (`judge_case`).

    A case with a plan verdict in its expected.json gets the plan review
    (`plan-review`); every case gets the story review's three samples
    (`review-r1-s1..`), over its screenplay or, for a plan case, over the
    plan alone; a case with narration gets the cold read. A case with a
    padded.json is read again padded, in a work directory of its own, and
    each valid sample is paired with its padded twin for the length audit.
    Decisions are cached under `<work>/calibration/<case>/`, so a second
    run pays only for what changed. `model` is the writer's: the reviewer
    is refused in its family, as `yt script --review` refuses it, and
    `pool` or `sdk:<id>` is resolved as it resolves them (AUTO-125), so a
    pool reviewer is calibrated on the gateway engine exactly as it would
    review, and is refused before any case is read without it. Once
    `budget_usd` is spent no further case is read, and an unread case
    fails. Nothing is revised and nothing is appended to the review log:
    these are not the channel's scripts. The report is also written to
    `<work>/calibration/calibration.json`."""
    from . import flags, house, reviewers, script       # script imports this module
    from .core import Idea, write_json

    pooled = reviewers.resolve(model, reviewer_model)
    if pooled is not None:
        reviewer_model = pooled.model
    elif own := flags.own_reviewer(model, reviewer_model):
        raise ValueError(own)
    work = Path(work)
    rules = house.load(work)
    block, digest = rules.for_stage("script"), rules.digest("script")
    cases = calibration_cases(directory)
    fallback = next((c["script"]["idea"] for c in cases
                     if c["script"] and isinstance(c["script"].get("idea"), dict)), None)
    writers: list = []

    def reader(case: dict, name: str):
        plan = case["plan"]
        seconds = sum(int(s.get("seconds", 0) or 0) for s in plan.get("sections") or [])
        raw = (case["script"] or {}).get("idea") or fallback or {
            "title": plan.get("promise") or case["name"], "genre": "",
            "description": plan.get("logline") or ""}
        w = script.Screenwriter(
            Idea.from_raw(raw), model=model, minutes=max(seconds, 60) / 60,
            reviewer_model=reviewer_model, reviewer=pooled,
            review_budget_usd=review_budget_usd, house=block, house_digest=digest,
            fresh=fresh, verbose=verbose, root=work / "calibration" / name, review_log=None)
        writers.append(w)
        return w

    def spent() -> bool:
        return budget_usd is not None and sum(w.usage.cost_usd for w in writers) >= budget_usd

    rows, pairs = [], []
    for case in cases:
        expected = case["expected"]
        row = {"name": case["name"], "criterion": expected.get("criterion"),
               "story": story_judged(expected),
               "expected": expected.get("verdict"), "got": None, "plan_review": None,
               "cold_read": None, "ran": False, "passed": False, "failures": []}
        rows.append(row)
        if spent():
            row["failures"] = [f"not read: the budget of ${budget_usd:g} was spent"]
            continue
        row["ran"] = True
        w, plan, got = reader(case, case["name"]), case["plan"], {}
        if (expected.get("plan_review") or {}).get("verdict"):
            try:
                review, _, _ = w.review_plan(plan)
                got["plan_review"] = row["plan_review"] = review.get("verdict")
                got["plan_stakes_concrete"] = review.get("stakes_concrete")
            except script.ClaudeError as exc:
                row["failures"].append(f"the plan review failed ({exc})")
        sections = script.sections_of(case["script"]) if case["script"] else []
        shots = w._renumber(sections)
        rnd = w.review_round(sections, plan, 1)
        got.update(verdict=rnd["verdict"], grades=rnd["grades"], flags=rnd["flags"],
                   trigger=rnd.get("trigger"))
        row.update(got=rnd["verdict"], grades=rnd["grades"], trigger=rnd.get("trigger"),
                   samples=[{k: s.get(k) for k in ("name", "verdict", "valid", "failed")}
                            for s in rnd["samples"]])
        if any(s.narration for s in shots):
            cold = w.cold_read(shots, plan)
            got["cold_read"] = row["cold_read"] = {
                k: cold[k] for k in ("premise", "match", "difference",
                                     "unexplained_terms", "failed") if k in cold}
        row["failures"] += judge_case(expected, got)
        if case["padded"]:
            if spent():
                row["failures"].append(f"the padded copy was not read: the budget of "
                                       f"${budget_usd:g} was spent")
            else:
                p = reader(case, f"{case['name']}-padded")
                longer = script.sections_of(case["padded"])
                p._renumber(longer)
                padded = p.review_round(longer, plan, 1)
                pairs += [(a, b) for a, b in zip(rnd["samples"], padded["samples"])
                          if a.get("valid") and b.get("valid")]
                row["padded"] = padded["verdict"]
        row["passed"] = not row["failures"]
    audit = length_audit(pairs)
    log = read_review_log(work / script.REVIEW_LOG)
    agreed, count = agreement(log)
    report = {"directory": str(directory), "model": model, "reviewer_model": reviewer_model,
              **({"reviewer": pooled.record()} if pooled is not None else {}),
              "cases": rows, "audit": audit,
              "passed": all(r["passed"] for r in rows) and audit["passed"],
              "verdicts": len(log), "labelled": count, "agreed": agreed,
              "cost_usd": round(sum(w.usage.cost_usd for w in writers), 4)}
    (work / "calibration").mkdir(parents=True, exist_ok=True)
    write_json(work / "calibration" / "calibration.json", report)
    return report


def calibration_lines(report: Mapping) -> list[str]:
    """The calibration as the operator reads it: one row per case (fixture,
    criterion, expected, got, plan review, cold read, result) with what a
    failing row got wrong under it, the length audit, and whether --gate
    may be turned on."""
    rows = report.get("cases") or []
    width = max([len("fixture")] + [len(r["name"]) for r in rows]) + 2
    head = ("criterion", "expected", "got", "plan", "cold read")
    sizes = (14, 11, 15, 9, 11)
    out = ["fixture".ljust(width) + "".join(h.ljust(s) for h, s in zip(head, sizes))
           + "result"]
    for r in rows:
        cold = r.get("cold_read") or {}
        # a case judged on the cold read alone says so where the criterion is
        criterion = r.get("criterion") or ("-" if r.get("story", True) else "cold read")
        cells = (criterion, r.get("expected") or "-", r.get("got") or "-",
                 r.get("plan_review") or "-",
                 cold.get("match") or ("failed" if cold.get("failed") else "-"))
        result = "pass" if r.get("passed") else "fail" if r.get("ran") else "not run"
        out.append(r["name"].ljust(width)
                   + "".join(str(c).ljust(s) for c, s in zip(cells, sizes)) + result)
        out += [f"    - {line}" for line in r.get("failures") or []]
    audit = report.get("audit") or {}
    limit = f"{LENGTH_BIAS_LIMIT:.0%}"
    ceiling = int(audit.get("ceiling") or 0)
    if audit.get("pairs") and ceiling >= audit["pairs"]:
        out.append(f"length audit: fail: all {audit['pairs']} pairs were read at the ceiling "
                   "(recommend, every grade Excellent), where the padded script has nothing "
                   "to win, so the audit could not see a bias")
    elif audit.get("pairs"):
        out.append(f"length audit: {'pass' if audit.get('passed') else 'fail'}: the padded "
                   f"script won {audit['longer']} of {audit['pairs']} pairs, "
                   f"{audit.get('ties', 0)} tied, {ceiling} at the ceiling where it could "
                   f"not win (it fails above {limit})")
    else:
        out.append(f"length audit: fail: no pair was read (it fails above {limit}; a "
                   "padded.json beside a case's script is needed)")
    good = [r for r in rows if r.get("story", True) and not r.get("criterion")]
    seeded = [r for r in rows if r not in good]
    out.append(f"seeded-bad caught: {sum(1 for r in seeded if r.get('passed'))} of "
               f"{len(seeded)}; good recommended: {sum(1 for r in good if r.get('passed'))} "
               f"of {len(good)}; spent ${float(report.get('cost_usd') or 0):.2f}")
    count = int(report.get("labelled") or 0)
    # judging 9 asks for the agreement with the labels to be reported; a
    # count of labels alone is not a calibration against them
    agreed = (f"; the reviewer agreed with {int(report.get('agreed') or 0)} of {count} "
              "labels" if count else "")
    why = []
    if not report.get("passed"):
        why.append("the reviewer did not pass this calibration")
    if count < GATE_LABELLED:
        why.append(f"{count} labelled verdicts in work/review-log.jsonl, "
                   f"{GATE_LABELLED} needed")
    out.append(("--gate: not yet: " + "; ".join(why) if why else
                "--gate may be turned on: the seeded-bad fixtures all fail, the good ones "
                f"pass, the length audit passes and {count} labelled verdicts are in "
                "work/review-log.jsonl") + agreed)
    return out


def main(argv: list[str] | None = None) -> int:
    """`python -m ytstudio.narrative calibrate <dir>`: the calibration run.
    A module entry point, not a `yt` stage: it reads fixtures, not a film.
    Exits 0 when the reviewer passed every case and the length audit, 1
    when it did not, 2 on a refused argument."""
    import argparse
    from . import flags
    from .vfx import REVIEW_USD, REVIEWER_MODEL

    parser = argparse.ArgumentParser(
        prog="python -m ytstudio.narrative",
        description="Check the story reviewer against the calibration set before "
                    "`yt script --gate` trusts it (judging 9 and 10).")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("calibrate", help="read every calibration case and print the table")
    p.add_argument("directory", help="the cases, e.g. tests/fixtures/narrative")
    p.add_argument("--model", default="sonnet",
                   help="the writer's model, which the reviewer may not share (default sonnet)")
    flags.reviewer_model(p, default=REVIEWER_MODEL,
                         help=f"the model under calibration (default {REVIEWER_MODEL})")
    flags.review_budget(p, default=REVIEW_USD,
                        help=f"what one review may cost (default {REVIEW_USD:g})")
    flags.budget(p, default=CALIBRATE_BUDGET_USD,
                 help=f"read no further case once this much is spent (default "
                      f"{CALIBRATE_BUDGET_USD:g}; about ${CALIBRATE_USD_PER_CASE:g} a case)")
    p.add_argument("--work", default="work",
                   help="where the readings are cached (<work>/calibration/) and "
                        "review-log.jsonl is counted (default work)")
    flags.fresh(p, help="ask every reading again instead of reusing the ones on disk")
    p.add_argument("--quiet", action="store_true", help="print the table only")
    args = parser.parse_args(argv)
    if not Path(args.directory).is_dir():
        print(f"error: no calibration cases at {args.directory}", file=sys.stderr)
        return 2
    try:
        report = calibrate(args.directory, work=args.work, model=args.model,
                           reviewer_model=args.reviewer_model, budget_usd=args.budget_usd,
                           review_budget_usd=args.review_budget_usd, fresh=args.fresh,
                           verbose=not args.quiet)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("\n".join(calibration_lines(report)))
    if not args.quiet:
        print(f"wrote {Path(args.work) / 'calibration' / 'calibration.json'}",
              file=sys.stderr)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    # Run as a program this file is `__main__`; the script stage imports it
    # as ytstudio.narrative. The entry point is that one's, so one copy of
    # the module answers both.
    from ytstudio.narrative import main as _main
    sys.exit(_main())
