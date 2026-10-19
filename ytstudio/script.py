#!/usr/bin/env python3
"""Write the full shooting script for one video, shot by shot.

`yt ideas` has the idea, `yt research` has the facts and the licensed footage,
`yt styles` has the visual treatment, and this turns all of it into an AV
screenplay -- every shot with its picture, its
narration verbatim, the super on screen, the asset it uses and the source the
claim came from.

    yt script --title "How EUV light is made" --minutes 8
    yt script --from ideas.json --index 1 --minutes 12 \
        --dossier research/how-euv-light-is-made --direction styles.json
    yt script --from ideas.json --title "lithography" \
        --inspo "Asianometry,Wendover Productions" --polish --verify
    yt script --title "..." --md script.md --prompter vo.txt \
        --csv shotlist.csv --json script.json

Written in one call per section rather than one call for the whole script: a
15-minute screenplay written in a single pass loses its thread, repeats its own
numbers and drifts off the beat sheet. Each section is written knowing the beats
it owns, the words it has to fill, what the previous section just said and what
the next one needs set up.

With --dossier, the narration is held to what the research actually supports:
every claim carries the source it came from, and --verify re-reads the finished
script looking for lines the dossier does not back.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from claude_engine import ClaudeEngine, ClaudeError, EngineConfig, Usage
from .core import Idea, _merge_usage, read_json, timecode, wrap, write_json
from .ideas import CHANNEL_STYLES, style_brief
from . import house, workspace

#: Narration pace. 150 wpm is an unhurried documentary read; 180 is brisk.
DEFAULT_WPM = 150

SYSTEM = (
    "You are a screenwriter for factual YouTube: documentary, explainer and "
    "video-essay work. You write narration to be spoken aloud, not read -- "
    "short sentences, plain words, one idea at a time, numbers said the way a "
    "person says them. You write the picture with the same care as the words, "
    "and you never write a shot that cannot be shot. You do not invent facts, "
    "figures, quotes or sources: when the research does not support a line, "
    "you cut the line rather than soften it."
)


# --------------------------------------------------------------------------- #
# schemas
# --------------------------------------------------------------------------- #

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "logline": {"type": "string"},
        "promise": {"type": "string"},
        "payoff": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "purpose": {"type": "string"},
                    "seconds": {"type": "integer"},
                    "beats": {"type": "array", "items": {"type": "string"}},
                    "claims": {"type": "array", "items": {"type": "string"}},
                    "visuals": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["heading", "purpose", "seconds", "beats"],
            },
        },
    },
    "required": ["logline", "promise", "payoff", "sections"],
}


def section_schema(sourced: bool) -> dict:
    """Shots for one section. `sourced` makes the citation mandatory."""
    props = {
        "visual": {"type": "string"},
        "narration": {"type": "string"},
        "on_screen": {"type": "string"},
        "asset": {"type": "string"},
        "style": {"type": "string"},
        "source": {"type": "string"},
        "seconds": {"type": "integer"},
        "note": {"type": "string"},
    }
    required = ["visual", "narration", "seconds", "asset", "style"]
    return {
        "type": "object",
        "properties": {
            "shots": {
                "type": "array",
                "items": {"type": "object", "properties": props,
                          "required": required + (["source"] if sourced else [])},
            }
        },
        "required": ["shots"],
    }


POLISH_SCHEMA = {
    "type": "object",
    "properties": {
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "narration": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["n", "narration", "why"],
            },
        }
    },
    "required": ["edits"],
}

CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "flags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "problem": {"type": "string"},
                    "severity": {"type": "string",
                                 "enum": ["blocker", "important", "minor"]},
                    "fix": {"type": "string"},
                },
                "required": ["n", "problem", "severity", "fix"],
            },
        },
        "verdict": {"type": "string"},
    },
    "required": ["flags", "verdict"],
}


# --------------------------------------------------------------------------- #
# the script itself
# --------------------------------------------------------------------------- #

@dataclass
class Shot:
    visual: str = ""
    narration: str = ""
    on_screen: str = ""
    asset: str = ""
    style: str = ""
    source: str = ""
    seconds: int = 0
    note: str = ""
    n: int = 0          # global shot number, for polish and fact-check notes
    label: str = ""     # "3.2", for humans reading the page
    at: int = 0         # start time in seconds from the top
    revised: str = ""   # why the polish pass rewrote this line

    @classmethod
    def from_raw(cls, raw: dict) -> Shot:
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in raw.items() if k in known and v is not None}
        for key in ("visual", "narration", "on_screen", "asset", "style",
                    "source", "note"):
            if key in clean:
                clean[key] = str(clean[key]).strip()
        try:
            clean["seconds"] = max(1, int(clean.get("seconds", 0) or 0))
        except (TypeError, ValueError):
            clean["seconds"] = 5
        return cls(**clean)

    @property
    def words(self) -> int:
        return len(self.narration.split())


@dataclass
class Section:
    heading: str
    purpose: str = ""
    seconds: int = 0
    beats: list[str] = field(default_factory=list)
    shots: list[Shot] = field(default_factory=list)

    @property
    def words(self) -> int:
        return sum(shot.words for shot in self.shots)

    @property
    def actual_seconds(self) -> int:
        return sum(shot.seconds for shot in self.shots)


def shot_listing(shots: list[Shot], *, cite: bool = False) -> str:
    """Every shot numbered for the review stages.

    Silent shots are listed too, marked: dropping them leaves gaps in the
    numbering, and a reviewer reads a gap as a missing shot.
    """
    lines = []
    for shot in shots:
        body = shot.narration or "[picture only, no narration]"
        if cite and shot.source:
            body += f"  [cited: {shot.source}]"
        lines.append(f"{shot.n}. [{shot.label} {timecode(shot.at)} "
                     f"{shot.seconds}s] {body}")
    return "\n".join(lines)


def words_for(seconds: int, wpm: int) -> int:
    return max(1, round(seconds / 60 * wpm))


_NUMBER = re.compile(r"\b\d[\d,.]*\s?(?:%|°[CF]|nm|mm|cm|km|kg|lb|mi|ft|m|s|"
                     r"min|hours?|years?|billion|million|thousand)?\b", re.I)


def spoken_numbers(shots: list[Shot], limit: int = 25) -> list[str]:
    """Figures already said out loud, so a later section does not re-announce them."""
    seen, out = set(), []
    for shot in shots:
        for match in _NUMBER.findall(shot.narration):
            token = match.strip()
            if len(token) > 1 and token.lower() not in seen:
                seen.add(token.lower())
                out.append(token)
    return out[-limit:]


# --------------------------------------------------------------------------- #
# research and direction inputs
# --------------------------------------------------------------------------- #

def sections_of(data: dict) -> list[Section]:
    """A screenplay's sections of shots, from its JSON form."""
    known = {f.name for f in fields(Shot)}
    sections = []
    for raw in (data or {}).get("sections", []):
        sections.append(Section(
            heading=str(raw.get("heading", "")),
            purpose=str(raw.get("purpose", "")),
            seconds=int(raw.get("seconds", 0) or 0),
            beats=[str(b) for b in raw.get("beats", [])],
            shots=[Shot(**{k: v for k, v in shot.items() if k in known})
                   for shot in raw.get("shots", [])]))
    return sections


def load_script(path: str) -> tuple[list[Section], dict]:
    """The screenplay `yt script` wrote, as sections of shots."""
    data = read_json(path) or {}
    return sections_of(data), data


#: Claims are offered best-sourced first when there are more than fit.
_CONFIDENCE = {"high": 0, "medium": 1, "low": 2}


def research_block(data: dict, *, limit: int = 60) -> str:
    """Claims with their sources, licensed assets, and what to correct."""
    parts = []
    if data.get("premise"):
        parts.append(f"Researched premise: {data['premise']}")
    if data.get("verdict"):
        parts.append(f"Does it hold up: {data['verdict']}")

    ranked = sorted(data.get("claims", []),
                    key=lambda c: _CONFIDENCE.get(c.get("confidence"), 3))
    if len(ranked) > limit:
        # Dropping claims silently would let the script contradict research
        # it never saw; say so, and drop the least trusted.
        print(f"note: {len(ranked)} claims in the dossier; the script sees the "
              f"{limit} best sourced", file=sys.stderr)
    claims = []
    for claim in ranked[:limit]:
        number = f" [{claim['number']}]" if claim.get("number") else ""
        caveat = f" CAVEAT: {claim['caveat']}" if claim.get("caveat") else ""
        claims.append(
            f"- {claim.get('claim', '')}{number} -- {claim.get('detail', '')} "
            f"(source: {claim.get('source_title', '?')}, "
            f"{claim.get('source_url', 'no url')}; "
            f"{claim.get('source_kind', '?')}, "
            f"{claim.get('confidence', '?')} confidence){caveat}")
    if claims:
        parts.append("Every claim you may put on camera, with the source to "
                     "cite for it:\n" + "\n".join(claims))

    assets = [f"- [{a.get('kind', '?')}] {a.get('title', '')} "
              f"({a.get('licence', 'licence unknown')}"
              f"{', commercial ok' if a.get('commercial_ok') else ''})"
              f" -- for {a.get('use_for', 'unspecified')}"
              for a in data.get("assets", [])[:limit]]
    if assets:
        parts.append("Footage and imagery already found and licensed:\n"
                     + "\n".join(assets))
    if data.get("misconceptions"):
        parts.append("Misconceptions to correct out loud:\n"
                     + "\n".join(f"- {m}" for m in data["misconceptions"][:8]))
    gaps = [f"- {g.get('severity', '?')}: {g.get('issue', '')}"
            for g in data.get("gaps", [])
            if g.get("severity") in ("blocker", "important")]
    if gaps:
        parts.append("Known weaknesses -- do not write a line that depends on "
                     "these being resolved:\n" + "\n".join(gaps))
    return "\n\n".join(parts)


def direction_block(data: dict) -> str:
    """The chosen visual treatment, as `yt styles` wrote it."""
    parts = []
    if data.get("message"):
        parts.append(f"Visual message: {data['message']}")
    styles = data.get("styles", [])
    if styles:
        lines = []
        for n, style in enumerate(styles, 1):
            bits = [f"{n}. {style.get('name', '')} (fit "
                    f"{style.get('fit', '?')}/10): {style.get('shows_well', '')}"]
            for key, label in (("palette", "palette"), ("typography", "type"),
                               ("motion", "motion"), ("sound", "sound")):
                if style.get(key):
                    bits.append(f"   {label}: {style[key]}")
            for shot in style.get("shots", [])[:5]:
                bits.append(f"   planned shot: {shot}")
            lines.append("\n".join(bits))
        parts.append("Visual treatments chosen for this video, best first. "
                     "Name the treatment each shot belongs to:\n"
                     + "\n".join(lines))
    if data.get("combination"):
        parts.append(f"How they combine: {data['combination']}")
    if data.get("avoid"):
        parts.append("Treatments ruled out: "
                     + "; ".join(f"{r.get('name', '')} ({r.get('why', '')})"
                                 for r in data["avoid"]))
    if data.get("thumbnail"):
        parts.append(f"Thumbnail and title card: {data['thumbnail']}")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# the screenwriter
# --------------------------------------------------------------------------- #

#: A section is about this long; shorter pieces get fewer sections, never
#: sections shorter than the piece.
SECTION_SECONDS = 90
MAX_SECTIONS = 12


def section_count(seconds: int, requested: int = 0) -> int:
    """How many sections a runtime holds: one per ~90 s, between 1 and 12;
    a piece under six minutes still gets up to four, one per 15 s, so a
    one-minute video has four beats and a ten-second one has one."""
    if requested:
        return requested
    floor = min(4, seconds // 15)
    return max(1, min(MAX_SECTIONS, max(round(seconds / SECTION_SECONDS), floor)))


def fit_sections(sections: list[dict], seconds: int) -> list[dict]:
    """Hold the sections to the target runtime.

    The model's runtimes rarely add up, so they are scaled to the total in
    proportion; a section with no time gets an equal share. Each keeps at
    least 5 s, or the piece divided evenly when that is less, and the
    rounding drift lands on the longest section so the sum is exact.
    """
    if not sections:
        return sections
    given = [max(0, int(s.get("seconds", 0) or 0)) for s in sections]
    total = sum(given)
    share = seconds / len(sections)
    scaled = [g * seconds / total if total else share for g in given]
    floor = min(5, seconds // len(sections)) or 1
    fitted = [max(floor, round(x)) for x in scaled]
    drift = seconds - sum(fitted)
    longest = max(range(len(fitted)), key=lambda i: fitted[i])
    fitted[longest] = max(floor, fitted[longest] + drift)
    for section, value in zip(sections, fitted):
        section["seconds"] = value
    return sections


class Screenwriter:
    """Plans the beat sheet, writes each section, then polishes and checks."""

    def __init__(self, idea: Idea, *, model: str = "sonnet", minutes: float = 10,
                 wpm: int = DEFAULT_WPM, sections: int = 0,
                 research: str = "", direction: str = "", outline: dict | None = None,
                 inspo: list[str] | None = None, notes: str = "",
                 lang: str = "", house: str = "",
                 house_digest: dict | None = None,
                 verbose: bool = True) -> None:
        self.idea = idea
        self.model = model
        self.seconds = max(10, round(minutes * 60))
        self.wpm = wpm
        self.sections = sections
        self.research = research
        self.direction = direction
        self.outline = outline or {}
        self.inspo = inspo or []
        self.notes = notes
        self.lang = lang
        self.house = house
        self.house_digest = house_digest or {}
        self.verbose = verbose
        self.usage = Usage()

    # -- plumbing ----------------------------------------------------------

    def _say(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr)

    def _ask(self, prompt: str, schema: dict) -> dict:
        engine = ClaudeEngine(EngineConfig(tools=(), model=self.model))
        try:
            return engine.ask_json(prompt, schema, system=SYSTEM)
        finally:
            _merge_usage(self.usage, engine.usage)

    def _brief(self) -> str:
        bits = [f"Title: {self.idea.title}", f"Genre: {self.idea.genre}",
                f"Premise: {self.idea.description}"]
        if self.idea.hook:
            bits.append(f"Planned hook: {self.idea.hook}")
        if self.idea.keywords:
            bits.append(f"Search intent: {', '.join(self.idea.keywords)}")
        bits.append(f"Runtime: {timecode(self.seconds)} "
                    f"({words_for(self.seconds, self.wpm)} words of narration "
                    f"at {self.wpm} wpm)")
        return "\n".join(bits)

    def _context(self) -> list[str]:
        parts = []
        if self.house:
            parts.append(self.house)
        if self.lang:
            # The code and every stage's output are English; the film is in
            # whatever language this says. The two are separate axes.
            parts.append(f"Write the narration, the on-screen text and every "
                         f"word a viewer sees or hears in {self.lang}. The "
                         "shot descriptions and production notes stay in "
                         "English, for the crew and the tools.")
        if self.inspo:
            parts.append(style_brief(self.inspo) + "\n\nWrite the narration in "
                         "that voice: its sentence length, its vocabulary, its "
                         "attitude to the audience.")
        if self.research:
            parts.append(self.research)
        if self.direction:
            parts.append(self.direction)
        if self.notes:
            parts.append(f"The creator's own notes, which outrank the rest: "
                         f"{self.notes}")
        return parts

    # -- stage one: the beat sheet -----------------------------------------

    def plan(self) -> dict:
        if self.outline.get("sections"):
            self._say(f"[plan] reusing the dossier outline "
                      f"({len(self.outline['sections'])} sections)")
            sections = []
            for raw in self.outline["sections"]:
                sections.append({
                    "heading": str(raw.get("heading", "")),
                    "purpose": "", "seconds": int(raw.get("seconds", 0) or 0),
                    "beats": [str(b) for b in raw.get("beats", [])],
                    "claims": [str(c) for c in raw.get("claims_used", [])],
                    "visuals": [str(a) for a in raw.get("assets_used", [])],
                })
            fit_sections(sections, self.seconds)
            return {"logline": self.outline.get("working_title", self.idea.title),
                    "promise": "", "payoff": "", "sections": sections}

        count = section_count(self.seconds, self.sections)
        self._say(f"[plan] {count} section(s) for {timecode(self.seconds)}")
        parts = [self._brief(), *self._context(),
                 f"Plan this video as {count} sections that total "
                 f"{self.seconds} seconds. Open on the strongest concrete "
                 "fact or image, never a definition. Give each section a "
                 "heading, the job it does in one clause, a runtime in "
                 "seconds, and 2-5 beats in the order they are said. Where "
                 "research is supplied, name the claims each section rests on "
                 "and the assets it shows. Put the weakest material last or "
                 "cut it. Also give the logline, what the first fifteen "
                 "seconds promise the viewer, and how that promise is paid "
                 "off."]
        data = self._ask("\n\n".join(parts), PLAN_SCHEMA)

        # The model's section runtimes rarely add up; hold the total.
        fit_sections(data.get("sections") or [], self.seconds)
        return data

    # -- stage two: write it -----------------------------------------------

    def write_section(self, spec: dict, *, index: int, total: int,
                      plan: dict, written: list[Shot],
                      next_heading: str, sourced: bool) -> list[Shot]:
        seconds = int(spec.get("seconds", 0) or 30)
        budget = words_for(seconds, self.wpm)
        self._say(f"[{index}/{total}] {spec.get('heading', '')} "
                  f"-- {seconds}s, ~{budget} words")

        beats = "\n".join(f"- {b}" for b in spec.get("beats", []))
        parts = [self._brief(), *self._context()]
        if plan.get("logline"):
            parts.append(f"Logline: {plan['logline']}\n"
                         f"The hook promises: {plan.get('promise', '')}\n"
                         f"Paid off by: {plan.get('payoff', '')}")
        parts.append(
            f"Write section {index} of {total}: \"{spec.get('heading', '')}\".\n"
            f"Its job: {spec.get('purpose', 'as the beats require')}\n"
            f"Its beats, in order:\n{beats}")
        if spec.get("claims"):
            parts.append("The claims this section rests on:\n"
                         + "\n".join(f"- {c}" for c in spec["claims"]))
        if spec.get("visuals"):
            parts.append("Assets planned for it: " + ", ".join(spec["visuals"]))

        if written:
            tail = "\n\n".join(f'{s.label}: "{s.narration}"' for s in written[-2:])
            parts.append("The section before this one ended on these shots -- "
                         f"pick up from them, do not restate them:\n\n{tail}")
            numbers = spoken_numbers(written)
            if numbers:
                parts.append("Figures already said out loud. Do not announce "
                             "any of them a second time as if new: "
                             + ", ".join(numbers))
        if next_heading:
            parts.append(f"The next section is \"{next_heading}\". End this "
                         "one so that it is the obvious next thing, without "
                         "announcing it.")

        parts.append(
            f"Break the section into shots of 4-15 seconds, {seconds} seconds "
            f"in total, and about {budget} words of narration across the whole "
            "section -- this is the runtime budget, so write to it rather than "
            "past it.\n\n"
            "For each shot give:\n"
            "- visual: what is on screen, concretely enough to shoot or build\n"
            "- narration: the words spoken, verbatim, as they will be read "
            "aloud. Numbers written the way they are said. No stage "
            "directions in this field, and an empty string where the picture "
            "plays alone.\n"
            "- on_screen: the super, label or lower third, or empty\n"
            "- asset: the licensed asset it uses, or what has to be made\n"
            "- style: which visual treatment this shot belongs to\n"
            "- seconds: its duration\n"
            "- note: pacing, sound or performance direction, or empty")
        if sourced:
            parts.append("- source: the source backing this shot's claim, as "
                         "given in the research. Put the word UNSOURCED where "
                         "a line states something the research does not cover, "
                         "rather than attaching a source that does not say it.")

        data = self._ask("\n\n".join(parts), section_schema(sourced))
        return [Shot.from_raw(raw) for raw in data.get("shots", [])]

    # -- stage three: tighten ----------------------------------------------

    def polish(self, shots: list[Shot]) -> int:
        self._say(f"[polish] reading {len(shots)} shots end to end")
        parts = [self._brief(),
                 "Here is the finished narration, shot by shot, with the shot "
                 "number first:\n\n" + shot_listing(shots),
                 "Read it as one continuous read-aloud and tighten only what "
                 "needs it. Rewrite a line when it repeats something already "
                 "said, stumbles when spoken, buries its point at the end, "
                 "opens a sentence the same way as its neighbour, or states a "
                 "number a second time as if new. Keep each rewrite within a "
                 "couple of words of the original length, keep its facts "
                 "exactly, and keep the voice. Return only the lines you "
                 "actually changed -- most lines should be left alone -- with "
                 "the shot number, the new line, and one clause saying why."]
        try:
            data = self._ask("\n\n".join(parts), POLISH_SCHEMA)
        except ClaudeError as exc:
            self._say(f"[polish] failed ({exc}); keeping the script as written")
            return 0

        by_n = {s.n: s for s in shots}
        applied = 0
        for edit in data.get("edits", []):
            shot = by_n.get(int(edit.get("n", 0) or 0))
            line = str(edit.get("narration", "")).strip()
            if shot is None or not line or line == shot.narration:
                continue
            shot.narration, shot.revised = line, str(edit.get("why", "")).strip()
            applied += 1
        self._say(f"[polish] rewrote {applied} line(s)")
        return applied

    # -- stage four: hold it to the research -------------------------------

    def verify(self, shots: list[Shot]) -> dict:
        if not self.research:
            self._say("[verify] no research supplied; nothing to check against")
            return {}
        self._say(f"[verify] checking {len(shots)} shots against the research")
        parts = [self._brief(), self.research,
                 "The script as written, with the source each shot cites:\n\n"
                 + shot_listing(shots, cite=True),
                 "Check every line against the research above. Flag a line "
                 "when it states a fact the research does not contain, when it "
                 "overstates what its own cited source says, when it cites a "
                 "source that does not cover it, when it presents a low "
                 "confidence or caveated claim as settled, or when it is "
                 "marked UNSOURCED. Give the shot number, the problem, how "
                 "severe it is, and the smallest fix that makes the line "
                 "defensible. Shots marked picture-only carry no claim, so "
                 "skip them. Do not flag ordinary connective narration that "
                 "states no fact, and use shot number 0 for a problem with the "
                 "script as a whole. Then give a one-sentence verdict on whether "
                 "this script can be published as it stands."]
        try:
            return self._ask("\n\n".join(parts), CHECK_SCHEMA)
        except ClaudeError as exc:
            self._say(f"[verify] failed ({exc})")
            return {}

    # -- run ---------------------------------------------------------------

    def run(self, *, do_polish: bool = False, do_verify: bool = False) -> dict:
        plan = self.plan()
        specs = plan.get("sections") or []
        if not specs:
            raise ClaudeError("the plan came back with no sections")

        sourced = bool(self.research)
        sections: list[Section] = []
        written: list[Shot] = []
        failures = 0
        for index, spec in enumerate(specs, 1):
            section = Section(heading=str(spec.get("heading", f"Section {index}")),
                              purpose=str(spec.get("purpose", "")),
                              seconds=int(spec.get("seconds", 0) or 0),
                              beats=[str(b) for b in spec.get("beats", [])])
            try:
                shots = self.write_section(
                    spec, index=index, total=len(specs), plan=plan,
                    written=written, sourced=sourced,
                    next_heading=str(specs[index].get("heading", ""))
                    if index < len(specs) else "")
            except ClaudeError as exc:
                # One failed section must not cost the whole screenplay.
                self._say(f"  section {index} failed: {exc}")
                failures += 1
                shots = []
            for i, shot in enumerate(shots, 1):
                shot.n = len(written) + i
                shot.label = f"{index}.{i}"
            section.shots = shots
            written += shots
            sections.append(section)

        at = 0
        for shot in written:
            shot.at, at = at, at + shot.seconds

        polished = self.polish(written) if do_polish and written else 0
        check = self.verify(written) if do_verify and written else {}

        return {
            "idea": asdict(self.idea),
            "logline": plan.get("logline", ""),
            "promise": plan.get("promise", ""),
            "payoff": plan.get("payoff", ""),
            "target_seconds": self.seconds,
            "wpm": self.wpm,
            "sections": [asdict(s) for s in sections],
            "polished": polished,
            "failed_sections": failures,
            "lang": self.lang,
            "house": self.house_digest,
            "check": check,
            "usage": asdict(self.usage),
        }


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #

#: The delivery gate reads a caption at 17 characters a second; a super on
#: screen is read the same way, so the same limit holds for it. This is the
#: measurable half of the house rule about reserving time for reading.
READING_CPS = 17.0

#: Enough number words to tell "12" said as "twelve" from "12" never said.
SPOKEN_NUMBERS = {
    0: ("zero",), 1: ("um", "uma", "one"), 2: ("dois", "duas", "two"),
    3: ("tres", "três", "three"), 4: ("quatro", "four"), 5: ("cinco", "five"),
    6: ("seis", "six"), 7: ("sete", "seven"), 8: ("oito", "eight"),
    9: ("nove", "nine"), 10: ("dez", "ten"), 11: ("onze", "eleven"),
    12: ("doze", "twelve"), 13: ("treze", "thirteen"),
    15: ("quinze", "fifteen"), 20: ("vinte", "twenty"),
    30: ("trinta", "thirty"), 40: ("quarenta", "forty"),
    50: ("cinquenta", "fifty"), 60: ("sessenta", "sixty"),
    100: ("cem", "cento", "hundred"), 1000: ("mil", "thousand"),
}


def legibility(sections: list[Section], timings: dict | None = None) -> list[dict]:
    """Supers nobody can finish reading in the time they are on screen."""
    out = []
    for section in sections:
        for shot in section.shots:
            text = " ".join((shot.on_screen or "").split())
            seconds = float((timings or {}).get(shot.label, shot.seconds) or 0)
            if not text or seconds <= 0:
                continue
            cps = len(text) / seconds
            if cps > READING_CPS:
                out.append({"label": shot.label, "cps": round(cps, 1),
                            "seconds": round(seconds, 2), "chars": len(text),
                            "needs_seconds": round(len(text) / READING_CPS, 1),
                            "text": text[:60]})
    return out


def unspoken_numbers(sections: list[Section]) -> list[dict]:
    """Figures put on screen that the narration never says out loud.

    The house rule is that the voice carries what the viewer must understand.
    This is a heuristic and says so: a figure may be spoken in words, so the
    common number words count as spoken. What is left is worth a look.
    """
    out = []
    for section in sections:
        for shot in section.shots:
            said = (shot.narration or "").lower()
            for raw in re.findall(r"\d[\d.,]*", shot.on_screen or ""):
                digits = raw.rstrip(".,")
                if digits in said:
                    continue
                try:
                    value = int(digits.replace(".", "").replace(",", ""))
                except ValueError:
                    value = None
                if value is not None and any(word in said for word
                                             in SPOKEN_NUMBERS.get(value, ())):
                    continue
                out.append({"label": shot.label, "number": digits,
                            "super": " ".join((shot.on_screen or "").split())[:50]})
    return out


def print_hints(hints: dict) -> None:
    """What the house standards can be checked for, rather than asked for."""
    if hints.get("legibility"):
        print(f"\n  supers above {READING_CPS:.0f} characters a second "
              "(nobody finishes reading them):")
        for row in hints["legibility"]:
            print(f"    {row['label']:<6} {row['chars']} chars in "
                  f"{row['seconds']}s = {row['cps']} cps, needs "
                  f"{row['needs_seconds']}s — {row['text']}")
    if hints.get("unspoken_numbers"):
        print("\n  figures on screen the narration never says "
              "(heuristic: check whether they are spoken as words):")
        for row in hints["unspoken_numbers"]:
            print(f"    {row['label']:<6} {row['number']:<8} {row['super']}")


def print_script(idea: Idea, data: dict, sections: list[Section]) -> None:
    shots = [s for sec in sections for s in sec.shots]
    print(f"\n{idea.title.upper()}")
    print(f"{idea.genre} · target {timecode(data['target_seconds'])} · "
          f"{sum(s.words for s in shots)} words · {len(shots)} shots")
    if data.get("logline"):
        print(wrap(f"logline: {data['logline']}", "  "))

    for section in sections:
        print(f"\n\n{'=' * 78}\n{section.heading.upper()}  "
              f"[{timecode(section.shots[0].at) if section.shots else '-'}, "
              f"{section.actual_seconds}s, {section.words} words]")
        if section.purpose:
            print(wrap(section.purpose, "  "))
        for shot in section.shots:
            print(f"\n  {shot.label}  {timecode(shot.at)}  ({shot.seconds}s)"
                  + (f"  [{shot.style}]" if shot.style else ""))
            print(wrap(f"VIDEO  {shot.visual}", "    "))
            if shot.narration:
                print(wrap(f'AUDIO  "{shot.narration}"', "    "))
            if shot.on_screen:
                print(wrap(f"SUPER  {shot.on_screen}", "    "))
            if shot.asset:
                print(wrap(f"ASSET  {shot.asset}", "    "))
            if shot.source:
                print(wrap(f"CITE   {shot.source}", "    "))
            if shot.note:
                print(wrap(f"NOTE   {shot.note}", "    "))

    check = data.get("check") or {}
    if check.get("flags"):
        numbers = {s.n for s in shots}
        print(f"\n\n{'=' * 78}\nFACT-CHECK")
        for flag in sorted(check["flags"],
                           key=lambda f: ["blocker", "important", "minor"].index(
                               f.get("severity", "minor"))):
            where = (f"shot {flag['n']}" if flag.get("n") in numbers
                     else "script-wide")
            print(wrap(f"· {where} "
                        f"({flag.get('severity', '?')}): {flag.get('problem', '')}",
                        "  "))
            print(wrap(f"fix: {flag.get('fix', '')}", "      "))
    if check.get("verdict"):
        print(wrap(f"verdict: {check['verdict']}", "  "))


def write_md(path: str, idea: Idea, data: dict, sections: list[Section]) -> None:
    shots = [s for sec in sections for s in sec.shots]
    md = [f"# {idea.title}", "",
          f"`{idea.genre}` · target **{timecode(data['target_seconds'])}** · "
          f"{sum(s.words for s in shots)} words at {data['wpm']} wpm · "
          f"{len(shots)} shots", ""]
    for key, label in (("logline", "Logline"), ("promise", "The hook promises"),
                       ("payoff", "Paid off by")):
        if data.get(key):
            md += [f"**{label}.** {data[key]}", ""]

    for n, section in enumerate(sections, 1):
        start = timecode(section.shots[0].at) if section.shots else "-"
        md += [f"## {n}. {section.heading}", "",
               f"*{start} · {section.actual_seconds}s · {section.words} words*"
               + (f" — {section.purpose}" if section.purpose else ""), ""]
        for shot in section.shots:
            md += [f"### {shot.label} · {timecode(shot.at)} ({shot.seconds}s)"
                   + (f" · `{shot.style}`" if shot.style else ""), "",
                   f"**VIDEO** — {shot.visual}", ""]
            if shot.narration:
                md += [f"**AUDIO** — {shot.narration}", ""]
            extras = [(f"**SUPER** — {shot.on_screen}" if shot.on_screen else ""),
                      (f"**ASSET** — {shot.asset}" if shot.asset else ""),
                      (f"**CITE** — {shot.source}" if shot.source else ""),
                      (f"**NOTE** — {shot.note}" if shot.note else ""),
                      (f"*polished: {shot.revised}*" if shot.revised else "")]
            md += [e for e in extras if e]
            md.append("")

    check = data.get("check") or {}
    if check.get("flags") or check.get("verdict"):
        md += ["## Fact-check", ""]
        if check.get("verdict"):
            md += [f"**Verdict.** {check['verdict']}", ""]
        numbers = {s.n for s in shots}
        for flag in check.get("flags", []):
            where = (f"shot {flag['n']}" if flag.get("n") in numbers
                     else "script-wide")
            if flag.get("resolved"):
                # The flag stays as provenance; what changed is recorded with it.
                md += [f"- ~~{flag.get('severity', '?')}~~ · {where} — "
                       f"{flag.get('problem', '')}",
                       f"  - **resolved:** {flag['resolved']}"]
                continue
            md += [f"- **{flag.get('severity', '?')}** · {where} — "
                   f"{flag.get('problem', '')}",
                   f"  - fix: {flag.get('fix', '')}"]
        md.append("")
    Path(path).write_text("\n".join(md), encoding="utf-8")


def write_prompter(path: str, idea: Idea, sections: list[Section],
                   *, wpm: int) -> None:
    """Narration only, in order: what gets read into the microphone."""
    out = [idea.title.upper(), ""]
    for section in sections:
        lines = [s.narration for s in section.shots if s.narration]
        if not lines:
            continue
        out += [f"--- {section.heading.upper()} "
                f"({sum(len(l.split()) for l in lines)} words) ---", ""]
        out += [wrap(line, width=64) + "\n" for line in lines]
    words = sum(s.words for sec in sections for s in sec.shots)
    out += [f"--- END · {words} words · about {timecode(round(words / wpm * 60))} "
            f"at {wpm} wpm ---"]
    Path(path).write_text("\n".join(out) + "\n", encoding="utf-8")


def write_csv(path: str, sections: list[Section]) -> None:
    cols = ["label", "at", "seconds", "section", "style", "visual", "narration",
            "on_screen", "asset", "source", "note"]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for section in sections:
            for shot in section.shots:
                row = asdict(shot) | {"section": section.heading,
                                      "at": timecode(shot.at)}
                writer.writerow({c: row.get(c, "") for c in cols})


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "script", parents=list(parents), help="write the full shooting script",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--minutes", type=float, default=10.0,
                   help="target runtime in minutes (default 10)")
    p.add_argument("--wpm", type=int, default=DEFAULT_WPM,
                   help=f"narration pace (default {DEFAULT_WPM})")
    p.add_argument("--sections", type=int, default=0,
                   help="how many sections (default: one per 90 seconds)")
    p.add_argument("--dossier", metavar="DIR",
                   help="research: claims, sources, assets (default: the project's)")
    p.add_argument("--direction", metavar="FILE",
                   help="visual direction (default: the project's styles.json)")
    p.add_argument("--outline", action="store_true",
                   help="use the dossier's own script outline as the beat sheet")
    p.add_argument("--lang", default="", metavar="CODE",
                   help="the language the film is in, for narration and "
                        "on-screen text (e.g. en, pt-BR). Shot descriptions "
                        "stay English for the crew; `yt voice` takes this as "
                        "its default")
    p.add_argument("--inspo", default="",
                   help="comma-separated channels to borrow the voice of")
    p.add_argument("--notes", default="",
                   help="your own instructions, which outrank everything else")
    p.add_argument("--polish", action="store_true",
                   help="reread the finished narration and tighten it")
    p.add_argument("--verify", action="store_true",
                   help="fact-check the script against the dossier")
    p.add_argument("--md", dest="md_out", help="where to write the screenplay")
    p.add_argument("--prompter", dest="vo_out", help="where to write narration only")
    p.add_argument("--csv", dest="csv_out", help="where to write the shot list")
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
    idea, project = workspace.resolve(args, inputs=("dossier", "direction"))

    dossier = read_json(project.source("dossier", args.dossier))
    direction = read_json(project.source("styles", args.direction))
    if args.verify and not dossier:
        print("warning: --verify needs research; nothing to check against",
              file=sys.stderr)
    if args.outline and not (dossier or {}).get("outline"):
        print("warning: --outline needs a dossier with an outline; planning "
              "from scratch", file=sys.stderr)
    if not args.quiet:
        print(f"writing: {idea.title}\n  -> {project.root}", file=sys.stderr)

    rules, digest = house.for_args(args, project, "script")
    if rules and not args.quiet:
        print(f"standards: {', '.join(digest['sources'])}", file=sys.stderr)
    writer = Screenwriter(
        idea, house=rules, house_digest=digest,
        model=args.model, minutes=args.minutes, wpm=args.wpm,
        sections=args.sections, inspo=split(args.inspo), notes=args.notes,
        lang=args.lang,
        research=research_block(dossier) if dossier else "",
        direction=direction_block(direction) if direction else "",
        outline=(dossier or {}).get("outline") if args.outline else None,
        verbose=not args.quiet)

    try:
        data = writer.run(do_polish=args.polish, do_verify=args.verify)
    except ClaudeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    sections = [Section(heading=s["heading"], purpose=s["purpose"],
                        seconds=s["seconds"], beats=s["beats"],
                        shots=[Shot(**shot) for shot in s["shots"]])
                for s in data["sections"]]
    shots = [s for sec in sections for s in sec.shots]
    if not shots:
        print("error: no shots were written", file=sys.stderr)
        return 1

    hints = {"legibility": legibility(sections),
             "unspoken_numbers": unspoken_numbers(sections)}
    data |= hints

    print_script(idea, data, sections)
    print_hints(hints)

    for path, writer_fn in (
            (project.out("script_md", args.md_out),
             lambda p: write_md(p, idea, data, sections)),
            (project.out("narration", args.vo_out),
             lambda p: write_prompter(p, idea, sections, wpm=args.wpm)),
            (project.out("shotlist", args.csv_out),
             lambda p: write_csv(p, sections)),
            (project.out("script", args.json_out),
             lambda p: write_json(p, data))):
        writer_fn(path)
        print(f"wrote {path}", file=sys.stderr)

    words = sum(s.words for s in shots)
    runtime = sum(s.seconds for s in shots)
    print(f"\n{len(shots)} shots · {words} words · {timecode(runtime)} "
          f"of {timecode(data['target_seconds'])} planned "
          f"({timecode(round(words / args.wpm * 60))} spoken at {args.wpm} wpm)",
          file=sys.stderr)
    if data["failed_sections"]:
        print(f"warning: {data['failed_sections']} section(s) failed and are "
              "empty", file=sys.stderr)
    print(f"{writer.usage}", file=sys.stderr)
    return 1 if data["failed_sections"] else 0
