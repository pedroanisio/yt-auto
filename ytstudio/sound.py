#!/usr/bin/env python3
"""Propose the sound design: effects, ambience, score and the mix that holds them.

Reads the screenplay `yt script` wrote -- its shots, its timecodes, the words
being spoken over each one -- and spots the audio against it: where music comes
in and what it is doing there, which effects hit which frame, what the room
sounds like underneath, and where everything drops out so the next thing lands.

    yt sound --script script.json
    yt sound --script script.json --direction styles.json --review
    yt sound --title "How EUV light is made" --minutes 9 --mood "cold, procedural"
    yt sound --script script.json --free-only --md sound.md --csv cues.csv
    yt sound --script script.json --library "epidemic sound" --no-music
    yt sound --title "How EUV light is made" --fetch --free-only

Without --script it works from the idea and a runtime, and spots in broad
strokes; with the screenplay it places cues on real timecodes and names the word
or frame each effect lands on.

`--fetch` turns the cue sheet into files: every effect, bed, stinger,
transition and music cue is searched on Freesound (CC0 and CC-BY only), the
best hit by duration and rating is downloaded into `<project>/sound/` and
written back into the cue as `file`, `licence`, `credit`, `source_url` and
`duration`, which is what `yt build` places. A cue nothing matched keeps no
file and is listed under `unfilled` with the queries tried. With a sound.json
already in the project, `--fetch` reuses it and asks the model nothing;
`--fresh` spots again first.

Cues are proposed as original material to record, commission or license: the
score is described (instruments, tempo, character, how it enters and leaves) so
a composer or a library search can produce it. Reference tracks are named as
direction only, never as something to drop into the edit.
"""

from __future__ import annotations

import argparse
import array
import csv
import hashlib
import math
import os
import re
import shlex
import subprocess
import sys
import wave
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from claude_engine import ClaudeEngine, ClaudeError, EngineConfig, Usage
from .core import (Idea, _merge_usage, read_json, slugify, timecode, wrap,
                   write_json)
from .ideas import CHANNEL_STYLES, style_brief
from . import freesound, harness, house, workspace
from .script import Section, load_script, sections_of

#: What a cue can be. `silence` is a cue too: scored silence is a decision,
#: not an absence, and it is the one most often left out.
CUE_KINDS = ("music", "sfx", "ambience", "stinger", "transition", "silence")

#: YouTube normalises loud uploads to roughly this integrated loudness, so
#: mixing hotter than it buys nothing but a squashed dynamic range.
TARGET_LUFS = -14.0

SYSTEM = (
    "You are a sound designer and composer for factual YouTube: documentary, "
    "explainer and video-essay work. You spot audio against picture and "
    "narration, never as wallpaper. You know that music under speech has to "
    "sit 6 to 9 dB below it and stay out of the 1-4 kHz range the voice "
    "lives in, that a cue entering on a cut lands and a cue entering mid "
    "sentence muddies, and that the silence before a cue is what makes the "
    "cue work. You prefer the real recorded sound of the subject to a library "
    "whoosh, you name the exact sync point for every effect, and you never "
    "reach for the cliched trailer braam, the cinematic whoosh or the "
    "ticking-clock bed unless you can say why it is right here. You propose "
    "original or licensable material only: describe a cue so it can be "
    "recorded, commissioned or found, and name existing tracks as direction "
    "for the composer, never as something to use in the edit."
)


# --------------------------------------------------------------------------- #
# schemas
# --------------------------------------------------------------------------- #

PALETTE_SCHEMA = {
    "type": "object",
    "properties": {
        "approach": {"type": "string"},
        "instrumentation": {"type": "string"},
        "sfx_palette": {"type": "string"},
        "ambience": {"type": "string"},
        "avoid": {"type": "string"},
        "silence": {"type": "string"},
        "motifs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "stands_for": {"type": "string"},
                    "instrumentation": {"type": "string"},
                    "character": {"type": "string"},
                    "returns": {"type": "string"},
                },
                "required": ["name", "stands_for", "instrumentation",
                             "character", "returns"],
            },
        },
        "mix": {
            "type": "object",
            "properties": {
                "narration": {"type": "string"},
                "music_bed": {"type": "string"},
                "effects": {"type": "string"},
                "master": {"type": "string"},
            },
            "required": ["narration", "music_bed", "effects", "master"],
        },
        "references": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["approach", "instrumentation", "sfx_palette", "ambience",
                 "avoid", "silence", "motifs", "mix"],
}

CUES_SCHEMA = {
    "type": "object",
    "properties": {
        "cues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(CUE_KINDS)},
                    "name": {"type": "string"},
                    "at": {"type": "integer"},
                    "seconds": {"type": "integer"},
                    "shot": {"type": "string"},
                    "purpose": {"type": "string"},
                    "description": {"type": "string"},
                    "motif": {"type": "string"},
                    "instrumentation": {"type": "string"},
                    "tempo": {"type": "string"},
                    "sync": {"type": "string"},
                    "layers": {"type": "array", "items": {"type": "string"}},
                    "entry": {"type": "string"},
                    "exit": {"type": "string"},
                    "level": {"type": "string"},
                    "source": {"type": "string"},
                    "search": {"type": "array", "items": {"type": "string"}},
                    "query": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["kind", "name", "at", "seconds", "purpose",
                             "description", "source", "query"],
            },
        }
    },
    "required": ["cues"],
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "flags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cue": {"type": "string"},
                    "problem": {"type": "string"},
                    "severity": {"type": "string",
                                 "enum": ["blocker", "important", "minor"]},
                    "fix": {"type": "string"},
                },
                "required": ["cue", "problem", "severity", "fix"],
            },
        },
        "verdict": {"type": "string"},
    },
    "required": ["flags", "verdict"],
}

#: The cue fields a revision may rewrite: the design, never the file contract
#: `--fetch` writes for `yt build`, and never the cue's id.
REVISE_TEXT = ("name", "purpose", "description", "motif", "instrumentation",
               "tempo", "sync", "entry", "exit", "level", "source")
REVISE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["fix", "keep"]},
        "name": {"type": "string"}, "purpose": {"type": "string"},
        "description": {"type": "string"}, "motif": {"type": "string"},
        "instrumentation": {"type": "string"}, "tempo": {"type": "string"},
        "sync": {"type": "string"}, "entry": {"type": "string"},
        "exit": {"type": "string"}, "level": {"type": "string"},
        "source": {"type": "string"},
        "at": {"type": "integer"}, "seconds": {"type": "integer"},
        "why": {"type": "string"},
    },
    "required": ["action", "why"],
}
#: Severities the review-then-revise loop acts on.
REVISABLE = ("blocker", "important")


@dataclass
class Cue:
    kind: str = "music"
    name: str = ""
    at: int = 0
    seconds: int = 0
    shot: str = ""
    purpose: str = ""
    description: str = ""
    motif: str = ""
    instrumentation: str = ""
    tempo: str = ""
    sync: str = ""
    layers: list[str] = field(default_factory=list)
    entry: str = ""
    exit: str = ""
    level: str = ""
    source: str = ""
    search: list[str] = field(default_factory=list)
    query: str = ""
    note: str = ""
    cue_id: str = ""
    section: str = ""
    # What `yt sound --fetch` writes back: the contract with `yt build`. A
    # cue with no `file` is one nothing was found for.
    file: str = ""
    licence: str = ""
    credit: str = ""
    source_url: str = ""
    duration: float = 0.0
    freesound_id: int = 0
    query_used: str = ""

    @classmethod
    def from_raw(cls, raw: dict) -> Cue:
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in raw.items() if k in known and v is not None}
        for key in ("kind", "name", "shot", "purpose", "description", "motif",
                    "instrumentation", "tempo", "sync", "entry", "exit",
                    "level", "source", "query", "note"):
            if key in clean:
                clean[key] = str(clean[key]).strip()
        clean["kind"] = clean.get("kind", "music").lower()
        if clean["kind"] not in CUE_KINDS:
            clean["kind"] = "sfx"
        for key, floor in (("at", 0), ("seconds", 1)):
            try:
                clean[key] = max(floor, int(clean.get(key, floor) or floor))
            except (TypeError, ValueError):
                clean[key] = floor
        return cls(**clean)

    @property
    def out(self) -> int:
        return self.at + self.seconds


#: Cue numbering, by kind: M1 for music, S1 for an effect, and so on.
_PREFIX = {"music": "M", "sfx": "S", "ambience": "A", "stinger": "X",
           "transition": "T", "silence": "Q"}


def number_cues(cues: list[Cue]) -> list[Cue]:
    """Sort by timecode and give each cue the id a session file would use."""
    cues.sort(key=lambda c: (c.at, c.kind, c.name))
    counters: dict[str, int] = {}
    for cue in cues:
        prefix = _PREFIX.get(cue.kind, "C")
        counters[prefix] = counters.get(prefix, 0) + 1
        cue.cue_id = f"{prefix}{counters[prefix]}"
    return cues


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def on_measured_clock(script: dict, voice: dict | None) -> list[Section]:
    """The screenplay's sections with every recorded shot at its take's
    length and position; the plan stands where nothing was recorded."""
    if not voice or not voice.get("takes"):
        return sections_of(script)
    from . import retime
    import copy
    return sections_of(retime.apply(copy.deepcopy(script), voice))


def coverage(cues: list[Cue], runtime: int) -> dict:
    """Facts about the spotting, computed rather than asked for.

    A model is poor at counting its own minutes of music and excellent at
    judging them once told, so the review stage is handed these numbers.
    """
    music = _merge([(c.at, c.out) for c in cues if c.kind in ("music", "stinger")])
    beds = _merge([(c.at, c.out) for c in cues if c.kind == "ambience"])
    scored = sum(end - start for start, end in music)
    gaps, last = [], 0
    for start, end in music:
        if start > last:
            gaps.append((last, start))
        last = end
    if last < runtime:
        gaps.append((last, runtime))
    return {
        "runtime": runtime,
        "music_seconds": scored,
        "music_share": round(scored / runtime * 100) if runtime else 0,
        "longest_music_run": max((e - s for s, e in music), default=0),
        "unscored_spans": [f"{timecode(s)}-{timecode(e)}" for s, e in gaps
                           if e - s >= 5],
        "ambience_seconds": sum(e - s for s, e in beds),
        "counts": {kind: sum(1 for c in cues if c.kind == kind)
                   for kind in CUE_KINDS},
        "silent_by_design": [f"{timecode(c.at)} ({c.seconds}s)"
                             for c in cues if c.kind == "silence"],
    }


# --------------------------------------------------------------------------- #
# inputs
# --------------------------------------------------------------------------- #

def section_block(section: Section, *, narration: bool = True) -> str:
    """One section's shots as the spotting stage needs to see them."""
    lines = []
    for shot in section.shots:
        head = (f"- {shot.label} [{timecode(shot.at)}, {shot.seconds}s]"
                + (f" ({shot.style})" if shot.style else ""))
        lines.append(f"{head}\n  PICTURE: {shot.visual}")
        if narration and shot.narration:
            lines.append(f'  NARRATION: "{shot.narration}"')
        elif not shot.narration:
            lines.append("  NARRATION: none -- picture plays alone")
        if shot.on_screen:
            lines.append(f"  SUPER: {shot.on_screen}")
        if shot.note:
            lines.append(f"  DIRECTION: {shot.note}")
    return "\n".join(lines)


def direction_block(data: dict) -> str:
    """The sound and motion notes `yt styles` already committed to."""
    parts = []
    if data.get("message"):
        parts.append(f"What the visuals have to communicate: {data['message']}")
    for style in data.get("styles", []):
        bits = [f"Treatment: {style.get('name', '')} "
                f"(fit {style.get('fit', '?')}/10)"]
        for key, label in (("sound", "its sound note"),
                           ("motion", "its motion and pacing"),
                           ("palette", "its palette")):
            if style.get(key):
                bits.append(f"  {label}: {style[key]}")
        parts.append("\n".join(bits))
    if data.get("combination"):
        parts.append(f"How the treatments combine: {data['combination']}")
    return "\n\n".join(parts)


def subject_block(data: dict, *, limit: int = 12) -> str:
    """What the subject actually sounds like, from the research."""
    parts = []
    if data.get("premise"):
        parts.append(f"Subject: {data['premise']}")
    assets = [f"- [{a.get('kind', '?')}] {a.get('title', '')} "
              f"({a.get('licence', 'licence unknown')})"
              for a in data.get("assets", [])
              if a.get("kind") in ("video", "audio", "animation")][:limit]
    if assets:
        parts.append("Footage and audio already licensed, whose own sound you "
                     "can use or replace:\n" + "\n".join(assets))
    numbers = [f"- {c.get('number')}: {c.get('claim', '')}"
               for c in data.get("claims", []) if c.get("number")][:limit]
    if numbers:
        parts.append("Facts the audio may be asked to punctuate:\n"
                     + "\n".join(numbers))
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# the sound designer
# --------------------------------------------------------------------------- #

class SoundDesigner:
    """Builds the sonic palette, spots the cues, then reviews the result."""

    def __init__(self, idea: Idea, *, model: str = "sonnet", runtime: int = 600,
                 sections: list[Section] | None = None, direction: str = "",
                 subject: str = "", inspo: list[str] | None = None,
                 mood: str = "", library: list[str] | None = None,
                 free_only: bool = False, music: bool = True,
                 house: str = "", house_digest: dict | None = None,
                 verbose: bool = True, root: Path | str | None = None) -> None:
        self.idea = idea
        self.root = root            # the project, for its open revision notes
        self.model = model
        self.runtime = max(10, runtime)
        self.sections = sections or []
        self.direction = direction
        self.subject = subject
        self.inspo = inspo or []
        self.mood = mood
        self.library = library or []
        self.free_only = free_only
        self.music = music
        self.house = house
        self.house_digest = house_digest or {}
        self.verbose = verbose
        self.usage = Usage()
        self.palette: dict = {}

    # -- plumbing ----------------------------------------------------------

    def _say(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr)

    def _ask(self, prompt: str, schema: dict) -> dict:
        engine = harness.build_engine(
            EngineConfig(tools=(), model=self.model), ClaudeEngine)
        try:
            return engine.ask_json(prompt, schema, system=harness.with_feedback(
                SYSTEM, self.root, "sound"))
        finally:
            _merge_usage(self.usage, engine.usage)

    def _brief(self) -> str:
        bits = [f"Title: {self.idea.title}", f"Genre: {self.idea.genre}",
                f"Premise: {self.idea.description}",
                f"Runtime: {timecode(self.runtime)}"]
        if self.mood:
            bits.append(f"Wanted feeling: {self.mood}")
        return "\n".join(bits)

    #: Every spotting prompt ends with this: the fetch step searches a sound
    #: library with `query`, so it has to name the sound, not the intention.
    QUERY_ASK = (
        "For every cue except silence also give `query`: the 2-5 plain words "
        "a sound-library search (Freesound) would match. Name the sound "
        "itself -- the material, the action, the instrument, the room -- not "
        "its purpose, its level or its processing. A music cue's query names "
        "a library bed that could stand in for it (instrument and character); "
        "if nothing in a library could, say so in `source` and still give the "
        "nearest query.")

    def _sourcing(self) -> str:
        if self.free_only:
            text = ("Sourcing is restricted: every cue and effect must be "
                    "something you record yourself, a public-domain or CC0 "
                    "recording, or a CC-BY recording whose credit line you "
                    "state. No subscription libraries and no commissioned "
                    "score. For found audio give the catalogue and the exact "
                    "search terms; Freesound, Internet Archive, NASA audio "
                    "and the BBC sound effects library are the usual sources.")
        elif self.library:
            text = ("Available for sourcing: " + ", ".join(self.library)
                    + ". Prefer them over commissioning, and give the search "
                    "terms and descriptors that would find each cue there. "
                    "Say which cues are worth recording or commissioning "
                    "instead because no library will have them.")
        else:
            text = ("For each cue say how to get it: recorded yourself (say "
                    "how, and with what), found in an open catalogue (give the "
                    "catalogue and search terms), licensed from a subscription "
                    "library (give the descriptors to search), or commissioned "
                    "from a composer (give them tempo, key and character).")
        return text + "\n\n" + self.QUERY_ASK

    def _context(self) -> list[str]:
        parts = []
        if self.house:
            parts.append(self.house)
        if self.inspo:
            parts.append(style_brief(self.inspo) + "\n\nMatch the sonic "
                         "register those channels use: how much music they "
                         "run, how loud it sits, how dry or reverberant their "
                         "narration is.")
        if self.direction:
            parts.append(self.direction)
        if self.subject:
            parts.append(self.subject)
        if not self.music:
            parts.append("This video carries NO MUSIC. Work with effects, "
                         "ambience, the voice and silence only. Do not "
                         "propose a score or a bed.")
        return parts

    # -- stage one: the palette --------------------------------------------

    def plan(self) -> dict:
        self._say("[palette] sonic identity, motifs and mix targets")
        structure = "\n".join(
            f"- {n}. {s.heading} [{timecode(s.shots[0].at) if s.shots else '?'}, "
            f"{s.actual_seconds or s.seconds}s]"
            + (f" -- {s.purpose}" if s.purpose else "")
            for n, s in enumerate(self.sections, 1))
        parts = [self._brief(), *self._context()]
        if structure:
            parts.append(f"The cut, section by section:\n{structure}")
        parts.append(
            "Design the sound of this video before placing a single cue.\n\n"
            "Give the overall approach in a few sentences: what the audio is "
            "for here, and what it must never do. Then the instrumentation "
            "for the score, the palette the effects are drawn from, what the "
            "ambience beds are, and what to avoid -- name the specific "
            "cliches that would cheapen this subject.\n\n"
            "Then 2-4 motifs: a named recurring figure for each idea the "
            "video keeps returning to. For each, what it stands for, what "
            "plays it, its character in a few words, and how it comes back "
            "and transforms as the video goes on.\n\n"
            "Then where the audio drops to nothing, and why that silence "
            "earns its place.\n\n"
            f"Then the mix targets, in numbers: narration level and "
            "treatment, how far the music bed sits under it in dB and which "
            "frequencies it leaves alone, how loud impacts and effects peak, "
            f"and the master target (YouTube normalises to about "
            f"{TARGET_LUFS} LUFS integrated, with true peak at -1 dBTP).\n\n"
            "Finally, name a few reference tracks or scores as direction for "
            "the composer -- what to listen to for the feel, not what to use.")
        self.palette = self._ask("\n\n".join(parts), PALETTE_SCHEMA)
        return self.palette

    # -- stage two: spot it ------------------------------------------------

    def _palette_recap(self) -> str:
        if not self.palette:
            return ""
        motifs = "\n".join(
            f"- {m.get('name', '')}: {m.get('stands_for', '')} -- "
            f"{m.get('instrumentation', '')}, {m.get('character', '')}"
            for m in self.palette.get("motifs", []))
        mix = self.palette.get("mix", {})
        return (f"The sound design you already committed to:\n"
                f"Approach: {self.palette.get('approach', '')}\n"
                f"Score: {self.palette.get('instrumentation', '')}\n"
                f"Effects: {self.palette.get('sfx_palette', '')}\n"
                f"Ambience: {self.palette.get('ambience', '')}\n"
                f"Never: {self.palette.get('avoid', '')}\n"
                f"Motifs:\n{motifs}\n"
                f"Mix: narration {mix.get('narration', '')}; bed "
                f"{mix.get('music_bed', '')}; effects {mix.get('effects', '')}")

    def spot_section(self, section: Section, *, index: int, total: int,
                     placed: list[Cue]) -> list[Cue]:
        start = section.shots[0].at if section.shots else 0
        end = start + (section.actual_seconds or section.seconds)
        self._say(f"[{index}/{total}] {section.heading} "
                  f"-- {timecode(start)} to {timecode(end)}")

        running = "\n".join(
            f"- {c.cue_id or c.kind} {timecode(c.at)}-{timecode(c.out)}: "
            f"{c.name} ({c.kind})" for c in placed[-8:])
        parts = [self._brief(), self._palette_recap(), *self._context()]
        parts.append(f"Section {index} of {total}: \"{section.heading}\"\n"
                     f"It runs {timecode(start)} to {timecode(end)}.\n\n"
                     f"Its shots:\n{section_block(section)}")
        if running:
            parts.append("Cues already placed earlier in the video -- carry "
                         "them on, resolve them or leave them alone, and do "
                         f"not restate a motif that is still running:\n{running}")
        parts.append(
            "Spot this section. Place every cue with an absolute start "
            f"timecode in seconds from the top of the video, inside "
            f"{start}-{end} seconds, and a duration.\n\n"
            "Cover, where each is warranted:\n"
            "- music: what plays, which motif it belongs to, its "
            "instrumentation and tempo, how it enters (on which cut or word) "
            "and how it leaves\n"
            "- sfx: the effect, the exact sync point -- the frame or the word "
            "it lands on -- and the layers it is built from\n"
            "- ambience: the bed under the section, and where it changes "
            "because the location or scale changed\n"
            "- stinger and transition: only where the edit needs punctuating\n"
            "- silence: where everything drops away, and what the drop is for\n\n"
            "Do not score wall to wall. Leave the narration room where the "
            "point is the words, and give every cue a reason that names what "
            "the viewer gets from it. State the level each cue sits at "
            "relative to the narration.\n\n" + self._sourcing())
        data = self._ask("\n\n".join(parts), CUES_SCHEMA)

        cues = []
        for raw in data.get("cues", []):
            cue = Cue.from_raw(raw)
            cue.section = section.heading
            # Keep a cue inside its own section: a drifting timecode makes the
            # cue sheet unusable, and the next section will cover that ground.
            cue.at = min(max(cue.at, start), max(start, end - 1))
            cue.seconds = max(1, min(cue.seconds, max(1, end - cue.at)))
            cues.append(cue)
        return cues

    def spot_whole(self) -> list[Cue]:
        """No screenplay: spot the runtime in broad strokes instead."""
        self._say(f"[spot] no screenplay, spotting {timecode(self.runtime)} "
                  "in broad strokes")
        parts = [self._brief(), self._palette_recap(), *self._context()]
        parts.append(
            f"There is no shot list yet. Spot the whole {self.runtime} "
            "seconds as a plan the editor can cut to: where music enters and "
            "leaves, what the beds are, the effects the subject calls for, "
            "and where the audio drops out. Give every cue an absolute start "
            f"time in seconds between 0 and {self.runtime} and a duration. "
            "Say in each cue's purpose what has to be happening on screen for "
            "it to work, since the picture is not written yet.\n\n"
            + self._sourcing())
        data = self._ask("\n\n".join(parts), CUES_SCHEMA)
        cues = []
        for raw in data.get("cues", []):
            cue = Cue.from_raw(raw)
            cue.at = min(cue.at, self.runtime - 1)
            cue.seconds = max(1, min(cue.seconds, self.runtime - cue.at))
            cues.append(cue)
        return cues

    # -- stage three: listen back ------------------------------------------

    def review(self, cues: list[Cue], stats: dict, *,
               only: list[str] | None = None) -> dict:
        self._say(f"[review] {len(only) if only else len(cues)} cues, "
                  f"{stats['music_share']}% of the runtime scored")
        sheet = "\n".join(
            f"{c.cue_id} [{c.kind}] {timecode(c.at)}-{timecode(c.out)} "
            f"({c.seconds}s) {c.name} -- {c.purpose} | level: {c.level or '?'} "
            f"| source: {c.source}" for c in cues)
        parts = [self._brief(), self._palette_recap(),
                 f"The cue sheet:\n\n{sheet}",
                 "Measured from the cue sheet:\n"
                 f"- {stats['music_seconds']}s of {stats['runtime']}s carries "
                 f"music or stingers ({stats['music_share']}%)\n"
                 f"- longest unbroken music run: {stats['longest_music_run']}s\n"
                 f"- stretches of 5s or more with no music: "
                 f"{', '.join(stats['unscored_spans']) or 'none'}\n"
                 f"- ambience beds cover {stats['ambience_seconds']}s\n"
                 f"- cues by kind: {stats['counts']}\n"
                 f"- scored silence: "
                 f"{', '.join(stats['silent_by_design']) or 'none'}",
                 "Listen back to this as one pass and flag what would be "
                 "wrong in the room: music that runs so long it stops being "
                 "heard, a cue that fights the narration or enters mid "
                 "sentence, an effect with no sync point, a bed that never "
                 "changes when the picture changes, a motif that returns "
                 "before it was missed, silence used nowhere or used as an "
                 "excuse, levels that cannot coexist, and anything whose "
                 "sourcing will not survive a copyright claim. The in and "
                 "out timecodes on each line are where a cue starts and "
                 "stops; its `exit` text says how it leaves, never when, so "
                 "judge overlaps and what is still sounding by the "
                 "timecodes alone. Give the cue "
                 "id, the problem, how severe it is, and the smallest fix. "
                 "Then one sentence on whether this is ready to hand to an "
                 "editor."]
        if only:
            parts.append(
                "These cues were just revised. Judge only " + ", ".join(only)
                + ": flag a problem that is still there or one the change "
                "introduced, and flag no other cue.")
        try:
            return self._ask("\n\n".join(parts), REVIEW_SCHEMA)
        except ClaudeError as exc:
            self._say(f"[review] failed ({exc})")
            return {}

    # -- stage four: fix what the listen-back found --------------------------

    def revise(self, cues: list[Cue], flag: dict) -> dict:
        """The smallest change to one flagged cue, or a reason to keep it.

        Returns {"applied": {...} | None, "why": str}; a failed call changes
        nothing and is a keep.
        """
        by_id = {c.cue_id: c for c in cues}
        cue = by_id.get(str(flag.get("cue", "")).strip())
        if cue is None:
            return {"applied": None, "why": "no such cue on the sheet"}
        sheet = "\n".join(
            f"{c.cue_id} [{c.kind}] {timecode(c.at)}-{timecode(c.out)} "
            f"({c.seconds}s) {c.name} -- {c.purpose} | level: {c.level or '?'} "
            f"| source: {c.source}" for c in cues)
        now = {k: getattr(cue, k) for k in REVISE_TEXT}
        parts = [self._brief(), self._palette_recap(),
                 f"The cue sheet:\n\n{sheet}",
                 f"The listen-back flagged {cue.cue_id} "
                 f"({flag.get('severity', '')}): {flag.get('problem', '')} "
                 f"(suggested: {flag.get('fix', '')}).\n"
                 f"Its fields now: {now}",
                 "Make the smallest change to this one cue that removes the "
                 "problem, giving only the fields that change (`at` and "
                 f"`seconds` stay inside 0 to {self.runtime}). Never add a "
                 "reference track as something to drop into the edit. When the "
                 "cue is already right, or the fix would break another cue, "
                 "answer keep and say why in one clause."]
        try:
            data = self._ask("\n\n".join(parts), REVISE_SCHEMA)
        except ClaudeError as exc:
            self._say(f"[revise] {cue.cue_id} failed ({exc}); keeping it")
            return {"applied": None, "why": f"revision failed: {exc}"}
        why = str(data.get("why", "")).strip()
        if data.get("action") != "fix":
            return {"applied": None, "why": why or "kept as written"}
        before, after = {}, {}
        for key in REVISE_TEXT:
            value = str(data.get(key, "") or "").strip()
            if value and value != getattr(cue, key):
                before[key], after[key] = getattr(cue, key), value
        at, seconds = cue.at, cue.seconds
        try:
            if data.get("at") is not None:
                at = min(max(int(data["at"]), 0), self.runtime - 1)
            if data.get("seconds") is not None:
                seconds = max(1, int(data["seconds"]))
        except (TypeError, ValueError):
            pass
        seconds = max(1, min(seconds, self.runtime - at))   # ends inside the film
        for key, value in (("at", at), ("seconds", seconds)):
            if value != getattr(cue, key):
                before[key], after[key] = getattr(cue, key), value
        if not after:
            return {"applied": None, "why": why or "nothing to change"}
        for key, value in after.items():
            setattr(cue, key, value)
        return {"applied": {"cue": cue.cue_id, "before": before, "after": after,
                            "why": why}, "why": why}

    def review_and_revise(self, cues: list[Cue], stats: dict, *, rounds: int = 2,
                          fix: bool = True) -> dict:
        """Listen back, fix the important and blocking cues, re-check just those.

        Stops when nothing binding is left or the rounds run out. The result
        is the review, with `revisions` (what changed), `kept` (cues left as
        written, with the reason, and still flagged) and `unresolved` (flags
        still standing after the last round, each kept with that reason).
        """
        review = self.review(cues, stats)
        if not review or not fix:
            return review

        def binding(flags, skip=()):
            return [f for f in flags if f.get("severity") in REVISABLE
                    and f.get("cue") not in skip]

        revisions: list[dict] = []
        kept: dict[str, str] = {}
        used = 0
        for _ in range(max(0, rounds)):
            todo = binding(review.get("flags", []), kept)
            if not todo:
                break
            used += 1
            changed: list[str] = []
            for f in todo:
                if f["cue"] in changed:
                    continue        # a cue's flags are answered by one change
                result = self.revise(cues, f)
                if result["applied"]:
                    revisions.append(result["applied"])
                    changed.append(f["cue"])
                else:
                    kept[f["cue"]] = result["why"]
            if not changed:
                break
            again = self.review(cues, coverage(cues, self.runtime), only=changed)
            if not again:
                break
            review["flags"] = (
                [f for f in review.get("flags", []) if f.get("cue") not in changed]
                + [f for f in again.get("flags", []) if f.get("cue") in changed])
            review["recheck"] = again.get("verdict", "")
        left = binding(review.get("flags", []), kept)
        for f in review.get("flags", []):
            if f.get("cue") in kept:
                f["kept"] = kept[f["cue"]]
        for f in left:
            f["kept"] = (f"still flagged after {used} round(s) of revision; "
                         "left as it is")
        review["revisions"] = revisions
        review["kept"] = [{"cue": c, "why": why} for c, why in kept.items()]
        review["unresolved"] = left
        self._say(f"[revise] changed {len(revisions)}, kept {len(kept)}, "
                  f"{len(left)} still flagged")
        return review

    # -- run ---------------------------------------------------------------

    def run(self, *, do_review: bool = False, fix: bool = True) -> dict:
        palette = self.plan()
        cues: list[Cue] = []
        failures = 0
        if self.sections:
            for index, section in enumerate(self.sections, 1):
                try:
                    cues += self.spot_section(section, index=index,
                                              total=len(self.sections),
                                              placed=cues)
                except ClaudeError as exc:
                    self._say(f"  section {index} failed: {exc}")
                    failures += 1
                number_cues(cues)
        else:
            cues = self.spot_whole()
        number_cues(cues)

        stats = coverage(cues, self.runtime)
        review = (self.review_and_revise(cues, stats, fix=fix)
                  if do_review and cues else {})
        if review.get("revisions"):
            stats = coverage(cues, self.runtime)
        return {
            "idea": asdict(self.idea),
            "runtime": self.runtime,
            "palette": palette,
            "cues": [asdict(c) for c in cues],
            "coverage": stats,
            "review": review,
            "house": self.house_digest,
            "failed_sections": failures,
            "usage": asdict(self.usage),
        }


# --------------------------------------------------------------------------- #
# fetch: the cue sheet becomes files
# --------------------------------------------------------------------------- #

#: Cue kinds a library can supply. Silence is placed, never downloaded.
FETCH_KINDS = ("sfx", "ambience", "stinger", "transition", "music")
#: Where the files go, relative to the project root.
SOUND_DIR = "sound"
#: What was fetched, and why it is a scratch mix's material, not a master's.
FETCH_QUALITY = (f"Freesound {freesound.PREVIEW}: the high-quality mp3 preview, "
                 "fine for a scratch mix and for placing against picture; the "
                 "original upload needs OAuth and is not fetched")
#: The fields `--fetch` writes into a cue, and clears before trying again.
FETCHED_FIELDS = ("file", "licence", "credit", "source_url", "duration",
                  "freesound_id", "query_used", "synth", "gain_db",
                  "source_peak_dbfs")
#: Function words that carry no sound: dropped when a cue's name is shortened
#: into a last-resort query. Nothing about any subject lives here.
_STOP = frozenset("a an and the of on in at to for with from by or into onto "
                  "over under up down out off as is it its this that then "
                  "following follows after before".split())


def _words(text: str, *, limit: int) -> str:
    """The first sounding words of a phrase: no numbers, units or function
    words, since a library search ANDs every word it is given."""
    clean = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower())
    words = [w for w in clean.split()
             if not any(ch.isdigit() for ch in w) and w not in _STOP
             and w not in ("hz", "khz", "db", "dbfs", "lufs", "ms", "s")]
    return " ".join(words[:limit])


def cue_queries(cue: dict) -> list[str]:
    """What to search for, in order: the model's `query`, its search terms,
    each layer the cue is built from (a layer names a sound; its qualifier
    after the comma does not), the cue's name, and the name shortened to its
    first three and then two sounding words."""
    candidates = [cue.get("query") or ""]
    candidates += [s for s in (cue.get("search") or []) if isinstance(s, str)]
    for layer in cue.get("layers") or []:
        if isinstance(layer, str):
            candidates.append(_words(re.split(r"[,:;(]", layer, 1)[0], limit=3))
    name = re.sub(r"[^a-z0-9]+", " ", str(cue.get("name") or "").lower()).strip()
    candidates.append(name)
    candidates.append(_words(name, limit=3))
    candidates.append(_words(name, limit=2))
    out: list[str] = []
    for text in candidates:
        text = " ".join(str(text).lower().split())
        if text and text not in out:
            out.append(text)
    return out


def _index_path(root: Path) -> Path:
    """Freesound id -> the file already fetched for it, so a re-spotted
    sheet that lands on the same sound does not download it again."""
    return Path(root) / SOUND_DIR / "index.json"


#: Beds are heard as a level over time, so they are lifted to their design
#: level by integrated loudness; every other kind is a short event, heard at
#: its peak, so it is lifted by true peak.
LOUDNESS_KINDS = ("music", "ambience")
#: A lifted source's noise floor must sit at least this far under the
#: narration (the sheet's own level, else the programme target). Speech with
#: a masker 12 dB down is still work to follow; this is the one judgement in
#: the stage, kept in one place.
NOISE_BELOW_NARRATION_DB = 12.0
#: Candidates tried for one cue (its first hit included) before it is left unfilled.
FETCH_TRIES = 3
#: The measurement definitions. ebur128 reports a momentary loudness (M) over
#: a 400 ms window every 100 ms; a window is complete once 400 ms have been
#: heard, and the floor is the level the quietest tenth of them reaches.
WINDOW_S, FLOOR_FRACTION = 0.4, 0.10
#: BS.1770's absolute gate: loudness at or under it is silence to ebur128.
GATE_LUFS = -70.0
#: Seconds allowed the remote measurement, plus this much per file.
MEASURE_SECONDS, MEASURE_SECONDS_PER_FILE = 120, 30


class MeasureError(RuntimeError):
    """The files could not be measured at all; the reason is the message."""


@dataclass
class Levels:
    """What one file measures: integrated loudness (LUFS), true peak (dBFS),
    the noise floor (dBFS, momentary loudness of its quietest tenth), and the
    host that measured it."""
    lufs: float
    true_peak: float
    floor: float
    host: str = ""


def reference_of(kind: str) -> str:
    """What a cue of this kind is levelled by: "loudness" or "peak"."""
    return "loudness" if kind in LOUDNESS_KINDS else "peak"


def gain_reach() -> float:
    """The most gain one cue can be given: the schema's cap on a track. The
    bus gain is shared by every cue on the bus, so it cannot be spent per cue."""
    from . import build
    return build.facets_of("audioTrack", "gain")[1]


_NUM = r"(-?inf|-?\d+(?:\.\d+)?)"
_FRAME = re.compile(r"\bt:\s*(\d+(?:\.\d+)?)\s.*?\bM:\s*" + _NUM)
_SUM_LUFS = re.compile(r"Integrated loudness:\s*I:\s*" + _NUM + r"\s*LUFS")
_SUM_PEAK = re.compile(r"True peak:\s*Peak:\s*" + _NUM + r"\s*dBFS")


def parse_ebur128(text: str) -> Levels | None:
    """Levels from ffmpeg's `ebur128=peak=true:framelog=verbose` output; None
    when it has no summary (the file was not read). A file shorter than one
    window has no complete window: its loudness stands for the floor."""
    frames, _, summary = text.partition("Summary:")
    lufs, peak = _SUM_LUFS.search(summary), _SUM_PEAK.search(summary)
    if not (lufs and peak):
        return None
    windows = sorted(float(m) for t, m in _FRAME.findall(frames)
                     if float(t) >= WINDOW_S - 1e-6)
    loudness = float(lufs.group(1))
    floor = (windows[max(0, math.ceil(FLOOR_FRACTION * len(windows)) - 1)]
             if windows else loudness)
    return Levels(lufs=loudness, true_peak=float(peak.group(1)), floor=floor)


def measure_script(remote: str, names: list[str], ffmpeg: str = "") -> str:
    """The one remote shell for a batch: unpack the files into `remote`, run
    ebur128 on each between @@FILE/@@END marks, and remove the directory."""
    q = shlex.quote
    runs = "".join(
        f'echo "@@FILE {n}"; "$F" -hide_banner -nostats -loglevel verbose -i {q(name)} -vn '
        f'-af ebur128=peak=true:framelog=verbose -f null - 2>&1; '
        f'echo "@@END {n} $?"; ' for n, name in enumerate(names))
    return (f"mkdir -p {q(remote)} && cd {q(remote)} && tar -xf - && "
            f'{{ F=$(command -v ffmpeg 2>/dev/null); [ -n "$F" ] || F={q(ffmpeg)}; '
            f'if [ -x "$F" ]; then {runs}true; else echo __NO_FFMPEG__; false; fi; }}; '
            f"code=$?; cd /; rm -rf {q(remote)}; exit $code")


def measure_remote(paths) -> list[Levels | None]:
    """Measure every file on a render host, in one ssh: the files travel as a
    tar on stdin (as a still's bundle does), ffmpeg runs there, and the
    answers come back on stdout. ffmpeg is not run on this workstation. A
    file the host could not read is None; no host, no ffmpeg, or a dead link
    is a MeasureError."""
    from . import render
    paths = [Path(p) for p in paths]
    if not paths:
        return []
    if os.environ.get(render.NO_REMOTE_ENV):
        raise MeasureError(f"{render.NO_REMOTE_ENV} is set: nothing is run on a "
                           "render host")
    host = render.remote_host(None, "audio")
    if not host:
        raise MeasureError("no render host configured (RS_RENDER_HOSTS or "
                           "RS_RENDER_HOST); ffmpeg is not run on this workstation")
    known = render._HOSTS.get(host) or render.probe(host)
    beside = f"{Path(known.ffprobe).parent}/ffmpeg" if known.ffprobe else ""
    names = [f"{n}{re.sub(r'[^.A-Za-z0-9]', '', p.suffix)}" for n, p in enumerate(paths)]
    script = measure_script(f"{render.REMOTE_ROOT}/sound-{render.job_tag()}", names, beside)
    seconds = MEASURE_SECONDS + MEASURE_SECONDS_PER_FILE * len(paths)
    try:
        render.refuse_remote()
        done = subprocess.run(
            render.probe_argv(host, script, 8),
            input=render._tar({name: p.read_bytes() for name, p in zip(names, paths)}),
            capture_output=True, timeout=seconds)
    except (render.RemoteRefused, subprocess.TimeoutExpired, OSError) as exc:
        raise MeasureError(f"render host {host} unreachable: {exc}") from exc
    out = done.stdout.decode("utf-8", "replace")
    if "__NO_FFMPEG__" in out:
        raise MeasureError(f"no ffmpeg on render host {host} (not on PATH, not "
                           "beside its ffprobe)")
    blocks = {int(n): (int(code), body) for n, body, code in re.findall(
        r"@@FILE (\d+)\n(.*?)\n?@@END \1 (\d+)", out, re.S)}
    if not blocks:
        raise MeasureError(f"measuring on {host} failed (exit {done.returncode}): "
                           f"{done.stderr.decode('utf-8', 'replace').strip()[:200]}")
    found: list[Levels | None] = []
    for n in range(len(paths)):
        code, body = blocks.get(n, (1, ""))
        got = parse_ebur128(body) if code == 0 else None
        if got is not None:
            got.host = host
        found.append(got)
    return found


def narration_level(data: dict) -> tuple[float, str]:
    """(dB, where it came from): the narration level the sheet's mix states,
    else the programme target."""
    from . import build
    said = ((data.get("palette") or {}).get("mix") or {}).get("narration")
    level = build.level_db(str(said or ""))
    return (level, "the sheet's narration level") if level is not None \
        else (TARGET_LUFS, "programme target")


def noise_ceiling(narration: float) -> float:
    """The loudest a lifted source's noise floor may sit."""
    return narration - NOISE_BELOW_NARRATION_DB


def _finite(value: float) -> float | None:
    return round(value, 2) if math.isfinite(value) else None


def level_to_design(cue: dict, got: Levels, narration: float
                    ) -> tuple[dict | None, str]:
    """(fields for the cue, refusal) for a measured file. The build asks
    `level + gain_db` of the file, taken as a 0 dBFS reference, so
    `gain_db` is minus the file's reference (loudness for a bed, true peak
    for an event) and the lift actually applied is the design level less
    that reference. That net lift must fit the track's gain range, and the file's noise floor
    after it must still sit under the narration (a bed, being its own floor,
    is held to its design level instead: the sheet owns where it sits)."""
    from . import build
    kind = reference_of(cue.get("kind") or "")
    ref, unit = (got.lufs, "LUFS") if kind == "loudness" else (got.true_peak, "dBTP")
    if ref <= (GATE_LUFS if kind == "loudness" else -120.0):
        return None, f"silent source (measured {ref:.1f} {unit})"
    design = build.level_db(str(cue.get("level") or "")) or 0.0
    lift, reach = design - ref, gain_reach()
    cut = build.facets_of("audioTrack", "gain")[0]
    # The build applies the cue's level plus its gain_db, so the gain the track
    # needs is the net one, design less reference: that is what must sit in
    # the track's range, in both directions.
    if lift > reach or lift < cut:
        return None, (f"source measures {ref:.1f} {unit}; reaching the cue's "
                      f"{design:+.1f} dB design level needs {lift:+.1f} dB, "
                      f"beyond the track's {reach if lift > reach else cut:+.0f} dB")
    ceiling = noise_ceiling(narration)
    if kind == "loudness":
        ceiling = max(ceiling, design)
    after = got.floor + lift
    if after > ceiling:
        return None, (f"noise: the source's floor is {got.floor:.1f} dBFS; "
                      f"lifted by {lift:+.1f} dB it sits at {after:.1f}, above "
                      f"the {ceiling:.1f} ceiling ({NOISE_BELOW_NARRATION_DB:g} dB "
                      f"under the narration at {narration:.1f})")
    return {"gain_db": round(-ref, 2), "lift_by": kind,
            "source_lufs": _finite(got.lufs),
            "source_true_peak_dbfs": _finite(got.true_peak),
            "source_noise_floor_dbfs": _finite(got.floor)}, ""


@dataclass
class _Job:
    """One cue being filled: where it is, what it may still try."""
    cue: dict
    cid: str
    n: int
    miss: dict
    used: str = ""
    old_id: object = None           # the Freesound id of a kept file
    hits: list | None = None        # hits still to try (kept files: searched on refusal)
    rel: str | None = None          # the file now under test
    hit: object = None              # its Freesound hit (None for a kept file)
    refusals: list = field(default_factory=list)
    tries: int = 0
    done: bool = False


def fetch(root: str | Path, data: dict, *, token: str | None = None,
          say=None, measure=None) -> dict:
    """Fill the cue sheet in `data` with files under `<root>/sound/`.

    Mutates `data`: each filled cue gains the contract fields (`file`,
    `licence`, `credit`, `source_url`, `duration`), `data["unfilled"]` lists
    what nothing matched, and `data["fetch"]` is the summary returned. A cue
    already carrying a file that exists is kept; a Freesound id already on
    disk is reused; so re-running costs searches, never downloads. `yt run`
    can call this directly once the sheet is written.

    Each file is measured (`measure(paths) -> [Levels | None]`, default
    `measure_remote`: one ssh to a render host per batch) and the cue gains
    the `gain_db` that brings it to its design level, by loudness for a bed
    and by peak for an event (`level_to_design`). A source the track's gain
    cannot reach, or whose noise floor the lift would raise too near the
    narration, is refused with the measured figures, and the next-ranked hit
    of the same search is tried (`FETCH_TRIES` in all) before the cue is
    left unfilled. A batch that could not be measured keeps its files
    unlevelled and says so in the summary (`measure_error`, `unmeasured`).
    """
    root = Path(root)
    measure = measure or measure_remote
    say = say or (lambda msg: print(msg, file=sys.stderr))
    token = freesound.token() if token is None else token
    index = read_json(_index_path(root), warn=False) or {}
    narration, narration_from = narration_level(data)
    summary: dict = {"source": "freesound", "quality": FETCH_QUALITY,
                     "dir": SOUND_DIR, "filled": 0, "cached": 0, "bytes": 0,
                     "unfilled": 0, "skipped": [], "levelled": 0,
                     "refused": [], "narration_db": narration,
                     "narration_from": narration_from}
    unfilled: list[dict] = []
    jobs: list[_Job] = []
    if not token:
        # The name of the variable, never a value.
        summary["error"] = (f"no Freesound token: set {freesound.TOKEN_VARS[0]} "
                            "(the API client secret) in the env file")
        say("  " + summary["error"])

    for n, cue in enumerate(data.get("cues") or [], 1):
        cid = cue.get("cue_id") or f"#{n}"
        if cue.get("kind") not in FETCH_KINDS:
            summary["skipped"].append(cid)
            continue
        queries = cue_queries(cue)
        miss = {"cue_id": cid, "name": cue.get("name", ""),
                "kind": cue.get("kind", ""), "queries": queries}
        existing = cue.get("file")
        if existing and (cue.get("freesound_id") or cue.get("synth")) \
                and (root / existing).is_file():
            if cue.get("synth") or cue.get("source_true_peak_dbfs") is not None:
                # Original sound is made at its level; a levelled file is done.
                summary["filled"] += 1
                summary["cached"] += 1
                summary["bytes"] += (root / existing).stat().st_size
                say(f"  {cid} kept {existing}")
            else:
                # A fetched file nobody measured (an older run, or no host
                # then): measured now, in the same batch as the new ones.
                jobs.append(_Job(cue, cid, n, miss, rel=existing, tries=1,
                                 used=cue.get("query_used") or "",
                                 old_id=cue.get("freesound_id")))
            continue
        for key in FETCHED_FIELDS:
            cue.pop(key, None)
        if not token:
            unfilled.append(miss | {"reason": f"no token ({freesound.TOKEN_VARS[0]} "
                                              "not set)"})
            continue
        want = max(1, int(cue.get("seconds") or 1))
        hits, used = freesound.ranked(queries, want_seconds=want, token=token)
        if not hits:
            reason = "no CC0 or CC-BY hit on Freesound for any query"
            if cue.get("kind") == "music":
                reason += ("; a composition brief with no library stand-in "
                           "stays unfilled")
            unfilled.append(miss | {"reason": reason})
            say(f"  {cid} unfilled: tried {', '.join(repr(q) for q in queries)}")
            continue
        jobs.append(_Job(cue, cid, n, miss, used=used, hits=hits[:FETCH_TRIES]))

    levels: dict[str, Levels | None] = {}
    taken: set[str] = set()          # files a cue now uses

    def give_up(job: _Job) -> None:
        job.done = True
        why = (" | ".join(f"{label}: {reason}" for label, reason in job.refusals)
               if job.refusals else "no CC0 or CC-BY hit on Freesound for any query")
        if len(job.refusals) > 1:
            why = f"{len(job.refusals)} sources refused, {why}"
        unfilled.append(job.miss | {"reason": why})
        say(f"  {job.cid} unfilled: {why}")

    def advance(job: _Job) -> None:
        """Give the job its next file to test, or end it."""
        while job.rel is None and not job.done:
            if job.tries >= FETCH_TRIES or not job.hits:
                give_up(job)
                return
            hit = job.hits.pop(0)
            rel = index.get(str(hit.id))
            if rel and (root / rel).is_file():
                summary["cached"] += 1
            else:
                rel = (f"{SOUND_DIR}/{job.n:02d}-"
                       f"{slugify(job.cue.get('name') or job.cue.get('kind', 'cue'), 40)}"
                       f".{hit.ext}")
                try:
                    freesound.download(hit, root / rel)
                except freesound.FreesoundError as exc:
                    job.refusals.append((f"hit {hit.id}", str(exc)))
                    job.tries += 1
                    continue
                index[str(hit.id)] = rel
                write_json(_index_path(root), index)
            job.rel, job.hit = rel, hit

    def refuse(job: _Job, why: str) -> None:
        label = f"hit {job.hit.id}" if job.hit else f"kept {job.rel}"
        job.refusals.append((label, why))
        summary["refused"].append({"cue_id": job.cid, "file": job.rel, "reason": why,
                                   "freesound_id": job.hit.id if job.hit else job.old_id})
        say(f"  {job.cid} refused {job.rel}: {why}")
        job.tries += 1
        if job.hit is None:
            # A kept file refused: forget it, and look for others to try.
            for key in FETCHED_FIELDS:
                job.cue.pop(key, None)
            if token:
                want = max(1, int(job.cue.get("seconds") or 1))
                found, job.used = freesound.ranked(
                    job.miss["queries"], want_seconds=want, token=token)
                job.hits = [h for h in found if h.id != job.old_id][:FETCH_TRIES]
        job.rel = job.hit = None

    def accept(job: _Job, fields: dict | None) -> None:
        cue, rel, hit = job.cue, job.rel, job.hit
        if hit is not None:
            cue.update(file=rel, licence=hit.licence, credit=hit.credit,
                       source_url=hit.url, duration=round(hit.duration, 3),
                       freesound_id=hit.id, query_used=job.used)
        else:
            summary["cached"] += 1
        if fields:
            cue.update(fields)
            summary["levelled"] += 1
        else:
            summary.setdefault("unmeasured", []).append(job.cid)
        job.done = True
        taken.add(rel)
        summary["filled"] += 1
        summary["bytes"] += (root / rel).stat().st_size
        how = (f"<- {hit.licence} {hit.duration:.1f}s (query {job.used!r})"
               if hit is not None else "kept")
        gain = f", gain {fields['gain_db']:+.1f} dB" if fields else ""
        say(f"  {job.cid} {rel} {how}{gain}")

    while True:
        active = [j for j in jobs if not j.done]
        for job in active:
            advance(job)
        testing = [j for j in active if not j.done]
        if not testing:
            break
        wanted = sorted({j.rel for j in testing} - set(levels))
        if wanted and "measure_error" not in summary:
            try:
                got = measure([root / rel for rel in wanted])
                levels.update(zip(wanted, got))
            except MeasureError as exc:
                summary["measure_error"] = str(exc)
        discard: set[str] = set()
        for job in testing:
            got = levels.get(job.rel)
            if got is None:
                accept(job, None)
                continue
            fields, why = level_to_design(job.cue, got, narration)
            if why:
                discard.add(job.rel)
                refuse(job, why)
            else:
                accept(job, fields)
        for rel in discard - taken - {j.rel for j in jobs if not j.done}:
            (root / rel).unlink(missing_ok=True)
            levels.pop(rel, None)
            for key in [k for k, v in index.items() if v == rel]:
                index.pop(key)
        if discard:
            write_json(_index_path(root), index)

    if summary.get("unmeasured") or "measure_error" in summary:
        host = next((v.host for v in levels.values() if v), "")
        summary["warning"] = (
            f"{len(summary.get('unmeasured', []))} file(s) NOT LEVELLED: "
            + (summary.get("measure_error")
               or f"the host {host} could not read them")
            + "; the cues keep their sheet level and no noise check was made")
        say("  WARNING: " + summary["warning"])

    data["unfilled"] = unfilled
    summary["unfilled"] = len(unfilled)
    data["fetch"] = summary
    return summary


def fetch_file(path: str | Path, *, token: str | None = None, say=None) -> dict:
    """`fetch` for a sound.json on disk, written back in place."""
    path = Path(path)
    data = read_json(path)
    if not data:
        raise FileNotFoundError(f"no cue sheet at {path}")
    summary = fetch(path.parent, data, token=token, say=say)
    write_json(path, data)
    return summary


# --------------------------------------------------------------------------- #
# realisation: the cues nothing fetched become original procedural sound
# --------------------------------------------------------------------------- #

#: The synthesis workspace and the WAVs it writes, relative to the project.
SYNTH_DIR = f"{SOUND_DIR}/synth"
#: Every realised file is 48 kHz stereo 16-bit PCM: what the mix expects.
SYNTH_RATE, SYNTH_CHANNELS, SYNTH_WIDTH = 48000, 2, 2
#: Provenance for a sound nobody else made; `yt build` carries both into the
#: document exactly as it does a fetched cue's licence and credit.
SYNTH_LICENCE = "original"
SYNTH_CREDIT = "Original procedural sound design, synthesised for this video"
#: The author writes and runs one script in its workspace; it never fans out.
SYNTH_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep", "Bash")
SYNTH_TIMEOUT = 1800.0
#: A peak below this is silence; at or above this it clips.
SILENT_DBFS, CLIP_DBFS = -60.0, -0.1

#: What the agent is told, beside the list of cues. The aesthetic is the
#: cue's own description; nothing about any subject lives here.
SYNTH_RULES = """\
Write `make_audio.py` in this directory: one deterministic Python script
(standard library, NumPy and SciPy only; a fixed random seed) that synthesises
every cue in cues.json and writes it to the cue's `file` path, relative to the
project root two directories up (`Path(__file__).resolve().parents[2]`).

- Format: 48000 Hz, stereo, 16-bit PCM WAV, exactly that.
- Length: the cue's `seconds`, so it fits the window the mix gives it.
- Level: peak between -18 and -3 dBFS; never clip, never silence.
- Original: no samples, no recordings, no downloaded audio, no voice. Build each
  sound from oscillators, filtered noise, envelopes and resonances, the way
  the cue's description, layers and sync point ask. A tuned impact is a damped
  mode with a fast attack; water is a rising cavity mode plus band-passed
  noise; a room is low, slow, broadband; a score cue follows its tempo and
  instrumentation as far as synthesis can.
- Edges: fade in and out over a few milliseconds so no cue starts or stops
  with a click.

Run `python3 make_audio.py`, then `python3 check.py`. check.py is the
pipeline's own check of every file; fix the script and run both again until
every cue prints `ok`. Do not hand-edit a WAV and do not write files outside
the cue paths and this directory.
"""

#: The checker the agent runs: the plumbing's `check_wav`, imported, so the
#: agent and the pipeline cannot disagree about what passes.
CHECK_SCRIPT = """#!/usr/bin/env python3
\"\"\"check -- the pipeline's own check of every WAV this workspace must write.

    python3 check.py      one line per cue; exit 0 only when every cue is ok
\"\"\"
import json
import sys
from pathlib import Path

sys.path.insert(0, @AUTOMATION_ROOT@)
from ytstudio.sound import check_wav  # noqa: E402

here = Path(__file__).resolve().parent
root = here.parents[1]
bad = 0
for t in json.loads((here / "cues.json").read_text(encoding="utf-8")):
    seconds, problems = check_wav(root / t["file"], want_seconds=float(t["seconds"]))
    if problems:
        bad += 1
        print(f"{t['cue_id']} {t['file']}: " + "; ".join(problems))
    else:
        print(f"{t['cue_id']} ok {t['file']} {seconds:.2f}s")
sys.exit(1 if bad else 0)
"""

#: The cue fields an author is handed: what the sound must be and when.
_BRIEF_FIELDS = ("kind", "name", "at", "seconds", "description", "purpose",
                 "layers", "sync", "instrumentation", "tempo", "motif",
                 "entry", "exit")


def synth_targets(root: str | Path, data: dict) -> list[dict]:
    """The cues a synthesis should realise: every library-kind cue whose file
    is missing or gone, each with the path its WAV must be written to.
    Silence is a window on the master, never a file."""
    root = Path(root)
    out: list[dict] = []
    for n, cue in enumerate(data.get("cues") or [], 1):
        if cue.get("kind") not in FETCH_KINDS:
            continue
        existing = cue.get("file")
        if existing and (root / existing).is_file():
            continue
        try:
            seconds = max(0.1, float(cue.get("seconds") or 1))
        except (TypeError, ValueError):
            seconds = 1.0
        row = {"cue_id": cue.get("cue_id") or f"#{n}", "n": n}
        row.update({k: cue[k] for k in _BRIEF_FIELDS if cue.get(k) not in (None, "", [])})
        row["seconds"] = seconds
        slug = slugify(cue.get("name") or cue.get("kind") or "cue", 40)
        row["file"] = f"{SYNTH_DIR}/{n:02d}-{slug}.wav"
        out.append(row)
    return out


def check_wav(path: str | Path, *, want_seconds: float) -> tuple[float | None, list[str]]:
    """(seconds, problems) for one realised file; no problems means the mix
    can place it as it is."""
    path = Path(path)
    if not path.is_file():
        return None, ["not written"]
    try:
        with wave.open(str(path), "rb") as handle:
            rate, channels = handle.getframerate(), handle.getnchannels()
            width, frames = handle.getsampwidth(), handle.getnframes()
            raw = handle.readframes(frames)
    except (wave.Error, EOFError, OSError) as exc:
        return None, [f"not a readable WAV ({exc})"]
    seconds = frames / rate if rate else 0.0
    problems: list[str] = []
    if rate != SYNTH_RATE:
        problems.append(f"{rate} Hz, not {SYNTH_RATE}")
    if channels != SYNTH_CHANNELS:
        problems.append(f"{channels} channel(s), not stereo")
    if width != SYNTH_WIDTH:
        problems.append(f"{8 * width}-bit, not 16-bit")
        return seconds, problems
    if not max(0.1, 0.5 * want_seconds) <= seconds <= want_seconds + 2.0:
        problems.append(f"{seconds:.2f}s long for a {want_seconds:g}s cue")
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - len(raw) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    peak = max(abs(min(samples, default=0)), abs(max(samples, default=0)))
    dbfs = 20 * math.log10(peak / 32768) if peak else float("-inf")
    if dbfs < SILENT_DBFS:
        problems.append("silent (no signal)" if not peak else f"silent (peak {dbfs:.0f} dBFS)")
    elif dbfs >= CLIP_DBFS:
        problems.append(f"clips (peak {dbfs:.1f} dBFS)")
    return seconds, problems


def synth_brief(targets: list[dict]) -> str:
    """BRIEF.md: the rules, then every cue with the file it must become."""
    lines = ["# Realise these cues as original sound", "", SYNTH_RULES, "## Cues", ""]
    for t in targets:
        lines.append(f"### {t['cue_id']} -> `{t['file']}`")
        for key in _BRIEF_FIELDS:
            if key in t:
                value = t[key]
                if isinstance(value, list):
                    value = "; ".join(str(v) for v in value)
                lines.append(f"- {key}: {value}")
        lines.append("")
    return "\n".join(lines)


def synth_author(*, model: str | None = None, budget_usd: float = 2.0,
                 timeout: float = SYNTH_TIMEOUT):
    """The default author: one sealed agent in the workspace that writes and
    runs make_audio.py until check.py passes. Returns its Usage."""
    def author(ws: Path, targets: list[dict]) -> Usage:
        engine = harness.ENGINE(EngineConfig(
            tools=SYNTH_TOOLS, model=model, permission_mode="dontAsk", cwd=ws,
            max_turns_budget_usd=budget_usd, timeout=timeout))
        engine.agent(f"Read BRIEF.md and cues.json. Write make_audio.py as the brief "
                     f"says, run `python3 make_audio.py`, then `python3 check.py`, and "
                     f"repeat until check.py reports every one of the {len(targets)} "
                     "cue(s) ok. Reply with one line.")
        return engine.usage
    return author


def realise(root: str | Path, data: dict, *, author=None, model: str | None = None,
            budget_usd: float = 2.0, say=None, measure=None) -> dict:
    """Fill every cue still without a file with original procedural sound.

    Mutates `data` like `fetch`: a realised cue gains `file`, `licence`,
    `credit`, `source_url` and `duration` -- the contract `yt build` places
    -- plus `synth` (the script and its sha256, so the sound can be
    regenerated); a cue whose file fails `check_wav` keeps no file and is
    listed under `unfilled` with why. `data["synth"]` is the summary returned.
    """
    root = Path(root)
    say = say or (lambda msg: print(msg, file=sys.stderr))
    targets = synth_targets(root, data)
    script_rel = f"{SYNTH_DIR}/make_audio.py"
    summary: dict = {"dir": SYNTH_DIR, "script": script_rel, "targets": len(targets),
                     "realised": 0, "failed": 0, "cost_usd": 0.0}
    data["synth"] = summary
    if not targets:
        return summary
    ws = root / SYNTH_DIR
    ws.mkdir(parents=True, exist_ok=True)
    write_json(ws / "cues.json", targets)
    notes = harness.feedback_for(root, "sound")
    (ws / "BRIEF.md").write_text(synth_brief(targets) + (f"\n\n{notes}\n" if notes else ""),
                                 encoding="utf-8")
    automation_root = str(Path(__file__).resolve().parent.parent)
    (ws / "check.py").write_text(
        CHECK_SCRIPT.replace("@AUTOMATION_ROOT@", repr(automation_root)), encoding="utf-8")

    author = author or synth_author(model=model, budget_usd=budget_usd)
    usage = None
    try:
        usage = author(ws, targets)
    except ClaudeError as exc:
        summary["error"] = str(exc)[:300]
        say(f"  synthesis stopped: {summary['error']}")
    summary["cost_usd"] = round(float(getattr(usage, "cost_usd", 0.0) or 0.0), 4)
    script = ws / "make_audio.py"
    digest = hashlib.sha256(script.read_bytes()).hexdigest() if script.is_file() else ""

    by_id = {(c.get("cue_id") or f"#{n}"): c
             for n, c in enumerate(data.get("cues") or [], 1)}
    unfilled = {u.get("cue_id"): u for u in data.get("unfilled") or []
                if isinstance(u, dict)}
    for t in targets:
        cue = by_id[t["cue_id"]]
        seconds, problems = check_wav(root / t["file"], want_seconds=t["seconds"])
        if not problems and not digest:
            problems = ["no make_audio.py was written, so the file cannot be regenerated"]
        if problems:
            summary["failed"] += 1
            previous = unfilled.get(t["cue_id"]) or {}
            unfilled[t["cue_id"]] = {
                "cue_id": t["cue_id"], "name": cue.get("name", ""),
                "kind": cue.get("kind", ""), "queries": previous.get("queries", []),
                "reason": "synthesis: " + "; ".join(problems)}
            say(f"  {t['cue_id']} not realised: {'; '.join(problems)}")
            continue
        for key in FETCHED_FIELDS:
            cue.pop(key, None)
        cue.update(file=t["file"], licence=SYNTH_LICENCE, credit=SYNTH_CREDIT,
                   source_url="", duration=round(seconds, 3),
                   synth={"script": script_rel, "sha256": digest})
        unfilled.pop(t["cue_id"], None)
        summary["realised"] += 1
        say(f"  {t['cue_id']} {t['file']} <- synthesised {seconds:.2f}s")

    # A synthesised file is a file in the mix: measured and levelled like a
    # fetched one, in one batch, and refused when its level cannot be reached.
    made = [t for t in targets if t["cue_id"] not in unfilled]
    if made:
        narration, _ = narration_level(data)
        try:
            got = (measure or measure_remote)([root / t["file"] for t in made])
        except MeasureError as exc:
            summary["measure_error"] = str(exc)
            summary["unmeasured"] = [t["cue_id"] for t in made]
            say(f"  WARNING: {len(made)} synthesised file(s) NOT LEVELLED: {exc}")
            got = [None] * len(made)
        summary["levelled"] = 0
        for t, levels in zip(made, got):
            cue = by_id[t["cue_id"]]
            if levels is None:
                if "measure_error" not in summary:
                    summary.setdefault("unmeasured", []).append(t["cue_id"])
                continue
            fields, why = level_to_design(cue, levels, narration)
            if why:
                for key in FETCHED_FIELDS:
                    cue.pop(key, None)
                summary["realised"] -= 1
                summary["failed"] += 1
                unfilled[t["cue_id"]] = {
                    "cue_id": t["cue_id"], "name": cue.get("name", ""),
                    "kind": cue.get("kind", ""), "queries": [],
                    "reason": "synthesis: " + why}
                say(f"  {t['cue_id']} synthesised file refused: {why}")
            else:
                cue.update(fields)
                summary["levelled"] += 1
    data["unfilled"] = list(unfilled.values())
    return summary


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #

def print_sound(idea: Idea, data: dict, cues: list[Cue]) -> None:
    palette = data.get("palette") or {}
    stats = data.get("coverage") or {}
    print(f"\n{idea.title.upper()} — SOUND")
    print(f"{timecode(data['runtime'])} · {len(cues)} cues · "
          f"{stats.get('music_share', 0)}% scored")
    for label, key in (("approach", "approach"), ("score", "instrumentation"),
                       ("effects", "sfx_palette"), ("ambience", "ambience"),
                       ("silence", "silence"), ("never", "avoid")):
        if palette.get(key):
            print(wrap(f"{label}: {palette[key]}", "  "))
    for motif in palette.get("motifs", []):
        print(f"\n  motif · {motif.get('name', '')}")
        print(wrap(f"{motif.get('stands_for', '')} — "
                    f"{motif.get('instrumentation', '')}, "
                    f"{motif.get('character', '')}", "    "))
        print(wrap(f"returns: {motif.get('returns', '')}", "    "))
    mix = palette.get("mix") or {}
    if mix:
        print("\n  mix")
        for key in ("narration", "music_bed", "effects", "master"):
            if mix.get(key):
                print(wrap(f"{key}: {mix[key]}", "    "))
    if palette.get("references"):
        print(wrap("listen to: " + ", ".join(palette["references"]), "  "))

    section = None
    for cue in cues:
        if cue.section and cue.section != section:
            section = cue.section
            print(f"\n\n{'=' * 78}\n{section.upper()}")
        print(f"\n  {cue.cue_id}  {timecode(cue.at)}–{timecode(cue.out)}  "
              f"({cue.seconds}s)  [{cue.kind}]"
              + (f"  {cue.shot}" if cue.shot else ""))
        print(wrap(cue.name, "    "))
        print(wrap(f"why: {cue.purpose}", "    "))
        print(wrap(cue.description, "    "))
        for label, value in (("motif", cue.motif), ("instr", cue.instrumentation),
                             ("tempo", cue.tempo), ("sync", cue.sync),
                             ("in", cue.entry), ("out", cue.exit),
                             ("level", cue.level), ("source", cue.source),
                             ("note", cue.note)):
            if value:
                print(wrap(f"{label}: {value}", "      "))
        if cue.layers:
            print(wrap("layers: " + ", ".join(cue.layers), "      "))
        if cue.search:
            print(wrap("search: " + "; ".join(cue.search), "      "))
        if cue.query:
            print(wrap(f"query: {cue.query}", "      "))
        if cue.file:
            print(wrap(f"file: {cue.file} ({cue.licence}, {cue.duration}s)",
                       "      "))
            print(wrap(f"credit: {cue.credit}", "      "))

    if data.get("unfilled"):
        print(f"\n\n{'=' * 78}\nUNFILLED")
        for miss in data["unfilled"]:
            print(wrap(f"· {miss.get('cue_id', '?')} [{miss.get('kind', '?')}] "
                       f"{miss.get('name', '')}: {miss.get('reason', '')}", "  "))
            print(wrap("tried: " + "; ".join(miss.get("queries", [])), "      "))

    review = data.get("review") or {}
    if review.get("flags"):
        print(f"\n\n{'=' * 78}\nLISTEN-BACK")
        for flag in sorted(review["flags"],
                           key=lambda f: ["blocker", "important", "minor"].index(
                               f.get("severity", "minor"))):
            print(wrap(f"· {flag.get('cue', '?')} "
                        f"({flag.get('severity', '?')}): {flag.get('problem', '')}",
                        "  "))
            print(wrap(f"fix: {flag.get('fix', '')}", "      "))
            if flag.get("kept"):
                print(wrap(f"kept: {flag['kept']}", "      "))
    for rev in review.get("revisions", []):
        print(wrap(f"· revised {rev.get('cue', '?')}: {rev.get('why', '')}", "  "))
    if review.get("verdict"):
        print(wrap(f"verdict: {review['verdict']}", "  "))


def write_md(path: str, idea: Idea, data: dict, cues: list[Cue]) -> None:
    palette = data.get("palette") or {}
    stats = data.get("coverage") or {}
    md = [f"# Sound design — {idea.title}", "",
          f"`{idea.genre}` · {timecode(data['runtime'])} · {len(cues)} cues · "
          f"{stats.get('music_share', 0)}% of the runtime scored", ""]
    for label, key in (("Approach", "approach"), ("Score", "instrumentation"),
                       ("Effects palette", "sfx_palette"),
                       ("Ambience", "ambience"), ("Silence", "silence"),
                       ("Never", "avoid")):
        if palette.get(key):
            md += [f"**{label}.** {palette[key]}", ""]

    if palette.get("motifs"):
        md += ["## Motifs", "",
               "| motif | stands for | played by | character | returns |",
               "|---|---|---|---|---|"]
        md += [f"| {m.get('name', '')} | {m.get('stands_for', '')} | "
               f"{m.get('instrumentation', '')} | {m.get('character', '')} | "
               f"{m.get('returns', '')} |" for m in palette["motifs"]]
        md.append("")

    mix = palette.get("mix") or {}
    if mix:
        md += ["## Mix", ""]
        md += [f"- **{k.replace('_', ' ')}.** {v}" for k, v in mix.items() if v]
        md.append("")
    if palette.get("references"):
        md += ["*Listen to, as direction only: "
               + ", ".join(palette["references"]) + ".*", ""]

    md += ["## Cue sheet", "",
           "| cue | in | out | kind | name | what it does | source |",
           "|---|---|---|---|---|---|---|"]
    md += [f"| {c.cue_id} | {timecode(c.at)} | {timecode(c.out)} | {c.kind} | "
           f"{c.name} | {c.purpose} | {c.source} |" for c in cues]
    md.append("")

    section = None
    for cue in cues:
        if cue.section and cue.section != section:
            section = cue.section
            md += [f"## {section}", ""]
        md += [f"### {cue.cue_id} · {timecode(cue.at)}–{timecode(cue.out)} "
               f"({cue.seconds}s) · {cue.kind}"
               + (f" · {cue.shot}" if cue.shot else ""), "",
               f"**{cue.name}** — {cue.purpose}", "", cue.description, ""]
        for label, value in (("Motif", cue.motif),
                             ("Instrumentation", cue.instrumentation),
                             ("Tempo", cue.tempo), ("Sync", cue.sync),
                             ("Entry", cue.entry), ("Exit", cue.exit),
                             ("Level", cue.level), ("Source", cue.source),
                             ("Note", cue.note)):
            if value:
                md.append(f"- **{label}.** {value}")
        if cue.layers:
            md.append("- **Layers.** " + ", ".join(cue.layers))
        if cue.search:
            md.append("- **Search.** " + "; ".join(f"`{s}`" for s in cue.search))
        if cue.query:
            md.append(f"- **Query.** `{cue.query}`")
        if cue.file:
            md.append(f"- **File.** `{cue.file}` — {cue.licence}, "
                      f"{cue.duration}s — {cue.credit}")
        md.append("")

    if data.get("unfilled"):
        md += ["## Unfilled", ""]
        md += [f"- **{m.get('cue_id', '?')}** [{m.get('kind', '?')}] "
               f"{m.get('name', '')} — {m.get('reason', '')} (tried: "
               + "; ".join(f"`{q}`" for q in m.get("queries", [])) + ")"
               for m in data["unfilled"]]
        md.append("")
    if data.get("fetch"):
        md += [f"*Fetched: {data['fetch'].get('filled', 0)} cue(s), "
               f"{data['fetch'].get('bytes', 0)} bytes. "
               f"{data['fetch'].get('quality', '')}.*", ""]

    review = data.get("review") or {}
    if review.get("flags") or review.get("verdict"):
        md += ["## Listen-back", ""]
        if review.get("verdict"):
            md += [f"**Verdict.** {review['verdict']}", ""]
        for flag in review.get("flags", []):
            md += [f"- **{flag.get('severity', '?')}** · {flag.get('cue', '?')} "
                   f"— {flag.get('problem', '')}",
                   f"  - fix: {flag.get('fix', '')}"]
            if flag.get("kept"):
                md.append(f"  - kept: {flag['kept']}")
        for rev in review.get("revisions", []):
            md.append(f"- revised {rev.get('cue', '?')} — {rev.get('why', '')}")
        md.append("")
    Path(path).write_text("\n".join(md), encoding="utf-8")


def write_csv(path: str, cues: list[Cue]) -> None:
    cols = ["cue_id", "kind", "in", "out", "seconds", "section", "shot", "name",
            "purpose", "description", "motif", "instrumentation", "tempo",
            "sync", "layers", "entry", "exit", "level", "source", "search",
            "query", "note", "file", "licence", "credit", "source_url",
            "duration"]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for cue in cues:
            row = asdict(cue) | {"in": timecode(cue.at), "out": timecode(cue.out),
                                 "layers": "; ".join(cue.layers),
                                 "search": "; ".join(cue.search)}
            writer.writerow({c: row.get(c, "") for c in cols})


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "sound", parents=list(parents),
        help="propose effects, ambience, score and the mix",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--script", metavar="FILE",
                   help="the screenplay (default: the project's script.json)")
    p.add_argument("--minutes", type=float, default=10.0,
                   help="runtime, when there is no screenplay (default 10)")
    p.add_argument("--direction", metavar="FILE",
                   help="visual direction (default: the project's styles.json)")
    p.add_argument("--dossier", metavar="DIR",
                   help="research, for what the subject sounds like")
    p.add_argument("--inspo", default="",
                   help="comma-separated channels whose sonic register to match")
    p.add_argument("--mood", default="", help="the feeling you are after")
    p.add_argument("--library", default="", metavar="NAMES",
                   help="music and sfx subscriptions you have")
    p.add_argument("--free-only", action="store_true", dest="free_only",
                   help="only CC0, public domain, CC-BY or self-recorded audio")
    p.add_argument("--no-music", action="store_true", dest="no_music",
                   help="effects, ambience and silence only; no score")
    p.add_argument("--review", action="store_true",
                   help="listen back to the cue sheet, fix the important and "
                        "blocking cues, and re-check them")
    p.add_argument("--report-only", action="store_true", dest="report_only",
                   help="with --review: report the findings without fixing them")
    p.add_argument("--fetch", action="store_true",
                   help="download CC0/CC-BY audio for every cue from Freesound "
                        "into <project>/sound/ and write the files into the "
                        "cue sheet (reuses an existing sound.json)")
    p.add_argument("--synth", action="store_true",
                   help="realise every cue still without a file as original "
                        "procedural sound: an agent writes and runs "
                        "sound/synth/make_audio.py, and every WAV is checked "
                        "before the cue says it has one (after --fetch when "
                        "both are given; reuses an existing sound.json)")
    p.add_argument("--synth-budget-usd", type=float, default=2.0,
                   dest="synth_budget_usd",
                   help="spend cap for the synthesis agent (default 2)")
    p.add_argument("--fresh", action="store_true",
                   help="with --fetch or --synth: spot the cue sheet again "
                        "instead of reusing the project's sound.json")
    p.add_argument("--md", dest="md_out", help="where to write the sound document")
    p.add_argument("--csv", dest="csv_out", help="where to write the cue sheet")
    p.add_argument("--json", dest="json_out", help="where to write structured JSON")
    p.add_argument("--list-voices", action="store_true",
                   help="print the channels with a hand-written voice brief")
    p.set_defaults(run=run)
    return p


def run(args) -> int:
    if args.list_voices:
        for name, brief in sorted(CHANNEL_STYLES.items()):
            print(f"{name:<24} {brief}")
        return 0

    split = lambda s: [p.strip() for p in s.split(",") if p.strip()]  # noqa: E731

    # An explicit --script may be the only thing identifying the video, so it
    # is read before the project is resolved, and its idea is the fallback.
    sections: list[Section] = []
    script: dict = {}
    if args.script:
        sections, script = load_script(args.script)
    fallback = None
    if script.get("idea"):
        known = {f.name for f in fields(Idea)}
        fallback = Idea(**{k: v for k, v in script["idea"].items() if k in known})

    idea, project = workspace.resolve(args, idea=fallback,
                                      inputs=("script", "direction", "dossier"))
    if not args.script:
        found = project.source("script")
        if found:
            sections, script = load_script(found)
        elif not args.quiet:
            print("no screenplay yet; spotting the runtime in broad strokes",
                  file=sys.stderr)
    voice = read_json(project.source("voice"), warn=False) if project.source("voice") else None
    if sections and voice:
        # Cues land on frames of the film as it will be cut: the narration's
        # measured clock, which is the one `yt build` places the takes on.
        planned = sum(s.actual_seconds for s in sections)
        sections = on_measured_clock(script, voice)
        measured = sum(s.actual_seconds for s in sections)
        if measured != planned and not args.quiet:
            print(f"narration measured: spotting on its clock, {timecode(measured)} "
                  f"(the screenplay planned {timecode(planned)})", file=sys.stderr)

    sheet = project.path("sound")
    if (args.fetch or args.synth) and not args.fresh and sheet.exists():
        data = read_json(sheet)
        if data and data.get("cues"):
            if not args.quiet:
                print(f"cue sheet already spotted: {sheet} (--fresh to spot "
                      "it again)", file=sys.stderr)
            return _finish(args, idea, project, data)

    runtime = (sum(s.actual_seconds for s in sections)
               or script.get("target_seconds") or round(args.minutes * 60))
    dossier = read_json(project.source("dossier", args.dossier))
    direction = read_json(project.source("styles", args.direction))
    if not args.quiet:
        print(f"scoring: {idea.title}\n  -> {project.root}", file=sys.stderr)

    rules, digest = house.for_args(args, project, "sound")
    if rules and not args.quiet:
        print(f"standards: {', '.join(digest['sources'])}", file=sys.stderr)
    designer = SoundDesigner(
        idea, house=rules, house_digest=digest, root=project.root,
        model=args.model, runtime=runtime, sections=sections,
        direction=direction_block(direction) if direction else "",
        subject=subject_block(dossier) if dossier else "",
        inspo=split(args.inspo), mood=args.mood, library=split(args.library),
        free_only=args.free_only, music=not args.no_music,
        verbose=not args.quiet)

    try:
        data = designer.run(do_review=args.review, fix=not args.report_only)
    except ClaudeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    code = _finish(args, idea, project, data)
    print(f"{designer.usage}", file=sys.stderr)
    return code


def _finish(args, idea: Idea, project, data: dict) -> int:
    """Fetch if asked, then print and write the three outputs."""
    if args.fetch:
        if not args.quiet:
            print(f"[fetch] freesound -> {project.root / SOUND_DIR}",
                  file=sys.stderr)
        fetch(project.root, data, say=(lambda m: None) if args.quiet else None)
    if getattr(args, "synth", False):
        if not args.quiet:
            print(f"[synth] original sound -> {project.root / SYNTH_DIR}",
                  file=sys.stderr)
        realise(project.root, data, model=args.model, budget_usd=args.synth_budget_usd,
                say=(lambda m: None) if args.quiet else None)

    known = {f.name for f in fields(Cue)}
    cues = [Cue(**{k: v for k, v in row.items() if k in known})
            for row in data["cues"]]
    if not cues:
        print("error: no cues were proposed", file=sys.stderr)
        return 1

    print_sound(idea, data, cues)

    for path, writer_fn in (
            (project.out("sound_md", args.md_out),
             lambda p: write_md(p, idea, data, cues)),
            (project.out("cues", args.csv_out), lambda p: write_csv(p, cues)),
            (project.out("sound", args.json_out),
             lambda p: write_json(p, data))):
        writer_fn(path)
        print(f"wrote {path}", file=sys.stderr)

    stats = data.get("coverage") or {}
    print(f"\n{len(cues)} cues · {stats.get('music_share', 0)}% scored · "
          f"longest run {stats.get('longest_music_run', 0)}s · "
          f"{len(stats.get('silent_by_design', []))} silence(s) by design",
          file=sys.stderr)
    if data.get("fetch"):
        done = data["fetch"]
        wanted = sum(1 for c in cues if c.kind in FETCH_KINDS)
        print(f"fetched {done['filled']} of {wanted} cue(s) "
              f"({done['cached']} already on disk) · {done['bytes']} bytes "
              f"-> {project.root / SOUND_DIR} · {done['unfilled']} unfilled",
              file=sys.stderr)
        print(f"levelled {done.get('levelled', 0)} · "
              f"{len(done.get('refused', []))} source(s) refused for level or "
              f"noise (narration {done.get('narration_db', 0):+.1f} dB, "
              f"{done.get('narration_from', '')})", file=sys.stderr)
        if done.get("error"):
            print(f"warning: {done['error']}", file=sys.stderr)
        if done.get("warning"):
            print(f"WARNING: {done['warning']}", file=sys.stderr)
    if data.get("synth"):
        done = data["synth"]
        print(f"synthesised {done['realised']} of {done['targets']} cue(s) · "
              f"{done['failed']} failed the check · ${done['cost_usd']:.2f} -> "
              f"{project.root / SYNTH_DIR}", file=sys.stderr)
        if done.get("error"):
            print(f"warning: {done['error']}", file=sys.stderr)
    failed = data.get("failed_sections") or 0
    if failed:
        print(f"warning: {failed} section(s) failed", file=sys.stderr)
    return 1 if failed else 0
