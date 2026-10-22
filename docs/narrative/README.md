# Narrative craft corpus

What a yt-auto film must do to hold and reward a viewer, written as rules a
writer can follow, a reviewer can check and a pipeline stage can enforce.
Four files, one index.

## The files

| File | Covers | Rules | Feeds stages |
|---|---|---|---|
| `structure-and-hooks.md` | spine and plan (BUT/THEREFORE, ABT, cause-and-effect), promise and payoff, the first 30 s, sections and re-hooks, the ending, series format, reading the retention curve back into the script | 40 | ideas, script, keyframe, sound, build, qa, improve |
| `attention-and-stakes.md` | curiosity gap (Loewenstein, Kang, Gruber), paying the gap, misconception evidence (Muller), stakes (Hitchcock, Pixar, identifiable victim, compassion fade, transportation), honest stakes, cognitive load (Mayer, Sweller), measuring attention | 28 | ideas, research, script, keyframe, vfx, voice, qa, improve |
| `direction-and-narration.md` | binding picture to words, what is in the frame, motion and transitions, cuts, writing for the ear, delivery (wpm, pauses, emphasis, read-aloud) | 29 | script, styles, keyframe, vfx, voice, retime, sound, build |
| `judging-narrative.md` | YouTube's retention vocabulary and ranking objective, running a model as story judge (absolute scoring, order swap, k ≥ 3, different family), the coverage-shaped rubric, the plan "train", cold-reader and read-aloud pre-voice checks | 15 | qa, improve, `yt script --review` |

Each file has the same shape: numbered rules (bold statement, evidence with
inline citations, a `Check:` line naming what a reviewer or a lint can test),
then **Contested** (claims that circulate as settled and are not), **Folklore
and open questions** (rules that could not be sourced to a page that was
read, with what would settle them) and **Sources** grouped by kind.

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
2. **Distilled into `work/house.md`.** Each pipeline stage gets a section of
   `house.md` holding only the rule statements and `Check:` lines whose
   `applies_to` names that stage, at the strength stated here, with no
   citations and no numbers that the corpus marks snippet-sourced or
   unverified. `house.py` injects the stage's section into that stage's
   prompt. The corpus is the source of truth; `house.md` is its compiled
   form and is regenerated, not hand-edited, when a rule changes.
3. **The narrative rubric.** `yt script --review` scores a plan and script
   against criteria drawn from the `Check:` lines, in the shape set by
   `judging-narrative.md` rules 7–13: absolute per-criterion labels of five
   or fewer levels, a different model family from the writer, majority of
   at least three runs, verdict Pass / Consider / Recommend, every Consider
   problem tied to a beat id. Validate-retry-fail runs before voice is paid
   for.
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
freeze-first; humour and tone as retention devices; sponsor-segment
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
