# Structure, hooks and endings

What a yt-auto film must do with its shape, its first thirty seconds and its
last ten, stated as rules a writer can follow and a reviewer can check. Every
rule names its source, the year, the URL and the kind of source (peer-reviewed,
platform documentation, practitioner, book, other); rules whose evidence was
found weaker than first claimed are stated at the corrected strength, and what
could not be read is listed at the end rather than cited as if it had been.

A note on strength. Almost nothing here was measured against YouTube
retention. The platform documentation defines what YouTube reports; the
peer-reviewed work measures learning, recall or clicks in other media; the
practitioner material is single-channel curves and craft testimony. The
"Check:" lines are what the pipeline can enforce today; the evidence loop
(`yt improve` fed by the channel's own curves) is what will make the
thresholds empirical.

Shared rules. Several rules recur across the corpus; each has one home file
and the others point to it. The 30-second Intro metric is defined in
`judging-narrative.md` rule 1 (rules 9 and 38 here apply it to writing);
misconception-first evidence lives in `attention-and-stakes.md` rule 7 (rule
15 here fixes its position); concrete-before-abstract is rule 21 here with
the frame-level and sentence-level versions in `direction-and-narration.md`
rules 9 and 20; the promise/payoff ledger is rules 6–8 here, with the
satisfaction evidence in `attention-and-stakes.md` rule 6 and the plan
"train" in `judging-narrative.md` rule 13; curiosity-gap evidence
(Loewenstein, Kang) is `attention-and-stakes.md` rules 1–3, cited here in
rules 14 and 22 only by reference; the Zeigarnik objection is stated once in
`attention-and-stakes.md` Contested and repeated here in one line.

---

## Spine and plan

1. **Between any two consecutive beats the honest connective is BUT,
   THEREFORE or BECAUSE; a beat that only follows AND THEN is reworked, cut
   or re-ordered.** The South Park writers test an outline by reading the
   implied word between beats: "this happens, and therefore this happens.
   But this happens, therefore this happens"; if "and then" fits, the outline
   fails, and Stone names "because" as a third legitimate link (Trey Parker
   and Matt Stone, NYU writing class transcript, 2014,
   https://speakola.com/arts/matt-stone-trey-parker-nyu-writing-class-2014 —
   practitioner talk). Perell's retelling adds the Jimmy-at-the-mall example
   (David Perell, *The But & Therefore Rule*, n.d.,
   https://perell.com/note/but-therefore-rule/ — practitioner essay). For a
   factual film each section must complicate the one before it or follow from
   it. The remedy "cut or re-order" is this project's operationalisation; the
   source only says the outline fails. No experimental test exists.
   Check: the plan records the connective between each adjacent pair of
   beats; any AND fails; two or more consecutive THEREFORE links with no BUT
   is a warning (no complication). Automatable once `PLAN_SCHEMA` carries the
   connective.
   Stages: script, qa, plan.beats

2. **Draft the whole film as one ABT sentence — "[setup] AND [more setup],
   BUT [the problem], THEREFORE [what the film does]" — as a planning check
   against list-shaped drift, not as a gate that every section must map
   to.** Olson's template gives "momentum (And), conflict (But), and
   resolution (Therefore)" and names the failure mode as "a stultifying
   procession of 'and, and, and'" (Randy Olson, *Houston, We Have a
   Narrative*, 2015,
   https://press.uchicago.edu/ucp/books/book/chicago/H/bo21174162.html —
   book; quotes are from the publisher's description). The ABT doubles as the
   promise (AND/BUT) and the payoff (THEREFORE). Olson's Narrative Index
   (count of "but" divided by count of "and", ×100; "good speeches" score 20
   or more; needs ~1,000 words) exists only in press interviews (Joe Romm
   quoting Olson, ThinkProgress, 2018,
   https://thinkprogress.org/what-trump-can-teach-progressives-about-viral-communications-5d1e468446d0/
   — other) and the one claim that it "has shown" narrative users are more
   effective is asserted without data (Bill Dennison, *Narrative literacy for
   science communication*, 2017,
   https://ian.umces.edu/blog/narrative-literacy-for-science-communication/
   — practitioner essay). No source prescribes section-level mapping and no
   peer-reviewed validation of ABT was found.
   Check: `PLAN_SCHEMA` gets an `abt` field; the reviewer rejects a plan whose
   BUT clause is missing or is not a genuine contradiction of the AND, or
   whose THEREFORE is not what the film delivers. Optional soft lint: count
   but/however/yet against and in the narration and warn under ~0.1; warning
   only.
   Stages: script, qa, plan.abt

3. **The spine is a chain of cause and effect over time with an agent at its
   centre; a plan whose beats could be reordered without loss is a list and
   is rejected at plan time.** Narrative "describes the cause-and-effect
   relationships between events that take place over a particular time
   period that impact particular characters", and in the comparison cited
   narrative text "was read twice as fast and recalled twice as well" as
   expository text (Michael F. Dahlstrom, *Using narratives and storytelling
   to communicate science with nonexpert audiences*, PNAS, 2014,
   https://pmc.ncbi.nlm.nih.gov/articles/PMC4183170 — peer-reviewed). From
   the editing room: "Story comes from within the material and the ways in
   which you, the filmmaker, structure it", and "the point of attack ushers
   the viewer into the world of your film" (Sheila Curran Bernard,
   *Documentary Storytelling*, as reviewed in *Story's the Thing*,
   https://documentary.org/feature/storys-thing — book via review; 5th ed.
   2022, https://www.routledge.com/Documentary-Storytelling-Creative-Nonfiction-on-Screen/CurranBernard/p/book/9781032267296).
   Bernard explicitly does not require three acts.
   Check: the plan names (a) the agent(s) the story follows, (b) the time
   axis, (c) at least two "because" links between beats, and (d) the point
   of attack, with the reason for entering there. The reviewer applies the
   reorder test and fails a plan that survives it.
   Stages: research, script, qa, plan.beats

4. **Lock the payoff beat and the final line before any section is written;
   sections are then written backward from the ending.** "Come up with your
   ending before you figure out your middle. Seriously. Endings are hard, get
   yours working up front" (Emma Coats, Pixar story rule 7, compiled by
   Aerogramme Writers' Studio, 2013,
   https://www.aerogrammestudio.com/2013/03/07/pixars-22-rules-of-storytelling/
   — practitioner). "Once the Climax is in hand, stories are in a significant
   way rewritten backward, not forward", and an ending must be "inevitable
   and unexpected" (Robert McKee, *Story*, 1997, notes by Derek Sivers,
   https://sive.rs/book/Story — book via secondary notes). Craft sources, no
   data.
   Check: `payoff` must be a concrete sentence containing a specific fact,
   number or image from research; the script stage refuses to run while
   `payoff` is empty or generic ("we learned a lot").
   Stages: script, plan.payoff

## Promise and payoff

5. **Freeze the title and thumbnail promise before the hook is written; the
   hook is a function of the packaging.** Lawrence finds a proven title and
   thumbnail first: "And then I will write the hook and then I'll make the
   video. If you make the video first, you're probably going to fail" (Ed
   Lawrence, *Mastering YouTube Strategies*, Creators on Air transcript,
   2024,
   https://www.passionfroot.me/creators-on-air/mastering-youtube-strategies-with-ed-lawrence
   — practitioner). YouTube's own diagnosis of a weak opening starts with the
   thumbnail and title (YouTube Help, *Measure key moments for audience
   retention*, https://support.google.com/youtube/answer/9314415 — platform
   documentation). The MrBeast memo says the same (CTR "drives the video
   concepts"; title and thumbnail "set the expectations") but is an
   unauthenticated reproduction (attributed to Jimmy Donaldson, reproduced by
   Alexander Jarvis, 2024,
   https://www.alexanderjarvis.com/memo-how-to-succeed-in-mrbeast-production/
   — other).
   Check: `plan.promise` (title plus thumbnail concept) is frozen before the
   script stage runs; the script reviewer receives it and fails a hook that
   does not reference its key claim.
   Stages: ideas, script, plan.promise

6. **Write the promise into the plan as one sentence, restate its question
   within the first 30 seconds, and make the final section its explicit
   payoff; nothing promised goes unpaid and no payoff answers a question that
   was never promised.** A high Intro figure "could mean: the content in the
   first 30 seconds matched the viewer's expectation of the video's thumbnail
   and title" (YouTube Help, *Measure key moments*, above — platform
   documentation). "If you have a hook, it's because you have some sense of
   what you're hooking someone into" (Grant Sanderson on David Perell's
   podcast, *Math for the Masses*, n.d.,
   https://perell.com/podcast/grant-sanderson-math-for-the-masses/ —
   practitioner). Johnny Harris teaches a lesson titled "Your Story Promise"
   (Bright Trip, *Visual Storytelling*,
   https://www.brighttrip.com/courses/visual-storytelling — course listing)
   and a podcast chapter titled "Every Story is a Promise" (Perell and
   Harris, How I Write, 2025, chapter list at
   https://podwise.ai/dashboard/episodes/3299864 — practitioner; titles only,
   his wording was behind a sign-in).
   Check: a paraphrase of `promise` occurs in the first 30 s of narration;
   the last section contains the `payoff` sentence; every noun phrase in the
   title appears somewhere in the narration. Fail on any miss.
   Stages: script, qa, plan.promise, plan.payoff

7. **The last beat pays the title promise in resolved form and mirrors the
   opening image or question, so the viewer can see the distance
   travelled.** The beat-sheet convention: an Opening Image that "instantly
   introduces us to the present world" and a Final Image that "mirrors or
   contrasts the opening image" (Blake Snyder, *Save the Cat!*, 2005, as
   summarised by Reedsy, 2024,
   https://reedsy.com/blog/guide/story-structure/save-the-cat-beat-sheet —
   book via secondary; Snyder's exact wording not verified). McKee asks for
   one "Key Image" that "sums up and concentrates all meaning and emotion"
   (McKee via Sivers, above). Vox's black-hole film withheld the photograph
   until the viewer understood why it was hard: "I wanted to get people to
   understand why this is so difficult before they saw the result" (Joss
   Fong, interviewed by Tiên Nguyễn, The Open Notebook, 2020,
   https://www.theopennotebook.com/2020/01/07/videogram-how-a-vox-video-explains-the-science-behind-the-first-photo-of-a-black-hole/
   — practitioner).
   Check: `payoff` semantically answers `promise` (reviewer: "does the last
   section state the answer to the title question in one sentence?"); the
   final section references a noun phrase or question from the first section
   (callback test); the final keyframe references the opening frame's subject
   in a changed state.
   Stages: script, keyframe, qa, plan.payoff

8. **Keep a payoff ledger: the presiding questions the hook and section
   openers pose are answered before the end, every late reveal was planted
   earlier, and any question deliberately left open is recorded as a
   choice.** Closure is the "feeling of finality" that rests on the judgment
   that "the presiding macro questions posed by the plot of the narrative get
   answered"; the authors treat closure as a matter of degree and amend the
   definition rather than endorsing it flat (Tobias Klauk, Tilmann Köppe and
   Edgar Onea, *More on narrative closure*, Journal of Literary Semantics,
   2016, https://www.degruyterbrill.com/document/doi/10.1515/jls-2016-0003/html
   — peer-reviewed; abstract read, full text paywalled). "To set up means to
   layer in knowledge. To pay off means to close the gap by delivering that
   knowledge" — and in the same notes, "unanswered questions may trail out of
   the film", which is why the ledger records deliberate openings instead of
   banning them (McKee via Sivers, above).
   Check: `questions_raised[]` with the beat that raises each and
   `answered_at`; fail if a macro question has no answering beat, or if the
   final payoff references a fact or object never introduced; setups never
   referenced again are cut candidates.
   Stages: script, qa, plan.questions_raised

## The opening

9. **Within the first 30 seconds the viewer knows why they should care and
   sees what the title and thumbnail promised; the compelling material comes
   early, not late.** "Intro tells you what percentage of your audience still
   watched your video after the first 30 seconds"; the remedies are to change
   the thumbnail and title to match the content and to "modify the first 30
   seconds of your video and experiment with different styles"; when top
   moments fall late, "consider introducing the compelling content earlier"
   (YouTube Help, *Measure key moments*, above — platform documentation).
   The SoME judging criteria require Motivation: "It should be clear to the
   reader/viewer within the first 30 seconds why they should care" (Grant
   Sanderson, *Summer of Math Exposition* announcement, 2021,
   https://www.3blue1brown.com/blog/some1 — practitioner; one judging
   panel's criterion). The 30 seconds is an analytics checkpoint plus one
   competition's rubric, not an empirically derived threshold, and neither
   source forbids orienting context; YouTube publishes no retention
   benchmark for it.
   Check: narration time to the first sentence that states the stake or
   question (the ABT's BUT clause) is ≤ 30 s; the first 30 s contain a noun
   phrase from the title. Improve stage: an Intro figure below the channel's
   own median routes a finding to script with this rule.
   Stages: script, qa, improve

10. **Deliver on the title and thumbnail in the initial seconds: the
    opening's first job is to confirm the viewer clicked the right video.**
    "The initial seconds of your content are when viewers decide to stay or
    move on"; "Ensure your intro delivers on the promise made by your
    thumbnail and title"; "Keep your intro concise and get straight to
    providing that value" (YouTube Help, *Understand your content
    performance for YouTube's recommendation system*, 2025,
    https://support.google.com/youtube/answer/16559650 — platform
    documentation). Lawrence writes "an introduction that undoes the
    clickbait, sets up the value the viewer's gonna get" (Ed Lawrence on
    Creator Science, 2023, https://creatorscience.com/podcast/ed-lawrence/ —
    practitioner, near the 3:49 mark). The MrBeast memo's version ("you'd
    feel like you were lied to and click off") is framed around the first
    minute, not the first seconds (memo via Jarvis, above — other). No
    verified source fixes a 5–15 s window; the window below is a house
    choice.
    Check: the title's key noun or claim is spoken or on screen within the
    first 15 s; the first keyframe shows the thumbnail subject; fail if the
    first 15 s contain only a channel intro, greeting or unrelated context.
    Stages: script, keyframe, qa

11. **No pre-content filler before the hook — no logo sting or intro
    graphics, no rundown of what the video will cover, no self-introduction
    or channel pitch, no opening call-to-action; the subject, or the host
    delivering it, is on screen within 10–15 seconds.** Wistia cut a long
    series setup "and got to the good stuff within the first 10 seconds.
    Overall engagement for the video popped up by 5% points, and we cut our
    initial viewership loss in half" (Wistia, *What Your Video Engagement
    Graph Is Telling You*, 2026,
    https://wistia.com/blog/engagement-graph-learnings — vendor data, one
    case); across Wistia-hosted video the "nose" (first 2% of runtime) loses
    4.9% on 1–2 minute videos and 17.3% on 5–10 minute ones, and the remedy
    is "Experiment with removing intro graphics and logo sequences" (Wistia,
    *Understanding Audience Retention*, 2020,
    https://wistia.com/learn/marketing/understanding-audience-retention —
    vendor data, business video). A review that started at 0:38 after a
    rundown and a self-introduction was cut post-upload to start at 0:11 and
    the early curve went "mostly completely flat" (George Blackman,
    *Retention Review #7*, 2023,
    https://georgeblackman.substack.com/p/retention-review-7-he-fixed-retention
    — practitioner, no percentages). YouTube said in 2011 to "share your
    channel branding later" and that "a flashy intro may look cool, but it's
    not the star of the video" (Ryan Nugent, YouTube Creator Playbook, 2011,
    https://blog.youtube/news-and-events/this-is-first-of-series-of-posts/ —
    platform documentation; its window is 15 s and it praises a host who
    greets, jokes and sets up the show inside it). A client's opening
    call-to-action "had to go" (Paddy Galloway, retention thread, 2022,
    https://x.com/PaddyG96/status/1510219133895741443 — practitioner, read
    via a threadreader mirror). A brief in-character greeting that itself
    sets up the show is not contraindicated by any of these.
    Check: lint rejects the script if any of {channel name, "welcome",
    "subscribe", "my name is", "in this video I"} occurs before the first
    content sentence; the plan is rejected if a logo or title-card beat is
    scheduled before 0:10.
    Stages: script, qa

12. **Before explaining details, tell the viewer why the topic matters to
    them: the stakes, or a context they recognise.** An SEO explainer that
    opened on the agency's own AI use and jargon lost "about 30% of the
    audience" in its first segment "whereas S2 and S3 only lose 5-10%", and
    the lesson drawn is to "make the audience know why they should care or
    show them the topic in a context they'll recognise before explaining the
    nuances" (George Blackman, *Retention Review #5*, Dec 2022,
    https://georgeblackman.substack.com/p/retention-review-5-why-should-i-care
    — practitioner; one self-submitted video, and audiences shrink early
    anyway). Blackman's hook formulas carry Stakes in both variants
    (Target/Transformation/Stakes; Character/Concept/Stakes) and his worked
    example held 86% at 30 s (Blackman, *My 3-Step framework for writing
    killer hooks*, 2023,
    https://www.georgeblackman.com/write-on-time/my-3-step-framework-for-writing-killer-hooks
    — practitioner). Lawrence's intro "makes them understand why this is an
    important story" (Lawrence on Creator Science, above). The 30 s window
    is YouTube's measurement convention, not a tested threshold.
    Check: the reviewer locates target (who this is for), transformation
    (what they will know) and stakes (the cost of not knowing) each in a
    specific sentence inside the first 30 s; missing stakes fails.
    Stages: script, qa

13. **Confirm the subject within the first 5–10 seconds but never enumerate
    the conclusions: the hook withholds the answer, not the topic.**
    YouTube's 2014 tips include "Tell them what they're going to see", with
    an example whose subject is clear within five seconds (Devin McNulty and
    Jeremy Kaye, *Four tips to hook your viewers on YouTube*, 2014,
    https://blog.youtube/creator-and-artist-stories/four-tips-to-hook-your-viewers-on/
    — platform documentation), and the 2011 playbook says "make it clear what
    your video is about early on" and "tease the rest" (Nugent, above). A
    list-of-points opener "is not a hook, that's just saying what you're
    going to say" (Lawrence, Creators on Air transcript, above). In 8,977
    Upworthy headline A/B tests, concreteness raised click-through only when
    the baseline was vague (8.7% of tests) and lowered it when headlines were
    already concrete (50.9%); interaction −0.058, p < 0.001 (*When curiosity
    gaps backfire*, registered report, 2024,
    https://pmc.ncbi.nlm.nih.gov/articles/PMC11704130/ — peer-reviewed;
    headlines, not video openings).
    Check: the subject noun is present by 0:10; none of the film's section
    conclusions (from `payoff` or section summaries) is stated verbatim in
    the hook; fail on a bullet-list preview ("first we'll ..., then ...").
    Stages: script, qa

14. **Open a specific question the title did not answer, and build the body
    as a chain of questions opened and then closed.** Curiosity is "a form of
    cognitively induced deprivation that arises from the perception of a gap
    in knowledge or understanding" (George Loewenstein, *The Psychology of
    Curiosity*, Psychological Bulletin, 1994, abstract at
    https://stafforini.com/works/loewenstein-1994-psychology-curiosity-review/,
    PDF at https://www.cmu.edu/dietrich/sds/docs/loewenstein/PsychofCuriosity.pdf
    — peer-reviewed; abstract read, PDF is a scan). Curiosity while reading
    trivia questions tracked reward-anticipation activity, people spent
    scarce tokens and waiting time to learn answers, and curiosity predicted
    recall of surprising answers one to two weeks later (Kang et al., *The
    Wick in the Candle of Learning*, Psychological Science, 2009,
    https://authors.library.caltech.edu/22280/ — peer-reviewed). The
    practitioner form: "You open a loop, you then go and close it, and then
    you move on to the next point"; "YouTube really is just a game of making
    people want to know what happens next" (Lawrence, Creators on Air
    transcript, above); "the audience needs to know pretty much at all times
    what it is they're waiting around for next" (George Blackman on Creator
    Science, 2024, https://creatorscience.com/podcast/george-blackman/ —
    practitioner). The "by the 30-second mark" phrasing attributed to Colin
    and Samir could not be read (see Folklore); the Intro window is the house
    deadline. Open loops are justified by the information-gap and
    prequestion evidence, not by the Zeigarnik effect (see Contested).
    Check: `hook.question` is a literal question (or a declared unknown) and
    is not answerable from the title alone; each section carries `opens[]`
    and `closes[]` question ids; a section that closes nothing and opens
    nothing fails.
    Stages: script, qa, plan.hook

15. **Voice the common intuitive-but-wrong belief explicitly and refute it,
    rather than giving only the clean exposition; where the film can, make
    the viewer commit to a guess first.** In randomised online experiments
    (364 first-year physics students, four treatments) the Refutation and
    Dialogue videos that stated misconceptions produced the largest learning
    gains over a concise Exposition, with low-prior-knowledge viewers
    benefiting most (D. A. Muller, J. Bewes, M. D. Sharma and P. Reimann,
    *Saying the wrong thing: improving learning with multimedia by including
    misconceptions*, Journal of Computer Assisted Learning, 2008,
    https://doi.org/10.1111/j.1365-2729.2007.00248.x — peer-reviewed; abstract
    via Crossref). Viewers of the dialogue version reported more mental
    effort and scored higher than lecture-style viewers (Muller, Sharma and
    Reimann, *Raising cognitive load with linear multimedia to promote
    conceptual change*, Science Education, 2008,
    https://doi.org/10.1002/sce.20244 — peer-reviewed; abstract). The
    secondary account gives pre-test ~6/26, no gain from the "clear" video,
    and "just about doubled, to almost 12 out of 26" for the "confusing" one
    (Cathie Norris and Elliot Soloway, THE Journal, 2015,
    https://thejournal.com/articles/2015/05/05/watching-videos-does-not-necessarily-lead-to-learning.aspx
    — other); "no gain" is that account's rounding — the JCAL abstract
    implies a smaller gain for exposition, not none (see
    `attention-and-stakes.md` rule 7 and its Contested entry). Committing to a prediction before a demonstration is separate
    classroom evidence: passive observers learn no better than non-viewers
    (Crouch, Fagen, Callan and Mazur, *Classroom demonstrations: Learning
    tools or entertainment?*, American Journal of Physics, 2004,
    https://doi.org/10.1119/1.1707018 — peer-reviewed), which is what Muller
    paraphrases as "If you perform a demonstration and don't force the class
    to make a prediction, they'll learn about as much as if they never saw
    the demonstration at all" (Scientific American, 2025,
    https://www.scientificamerican.com/article/how-youtube-star-derek-muller-of-veritasium-is-challenging-scientific/
    — other). Muller's stated method: "engage with people's prior knowledge,
    which includes talking about misconceptions" (Creator Handbook, 2017,
    https://www.creatorhandbook.net/inside-veritasium-an-interview-with-dr-derek-muller-12ad2351a4b3/
    — practitioner interview) and "if you don't tap in to them ... it's
    really just reinforcing what they thought beforehand" (University of
    Sydney, *An element of truth*, 2024,
    https://www.sydney.edu.au/news-opinion/news/2024/11/05/the-element-of-truth.html
    — other). YouTube's own example is CGP Grey opening on "how many countries
    are there?" (McNulty and Kaye, above). The outcome measured is learning,
    not watch time; the tested dialogue interleaved misconception and
    correction throughout, so the evidence supports including the
    misconception, not any fixed position. Opening on it is this corpus's
    choice, because the gap must sit on something the viewer already partly
    believes.
    Check: the research stage lists what people wrongly assume about the
    topic, with a source; `PLAN_SCHEMA` gets a `misconception` field, and it
    may not be empty for a science or explainer topic. Reviewer: is the
    belief stated or implied in the first 30 s, and contradicted or put in
    doubt before the explanation starts?
    Stages: research, script, qa, plan.misconception

16. **A story-driven film cold-opens at its most dramatic moment and gives
    context afterwards; an explainer opens on the subject itself, never on a
    build-up — and a cold open without stakes is not a hook.** "Hit them
    with a cold opening" is one of YouTube's four listed openings (McNulty
    and Kaye, above). "If you're starting with a story, you want to drop
    people into the darkest, deepest, most chaotic moment of the story"
    (Lawrence, Creators on Air transcript, above). Google's ad-creative
    research recommends "an emerging story arc" over a slow build and reports
    that opening visuals that make viewers think or feel "correlate with
    longer view times" (PPC Land summarising the ABCD framework, 2024,
    https://ppc.land/mastering-youtube-advertising-with-the-abcd-framework/ —
    other; skippable ads, correlational). The counter-case: a six-second
    wordless cold open on "dumb ways to die" left the reviewer with "a lack
    of stakes" (George Blackman, *RR#8 — Study this hook edit*, 2023,
    https://georgeblackman.substack.com/p/rr8-study-this-hook-edit —
    practitioner). No controlled experiment on organic openings exists.
    Check: if `plan.mode == "story"`, the first beat is the climax or
    inciting incident, not the chronological start; if "explainer", the
    first beat contains the subject noun. Either way, context beats may not
    precede the first subject or incident beat.
    Stages: script, plan.mode

17. **The first spoken sentence addresses the viewer or their situation
    concretely; a vague first-person opener fails even with strong
    visuals.** A well-edited hook whose first line was "This is how watching
    Iman Gadzhi changed my life" still showed "a significant dropoff"
    because the line is vague and does not address the viewer; the rule
    drawn is to make viewers feel personally addressed from the start, for
    example with a direct question (Blackman, *RR#8*, above —
    practitioner). Tayo Aina's "10-second rule" says the same, but his
    "+30% retention" claim has no baseline (Tayo Aina, 2024,
    https://tayoaina.com/newsletter/my-10-second-rule-for-youtube-video-retention/
    — practitioner). Low confidence: two practitioners, one case.
    Check: lint — the first sentence of narration contains "you"/"your" or a
    concrete noun from the title, and does not begin with "I"/"my"/"this is"
    plus an abstraction.
    Stages: script, qa

18. **Narration starts within a second and the hero subject is in the first
    frame; no wordless establishing shot or title card.** Blackman would
    have cut the six seconds before the narrator spoke in one hook and
    praised frequent angle changes in another, which still dropped because
    the first sentence was vague — visuals do not rescue a weak line
    (Blackman, *RR#8*,
    https://georgeblackman.substack.com/p/rr8-study-this-hook-edit —
    practitioner). The ad-research figure of
    "more than two frames in the first five seconds" comes from a PDF that
    could not be read and is listed under Folklore.
    Check: at build, narration onset ≤ 1.0 s; the first keyframe contains
    the face or object named in the title. Automatable from the timeline.
    Stages: script, keyframe, build

## Sections and the middle

19. **State the specific question a section answers in its first line and
    answer it in the same section; the question must be specific, not a
    generic tease.** Asking two questions before each ~2-minute video
    segment improved memory for the material, including items not
    pre-questioned (Carpenter and Toftness, 2017, as summarised by BPS
    Research Digest,
    https://bps.org.uk/research-digest/heres-simple-way-boost-your-learning-videos-prequestion
    — other, n = 85). With authentic 20-minute lectures the benefit was
    confined to the pre-questioned material and "small or absent" elsewhere
    (Toftness, Carpenter, Lauber and Mickes, *The Limited Effects of
    Prequestions on Learning from Authentic Lecture Videos*, JARMAC, 2018,
    https://pure.royalholloway.ac.uk/en/publications/the-limited-effects-of-prequestions-on-learning-from-authentic-le/
    — peer-reviewed). Not verified in this pass; exact percentages are in
    figures not retrieved. Neither study tests where the question sits or
    whether the section closes it: the open-then-close form is practitioner
    craft ("You open a loop, you then go and close it, and then you move on
    to the next point", Lawrence, rule 14), and the opening-sentence
    placement is house.
    Check: each section's first two sentences contain an interrogative or an
    explicit "the question is ..." whose answer appears later in the same
    section, or is explicitly deferred to a named later section. The
    reviewer keeps the question ledger (rule 8); any question never answered
    fails.
    Stages: script, qa

20. **Every section stands on its own with its own payoff — a fact, reversal
    or image the viewer did not have at its start; a section that only sets
    up a later one is cut or merged.** "Each individual scene has to work as
    a funny sketch" (Parker and Stone, above). Fong hopes "each episode has
    a moment that might stick in people's minds" rather than "so much
    information that almost nothing really sticks", and there is no fixed
    template because "every story has its own needs" (Joss Fong, Video
    Consortium Mag, n.d., https://videoconsortium.org/mag/episode-1-joss-fong
    — practitioner); her script editors must say "I was confused at this
    part" (Fong, The Open Notebook, above). YouTube's Dips "highlight moments
    in your video that were either skipped or moments where viewers stopped
    watching" (YouTube Help, *Measure key moments*, above — platform
    documentation).
    Check: the reviewer writes one line per section — "what the viewer has
    at the end of this section that they did not have at its start"; a blank
    line fails. Improve stage: map retention dips to section boundaries and
    route the dipped section's beat to this rule.
    Stages: script, qa, improve

21. **Introduce the concrete case or visual first and derive the general
    principle from it; never open a section with the abstract definition and
    then illustrate.** The secret is "making sure that you resist the
    temptation to start with the general abstract thing"; "pretty much all
    textbooks and explanations do it the other way around ... because that's
    so tempting to do once you understand a topic"; "that bit of curiosity is
    the hook of the video" (Grant Sanderson, Dropbox blog interview, n.d.,
    https://blog.dropbox.com/topics/work-culture/grant-sanderson-channels-his-passion-for-math-into-marvelously-i
    — other). "When we put the visuals first, then start to articulate the
    meaning, you can get a sense of ownership over it" (The Stanford Daily,
    2020,
    https://stanforddaily.com/2020/01/24/3blue1brown-creator-grant-sanderson-15-talks-engaging-with-math-using-stories-and-visuals/
    — other). For a pipeline that keyframes from the script this also decides
    what the first frame of each section shows.
    Check: classify each section's first sentence as CONCRETE (named object,
    place, number, event, person) or ABSTRACT (definition, category, "X is
    the study of"); fail a section that opens ABSTRACT and introduces its
    first concrete instance more than ~20 s later.
    Stages: script, keyframe

22. **Withhold the central reveal until the viewer understands why it is hard
    or surprising; put the scale, constraint or failed attempt before the
    result.** "I didn't want the photo to be the first thing that people
    see"; "if you don't have a sense of how small and how far away this
    thing is, you're not going to be impressed"; around the reveal "I needed
    to create a bit of a reflective moment" (Fong, The Open Notebook,
    https://www.theopennotebook.com/2020/01/07/videogram-how-a-vox-video-explains-the-science-behind-the-first-photo-of-a-black-hole/
    — practitioner). The mechanism is Loewenstein's: curiosity requires some
    prior knowledge to notice the gap (Loewenstein 1994, above —
    peer-reviewed; characterisation from the abstract and citation record,
    the PDF being a scan). The setup is what manufactures the stakes.
    Check: the reviewer locates the film's main reveal (the THEREFORE beat)
    and confirms that at least one preceding beat establishes a constraint,
    scale or failed attempt that makes it non-obvious; fail if the payoff
    fact appears in full before any difficulty is established.
    Stages: script, qa

23. **Plan explicit re-hooks: one where the opening's promise hands over to
    the body (around 1:00) and one at each later predictable drop point,
    each raising or restating a question rather than summarising — as
    defaults to tune from the channel's own curves, not as a cadence.** The
    only documented production schedule: "After the first minute of content
    you will have what we call minutes 1 thru 3 ... this is where you have to
    transition from hype to execution"; "a 3 minute re-engagement"; "usually
    at the 6 minute mark we will include another re-engagement" (MrBeast
    memo via Jarvis,
    https://www.alexanderjarvis.com/memo-how-to-succeed-in-mrbeast-production/
    — other, unauthenticated, 10–20 minute spectacle format). Hooking is "a game of hooking people nonstop" and "I just write
    another hook at the end" (Lawrence, Creators on Air transcript, above).
    Blackman treats each listicle segment as a mini video with its own hook,
    stakes and resolution (*Retention Review #5*, above). YouTube: if top
    moments occur late, introduce the compelling content earlier, "since
    audience sizes typically decrease over the video's length" (YouTube
    Help, *Measure key moments*, above). Low confidence; no public dataset
    supports any cadence.
    Check: `beats[].rehook` (bool), required at the first section boundary
    and at least once per three minutes of runtime; each rehook beat opens a
    question id. Post-publish: a dip with no planned rehook in the 20 s
    before it becomes a finding for script.
    Stages: script, improve, plan.beats

24. **Keep the narration moving between ideas without sprawling across
    topics, and put the biggest jumps and widest coverage toward the end — a
    pacing dial to calibrate on the channel's data, not a law.** Measuring
    texts as paths through embedding space, higher semantic speed predicted
    higher IMDb ratings for TV episodes (+0.072 per SD, n = 12,336) while
    higher volume (ground covered per chunk) predicted lower (−0.082), both
    "driven by changes that happen toward the end of the text"; movies show
    the speed effect only, and academic papers reverse both (Olivier Toubia,
    Jonah Berger and Jehoshua Eliashberg, *How quantifying the shape of
    stories predicts their success*, PNAS, 2021,
    https://faculty.wharton.upenn.edu/wp-content/uploads/2016/11/How-Quantifying-the-Shapes-of-Stories-Predicts-their-Success.pdf
    — peer-reviewed; Knowledge at Wharton interview, 2021,
    https://knowledge.wharton.upenn.edu/?p=20927 — other). Effects are small
    (≈0.05–0.08 rating points per SD) and correlational; explainers sit
    between entertainment and exposition.
    Check: embed the narration in ~100-word windows; compute mean
    consecutive distance (speed) and hull volume per minute; flag scripts in
    the bottom decile of speed (dwelling) or top decile of volume (sprawl)
    relative to the channel's own corpus, and flag a last quarter that is the
    slowest segment. Thresholds come from the improve stage.
    Stages: script, improve

25. **Front-load scene-setting, move the middle through consequences and
    actors, and concentrate reasoning and realisation in the final third, so
    explanatory depth rises section by section.** Across ~40,000 traditional
    and ~20,000 non-traditional narratives (including TED talks and science
    reporting) function-word trajectories show three phases — staging, plot
    progression, cognitive tension — with cognitive-process words ("think",
    "realize", "because") rising toward the climax (Ryan L. Boyd, Kate G.
    Blackburn and James W. Pennebaker, *The narrative arc*, Science
    Advances, 2020, https://doi.org/10.1126/sciadv.aba2196 — peer-reviewed;
    abstract and press coverage at
    https://spacedaily.com/invisible-words-reveal-common-structure-among-stories-999/
    — other). This is descriptive — what texts do, good and bad alike — not
    a tested prescription; the prescription is this corpus's house default.
    Check: per-section ratio of cognitive-process words (because, so, means,
    realize, turns out, therefore) to total words; warn if the opening
    section has the highest ratio or the ratio does not rise across
    sections. Reviewer: does each section raise the stakes or the
    explanatory depth over the previous one?
    Stages: script, qa

26. **Give the back half a cost: after the first answer, a complication the
    viewer pays for, and an ending that returns to the opening situation
    changed.** Harmon's eight steps — comfort, want, unfamiliar situation,
    adapt, get it, "pay a heavy price for it", return, changed (Dan Harmon's
    story circle as summarised on Wikipedia,
    https://en.wikipedia.org/wiki/Dan_Harmon — other; the original Channel
    101 pages returned HTTP 402). Steps 5–6 are what produce a BUT in the
    back half of an 8–20 minute film, which otherwise runs out of
    complications after the first reveal. Sanderson's stated ending — that
    "the kind of problem solving abilities and patterns that you're coming to
    recognize" become useful (Perell podcast, above) — is a creator example
    of step 8. No empirical test of the circle exists.
    Check: plan beats tagged with circle steps 1–8; fail if step 2 (the
    want/question), step 6 (a cost or complication after the first answer)
    or step 8 (return changed) has no beat.
    Stages: script, plan.beats

## The ending

27. **End within a few seconds of the final payoff: no recap, no sign-off,
    no heart-to-heart; at most a one-line pointer to the next film, then
    cut.** "The video endings must always be abrupt to protect retention";
    the arc ends with "a satisfying payoff at the end of the video with an
    abrupt ending" (*How to Succeed in MrBeast Production*, leaked handbook
    PDF, 2024,
    https://cdn.prod.website-files.com/6623b7720b009050313e701c/66ede69453b7bbadcd2f05a8_How-To-Succeed-At-MrBeast-Production%20(2).pdf
    — other; pp. 29 and 35 of 36). "Once the final bit of value has been
    delivered, end the video. Your outro should be your Call To Action and
    nothing more. Send them to another video and stop talking"; "Retention
    can absolutely bomb in the final 15s" (George Blackman, *5
    Retention-Killing YouTube Mistakes To Avoid*, Oct 2022,
    https://georgeblackman.substack.com/p/5-retention-killing-youtube-mistakes
    — practitioner; an assertion, no chart in that section). Wistia: the
    tail is "a clear signal to the viewer that the 'valuable' content is
    finished ... we often see a noticeable drop-off here"; "avoid recapping
    everything you just said" (Wistia, *Understanding Audience Retention*,
    above — vendor, unquantified for the tail). Practitioner consensus, not
    published tail curves.
    Check: count seconds and words of narration after the last sentence that
    introduces new information or resolves a promise; flag over ~10 s or 25
    words. Lexical flag on "thanks for watching", "that's it for today", "in
    this video we saw".
    Stages: script, build, qa

28. **Never signal that the ending is coming except to build toward the
    final payoff.** "Don't ever signal the end of the video unless it's to
    build hype for the prize or payoff at the end of the video" (MrBeast
    handbook, above, p. 8 — other). "Don't use phrases like 'in summary,'
    'that's about it,' or 'to wrap things up.' This is an indicator that the
    good information is over" (Wistia, *Understanding Audience Retention*,
    https://wistia.com/learn/marketing/understanding-audience-retention —
    vendor). "Finally" and "before we go" are house extensions, not
    in either source, and "finally" must be exempted when it enumerates.
    Check: regex over the last 20% of the script for end-signal phrases; any
    hit must be followed by a payoff beat (promise resolution), not a recap
    or a call to action.
    Stages: script, qa

29. **Put the biggest promised payoff last; if the payoff viewers clicked
    for lands early or in the middle, foreshadow a further one or end the
    film there.** "If your payoff happens at the end of your video ... end the
    video. If it happens in the middle, set up/foreshadow another one.
    Otherwise, people have nothing concrete to stick around for" (George
    Blackman, *Retention Review #11*, 2023,
    https://georgeblackman.substack.com/p/rr11-were-all-trapped —
    practitioner). In a 51.7%-average-viewed video "the SECOND payoff is
    never hinted at, which might explain the second half of the graph": an
    unannounced second payoff makes a late spike but does not hold the
    audience between payoffs (Blackman, *Retention Review #4*, 2023,
    https://georgeblackman.substack.com/p/retention-review-4-second-payoff —
    practitioner). "Strong payoffs at the end of videos boost retention"; in
    stair-stepping formats "the payoff of the world record is at the end"
    (MrBeast handbook, above, p. 29 — other). "The first commandment of all
    temporal art is: Save the best for last" (McKee via Sivers, above —
    book). A hint suffices; the sources do not demand an explicit promise or
    an immediate one.
    Check: from the plan, the beat that resolves `promise` must sit in the
    final section, or a later beat must introduce a new explicit question or
    stake within one section of it. Reviewer rubric: "what concrete thing is
    the viewer waiting for at minute N?" must have an answer for every
    section after the promise is paid.
    Stages: script, qa, plan.promise

30. **The final narrated beat is at or near the film's peak intensity:
    memory of the whole is dominated by its peak and its end, not its
    length.** With film clips, "an unweighted combination of the peak
    discomfort in the series and of the terminal discomfort accounted for 94%
    of the variance in global evaluations. The factor of duration, although
    significant, accounted for only 3% of additional variance", and affect
    during "the perceived ending of a social episode is weighted heavily"
    (Barbara L. Fredrickson and Daniel Kahneman, *Duration Neglect in
    Retrospective Evaluations of Affective Episodes*, JPSP, 1993,
    https://bear.warrington.ufl.edu/brenner/mar7588/Papers/fredr-kahneman-jpsp1993.pdf
    — peer-reviewed). A meta-analysis of 174 effect sizes finds the peak-end
    effect large (r = 0.581, 95% CI 0.487–0.661) and the duration effect
    "essentially nil" (Balca Alaybek et al., *All's well that ends (and
    peaks) well?*, OBHDP, 2022,
    https://ideas.repec.org/a/eee/jobhdp/v170y2022ics0749597822000334.html —
    peer-reviewed). Subjects chose to repeat a longer cold-water trial with a
    better end (Kahneman, Fredrickson, Schreiber and Redelmeier, 1993, cited
    via https://en.wikipedia.org/wiki/Peak%E2%80%93end_rule — other). Never
    tested against YouTube ratings or retention; the transfer is an
    inference.
    Check: the reviewer scores each beat's intensity 1–5; the final narrated
    beat scores at or above the film's median and within one point of the
    peak. Sound and VFX: the strongest music cue and the key image belong on
    the final beat, not on a sponsor read or an administrative outro.
    Stages: script, sound, vfx, qa

31. **Do not pad the ending for watch time: duration barely affects
    remembered satisfaction, longer videos complete less, and YouTube
    counts survey-rated "valued watchtime".** Across 5.3 million videos
    "duration alone explains more than 58% of the variance of watch
    percentage. Intuitively, longer videos are less likely to be fully
    watched" (Siqi Wu, Marian-Andrei Rizoiu and Lexing Xie, *Beyond Views*,
    ICWSM, 2018, https://arxiv.org/abs/1709.02541 — peer-reviewed). "We don't
    want viewers regretting the videos they spend time watching"; "only
    videos that you rate highly with four or five stars are counted as
    valued watchtime" (Cristos Goodrow, *On YouTube's recommendation
    system*, 2021,
    https://blog.youtube/inside-youtube/on-youtubes-recommendation-system/ —
    platform documentation); surveys "help the system understand
    satisfaction, not just watch time" (YouTube Help, *How YouTube
    recommendations work*, 2025,
    https://support.google.com/youtube/answer/16089387 — platform
    documentation). Duration neglect as in rule 30.
    Check: narration after the payoff beat ≤ 5% of runtime; retime inserts no
    stretched pauses in the final section to hit a length target. Improve:
    compare average percentage viewed of films with short and long tails.
    Stages: script, retime, improve

32. **End with full closure and a concrete next question, not a cliffhanger
    tease.** In a lab experiment (N = 133, serial drama with and without
    cliffhanger endings, self-report plus electrodermal activity and
    cortisol) "cliffhangers lead to higher arousal but do not increase
    enjoyment or the intention to continue watching" (Dominique S. Wirz,
    Alexander Ort, Björn Rasch and Andreas Fahr, *The role of cliffhangers in
    serial entertainment*, Psychology of Popular Media, 2023,
    https://sonar.ch/unifr/documents/319990 — peer-reviewed; TV drama, not
    factual video). Closure needs the presiding questions answered (Klauk et
    al., rule 8). An unresolved ending risks the post-watch rating without
    buying continuation; a next-question hand-off keeps closure and still
    points forward.
    Check: the ledger (rule 8) is balanced inside the film; a
    forward-pointing final line may raise a new question only when it is
    labelled as the next film's promise (`next_film`), never as this film's
    unresolved loop.
    Stages: script, plan.next_film

33. **Close the final section with one short takeaway line and take the
    music out on it; hold a beat after the central idea so it lands.** Song
    starts and stops "are cues to viewers — emotional cues to viewers, but
    also sort of attention cues ... they can help point people to main points
    if you sort of end the track at the right time"; a plain "that's right"
    beat after the big idea stays in because "you can't really afford to be
    too clever or beautiful when you're doing voice-over" and "there's a
    really high risk of charging forward and not realizing that the viewer
    is not with you" (Fong, The Open Notebook,
    https://www.theopennotebook.com/2020/01/07/videogram-how-a-vox-video-explains-the-science-behind-the-first-photo-of-a-black-hole/
    — practitioner). Single practitioner; low confidence.
    Check: the final section's last narrated sentence is ≤ 20 words and
    contains the answer to the promise; the sound stage ends the music bed
    within 1 s of that line; retime inserts a 0.5–1.5 s hold after the
    central-idea line.
    Stages: script, sound, retime

34. **For bleak or cosmic-scale subjects, return to the viewer's own scale
    with an earned, specific reason to care — after the hard part, never
    instead of it.** Kurzgesagt's founder, as quoted in a search snippet of
    an interview this pass could not open: "If you zoom out further, nothing
    matters. But then you need to get back to the lives we are living" (Dani
    Di Placido interviewing Philipp Dettmer, Forbes, 2024,
    https://www.forbes.com/sites/danidiplacido/2024/10/24/kurzgesagt-in-a-nutshell-creator-talks-youtube-tiktok-and-optimistic-nihilism/
    — practitioner interview, unverified). A third-party analysis describes
    the same five-step arc — the pivot, then the release — and argues "this
    structure works because it earns the comforting conclusion. Unearned
    optimism feels fake" (Devon Harrison, Livecounts, 2026,
    https://livecounts.io/blog/case-studies/kurzgesagts-existential-hook-optimistic-nihilism-as-a-branding-strategy
    — practitioner). Low confidence; no primary Kurzgesagt process document
    on endings was found.
    Check: when the film's stakes are large-scale, the final section
    contains a sentence addressed to the viewer's scale ("you", "your", daily
    life) and it comes after, not instead of, the hardest claim; flag endings
    whose last three sentences are generic positivity with no specific fact.
    Stages: script

35. **Reserve the last 5–20 seconds as a quiet picture-and-music tail for
    the end screen: no new narration or on-screen text, one spoken hand-off
    to one named next film, at least 10 seconds of uncluttered picture.**
    "End screens can be added to the last 5–20 seconds of a video"; the
    video must be at least 25 s long; up to four elements; "consider the
    video's last 20 seconds when editing it" and "leave enough space and
    time at the end of the video" (YouTube Help, *Add end screens to
    videos*, 2025, https://support.google.com/youtube/answer/6388789 —
    platform documentation; launch post: Muli Salem, *Keep 'Em Watching with
    End Screens*, 2016,
    https://blog.youtube/news-and-events/keep-em-watching-with-end-screens/ —
    platform documentation). This reconciles with rule 27: the narration ends
    on the payoff, then the tail runs on picture and music. Elements cover
    the lower and centre of the frame, so keyframes there carry nothing.
    Check: film length ≥ 25 s and a tail segment of 10–20 s flagged
    `end_screen_safe`; VFX and keyframe stages place no text or focal detail
    in the end-screen element zones during it; the script's tail contains at
    most one sentence and it names a specific next film; the music continues
    under the tail and resolves at the cut.
    Stages: script, keyframe, vfx, sound, build

36. **Hand off to one specific next film that continues the question.**
    YouTube optimises "not only on the next view, but also successive views
    thereafter" and "for how a video contributes to a longer viewing
    session" (Eric Meyerson, *YouTube Now: Why We Focus on Watch Time*, 2012,
    https://blog.youtube/news-and-events/youtube-now-why-we-focus-on-watch-time/
    — platform documentation), and today "our system uses the video you're
    currently watching as the main signal when suggesting a video to play
    next" (YouTube Help, *How YouTube recommendations work*, above). "Send
    them to another video and stop talking" (Blackman, *5 Retention-Killing
    Mistakes*, above). "Session time" as a per-video ranking factor is a
    2012 statement, not a documented current metric.
    Check: plan field `next_film` (slug of an existing or planned film whose
    promise follows from this film's payoff); the end-screen element and the
    single spoken hand-off reference it; the reviewer rejects a generic
    "check out our other videos". Improve: track end-screen element click
    rate per film.
    Stages: script, build, improve, plan.next_film

37. **Keep the channel a repeatable format — same narrator persona, same
    opening and ending device, same thumbnail grammar, one connected topic
    — and register films as a series playlist or show.** "A show is a
    collection of high-quality episodes with a clear connection across
    episodes ... Feature the same characters or hosts, tell one long story,
    or talk about a similar topic in each video. Use the same intro, same
    style of thumbnails, and same video format"; shows may surface under
    "Recommended shows" and "Continue watching" (YouTube Help, *Create a show
    from a playlist*, 2026, https://support.google.com/youtube/answer/16590581
    — platform documentation). A series playlist "allows other videos in the
    playlist to be featured and recommended" (YouTube Help, *Series
    playlists*, 2025, https://support.google.com/youtube/answer/6084043 —
    platform documentation). "One way to boost retention on a video is to
    have a good format for the video to follow", with a predictable payoff
    position (MrBeast handbook, above — other). No published measurement of
    the recommendation lift.
    Check: `work/house.md` carries a `format` block (narrator persona,
    recurring device for opening and ending, thumbnail grammar, series
    topic); the reviewer checks each plan against it; the build stage adds
    the upload to the series playlist or show and sets serial or non-serial
    order.
    Stages: ideas, styles, script, voice, build

## Reading the retention curve back into the script

38. **Treat the Intro figure as the opening's measured diagnostic, and judge
    it only against like-for-like data.** YouTube reports Intro (% still
    watching after 30 s) once a video is ≥ 60 s with ≥ 100 views, defines no
    pass threshold, and compares "typical retention" against the channel's
    ten latest videos of similar length as a whole curve (YouTube Help,
    *Measure key moments*, above — platform documentation); reading that
    curve at 0:30 is a house convention. "If your overall retention looks
    lower than normal, it could be that your video got a lot of views from
    external traffic" (The YouTube Team, *Master these 4 metrics*, 2025,
    https://blog.youtube/creator-and-artist-stories/master-these-4-metrics/ —
    platform documentation). A 14%-retention video with 1.1M views had "86%
    of the views ... from ranking in google search"; "always take retention
    with a grain of salt and, when possible, use controls" (Paddy Galloway,
    2023, https://x.com/PaddyG96/status/1638511154744942594 —
    practitioner). Engagement must be calibrated against video properties;
    across 5.3M videos a "relative engagement" measure was stable over time
    and largely predictable from context (R² = 0.77), so a raw watch
    percentage is not a hook score (Wu, Rizoiu and Xie, above —
    peer-reviewed).
    Check: `yt improve` emits a hook finding only when retention at 30 s is
    below the median of the channel's last ten videos of similar length and
    the share of views from search or external traffic is below a threshold
    (or when it reads per-traffic-source retention); `yt qa` reports
    retention at 30 s and 60 s and the 0:30–1:00 slope as separate fields.
    Stages: qa, improve

39. **Spend more drafting passes and the strictest review on the first 60
    seconds than on any other minute.** "The first minute of each video is
    the most important minute of each video ... we lost 21 million viewers in
    the first minute" of 60 million (MrBeast memo via Jarvis, above —
    other). "The outsized returns are in making better intros. Aim to hold an
    extra 10% at the 30 second mark" (Galloway, 2022 thread, above —
    practitioner). One vidIQ video fell "from 95% in the opening seconds to
    about 65% by the 30-second mark" then flattened (Lydia Sweatt and Darryl
    Rentz, vidIQ, 2026,
    https://vidiq.com/blog/post/increase-audience-retention-youtube/ —
    vendor). The shape is consistent across sources; every percentage is
    channel-specific.
    Check: the script reviewer runs at least two passes on the first 60 s,
    one for promise match and one for question and stakes.
    Stages: script, qa

40. **Read the ending's tail as a QA metric: a final-5% cliff steeper than
    the preceding slope is an outro problem, a late spike is a payoff that
    should have been promised earlier, and a final point harder than the
    film's median is overload.** "Dips mean that viewers are abandoning or
    skipping at that specific part of your video"; "Spikes appear when more
    viewers are watching, rewatching, or sharing those parts"; "Videos on
    YouTube generally taper off during the playback period" (YouTube Help,
    *Measure key moments*, https://support.google.com/youtube/answer/9314415 — platform
    documentation). A spike at ~8
    minutes "is the second payoff we've been looking for, but weren't told
    about" (Blackman, *Retention Review #4*, above); a video "cruising on a
    nice 60% until the final point we made, where retention dropped off
    rapidly" because that point "was probably 1 or 2 steps beyond where most
    of the audience was" (Blackman, *5 Retention-Killing Mistakes*, above,
    mistake 5 — practitioner). Tail loss "is highest for videos 10 - 20
    minutes in length" (Wistia, *Understanding Audience Retention*, above —
    vendor).
    Check: improve stage pulls the retention curve, computes the slope of
    the last 5% against the slope of 75–95%, and files a finding routed to
    script ("outro too long / recap") when it is ≥ 2× steeper; any spike
    after the promise is paid becomes an "un-foreshadowed payoff" finding;
    the final point is checked against the film's median difficulty.
    Stages: qa, improve

---

## Contested

What is repeated as settled and is not. Each line says what the evidence
read here says.

- **Open loops work because of the Zeigarnik effect.** A 2025 meta-analysis
  (Ghibellini and Meier, Humanities and Social Sciences Communications,
  https://www.nature.com/articles/s41599-025-05000-w) found no memory
  advantage for unfinished tasks and concludes the effect "lacks universal
  validity"; only the tendency to resume interrupted tasks (Ovsiankina)
  replicated. Justify loops by the information-gap and prequestion evidence
  (rules 14 and 19), not by Zeigarnik.
- **Cliffhangers and unresolved teases keep viewers watching.** Wirz et al.
  2023 (rule 32) found higher arousal and no increase in enjoyment or
  intention to continue; a survey-based VoD study claims the opposite but is
  correlational. Evidence for "end a section on a tease" is weak to null;
  evidence for "answer what you asked" is stronger.
- **Olson's Narrative Index with thresholds (above 20 good, below 10 bad).**
  Blog and press interviews only; thresholds shift between posts; the UMCES
  "has shown" claim carries no data. Usable as a soft lint, not a gate.
- **ABT is proven by experiment.** No randomised test of ABT against AAA
  structure was found; Olson's book rests on general narrative-processing
  research, which supports narrative over lists but not the template.
- **Every film needs three acts or a hero's journey.** Bernard says
  documentaries need not have three acts; Fong says Vox tried to standardise
  episode structure and "it just didn't work out because every story has its
  own needs". Three acts are a practitioner default (the South Park
  whiteboard), not a tested requirement.
- **Faster pacing is always better.** Toubia et al. (rule 24): the sign of
  semantic speed flips between entertainment and exposition, effects are
  small and correlational, and a 2026 study applying the same measures to
  28,606 books (Zimmerman, *Semantic Novelty at Scale*, arXiv 2602.20647 —
  preprint, not a replication of Toubia et al.) finds circuitousness 93%
  correlated with length, so shape effects can be length confounds.
- **The hook must land in the first 3, 5, 8, 10 or 15 seconds.** Every
  vendor picks a different number (vidIQ 15 s, Tayo Aina 10 s, others 3 s).
  YouTube's only published structural window is 30 s (Intro) and its
  recommendation guide says only "the initial seconds". The 3b1b criterion
  is also 30 s.
- **70% (or 75%) retention at 30 seconds is the benchmark.** Galloway's 70%
  is a figure he says he reaches for clients, with the caveat that retention
  depends on context; third-party sites repeat 70–75% with no data. The
  only platform threshold is the 50%-at-30 s cut for the "above typical
  intros" list on the Content tab tips page (answer/12942217, read for
  `judging-narrative.md` rule 1), and it is a list filter, not a target.
- **Most viewers leave in the first 30 seconds: 30% / 33% / 40% / 50%.**
  Galloway says 30–50%, vidIQ shows 95% to 65% on one video, Wistia's nose
  loss is 4.9–17.3% over the first 2% of runtime on business video, Tayo
  Aina says 40% in 10 s with no source. The shape is robust; every number
  is channel-specific or unsourced.
- **Re-hook every 2–3 minutes.** Vendor studies with no method shown. The
  MrBeast 3- and 6-minute marks are one unauthenticated memo for a spectacle
  format. A tunable default (rule 23), not a rule.
- **Cold open / in medias res is always best.** YouTube lists it as one of
  four equal openings; the ABCD data is from skippable ads; Blackman
  documents a wordless cold open that failed for lack of stakes.
- **A bigger curiosity gap always wins.** The Upworthy registered report
  (rule 13) shows vagueness loses once a baseline is already concrete, and
  Scacco and Muddiman (cited there) found question headlines slightly
  reduced intention to read. The gap must be specific.
- **"Preview what's coming" versus "never say what you're going to say".**
  YouTube (2011, 2014) says make the subject clear early and tease the rest;
  Lawrence says a list of points is not a hook. Reconcilable: orient on the
  subject, withhold the conclusions (rule 13).
- **The MrBeast memo proves anything.** Posted by Rosanna Pansino, confirmed
  anonymously by two former producers, not acknowledged by MrBeast; the copy
  cited is itself a reproduction; its retention numbers are one channel's.
- **Early retention or the Intro figure is a ranking signal.** Vendor blogs
  only. YouTube's recommendation guide names engagement as one of three
  signal buckets and never isolates the intro; its 2021 post names clicks,
  watch time, survey responses, shares, likes and dislikes.
- **"Thanks for watching / like and subscribe" kills retention.** Widely
  repeated and consistent with single-creator curves; no controlled or
  large-N study isolates the sign-off.
- **Recaps at the end always hurt.** True of entertainment and marketing
  video per Blackman and Wistia; for educational content a brief
  retrieval-style recap might aid learning (testing-effect literature, not
  read here). "No recap" is a default, not a law.
- **The peak-end rule applies to YouTube satisfaction.** Strongly supported
  for affective episodes including film clips; never tested against YouTube
  survey ratings or retention.
- **"Endings must always be abrupt" transfers to factual explainers.**
  Stated for challenge formats; Kurzgesagt's films end with a reflective
  pivot and a sponsor segment. Rule 35's shape (abrupt narration, quiet
  end-screen tail) is a synthesis, not a sourced claim.
- **"Session time" is a per-video ranking factor; end-screen click-rate
  benchmarks (3–7%, 5–8%); a spoken CTA beats a silent one.** The first is a
  2012 statement absent from current help pages; the second two are vendor
  numbers with no methodology.
- **Harmon's circle is in "every TV show and film there is"; Boyd's arc is
  a success recipe.** The circle is a Campbell derivative on a wiki with no
  empirical test; Boyd et al. describe a statistical average found in good
  and bad texts alike.
- **Veritasium's "formula" (misconception → question → explanation, with an
  A-plot/B-plot cut when it gets technical).** Found only in an AI-generated
  summary of a third-party video that sells a course. The misconception-first
  part is backed by Muller's thesis work (rule 15); the A/B-plot part is
  unverified.
- **Save the Cat's Final Image is "the opposite of" the Opening Image.** All
  sources are secondary paraphrases; Snyder's wording was not checked.

---

## Folklore and open questions

Rules or figures that could not be sourced to a page that was read, and what
would settle them. None of the research findings in scope were refuted
outright; everything moved here is unverified, not disproved.

- **"By the 30-second mark you need to introduce a new question" and the
  0–7 / 7–27 / 28–30 s hook segments** attributed to Colin and Samir
  (https://x.com/ColinandSamir/status/1903970727537365334). The post returned
  HTTP 402; the wording is from a search snippet. Verify before it enters
  `house.md`. Their paid playbook says publicly only that "the first 30
  seconds of your video define whether or not your viewers will stick
  around".
- **"Most YouTubers lose 30–50% of viewers in the first 30 seconds"** (Paddy
  Galloway, March 2022 thread). No URL was captured; unverified.
- **"More than two frames in the first five seconds" and "tightly-framed
  shots of people or product at the beginning"** (Google/YouTube ABCD
  reference guide, 2019,
  https://services.google.com/fh/files/misc/youtube_google_abcd_reference_guide_en.pdf).
  The PDF was not text-readable; wording is from a search summary and the
  PPC Land secondary. Ad research in any case.
- **"Above typical intros" = 50% or more still watching at 30 s.** Resolved:
  it is not on answer/9314415 but is on the Content tab tips page
  (answer/12942217), read for `judging-narrative.md` rule 1. It is the
  filter for a list in Studio, not a benchmark.
- **Johnny Harris on promise, payoff and "let me show you".** The How I
  Write transcript is behind a Podwise sign-in; only chapter titles were
  verified (rule 6). His Bright Trip lessons and Adobe MAX 2025 session are
  the other primary venues.
- **Philipp Dettmer's "get back to the lives we are living."** Forbes
  returned 403; quoted from a search snippet (rule 34). Kurzgesagt's
  published process material covers research and fact-checking, not
  structure or endings.
- **Dan Harmon's own wording of the story circle.** Channel 101 "Story
  Structure 101–104" returned HTTP 402; the eight steps are from Wikipedia
  (rule 26).
- **Muller and Sharma 2012 (IISME) and the Sydney thesis.** Both 403; the
  6/26 → ~12/26 numbers rest on THE Journal's account. The peer-reviewed
  JCAL and Science Education papers (rule 15) were confirmed via Crossref
  abstracts only; pull full texts for exact effect sizes.
- **Loewenstein 1994 full text.** The CMU PDF is a CCITT scan with no text
  layer; the byrdseed.com copy has one and was read for
  `attention-and-stakes.md` (rules 1–3), which confirms the four situational
  triggers (pp. 90–91) and the guess-and-feedback experiments (pp. 91–92).
  The "curiosity requires prior knowledge" claim (rule 22) is Loewenstein's
  via Kidd and Hayden 2015, not a direct quotation. Kang et al. 2009's
  inverted-U is in the full paper (r = .44 with P(1−P), n = 19), read for
  the same file.
- **Toftness et al. 2018 percentages; Wirz et al. 2023 effect sizes.**
  Paywalled (PDF 403 / APA PsycNet).
- **Carroll 2007, *Narrative Closure*, Philosophical Studies 135:1–15.**
  Closed access; the definition in rule 8 is quoted via Klauk et al.
- **"Write the hook last, once the body is complete"** (attributed to George
  Blackman). Secondary summary only, not verified in his words; it sits
  beside Lawrence's "hook before the video" (rule 5) and the two are not
  contradictory if the promise is frozen first.
- **Chekhov's gun as a payoff rule.** The Backstage explainer returned 403;
  the principle concerns props and admits red herrings, so it was dropped
  from rule 8 in favour of McKee and Klauk et al.
- **Creator sources not reached before the search budget ran out:**
  Kurzgesagt (no primary statement of script structure); Tom Scott,
  Wendover and Real Engineering (third-party pattern analyses only); Cleo
  Abram's four-step process (pitch → info doc → outline → script; no
  statement on beat order, hook or ending); Sanderson's "5 things" explainer
  video (youtu.be/ojjzXyQCzso); Veritasium's "Clickbait is Unreasonably
  Effective" and the PFAS video that "layers one mystery atop another";
  Fong's NYU "Production Process" talk; the Storybench Howtown interview.
- **"The best YouTube intro according to 63 audience retention graphs"**
  (youtube.com/watch?v=s1jmSon6Z4w). Channel could not be attributed; if real,
  the closest thing to a data study on intro styles.
- **No study links narrative structure, hook type or ending shape to
  YouTube audience-retention curves.** The nearest data are MOOC engagement,
  Upworthy click-through and single-channel curves. Every threshold above is
  a prior until the improve stage logs hook type, question, stakes, payoff
  position and tail length per film and regresses retention on them.
- **Does a short recap help factual video?** Untested on YouTube; a
  recap-versus-no-recap tail test on the channel's own films would settle
  it.
- **Do shows and series playlists measurably raise next-episode views?**
  YouTube says episodes "may be eligible"; nothing published.

---

## Sources

Deduplicated; grouped by kind. "Via" marks a secondary copy where the primary
was not read.

### Peer-reviewed

- Alaybek, B. et al. *All's well that ends (and peaks) well? A meta-analysis of the peak-end rule and duration neglect.* OBHDP 170, 2022. https://ideas.repec.org/a/eee/jobhdp/v170y2022ics0749597822000334.html
- Boyd, R. L., Blackburn, K. G., Pennebaker, J. W. *The narrative arc: Revealing core narrative structures through text analysis.* Science Advances 6(32), 2020. https://doi.org/10.1126/sciadv.aba2196
- Crouch, C., Fagen, A. P., Callan, J. P., Mazur, E. *Classroom demonstrations: Learning tools or entertainment?* American Journal of Physics 72(6), 2004. https://doi.org/10.1119/1.1707018
- Dahlstrom, M. F. *Using narratives and storytelling to communicate science with nonexpert audiences.* PNAS 111 (Suppl 4), 2014. https://pmc.ncbi.nlm.nih.gov/articles/PMC4183170
- Fredrickson, B. L., Kahneman, D. *Duration Neglect in Retrospective Evaluations of Affective Episodes.* JPSP 65(1), 1993. https://bear.warrington.ufl.edu/brenner/mar7588/Papers/fredr-kahneman-jpsp1993.pdf
- Ghibellini, R., Meier, B. *The Zeigarnik effect: a meta-analysis.* Humanities and Social Sciences Communications 12:962, 2025. https://www.nature.com/articles/s41599-025-05000-w (read for `attention-and-stakes.md`; cited here in Contested)
- Kang, M. J. et al. *The Wick in the Candle of Learning: Epistemic Curiosity Activates Reward Circuitry and Enhances Memory.* Psychological Science 20(8), 2009. https://authors.library.caltech.edu/22280/
- Klauk, T., Köppe, T., Onea, E. *More on narrative closure.* Journal of Literary Semantics 45(1), 2016. https://www.degruyterbrill.com/document/doi/10.1515/jls-2016-0003/html (abstract)
- Loewenstein, G. *The Psychology of Curiosity: A Review and Reinterpretation.* Psychological Bulletin 116(1), 1994. https://www.cmu.edu/dietrich/sds/docs/loewenstein/PsychofCuriosity.pdf (scan); abstract at https://stafforini.com/works/loewenstein-1994-psychology-curiosity-review/
- Muller, D. A., Bewes, J., Sharma, M. D., Reimann, P. *Saying the wrong thing: improving learning with multimedia by including misconceptions.* Journal of Computer Assisted Learning 24(2), 2008. https://doi.org/10.1111/j.1365-2729.2007.00248.x (abstract)
- Muller, D. A., Sharma, M. D., Reimann, P. *Raising cognitive load with linear multimedia to promote conceptual change.* Science Education 92(2), 2008. https://doi.org/10.1002/sce.20244 (abstract)
- Toftness, A., Carpenter, S., Lauber, S., Mickes, L. *The Limited Effects of Prequestions on Learning from Authentic Lecture Videos.* JARMAC 7(3), 2018. https://pure.royalholloway.ac.uk/en/publications/the-limited-effects-of-prequestions-on-learning-from-authentic-le/
- Toubia, O., Berger, J., Eliashberg, J. *How quantifying the shape of stories predicts their success.* PNAS 118(26), 2021. https://faculty.wharton.upenn.edu/wp-content/uploads/2016/11/How-Quantifying-the-Shapes-of-Stories-Predicts-their-Success.pdf
- (Registered report, Upworthy Research Archive.) *When curiosity gaps backfire: effects of headline concreteness on information selection decisions.* 2024. https://pmc.ncbi.nlm.nih.gov/articles/PMC11704130/
- Zimmerman, W. F. *Semantic Novelty at Scale: Narrative Shape Taxonomy and Readership Prediction in 28,606 Books.* arXiv 2602.20647, 2026 (preprint; cited in Contested)
- Wirz, D. S., Ort, A., Rasch, B., Fahr, A. *The role of cliffhangers in serial entertainment.* Psychology of Popular Media 12(2), 2023. https://sonar.ch/unifr/documents/319990
- Wu, S., Rizoiu, M.-A., Xie, L. *Beyond Views: Measuring and Predicting Engagement in Online Videos.* ICWSM, 2018. https://arxiv.org/abs/1709.02541

### Platform documentation

- Goodrow, C. *On YouTube's recommendation system.* YouTube Official Blog, 2021. https://blog.youtube/inside-youtube/on-youtubes-recommendation-system/
- McNulty, D., Kaye, J. *Four tips to hook your viewers on YouTube.* YouTube blog, 2014. https://blog.youtube/creator-and-artist-stories/four-tips-to-hook-your-viewers-on/
- Meyerson, E. *YouTube Now: Why We Focus on Watch Time.* YouTube blog, 2012. https://blog.youtube/news-and-events/youtube-now-why-we-focus-on-watch-time/
- Nugent, R. *YouTube Creator Playbook tips: the first 15 seconds.* YouTube blog, 2011. https://blog.youtube/news-and-events/this-is-first-of-series-of-posts/
- Salem, M. *Keep 'Em Watching with End Screens.* YouTube blog, 2016. https://blog.youtube/news-and-events/keep-em-watching-with-end-screens/
- The YouTube Team. *Stop guessing, start growing: Master these 4 metrics.* YouTube blog, 2025. https://blog.youtube/creator-and-artist-stories/master-these-4-metrics/
- YouTube Help. *Add end screens to videos.* 2025. https://support.google.com/youtube/answer/6388789
- YouTube Help. *Create a show from a playlist.* 2026. https://support.google.com/youtube/answer/16590581
- YouTube Help. *How YouTube recommendations work.* 2025. https://support.google.com/youtube/answer/16089387
- YouTube Help. *Measure key moments for audience retention.* https://support.google.com/youtube/answer/9314415
- YouTube Help. *Series playlists.* 2025. https://support.google.com/youtube/answer/6084043
- YouTube Help. *Understand your content performance for YouTube's recommendation system.* 2025. https://support.google.com/youtube/answer/16559650

### Practitioner

- Aina, T. *My "10-Second Rule" for YouTube Video Retention.* 2024. https://tayoaina.com/newsletter/my-10-second-rule-for-youtube-video-retention/
- Blackman, G. *5 Retention-Killing YouTube Mistakes To Avoid.* Oct 2022. https://georgeblackman.substack.com/p/5-retention-killing-youtube-mistakes
- Blackman, G. *My 3-Step framework for writing killer hooks.* 2023. https://www.georgeblackman.com/write-on-time/my-3-step-framework-for-writing-killer-hooks
- Blackman, G. *Retention Review #4 — SECOND Payoff, Context, Result.* 2023. https://georgeblackman.substack.com/p/retention-review-4-second-payoff
- Blackman, G. *Retention Review #5 — Why Should I Care?* Dec 2022. https://georgeblackman.substack.com/p/retention-review-5-why-should-i-care
- Blackman, G. *Retention Review #7 — He fixed retention AFTER uploading.* 2023. https://georgeblackman.substack.com/p/retention-review-7-he-fixed-retention
- Blackman, G. *RR#8 — Study this hook edit.* 2023. https://georgeblackman.substack.com/p/rr8-study-this-hook-edit
- Blackman, G. *Retention Review #11 — We're all trapped.* 2023. https://georgeblackman.substack.com/p/rr11-were-all-trapped
- Clouse, J., Blackman, G. *George Blackman on Creator Science* (podcast). 2024. https://creatorscience.com/podcast/george-blackman/
- Clouse, J., Lawrence, E. *Ed Lawrence (Film Booth) on Growing a YouTube Channel* (podcast). 2023. https://creatorscience.com/podcast/ed-lawrence/
- Coats, E. *Pixar's 22 Rules of Storytelling*, compiled by Aerogramme Writers' Studio. 2013. https://www.aerogrammestudio.com/2013/03/07/pixars-22-rules-of-storytelling/
- Dennison, B. *Narrative literacy for science communication.* UMCES IAN blog, 2017. https://ian.umces.edu/blog/narrative-literacy-for-science-communication/
- Fong, J. (interviewed by Nguyễn, T.) *Videogram: How a Vox Video Explains the Science behind the First Photo of a Black Hole.* The Open Notebook, 2020. https://www.theopennotebook.com/2020/01/07/videogram-how-a-vox-video-explains-the-science-behind-the-first-photo-of-a-black-hole/
- Fong, J. *Video Consortium Mag, Episode 1.* n.d. https://videoconsortium.org/mag/episode-1-joss-fong
- Fritts, E., Muller, D. *Inside Veritasium: An Interview with Dr. Derek Muller.* Creator Handbook, 2017. https://www.creatorhandbook.net/inside-veritasium-an-interview-with-dr-derek-muller-12ad2351a4b3/
- Galloway, P. Retention thread ("how I regularly hit 70% with clients"). X, 2022. https://x.com/PaddyG96/status/1510219133895741443
- Galloway, P. "This video has 14% retention but 1.1 million views." X, 2023. https://x.com/PaddyG96/status/1638511154744942594
- Harris, J., Drew, N. *Visual Storytelling* (course listing; lesson titles only). Bright Trip. https://www.brighttrip.com/courses/visual-storytelling
- Harrison, D. *Kurzgesagt's Existential Hook: Optimistic Nihilism as a Branding Strategy.* Livecounts, 2026. https://livecounts.io/blog/case-studies/kurzgesagts-existential-hook-optimistic-nihilism-as-a-branding-strategy
- Lawrence, E. *Mastering YouTube Strategies with Ed Lawrence* (Creators on Air transcript). 2024. https://www.passionfroot.me/creators-on-air/mastering-youtube-strategies-with-ed-lawrence
- Parker, T., Stone, M. *NYU writing class, 2014* (transcript). Speakola. https://speakola.com/arts/matt-stone-trey-parker-nyu-writing-class-2014
- Perell, D. *The But & Therefore Rule.* n.d. https://perell.com/note/but-therefore-rule/
- Perell, D., Harris, J. *Johnny Harris: Master Storytelling on YouTube* (How I Write; chapter list only). 2025. https://podwise.ai/dashboard/episodes/3299864
- Perell, D., Sanderson, G. *Grant Sanderson: Math for the Masses* (podcast page). n.d. https://perell.com/podcast/grant-sanderson-math-for-the-masses/
- Sanderson, G. *Summer of Math Exposition (SoME1): announcement and judging criteria.* 2021. https://www.3blue1brown.com/blog/some1

### Books (read via publisher pages, reviews or notes)

- Bernard, S. C. *Documentary Storytelling: Creative Nonfiction on Screen*, 5th ed. Routledge, 2022. https://www.routledge.com/Documentary-Storytelling-Creative-Nonfiction-on-Screen/CurranBernard/p/book/9781032267296 — and the review *Story's the Thing*, Documentary magazine. https://documentary.org/feature/storys-thing
- McKee, R. *Story*, 1997. Notes by Derek Sivers. https://sive.rs/book/Story
- Olson, R. *Houston, We Have a Narrative: Why Science Needs Story.* University of Chicago Press, 2015. https://press.uchicago.edu/ucp/books/book/chicago/H/bo21174162.html
- Snyder, B. *Save the Cat!*, 2005. Via Reedsy, *Save the Cat Beat Sheet*, 2024. https://reedsy.com/blog/guide/story-structure/save-the-cat-beat-sheet

### Other (press, vendor data, secondary)

- BPS Research Digest. *Here's a simple way to boost your learning from videos: the "prequestion".* 2017. https://bps.org.uk/research-digest/heres-simple-way-boost-your-learning-videos-prequestion
- Donaldson, J. (attributed). *How to Succeed in MrBeast Production* (leaked handbook PDF, hosted copy). 2024. https://cdn.prod.website-files.com/6623b7720b009050313e701c/66ede69453b7bbadcd2f05a8_How-To-Succeed-At-MrBeast-Production%20(2).pdf
- Donaldson, J. (attributed), reproduced by Jarvis, A. *MEMO: How to succeed in MrBeast production.* 2024. https://www.alexanderjarvis.com/memo-how-to-succeed-in-mrbeast-production/
- Dropbox blog. *Grant Sanderson channels his passion for math into marvelously intuitive explainer videos.* n.d. https://blog.dropbox.com/topics/work-culture/grant-sanderson-channels-his-passion-for-math-into-marvelously-i
- Knowledge at Wharton. *The Science of Blockbusters: What Makes a Good Story* (Berger interview). 2021. https://knowledge.wharton.upenn.edu/?p=20927
- Norris, C., Soloway, E. *Watching Videos Does Not Necessarily Lead to Learning.* THE Journal, 2015. https://thejournal.com/articles/2015/05/05/watching-videos-does-not-necessarily-lead-to-learning.aspx
- PPC Land. *Mastering YouTube advertising with the ABCD framework.* 2024. https://ppc.land/mastering-youtube-advertising-with-the-abcd-framework/
- Romm, J. (quoting Olson, R.) *The viral storytelling trick progressives can learn from Trump.* ThinkProgress, 2018. https://thinkprogress.org/what-trump-can-teach-progressives-about-viral-communications-5d1e468446d0/
- Scientific American (Bechard, D. E.). *How YouTube Star Derek Muller of Veritasium Is Challenging Scientific Misconceptions and Exposing PFAS Contamination.* 2025. https://www.scientificamerican.com/article/how-youtube-star-derek-muller-of-veritasium-is-challenging-scientific/
- SpaceDaily. *Invisible words reveal common structure among stories.* 2020. https://spacedaily.com/invisible-words-reveal-common-structure-among-stories-999/
- Sweatt, L., Rentz, D. *YouTube Audience Retention: What's a Good Rate and How to Improve It?* vidIQ, 2026. https://vidiq.com/blog/post/increase-audience-retention-youtube/
- The Stanford Daily. *3Blue1Brown creator Grant Sanderson '15 talks engaging with math using stories and visuals.* 2020. https://stanforddaily.com/2020/01/24/3blue1brown-creator-grant-sanderson-15-talks-engaging-with-math-using-stories-and-visuals/
- University of Sydney. *An element of truth* (Derek Muller). 2024. https://www.sydney.edu.au/news-opinion/news/2024/11/05/the-element-of-truth.html
- Wikipedia. *Dan Harmon* (story circle section, citing Channel 101). https://en.wikipedia.org/wiki/Dan_Harmon
- Wikipedia. *Peak–end rule* (for Kahneman, Fredrickson, Schreiber and Redelmeier, 1993). https://en.wikipedia.org/wiki/Peak%E2%80%93end_rule
- Wistia (Currier, A., Fishman, E., Marinelli, L.). *Understanding Audience Retention.* 2020. https://wistia.com/learn/marketing/understanding-audience-retention
- Wistia (Lavigne, C., Day, A.). *What Your Video Engagement Graph Is Telling You.* 2026. https://wistia.com/blog/engagement-graph-learnings

### Cited only as unverified (see Folklore)

- Colin and Samir. *Let's talk about the 3 rules of a good YouTube hook.* X, 2025. https://x.com/ColinandSamir/status/1903970727537365334 (HTTP 402)
- Di Placido, D. (interviewing Dettmer, P.) *"Kurzgesagt—In A Nutshell" Creator Talks YouTube, TikTok And Optimistic Nihilism.* Forbes, 2024. https://www.forbes.com/sites/danidiplacido/2024/10/24/kurzgesagt-in-a-nutshell-creator-talks-youtube-tiktok-and-optimistic-nihilism/ (HTTP 403)
- Google/YouTube. *ABCD Reference guide.* 2019. https://services.google.com/fh/files/misc/youtube_google_abcd_reference_guide_en.pdf (not text-readable)
