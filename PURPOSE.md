# Purpose

> **An AI-directed production pipeline that turns an idea into a YouTube film
> at the quality of the best channels in its niche: sourced script,
> multi-style motion graphics and simulation, voice and sound design, all
> rendered on scene-render and proven by QA before it ships.**

This repository exists to produce YouTube videos that stand beside the best
channels in their niche, and that a viewer would subscribe for. It is built
for that, not to show that AI can make a video.

`yt` is a production pipeline. It takes an idea to a finished, gated film,
with generative AI making and judging every creative decision. The rendering
stack is scene-render and sr-core. The rest are tools the AI directs.

## What we are building

One pipeline that, for any subject and any channel, delivers:

- **Script.** A sourced, fact-checked screenplay, written to the channel's
  voice and pace.
- **Picture.** Visually intense motion graphics and VFX, in whichever
  treatment the film calls for: motion graphics, 2D, 2.5D, 3D, voxel, pixel
  art, planetary and astrophysical simulation, and mathematical simulation
  and visualisation. The treatment is chosen per video for what the film must
  make a viewer understand, and one film may combine several.
- **Sound.** Narration, sound effects, ambience and score, spotted to the
  picture and mixed to delivery loudness.
- **Proof.** A rendered video that has passed the delivery gate and the QA
  scorecard, with the evidence kept beside it.

The target is not "good for AI". The target is the quality bar of the
channels we compete with, met or beaten in every category that
`docs/production.md` scores: visual quality, animation and VFX,
cinematography, editing and pacing, graphics, voiceover, sound design, music,
narrative clarity, and overall production.

## Why this way

**AI makes the decisions, the tooling makes them honest.** Every creative
choice is a model decision: what the video argues, how it looks, what each
shot shows, how it sounds, whether it is good enough. The code supplies the
facts, the schema, the checks and the plumbing. It never supplies the taste.

**One engine, one document.** Everything renders through scene-render from a
single sr-core document: 2D layout, 3D sets with cameras, lights and
materials, particles, fluids, physics, captions, the mix and the outputs.
One deterministic engine means every frame can be reproduced, inspected and
corrected, and no quality depends on a hand pass in another tool.

**Tools are chosen by the job, not by habit.** Voices come from ElevenLabs,
Kokoro or Chatterbox, depending on quality, cost and whether a voice is
cloned. Licensed media comes from open catalogues, 3D props from CC0
libraries, and sound effects from Freesound. Model access goes through
`claude_engine.py`, or `codex_engine.py` when Codex is chosen. Any tool can be
replaced without changing what the pipeline promises.

**The rendered film is the evidence.** A manifest, an exit code or a valid
document proves nothing about quality. Every judgement is made on frames and
sound, by mechanical checks and by AI review, and `yt improve` loops
inspect, fix, render and compare until the film meets the bar or a limit is
proven and named.

## One pipeline, many channels

A channel is its standards: niche, voice, look, pace, the rules in its
`house.md`, and its backlog of ideas. A channel is not code. The same tooling
serves one channel or many, by keeping each channel's standards and videos in
their own work directory (`--work DIR`) and letting the AI read them.

That is why the tooling must stay universal. A rule, prompt, example,
constant or threshold written for one video makes every other video worse.
A film's nouns belong in its project directory, never in `ytstudio/`.
`tests/test_universality.py` enforces this.

## What done means

A video is done when it is **READY TO PUBLISH** by `docs/production.md`:

- an overall score of at least 9.0, and no category under 8.5;
- no placeholder, and no blocking finding from QA, the geometry check or the
  delivery gate;
- the whole chain reproducible from the idea forward.

`work/` is ephemeral by design. The pipeline is the product; a video is the
proof that it works.

## Where each promise is kept

| This document says | Where it is specified or enforced |
|---|---|
| The stages, the stack, what each one writes | [`README.md`](README.md): "The `yt` pipeline", "The stages", "Where things land" |
| How to run it, the invariants, the traps | [`ytstudio/SKILL.md`](ytstudio/SKILL.md), the operator's manual |
| The quality bar and what done means | [`docs/production.md`](docs/production.md) §2 and §11 |
| The improvement loop | [`docs/production.md`](docs/production.md) §8, and README "`yt improve`" |
| Where heavy work runs | [`docs/compute.md`](docs/compute.md) |
| A channel's standards | `work/house.md`, and a video's own `house.md`; README "Standards and keys" |
| Voices, sound, props, catalogues | README "`yt voice`", "`yt sound`", "`yt props`", "`yt assets`" |
| The model engines | `claude_engine.py`, `codex_engine.py`; README "`claude_engine` — the library" |
| Universality | `tests/test_universality.py`, `tests/test_design.py` |
