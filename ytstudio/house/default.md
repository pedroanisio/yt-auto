<!--
The shipped house standards: docs/narrative/ distilled by hand, one section
per reader. Each section's comment lists the corpus rules it states, by file
(structure, attention, direction, judging) and number; tests/test_house_default
checks that every id exists, that its Stages: tag names the section's reader,
that each section stays under 1,500 characters and that none names a look.
The channel's work/house.md and a project's house.md replace these sections
by name. A decision's cache key holds its stage's block (`## all` and its
own section), so an edit to a section re-asks the cached decisions of the
stages that read it; comments never reach a prompt, and an edit to one
re-asks nothing.
-->

## all
<!-- rules: direction 1, 8, 9, 24; structure 21 -->
The narration carries the message on first listen: a viewer who cannot rewind understands each line as it is said, while the picture shows what that line says. Show the concrete case before the abstract rule. One shot carries one new idea.

## script
<!-- rules: judging 13; structure 1, 6, 7, 9, 19, 20, 21, 22, 27; attention 8, 9, 10; direction 16, 21 -->
- State the film's train in one sentence: who or what pursues what, against which obstacle, to answer which question. A topic is not a train.
- Join consecutive beats with but, therefore or because; a beat that only follows "and then" is reworked, cut or moved.
- Write the promise as one sentence and restate its question within the first 30 seconds; the last section pays it in resolved form. Nothing promised goes unpaid.
- Within the first 30 seconds the viewer knows why they should care: the consequence for them or people like them.
- Say what is at risk, and by when, before the mechanism is explained whenever possible; withhold it only when a twist is the point. Later beats put more at risk than earlier ones.
- Each section opens on the specific question it answers and answers it before it ends, or names the later section that does, leaving the viewer something they did not have at its start.
- Open each section on the concrete case and derive the principle from it; never open on a definition.
- Withhold the central reveal until the viewer understands why it is hard.
- End within a few seconds of the final payoff: no recap, no sign-off.
- Say numbers as a person says them: approximate unless the exact figure is the point, and never two in one sentence that ask for arithmetic.
- Keep sentences short enough to say in one breath.

## styles
<!-- rules: direction 4, 9 -->
- Every section has an anchor visual, something that can be shown, named before the script is written; the story is scoped to what can be shown, and a section with nothing to show is cut or merged.
- For every abstract claim the first shot is a specific worked example, simulation or drawing; the definition comes after the viewer has seen it happen.

## vfx
<!-- rules: direction 1, 3, 6, 7, 8, 12 -->
- The picture shows what the narration says while it says it: cut the animation to the narration, not to a fixed grid.
- When a frame holds more than one thing the eye could land on, cue the one being named (a highlight, an arrow, a zoom, a hand drawing it) at its first spoken mention.
- Build every diagram in steps keyed to the narration, one new part per beat, never the finished diagram talked over.
- A shot introduces at most one new claim and one group of simultaneous motion.
- The picture changes when the idea changes and keeps moving with the information; no long static hold while the voice runs on.
- Text the viewer must read stays up long enough to read and never spans a cut.

## sound
<!-- rules: attention 21; structure 35 -->
- Every effect, sting or insert is tied to a beat of the script; one that serves no claim or step is cut.
- The last 5 to 20 seconds, where the end screen sits, are a quiet tail: no new narration beyond the one hand-off line, and the music continues under it and resolves at the cut.

## voice
<!-- rules: direction 27, 28 -->
- Pause only at clause and sentence boundaries, and before the sentence's key term or number; never strip those silences.
- One emphasised word per sentence, and it is a word in the line. Every name or term whose pronunciation is not obvious gets a pronouncer with its stressed syllable.

## format
<!-- rules: structure 36, 37; attention 18 -->
<!-- Structure 37 also asks for a thumbnail grammar. That is a look, which
is styles.json's under invariant 2, so it is left out here on purpose. -->
The channel is a repeatable format. These are neutral defaults until the channel's own house file states its format.
- Narrator: one persona across films, the viewer's proxy who is finding out, not the expert who already knows; expertise belongs to named sources.
- Opening: the same device every film; with none set here, open on this film's question.
- Ending: the same device every film; with none set here, end on the payoff.
- Hand-off: when the channel or the plan names the next film, one spoken pointer to that film, which continues this film's question, never a generic invitation to watch more; with none named, no hand-off.
- Series: the one connected topic the films share; with none set here, the film stands alone.
