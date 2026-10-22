# Judging narrative quality

How a script or a published film is measured: what the retention data can and
cannot say, how a model acting as story reviewer must be run so its verdict
means something, and what shape the rubric takes. Companion to the craft files
in `docs/narrative/`; those say what a good story is, this says how to tell
whether a given one is.

Every rule names its sources inline, with the kind of source in brackets. A
rule marked *(house convention)* is the pipeline's own operationalisation of a
source that stops short of it.

Shared rules. This file is the home for YouTube's retention vocabulary
(rules 1–3), the ranking objective (rule 4), the judge protocol (rules
7–11) and the plan "train" (rule 13). `structure-and-hooks.md` rules 9, 38
and 40 and `attention-and-stakes.md` rule 27 apply rules 1–3 to writing;
the read-aloud pass in rule 15 is specified in `direction-and-narration.md`
rule 29; the MrBeast time bands in rule 5 are quoted from the PDF text in
`structure-and-hooks.md` rules 23 and 27–29.

---

## Reading retention data after publish

1. **Record YouTube's own key-moment primitives per published film — intro retention at 0:30, top moments, spikes and dips with timestamps — and compare them against the channel's typical retention, not a vendor benchmark.**
   YouTube Studio defines a small vocabulary and the ingest should use it
   verbatim, so a finding routed to `script` is stated in terms the data
   supports. YouTube Help, "Measure key moments for audience retention"
   (https://support.google.com/youtube/answer/9314415) [platform
   documentation]: "Intro tells you what percentage of your audience still
   watched your video after the first 30 seconds"; top moments are "where
   almost no one dropped off"; spikes "were rewatched or shared"; dips were
   "either skipped or moments where viewers stopped watching"; key moments need
   a video of at least 60 s with at least 100 views and are "only highlighted
   if they're detected". YouTube Help, "Content tab analytics tips"
   (https://support.google.com/youtube/answer/12942217) [platform
   documentation]: "A typical intro is about 30 seconds long"; videos with "50%
   of the audience or more watching after the 30-second mark" are listed as
   above-typical intros; "Typical retention is the engagement that the last 10
   of your videos of a similar length have maintained". The "past year" window
   that circulates comes from a 2021 launch announcement (Search Engine
   Journal, "YouTube Rolls Out 'Typical Audience Retention'", 2021,
   https://searchenginejournal.com/youtube-typical-audience-retention/421700
   [news report of a Creator Insider video]), which also described the intro
   baseline as the past 30 videos — the definition has changed since, so do
   not hardcode a window. YouTube Studio also offers a comparison against all
   videos of similar length; preferring the channel's own baseline is a house
   convention, not a platform instruction.
   Check: the analytics ingest schema has `intro_retention_30s`,
   `above_typical_intro`, `top_moments[]`, `spikes[]`, `dips[]` (each with a
   timestamp) and `typical_curve` built from the channel's last 10
   similar-length videos; a retention finding is rejected by the validator
   unless it references one of these fields.

2. **Route script findings from the standard (absolute) retention curve, which carries real timestamps; use the similar-length comparison only for a film-level verdict.**
   The two views answer different questions. YouTube's 2012 analytics post
   (Lauren Vilders, "How to master your data with YouTube Analytics", YouTube
   Creators blog, 2012,
   https://youtube-creators.googleblog.com/2012/05/this-is-part-of-ongoing-series-sharing.html
   [platform documentation]) defines absolute retention as "viewer retention
   as a percentage of total views" where "rewinding and re-watching can result
   in values higher than 100%", and relative retention as "how your video
   compares to videos of a similar length", to be used to "understand why
   certain videos keep your audience engaged and why others are poor
   performers". The current Help page (answer/9314415, above; answer/1715160
   now redirects to it) adds that per-segment view counts in the detailed
   activity report "are expected at times to exceed your video's overall view
   count", and tells the creator to use "when viewers start and stop watching"
   to improve content. YouTube never says a relative dip must not be read as a
   timestamp; the relative view is time-indexed too. Routing only
   absolute-curve timestamps to the script stage is a house convention chosen
   because a relative dip is confounded by what every similar-length video does
   at that point.
   Check: a finding routed to `script` must carry a timestamp from the
   standard curve; a finding derived from the comparison view may only carry a
   film-level label (above/below typical for length) *(house convention)*.

3. **For every dip, name the beat that was on screen and whether viewers skipped it or left; for every late top moment, consider moving that material earlier in the next script.**
   This is the evidence loop in its direct form: the retention report becomes
   a diff against the beat sheet. YouTube Help (answer/9314415, above)
   [platform documentation] gives the platform's own guidance: "review your
   dips to better understand why audiences lost interest in a particular
   segment"; if top moments fall late, "consider introducing the compelling
   content earlier in the video" because "audience sizes typically decrease
   over the length of the video"; and "consider creating newer content by
   expanding on the content from the top moments". Dips are defined as
   "abandoning or skipping", two different signals the finding should keep
   apart. The candidate fixes "cut the beat" and "shorten it" are the
   pipeline's own, not YouTube's. The only quantified rewrite-from-dips result
   found is an uncontrolled single-creator anecdote (see Folklore) and is not
   evidence.
   Check: given the published film's beat sheet with timestamps and the dips
   list, every dip after 0:30 produces exactly one finding naming a beat id and
   tagged `skipped` or `abandoned`; a reviewer confirms the next script in the
   same series addresses or explicitly rejects each finding; a late top moment
   produces a finding tagged `move_earlier`.

4. **Weight absolute watch time and satisfaction proxies above click-through rate when scoring a published film, and ask the QA reviewer whether a viewer would rate the film four or five stars.**
   YouTube's published ranking objective is expected watch time per
   impression, chosen because "ranking by click-through rate often promotes
   deceptive videos that the user does not complete ('clickbait') whereas watch
   time better captures engagement" (Covington, Adams & Sargin, "Deep Neural
   Networks for YouTube Recommendations", RecSys 2016,
   https://blog.acolyer.org/2016/09/19/deep-neural-networks-for-youtube-recommendations/
   summarising the paper; PDF at
   https://static.googleusercontent.com/media/research.google.com/en//pubs/archive/45530.pdf)
   [peer-reviewed]. That objective still contains the click probability (the
   learned odds are approximately E[T](1+P)), so CTR is de-emphasised, not
   dropped. Since 2021 YouTube also predicts survey responses: "Only videos
   that you rate highly with four or five stars are counted as valued
   watchtime", alongside "clicks, watchtime, survey responses, sharing, likes,
   and dislikes" (Cristos Goodrow, "On YouTube's recommendation system",
   YouTube Official Blog, 2021,
   https://blog.youtube/inside-youtube/on-youtubes-recommendation-system/)
   [platform documentation]. Neither source mentions average percentage
   viewed; the 2016 model weights absolute seconds watched, so AVD is the
   closer proxy. Whether creators can see the survey score is not addressed by
   either source.
   Check: the `qa` report emits a predicted average view duration and a 1–5
   "viewer satisfaction" label with a one-line justification; `improve` ranks
   AVD and satisfaction findings above CTR findings; CTR remains an input, not
   the score.

5. **The beat sheet declares a retention job for every time band: a first-minute beat that pays the title promise, a hype-to-execution transition in minutes 1–3, re-engagement beats near 3:00 and 6:00 for films over seven minutes, and no section without a stated job.**
   The only published minute-by-minute retention model from a top channel is
   the leaked MrBeast onboarding document ("How to Succeed in MrBeast
   Production", 2024,
   https://cdn.prod.website-files.com/6623b7720b009050313e701c/66ede69453b7bbadcd2f05a8_How-To-Succeed-At-MrBeast-Production%20(2).pdf)
   [practitioner, internal, unauthenticated]: "The first minute of each video
   is the most important minute of each video"; minutes 1–3 are "where you
   have to transition from hype to execution"; "Usually at the 6 minute mark we
   will include another re-engagement"; "No dull moments in videos"; the named
   KPIs are CTR, AVD and AVP. The quotes used here come from a summary
   (Daniel Scrivner, "Inside MrBeast's $100 Million Content Machine",
   https://www.danielscrivner.com/how-to-succeed-in-mrbeast-production-summary/)
   [practitioner essay] that matches independent write-ups (Simon Willison,
   "How to succeed in MrBeast production", 2024,
   https://simonwillison.net/2024/Sep/15/how-to-succeed-in-mrbeast-production/
   [practitioner essay]: "Minutes 1-3, 3-6 and 6-end all have their own
   clearly defined responsibilities"). Two former producers reportedly
   confirmed the document (Dexerto, "Leaked MrBeast PDF reveals YouTuber's
   secrets to video success",
   https://www.dexerto.com/youtube/leaked-mrbeast-pdf-reveals-youtubers-secrets-to-video-success-2900841/
   [news]); MrBeast has not. It is a challenge-format channel's model, gives
   counts rather than percentage targets, and its one calibration point —
   losing 21M of 60M viewers in the first minute called "reasonably good" — is
   for a mega-viral video. Treat the time bands as a default to tune from the
   channel's own curves.
   Check: `PLAN_SCHEMA` beats carry a `retention_job` field; the reviewer
   fails a plan for a film over seven minutes with no beat tagged
   `re_engagement` in [2:30, 3:30] or [5:30, 6:30], or whose first-minute beat
   does not restate the title promise.

6. **Set retention targets as the channel's own moving baseline and use third-party numbers only as sanity bounds; no vendor "good retention" band is a gate.**
   YouTube publishes no benchmark beyond the 50%-at-0:30 above-typical-intro
   cut and the channel's own typical curve (rule 1). The vendor that publishes
   AVD bands says itself "There is no universal good AVD" and labels its bands
   "vidIQ guidance" with no method (vidIQ, "YouTube Average View Duration:
   What Is a Good AVD?", https://vidiq.com/blog/post/average-view-duration/)
   [practitioner]. The one large-sample length curve found is business video,
   not YouTube: engagement "only minorly different between one minute and
   five minutes", dropping "significantly for videos that go over five
   minutes", with "a 30-60 minute video [at] roughly half the engagement rate
   of a 1-3 minute video" and 43% average for 3–5 minute videos (Wistia, "Top 5
   Insights from Wistia's 2024 State of Video Report", 2024,
   https://wistia.com/blog/insights-state-of-video-report) [vendor data study].
   A platform-wide YouTube average circulates from a report whose primary page
   is gone (see Folklore); use it as an order of magnitude only.
   Check: the `improve` stage's retention finding reports the delta from the
   channel's typical curve; any hard threshold in config must cite its source
   and carry the tag `sanity_bound`.

---

## Running a model as the story judge

7. **Score each draft absolutely against the rubric; use pairwise comparison only to choose between two rewrites, and then run both orderings and several samples, declaring a winner only when the preference is consistent.**
   Pairwise judging is more sensitive to order and to irrelevant surface
   features. Zheng et al., "Judging LLM-as-a-Judge with MT-Bench and Chatbot
   Arena", 2023, https://arxiv.org/html/2306.05685v4 [peer-reviewed], Table 2:
   position-consistent verdicts in only 65% of swaps for GPT-4 and 23.8% for
   Claude-v1; their fix is to count a win only when preferred in both orders.
   "Pairwise or Pointwise? Evaluating Feedback Protocols for Bias in
   LLM-Based Evaluation", COLM 2025, https://arxiv.org/abs/2504.14716
   [peer-reviewed]: "Pairwise preferences flip in about 35% of the cases,
   compared to only 9% for absolute scores" under distractor features. "The
   Coin Flip Judge? Reliability and Bias in LLM-as-a-Judge Evaluation", 2026,
   https://arxiv.org/abs/2606.13685 [preprint]: single-trial pairwise verdicts
   "flip on average 13.6% of the time", about 11 trials for a 95%-stable
   majority, and a 72% first-position preference for one model. Absolute
   scoring with a fixed rubric is the right default for a gate.
   Check: the review gate returns per-criterion absolute labels; any pairwise
   call in the codebase goes through an order-swap plus n-sample majority
   helper, and returns a tie when the orderings disagree.

8. **Write every rubric criterion as binary or at most five labelled levels, each level described in one line with a worked example; never ask for an unbounded or 1–100 score, and shuffle option order when the judge picks among labels.**
   Rubric form drives reliability more than prompt wording. Rao and
   Callison-Burch, "Autorubric: A Unifying Framework for Rubric-Based LLM
   Evaluation on Non-Verifiable Tasks", 2026,
   https://arxiv.org/html/2603.00077v1 [preprint]: "LLM judges exhibit poor
   calibration when asked to produce unbounded numeric scores"; "Binary
   criteria are the simplest to define and yield the highest inter-rater
   reliability"; "shuffling option order across evaluations removes
   systematic position effects"; on ordinal criteria, adjacent accuracy runs
   85–93% against 38–58% exact, so a five-level scale is read at ±1. The same
   paper relays that about 55% of judgment variance is unexplained by the
   stated rubric and that one model agreed with itself on only 61% of cases
   (both cited from secondary work; see Folklore). Few-shot anchors raise
   consistency (GPT-4 65% to 77.5%, Claude-v1 23.8% to 63.7%) at about four
   times the token cost (Zheng et al. 2023, above, Table 13); a scored anchor
   example adds one to two points at a single sample and nothing once eight
   samples are ensembled ("On Cost-Effective LLM-as-a-Judge Improvement
   Techniques", 2026, https://arxiv.org/html/2604.13717 [preprint], Table 9).
   Check: a rubric linter rejects any criterion without an enumerated label
   set of five or fewer with per-level descriptions and one example; the
   judge wrapper shuffles label order and aggregates at least three samples.

9. **Expect the story reviewer to disagree with a human about one script in four; control length explicitly, calibrate it on a human-labelled set of the channel's own scripts before using it as a gate, and do not ask it to reason at length before judging narrative quality.**
   The only creative-writing judge benchmark with debiased human preferences
   puts the best off-the-shelf judge at 73% agreement and trained reward
   models at 78% (LitBench, "A Benchmark and Dataset for Reliable Evaluation
   of Creative Writing", EACL 2026, https://arxiv.org/html/2507.00769,
   published at https://aclanthology.org/2026.eacl-long.362.pdf)
   [peer-reviewed], Table 1: Claude-3.7-Sonnet 73.1%, DeepSeek-R1 71.0%,
   GPT-4.1 70.2%, Claude-3.5-Haiku 67.5%. The raw data had "65.25% of chosen
   responses longer than rejected responses" and had to be pruned to
   symmetry; reward models trained without that control "strongly prefer the
   longer of the two stories"; explicit reasoning "introduces textual noise
   when judging narrative quality". The 85% judge–human agreement often quoted
   is from chat answers on MT-Bench (Zheng et al. 2023, above) and does not
   transfer to narrative judging.
   Check: a length-bias audit runs the reviewer on pairs of the same script at
   two lengths and fails the gate if the longer wins more than 60% of the
   time; a calibration set of at least 20 human-labelled scripts is stored
   and per-criterion agreement is reported; the rubric states in words that
   longer is not better.

10. **Review with a different model family than the one that wrote, and seed the calibration set with known-bad scripts the reviewer must fail.**
    Self-preference and leniency are measured. Zheng et al. 2023 (above),
    §3.3: "GPT-4 favors itself with a 10% higher win rate", Claude-v1 by 25%,
    on limited data. Liu et al., "G-Eval: NLG Evaluation using GPT-4 with
    Better Human Alignment", 2023, https://arxiv.org/abs/2303.16634
    [peer-reviewed], abstract: "LLM-based evaluators having a bias towards the
    LLM-generated texts". Thakur et al., "Judging the Judges: Evaluating
    Alignment and Vulnerabilities in LLMs-as-Judges", 2024,
    https://arxiv.org/abs/2406.12624 [peer-reviewed]: "only the best (and
    largest) models achieve reasonable alignment with humans", scores "may
    still differ with up to 5 points from human-assigned scores", with "a
    tendency toward leniency". The plan already uses a different-family
    reviewer; seeded failures are what make leniency observable.
    Check: config asserts `reviewer.family != writer.family`; the calibration
    run must return `Pass` (the coverage term for reject, rule 12) on every
    seeded bad script or the gate is disabled.

11. **Never gate a script on a single-run difference of less than one level on a five-level criterion; take the majority of at least three independent runs and return borderline results as "Consider".**
    Even expert humans agree near zero when ranking already-good work. Pier et
    al., "Low agreement among reviewers evaluating the same NIH grant
    applications", PNAS 2018, https://www.pnas.org/doi/10.1073/pnas.1714379115
    [peer-reviewed]: 43 reviewers scoring 25 already-funded applications gave
    an inter-rater reliability of exactly zero (95% CI 0–0.14). Erosheva et
    al., "When Zero May Not Be Zero", JRSS-A 2021,
    https://academic.oup.com/jrsssa/article/184/3/904/7068416
    [peer-reviewed] attribute this to range restriction: over the full range a
    single reviewer reaches about 0.34–0.37 and a three-reviewer average about
    0.6. Either way, discriminating among good drafts is noisy and averaging
    three judges roughly doubles reliability; the same logic applies to model
    runs, whose self-agreement is also imperfect (rule 8).
    Check: gate decisions are the majority over k ≥ 3 runs; the pass
    threshold sits a full level above the fail boundary; anything between is
    `Consider`, never `Pass` or `Recommend`.

---

## The rubric's shape

12. **Model the review output on script coverage: a logline, a synopsis the reviewer writes before grading, per-category grades on a four-label scale, and a three-way verdict Pass / Consider / Recommend where Consider means concrete problems to fix before voicing.**
    Coverage is the industry's existing instrument for judging a story on
    paper before money is spent, which is exactly the position of
    `yt script --review`, and its shape maps onto validate-retry-fail. Per
    Wikipedia, "Script coverage", https://en.wikipedia.org/wiki/Script_coverage
    [tertiary]: a logline is "a brief 1 or 2 line summary of the story"; the
    synopsis proves the reader understood the script; categories are graded
    "Excellent, Good, Fair, Poor" across premise, storyline, structure,
    characterization, dialogue and writing style; Pass "fails to make the
    grade in most areas", Consider has "a considerable number of strong
    points" but "a number of significant problems that need to be successfully
    solved", Recommend is "extremely strong in all respects". The documentary
    counterpart adds the question the film is organised around: applicants
    must "articulate the project's story or structure and its central driving
    questions", judged on "strong artistic vision; creative ambition and
    craft; accountability and rigor; and contemporary cultural relevance and
    impact", which "are not boxes to be checked" (Sundance Institute,
    "Documentary Fund", https://www.sundance.org/documentary-fund/ and the
    2027 application page https://apply.sundance.org/prog/2027_documentary_fund_grant/)
    [practitioner, institutional criteria].
    Check: the review output schema has `logline`, `synopsis`, per-category
    `grade` in {Excellent, Good, Fair, Poor}, `verdict` in {Pass, Consider,
    Recommend}, and for Consider a non-empty `problems[]` each tied to a beat
    id; the validator rejects a Recommend that carries any Poor.

13. **Every plan carries a one-sentence "train" — who or what is pursuing what, against what, and which question the film answers — and the reviewer rejects a plan whose sentence names only a topic.**
    The one-sentence test is the documentary form of the logline. Sheila
    Curran Bernard's *Documentary Storytelling* calls the central
    forward-moving thread the narrative spine, or train, which "can often be
    stated in one sentence", with *Super Size Me* as the example; history and
    theme enter only where they deepen that line (secondary summary, SoBrief,
    https://sobrief.com/books/documentary-storytelling [tertiary]; the primary
    text was not reachable, but the 5th edition's chapters "Story Basics",
    "Structure" and "The Film on Paper" are confirmed by the publisher page,
    Routledge, https://www.routledge.com/Documentary-Storytelling-Creative-Nonfiction-on-Screen/Bernard/p/book/9781032267296
    [book]). Script coverage's logline (rule 12) and Sundance's "central
    driving questions" (rule 12) say the same from two other directions. The
    existing `promise`/`payoff` fields become a question with stakes.
    Check: `PLAN_SCHEMA.train` is one sentence containing a question mark or an
    explicit whether/why/how clause and a named obstacle; the reviewer lists
    which beats advance the train and fails the plan if any section advances
    none.

---

## Pre-voice checks that are not story checks

14. **Run a cold-reader review in which the reviewer sees only the script — no research, brief or plan — and must restate the premise, say for each major claim why a viewer should care, and list every term the script never explains.**
    The writer, human or model, cannot see what it failed to explain because
    it already knows it. Steven Pinker names the failure the curse of
    knowledge, "the failure to understand that other people don't know what we
    know", and the remedy "show a draft to a representative reader" (Harvard
    Gazette, "Exorcising the curse of knowledge", 2012,
    https://news.harvard.edu/gazette/story/2012/11/exorcising-the-curse-of-knowledge/)
    [practitioner essay]. *The Sense of Style* adds: show it to people like
    the intended audience "and find out whether they can follow it", show it
    "to yourself, ideally after enough time has passed that the text is no
    longer familiar", and revise "when it comes from more than one reader or
    when it makes sense to the writer herself" (as quoted in the i-deel review,
    https://www.i-deel.org/blog/book-review-the-sense-of-style-by-steven-pinker)
    [book, via review]. Denying the reviewer the research context also stops
    it rewarding faithfulness to the notes instead of clarity to a viewer.
    Check: the cold-reader prompt is built from the script text only (the
    harness asserts no research or plan is in context); its output lists
    unexplained terms and a restated premise; the validator diffs the
    restated premise against `train` and raises a finding on mismatch.

15. **Before paying for final voice, run a cheap TTS read of the script to catch surface problems — unpronounceable tokens, over-long sentences, typos, repeated words — and nothing else; a read-aloud pass is not a test of story quality.**
    Reading aloud measurably improves detection of surface errors, and little
    else. "Reading Aloud Improves Proofreading (but Using Sans Forgetica Font
    Does Not)", Journal of Applied Research in Memory and Cognition, 2022,
    https://www.researchgate.net/publication/359306925_Reading_aloud_improves_proofreading_but_using_Sans_Forgetica_font_does_not
    [peer-reviewed; abstract only]: reading aloud improved error detection
    over silent reading, and participants "did not predict an improvement in
    the aloud condition". "Can Reading Aloud Replace Teacher Feedback?",
    TESL-EJ 28(4), 2025, https://tesl-ej.org/wordpress/issues/volume28/ej112/ej112a5/
    [peer-reviewed]: prior studies "hardly provide the empirical justification
    one would expect for an intervention so universally encouraged"; in 60
    students, reading aloud removed about 3 errors per paragraph against about
    7 for external feedback, and it "was more functional in detecting
    surface-level errors". The voice stage will expose these faults anyway;
    the pre-voice pass is the cheap place to catch them, and the story check
    belongs to the rubric gate.
    Check: a pre-voice linter renders a draft TTS and runs sentence length,
    non-dictionary tokens and numerals-without-spoken-form checks, blocking the
    voice stage until resolved; it carries no story criteria.

---

## Contested

**Pairwise judging is more reliable than pointwise.** True for some setups —
pointwise scores fluctuate across runs — but the COLM 2025 protocol paper
finds pairwise far more vulnerable to distractor features (35% versus 9%
flips) and the Coin Flip Judge preprint finds single-trial pairwise verdicts
flip 13.6% on average. The literature is split; the protocol follows the task
(rule 7).

**LLM judges reach human-level agreement.** On MT-Bench chat answers GPT-4
reaches 85% against a human–human 81%. On debiased creative-writing
preferences the best judge reaches 73% (LitBench). The chat number does not
transfer to narrative judging (rule 9).

**Few-shot anchors fix calibration.** Zheng et al. found consistency gains
(65% to 77.5%) at about four times the cost; the 2026 cost-effectiveness
study found a scored anchor adds one to two points at one sample and nothing
once eight samples are ensembled. Ensembling dominates anchoring (rule 8).

**Expert reviewers agree on quality.** Pier et al. measured zero inter-rater
reliability among NIH reviewers on funded grants; Erosheva et al. argue this
is range restriction and the full-range figure is about 0.35 for one reviewer
and 0.6 for three. Either way, single-judge scores among good drafts are
mostly noise (rule 11).

**Audience retention is what the algorithm rewards.** YouTube's recommender
ranks on expected watch time per impression and on predicted survey
satisfaction, with shares, likes and dislikes as further signals; average
percentage viewed is a creator-facing analytic, not a stated ranking input,
and the 2016 objective keeps the click probability inside it (rule 4). A
vendor article claiming retention is "less important than you think" was not
verified.

**Reading your script aloud makes it a better story.** The evidence supports
surface-error detection only; TESL-EJ found reading aloud removed fewer
errors than external feedback and describes prior support as thin (rule 15).

**"70% at 30 seconds" or "at one minute" is what good channels hit.**
Third-party blogs give 60–75% with no methodology. YouTube's only stated
threshold is 50% at 0:30 for the above-typical-intro list, and the MrBeast
document calls about 65% retained at 1:00 "reasonably good" on a 60M-click
video (rules 1, 5, 6).

**Vendor "good AVP by length" bands** (under 5 min 50–70%, 5–15 min 40–55%,
15–30 min 30–45%). vidIQ labels them guidance with no data; Wistia's data is
business video, not YouTube (rule 6).

**The MrBeast time bands transfer to factual explainers.** The document is a
challenge-format channel's internal model, unconfirmed by MrBeast, with
counts rather than percentage targets; two former producers reportedly
vouched for it. Default to tune, not a rule (rule 5).

**BBC commissioning wants explainers.** A paywalled Deadline report (February
2025, seen only in search snippets) quotes the BBC's documentaries head
saying the BBC no longer wants "biographies", "summaries" or "explainers" and
favours testimony-led films. The primary could not be read and its relevance
to factual YouTube is unclear.

---

## Folklore and open questions

**"The average YouTube video retains 23.7% of its viewers", "only 16.8% of
videos pass 50%", "55% of viewers are lost in the first minute", "Educational
How-To leads at 42.1%".** Status: folklore pending a reachable primary. The
figures trace to a 2025 Retention Rabbit report (10k+ videos, mostly early
and mid-stage channels, self-described) whose primary page returned 404;
they are known only through Stripo's secondary write-up ("YouTube Benchmarks
by Niche — Research Report 2025", https://research.stripo.email/youtube-benchmarks
[secondary data report]) and secondary sites disagree on sample details. Use
as an order of magnitude at most.

**"Put re-hooks at the 25% and 65% marks" and "viewers decide in 8 seconds".**
Status: blog folklore; no primary data found. The only documented
re-engagement schedule is MrBeast's at about 3:00 and 6:00 (rule 5).

**"Shortening sentences to about seven words lifted retention at 2:00 from
38% to 56%."** Status: a single creator's uncontrolled anecdote
(allin1panel.com); not evidence for rule 3.

**"About 55% of judge variance is unexplained by the rubric" and "one model
agrees with itself on only 61% of cases".** Status: cited via Autorubric and
LitBench from Feuer et al. 2025 and Haldar & Hockenmaier 2025; neither
primary was fetched. Confirm before quoting the numbers outside rule 8.

**Bernard's "train" passage.** The exact sentence and page were not retrieved
(O'Reilly, Google Books and archive.org blocked); rule 13 rests on a secondary
summary plus the publisher's chapter list and on two independent instruments
(coverage logline, Sundance criteria) that say the same thing.

**JARMAC 2022 read-aloud effect sizes.** The abstract was seen only via a
ResearchGate listing; sample size and effect sizes were not captured
(psycnet and Ovid paywalled). Rule 15 is stated at the strength of the
abstract only.

**The MrBeast PDF text.** It could not be extracted when this file was
written, so the quotes in rule 5 come from a summary that matches independent
write-ups; the text was later extracted for `structure-and-hooks.md`, whose
rules 23 and 27–29 cite pages 8, 29 and 35 directly. Before any phrase is
placed in `work/house.md`, check rule 5's wording against that extraction.
The document remains unauthenticated either way.

**No first-person account from a top channel of using retention dips to
rewrite a script** (Veritasium, Kurzgesagt, Johnny Harris, Paddy Galloway,
Film Booth) was found before the search budget ran out; rule 3 is grounded in
YouTube's guidance and the MrBeast document, not in a documented workflow.

**BBC commissioning briefs** (bbc.co.uk/commissioning, documentaries and
factual "what we want") were unreachable from this environment; the stated
criteria are missing from this corpus.

**Pixar Braintrust as a pre-release review model.** HBR's "How Pixar Fosters
Collective Creativity" (2008) and the *Creativity, Inc.* excerpt were
paywalled; the candour-without-authority rule is well known but no readable
primary was captured, so it is not a rule here.

**Screenplay-competition rubrics** (The Black List's 1–10 categories, Nicholl
reader criteria) were not retrieved; they would sharpen rule 12's category
list.

**YouTube Analytics API coverage.** Whether the API exposes the per-video
retention curve (`elapsedVideoTimeRatio` × `audienceWatchRatio`) and whether
key moments (intro %, dips, spikes) are available outside the Studio UI was
not checked; rules 1–3 assume they are.

---

## Sources

Platform documentation

- YouTube Help, "Measure key moments for audience retention". https://support.google.com/youtube/answer/9314415 (answer/1715160 redirects here)
- YouTube Help, "Content tab analytics tips". https://support.google.com/youtube/answer/12942217
- Lauren Vilders, "How to master your data with YouTube Analytics", YouTube Creators blog, 2012. https://youtube-creators.googleblog.com/2012/05/this-is-part-of-ongoing-series-sharing.html
- Cristos Goodrow, "On YouTube's recommendation system", YouTube Official Blog, 2021. https://blog.youtube/inside-youtube/on-youtubes-recommendation-system/

Peer-reviewed

- Paul Covington, Jay Adams, Emre Sargin, "Deep Neural Networks for YouTube Recommendations", RecSys 2016. https://static.googleusercontent.com/media/research.google.com/en//pubs/archive/45530.pdf (summary: https://blog.acolyer.org/2016/09/19/deep-neural-networks-for-youtube-recommendations/)
- Lianmin Zheng et al., "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena", 2023. https://arxiv.org/html/2306.05685v4
- "Pairwise or Pointwise? Evaluating Feedback Protocols for Bias in LLM-Based Evaluation", COLM 2025. https://arxiv.org/abs/2504.14716
- Daniel Fein, Sebastian Russo, Violet Xiang, Kabir Jolly, Rafael Rafailov, Nick Haber, "LitBench: A Benchmark and Dataset for Reliable Evaluation of Creative Writing", arXiv July 2025; EACL 2026 long paper. https://arxiv.org/html/2507.00769 ; https://aclanthology.org/2026.eacl-long.362.pdf (the Anthology PDF was fetched but its text could not be extracted here; the arXiv record was confirmed)
- Yang Liu et al., "G-Eval: NLG Evaluation using GPT-4 with Better Human Alignment", 2023. https://arxiv.org/abs/2303.16634
- Aman Singh Thakur et al., "Judging the Judges: Evaluating Alignment and Vulnerabilities in LLMs-as-Judges", 2024. https://arxiv.org/abs/2406.12624
- Elizabeth L. Pier et al., "Low agreement among reviewers evaluating the same NIH grant applications", PNAS 2018. https://www.pnas.org/doi/10.1073/pnas.1714379115
- Elena A. Erosheva et al., "When Zero May Not Be Zero: A Cautionary Note on the Use of Inter-Rater Reliability in Evaluating Grant Peer Review", JRSS-A 2021. https://academic.oup.com/jrsssa/article/184/3/904/7068416
- "Reading Aloud Improves Proofreading (but Using Sans Forgetica Font Does Not)", Journal of Applied Research in Memory and Cognition, 2022, doi 10.1037/mac0000011. https://www.researchgate.net/publication/359306925_Reading_aloud_improves_proofreading_but_using_Sans_Forgetica_font_does_not
- "Can Reading Aloud Replace Teacher Feedback? A Comparative Analysis of Proofreading in ESL Writing", TESL-EJ 28(4), 2025. https://tesl-ej.org/wordpress/issues/volume28/ej112/ej112a5/

Preprints

- Delip Rao, Chris Callison-Burch, "Autorubric: A Unifying Framework for Rubric-Based LLM Evaluation on Non-Verifiable Tasks", arXiv 2603.00077, February 2026 (title corrected against the arXiv record). https://arxiv.org/html/2603.00077v1
- Ryan Lail, Luke Markham, "On Cost-Effective LLM-as-a-Judge Improvement Techniques", arXiv 2604.13717, April 2026 (evaluated on RewardBench 2, not creative writing). https://arxiv.org/html/2604.13717
- Abel Yagubyan, "The Coin Flip Judge? Reliability and Bias in LLM-as-a-Judge Evaluation", arXiv 2606.13685, 2026 (single author; the 13.6% flip figure is in the abstract). https://arxiv.org/abs/2606.13685

Practitioner and institutional

- "How to Succeed in MrBeast Production", leaked internal onboarding document, 2024. https://cdn.prod.website-files.com/6623b7720b009050313e701c/66ede69453b7bbadcd2f05a8_How-To-Succeed-At-MrBeast-Production%20(2).pdf
- Daniel Scrivner, "Inside MrBeast's $100 Million Content Machine" (summary with quotes). https://www.danielscrivner.com/how-to-succeed-in-mrbeast-production-summary/
- Simon Willison, "How to succeed in MrBeast production", 2024. https://simonwillison.net/2024/Sep/15/how-to-succeed-in-mrbeast-production/
- Sundance Institute, "Documentary Fund". https://www.sundance.org/documentary-fund/
- Sundance Institute, "2027 Documentary Fund Grant" application page. https://apply.sundance.org/prog/2027_documentary_fund_grant/
- Steven Pinker, "Exorcising the curse of knowledge", Harvard Gazette, 2012. https://news.harvard.edu/gazette/story/2012/11/exorcising-the-curse-of-knowledge/
- vidIQ, "YouTube Average View Duration: What Is a Good AVD?". https://vidiq.com/blog/post/average-view-duration/

Vendor data

- Wistia, "Top 5 Insights from Wistia's 2024 State of Video Report", 2024. https://wistia.com/blog/insights-state-of-video-report
- Stripo Research, "YouTube Benchmarks by Niche — Research Report 2025" (citing Retention Rabbit 2025, primary unreachable). https://research.stripo.email/youtube-benchmarks

Books and secondary

- Sheila Curran Bernard, *Documentary Storytelling: Creative Nonfiction on Screen*, 5th ed., Routledge 2022 (publisher page, table of contents). https://www.routledge.com/Documentary-Storytelling-Creative-Nonfiction-on-Screen/Bernard/p/book/9781032267296
- SoBrief, summary of *Documentary Storytelling*. https://sobrief.com/books/documentary-storytelling
- i-deel, review of Steven Pinker, *The Sense of Style*. https://www.i-deel.org/blog/book-review-the-sense-of-style-by-steven-pinker
- Wikipedia, "Script coverage". https://en.wikipedia.org/wiki/Script_coverage

News

- Matt G. Southern, "YouTube Rolls Out 'Typical Audience Retention'", Search Engine Journal, 2021. https://searchenginejournal.com/youtube-typical-audience-retention/421700
- Dexerto, "Leaked MrBeast PDF reveals YouTuber's secrets to video success". https://www.dexerto.com/youtube/leaked-mrbeast-pdf-reveals-youtubers-secrets-to-video-success-2900841/
