# Narrative craft corpus

What a yt-auto film must do to hold and reward a viewer, written as rules a
writer can follow, a reviewer can check and a pipeline stage can enforce.
Four files, one index.

## The files

| File | Covers | Rules | Feeds stages |
|---|---|---|---|
| `structure-and-hooks.md` | spine and plan (BUT/THEREFORE, ABT, cause-and-effect), promise and payoff, the first 30 s, sections and re-hooks, the ending, series format, reading the retention curve back into the script | 40 | ideas, research, styles, script, voice, retime, keyframe, vfx, sound, build, qa, improve |
| `attention-and-stakes.md` | curiosity gap (Loewenstein, Kang, Gruber), paying the gap, misconception evidence (Muller), stakes (Hitchcock, Pixar, identifiable victim, compassion fade, transportation), honest stakes, cognitive load (Mayer, Sweller), measuring attention | 28 | ideas, research, script, voice, retime, keyframe, vfx, sound, build, qa, improve |
| `direction-and-narration.md` | binding picture to words, what is in the frame, motion and transitions, cuts, writing for the ear, delivery (wpm, pauses, emphasis, read-aloud) | 29 | styles, script, voice, retime, keyframe, vfx, sound, build, qa |
| `judging-narrative.md` | YouTube's retention vocabulary and ranking objective, running a model as story judge (absolute scoring, order swap, k ≥ 3, different family), the coverage-shaped rubric, the plan "train", cold-reader and read-aloud pre-voice checks | 15 | script, voice, qa, improve |

Each file has the same shape: numbered rules (bold statement, evidence with
inline citations and at least one URL, a `Check:` line naming what a
reviewer or a lint can test, a `Stages:` line), then **Contested** (claims
that circulate as settled and are not), **Folklore and open questions**
(rules that could not be sourced to a page that was read, with what would
settle them) and **Sources** grouped by kind.

A rule's `Stages:` line names the `yt` stages it bears on (ideas, research,
styles, script, voice, retime, keyframe, vfx, sound, build, qa, improve)
and, where its check asks the screenplay for something, the field as
`plan.<field>` or `shot.<field>`. A field named there is what the check
needs, not a promise that the schema has it today. The "Feeds stages"
column above is the union of a file's `Stages:` lines, and
`tests/test_doc_narrative.py` keeps the two equal.

Shared rules have one home file; the others cross-reference it (each file
says so in a "Shared rules" paragraph under its intro). Homes: 30-second
Intro metric and retention vocabulary → judging 1–3; curiosity gap →
attention 1–3; misconception evidence → attention 7, its position in the
film → structure 15; concrete before abstract → structure 21 (section),
direction 9 and 20 (frame, sentence); on-screen text and redundancy →
direction 2; words per minute → direction 25; read-aloud gate → direction
29, with judging 15 bounding it; promise/payoff → structure 6–8, train →
judging 13; Zeigarnik → attention Contested.

## How the corpus is used

1. **Read by maintainers.** It is the reference when a prompt, a lint or a
   reviewer rubric is changed: the rule says what to enforce, the evidence
   says how hard to enforce it, the Contested and Folklore sections say what
   not to promise.
2. **Distilled into `ytstudio/house/default.md`.** By hand, one section per
   reader (`## all`, `## script`, `## styles`, `## vfx`, `## sound`,
   `## voice`, `## format`), holding only the rule statements and `Check:`
   lines whose `Stages:` tag names that reader, at the strength stated
   here, with no citations and no numbers that the corpus marks
   snippet-sourced or unverified. Each section opens with a
   `<!-- rules: ... -->` comment naming the rules it states;
   `tests/test_house_default.py` holds those ids to this corpus (each
   exists, and once a file is tagged, its `Stages:` tag names the reader).
   `house.py` hands each stage `## all` and its own section. The channel's
   `work/house.md` and a project's `house.md` replace sections by name.
   There is no generator: when a rule changes, the section that cites it is
   edited, and the test catches a citation that drifts.
3. **The narrative rubric.** `ytstudio/narrative.py` holds it, in the
   shape set by `judging-narrative.md` rules 7–13: `RUBRIC`, ten criteria
   (train, hook, stakes, open loops, reveal setup, tension, ending, honesty,
   voice, devices) drawn
   from the `Check:` lines, each with four labelled levels (Excellent, Good,
   Fair, Poor) of one line, a worked example, the corpus rules it cites and
   the strength of its lead rule from the table below; the review's schema
   (a logline and a synopsis before the grades, a grade per criterion, a
   verdict reject / consider / recommend, every problem a flag tied to a
   section and a shot); the plan review's schema; the level order permuted
   per sample; and `check_review`, which names a recommend carrying a Poor,
   a consider with no flag, a flag on no criterion and an opening heard to
   pull no curiosity trigger under a hook graded Good or better. `reject` is
   coverage's Pass, renamed because doctor, improve and the operator read
   verdict strings. The hook's window is written in words, the house's to
   set. One tuple is to be rendered into the writer's prompt and the
   reviewer's role, as `framecheck.CHECKLIST` is. `tests/test_narrative_rubric.py` holds every cited
   rule id to this corpus, and `tests/fixtures/narrative/` is the floor of
   the calibration set (judging 10): plans and scripts seeded with one known
   fault each (a list-shaped plan, a topic-only train, an empty payoff, an
   AND chain, a reveal before its constraint, a recap ending, a hook that
   enumerates its conclusions, a middle whose stakes stay flat, an opening
   with no curiosity trigger, a premise a cold reader could not restate,
   terms a lay audience is never given, the good film read as a lecture,
   an analogy that maps a comet's tail onto its path, an aside that serves
   no claim), the good pair, and a joke that makes the point: the good
   script with one joke whose punchline is the shot's claim, which expects
   recommend with voice no lower than Good (its `at_least`, AUTO-159),
   since a recommend may carry a Fair and its verdict alone would pass a
   reviewer that marks the joke down; the good
   script and the recap ending each have a padded copy. judging 10 asks
   the calibration run for reject on every seeded case; the cases whose
   one fault is a Fair, or a Poor on a criterion that does not decide a
   film (a hook that lists its conclusions, a recap ending, a reveal
   before its constraint, an AND chain, flat tension, an opening with no
   trigger, a lecture voice, an aside that serves no claim, a device that maps onto the wrong thing), are held to consider instead, because judging 11 and 12 make a fixable fault a
   consider, and a gate that rejects them would regenerate a film one
   flag would mend. Their `expected.json` says so in `judging_10`: the
   calibration fails a reviewer that recommends them, grades the named
   criterion above Fair or leaves it unflagged. A premise the narration
   never voices and unexplained terms have no rubric criterion: judging 14
   puts them among the pre-voice checks that are not story checks, so
   their cases say `"story": false` and are judged on the cold read alone
   (the mismatch; the terms, by any of the names a reader may give them).
   The stage now files the cold read's terms as `rule:unexplained-term`
   for `revise-ledger-2` on a lay plan (a warning on any other); the
   jargon case still says `"story": false`.
   The review that runs it (a
   different model family from the writer, majority of at least three
   runs, validate-retry-fail before voice is paid for) is the script
   stage's, wired by `yt script --review` (AUTO-93): three samples
   `review-r1-s1..s3`, each with the levels in its own order, the median
   of the valid ones (fewer than two is `not_evidenced`), a cold reader
   that hears the narration alone (judging 14), and every verdict appended
   to `work/review-log.jsonl` for the labels this calibration needs. Its
   reject stops voice only under `--gate`, and `--gate` may be turned on
   only when `python -m ytstudio.narrative calibrate tests/fixtures/narrative`
   (AUTO-97) shows the condition judging 9 and 10 set: the seeded-bad
   fixtures all fail, the good ones pass, the length audit passes (the
   padded copies, the same content at about twice the words, may not win
   more than 60% of their sample pairs, and an audit whose every pair was
   read at the ceiling, recommend with every grade Excellent, fails), and
   work/review-log.jsonl holds at least 20 rows with a human `label`. The
   fixtures are the floor, never the channel's scripts; the 20 labelled
   rows are the set, and the run reports how many of them carry the
   reviewer's own verdict. A live run reads the cases on the real models
   with the plan review where the case names a plan verdict, the three
   samples on every case, and the cold read where there is narration, at
   about $1 a case at opus, about $18 for the shipped set with its two
   padded reads, capped by `--budget-usd`; `yt
   doctor` warns `gate on with N labelled verdicts (20 needed)` for a
   script written under `--gate` below that count. The plan review before
   any section is drafted is wired too (`yt script --review`, AUTO-92).
4. **The evidence loop.** `yt improve` ingests the channel's retention curves
   (judging 1–3), maps dips, spikes and the Intro figure to beat ids and
   routes findings to the script stage under the rule they bear on. Every
   threshold in the corpus is a prior until this loop has logged enough
   films to replace it.

## Rules of evidence

- **Every rule names its source inline**: author, title, venue, year, URL
  and kind. Kinds, in descending weight: peer-reviewed (including
  meta-analyses), data study, platform documentation (YouTube Help and
  official blogs), practitioner (talks, essays, interviews, creator process),
  book (read via publisher page, review or notes unless stated), secondary
  or other (press, vendor, wiki, AI-assisted guides). A thesis or preprint
  is named as such.
- **A rule is stated at the strength its sources support after
  verification**, not the strength the folklore gives it. Where the
  verification pass weakened a claim, the corrected wording is in the rule
  and the original claim is in Contested or dropped.
- **House conventions are labelled.** Any threshold, window, remedy or
  check the sources stop short of (the 1 s lag tolerance, the 15 s subject
  window, "cut or re-order", the 90 s viewer-stake window, "finally" as an
  end signal) says "house" in the text. A house number is a default to tune
  from the channel's own data, never a sourced bound.
- **Quote from pages that were opened.** A number or phrase that came from
  an abstract, a search snippet, a secondary summary or a redirected URL
  says so next to the citation. Nothing is cited as if read when it was not;
  unreachable sources sit under Folklore with their HTTP status and what
  would settle them.
- **Outcome mismatch is stated.** Most peer-reviewed evidence measures
  learning, recall, donations, clicks or sharing in other media; the
  platform documentation defines what YouTube reports; the practitioner
  material is single-channel curves. No source read here links narrative
  structure to YouTube retention curves. Each rule says which outcome its
  evidence measured.
- **Duplicates are consolidated, not repeated.** A rule that belongs to two
  dimensions is stated once and referenced from the other file.
- **Citations are checked on doubt.** URLs, dates, page numbers, author
  lists and source kinds that looked mismatched were re-fetched or checked
  against the extracted texts in the research scratchpad; corrections are
  in the text (Wistia post dates, Lawrence timestamp, Loewenstein pages,
  arXiv titles and authors, the above-typical-intro page).

## What the corpus does not yet cover

Craft areas a top-tier factual film depends on with no rule or only a
passing mention: music and sound design beyond Fong's cue-ending note and
pause handling; title and thumbnail craft beyond concreteness and
freeze-first; the evidence for humour and tone as retention devices,
still unread, although humour's place is now stated under attention 21
(allowed when it serves a claim, never required); sponsor-segment
placement and disclosure; chapters and description metadata; captions and
accessibility (only as a Contested entry); the research and fact-checking
process itself (Kurzgesagt, Veritasium and Vox process material not read);
analogy and metaphor selection for mechanisms; colour, typography and
legibility beyond subtitle speed; narrator persona and tone across a
series; topic selection and runtime choice at the ideas stage; the
comments-and-community loop; vertical and short-form variants. Primary
creator statements (Kurzgesagt, Tom Scott, Wendover, Real Engineering, Cleo
Abram, Johnny Harris) remain unread; BBC Editorial Guidelines and Ofcom
2.2 are the missing regulatory anchor for honest stakes; the YouTube
Analytics API's coverage of key moments is unchecked.

## Strength of each rule

One word per rule, so a stage, a lint or a rubric reads how hard to enforce
a rule from here instead of deciding it again. The word is the kind of the
strongest source that supports the rule *as stated*, not merely its topic:

- **peer-reviewed**: a peer-reviewed study, meta-analysis or review tests
  what the rule prescribes, on whatever outcome it measured (learning,
  recall, donations, clicks, ratings), which the rule names. No such study
  measured YouTube retention, so every row with this word carries that
  transfer: the word vouches for the outcome the rule's text names, not for
  watch time. A peer-reviewed source that only describes what texts do,
  that does not test the device or placement the rule prescribes, or that
  this pass could not verify does not lift a rule to this word; the rule
  takes the strength of what remains, and its text says which part that is.
- **platform**: YouTube's own documentation (Help pages, official blogs,
  the creator playbook) or a delivery specification is the strongest
  support.
- **practitioner**: named craft sources carry it: creators, editors, books,
  vendor data, press, a preprint or a thesis. Taste with a name on it.
- **house**: the sources stop short of the rule, or failed to replicate,
  and it is kept as this pipeline's default; its text says so. A house
  rule is a prior to tune from the channel's own curves, never a gate on
  its own.

A house number inside a stronger rule (a window, a threshold) is still
house; the rule's text labels it. Every word here is a prior until the
evidence loop (`yt improve` fed by the channel's retention) replaces it.

| Rule | Strength |
|---|---|
| structure 1 | practitioner |
| structure 2 | practitioner |
| structure 3 | peer-reviewed |
| structure 4 | practitioner |
| structure 5 | practitioner |
| structure 6 | platform |
| structure 7 | practitioner |
| structure 8 | practitioner |
| structure 9 | platform |
| structure 10 | platform |
| structure 11 | platform |
| structure 12 | practitioner |
| structure 13 | platform |
| structure 14 | peer-reviewed |
| structure 15 | peer-reviewed |
| structure 16 | practitioner |
| structure 17 | practitioner |
| structure 18 | practitioner |
| structure 19 | practitioner |
| structure 20 | practitioner |
| structure 21 | practitioner |
| structure 22 | practitioner |
| structure 23 | house |
| structure 24 | peer-reviewed |
| structure 25 | house |
| structure 26 | practitioner |
| structure 27 | practitioner |
| structure 28 | practitioner |
| structure 29 | practitioner |
| structure 30 | peer-reviewed |
| structure 31 | peer-reviewed |
| structure 32 | peer-reviewed |
| structure 33 | practitioner |
| structure 34 | practitioner |
| structure 35 | platform |
| structure 36 | platform |
| structure 37 | platform |
| structure 38 | platform |
| structure 39 | practitioner |
| structure 40 | platform |
| attention 1 | peer-reviewed |
| attention 2 | house |
| attention 3 | peer-reviewed |
| attention 4 | peer-reviewed |
| attention 5 | peer-reviewed |
| attention 6 | peer-reviewed |
| attention 7 | peer-reviewed |
| attention 8 | practitioner |
| attention 9 | practitioner |
| attention 10 | practitioner |
| attention 11 | practitioner |
| attention 12 | house |
| attention 13 | peer-reviewed |
| attention 14 | house |
| attention 15 | peer-reviewed |
| attention 16 | peer-reviewed |
| attention 17 | peer-reviewed |
| attention 18 | practitioner |
| attention 19 | peer-reviewed |
| attention 20 | practitioner |
| attention 21 | peer-reviewed |
| attention 22 | peer-reviewed |
| attention 23 | peer-reviewed |
| attention 24 | peer-reviewed |
| attention 25 | peer-reviewed |
| attention 26 | peer-reviewed |
| attention 27 | platform |
| attention 28 | peer-reviewed |
| direction 1 | peer-reviewed |
| direction 2 | peer-reviewed |
| direction 3 | platform |
| direction 4 | practitioner |
| direction 5 | peer-reviewed |
| direction 6 | peer-reviewed |
| direction 7 | peer-reviewed |
| direction 8 | peer-reviewed |
| direction 9 | practitioner |
| direction 10 | peer-reviewed |
| direction 11 | peer-reviewed |
| direction 12 | house |
| direction 13 | peer-reviewed |
| direction 14 | practitioner |
| direction 15 | practitioner |
| direction 16 | practitioner |
| direction 17 | practitioner |
| direction 18 | practitioner |
| direction 19 | peer-reviewed |
| direction 20 | peer-reviewed |
| direction 21 | practitioner |
| direction 22 | practitioner |
| direction 23 | practitioner |
| direction 24 | practitioner |
| direction 25 | peer-reviewed |
| direction 26 | peer-reviewed |
| direction 27 | peer-reviewed |
| direction 28 | practitioner |
| direction 29 | practitioner |
| judging 1 | platform |
| judging 2 | house |
| judging 3 | platform |
| judging 4 | peer-reviewed |
| judging 5 | practitioner |
| judging 6 | platform |
| judging 7 | peer-reviewed |
| judging 8 | practitioner |
| judging 9 | peer-reviewed |
| judging 10 | peer-reviewed |
| judging 11 | peer-reviewed |
| judging 12 | practitioner |
| judging 13 | practitioner |
| judging 14 | practitioner |
| judging 15 | peer-reviewed |
